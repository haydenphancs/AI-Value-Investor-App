"""Fund-shaped empty builds are cacheable — Revenue Breakdown, overview fundamentals and the
profitability snapshot (2026-10-01, spec ETF steps 3-5).

A fund (VB, SPY …) opened through TickerDetailView reaches the equity Financials services.
FMP answers its statements and metrics with a genuine 200 `[]`, and every write gate read
that as a possible outage: Revenue Breakdown kept the placeholder card 60 s, the overview's
fundamentals bundle (≈15 FMP calls, a full daily history among them) was never cached
because `key_metrics` was "missing", and the all-absent profitability build was not even
kept in memory. Each view of a fund re-ran all of it.

The rule each gate now follows, and what every test below pins:

  * a build is fund-shaped ONLY when the FMP profile fetched for THAT build POSITIVELY says
    fund (`isEtf is True` / `isFund is True` — never inferred from the empty answer), AND the
    legs the gate depends on answered RAW lists (an exception or an error dict that the
    builder silently coerces to `[]` is a failure, not a fund), AND where the shape is "no
    rows" the raw answer was exactly `[]` (junk rows dropped by `_as_list` are malformed
    data), AND the build carries no degraded reason beyond that service's fund-shape set;
  * anything else keeps today's refusal, and the served response is unchanged (iOS sees the
    same `degraded` reasons).

Hermetic: FMP, Supabase, Profit Power, short interest and the movers service are faked.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

import app.services.profitability_snapshot_service as ps
import app.services.revenue_breakdown_service as rb
import app.services.stock_overview_service as S
from app.integrations.fmp import FMPRateLimitException, FMPUnavailableException
from app.schemas.revenue_breakdown import RevenueBreakdownResponse
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.utils.market_hours import ET

B = 1e9
_FUND_PROFILE = {"symbol": "VB", "companyName": "Vanguard Small-Cap ETF", "isEtf": True,
                 "isFund": False, "sector": "", "industry": ""}
_MUTUAL_FUND_PROFILE = {"symbol": "VFIAX", "companyName": "Vanguard 500 Index Admiral",
                        "isEtf": False, "isFund": True}
_COMPANY_PROFILE = {"symbol": "ACME", "companyName": "Acme Corp", "isEtf": False,
                    "isFund": False, "sector": "Technology", "industry": "Software"}
# Profiles that must NOT read as a fund: only a real `True` counts.
_NOT_FUND_PROFILES = [
    pytest.param({}, id="empty"),
    pytest.param({"isEtf": "true"}, id="string-true"),
    pytest.param({"isEtf": 1}, id="int-1"),
    pytest.param({"isEtf": False, "isFund": False}, id="both-false"),
    pytest.param({"isEtf": None}, id="null"),
    pytest.param([{"isEtf": True}], id="list-wrapped"),
    pytest.param(None, id="none"),
]

_INCOME = [{"fiscalYear": 2025, "date": "2025-09-27", "revenue": 416.0 * B,
            "costOfRevenue": 220.0 * B, "operatingExpenses": 62.0 * B,
            "incomeTaxExpense": 20.0 * B, "netIncome": 112.0 * B}]
_SEG = [{"fiscalYear": 2025, "date": "2025-09-27",
         "data": {"iPhone": 209.6 * B, "Services": 109.2 * B, "Mac": 33.7 * B,
                  "iPad": 28.4 * B, "Wearables": 35.1 * B}}]

_UNSET: Any = object()


def _answer(v: Any) -> Any:
    if isinstance(v, BaseException):
        raise v
    return v


def _inline_executor(monkeypatch) -> None:
    """Run every executor job (the fire-and-forget upsert, `asyncio.to_thread`) inline, so
    "nothing was written" is a fact the test reads, not a race it might lose."""
    loop = asyncio.get_running_loop()

    def _run(executor, fn, *args):
        fut = loop.create_future()
        try:
            fut.set_result(fn(*args))
        except BaseException as exc:  # noqa: BLE001 — handed to the awaiting caller
            fut.set_exception(exc)
        return fut

    monkeypatch.setattr(loop, "run_in_executor", _run)


def _fund_shape_logs(caplog, step: str) -> List[logging.LogRecord]:
    return [r for r in caplog.records
            if r.levelno == logging.INFO and r.getMessage().startswith("[fund-shape]")
            and f"step={step}" in r.getMessage()]


# ═══════════════════════════════════════════════════════════════════════════════════
# Step 3 — Revenue Breakdown
# ═══════════════════════════════════════════════════════════════════════════════════


class _RevFMP:
    """Each feed is a value to return or an exception to raise; every call is counted.
    `profile=_UNSET` leaves `get_company_profile` OFF the fake (AttributeError on use), like
    the existing revenue fakes."""

    def __init__(self, seg: Any = (), inc: Any = (), ec: Any = (), profile: Any = _UNSET):
        self._seg = list(seg) if isinstance(seg, tuple) else seg
        self._inc = list(inc) if isinstance(inc, tuple) else inc
        self._ec = list(ec) if isinstance(ec, tuple) else ec
        self.calls: List[str] = []
        if profile is not _UNSET:
            self._profile = profile

            async def get_company_profile(ticker):
                self.calls.append("profile")
                return _answer(self._profile)

            self.get_company_profile = get_company_profile

    async def get_revenue_product_segmentation(self, *a, **k):
        self.calls.append("segmentation")
        return _answer(self._seg)

    async def get_income_statement(self, *a, **k):
        self.calls.append("income")
        return _answer(self._inc)

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        self.calls.append("calendar")
        if isinstance(self._ec, BaseException):
            if raise_errors:
                raise self._ec
            return []
        if not isinstance(self._ec, list):
            if raise_errors:
                raise FMPUnavailableException("calendar: non-list body")
            return []
        return self._ec


class _Result:
    def __init__(self, data):
        self.data = data


class _RevSB:
    """Supabase fake for `revenue_breakdown_cache`: serves the last upserted row (or a
    preset one) and records every upsert."""

    def __init__(self, row: Optional[Dict[str, Any]] = None):
        self.row = row
        self.upserts: List[Dict[str, Any]] = []

    def table(self, name):
        assert name == "revenue_breakdown_cache", name
        sb = self

        class _T:
            def select(self, *a, **k): return self
            def eq(self, *a, **k): return self
            def limit(self, *a, **k): return self
            def execute(self): return _Result([sb.row] if sb.row else [])

            def upsert(self, row, **k):
                sb.upserts.append(row)
                sb.row = row

                class _E:
                    def execute(self_inner): return None
                return _E()
        return _T()


def _rev_svc(fmp: _RevFMP, row: Optional[Dict[str, Any]] = None) -> rb.RevenueBreakdownService:
    svc = rb.RevenueBreakdownService.__new__(rb.RevenueBreakdownService)
    svc.fmp = fmp
    svc.supabase = _RevSB(row)
    return svc


@pytest.fixture(autouse=True)
def _clean_module_state():
    for mod in (rb, ps):
        mod._cache.clear()
        mod._inflight.clear()
    ps._degraded_by_key.clear()
    ps._fund_shape_by_key.clear()
    S._cache.clear()
    yield
    for mod in (rb, ps):
        mod._cache.clear()
        mod._inflight.clear()
    ps._degraded_by_key.clear()
    ps._fund_shape_by_key.clear()
    S._cache.clear()


def _rev_row(body: Dict[str, Any], **extra: Any) -> Dict[str, Any]:
    return {"response_json": {**body, rb._VERSION_KEY: rb._RB_PAYLOAD_VERSION, **extra},
            "cached_at": datetime.now(timezone.utc).isoformat(), "next_earnings_date": None}


_FUND_CARD = {"symbol": "VB", "fiscal_year": "",
              "revenue_sources": [{"name": "Total Revenue", "value": 0.0}],
              "cost_of_sales": 0.0, "operating_expense": 0.0, "tax": 0.0,
              "degraded": ["income_statement_empty"]}
_GOOD_CARD = {"symbol": "AAPL", "fiscal_year": "2025",
              "revenue_sources": [{"name": "iPhone", "value": 1.0}],
              "cost_of_sales": 1.0, "operating_expense": 1.0, "tax": 0.0,
              "reported_revenue": 1.0}


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [_FUND_PROFILE, _MUTUAL_FUND_PROFILE], ids=["etf", "mutual-fund"])
async def test_a_fund_empty_card_is_persisted_once_with_the_marker_and_served_from_tier2(
    monkeypatch, caplog, profile,
):
    caplog.set_level(logging.INFO, logger=rb.__name__)
    _inline_executor(monkeypatch)
    fmp = _RevFMP(seg=[], inc=[], ec=[], profile=dict(profile))
    svc = _rev_svc(fmp)

    r = await svc.get_revenue_breakdown("VB")
    # The RESPONSE is unchanged — iOS still sees the empty card and its reason.
    assert r.degraded == ["income_statement_empty"]
    assert rb._is_placeholder_only(r)
    assert fmp.calls.count("profile") == 1, "the profile is fetched exactly once"
    assert len(svc.supabase.upserts) == 1
    stored = svc.supabase.upserts[0]["response_json"]
    assert stored[rb._SECURITY_KIND_KEY] == rb._SECURITY_KIND_FUND
    assert stored[rb._VERSION_KEY] == rb._RB_PAYLOAD_VERSION
    assert stored["degraded"] == ["income_statement_empty"]
    assert _fund_shape_logs(caplog, "revenue_breakdown_write"), "the admission must be logged"

    # The memory tier keeps the NORMAL 5 min (not the 60 s degraded TTL).
    real_time = rb.time.time
    now = real_time()
    monkeypatch.setattr(rb.time, "time", lambda: now + rb._DEGRADED_CACHE_TTL + 5)
    assert rb._cache_get("rev_breakdown:VB") is r
    monkeypatch.setattr(rb.time, "time", real_time)

    # Memory gone: a Tier-2 hit makes ZERO FMP calls and serves the same card.
    rb._cache.clear()
    fmp.calls.clear()
    again = await svc.get_revenue_breakdown("VB")
    assert fmp.calls == [], "a Tier-2 hit must not touch FMP"
    assert again.degraded == ["income_statement_empty"]
    assert again.model_dump() == r.model_dump(), "the marker must never reach the model"
    assert _fund_shape_logs(caplog, "revenue_breakdown_read")


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", _NOT_FUND_PROFILES)
async def test_an_empty_card_without_a_positive_fund_flag_is_not_persisted(monkeypatch, profile):
    _inline_executor(monkeypatch)
    fmp = _RevFMP(seg=[], inc=[], ec=[], profile=profile)
    svc = _rev_svc(fmp)
    r = await svc.get_revenue_breakdown("ACME")
    assert r.degraded == ["income_statement_empty"]
    assert fmp.calls.count("profile") == 1
    assert svc.supabase.upserts == [], "an operating company's empty answer may be an outage"
    now = time.time()
    monkeypatch.setattr(rb.time, "time", lambda: now + rb._DEGRADED_CACHE_TTL + 1)
    assert rb._cache_get("rev_breakdown:ACME") is None, "keeps today's 60 s retry"


@pytest.mark.asyncio
@pytest.mark.parametrize("profile_exc", [
    FMPRateLimitException("HTTP 429", retry_after="60"),
    FMPUnavailableException("HTTP 503"),
    RuntimeError("client bug"),
])
async def test_a_failed_profile_check_is_not_a_fund_and_warns_with_ticker_and_step(
    monkeypatch, caplog, profile_exc,
):
    caplog.set_level(logging.INFO, logger=rb.__name__)
    _inline_executor(monkeypatch)
    svc = _rev_svc(_RevFMP(seg=[], inc=[], ec=[], profile=profile_exc))
    r = await svc.get_revenue_breakdown("VB")
    assert r.degraded == ["income_statement_empty"]
    assert svc.supabase.upserts == []
    warnings = [rec.getMessage() for rec in caplog.records if rec.levelno == logging.WARNING]
    assert any("ticker=VB" in m and "step=fund_check" in m and type(profile_exc).__name__ in m
               for m in warnings), warnings
    assert not _fund_shape_logs(caplog, "revenue_breakdown_write")


@pytest.mark.asyncio
async def test_a_client_without_a_profile_method_degrades_to_not_a_fund(monkeypatch, caplog):
    """The existing revenue fakes have no `get_company_profile`; the check must log and
    refuse, never raise out of the build."""
    caplog.set_level(logging.WARNING, logger=rb.__name__)
    _inline_executor(monkeypatch)
    svc = _rev_svc(_RevFMP(seg=[], inc=[], ec=[]))
    r = await svc.get_revenue_breakdown("VB")
    assert r.degraded == ["income_statement_empty"] and svc.supabase.upserts == []
    assert any("step=fund_check" in rec.getMessage() for rec in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("seg,inc,ec,expected_degraded", [
    # Junk income rows: `_as_list` empties them, but that is malformed data, not a fund.
    pytest.param([], ["x", 3, None], [], ["income_statement_empty"], id="junk-income-rows"),
    pytest.param([], ["junk"], [], ["income_statement_empty"], id="one-junk-income-row"),
    # A dict row with no revenue is data with a hole, not an empty statement.
    pytest.param([], [{"fiscalYear": 2025, "revenue": None}], [], ["revenue_unreported"],
                 id="income-row-without-revenue"),
    # Segmentation failed (raised / error dict): two reasons, never the fund shape.
    pytest.param(FMPUnavailableException("seg 503"), [], [],
                 ["segmentation_unavailable", "income_statement_empty"], id="seg-raised"),
    pytest.param({"Error Message": "Limit Reach"}, [], [],
                 ["segmentation_unavailable", "income_statement_empty"], id="seg-error-dict"),
    # Segmentation rows beside an empty income statement: not what a fund answers.
    pytest.param(_SEG, [], [], ["income_statement_empty"], id="seg-rows-income-empty"),
    pytest.param(["junk-seg"], [], [], ["income_statement_empty"], id="junk-seg-rows"),
    # Calendar failed: no next-earnings bound — two reasons.
    pytest.param([], [], FMPRateLimitException("cal 429"),
                 ["earnings_calendar_unavailable", "income_statement_empty"], id="calendar-raised"),
    pytest.param([], [], {"Error Message": "x"},
                 ["earnings_calendar_unavailable", "income_statement_empty"], id="calendar-dict"),
])
async def test_a_fund_whose_other_legs_misbehaved_is_not_persisted_and_costs_no_profile_call(
    monkeypatch, seg, inc, ec, expected_degraded,
):
    _inline_executor(monkeypatch)
    fmp = _RevFMP(seg=seg, inc=inc, ec=ec, profile=dict(_FUND_PROFILE))
    svc = _rev_svc(fmp)
    r = await svc.get_revenue_breakdown("VB")
    assert r.degraded == expected_degraded
    assert "profile" not in fmp.calls, "only the exact empty shape pays for the profile call"
    assert svc.supabase.upserts == []


@pytest.mark.asyncio
async def test_a_build_with_income_rows_never_fetches_the_profile_and_stays_unmarked(monkeypatch):
    _inline_executor(monkeypatch)
    fmp = _RevFMP(seg=_SEG, inc=_INCOME, ec=[], profile=dict(_FUND_PROFILE))
    svc = _rev_svc(fmp)
    r = await svc.get_revenue_breakdown("AAPL")
    assert r.degraded == [] and rb._is_cacheable(r)
    assert "profile" not in fmp.calls
    assert len(svc.supabase.upserts) == 1
    assert rb._SECURITY_KIND_KEY not in svc.supabase.upserts[0]["response_json"]


@pytest.mark.asyncio
async def test_a_fund_labelled_filer_with_real_statements_takes_the_normal_path(monkeypatch):
    """Outlier: isEtf True with NON-empty statements (a BDC, a mislabelled company) — no
    profile call, no marker, ordinary complete-build caching."""
    _inline_executor(monkeypatch)
    fmp = _RevFMP(seg=[], inc=_INCOME, ec=[], profile=dict(_FUND_PROFILE))
    svc = _rev_svc(fmp)
    r = await svc.get_revenue_breakdown("ARCC")
    assert r.degraded == [] and [s.name for s in r.revenue_sources] == ["Total Revenue"]
    assert "profile" not in fmp.calls
    assert rb._SECURITY_KIND_KEY not in svc.supabase.upserts[0]["response_json"]


@pytest.mark.asyncio
async def test_concurrent_views_of_a_fund_share_one_build_and_one_profile_call(monkeypatch):
    _inline_executor(monkeypatch)
    fmp = _RevFMP(seg=[], inc=[], ec=[], profile=dict(_FUND_PROFILE))
    svc = _rev_svc(fmp)
    out = await asyncio.gather(*(svc.get_revenue_breakdown("VB") for _ in range(5)))
    assert all(o is out[0] for o in out)
    assert fmp.calls.count("profile") == 1 and fmp.calls.count("income") == 1
    assert len(svc.supabase.upserts) == 1
    assert rb._inflight == {}


@pytest.mark.parametrize("body,extra,served", [
    pytest.param(_FUND_CARD, {}, False, id="placeholder-unmarked"),
    pytest.param(_FUND_CARD, {"security_kind": "fund"}, True, id="placeholder-marked"),
    pytest.param(_FUND_CARD, {"security_kind": "etf"}, False, id="wrong-marker-value"),
    pytest.param(_FUND_CARD, {"security_kind": True}, False, id="bool-marker"),
    pytest.param({**_FUND_CARD, "degraded": ["segmentation_unavailable"]},
                 {"security_kind": "fund"}, False, id="marked-but-seg-failed"),
    pytest.param({**_FUND_CARD, "degraded": ["income_statement_empty", "earnings_calendar_unavailable"]},
                 {"security_kind": "fund"}, False, id="marked-but-calendar-failed"),
    pytest.param({**_FUND_CARD, "degraded": []}, {"security_kind": "fund"}, False,
                 id="marked-zero-card-without-the-reason"),
    pytest.param({**_FUND_CARD, "revenue_sources": [{"name": "iPhone", "value": 5.0}]},
                 {"security_kind": "fund"}, False, id="marked-but-not-a-placeholder"),
    pytest.param(_GOOD_CARD, {"security_kind": "fund"}, True, id="marker-on-a-complete-card"),
    pytest.param(_GOOD_CARD, {}, True, id="complete-card-control"),
])
def test_the_read_tier_admits_the_empty_card_only_on_a_marked_row(body, extra, served, caplog):
    caplog.set_level(logging.INFO, logger=rb.__name__)
    svc = _rev_svc(_RevFMP(), row=_rev_row(body, **extra))
    got = svc._check_supabase_cache("VB")
    assert (got is not None) is served
    if got is not None:
        assert rb._SECURITY_KIND_KEY not in got.model_dump()
    if not served:
        assert any("REFUSED" in rec.getMessage() for rec in caplog.records)


def test_the_reader_does_not_mutate_the_sdk_row():
    row = _rev_row(_FUND_CARD, security_kind="fund")
    svc = _rev_svc(_RevFMP(), row=row)
    assert svc._check_supabase_cache("VB") is not None
    assert row["response_json"][rb._SECURITY_KIND_KEY] == "fund", "the marker was popped off the row"


@pytest.mark.parametrize("resp_body,fund_shape,written,marked", [
    pytest.param(_FUND_CARD, False, False, False, id="fund-card-no-verdict"),
    pytest.param(_FUND_CARD, True, True, True, id="fund-card-with-verdict"),
    pytest.param({**_FUND_CARD, "degraded": ["segmentation_unavailable"]}, True, False, False,
                 id="verdict-cannot-admit-a-failed-feed"),
    pytest.param({**_FUND_CARD, "degraded": ["revenue_unreported"]}, True, False, False,
                 id="verdict-cannot-admit-revenue-unreported"),
    pytest.param(_GOOD_CARD, True, True, False, id="complete-card-never-marked"),
])
def test_the_write_backstop(resp_body, fund_shape, written, marked):
    svc = _rev_svc(_RevFMP())
    svc._upsert_supabase_cache_safe("VB", RevenueBreakdownResponse(**resp_body), None, fund_shape)
    assert bool(svc.supabase.upserts) is written
    if written:
        assert (rb._SECURITY_KIND_KEY in svc.supabase.upserts[0]["response_json"]) is marked


def test_is_cacheable_keeps_its_default_behaviour():
    fund = RevenueBreakdownResponse(**_FUND_CARD)
    assert not rb._is_cacheable(fund), "default (no verdict) still refuses the empty card"
    assert rb._is_cacheable(fund, fund_shape=True)
    assert rb._is_cacheable(RevenueBreakdownResponse(**_GOOD_CARD))


@pytest.mark.asyncio
async def test_the_builder_adapter_still_returns_the_two_tuple():
    svc = _rev_svc(_RevFMP(seg=[], inc=[], ec=[], profile=dict(_FUND_PROFILE)))
    out = await svc._build_revenue_breakdown("VB")
    assert isinstance(out, tuple) and len(out) == 2
    resp, nxt = out
    assert resp.degraded == ["income_statement_empty"] and nxt is None
    assert "profile" not in svc.fmp.calls, "the adapter makes no fund check of its own"


# ═══════════════════════════════════════════════════════════════════════════════════
# Step 4 — overview fundamentals bundle
# ═══════════════════════════════════════════════════════════════════════════════════


def _history_rows(n: int = 30) -> List[Dict[str, Any]]:
    """Settled rows ending three days ago (the frozen cycle below is yesterday's)."""
    end = datetime.now(tz=timezone.utc).date() - timedelta(days=3)
    return [{"date": (end - timedelta(days=n - 1 - i)).isoformat(), "open": 100.0,
             "high": 100.0, "low": 100.0, "close": 100.0 + i, "volume": 1_000}
            for i in range(n)]


class _OverviewFMP:
    """Every FMP call the fundamentals fan-out makes; unknown methods answer []."""

    def __init__(self, profile: Any, key_metrics: Any, history: Any = _UNSET):
        self.profile, self.key_metrics = profile, key_metrics
        self.history = _history_rows() if history is _UNSET else history
        self.calls: List[str] = []

    async def get_company_profile(self, ticker):
        self.calls.append("profile")
        return _answer(self.profile)

    async def get_key_metrics(self, ticker, period="annual", limit=5):
        self.calls.append("key_metrics")
        return _answer(self.key_metrics)

    async def get_historical_prices(self, symbol, frm, to):
        self.calls.append(f"history:{symbol}")
        if symbol == "SPY":
            return _history_rows()
        if isinstance(self.history, BaseException):
            raise self.history
        return [dict(r) for r in self.history]

    def __getattr__(self, name):
        async def _call(*args, **kwargs):
            self.calls.append(name)
            return []
        return _call


class _NoMovers:
    async def get_sector_performance(self):
        return []

    async def get_industry_performance(self):
        return []


def _overview_svc(monkeypatch, fmp: _OverviewFMP):
    async def _no_short_interest(ticker):
        return {}

    yesterday = datetime.now(tz=timezone.utc).date() - timedelta(days=1)
    boundary = datetime(yesterday.year, yesterday.month, yesterday.day, 18,
                        tzinfo=ET).astimezone(timezone.utc)
    monkeypatch.setattr(S, "current_close_cycle_start", lambda now=None: boundary)
    monkeypatch.setattr(S, "get_short_interest", _no_short_interest)
    monkeypatch.setattr(S, "get_market_movers_service", lambda: _NoMovers())
    svc = S.StockOverviewService.__new__(S.StockOverviewService)
    svc.fmp = fmp
    svc.supabase = None
    upserts: List[Dict[str, Any]] = []
    monkeypatch.setattr(svc, "_check_fundamentals_db", lambda ticker: None)
    monkeypatch.setattr(svc, "_upsert_fundamentals_db",
                        lambda ticker, data: upserts.append(dict(data)))
    return svc, upserts


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [_FUND_PROFILE, _MUTUAL_FUND_PROFILE], ids=["etf", "mutual-fund"])
async def test_a_fund_bundle_with_empty_key_metrics_is_cached_in_both_tiers(
    monkeypatch, caplog, profile,
):
    caplog.set_level(logging.INFO, logger=S.__name__)
    fmp = _OverviewFMP(profile=dict(profile), key_metrics=[])
    svc, upserts = _overview_svc(monkeypatch, fmp)

    data = await svc._get_fundamentals("VB")
    assert data["key_metrics"] == []
    assert S._ANSWERED_LISTS_KEY not in data, "the internal key leaked to the caller"
    assert len(upserts) == 1, "the fund bundle must reach the 24 h tier"
    assert S._ANSWERED_LISTS_KEY not in upserts[0], "the internal key leaked into Supabase"
    mem = S._cache.get("fundamentals:VB")
    assert mem is not None and S._ANSWERED_LISTS_KEY not in mem[1]
    assert _fund_shape_logs(caplog, "overview_fundamentals")

    # A second view inside the hour is a memory hit: zero FMP calls.
    fmp.calls.clear()
    again = await svc._get_fundamentals("VB")
    assert fmp.calls == [] and again is data


