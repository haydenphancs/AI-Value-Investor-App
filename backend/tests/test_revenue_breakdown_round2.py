"""Revenue Breakdown — round-2 fixes of the Financials-tab adversarial review (2026-09-30).

* R17 — the earnings-calendar "degraded" branch could never fire in production. The service
  called `FMPClient.get_earning_calendar_full(ticker)` with the default
  `raise_errors=False`, so a 429 / 5xx / error-dict body was swallowed into `[]` ("this
  ticker has no announcements"). The card was then persisted for 24 h with
  `next_earnings_date = NULL`, losing the report-day invalidation, and the first-pass test
  passed only because its fake raised straight out of the method, which the real client
  never does under the default flag. These tests drive the REAL client method with only
  `_make_request` stubbed, so dropping the flag turns them red.
* R43 — a LONE case/space variant of the filer's "Other" segment ("other", "OTHER",
  " Other ") skipped the merge branch and shipped under its raw spelling. iOS matches the
  reserved grey colour and its remainder fold on the exact string "Other", so it drew the
  variant in a palette colour AND appended a second grey "Other" row beside it.

Every assertion is the correct degraded behaviour: a `degraded` reason and no cache
write, or exactly one row spelled "Other". Never a plausible-looking wrong card. Hermetic:
`_make_request` and Supabase are faked; nothing reaches the network.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import pytest

import app.services.revenue_breakdown_service as rb
from app.integrations.fmp import (
    FMPClient,
    FMPRateLimitException,
    FMPUnavailableException,
)
from app.schemas.revenue_breakdown import RevenueBreakdownResponse
from app.services.revenue_breakdown_service import (
    RevenueBreakdownService,
    _RB_PAYLOAD_VERSION,
    _VERSION_KEY,
    _extract_segments,
    _is_cacheable,
    _reconcile_segments,
)

B = 1e9
_INCOME = [{"symbol": "AAPL", "fiscalYear": 2025, "date": "2025-09-27", "revenue": 416.0 * B,
            "costOfRevenue": 220.0 * B, "operatingExpenses": 62.0 * B,
            "incomeTaxExpense": 20.0 * B, "netIncome": 112.0 * B}]
_SEG = [{"symbol": "AAPL", "fiscalYear": 2025, "date": "2025-09-27",
         "data": {"iPhone": 209.6 * B, "Services": 109.2 * B, "Mac": 33.7 * B,
                  "iPad": 28.4 * B, "Wearables": 35.1 * B}}]
_SEG_NAMES = {"iPhone", "Services", "Mac", "iPad", "Wearables"}


class _Result:
    def __init__(self, data):
        self.data = data


class _SB:
    """Supabase fake: always a cache miss on read; records every upsert."""

    def __init__(self):
        self.upserts: List[Dict[str, Any]] = []

    def table(self, name):
        sb = self

        class _T:
            def select(self, *a, **k): return self
            def eq(self, *a, **k): return self
            def limit(self, *a, **k): return self
            def execute(self): return _Result([])

            def upsert(self, row, **k):
                sb.upserts.append(row)

                class _E:
                    def execute(self_inner): return None
                return _E()
        return _T()


def _real_client(monkeypatch, earnings: Any) -> FMPClient:
    """The REAL FMPClient (so `get_earning_calendar_full`'s own swallow-vs-raise logic
    runs) with only the transport stubbed. `earnings` is the per-symbol calendar answer:
    an exception to raise or a body to return."""
    client = FMPClient()
    calls: List[str] = []

    async def _fake_make_request(endpoint: str, params: Optional[Dict[str, Any]] = None):
        calls.append(endpoint)
        if endpoint == "earnings":
            if isinstance(earnings, BaseException):
                raise earnings
            return earnings
        if endpoint == "income-statement":
            return [dict(r) for r in _INCOME]
        if endpoint == "revenue-product-segmentation":
            return [dict(r, data=dict(r["data"])) for r in _SEG]
        raise AssertionError(f"unexpected FMP endpoint {endpoint!r}")

    monkeypatch.setattr(client, "_make_request", _fake_make_request)
    client.calls = calls  # type: ignore[attr-defined]
    return client


def _svc(fmp: Any) -> RevenueBreakdownService:
    svc = RevenueBreakdownService.__new__(RevenueBreakdownService)
    svc.fmp = fmp
    svc.supabase = _SB()
    return svc


@pytest.fixture(autouse=True)
def _clean_module_state():
    rb._cache.clear()
    rb._inflight.clear()
    yield
    rb._cache.clear()
    rb._inflight.clear()


async def _fetch_and_drain(svc: RevenueBreakdownService, ticker: str = "AAPL") -> RevenueBreakdownResponse:
    """Run the public entry point, then wait for EVERY default-executor job (the
    fire-and-forget upsert rides there), so "nothing was upserted" is a fact, not a race."""
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(executor)
    try:
        out = await svc.get_revenue_breakdown(ticker)
    finally:
        executor.shutdown(wait=True)   # the sync fake upsert needs no loop to finish
        loop.set_default_executor(ThreadPoolExecutor(max_workers=2))
    return out


# ── R17: a failed calendar marks the build degraded and is never persisted ──────────

@pytest.mark.asyncio
@pytest.mark.parametrize("earnings", [
    FMPRateLimitException("HTTP 429", retry_after="60"),
    FMPUnavailableException("HTTP 503 after retries"),
    {"Error Message": "Limit Reach . Please upgrade your plan"},   # an error dict with a 200
    "upstream said no",                                              # any other non-list body
])
async def test_a_real_client_calendar_failure_marks_the_build_degraded_and_persists_nothing(
    monkeypatch, earnings,
):
    client = _real_client(monkeypatch, earnings)
    svc = _svc(client)
    r = await _fetch_and_drain(svc)
    assert "earnings" in client.calls, "the calendar must actually be fetched"
    assert r.degraded == ["earnings_calendar_unavailable"], (
        "a swallowed calendar failure looked like 'no announcements' and was cached for 24 h"
    )
    assert not _is_cacheable(r)
    assert svc.supabase.upserts == [], "a card with no next-earnings bound was persisted"
    # The rest of the card is real data and is still served.
    assert {s.name for s in r.revenue_sources} == _SEG_NAMES
    assert r.net_income == 112.0 * B and r.fiscal_year == "2025"


@pytest.mark.asyncio
async def test_a_real_client_genuinely_empty_calendar_is_data_not_a_failure(monkeypatch):
    """No over-correction: a 200 [] is "no announcements on file" — a complete build that
    is cached (with no bound, which the 24 h age limit still caps)."""
    client = _real_client(monkeypatch, [])
    svc = _svc(client)
    r = await _fetch_and_drain(svc)
    assert r.degraded == [] and _is_cacheable(r)
    assert len(svc.supabase.upserts) == 1
    assert svc.supabase.upserts[0]["next_earnings_date"] is None


@pytest.mark.asyncio
async def test_a_real_client_successful_calendar_carries_the_next_earnings_bound(monkeypatch):
    upcoming = (date.today() + timedelta(days=20)).isoformat()
    past = (date.today() - timedelta(days=70)).isoformat()
    body = [
        {"symbol": "AAPL", "date": upcoming, "epsActual": None, "epsEstimated": 1.7,
         "revenueActual": None, "revenueEstimated": 120 * B},
        {"symbol": "AAPL", "date": past, "epsActual": 1.6, "epsEstimated": 1.5,
         "revenueActual": 110 * B, "revenueEstimated": 108 * B},
    ]
    client = _real_client(monkeypatch, body)
    svc = _svc(client)
    r = await _fetch_and_drain(svc)
    assert r.degraded == []
    assert len(svc.supabase.upserts) == 1
    row = svc.supabase.upserts[0]
    assert row["next_earnings_date"] == upcoming, "the stored row must invalidate on report day"
    assert row["response_json"][_VERSION_KEY] == _RB_PAYLOAD_VERSION


@pytest.mark.asyncio
async def test_the_calendar_is_requested_with_raise_errors():
    """Pins the call itself: the flag is what lets a failure reach the gather slot."""
    flags: List[Any] = []

    class _FMP:
        async def get_revenue_product_segmentation(self, *a, **k):
            return [dict(r) for r in _SEG]

        async def get_income_statement(self, *a, **k):
            return [dict(r) for r in _INCOME]

        async def get_earning_calendar_full(self, ticker, raise_errors=False):
            flags.append(raise_errors)
            return []

    resp, _next = await _svc(_FMP())._build_revenue_breakdown("AAPL")
    assert flags == [True]
    assert resp.degraded == []


# ── R43: exactly one row, spelled exactly "Other" ────────────────────────────────────

@pytest.mark.parametrize("key", ["other", "OTHER", " Other ", "Other ", "oTHer"])
def test_a_lone_other_variant_is_renamed_to_the_exact_other(key):
    out = _extract_segments({"data": {"A": 70 * B, key: 30 * B}})
    assert [(s.name, s.value) for s in out] == [("A", 70 * B), ("Other", 30 * B)]


@pytest.mark.parametrize("key", ["other", "OTHER", " Other "])
def test_a_lone_other_variant_survives_reconciliation_as_the_exact_other(key):
    """The review's repro: 100B of segments vs 103B revenue reconciles "exact" (2.9%), so
    nothing downstream renames it — the ship name is whatever `_extract_segments` chose."""
    srcs = _extract_segments({"data": {"A": 70 * B, key: 30 * B}})
    out, elim, outcome = _reconcile_segments(srcs, 103 * B, ticker="T")
    assert outcome == "exact" and elim is None
    assert [s.name for s in out] == ["A", "Other"]


@pytest.mark.parametrize("data", [
    {"A": 70 * B, "other": 30 * B},
    {"A": 60 * B, "OTHER": 30 * B, "tiny": 2 * B, "tiny2": 2 * B, "B": 6 * B},
    {"A": 50 * B, "other": 25 * B, " Other ": 25 * B},
    {"A": 96 * B, "other": 4 * B},                 # below 5%: folded, appended as "Other"
    {"Other Products": 40 * B, "other": 30 * B, "Brother": 30 * B},
])
def test_every_other_like_row_is_spelled_exactly_other(data):
    """The iOS contract (StockRepository `toDisplayModel`): grey colour and remainder fold
    match `name == "Other"`. Any other spelling of the same bucket is a second row there."""
    out = _extract_segments({"data": data})
    other_like = [s for s in out if s.name.strip().lower() == "other"]
    assert len(other_like) == 1 and other_like[0].name == "Other", [s.name for s in out]
    assert sum(s.value for s in out) == pytest.approx(sum(data.values())), "renaming lost revenue"
    assert [s.value for s in out] == sorted((s.value for s in out), reverse=True)
    # Names that merely CONTAIN "other" are distinct segments, untouched.
    for name in ("Other Products", "Brother"):
        if name in data:
            assert any(s.name == name for s in out)


@pytest.mark.asyncio
async def test_the_served_card_carries_one_exact_other_end_to_end():
    seg = [{"fiscalYear": 2025, "date": "2025-09-27", "data": {"A": 280 * B, "other": 136 * B}}]

    class _FMP:
        async def get_revenue_product_segmentation(self, *a, **k):
            return seg

        async def get_income_statement(self, *a, **k):
            return [dict(r) for r in _INCOME]

        async def get_earning_calendar_full(self, ticker, raise_errors=False):
            return []

    resp, _next = await _svc(_FMP())._build_revenue_breakdown("AAPL")
    assert [s.name for s in resp.revenue_sources] == ["A", "Other"]


def test_a_v3_row_is_rebuilt():
    """v3 rows were written by the first pass and may carry a raw "other" spelling or a
    NULL next-earnings bound from a swallowed calendar failure."""
    from datetime import datetime, timezone

    assert _RB_PAYLOAD_VERSION >= 4
    body = {"symbol": "AAPL", "fiscal_year": "2025",
            "revenue_sources": [{"name": "A", "value": 70.0}, {"name": "other", "value": 30.0}],
            "cost_of_sales": 1.0, "operating_expense": 1.0, "tax": 0.0, "reported_revenue": 103.0}

    class _SBRow(_SB):
        def __init__(self, version):
            super().__init__()
            self._row = {"response_json": {**body, _VERSION_KEY: version},
                         "cached_at": datetime.now(timezone.utc).isoformat(),
                         "next_earnings_date": None}

        def table(self, name):
            row = self._row

            class _T:
                def select(self, *a, **k): return self
                def eq(self, *a, **k): return self
                def limit(self, *a, **k): return self
                def execute(self): return _Result([row])
            return _T()

    svc = RevenueBreakdownService.__new__(RevenueBreakdownService)
    svc.fmp = None
    svc.supabase = _SBRow(3)
    assert svc._check_supabase_cache("AAPL") is None
    svc.supabase = _SBRow(_RB_PAYLOAD_VERSION)
    assert svc._check_supabase_cache("AAPL") is not None
