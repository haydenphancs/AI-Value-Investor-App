"""Signal of Confidence, round 2 (2026-09-30 adversarial review) — backend half of
R20 (a failed spin-off lookup read as a spin-off), R47 (a newest quarter with no cash-flow
row shipped as a measured $0) and R48 (x4-fallback points in the dividend verdict).

Hermetic (testing.md): the FMP stand-ins are in-test; R20 drives the REAL
`CorporateActionsService` with a stubbed price client, because the fake the first pass
used could only answer True / False / raise and never exercised the fail-closed None path
that the bug lived on. Every outlier asserts the correct DEGRADED behaviour (an unchanged
verdict plus a non-persisted build, a trimmed point, a refused ratio), never a number.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

import pytest

from app.integrations.fmp import FMPRateLimitException
from app.services import corporate_actions_service as cas
from app.services import signal_of_confidence_service as sos
from app.services.signal_of_confidence_service import SignalOfConfidenceService as S

# ── fixtures ────────────────────────────────────────────────────────────────

#: Fourteen calendar quarter ends, oldest first (2023-03-31 … 2026-06-30).
_QEND = [
    "2023-03-31", "2023-06-30", "2023-09-30", "2023-12-31",
    "2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31",
    "2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31",
    "2026-03-31", "2026-06-30",
]


def _q(d: str) -> str:
    return f"Q{(int(d[5:7]) - 1) // 3 + 1}"


def _label(d: str) -> str:
    return f"{_q(d)} '{d[2:4]}"


def _inc(d: str, shares: float = 4.3e9) -> dict:
    return {"date": d, "period": _q(d), "fiscalYear": d[:4], "weightedAverageShsOut": shares}


def _cf(d: str, **fields) -> dict:
    return {"date": d, **fields}


def _svc() -> S:
    return S.__new__(S)


class _FMP:
    """Hermetic FMP stand-in for the SoC builder (never a live call)."""

    def __init__(self, *, cashflow, income, cap=3.5e12, ratios=None, profile=None):
        self._cf, self._inc, self._cap = cashflow, income, cap
        self._ratios = ratios if ratios is not None else [
            {"date": "2024-12-31", "fiscalYear": "2024", "dividendPerShare": 0.98},
            {"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": 1.01}]
        self._profile = profile if profile is not None else [{"lastDividend": 1.01}]

    async def get_cash_flow_statement(self, ticker, period="quarter", limit=20):
        return self._cf

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return self._inc

    async def get_financial_ratios(self, ticker, period="annual", limit=10):
        return self._ratios

    async def get_earning_calendar_full(self, ticker):
        return []

    async def get_historical_market_cap(self, ticker, from_date=None, to_date=None, limit=2000):
        return [{"date": r["date"], "marketCap": self._cap} for r in self._inc]

    async def get_company_profile(self, ticker):
        return self._profile

    async def get_stock_price_quote(self, ticker):
        return {"marketCap": self._cap, "price": 230.0}


class _SpinFake:
    """Tri-state spin-off seam for the wiring tests that do not need the real service."""

    def __init__(self, answer=False):
        self.answer, self.calls = answer, []

    async def get_ex_dividend_dates(self, symbol, from_date=None, to_date=None):
        return []

    async def unclassified_adjustment_or_none(self, symbol, from_date=None, to_date=None, *,
                                              effective_from=None, effective_to=None):
        self.calls.append(effective_from)
        return self.answer


def _wire(fmp, ca) -> S:
    from tests._price_fakes import PriceFromFMPFake
    svc = _svc()
    svc.fmp = fmp
    svc.price = PriceFromFMPFake(fmp)
    svc.corporate_actions = ca
    return svc


async def _served(svc, ticker, monkeypatch):
    """Drive `get_signal_of_confidence` and report what reached the 24h tier."""
    writes = []
    monkeypatch.setattr(svc, "_check_supabase_cache", lambda t: None)
    monkeypatch.setattr(svc, "_upsert_supabase_cache_safe", lambda *a, **k: writes.append(a))
    sos._cache.clear()
    sos._inflight.clear()
    out = await svc.get_signal_of_confidence(ticker)
    for _ in range(50):
        if writes:
            break
        await asyncio.sleep(0.01)
    return out, writes


# ── R20: a failed spin-off lookup is UNKNOWN, not a spin-off ─────────────────


class _PriceClient:
    """The price-series client `CorporateActionsService._derive` reads (`get_fmp_client`).

    ``mode``: "flat" (both legs identical: no adjustment), "fail_full" (the adjusted leg
    429s), "short_raw" (the raw leg comes back with one row), or a list of
    ``(event_date, factor)`` steps applied to the raw leg before ``event_date``.
    """

    def __init__(self, mode="flat"):
        self.mode = mode
        self.calls = 0

    @staticmethod
    def _days(from_date, to_date):
        start = date.fromisoformat(from_date)
        end = date.fromisoformat(to_date)
        out, d = [], start
        while d <= end:
            out.append(d.isoformat())
            d += timedelta(days=7)
        return out

    def _rows(self, from_date, to_date, raw=False):
        rows = []
        for d in self._days(from_date, to_date):
            price = 100.0
            if raw and isinstance(self.mode, list):
                for event_date, factor in self.mode:
                    if d < event_date:
                        price *= factor
            rows.append({"date": d, "close": price, "adjClose": price})
        return rows

    async def get_historical_prices(self, sym, from_date=None, to_date=None):
        self.calls += 1
        if self.mode == "fail_full":
            raise FMPRateLimitException("429 Too Many Requests", retry_after="1")
        return self._rows(from_date, to_date)

    async def get_historical_prices_non_split_adjusted(self, sym, from_date=None, to_date=None):
        if self.mode == "short_raw":
            return self._rows(from_date, to_date, raw=True)[:1]
        return self._rows(from_date, to_date, raw=True)

    async def get_historical_prices_dividend_adjusted(self, sym, from_date=None, to_date=None):
        return self._rows(from_date, to_date, raw=True)


@pytest.fixture
def real_corporate_actions(monkeypatch):
    """The REAL CorporateActionsService over a stubbed price client, caches cleared."""
    holder = {"client": _PriceClient(), "db_writes": []}
    monkeypatch.setattr(cas, "get_fmp_client", lambda: holder["client"])

    # A CLOSED window reads/writes the Supabase tier; keep it in memory (hermetic), and
    # record writes so a test can assert a failed derivation is never persisted.
    async def _db_get(self, sym, kind, from_date, to_date):
        return None

    async def _db_put(self, sym, kind, from_date, to_date, events):
        holder["db_writes"].append((sym, kind, from_date, to_date, list(events)))

    monkeypatch.setattr(cas.CorporateActionsService, "_db_get", _db_get)
    monkeypatch.setattr(cas.CorporateActionsService, "_db_put", _db_put)
    cas._cache.clear()
    cas._inflight.clear()
    yield holder
    cas._cache.clear()
    cas._inflight.clear()


def _aapl_steady():
    """AAPL-shaped steady 0.4% payer: $3.5B a quarter on a $3.5T cap, every quarter."""
    dates = _QEND[-12:]
    cfs = [_cf(d, commonDividendsPaid=-3.5e9, commonStockRepurchased=-2.5e10) for d in dates]
    return _FMP(cashflow=cfs, income=[_inc(d) for d in dates])


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fail_full", "short_raw"])
async def test_a_failed_price_leg_keeps_the_healthy_verdict_and_is_not_persisted(
        mode, real_corporate_actions, monkeypatch, caplog):
    """R20, CONFIRMED by the reviewer's simulation: healthy → "Fair"; a 429 (or a short
    leg) on either price series made `has_unclassified_adjustment` fail CLOSED, the SoC
    read that True as a spin-off, ran the absolute ladder on 0.4% → red "Low", and
    persisted it for 24h with `degraded == []`."""
    real_corporate_actions["client"] = _PriceClient("flat")
    healthy, healthy_writes = await _served(
        _wire(_aapl_steady(), cas.CorporateActionsService()), "AAPL", monkeypatch)
    assert healthy.dividend_info is not None
    assert healthy.dividend_info.status == "Fair", "control: the relative verdict on a flat payer"
    assert healthy.degraded == [] and healthy_writes, "control: a healthy build persists"

    cas._cache.clear()
    real_corporate_actions["client"] = _PriceClient(mode)
    with caplog.at_level(logging.WARNING):
        broken, broken_writes = await _served(
            _wire(_aapl_steady(), cas.CorporateActionsService()), "AAPL", monkeypatch)
    assert real_corporate_actions["client"].calls > 0, "the real derivation was not exercised"
    assert broken.dividend_info.status == healthy.dividend_info.status, \
        "an unknown spin-off check changed the verdict"
    assert broken.degraded == ["spinoff_check"]
    assert broken_writes == [], "a build whose spin-off check could not run reached the 24h tier"
    assert sos._cache["signal_of_confidence:AAPL"][2] == sos._DEGRADED_CACHE_TTL
    assert "[soc-spinoff-check-unavailable] ticker=AAPL" in caplog.text
    assert "[soc-spinoff-baseline]" not in caplog.text, "the logs blamed a spin-off that never happened"


@pytest.mark.asyncio
async def test_a_real_spin_off_still_refuses_the_relative_verdict(real_corporate_actions,
                                                                  monkeypatch, caplog):
    """Control for R20: a derivable, UNNAMEABLE factor inside the window (GE HealthCare's
    1.280866) is still a spin-off — the absolute ladder runs and nothing is degraded."""
    spin_day = (date.today() - timedelta(days=200)).isoformat()
    real_corporate_actions["client"] = _PriceClient([(spin_day, 1.280866)])
    with caplog.at_level(logging.WARNING):
        resp, writes = await _served(
            _wire(_aapl_steady(), cas.CorporateActionsService()), "AAPL", monkeypatch)
    assert resp.degraded == [] and writes
    assert "[soc-spinoff-baseline] ticker=AAPL" in caplog.text
    assert resp.dividend_info.status == "Low"      # the absolute ladder on a 0.4% T12M


@pytest.mark.asyncio
async def test_the_tri_state_method_answers_none_where_the_13f_gate_fails_closed(
        real_corporate_actions):
    svc = cas.CorporateActionsService()
    lo, hi = "2026-01-01", "2026-06-30"
    fetch = cas.window_for_range(lo, hi)

    real_corporate_actions["client"] = _PriceClient("fail_full")
    assert await svc.unclassified_adjustment_or_none(
        "AAPL", *fetch, effective_from=lo, effective_to=hi) is None
    assert real_corporate_actions["db_writes"] == [], "a failed derivation was persisted"
    # The 13F backstop's contract is UNCHANGED: "could not look" still arms it.
    assert await svc.has_unclassified_adjustment(
        "AAPL", *fetch, effective_from=lo, effective_to=hi) is True

    for mode, inside_expect in (
        ("flat", False),
        ([("2026-03-02", 1.280866)], True),        # spin-off inside (lo, hi]
        ([("2026-03-02", 2.0)], False),            # a clean 2:1 split is NOT unclassified
    ):
        cas._cache.clear()
        real_corporate_actions["client"] = _PriceClient(mode)
        tri = await svc.unclassified_adjustment_or_none(
            "AAPL", *fetch, effective_from=lo, effective_to=hi)
        legacy = await svc.has_unclassified_adjustment(
            "AAPL", *fetch, effective_from=lo, effective_to=hi)
        assert tri is inside_expect and legacy is inside_expect, mode

    # The effective filter is the same half-open window: an event before `lo` is out.
    cas._cache.clear()
    real_corporate_actions["client"] = _PriceClient([("2025-12-29", 1.280866)])
    assert await svc.unclassified_adjustment_or_none(
        "AAPL", *fetch, effective_from=lo, effective_to=hi) is False


@pytest.mark.asyncio
async def test_a_raising_source_is_unknown_too(monkeypatch, caplog):
    class _Raises(_SpinFake):
        async def unclassified_adjustment_or_none(self, *a, **k):
            raise RuntimeError("corporate actions down")

    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        answer = await _wire(_aapl_steady(), _Raises())._spinoff_in_window("AAPL", "2024-09-30")
    assert answer is None
    assert "[soc-spinoff-check-unavailable] ticker=AAPL step=spinoff_check: RuntimeError" in caplog.text


@pytest.mark.asyncio
async def test_the_fail_closed_13f_method_is_never_the_soc_source():
    """The bug's exact seam: a source exposing ONLY the fail-closed method is not asked —
    the check is unknown (degraded), never a fail-closed True read as a spin-off."""
    class _LegacyOnly:
        def __init__(self):
            self.asked = False

        async def get_ex_dividend_dates(self, symbol, from_date=None, to_date=None):
            return []

        async def has_unclassified_adjustment(self, *a, **k):
            self.asked = True
            return True

    legacy = _LegacyOnly()
    resp, _, degraded = await _wire(_aapl_steady(), legacy)._build_signal_of_confidence("AAPL")
    assert legacy.asked is False
    assert degraded == ["spinoff_check"]
    assert resp.dividend_info.status == "Fair"


# ── R47: a newest quarter with no cash-flow row ──────────────────────────────


def _bb_history(dates, missing=()):
    return [_cf(d, commonStockRepurchased=-1e9) for d in dates if d not in missing]


def test_the_newest_edge_is_trimmed_and_the_window_still_holds_eight_quarters(caplog):
    dates = _QEND[-12:]
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        pts, diag = _svc()._build_quarters(
            _bb_history(dates, missing={dates[-1]}), [_inc(d) for d in dates], 100e9,
            {d: 100e9 for d in dates}, "LAG")
    assert len(pts) == 8, "the history has eight complete quarters — the chart keeps them"
    assert [p.period for p in pts] == [_label(d) for d in dates[-9:-1]]
    assert all(p.buyback_amount == 1000.0 for p in pts), "a $0 point is still on the wire"
    assert pts[-1].buyback_yield == pytest.approx(4.0)
    assert diag.missing_cash_flow_periods == [_label(dates[-1])] and diag.missing_cash_flow_recent
    assert "[soc-cashflow-row-missing] LAG" in caplog.text and "trimmed" in caplog.text


def test_two_lagging_quarters_are_both_trimmed_an_interior_gap_is_kept():
    dates = _QEND[-12:]
    pts, diag = _svc()._build_quarters(
        _bb_history(dates, missing={dates[-1], dates[-2], dates[-6]}),
        [_inc(d) for d in dates], 100e9, {d: 100e9 for d in dates}, "LAG2")
    assert pts[-1].period == _label(dates[-3])
    interior = _label(dates[-6])
    assert interior in [p.period for p in pts], "an interior gap keeps its point (share line)"
    assert [p for p in pts if p.period == interior][0].buyback_amount == 0.0
    assert set(diag.missing_cash_flow_periods) == {_label(d) for d in (dates[-1], dates[-2], dates[-6])}


def test_a_statement_with_no_matching_row_keeps_its_points_untrimmed():
    """Nothing to trim TO: the statement is missing, not lagging (the builder flags it)."""
    dates = _QEND[-8:]
    pts, diag = _svc()._build_quarters([], [_inc(d) for d in dates], 100e9, {}, "NONE")
    assert len(pts) == 8 and diag.cash_flow_rows_found is False


@pytest.mark.asyncio
async def test_the_builder_and_the_report_block_end_at_the_last_complete_quarter(monkeypatch):
    """R47's report path: the capital-allocation block's newest point is the last quarter
    with a cash-flow row — never the "$0" the header, mini-chart and prompt read."""
    from app.services.agents.ticker_report_data_collector import _build_capital_allocation_block

    dates = _QEND[-12:]
    fmp = _FMP(cashflow=_bb_history(dates, missing={dates[-1]}), income=[_inc(d) for d in dates],
               cap=100e9, ratios=[{"date": "2025-12-31", "dividendPerShare": 0}],
               profile=[{"lastDividend": 0}])
    out, writes = await _served(_wire(fmp, _SpinFake()), "LAG", monkeypatch)
    assert out.degraded == ["cash_flow_row"] and writes == [], "kept out of the 24h tier until the row lands"
    assert out.data_points[-1].period == _label(dates[-2])
    assert out.data_points[-1].buyback_amount > 0
    assert out.summary.buyback_yield == pytest.approx(4.0)

    block = _build_capital_allocation_block(out)
    assert block["data_points"][-1]["period"] == _label(dates[-2])
    assert block["data_points"][-1]["buyback_amount"] > 0


@pytest.mark.asyncio
async def test_a_statement_that_matches_no_quarter_is_a_failed_leg_not_a_lag():
    """`cash_flow_row` is IGNORABLE in the report collector (a lag); a statement that
    answered 200 with nothing usable is NOT a lag either. Round 3 (P10) gives it its own
    reason, `cash_flow_statement_missing` — the collector drops the section without making
    the report uncacheable, since the gap may be permanent — and keeps `cash_flow` for a
    leg that raised (tests/test_soc_round3_cashflow_reasons.py)."""
    dates = _QEND[-8:]
    fmp = _FMP(cashflow=[], income=[_inc(d) for d in dates], cap=100e9)
    resp, _, degraded = await _wire(fmp, _SpinFake())._build_signal_of_confidence("EMPTY")
    assert "cash_flow_statement_missing" in degraded
    assert "cash_flow" not in degraded and "cash_flow_row" not in degraded
    assert resp.degraded == degraded


# ── R48: x4-fallback points in the dividend verdict ──────────────────────────


class _P:
    def __init__(self, y, period):
        self.dividend_yield, self.period = y, period


def test_x4_fallback_points_are_left_out_of_the_baseline():
    """An annual payer's first two bars are x4 (no four consecutive quarters behind them):
    Q2 x4 = 12%, Q3 x4 = 0%. In the baseline they dragged a flat 3% payer to "Low"."""
    pts = [_P(12.0, "P0"), _P(0.0, "P1")] + [_P(3.0, f"P{i}") for i in range(2, 8)]
    svc = _svc()
    control = svc._build_dividend_info([], 3.0, 0.0, 0.0, data_points=pts,
                                       pays_common_dividend=True)
    assert control.status == "Low", "control: the x4 baseline is what goes wrong"
    info = svc._build_dividend_info([], 3.0, 0.0, 0.0, data_points=pts,
                                    pays_common_dividend=True,
                                    ttm_fallback_periods={"P0", "P1"})
    # two TTM baseline points < _MIN_BASELINE_POINTS → the absolute ladder on 3.0%
    assert info.status == "High"


def test_a_fallback_newest_point_refuses_the_relative_verdict(caplog):
    """A Q4-heavy quarter x4 (5.85% for a 2.95% payer) read "Very High"."""
    pts = [_P(2.95, f"P{i}") for i in range(7)] + [_P(5.85, "NEW")]
    svc = _svc()
    control = svc._build_dividend_info([], 2.95, 0.0, 0.0, data_points=pts,
                                       pays_common_dividend=True)
    assert control.status == "Very High", "control"
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        info = svc._build_dividend_info([], 2.95, 0.0, 0.0, data_points=pts,
                                        pays_common_dividend=True,
                                        ttm_fallback_periods={"NEW"}, ticker="KO")
    assert info.status == "High"                 # absolute ladder on the T12M 2.95%
    assert "[soc-ttm-fallback-verdict] ticker=KO" in caplog.text


def test_ttm_points_alone_keep_the_relative_verdict():
    """Control: no fallback anywhere → the relative path is unchanged."""
    pts = [_P(3.0, f"P{i}") for i in range(8)]
    info = _svc()._build_dividend_info([], 3.0, 0.0, 0.0, data_points=pts,
                                       pays_common_dividend=True, ttm_fallback_periods=set())
    assert info.status == "Fair"


def _ko_settlement(dates):
    per_share = {"Q1": 0.0207, "Q2": 0.51, "Q3": 0.51, "Q4": 1.0199}
    return [_cf(d, commonDividendsPaid=-per_share[_q(d)] * 4.3e9) for d in dates]


@pytest.mark.asyncio
async def test_wiring_a_quarter_missing_from_both_statements_makes_the_newest_x4(caplog):
    """KO, with Q3 '25 absent from BOTH statements (nothing flags it as missing): the
    newest point Q4 '25 has no consecutive window, so it is Q4 x4 = 5.85% against a TTM
    baseline of ~2.95% — "Very High" for a dividend that never changed. The ratio is now
    refused, the spin-off lookup is not spent on it, and the absolute ladder runs."""
    dates = [d for d in _QEND[:-2] if d != "2025-09-30"]          # … 2025-06-30, 2025-12-31
    fmp = _FMP(cashflow=_ko_settlement(dates), income=[_inc(d) for d in dates], cap=300e9)
    ca = _SpinFake()
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        resp, _, degraded = await _wire(fmp, ca)._build_signal_of_confidence("KO")
    assert resp.data_points[-1].period == "Q4 '25"
    assert resp.data_points[-1].dividend_yield > 5.0, "fixture: the newest point IS the lumpy x4"
    assert resp.dividend_info.status != "Very High"
    assert "[soc-ttm-fallback-verdict] ticker=KO" in caplog.text
    assert ca.calls == [], "the spin-off lookup was spent on a verdict that cannot run"
    assert degraded == []


@pytest.mark.asyncio
async def test_wiring_a_recent_listing_keeps_its_x4_bars_out_of_the_baseline():
    """An annual payer (June) with nine quarters of history: the first two displayed bars
    are x4 (0%, 0%), the baseline read 1.5% against a 3% newest TTM → "Very High"."""
    dates = _QEND[-9:]
    cfs = [_cf(d, commonDividendsPaid=(-3e9 if d[5:7] == "06" else 0)) for d in dates]
    fmp = _FMP(cashflow=cfs, income=[_inc(d) for d in dates], cap=100e9)
    ca = _SpinFake()
    resp, _, degraded = await _wire(fmp, ca)._build_signal_of_confidence("ANN")
    assert resp.dividend_info.status != "Very High"
    assert resp.dividend_info.status == "High"   # the absolute ladder on the 3% T12M
    assert ca.calls == []
    assert degraded == []


def test_the_no_cap_summary_fallback_does_not_read_one_x4_quarter():
    pts = [_P(2.95, f"P{i}") for i in range(7)] + [_P(5.85, "NEW")]
    for p in pts:
        p.buyback_yield, p.dividend_amount, p.buyback_amount, p.shares_outstanding = 0.0, 1.0, 0.0, 1.0
    s = _svc()._build_summary(pts, None, ttm_fallback_periods={"NEW"})
    assert s.dividend_yield == pytest.approx((2.95 * 3 + 5.85) / 4, abs=0.01)
    control = _svc()._build_summary(pts, None)
    assert control.dividend_yield == pytest.approx(5.85), "control: the newest point alone"


def test_payload_version_bumped_for_the_round_2_verdict():
    """A row stored under 7 carries a verdict computed with x4 points in its baseline."""
    assert sos._PAYLOAD_VERSION >= 8