@pytest.mark.asyncio
@pytest.mark.parametrize("profile,key_metrics,history,why", [
    pytest.param(dict(_COMPANY_PROFILE), [], _UNSET, "operating company", id="company-empty-km"),
    pytest.param({"companyName": "Unknown", "isEtf": "true"}, [], _UNSET, "string flag",
                 id="string-flag"),
    pytest.param(dict(_FUND_PROFILE), FMPRateLimitException("429"), _UNSET, "km raised",
                 id="fund-km-raised"),
    pytest.param(dict(_FUND_PROFILE), {"Error Message": "Limit Reach"}, _UNSET, "km error dict",
                 id="fund-km-error-dict"),
    pytest.param(dict(_FUND_PROFILE), None, _UNSET, "km null body", id="fund-km-null"),
    pytest.param(dict(_FUND_PROFILE), [], [], "no price history", id="fund-empty-history"),
    pytest.param(dict(_FUND_PROFILE), [], FMPUnavailableException("503"), "history raised",
                 id="fund-history-raised"),
    pytest.param({}, [], _UNSET, "empty profile", id="empty-profile"),
    pytest.param(FMPUnavailableException("profile 503"), [], _UNSET, "profile raised",
                 id="profile-raised"),
])
async def test_the_waiver_never_admits_a_failed_or_unproven_bundle(
    monkeypatch, caplog, profile, key_metrics, history, why,
):
    caplog.set_level(logging.INFO, logger=S.__name__)
    fmp = _OverviewFMP(profile=profile, key_metrics=key_metrics, history=history)
    svc, upserts = _overview_svc(monkeypatch, fmp)
    data = await svc._get_fundamentals("VB")
    assert S._ANSWERED_LISTS_KEY not in data
    assert upserts == [], f"{why}: a bundle that may hide an outage reached the 24 h tier"
    assert "fundamentals:VB" not in S._cache, f"{why}: kept in memory"
    assert not _fund_shape_logs(caplog, "overview_fundamentals")


