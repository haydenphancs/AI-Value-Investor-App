"""Signal of Confidence deep check (2026-09-30) — the backend half of findings
#53 #54 #56 #85 #86 #87 #88 #92 plus the `degraded` wire field.

Math tests over plain dicts (testing.md §1) and wiring tests that drive the real
`_build_signal_of_confidence` / `get_signal_of_confidence` with an in-test FMP fake (the
`test_signal_of_confidence_dividend_gate.py` pattern), because a mutation that stops
threading a diagnostic into the degraded gate stays green under leaf-only tests.

Every outlier asserts the CORRECT DEGRADED behaviour — a skipped quarter, a refused
verdict, a non-persisted build — never a plausible wrong number.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, timedelta

import pytest

from app.schemas.signal_of_confidence import (
    DividendInfoSchema,
    SignalOfConfidenceResponse,
    SignalOfConfidenceSummarySchema,
)
from app.services import signal_of_confidence_service as sos
from app.services.signal_of_confidence_service import SignalOfConfidenceService as S


def _svc() -> S:
    return S.__new__(S)


# ── fixtures ────────────────────────────────────────────────────────────────

#: Twelve calendar quarter ends, oldest first (2023-09-30 … 2026-06-30).
_QEND = [
    "2023-09-30", "2023-12-31", "2024-03-31", "2024-06-30",
    "2024-09-30", "2024-12-31", "2025-03-31", "2025-06-30",
    "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30",
]


def _q(date_str: str) -> str:
    return f"Q{(int(date_str[5:7]) - 1) // 3 + 1}"


def _inc(date_str: str, shares: float | None = 4.3e9, period: str | None = None,
         fy: str | None = None) -> dict:
    return {"date": date_str, "period": period or _q(date_str),
            "fiscalYear": fy or date_str[:4], "weightedAverageShsOut": shares}


def _cf(date_str: str, **fields) -> dict:
    return {"date": date_str, **fields}


def _caps(dates, cap: float) -> dict:
    return {d: cap for d in dates}


# ── #54: trailing-twelve-month yields ───────────────────────────────────────


def _ko_cash(dates):
    """KO-shaped settlement: 0.0207 / 0.51 / 0.51 / 1.0199 per share by calendar
    quarter on an UNCHANGED dividend (schemas/signal_of_confidence.py records it)."""
    per_share = {"Q1": 0.0207, "Q2": 0.51, "Q3": 0.51, "Q4": 1.0199}
    return [_cf(d, commonDividendsPaid=-per_share[_q(d)] * 4.3e9) for d in dates]


def test_ko_lumpy_settlement_charts_a_steady_trailing_yield():
    """x4 charted 0.12 / 2.92 / 2.92 / 5.85 on a 2.95% yield. TTM: every bar ~2.95."""
    pts = _svc()._build_data_points(
        _ko_cash(_QEND), [_inc(d) for d in _QEND], 300e9, _caps(_QEND, 300e9), "KO",
        pays_common_dividend=True,
    )
    assert len(pts) == 8
    t12m = sum((0.0207, 0.51, 0.51, 1.0199)) * 4.3e9 / 300e9 * 100
    for p in pts:
        assert abs(p.dividend_yield - t12m) / t12m < 0.05, (p.period, p.dividend_yield)
    # the raw quarter cash is untouched — the Capital view still shows the timing
    assert {round(p.dividend_amount) for p in pts} == {89, 2193, 4386}


def test_a_single_asr_quarter_does_not_dwarf_every_other_bar():
    """$1B a quarter plus one $10B ASR on a $100B cap: x4 read 40% for the ASR quarter."""
    bb = [-1e9] * 12
    bb[9] = -10e9
    cfs = [_cf(d, commonStockRepurchased=v) for d, v in zip(_QEND, bb)]
    pts = _svc()._build_data_points(
        cfs, [_inc(d) for d in _QEND], 100e9, _caps(_QEND, 100e9), "ASR")
    ttm_with_asr = (10e9 + 3e9) / 100e9 * 100
    assert max(p.buyback_yield for p in pts) <= ttm_with_asr + 1e-9
    assert min(p.buyback_yield for p in pts) == pytest.approx(4.0)
    assert max(p.buyback_amount for p in pts) == 10000.0      # raw cash kept


def test_a_gap_in_the_cash_flow_history_falls_back_to_x4_and_says_so(caplog):
    dates = [d for d in _QEND if d != "2025-03-31"]           # one missing quarter
    cfs = [_cf(d, commonStockRepurchased=-1e9) for d in dates]
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        pts, diag = _svc()._build_quarters(
            cfs, [_inc(d) for d in dates], 100e9, _caps(dates, 100e9), "GAP")
    by = {p.period: p for p in pts}
    # windows that straddle the gap are not twelve months — they fall back to x4 (4.0
    # here, the same number only because the cash is flat) and are named
    assert diag.ttm_fallback_periods == ["Q2 '25", "Q3 '25", "Q4 '25"]
    assert all(by[lbl].buyback_yield == pytest.approx(4.0) for lbl in diag.ttm_fallback_periods)
    assert "[soc-ttm-fallback]" in caplog.text


def test_the_x4_fallback_is_used_with_fewer_than_four_quarters():
    cfs = [_cf(d, commonStockRepurchased=-1e9) for d in _QEND[-2:]]
    pts, diag = _svc()._build_quarters(
        cfs, [_inc(d) for d in _QEND[-2:]], 100e9, _caps(_QEND, 100e9), "IPO")
    assert [p.buyback_yield for p in pts] == [4.0, 4.0]
    assert len(diag.ttm_fallback_periods) == 2


def test_sixteen_week_quarters_still_count_as_consecutive():
    """Costco's fiscal Q4 is 16 weeks (112 days) — a ~100-day cap would have pushed
    three of every four COST windows back to x4."""
    assert S._is_consecutive_window(["2025-02-16", "2025-05-11", "2025-08-31", "2025-11-23"])
    assert not S._is_consecutive_window(["2025-01-31", "2025-03-01", "2025-06-30", "2025-09-30"]), \
        "a 29-day fiscal-year-change stub is not a quarter"
    assert not S._is_consecutive_window(["2024-12-31", "2025-06-30", "2025-09-30", "2025-12-31"]), \
        "a missing quarter (181-day step) is not consecutive"
    assert not S._is_consecutive_window(["2025-01-01", "2025-x", "2025-06-30", "2025-09-30"])


def test_a_stopped_payer_trailing_sum_excludes_the_zeroed_quarters():
    """INTC: FY2024 paid, FY2025 = 0 with a stray mis-tagged line. The trailing sum of a
    FY2025 quarter may carry FY2024's REAL dividends, never the stray line."""
    dates = _QEND[:8]                                          # 2023-09-30 … 2025-06-30
    cfs = [_cf(d, commonDividendsPaid=-500e6) for d in dates]
    cfs[-1] = _cf(dates[-1], commonDividendsPaid=-14_339e6)    # stray FY2025 line
    by_year = {"2023": 0.5, "2024": 0.5, "2025": 0.0}
    pts = _svc()._build_data_points(
        cfs, [_inc(d) for d in dates], 100e9, _caps(dates, 100e9), "INTC",
        pays_common_dividend=False, dividend_by_year=by_year)
    newest = pts[-1]
    assert newest.dividend_amount == 0.0
    # window Q3'24..Q2'25 = two FY2024 quarters (1.0B) + two zeroed FY2025 quarters
    assert newest.dividend_yield == pytest.approx(1.0)
    assert newest.dividend_yield < 2.0, "the −14.3B stray line leaked into the trailing sum"


