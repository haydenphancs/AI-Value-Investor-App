"""Signal of Confidence, round 3 (2026-09-30 pre-ship review) — the cash-flow reasons.

P9  — the round-2 R47 trim (a newest quarter with no cash-flow row is cut from the series)
      had no bound: a cash-flow feed that stopped two or more quarters before the income
      statement rewound the chart AND its T12M by years (2023 cash over today's market cap)
      while reading only as the ignorable `cash_flow_row`, which the report froze. The trim
      stays; a tail longer than `_MAX_CF_LAG_QUARTERS`, or a kept series ending more than
      `_CF_STALE_MAX_DAYS` before the newest income quarter, is now the BLOCKING
      `cash_flow`.
P10 — a cash-flow leg that ANSWERED (a list, no raise) but matched no displayed quarter
      is `cash_flow_statement_missing` (the report collector drops the section without
      making the report uncacheable — the gap may be permanent); `cash_flow` stays for a
      leg that raised or answered a non-list.

Hermetic (testing.md): every FMP leg is an in-test stand-in. Every outlier asserts the
correct DEGRADED behaviour (a blocking reason, a trimmed series, a non-persisted build).
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.integrations.fmp import FMPRateLimitException
from app.services import signal_of_confidence_service as sos
from app.services.signal_of_confidence_service import SignalOfConfidenceService as S

# ── fixtures ────────────────────────────────────────────────────────────────

#: Twenty calendar quarter ends, oldest first: Q1'21 (2021-03-31) … Q4'25 (2025-12-31).
_QEND = [
    f"{y}-{md}"
    for y in range(2021, 2026)
    for md in ("03-31", "06-30", "09-30", "12-31")
]


def _q(d: str) -> str:
    return f"Q{(int(d[5:7]) - 1) // 3 + 1}"


def _label(d: str) -> str:
    return f"{_q(d)} '{d[2:4]}"


def _inc(d: str, shares: float = 4.3e9) -> dict:
    return {"date": d, "period": _q(d), "fiscalYear": d[:4], "weightedAverageShsOut": shares}


def _bb(d: str) -> dict:
    """A repurchaser's cash-flow row: $1B bought back, no dividend."""
    return {"date": d, "commonStockRepurchased": -1e9}


def _svc() -> S:
    return S.__new__(S)


class _FMP:
    """Hermetic FMP stand-in. ``cashflow`` may be a value or an exception to raise."""

    def __init__(self, *, cashflow, income, cap=100e9):
        self._cf, self._inc, self._cap = cashflow, income, cap

    async def get_cash_flow_statement(self, ticker, period="quarter", limit=20):
        if isinstance(self._cf, Exception):
            raise self._cf
        return self._cf

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return self._inc

    async def get_financial_ratios(self, ticker, period="annual", limit=10):
        return [{"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": 0}]

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        return []

    async def get_historical_market_cap(self, ticker, from_date=None, to_date=None, limit=2000):
        return [{"date": r["date"], "marketCap": self._cap} for r in self._inc]

    async def get_company_profile(self, ticker):
        return [{"lastDividend": 0}]

    async def get_stock_price_quote(self, ticker):
        return {"marketCap": self._cap, "price": 230.0}


class _NoSpinOff:
    async def get_ex_dividend_dates(self, symbol, from_date=None, to_date=None):
        return []

    async def unclassified_adjustment_or_none(self, symbol, from_date=None, to_date=None, *,
                                              effective_from=None, effective_to=None):
        return False


def _wire(fmp) -> S:
    from tests._price_fakes import PriceFromFMPFake
    svc = _svc()
    svc.fmp = fmp
    svc.price = PriceFromFMPFake(fmp)
    svc.corporate_actions = _NoSpinOff()
    return svc


async def _served(svc, ticker, monkeypatch):
    """Drive `get_signal_of_confidence` and report what reached the 24h tier."""
    writes = []
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda t: None)
    monkeypatch.setattr(svc, "_upsert_supabase_cache_safe", lambda *a, **k: writes.append(a))
    sos._cache.clear()
    sos._inflight.clear()
    try:
        out = await svc.get_signal_of_confidence(ticker)
        for _ in range(20):
            if writes:
                break
            await asyncio.sleep(0.01)
    finally:
        sos._cache.clear()
        sos._inflight.clear()
    return out, writes