@pytest.mark.asyncio
async def test_a_company_bundle_with_key_metrics_is_cached_as_before(monkeypatch):
    """Negative control: the ordinary path is untouched."""
    fmp = _OverviewFMP(profile=dict(_COMPANY_PROFILE), key_metrics=[{"peRatio": 20.0}])
    svc, upserts = _overview_svc(monkeypatch, fmp)
    data = await svc._get_fundamentals("ACME")
    assert len(upserts) == 1 and S._ANSWERED_LISTS_KEY not in data


@pytest.mark.asyncio
@pytest.mark.parametrize("answered", [
    pytest.param(_UNSET, id="absent"),
    pytest.param("key_metrics", id="a-string-is-not-a-set"),
    pytest.param(None, id="none"),
    pytest.param(frozenset(), id="empty-set"),
])
async def test_a_replaced_fetch_without_a_proper_answered_set_gets_no_waiver(monkeypatch, answered):
    """The `_fetch_fundamentals` fakes in other suites carry no internal key. A missing (or
    malformed) key must mean NO waiver — a string "key_metrics" must not pass an `in`."""
    svc, upserts = _overview_svc(monkeypatch, _OverviewFMP(profile={}, key_metrics=[]))
    bundle = {"profile": dict(_FUND_PROFILE), "stock_historical": _history_rows(),
              "key_metrics": [], S._SETTLED_THROUGH_KEY: S._settled_cutoff_date()}
    if answered is not _UNSET:
        bundle[S._ANSWERED_LISTS_KEY] = answered

    async def _fetch(ticker):
        return dict(bundle)

    monkeypatch.setattr(svc, "_fetch_fundamentals", _fetch)
    data = await svc._get_fundamentals("VB")
    assert upserts == [] and "fundamentals:VB" not in S._cache
    assert S._ANSWERED_LISTS_KEY not in data