def test_the_dividend_verdict_compares_the_newest_ttm_point_with_older_ones():
    """recent = the newest TTM point; baseline = points at least four quarters older."""
    class _P:
        def __init__(self, y):
            self.dividend_yield, self.period = y, None

    svc = _svc()
    # a 40% cut that only the NEWEST window shows — the old mean-of-last-4 diluted it
    pts = [_P(3.0)] * 7 + [_P(1.8)]
    info = svc._build_dividend_info([], 1.8, 0.0, 0.0, data_points=pts, pays_common_dividend=True)
    assert info.status == "Low"
    flat = svc._build_dividend_info([], 3.0, 0.0, 0.0, data_points=[_P(3.0)] * 8,
                                    pays_common_dividend=True)
    assert flat.status == "Fair"


def test_the_summary_fallback_without_a_current_cap_reads_the_newest_ttm_point():
    pts = _svc()._build_data_points(
        [_cf(d, commonStockRepurchased=-1e9) for d in _QEND],
        [_inc(d) for d in _QEND], None, _caps(_QEND, 100e9), "X")
    s = _svc()._build_summary(pts, None)
    assert s.buyback_yield == pytest.approx(4.0)


# ── #53: no usable market cap ────────────────────────────────────────────────


def _aapl_like_cf(dates):
    return [_cf(d, commonDividendsPaid=-3.7e9, commonStockRepurchased=-2.5e10) for d in dates]