def _report_refuses(resp) -> tuple:
    """Run the report collector's frozen-section gate on one SoC build."""
    from app.services.agents.ticker_report_data_collector import _refuse_degraded_financials

    out = SimpleNamespace(
        ticker="T", degraded_sections=[], signal_of_confidence=resp,
        growth_chart=None, profit_power=None, earnings=None, revenue_breakdown=None,
    )
    _refuse_degraded_financials(out)
    return out.signal_of_confidence, list(out.degraded_sections)


# ── P9: the trim is bounded ─────────────────────────────────────────────────


def test_a_cash_flow_feed_two_years_behind_is_stale_not_a_lag(caplog):
    """Income through Q4'25, cash flow stops at Q4'23: eight quarters trimmed. FAILS on the
    pre-round-3 code (no `cash_flow_stale`; the tail read as a one-filing lag)."""
    cf = [_bb(d) for d in _QEND if d <= "2023-12-31"]
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        pts, diag = _svc()._build_quarters(
            cf, [_inc(d) for d in _QEND], 100e9, {d: 100e9 for d in _QEND}, "STALE")
    assert diag.cash_flow_stale is True
    # The trim itself stays: no unmeasured quarter is ever shipped as a measured $0.
    assert pts[-1].period == "Q4 '23"
    assert all(p.buyback_amount == 1000.0 for p in pts)
    assert "[soc-cashflow-stale] ticker=STALE step=cash_flow" in caplog.text


@pytest.mark.asyncio
async def test_a_stale_feed_blocks_the_section_instead_of_freezing_2023_cash(monkeypatch):
    """The builder marks it `cash_flow_statement_missing`, never only the ignorable
    `cash_flow_row`: the report collector refuses the SECTION but the report stays
    cacheable (a stale vendor feed may never catch up). FAILS on the pre-round-3 code
    (degraded == ["cash_flow_row"], section frozen)."""
    cf = [_bb(d) for d in _QEND if d <= "2023-12-31"]
    fmp = _FMP(cashflow=cf, income=[_inc(d) for d in _QEND])
    out, writes = await _served(_wire(fmp), "STALE", monkeypatch)
    # A stale feed may never catch up (a vendor gap), so it is the cacheable
    # `cash_flow_statement_missing` (owner decision 2026-10-01): the report drops the
    # section, the build is never persisted, and it is never the ignorable `cash_flow_row`.
    assert "cash_flow_statement_missing" in out.degraded
    assert "cash_flow_row" not in out.degraded and "cash_flow" not in out.degraded
    assert writes == [], "a stale build never reaches the 24h tier"
    kept, lost = _report_refuses(out)
    assert kept is None, "the report must not freeze a series rewound by two years"
    assert lost == [], "a vendor gap must not keep the report out of the shared caches"


@pytest.mark.asyncio
async def test_a_single_old_cash_flow_row_is_stale():
    """The feed holds only the 2024-03-31 row: the series collapsed to one x4 point and
    the T12M was that one quarter over today's cap. FAILS on the pre-round-3 code."""
    fmp = _FMP(cashflow=[_bb("2024-03-31")], income=[_inc(d) for d in _QEND])
    resp, _, degraded = await _wire(fmp)._build_signal_of_confidence("ONEROW")
    assert "cash_flow_statement_missing" in degraded
    assert "cash_flow_row" not in degraded and "cash_flow" not in degraded
    assert resp.degraded == degraded


def test_two_trimmed_quarters_cross_the_bound():
    """`_MAX_CF_LAG_QUARTERS` is one filing: two trimmed quarters are stale."""
    dates = _QEND[-12:]
    cf = [_bb(d) for d in dates[:-2]]
    _pts, diag = _svc()._build_quarters(
        cf, [_inc(d) for d in dates], 100e9, {d: 100e9 for d in dates}, "TWO")
    assert len(diag.missing_cash_flow_periods) == 2
    assert diag.cash_flow_stale is True


def test_one_trimmed_quarter_far_behind_an_income_gap_is_stale():
    """One trimmed quarter, but the income history skips two quarters, so the kept series
    ends 275 days before the newest income quarter (> `_CF_STALE_MAX_DAYS`)."""
    dates = [d for d in _QEND if d not in ("2025-06-30", "2025-09-30")]
    cf = [_bb(d) for d in dates[:-1]]
    _pts, diag = _svc()._build_quarters(
        cf, [_inc(d) for d in dates], 100e9, {d: 100e9 for d in dates}, "GAP")
    assert diag.missing_cash_flow_periods == ["Q4 '25"]
    assert diag.cash_flow_stale is True