@pytest.mark.asyncio
async def test_fetch_fundamentals_names_only_the_raw_list_answers(monkeypatch):
    fmp = _OverviewFMP(profile=dict(_FUND_PROFILE), key_metrics={"Error Message": "x"})

    async def _ratios_raise(*a, **k):
        raise FMPUnavailableException("ratios 503")

    fmp.get_financial_ratios = _ratios_raise
    svc, _ = _overview_svc(monkeypatch, fmp)
    bundle = await svc._fetch_fundamentals("VB")
    answered = bundle[S._ANSWERED_LISTS_KEY]
    assert "key_metrics" not in answered, "an error dict is not a list answer"
    assert "fin_ratios" not in answered, "a raised leg is not a list answer"
    assert {"income_annual", "balance_annual", "cashflow_annual"} <= answered
    assert bundle["key_metrics"] == [] and bundle["fin_ratios"] == [], "coercion unchanged"


# ── The cached bundle never carries the profile's LIVE price (fix pass, 2026-10-01) ──────
#
# The fund waiver let a fund's bundle reach both tiers, and `_upsert_fundamentals_db`
# stored the raw profile — price, change and % included. `_build_full_response` falls back
# to those when the quote leg fails (`_get_volatile` folds the failure to `{}`), so a view
# up to 24 h later painted the CACHED price as live with `change_known=True`. Same exposure
# for an operating company. The live fields are now dropped from what either tier keeps.