class _FMP:
    """Hermetic FMP stand-in (testing.md: never a live call)."""

    def __init__(self, *, cashflow, income, ratios=None, profile=None, quote=None,
                 hist=None, hist_raises=False):
        self._cf, self._inc = cashflow, income
        self._ratios = ratios if ratios is not None else [
            {"date": "2024-09-28", "dividendPerShare": 0.98},
            {"date": "2025-09-27", "dividendPerShare": 1.01}]
        self._profile = profile if profile is not None else [{"lastDividend": 1.01}]
        self._quote = quote if quote is not None else {"marketCap": 3.5e12, "price": 230.0}
        self._hist = hist
        self._hist_raises = hist_raises

    async def get_cash_flow_statement(self, ticker, period="quarter", limit=20):
        return self._cf

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return self._inc

    async def get_financial_ratios(self, ticker, period="annual", limit=10):
        return self._ratios

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        return []

    async def get_historical_market_cap(self, ticker, from_date=None, to_date=None, limit=2000):
        if self._hist_raises:
            raise RuntimeError("historical-market-capitalization 503")
        return self._hist if self._hist is not None else []

    async def get_company_profile(self, ticker):
        return self._profile

    async def get_stock_price_quote(self, ticker):
        return self._quote


class _CorporateActions:
    """The tri-state spin-off seam. `unknown` answers None (a price leg could not be
    derived). The fail-closed `has_unclassified_adjustment` (13F backstop) must never be
    the SoC's source again — its "could not look" True read as a spin-off (round-2 R20)."""

    def __init__(self, spin=False, raises=False, unknown=False):
        self.spin, self.raises, self.unknown, self.calls = spin, raises, unknown, []

    async def get_ex_dividend_dates(self, symbol, from_date=None, to_date=None):
        return []

    async def unclassified_adjustment_or_none(self, symbol, from_date=None, to_date=None, *,
                                              effective_from=None, effective_to=None):
        self.calls.append((symbol, from_date, to_date, effective_from, effective_to))
        if self.raises:
            raise RuntimeError("price series down")
        return None if self.unknown else self.spin

    async def has_unclassified_adjustment(self, *a, **k):
        raise AssertionError("the SoC spin-off guard asked the FAIL-CLOSED 13F method")


def _wire(fmp, ca=None) -> S:
    from tests._price_fakes import PriceFromFMPFake
    svc = _svc()
    svc.fmp = fmp
    svc.price = PriceFromFMPFake(fmp)
    svc.corporate_actions = ca or _CorporateActions()
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


@pytest.mark.asyncio
async def test_no_market_cap_anywhere_is_degraded_not_a_low_verdict(monkeypatch):
    """Quote `{}` (price_service swallowed its profile failure), historical cap down,
    profile without a cap: every yield would read 0% and both verdicts "Low" for a top
    repurchaser — and it was written to the 24h tier."""
    dates = _QEND[-2:]
    fmp = _FMP(cashflow=_aapl_like_cf(dates), income=[_inc(d) for d in dates],
               quote={}, profile=[{"lastDividend": 1.01}], hist_raises=True)
    resp, _, degraded = await _wire(fmp)._build_signal_of_confidence("AAPL")
    assert "market_cap" in degraded
    assert resp.degraded == degraded

    out, writes = await _served(_wire(fmp), "AAPL", monkeypatch)
    assert writes == [], "a build with unpriced quarters reached the 24h tier"
    assert "market_cap" in out.degraded
    ts, _value, ttl = sos._cache["signal_of_confidence:AAPL"]
    assert ttl == sos._DEGRADED_CACHE_TTL


