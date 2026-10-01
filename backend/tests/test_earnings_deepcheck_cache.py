"""Earnings deep-check (2026-09-30): a PARTIAL build is never frozen; old rows are evicted.

Before: one failed FMP leg (income 429, prices timeout, or the per-symbol earnings feed —
whose 429 `get_earning_calendar_full` swallowed into `[]`) produced a degraded payload that
`get_earnings` wrote to the 24h Supabase tier for every user, and the report collector
baked into a paid report. There was no payload version either, so a code fix could not
evict a row already cached (the AVGO -89.97% row sat there).

Now: `degraded` lists the failed legs; such a build skips the upsert and lives 60s in
memory; `response_json` carries `payload_version`, and a row without the current one is a
miss. Hermetic: FMP and Supabase are faked.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.integrations.fmp import FMPClient, FMPException, FMPRateLimitException
from app.schemas.earnings import EarningsResponse
from app.services import earnings_service as es
from app.services.earnings_service import EarningsService


class _FakeFMP:
    def __init__(self, *, income_exc=None, est_exc=None, prices_exc=None, feed_exc=None,
                 income=None, prices=None, feed=None, estimates=None):
        self.income_exc, self.est_exc = income_exc, est_exc
        self.estimates = estimates if estimates is not None else [
            {"date": "2099-08-02", "epsAvg": 1.7, "revenueAvg": 24e9}]
        self.prices_exc, self.feed_exc = prices_exc, feed_exc
        self.income = income if income is not None else [
            {"date": "2026-02-01", "period": "Q1", "fiscalYear": 2026,
             "revenue": 19e9, "epsDiluted": 0.9},
            {"date": "2026-05-03", "period": "Q2", "fiscalYear": 2026,
             "revenue": 22e9, "epsDiluted": 1.03},
        ]
        self.prices = prices if prices is not None else [{"date": "2026-05-01", "close": 400.0}]
        self.feed = feed if feed is not None else [
            {"date": "2026-03-05", "epsActual": 1.6, "epsEstimated": 1.53,
             "revenueActual": 19e9, "revenueEstimated": 18.9e9},
            {"date": "2026-06-03", "epsActual": 1.58, "epsEstimated": 1.57,
             "revenueActual": 22e9, "revenueEstimated": 21.9e9},
        ]
        self.feed_raise_flags = []

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        if self.income_exc:
            raise self.income_exc
        return self.income

    async def get_analyst_estimates(self, ticker, period="quarter", limit=20):
        if self.est_exc:
            raise self.est_exc
        return self.estimates

    async def get_historical_prices(self, ticker, from_date=None, to_date=None):
        if self.prices_exc:
            raise self.prices_exc
        return self.prices

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        self.feed_raise_flags.append(raise_errors)
        if self.feed_exc is not None:
            if raise_errors:
                raise self.feed_exc
            return []          # the production default: swallowed into "no announcements"
        return self.feed


def _svc(fake, supabase=None) -> EarningsService:
    svc = EarningsService.__new__(EarningsService)
    svc.fmp = fake
    svc.supabase = supabase
    return svc


@pytest.fixture(autouse=True)
def _clean_memory_tier():
    es._cache.clear()
    es._inflight.clear()
    yield
    es._cache.clear()
    es._inflight.clear()


async def _get_earnings_recording_upserts(monkeypatch, svc, ticker):
    """Run get_earnings with the Supabase READ stubbed to a miss and the executor
    replaced by a synchronous recorder, so 'never upserted' is a fact, not a race."""
    upserts = []
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda t: None)
    monkeypatch.setattr(
        svc, "_upsert_supabase_cache_safe",
        lambda t, result, nxt: upserts.append((t, result, nxt)),
    )
    loop = asyncio.get_running_loop()

    def _run_now(executor, fn, *args):
        fn(*args)
        fut = loop.create_future()
        fut.set_result(None)
        return fut

    monkeypatch.setattr(loop, "run_in_executor", _run_now)
    resp = await svc.get_earnings(ticker)
    return resp, upserts


# ── The degraded gate ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "kwargs, reason",
    [
        ({"income_exc": FMPRateLimitException("429")}, "income"),
        ({"est_exc": TimeoutError("estimates timed out")}, "estimates"),
        ({"prices_exc": RuntimeError("prices boom")}, "prices"),
        ({"feed_exc": FMPRateLimitException("429 on /earnings")}, "earnings_feed"),
    ],
)
@pytest.mark.asyncio
async def test_failed_leg_is_never_persisted(monkeypatch, kwargs, reason):
    svc = _svc(_FakeFMP(**kwargs))
    resp, upserts = await _get_earnings_recording_upserts(monkeypatch, svc, "AVGO")

    assert reason in resp.degraded
    assert upserts == [], f"a degraded build ({reason}) reached the 24h Supabase tier"
    # Held briefly in memory (absorbs the retry storm), then gone.
    ts, value, ttl = es._cache["earnings:AVGO"]
    assert ttl == es._DEGRADED_CACHE_TTL == 60
    assert es._cache_get("earnings:AVGO") is value
    es._cache["earnings:AVGO"] = (ts - 61, value, ttl)
    assert es._cache_get("earnings:AVGO") is None


@pytest.mark.asyncio
async def test_feed_is_fetched_with_raise_errors(monkeypatch):
    fake = _FakeFMP()
    await _get_earnings_recording_upserts(monkeypatch, _svc(fake), "AVGO")
    assert fake.feed_raise_flags == [True]


@pytest.mark.asyncio
async def test_feed_outage_does_not_ship_gaap_vs_adjusted_misses():
    """The 429'd feed used to read as 'no announcements' → every quarter a GAAP-vs-non-GAAP
    'miss'. Now it is marked degraded AND those quarters carry no surprise.

    The analyst estimates sit AT the income period ends (adjusted epsAvg 1.6 / 1.57 vs GAAP
    0.9 / 1.03), so the GAAP-vs-adjusted branch is actually reached: with the old
    ``_compute_surprise(gaap, est_eps)`` restored this fails with -43.75% / -34.39%. (Round
    2, R30: the fixture used to carry only a 2099 estimate, so no quarter had a consensus
    and the surprise assertion could not fail.)"""
    estimates = [{"date": "2026-02-01", "epsAvg": 1.6, "revenueAvg": 19e9},
                 {"date": "2026-05-03", "epsAvg": 1.57, "revenueAvg": 22e9},
                 {"date": "2099-08-02", "epsAvg": 1.7, "revenueAvg": 24e9}]
    fake = _FakeFMP(feed_exc=FMPRateLimitException("429"), estimates=estimates)
    resp = await _svc(fake)._build_earnings("AVGO")
    assert resp.degraded == ["earnings_feed"]
    reported = [q for q in resp.eps_quarters if q.actual_value is not None]
    assert [q.fiscal_date for q in reported] == ["2026-02-01", "2026-05-03"]
    for q in reported:
        assert q.surprise_percent is None
        assert q.has_estimate is False
        assert q.estimate_value == q.actual_value


@pytest.mark.asyncio
async def test_non_list_legs_are_degraded():
    fake = _FakeFMP(income={"Error Message": "Limit Reach"}, prices={"error": "x"})
    fake.feed = {"Error Message": "Limit Reach"}
    resp = await _svc(fake)._build_earnings("AVGO")
    assert set(resp.degraded) == {"income", "prices", "earnings_feed"}


@pytest.mark.asyncio
async def test_genuinely_empty_legs_are_not_degraded(monkeypatch):
    """A new listing has no statements yet — an honest empty answer, cacheable."""
    svc = _svc(_FakeFMP(income=[], prices=[], feed=[]))
    resp, upserts = await _get_earnings_recording_upserts(monkeypatch, svc, "NEWCO")
    assert resp.degraded == []
    assert len(upserts) == 1
    assert es._cache["earnings:NEWCO"][2] == es._CACHE_TTL == 300


@pytest.mark.asyncio
async def test_complete_build_is_persisted_once(monkeypatch):
    svc = _svc(_FakeFMP())
    resp, upserts = await _get_earnings_recording_upserts(monkeypatch, svc, "AVGO")
    assert resp.degraded == []
    assert len(upserts) == 1 and upserts[0][1] is resp


def test_cache_set_default_ttl_is_unchanged():
    es._cache_set("k", "v")
    assert es._cache["k"][2] == es._CACHE_TTL
    es._cache_set("k2", "v", ttl=5)
    assert es._cache["k2"][2] == 5


# ── Payload version + writer guard ───────────────────────────────────────────


class _FakeSupabase:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.upserts = []

    def table(self, name):
        assert name == "earnings_cache"
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def upsert(self, payload, on_conflict=None):
        self.upserts.append(payload)
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


def _row(response_json, next_date=None):
    return {"response_json": response_json,
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "next_earnings_date": next_date}


def _minimal_response(**extra):
    return {"symbol": "AVGO", "eps_quarters": [], "revenue_quarters": [],
            "price_history": [], **extra}


def test_row_without_payload_version_is_a_miss():
    """Every row written before the deploy has no version — including AVGO's -89.97%."""
    svc = _svc(None, _FakeSupabase([_row(_minimal_response())]))
    assert svc._check_supabase_cache("AVGO") is None