_LIVE = {"price": 250.0, "change": 3.0, "changePercentage": 1.2, "changesPercentage": 1.2}
_FAILED_QUOTE = {"quote": {}, "chart_data": None}


def _full(svc, fund: Dict[str, Any], vol: Dict[str, Any]):
    # The fallback Price card reads no benchmark (2026-10-07), so this builds offline.
    return svc._build_full_response("VB", fund, vol, "3M", None, False)


@pytest.mark.asyncio
@pytest.mark.parametrize("profile,key_metrics", [
    pytest.param(_FUND_PROFILE, [], id="etf-waived"),
    pytest.param(_MUTUAL_FUND_PROFILE, [], id="mutual-fund-waived"),
    pytest.param(_COMPANY_PROFILE, [{"peRatio": 20.0}], id="company"),
])
async def test_a_cache_hit_with_a_failed_quote_raises_instead_of_serving_a_cached_price(
    monkeypatch, profile, key_metrics,
):
    fmp = _OverviewFMP(profile=dict(profile, **_LIVE), key_metrics=key_metrics)
    svc, upserts = _overview_svc(monkeypatch, fmp)

    # The fetching request keeps its (seconds-old) profile price as the fallback.
    first = await svc._get_fundamentals("VB")
    assert first["profile"]["price"] == 250.0, "the fetching request lost its own profile"
    fresh = _full(svc, first, _FAILED_QUOTE)
    assert fresh.current_price == 250.0 and fresh.change_known is True

    # Neither tier keeps a live field; everything else in the profile survives.
    assert len(upserts) == 1
    mem = S._cache["fundamentals:VB"][1]
    for kept in (upserts[0]["profile"], mem["profile"]):
        assert not set(_LIVE) & set(kept), f"a live price field was cached: {kept}"
        assert kept["companyName"] == profile["companyName"]
        assert kept.get("isEtf") == profile.get("isEtf")
    assert mem["stock_historical"] and mem["key_metrics"] == key_metrics

    # FMP's price moves; the next view is a memory hit with zero FMP calls.
    fmp.profile = dict(profile, price=999.0, change=-50.0, changePercentage=-5.0)
    fmp.calls.clear()
    hit = await svc._get_fundamentals("VB")
    assert fmp.calls == []
    with pytest.raises(FMPUnavailableException, match="No usable price"):
        _full(svc, hit, _FAILED_QUOTE)

    # Positive control: a live quote on the same hit is served from the quote.
    live = _full(svc, hit, {"quote": {"price": 260.0, "change": 1.0, "changePercentage": 0.4},
                            "chart_data": None})
    assert (live.current_price, live.price_change, live.price_change_percent) == (260.0, 1.0, 0.4)
    # The quote's price without a change is UNKNOWN, never the cached profile's change.
    no_change = _full(svc, hit, {"quote": {"price": 260.0}, "chart_data": None})
    assert no_change.change_known is False