@pytest.mark.asyncio
async def test_the_profile_cap_rescues_a_failed_quote_and_history(monkeypatch):
    dates = _QEND[-2:]
    fmp = _FMP(cashflow=_aapl_like_cf(dates), income=[_inc(d) for d in dates],
               quote={}, profile=[{"lastDividend": 1.01, "marketCap": 3.5e12}],
               hist_raises=True)
    resp, _, degraded = await _wire(fmp)._build_signal_of_confidence("AAPL")
    assert degraded == []
    assert all(p.buyback_yield > 0 and p.dividend_yield > 0 for p in resp.data_points)
    assert resp.summary.buyback_yield > 0

    out, writes = await _served(_wire(fmp), "AAPL", monkeypatch)
    assert out.degraded == [] and writes, "control: a priced build still persists"
    assert sos._cache["signal_of_confidence:AAPL"][2] == sos._CACHE_TTL


@pytest.mark.asyncio
async def test_a_zero_or_nan_profile_cap_is_not_a_cap():
    dates = _QEND[-2:]
    for bad in (0, float("nan"), -5, "n/a"):
        fmp = _FMP(cashflow=_aapl_like_cf(dates), income=[_inc(d) for d in dates],
                   quote={"marketCap": 0}, profile=[{"marketCap": bad, "lastDividend": 1.0}],
                   hist_raises=True)
        _resp, _, degraded = await _wire(fmp)._build_signal_of_confidence("AAPL")
        assert "market_cap" in degraded, bad


def test_a_non_returning_unpriced_quarter_is_not_degraded():
    """No capital returned → nothing to price → not a gap."""
    pts, diag = _svc()._build_quarters(
        [_cf(d) for d in _QEND[-2:]], [_inc(d) for d in _QEND[-2:]], None, {}, "Z")
    assert diag.unpriced_periods == []
    assert all(p.dividend_yield == 0 and p.buyback_yield == 0 for p in pts)


# ── #85: duplicate rows ──────────────────────────────────────────────────────


def _bb_fixture():
    dates = _QEND[-8:]
    bb = [-1e9] * 7 + [-10e9]
    return dates, [_cf(d, commonStockRepurchased=v) for d, v in zip(dates, bb)]


def test_a_restated_duplicate_income_row_is_collapsed():
    dates, cfs = _bb_fixture()
    inc = [_inc(d) for d in dates]
    inc.append(dict(inc[-1]))
    svc = _svc()
    pts = svc._build_data_points(cfs, inc, 100e9, _caps(dates, 100e9), "DUP")
    assert len(pts) == 8 and len({p.period for p in pts}) == 8
    assert svc._build_summary(pts, 100e9).buyback_yield == pytest.approx(13.0), \
        "the duplicate double-counted the T12M (22.0)"


def test_two_dates_sharing_a_label_keep_the_later_one(caplog):
    """A fiscal-year-end change: two period ends both labelled Q4 '25."""
    inc = [_inc("2025-06-30", period="Q2", fy="2025"),
           _inc("2025-09-30", shares=4.0e9, period="Q4", fy="2025"),
           _inc("2025-12-31", shares=3.9e9, period="Q4", fy="2025")]
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        pts = _svc()._build_data_points([], inc, 1e9, {}, "FYE")
    assert [p.period for p in pts] == ["Q2 '25", "Q4 '25"]
    assert pts[-1].shares_outstanding == 3900.0, "the EARLIER row survived"
    assert "[soc-dup-label]" in caplog.text


def test_a_duplicate_cash_flow_date_keeps_the_first_newest_filed_row():
    inc = [_inc("2026-06-30")]
    cfs = [_cf("2026-06-30", commonStockRepurchased=-2e9),     # restatement (FMP lists it first)
           _cf("2026-06-30", commonStockRepurchased=-1e9)]     # stale original
    pt = _svc()._build_data_points(cfs, inc, 100e9, {}, "R")[0]
    assert pt.buyback_amount == 2000.0


