"""Revenue Breakdown ("How X Makes Money") — backend deep-check fixes (2026-09-30).

Findings #10, #82, #83 of the Financials-tab adversarial review:

* #10 — a transient FMP failure on the INCOME STATEMENT was logged, replaced by `[]`, and
  built an all-zero card ("Total Revenue 0 / Net Profit 0", fiscal year "") that was then
  written to `revenue_breakdown_cache` for 24 h for every user. It now raises the typed
  FMP exception (→ FMP_RATE_LIMITED / FMP_UNAVAILABLE). A failed SEGMENT feed or earnings
  calendar still serves an honest card, but marked `degraded` and never persisted; a
  genuine empty statement (or one with no usable revenue) is the "no data" placeholder,
  never persisted either. Both cache tiers refuse such rows on write AND on read.
* #82 — a literal "Other" segment (≥ 5%) plus the folded small segments shipped as TWO
  rows named "Other".
* #83 — a REPORTED zero / negative revenue was logged as "no revenue data at all". It is
  genuine data: the 0.0 bar placeholder stays (a negative source would invert the iOS
  stack) and the signed figure travels in `reported_revenue`; it stays cacheable.

Every assertion is the CORRECT DEGRADED behaviour — an exception, a `degraded` reason, no
cache write — never a plausible-looking wrong number. Hermetic: FMP and Supabase are faked.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import pytest

import app.services.revenue_breakdown_service as rb
from app.api.error_response import ErrorCode, classify_exception
from app.integrations.fmp import FMPException, FMPRateLimitException, FMPUnavailableException
from app.schemas.revenue_breakdown import RevenueBreakdownResponse, RevenueSourceSchema
from app.services.revenue_breakdown_service import (
    RevenueBreakdownService,
    _DEGRADED_CACHE_TTL,
    _RB_PAYLOAD_VERSION,
    _VERSION_KEY,
    _extract_segments,
    _is_cacheable,
    _is_placeholder_only,
    _reconcile_segments,
)

B = 1e9
_AAPL_INCOME = [{"fiscalYear": 2025, "date": "2025-09-27", "revenue": 416.0 * B,
                 "costOfRevenue": 220.0 * B, "operatingExpenses": 62.0 * B,
                 "incomeTaxExpense": 20.0 * B, "netIncome": 112.0 * B}]
_AAPL_SEG = [{"fiscalYear": 2025, "date": "2025-09-27",
              "data": {"iPhone": 209.6 * B, "Services": 109.2 * B, "Mac": 33.7 * B,
                       "iPad": 28.4 * B, "Wearables": 35.1 * B}}]


_UNSET: Any = object()


class _FMP:
    """Each feed is either a value to return (None included) or an exception to raise."""

    def __init__(self, seg: Any = _UNSET, inc: Any = _UNSET, ec: Any = _UNSET):
        self._seg = [] if seg is _UNSET else seg
        self._inc = [] if inc is _UNSET else inc
        self._ec = [] if ec is _UNSET else ec
        self.ec_raise_flags: List[bool] = []

    @staticmethod
    async def _answer(v):
        if isinstance(v, BaseException):
            raise v
        return v

    async def get_revenue_product_segmentation(self, *a, **k):
        return await self._answer(self._seg)

    async def get_income_statement(self, *a, **k):
        return await self._answer(self._inc)

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        # Mirror the REAL client (fmp.py): by default a failure or a non-list body is
        # swallowed into [] ("no announcements"); only raise_errors=True surfaces it. A
        # fake that always raised made the calendar-degraded tests pass while production,
        # calling without the flag, could never reach that branch (round-2 R17).
        self.ec_raise_flags.append(raise_errors)
        if isinstance(self._ec, BaseException):
            if raise_errors:
                raise self._ec
            return []
        if not isinstance(self._ec, list):
            if raise_errors:
                raise FMPException(f"earnings (per-symbol): non-list body ({type(self._ec).__name__})")
            return []
        return self._ec


class _Result:
    def __init__(self, data):
        self.data = data


class _SB:
    """Supabase fake: serves `row` (or nothing) and records every upsert."""

    def __init__(self, row: Optional[Dict[str, Any]] = None):
        self._row = row
        self.upserts: List[Dict[str, Any]] = []

    def table(self, name):
        sb = self

        class _T:
            def select(self, *a, **k): return self
            def eq(self, *a, **k): return self
            def limit(self, *a, **k): return self
            def execute(self): return _Result([sb._row] if sb._row else [])

            def upsert(self, row, **k):
                sb.upserts.append(row)

                class _E:
                    def execute(self_inner): return None
                return _E()
        return _T()


def _svc(fmp: _FMP, row: Optional[Dict[str, Any]] = None) -> RevenueBreakdownService:
    svc = RevenueBreakdownService.__new__(RevenueBreakdownService)
    svc.fmp = fmp
    svc.supabase = _SB(row)
    return svc


@pytest.fixture(autouse=True)
def _clean_module_state():
    rb._cache.clear()
    rb._inflight.clear()
    yield
    rb._cache.clear()
    rb._inflight.clear()


async def _fetch(svc: RevenueBreakdownService, ticker: str = "AAPL"):
    out = await svc.get_revenue_breakdown(ticker)
    # Let the fire-and-forget executor upsert land before the test inspects it.
    await asyncio.sleep(0.05)
    return out


# ── #10: the income statement is the spine — a failure raises, nothing is cached ────

@pytest.mark.asyncio
@pytest.mark.parametrize("exc,code", [
    (FMPRateLimitException("HTTP 429", retry_after="60"), ErrorCode.FMP_RATE_LIMITED),
    (FMPUnavailableException("HTTP 503 after retries"), ErrorCode.FMP_UNAVAILABLE),
])
async def test_an_income_statement_failure_raises_typed_and_caches_nothing(exc, code):
    svc = _svc(_FMP(seg=_AAPL_SEG, inc=exc))
    with pytest.raises(type(exc)) as ei:
        await _fetch(svc)
    assert ei.value is exc, "the original typed exception must reach the endpoint"
    assert classify_exception(ei.value)[0] == code, "the error contract maps it, not a bare 502"
    assert svc.supabase.upserts == [], "an all-zero card was written to the 24 h tier"
    assert rb._cache == {}, "an all-zero card was kept in memory"
    assert rb._inflight == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"Error Message": "Limit Reach"}, "upstream said no", None])
async def test_an_income_error_payload_with_a_200_is_an_upstream_failure(payload):
    svc = _svc(_FMP(seg=_AAPL_SEG, inc=payload))
    with pytest.raises(FMPUnavailableException) as ei:
        await _fetch(svc)
    assert classify_exception(ei.value)[0] == ErrorCode.FMP_UNAVAILABLE
    assert svc.supabase.upserts == [] and rb._cache == {}


@pytest.mark.asyncio
async def test_a_non_fmp_income_exception_is_re_raised_not_swallowed():
    """A bug or an httpx error on the spine must surface as itself (classify_exception
    handles httpx), not be rebuilt into a confident zero card."""
    boom = RuntimeError("programming error in the client")
    svc = _svc(_FMP(seg=_AAPL_SEG, inc=boom))
    with pytest.raises(RuntimeError):
        await _fetch(svc)
    assert svc.supabase.upserts == [] and rb._cache == {}


@pytest.mark.asyncio
async def test_concurrent_joiners_of_a_failed_income_build_all_fail_and_nothing_lingers():
    svc = _svc(_FMP(seg=_AAPL_SEG, inc=FMPUnavailableException("503")))
    results = await asyncio.gather(*(svc.get_revenue_breakdown("AAPL") for _ in range(5)),
                                   return_exceptions=True)
    assert all(isinstance(r, FMPUnavailableException) for r in results), results
    assert rb._inflight == {} and rb._cache == {}


@pytest.mark.asyncio
async def test_a_genuinely_empty_income_statement_is_a_degraded_placeholder_never_persisted():
    """An ETF / unknown symbol: not an outage to raise, but a card built on nothing."""
    svc = _svc(_FMP(seg=[], inc=[]))
    r = await _fetch(svc)
    assert r.degraded == ["income_statement_empty"]
    assert _is_placeholder_only(r) and not _is_cacheable(r)
    assert r.net_income is None and r.reported_revenue is None, "no fabricated zero composition"
    assert svc.supabase.upserts == []


@pytest.mark.asyncio
async def test_income_rows_that_are_all_junk_count_as_empty():
    svc = _svc(_FMP(seg=[], inc=["x", 3, None]))
    r = await _fetch(svc)
    assert "income_statement_empty" in r.degraded
    assert svc.supabase.upserts == []


@pytest.mark.asyncio
async def test_a_statement_without_usable_revenue_is_marked_and_not_persisted():
    inc = [{"fiscalYear": 2025, "date": "2025-12-31", "revenue": None, "costOfRevenue": 5 * B,
            "operatingExpenses": 1 * B, "incomeTaxExpense": 0.0, "netIncome": -6 * B}]
    svc = _svc(_FMP(seg=[], inc=inc))
    r = await _fetch(svc)
    assert r.degraded == ["revenue_unreported"]
    assert r.reported_revenue is None and r.other_expense is None
    assert [(s.name, s.value) for s in r.revenue_sources] == [("Total Revenue", 0.0)]
    assert svc.supabase.upserts == []


# ── #10: segment feed / calendar failures serve an honest card, never persisted ─────

@pytest.mark.asyncio
@pytest.mark.parametrize("seg", [FMPUnavailableException("seg 503"), {"Error Message": "x"}])
async def test_a_segmentation_failure_serves_the_income_card_degraded(seg):
    svc = _svc(_FMP(seg=seg, inc=_AAPL_INCOME))
    r = await _fetch(svc)
    assert [(s.name, s.value) for s in r.revenue_sources] == [("Total Revenue", 416.0 * B)]
    assert r.net_income == 112.0 * B and r.fiscal_year == "2025", "the income half is real data"
    assert r.degraded == ["segmentation_unavailable"]
    assert svc.supabase.upserts == [], "a one-bar card must not be frozen for 24 h"


@pytest.mark.asyncio
@pytest.mark.parametrize("ec", [FMPUnavailableException("ec 503"), {"Error Message": "Limit Reach"}])
async def test_a_calendar_failure_is_marked_and_not_persisted(ec):
    fmp = _FMP(seg=_AAPL_SEG, inc=_AAPL_INCOME, ec=ec)
    svc = _svc(fmp)
    r = await _fetch(svc)
    assert fmp.ec_raise_flags == [True], "the calendar must be fetched with raise_errors=True"
    assert r.degraded == ["earnings_calendar_unavailable"]
    assert {s.name for s in r.revenue_sources} == {"iPhone", "Services", "Mac", "iPad", "Wearables"}
    assert svc.supabase.upserts == []


@pytest.mark.asyncio
async def test_every_side_feed_failing_lists_every_reason():
    svc = _svc(_FMP(seg=FMPUnavailableException("a"), inc=_AAPL_INCOME,
                    ec=FMPRateLimitException("b")))
    r = await _fetch(svc)
    assert r.degraded == ["segmentation_unavailable", "earnings_calendar_unavailable"]


@pytest.mark.asyncio
async def test_a_degraded_build_lives_in_memory_only_for_the_short_ttl(monkeypatch):
    svc = _svc(_FMP(seg=FMPUnavailableException("a"), inc=_AAPL_INCOME))
    r = await _fetch(svc)
    key = "rev_breakdown:AAPL"
    assert rb._cache_get(key) is r, "a burst of views shares the degraded build"
    now = time.time()
    monkeypatch.setattr(rb.time, "time", lambda: now + _DEGRADED_CACHE_TTL + 1)
    assert rb._cache_get(key) is None, "the next visit after the short TTL retries the feed"


@pytest.mark.asyncio
async def test_a_complete_build_is_persisted_with_the_bumped_version_and_the_normal_ttl(monkeypatch):
    svc = _svc(_FMP(seg=_AAPL_SEG, inc=_AAPL_INCOME))
    r = await _fetch(svc)
    assert r.degraded == [] and _is_cacheable(r)
    assert len(svc.supabase.upserts) == 1
    row = svc.supabase.upserts[0]["response_json"]
    assert row[_VERSION_KEY] == _RB_PAYLOAD_VERSION and row["degraded"] == []
    now = time.time()
    monkeypatch.setattr(rb.time, "time", lambda: now + _DEGRADED_CACHE_TTL + 1)
    assert rb._cache_get("rev_breakdown:AAPL") is r, "a complete build keeps the full 5-min tier"


def test_cache_set_ttl_is_a_backdated_stamp_and_never_extends_the_normal_tier(monkeypatch):
    now = 1_000_000.0
    monkeypatch.setattr(rb.time, "time", lambda: now)
    rb._cache_set("k", "v", ttl=60)
    rb._cache_set("long", "v", ttl=10_000)        # cannot outlive _CACHE_TTL
    rb._cache_set("neg", "v", ttl=-5)             # nonsense → expires at once, never negative-backdated past it
    monkeypatch.setattr(rb.time, "time", lambda: now + 59)
    assert rb._cache_get("k") == "v"
    assert rb._cache_get("neg") is None
    monkeypatch.setattr(rb.time, "time", lambda: now + 61)
    assert rb._cache_get("k") is None
    monkeypatch.setattr(rb.time, "time", lambda: now + rb._CACHE_TTL + 1)
    assert rb._cache_get("long") is None


# ── #10: both cache tiers refuse partial / no-data rows, whoever wrote them ─────────

def _row(body: Dict[str, Any], version: int = _RB_PAYLOAD_VERSION) -> Dict[str, Any]:
    from datetime import datetime, timezone
    return {"response_json": {**body, _VERSION_KEY: version},
            "cached_at": datetime.now(timezone.utc).isoformat(), "next_earnings_date": None}


_GOOD = {"symbol": "AAPL", "fiscal_year": "2025", "revenue_sources": [{"name": "iPhone", "value": 1.0}],
         "cost_of_sales": 1.0, "operating_expense": 1.0, "tax": 0.0, "reported_revenue": 1.0}
_ZERO_CARD = {"symbol": "AAPL", "fiscal_year": "", "revenue_sources": [{"name": "Total Revenue", "value": 0.0}],
              "cost_of_sales": 0.0, "operating_expense": 0.0, "tax": 0.0}


@pytest.mark.parametrize("body,served", [
    (_GOOD, True),
    ({**_GOOD, "degraded": ["segmentation_unavailable"]}, False),
    (_ZERO_CARD, False),                                   # the frozen-AAPL row itself
    ({**_ZERO_CARD, "reported_revenue": 0.0}, True),       # a REPORTED zero is data
    ({**_ZERO_CARD, "reported_revenue": -1.2 * B}, True),  # so is a reported negative
])
def test_the_read_tier_refuses_partial_and_no_data_rows(body, served):
    svc = _svc(_FMP(), row=_row(body))
    assert (svc._check_supabase_cache("AAPL") is not None) is served


def test_a_pre_bump_row_is_rebuilt():
    """v2 rows may BE the all-zero card or carry two "Other" rows — they must not serve."""
    assert _RB_PAYLOAD_VERSION >= 3
    svc = _svc(_FMP(), row=_row(_GOOD, version=2))
    assert svc._check_supabase_cache("AAPL") is None


@pytest.mark.parametrize("resp", [
    RevenueBreakdownResponse(**_ZERO_CARD),
    RevenueBreakdownResponse(**{**_GOOD, "degraded": ["earnings_calendar_unavailable"]}),
])
def test_the_write_tier_refuses_partial_and_no_data_builds(resp):
    svc = _svc(_FMP())
    svc._upsert_supabase_cache_safe("AAPL", resp, None)
    assert svc.supabase.upserts == []


def test_degraded_is_additive_and_defaults_empty_for_an_old_row():
    r = RevenueBreakdownResponse(**{k: v for k, v in _GOOD.items()})
    assert r.degraded == [] and r.model_dump()["degraded"] == []
    # default_factory: two instances never share one mutable list
    a, b = RevenueBreakdownResponse(**_GOOD), RevenueBreakdownResponse(**_GOOD)
    a.degraded.append("x")
    assert b.degraded == []


# ── #83: a REPORTED zero / negative revenue is data, not "no data" ──────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("revenue", [0.0, -1.2 * B])
async def test_reported_zero_or_negative_revenue_keeps_its_sign_and_stays_cacheable(revenue, caplog):
    caplog.set_level(logging.INFO, logger=rb.__name__)
    inc = [{"fiscalYear": 2025, "date": "2025-12-31", "revenue": revenue, "costOfRevenue": 0.5 * B,
            "operatingExpenses": 0.3 * B, "incomeTaxExpense": 0.0, "netIncome": -2.0 * B}]
    svc = _svc(_FMP(seg=[], inc=inc))
    r = await _fetch(svc)
    assert r.reported_revenue == revenue, "the signed reported figure must travel"
    # The bar placeholder stays 0.0 — never a NEGATIVE source (it would invert the iOS
    # stack) and never a fabricated POSITIVE revenue.
    assert [(s.name, s.value) for s in r.revenue_sources] == [("Total Revenue", 0.0)]
    assert all(s.value >= 0 for s in r.revenue_sources)
    assert r.degraded == [] and _is_cacheable(r)
    assert len(svc.supabase.upserts) == 1
    # The waterfall still closes on the reported figures.
    assert r.other_expense == pytest.approx(revenue - 0.5 * B - 0.3 * B - 0.0 + 2.0 * B)
    msgs = [rec.getMessage() for rec in caplog.records]
    assert not any("no usable revenue" in m for m in msgs), "a reported figure is not 'no data'"
    assert any("<= 0" in m for m in msgs)


# ── #82: exactly one "Other" row ─────────────────────────────────────────────────────

def test_a_literal_other_and_folded_small_segments_merge_into_one_row():
    out = _extract_segments({"data": {"Products": 60 * B, "Other": 30 * B, "Services": 6 * B,
                                      "Licensing": 4 * B}})
    names = [s.name for s in out]
    assert names.count("Other") == 1, names
    other = next(s for s in out if s.name == "Other")
    assert other.value == pytest.approx(34 * B)
    assert [s.value for s in out] == sorted((s.value for s in out), reverse=True)
    assert sum(s.value for s in out) == pytest.approx(100 * B), "merging must not lose revenue"


@pytest.mark.parametrize("data,other_value", [
    # lowercase literal key
    ({"Products": 60 * B, "other": 30 * B, "Services": 6 * B, "Licensing": 4 * B}, 34 * B),
    # padded literal key
    ({"Products": 60 * B, " Other ": 30 * B, "Licensing": 4 * B, "Services": 6 * B}, 34 * B),
    # two literal spellings, both kept at ≥ 5%, nothing folded
    ({"Products": 50 * B, "Other": 30 * B, "OTHER": 20 * B}, 50 * B),
    # literal Other below 5% is simply folded — one row
    ({"A": 60 * B, "B": 36 * B, "Other": 2 * B, "tiny": 2 * B}, 4 * B),
    # no literal Other — the fold appends one row, as before
    ({"A": 90 * B, "tiny1": 1 * B, "tiny2": 1 * B}, 2 * B),
    # a literal Other alone (nothing to fold) is left exactly as reported
    ({"A": 70 * B, "Other": 30 * B}, 30 * B),
])
def test_other_names_are_unique_across_feeds(data, other_value):
    out = _extract_segments({"data": data})
    lowered = [s.name.strip().lower() for s in out]
    assert len(lowered) == len(set(lowered)), [s.name for s in out]
    others = [s for s in out if s.name.strip().lower() == "other"]
    assert len(others) == 1 and others[0].value == pytest.approx(other_value)
    assert sum(s.value for s in out) == pytest.approx(sum(data.values()))


def test_names_that_merely_contain_other_are_not_merged():
    out = _extract_segments({"data": {"Other Products": 40 * B, "Brother Inc": 30 * B,
                                      "Other": 20 * B, "tiny": 1 * B}})
    names = {s.name for s in out}
    assert {"Other Products", "Brother Inc"} <= names
    assert [s.value for s in out if s.name == "Other"] == [pytest.approx(21 * B)]


@pytest.mark.asyncio
async def test_the_merged_other_survives_reconciliation_end_to_end():
    inc = [{"fiscalYear": 2025, "date": "2025-12-31", "revenue": 100 * B, "costOfRevenue": 50 * B,
            "operatingExpenses": 20 * B, "incomeTaxExpense": 5 * B, "netIncome": 25 * B}]
    seg = [{"fiscalYear": 2025, "date": "2025-12-31",
            "data": {"Products": 60 * B, "Other": 30 * B, "Services": 6 * B, "Licensing": 4 * B}}]
    svc = _svc(_FMP(seg=seg, inc=inc))
    r = await _fetch(svc)
    assert [s.name for s in r.revenue_sources].count("Other") == 1
    assert r.degraded == [] and len(svc.supabase.upserts) == 1
    # still an exact stack — merging did not change what reconciles
    _, elim, outcome = _reconcile_segments(r.revenue_sources, 100 * B)
    assert outcome == "exact" and elim is None


# ── helpers: the placeholder predicate is exact ──────────────────────────────────────

@pytest.mark.parametrize("sources,reported,expected", [
    ([("Total Revenue", 0.0)], None, True),
    ([("Total Revenue", 0.0)], 0.0, False),          # reported zero → data
    ([("Total Revenue", 5.0)], None, False),         # a real (unreconciled) figure
    ([("iPhone", 0.0)], None, False),                # not the placeholder row
    ([("Total Revenue", 0.0), ("A", 1.0)], None, False),
])
def test_is_placeholder_only(sources, reported, expected):
    r = RevenueBreakdownResponse(symbol="X", fiscal_year="", cost_of_sales=0.0, operating_expense=0.0,
                                 tax=0.0, reported_revenue=reported,
                                 revenue_sources=[RevenueSourceSchema(name=n, value=v) for n, v in sources])
    assert _is_placeholder_only(r) is expected