@pytest.mark.asyncio
async def test_a_legacy_tier2_row_with_a_profile_price_is_stripped_on_read(monkeypatch):
    """A row written before the fix still holds the price until its ceiling / close cycle."""
    fmp = _OverviewFMP(profile=dict(_FUND_PROFILE), key_metrics=[])
    svc, upserts = _overview_svc(monkeypatch, fmp)
    row = {"profile": dict(_FUND_PROFILE, **_LIVE), "key_metrics": [],
           "stock_historical": _history_rows(), S._SETTLED_THROUGH_KEY: S._settled_cutoff_date()}
    monkeypatch.setattr(svc, "_check_fundamentals_db", lambda ticker: row)

    got = await svc._get_fundamentals("VB")
    assert fmp.calls == [] and upserts == [], "a tier-2 hit refetched or rewrote"
    assert not set(_LIVE) & set(got["profile"])
    assert got["profile"]["companyName"] == _FUND_PROFILE["companyName"]
    assert not set(_LIVE) & set(S._cache["fundamentals:VB"][1]["profile"])
    assert row["profile"]["price"] == 250.0, "the SDK's row object was mutated"
    with pytest.raises(FMPUnavailableException, match="No usable price"):
        _full(svc, got, _FAILED_QUOTE)


@pytest.mark.parametrize("bundle", [
    pytest.param(None, id="none"),
    pytest.param([{"profile": {"price": 1.0}}], id="list"),
    pytest.param({"profile": None}, id="profile-none"),
    pytest.param({"profile": [{"price": 1.0}]}, id="profile-list"),
    pytest.param({"profile": {}}, id="profile-empty"),
    pytest.param({"profile": {"companyName": "X", "mktCap": 5.0}}, id="no-live-fields"),
])
def test_without_live_price_returns_a_bundle_with_nothing_to_drop_unchanged(bundle):
    assert S._without_live_price(bundle) is bundle