def test_duplicates_never_shrink_the_window_below_eight_distinct_quarters():
    dates = _QEND
    inc = [_inc(d) for d in dates] + [dict(_inc(d)) for d in dates[-3:]]
    pts = _svc()._build_data_points([], inc, 1e9, {}, "X")
    assert [p.period for p in pts] == [f"{_q(d)} '{d[2:4]}" for d in dates[-8:]]


# ── #86: spin-off baseline + single-day cap glitch ───────────────────────────


class _P:
    def __init__(self, y, period=None):
        self.dividend_yield, self.period = y, period


def test_a_spin_off_in_the_window_refuses_the_relative_verdict(caplog):
    """DD→Qnity-shaped: pre-spin caps too small by ~2, so the older yields read 4% on a
    real 2%. Ratio 0.5 → "Low" for a payer whose yield never moved."""
    svc = _svc()
    pts = [_P(y) for y in (4, 4, 4, 4, 4, 2, 2, 2)]
    control = svc._build_dividend_info([], 2.0, 0.0, 0.0, data_points=pts,
                                       pays_common_dividend=True)
    assert control.status == "Low", "control: the relative path is what goes wrong"
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        info = svc._build_dividend_info([], 2.0, 0.0, 0.0, data_points=pts,
                                        pays_common_dividend=True, spinoff_in_window=True,
                                        ticker="DD")
    assert info.status == "High"      # the absolute ladder on the current-cap 2.0%
    assert "[soc-spinoff-baseline] ticker=DD" in caplog.text


def test_a_one_day_cap_glitch_at_a_period_end_is_replaced_by_the_window_median(caplog):
    end = date(2026, 6, 30)
    lookup = {(end + timedelta(days=k)).isoformat(): 100e9 for k in range(-5, 6)}
    lookup[end.isoformat()] = 10e9                         # dropped digit
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        cap = sos._robust_market_cap_on(end.isoformat(), lookup, "AVGO")
    assert cap == pytest.approx(100e9)
    assert "[soc-mcap-outlier]" in caplog.text

    cfs = [_cf(d, commonStockRepurchased=-1e9) for d in _QEND]
    caps = {d: 100e9 for d in _QEND}
    caps.update({(date.fromisoformat(d) + timedelta(days=k)).isoformat(): 100e9
                 for d in _QEND for k in (-2, -1, 1, 2)})
    clean = _svc()._build_data_points(cfs, [_inc(d) for d in _QEND], 100e9, dict(caps), "X")
    caps["2026-06-30"] = 10e9
    glitched = _svc()._build_data_points(cfs, [_inc(d) for d in _QEND], 100e9, caps, "X")
    assert abs(glitched[-1].buyback_yield - clean[-1].buyback_yield) / clean[-1].buyback_yield < 0.10