def test_row_with_old_payload_version_is_a_miss():
    svc = _svc(None, _FakeSupabase([_row(_minimal_response(payload_version=0))]))
    assert svc._check_supabase_cache("AVGO") is None


def test_row_marked_degraded_is_a_miss():
    rj = _minimal_response(payload_version=es._EARNINGS_PAYLOAD_VERSION, degraded=["income"])
    svc = _svc(None, _FakeSupabase([_row(rj)]))
    assert svc._check_supabase_cache("AVGO") is None


@pytest.mark.asyncio
async def test_upsert_round_trips_with_the_current_version():
    built = await _svc(_FakeFMP())._build_earnings("AVGO")
    sb = _FakeSupabase()
    svc = _svc(None, sb)
    svc._upsert_supabase_cache_safe("AVGO", built, None)
    assert len(sb.upserts) == 1
    stored = sb.upserts[0]["response_json"]
    assert stored["payload_version"] == es._EARNINGS_PAYLOAD_VERSION

    sb.rows = [_row(stored)]
    read = svc._check_supabase_cache("AVGO")
    assert isinstance(read, EarningsResponse)
    assert read == built                                # payload_version stripped
    assert "payload_version" not in read.model_dump()


@pytest.mark.asyncio
async def test_writer_refuses_a_degraded_build_even_if_called():
    built = await _svc(_FakeFMP(income_exc=RuntimeError("x")))._build_earnings("AVGO")
    sb = _FakeSupabase()
    _svc(None, sb)._upsert_supabase_cache_safe("AVGO", built, None)
    assert sb.upserts == []