@pytest.mark.parametrize("field", list(_LIVE))
def test_without_live_price_drops_each_live_field_without_mutating_the_input(field):
    profile = {"companyName": "X", "mktCap": 5.0, "beta": 1.1, field: 7.0}
    bundle = {"profile": profile, "key_metrics": [1], "stock_historical": [2]}
    out = S._without_live_price(bundle)
    assert out is not bundle and field not in out["profile"]
    assert out["profile"] == {"companyName": "X", "mktCap": 5.0, "beta": 1.1}
    assert profile[field] == 7.0 and bundle["profile"] is profile, "input mutated"
    assert out["key_metrics"] is bundle["key_metrics"], "only the profile is replaced"


# ═══════════════════════════════════════════════════════════════════════════════════
# Step 5 — profitability snapshot (FUNDS ONLY)
# ═══════════════════════════════════════════════════════════════════════════════════


class _ProfFMP:
    def __init__(self, **answers: Any) -> None:
        self._answers = answers
        self.calls: List[str] = []

    def __getattr__(self, name: str):
        async def _call(*args, **kwargs):
            self.calls.append(name)
            return _answer(self._answers.get(name, []))
        return _call


class _PlainLookup:
    def get_current_benchmark_values(self, industry, sector, metrics):
        return {m: None for m in metrics}

    def get_current_benchmarks(self, industry, sector, metrics):
        return {m: None for m in metrics}


def _prof_svc(monkeypatch, fmp_answers: Dict[str, Any], pp: Any = None, lookup: Any = None):
    from app.services import profit_power_service

    pp = SimpleNamespace(annual=[], degraded=[]) if pp is None else pp
    pp_calls: List[str] = []

    class _ProfitPower:
        async def get_profit_power(self, ticker):
            pp_calls.append(ticker)
            return _answer(pp)

    monkeypatch.setattr(profit_power_service, "get_profit_power_service", lambda: _ProfitPower())
    monkeypatch.setattr(ps, "get_sector_benchmark_lookup", lambda: lookup or _PlainLookup())
    svc = ps.ProfitabilitySnapshotService.__new__(ps.ProfitabilitySnapshotService)
    svc.fmp = _ProfFMP(**fmp_answers)
    svc.supabase = None
    tier2_reads: List[str] = []

    def _no_tier2(ticker):
        tier2_reads.append(ticker)
        return None

    monkeypatch.setattr(svc, "_check_supabase_cache", _no_tier2)
    persisted: List[str] = []
    monkeypatch.setattr(svc, "_upsert_supabase_cache", lambda t, r: persisted.append(t))
    return svc, persisted, pp_calls


@pytest.mark.asyncio
@pytest.mark.parametrize("profile", [_FUND_PROFILE, _MUTUAL_FUND_PROFILE], ids=["etf", "mutual-fund"])
async def test_a_fund_no_values_build_is_kept_in_memory_never_persisted_status_intact(
    monkeypatch, caplog, profile,
):
    caplog.set_level(logging.INFO, logger=ps.__name__)
    _inline_executor(monkeypatch)
    svc, persisted, pp_calls = _prof_svc(monkeypatch, {
        "get_key_metrics_ttm": [], "get_company_profile": dict(profile), "get_ratios_ttm": [],
    })
    snap, degraded = await svc.get_profitability_snapshot_with_status("VB")
    assert all(m.value == "—" for m in snap.metrics)
    assert degraded == ["no_values"], "the collector still reads it as company state"
    assert persisted == [], "a fund's 3/5 sentinel must never reach the 24 h tier"
    assert "prof_snapshot:VB" in ps._cache
    assert _fund_shape_logs(caplog, "profitability_snapshot")

    svc.fmp.calls.clear()
    pp_calls.clear()
    snap2, degraded2 = await svc.get_profitability_snapshot_with_status("VB")
    assert svc.fmp.calls == [] and pp_calls == [], "a second view within 5 min made calls"
    assert snap2 is snap and degraded2 == ["no_values"]
    assert persisted == []
    assert ps._fund_shape_by_key == {}, "the verdict memo must be consumed by the gate"

    # After the normal 5-min TTL the card is rebuilt (no 24 h freeze in memory either).
    now = time.time()
    monkeypatch.setattr(ps.time, "time", lambda: now + ps._CACHE_TTL + 1)
    await svc.get_profitability_snapshot_with_status("VB")
    assert "get_company_profile" in svc.fmp.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("answers,pp,lookup,expected", [
    pytest.param({"get_company_profile": dict(_COMPANY_PROFILE)}, None, None, ["no_values"],
                 id="operating-company"),
    pytest.param({"get_company_profile": {"isEtf": "true"}}, None, None, ["no_values"],
                 id="string-flag"),
    pytest.param({"get_company_profile": FMPUnavailableException("p")}, None, None,
                 ["profile", "no_values"], id="profile-raised"),
    pytest.param({"get_company_profile": dict(_FUND_PROFILE),
                  "get_key_metrics_ttm": FMPRateLimitException("429")}, None, None,
                 ["key_metrics_ttm", "no_values"], id="fund-km-raised"),
    pytest.param({"get_company_profile": dict(_FUND_PROFILE),
                  "get_key_metrics_ttm": {"Error Message": "Limit Reach"}}, None, None,
                 ["no_values"], id="fund-km-error-dict"),
    pytest.param({"get_company_profile": dict(_FUND_PROFILE),
                  "get_ratios_ttm": {"Error Message": "Limit Reach"}}, None, None,
                 ["no_values"], id="fund-ratios-error-dict"),
    pytest.param({"get_company_profile": dict(_FUND_PROFILE),
                  "get_ratios_ttm": FMPUnavailableException("503")}, None, None,
                 ["ratios_ttm", "no_values"], id="fund-ratios-raised"),
    pytest.param({"get_company_profile": dict(_FUND_PROFILE)},
                 SimpleNamespace(annual=[], degraded=["annual_income"]), None,
                 ["profit_power", "no_values"], id="fund-profit-power-degraded"),
    pytest.param({"get_company_profile": dict(_FUND_PROFILE)},
                 FMPUnavailableException("pp 503"), None,
                 ["profit_power", "no_values"], id="fund-profit-power-raised"),
])
async def test_every_other_no_values_build_stays_served_uncached(
    monkeypatch, caplog, answers, pp, lookup, expected,
):
    caplog.set_level(logging.INFO, logger=ps.__name__)
    _inline_executor(monkeypatch)
    svc, persisted, _ = _prof_svc(monkeypatch, answers, pp=pp, lookup=lookup)
    snap, degraded = await svc.get_profitability_snapshot_with_status("VB")
    assert all(m.value == "—" for m in snap.metrics)
    assert degraded == expected
    assert persisted == [] and "prof_snapshot:VB" not in ps._cache, "today's retry is kept"
    assert not _fund_shape_logs(caplog, "profitability_snapshot")