def test_a_thin_neighbourhood_keeps_the_nearest_value():
    """Fewer than three neighbours: no median to trust, nothing replaced."""
    assert sos._robust_market_cap_on("2026-06-30", {"2026-06-30": 10e9, "2026-07-01": 100e9}) == 10e9
    assert sos._robust_market_cap_on("2026-06-30", {}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("spin, raises, unknown, expect_low, expect_degraded", [
    (False, False, False, True, []),
    (True, False, False, False, []),
    # could not check (a raise, or the tri-state None): NOT a spin-off — the relative
    # verdict runs — and the build is marked so it never reaches the 24h tier (R20)
    (False, True, False, True, ["spinoff_check"]),
    (False, False, True, True, ["spinoff_check"]),
])
async def test_the_builder_asks_corporate_actions_over_the_displayed_window(
        spin, raises, unknown, expect_low, expect_degraded, caplog):
    """Wiring: the spin check runs from the oldest DISPLAYED period end to today, its True
    refuses the ratio, and an unknown answer keeps the ratio and degrades the build."""
    dates = _QEND
    # yields 4% for the first displayed half, 2% after (the pre-/post-spin shape)
    cash = [-1e9] * 7 + [-0.5e9] * 5
    cfs = [_cf(d, commonDividendsPaid=v) for d, v in zip(dates, cash)]
    fmp = _FMP(cashflow=cfs, income=[_inc(d) for d in dates],
               quote={"marketCap": 100e9},
               hist=[{"date": d, "marketCap": 100e9} for d in dates])
    ca = _CorporateActions(spin=spin, raises=raises, unknown=unknown)
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        resp, _, degraded = await _wire(fmp, ca)._build_signal_of_confidence("DD")
    assert degraded == expect_degraded
    assert resp.degraded == expect_degraded
    assert ca.calls and ca.calls[0][3] == _QEND[-8], "effective_from must be the oldest displayed quarter"
    assert (resp.dividend_info.status == "Low") is expect_low
    if raises or unknown:
        assert "[soc-spinoff-check-unavailable] ticker=DD" in caplog.text
        assert "[soc-spinoff-baseline]" not in caplog.text, "an unknown check blamed a spin-off"


@pytest.mark.asyncio
async def test_a_non_payer_never_spends_the_spin_off_lookup():
    fmp = _FMP(cashflow=[_cf(d, commonStockRepurchased=-1e9) for d in _QEND],
               income=[_inc(d) for d in _QEND], quote={"marketCap": 100e9},
               ratios=[{"date": "2025-12-31", "dividendPerShare": 0}],
               profile=[{"lastDividend": 0}],
               hist=[{"date": d, "marketCap": 100e9} for d in _QEND])
    ca = _CorporateActions()
    await _wire(fmp, ca)._build_signal_of_confidence("AMZN")
    assert ca.calls == []


# ── #87: the per-fiscal-year map is keyed by fiscalYear ──────────────────────


HD_RATIOS = [{"date": "2025-02-02", "fiscalYear": "2024", "dividendPerShare": 0},
             {"date": "2026-02-01", "fiscalYear": "2025", "dividendPerShare": 0.4}]


def test_start_year_named_fiscal_years_key_on_fiscal_year():
    assert S._annual_dividend_map(HD_RATIOS) == {"2024": 0.0, "2025": 0.4}
    # 53-week year closing in early January, with and without fiscalYear
    assert S._annual_dividend_map([{"date": "2026-01-03", "fiscalYear": "2025",
                                    "dividendPerShare": 1.0}]) == {"2025": 1.0}
    assert S._annual_dividend_map([{"date": "2026-01-03", "dividendPerShare": 1.0}]) == {"2025": 1.0}


def test_two_rows_for_one_fiscal_year_keep_the_later_dated_row():
    rows = [{"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": 0.9},
            {"date": "2025-06-30", "fiscalYear": "2025", "dividendPerShare": 0.1}]
    assert S._annual_dividend_map(rows) == {"2025": 0.9}
    assert S._annual_dividend_map(list(reversed(rows))) == {"2025": 0.9}


def test_an_hd_style_initiator_keeps_its_first_paying_year_bars_and_label():
    svc = _svc()
    by_year = S._annual_dividend_map(HD_RATIOS)
    inc = [_inc(d, period=p, fy="2025") for d, p in
           (("2025-05-04", "Q1"), ("2025-08-03", "Q2"), ("2025-11-02", "Q3"), ("2026-02-01", "Q4"))]
    cfs = [_cf(r["date"], commonDividendsPaid=-100e6) for r in inc]
    pts = svc._build_data_points(cfs, inc, 100e9, _caps([r["date"] for r in inc], 100e9), "HD",
                                 pays_common_dividend=True, dividend_by_year=by_year)
    assert all(p.dividend_amount == 100.0 for p in pts), "FY2025 looked up FY2024's zero"
    info = svc._build_dividend_info([], 0.4, 0.0, 0.0, data_points=pts, annual_ratios=HD_RATIOS,
                                    pays_common_dividend=True)
    assert info.dividend_per_share_year == "2025"
    assert S._initiation_observed(HD_RATIOS, info.annual_dividends) is True


# ── #88: a quarter without a cash-flow row ───────────────────────────────────


def test_a_missing_newest_cash_flow_row_is_left_out_of_the_t12m(caplog):
    """Round-2 R47: the newest quarter's cash is UNKNOWN, so the series ends at the last
    quarter that HAS a cash-flow row — never a "$0" newest point that the report header,
    mini-chart and Stage B prompt read as a measured zero."""
    dates = _QEND[-8:]
    cfs = [_cf(d, commonStockRepurchased=-1e9) for d in dates[:-1]]
    svc = _svc()
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        pts, diag = svc._build_quarters(cfs, [_inc(d) for d in dates], 100e9,
                                         _caps(dates, 100e9), "MISS")
    assert len(pts) == 7, "only eight quarters exist, and the newest has no cash yet"
    assert pts[-1].period == "Q1 '26" and pts[-1].buyback_amount > 0
    assert diag.missing_cash_flow_periods == ["Q2 '26"] and diag.missing_cash_flow_recent
    s = svc._build_summary(pts, 100e9, missing_cash_flow_periods=set(diag.missing_cash_flow_periods))
    assert s.buyback_yield == pytest.approx(4.0), "the unknown quarter was summed as $0 (3.0)"
    assert s.buyback_status == "Very High"
    assert "[soc-cashflow-row-missing] MISS" in caplog.text and "2026-06-30" in caplog.text


def test_an_old_missing_row_is_logged_but_not_recent():
    dates = _QEND[-8:]
    cfs = [_cf(d, commonStockRepurchased=-1e9) for d in dates[1:]]
    _pts, diag = _svc()._build_quarters(cfs, [_inc(d) for d in dates], 100e9, {}, "OLD")
    assert diag.missing_cash_flow_periods == [f"{_q(dates[0])} '{dates[0][2:4]}"]
    assert diag.missing_cash_flow_recent is False


@pytest.mark.asyncio
async def test_a_missing_recent_row_degrades_the_build(monkeypatch):
    dates = _QEND[-8:]
    fmp = _FMP(cashflow=[_cf(d, commonStockRepurchased=-1e9) for d in dates[:-1]],
               income=[_inc(d) for d in dates], quote={"marketCap": 100e9},
               ratios=[{"date": "2025-12-31", "dividendPerShare": 0}], profile=[{"lastDividend": 0}],
               hist=[{"date": d, "marketCap": 100e9} for d in dates])
    out, writes = await _served(_wire(fmp), "MISS", monkeypatch)
    assert out.degraded == ["cash_flow_row"] and writes == []
    assert out.summary.buyback_yield == pytest.approx(4.0)


def test_the_dividend_card_skips_unknown_quarters():
    pts = [_P(2.0, f"Q{i}") for i in range(8)] + [_P(0.0, "MISSING")]
    info = _svc()._build_dividend_info([], 2.0, 0.0, 0.0, data_points=pts,
                                       pays_common_dividend=True,
                                       missing_cash_flow_periods={"MISSING"})
    assert info.status == "Fair", "the unknown newest quarter read as a cut to 0%"
    assert info.five_year_avg_yield == 2.0 and info.avg_yield_window == "8Q"


# ── #92: an unmeasured share-count change is not "unchanged" ─────────────────


@pytest.mark.parametrize("shares, known", [
    ([None] * 4, False),
    ([None, None, None, 1500.0], False),
    ([1500.0], False),
    ([1500.0, None, None, 1450.0], True),
])
def test_share_count_change_known(shares, known):
    from app.schemas.signal_of_confidence import SignalOfConfidenceDataPointSchema as DP
    pts = [DP(period=f"Q{i} '26", shares_outstanding=s) for i, s in enumerate(shares)]
    s = _svc()._build_summary(pts, 1e9)
    assert s.share_count_change_known is known
    if not known:
        assert s.share_count_change == 0.0


def test_an_empty_build_does_not_claim_a_measured_change():
    assert _svc()._build_summary([], 1e9).share_count_change_known is False


# ── #56: the average's window is stated ──────────────────────────────────────


def test_avg_yield_window_names_the_quarters_averaged():
    svc = _svc()
    info = svc._build_dividend_info([], 1.0, 0.0, 0.0, data_points=[_P(1.0)] * 6,
                                    pays_common_dividend=True)
    assert info.avg_yield_window == "6Q"
    few = svc._build_dividend_info([], 1.0, 0.0, 0.0, data_points=[_P(1.0)] * 2,
                                   pays_common_dividend=True)
    assert few.five_year_avg_yield == 0.0 and few.avg_yield_window is None
    zero = svc._build_dividend_info([], 1.0, 0.0, 0.0, data_points=[_P(0.0)] * 8,
                                    pays_common_dividend=True)
    assert zero.avg_yield_window is None, "no average, nothing to label"


# ── wire contract ────────────────────────────────────────────────────────────


def test_new_fields_are_additive_and_defaulted():
    assert SignalOfConfidenceResponse(
        symbol="X", data_points=[], summary=SignalOfConfidenceSummarySchema()).degraded == []
    assert SignalOfConfidenceSummarySchema().share_count_change_known is True
    assert DividendInfoSchema().avg_yield_window is None
    # a payload written before the fields existed still validates
    old = {"symbol": "X", "data_points": [], "summary": {"total_yield": 1.0},
           "dividend_info": {"five_year_avg_yield": 1.0}}
    SignalOfConfidenceResponse.model_validate(old)


def test_payload_version_bumped_for_the_ttm_yields():
    assert sos._PAYLOAD_VERSION >= 7


@pytest.mark.asyncio
async def test_a_healthy_build_serves_an_empty_degraded_list_and_persists(monkeypatch):
    fmp = _FMP(cashflow=_aapl_like_cf(_QEND), income=[_inc(d) for d in _QEND],
               hist=[{"date": d, "marketCap": 3.5e12} for d in _QEND])
    out, writes = await _served(_wire(fmp), "AAPL", monkeypatch)
    assert out.degraded == [] and writes
    assert out.summary.share_count_change_known is True
    assert out.dividend_info is not None and out.dividend_info.avg_yield_window == "8Q"


def test_a_cached_row_carrying_degraded_reasons_is_refused():
    """Defense in depth behind the write gate: a 24h row must never serve a partial build."""
    from datetime import datetime, timezone

    def _sb(payload):
        class _Res:
            data = [{"response_json": payload,
                     "cached_at": datetime.now(timezone.utc).isoformat(),
                     "next_earnings_date": None}]

        class _Tbl:
            def select(self, *a, **k): return self
            def eq(self, *a, **k): return self
            def limit(self, *a, **k): return self
            def execute(self): return _Res()

        class _SB:
            def table(self, *_a, **_k): return _Tbl()
        return _SB()

    body = {"symbol": "X", "data_points": [], "summary": {"total_yield": 1.0},
            "dividend_info": None, "payload_version": sos._PAYLOAD_VERSION}
    svc = _svc()
    svc.supabase = _sb({**body, "degraded": ["market_cap"]})
    assert svc._check_supabase_cache("X") is None
    svc.supabase = _sb({**body, "degraded": []})
    assert svc._check_supabase_cache("X") is not None, "control: a clean current row is served"
    svc.supabase = _sb({**body, "payload_version": 6})
    assert svc._check_supabase_cache("X") is None, "a pre-TTM (x4) row must be recomputed"


def test_a_one_day_date_wobble_between_the_statements_is_not_a_missing_row():
    """Both statements come from one filing; a vendor date wobble must not keep the
    ticker out of the 24h tier forever. A far-off date still counts as missing."""
    dates = _QEND[-4:]
    cfs = [_cf(d, commonStockRepurchased=-1e9) for d in dates[:-1]]
    cfs.append(_cf("2026-06-29", commonStockRepurchased=-1e9))       # income says 06-30
    pts, diag = _svc()._build_quarters(cfs, [_inc(d) for d in dates], 100e9, {}, "W")
    assert diag.missing_cash_flow_periods == []
    assert pts[-1].buyback_amount == 1000.0 and pts[-1].buyback_yield == pytest.approx(4.0)

    cfs[-1] = _cf("2026-06-15", commonStockRepurchased=-1e9)         # 15 days off
    _pts, diag = _svc()._build_quarters(cfs, [_inc(d) for d in dates], 100e9, {}, "W")
    assert diag.missing_cash_flow_periods == ["Q2 '26"] and diag.missing_cash_flow_recent