def test_report_day_row_is_still_stale():
    """`>=` stays: a row whose next date is TODAY rebuilds, so the after-close actuals
    appear the same evening (`>` would serve the morning build for 24h)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rj = _minimal_response(payload_version=es._EARNINGS_PAYLOAD_VERSION)
    svc = _svc(None, _FakeSupabase([_row(rj, next_date=today)]))
    assert svc._check_supabase_cache("AVGO") is None


# ── get_earning_calendar_full(raise_errors=...) on the real client ───────────


def _client(make_request) -> FMPClient:
    client = FMPClient.__new__(FMPClient)
    client._make_request = make_request   # instance attribute on a bare object
    return client


@pytest.mark.asyncio
async def test_calendar_default_still_swallows_for_other_callers():
    async def boom(endpoint, params=None):
        raise FMPRateLimitException("429")

    assert await _client(boom).get_earning_calendar_full("AVGO") == []


@pytest.mark.asyncio
async def test_calendar_raise_errors_propagates_failures():
    async def boom(endpoint, params=None):
        raise FMPRateLimitException("429")

    with pytest.raises(FMPRateLimitException):
        await _client(boom).get_earning_calendar_full("AVGO", raise_errors=True)


@pytest.mark.asyncio
async def test_calendar_non_list_body():
    async def dict_body(endpoint, params=None):
        return {"Error Message": "Limit Reach"}

    assert await _client(dict_body).get_earning_calendar_full("AVGO") == []
    with pytest.raises(FMPException):
        await _client(dict_body).get_earning_calendar_full("AVGO", raise_errors=True)


@pytest.mark.asyncio
async def test_calendar_normalizes_and_drops_non_dict_rows():
    async def rows(endpoint, params=None):
        assert endpoint == "earnings" and params == {"symbol": "AVGO"}
        return [{"date": "2026-06-03", "epsActual": 1.58, "revenueActual": 22e9}, "junk"]

    out = await _client(rows).get_earning_calendar_full("avgo", raise_errors=True)
    assert out == [{"date": "2026-06-03", "epsActual": 1.58, "revenueActual": 22e9,
                    "eps": 1.58, "revenue": 22e9}]