@pytest.mark.asyncio
async def test_a_one_filing_lag_is_still_only_the_ignorable_row(monkeypatch):
    """Boundary guard: the ordinary one-quarter lag keeps the round-2 behaviour —
    trimmed, `cash_flow_row` only, the report keeps the section."""
    dates = _QEND[-12:]
    fmp = _FMP(cashflow=[_bb(d) for d in dates[:-1]], income=[_inc(d) for d in dates])
    out, writes = await _served(_wire(fmp), "LAGQ", monkeypatch)
    assert out.degraded == ["cash_flow_row"] and writes == []
    assert out.data_points[-1].period == _label(dates[-2])
    kept, lost = _report_refuses(out)
    assert kept is out and lost == []


def test_no_tail_is_never_stale():
    dates = _QEND[-12:]
    _pts, diag = _svc()._build_quarters(
        [_bb(d) for d in dates], [_inc(d) for d in dates], 100e9,
        {d: 100e9 for d in dates}, "FULL")
    assert diag.cash_flow_stale is False and diag.missing_cash_flow_periods == []


# ── P10: a 200 with nothing usable is its own reason ────────────────────────


@pytest.mark.asyncio
async def test_an_empty_cash_flow_statement_is_statement_missing_not_a_failed_leg(monkeypatch):
    """FAILS on the pre-round-3 code (it emitted the blocking `cash_flow`)."""
    dates = _QEND[-8:]
    fmp = _FMP(cashflow=[], income=[_inc(d) for d in dates])
    out, writes = await _served(_wire(fmp), "EMPTY", monkeypatch)
    assert out.degraded == ["cash_flow_statement_missing"]
    assert writes == [], "an all-unknown build is still never persisted for a day"


@pytest.mark.asyncio
async def test_off_cycle_cash_flow_rows_matching_no_quarter_are_statement_missing():
    """Rows exist but none lands within ±7 days of an income period end."""
    dates = _QEND[-8:]
    cf = [_bb(f"{d[:8]}15") for d in dates]   # the 15th of each quarter-end month
    fmp = _FMP(cashflow=cf, income=[_inc(d) for d in dates])
    resp, _, degraded = await _wire(fmp)._build_signal_of_confidence("OFFCYC")
    assert degraded == ["cash_flow_statement_missing"]
    assert "cash_flow_row" not in degraded, "statement_missing already says the cash is unknown"
    assert resp.degraded == degraded


@pytest.mark.asyncio
async def test_a_raised_cash_flow_leg_stays_the_blocking_cash_flow():
    """A raise is transient: `cash_flow`, never the cacheable statement_missing."""
    dates = _QEND[-8:]
    fmp = _FMP(cashflow=FMPRateLimitException("429"), income=[_inc(d) for d in dates])
    _resp, _, degraded = await _wire(fmp)._build_signal_of_confidence("RAISED")
    assert "cash_flow" in degraded
    assert "cash_flow_statement_missing" not in degraded


@pytest.mark.parametrize("payload", [{"Error Message": "Limit Reach"}, None, "oops"])
@pytest.mark.asyncio
async def test_a_non_list_cash_flow_answer_stays_the_blocking_cash_flow(payload):
    """An FMP error dict / null / string body is the leg failing, not "no statement on
    file": `cash_flow`, never statement_missing (so the report is not cached on it)."""
    dates = _QEND[-8:]
    fmp = _FMP(cashflow=payload, income=[_inc(d) for d in dates])
    _resp, _, degraded = await _wire(fmp)._build_signal_of_confidence("NONLIST")
    assert degraded.count("cash_flow") == 1
    assert "cash_flow_statement_missing" not in degraded


@pytest.mark.asyncio
async def test_no_income_quarters_names_neither_cash_flow_reason():
    """With no displayed quarter there is nothing for the cash flow to match: the empty
    build says `income` (the raised leg), not statement_missing."""
    fmp = _FMP(cashflow=[], income=[])
    _resp, _, degraded = await _wire(fmp)._build_signal_of_confidence("NOINC")
    assert "cash_flow_statement_missing" not in degraded and "cash_flow" not in degraded


def test_payload_version_bumped_for_round_3():
    """FAILS on the pre-round-3 code (8)."""
    assert sos._PAYLOAD_VERSION >= 9