@pytest.mark.asyncio
async def test_a_fund_with_a_failed_benchmark_lookup_is_not_admitted(monkeypatch):
    from app.services.sector_benchmark_lookup import BenchmarkLookupFailed

    class _Failed:
        def get_current_benchmark_values(self, industry, sector, metrics):
            return BenchmarkLookupFailed({m: None for m in metrics})

        def get_current_benchmarks(self, industry, sector, metrics):
            return BenchmarkLookupFailed({m: None for m in metrics})

    _inline_executor(monkeypatch)
    svc, persisted, _ = _prof_svc(
        monkeypatch, {"get_company_profile": {**_FUND_PROFILE, "sector": "Financial Services"}},
        lookup=_Failed(),
    )
    _, degraded = await svc.get_profitability_snapshot_with_status("VB")
    assert degraded == ["benchmarks", "no_values"]
    assert "prof_snapshot:VB" not in ps._cache and persisted == []


@pytest.mark.asyncio
async def test_a_fund_with_measured_values_takes_the_normal_clean_path(monkeypatch):
    """Outlier: a fund-flagged symbol that DOES report a ratio — measured, clean, persisted
    exactly like any company; the fund branch is only for the all-absent build."""
    _inline_executor(monkeypatch)
    svc, persisted, _ = _prof_svc(monkeypatch, {
        "get_company_profile": dict(_FUND_PROFILE),
        "get_key_metrics_ttm": [{"returnOnEquityTTM": 0.12, "returnOnAssetsTTM": 0.05}],
    })
    _, degraded = await svc.get_profitability_snapshot_with_status("BDCX")
    assert degraded == [] and persisted == ["BDCX"]
    assert ps._fund_shape_by_key == {}, "a verdict noted for a measured build must not linger"


@pytest.mark.asyncio
async def test_a_stale_verdict_for_another_object_never_admits_a_replaced_build(monkeypatch):
    """The verdict is matched by IDENTITY: a fake `_compute_with_status` (as the other
    suites install) notes nothing, and a leftover verdict for a different object is void."""
    _inline_executor(monkeypatch)
    svc, persisted, _ = _prof_svc(monkeypatch, {})
    blank = SnapshotItemResponse(
        category="Profitability", rating=3, full_report_available=True, weighted_score=3.0,
        metrics=[SnapshotMetricResponse(name="Net Margin", value="—", metric_key="net_margin")],
    )
    other = blank.model_copy()
    ps._note_fund_shape("prof_snapshot:VB", other)

    async def _fake_compute(ticker):
        return blank, []

    monkeypatch.setattr(svc, "_compute_with_status", _fake_compute)
    _, degraded = await svc.get_profitability_snapshot_with_status("VB")
    assert degraded == ["no_values"]
    assert "prof_snapshot:VB" not in ps._cache and persisted == []
    assert ps._fund_shape_by_key == {}


def test_the_verdict_memo_is_bounded():
    for i in range(ps._CACHE_MAX_ENTRIES + 50):
        ps._note_fund_shape(f"prof_snapshot:T{i}", object())
    assert len(ps._fund_shape_by_key) <= ps._CACHE_MAX_ENTRIES


# ═══════════════════════════════════════════════════════════════════════════════════
# Collector parity: what a fund's cached builds do to the report
# ═══════════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_fund_rows_served_from_cache_leave_the_report_cacheable(monkeypatch):
    from app.services.agents.ticker_report_data_collector import (
        _refuse_degraded_financials,
        _settle_snapshot_result,
    )

    _inline_executor(monkeypatch)
    # Revenue Breakdown served from the marked Tier-2 row.
    rsvc = _rev_svc(_RevFMP(), row=_rev_row(_FUND_CARD, security_kind="fund"))
    revenue = await rsvc.get_revenue_breakdown("VB")
    assert rsvc.fmp.calls == []
    # Profitability served from Tier 1 after the fund build.
    psvc, _, _ = _prof_svc(monkeypatch, {"get_company_profile": dict(_FUND_PROFILE)})
    await psvc.get_profitability_snapshot_with_status("VB")
    prof_result = await psvc.get_profitability_snapshot_with_status("VB")

    out = SimpleNamespace(ticker="VB", degraded_sections=[], revenue_breakdown=revenue,
                          snap_profitability="unset")
    _refuse_degraded_financials(out)
    _settle_snapshot_result(out, "snap_profitability", prof_result, "VB")
    assert out.degraded_sections == [], "a fund's cached cards must not block the caches"
    assert out.snap_profitability is None, "the sentinel 3/5 is never frozen into a report"
    assert out.revenue_breakdown is revenue
