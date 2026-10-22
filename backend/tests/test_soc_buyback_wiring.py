"""The SEC buyback check, wired into Signal of Confidence (`_verify_buybacks`).

The rule itself is pinned in tests/test_soc_buyback_verification.py. These tests pin what
the SERVICE does with it, because a leaf test stays green when a mutation stops threading
the corrected figure into the bars or the trailing-12-month windows:

* INTC-shaped: FMP's withholding "buybacks" chart as 0 — the bar AND every window that
  touches the quarter — and the build is still persisted (the check succeeded).
* A real repurchaser: the same response whether the check runs or not.
* SEC down with no cached filing data: FMP's figures are kept, the build is degraded
  `buyback_check`, never persisted, and the report refuses the section (an AAPL-shaped
  repurchaser is NOT turned into "Low").
* The check off (no User-Agent), no CIK, or no buyback at all: SEC is never asked and
  nothing is degraded.

Hermetic (testing.md): FMP and the SEC concept service are in-test stand-ins.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.config import settings
from app.integrations.sec_edgar import SECConfigException, SECUnavailableException
from app.services import signal_of_confidence_service as sos
from app.services._buyback_verification import REPURCHASE_TAG, SEC_BUYBACK_TAGS, WITHHOLDING_TAG
from app.services.signal_of_confidence_service import SignalOfConfidenceService as S

UA = "Caydex test test@example.com"
CIK = "0000050863"
M = 1_000_000

#: Twenty calendar quarter ends, oldest first: Q1'21 … Q4'25.
_QEND = [f"{y}-{md}" for y in range(2021, 2026) for md in ("03-31", "06-30", "09-30", "12-31")]
#: INTC's shape: tax withholding filed under `commonStockRepurchased` in two Q2s only.
_WITHHOLDING_Q = {"2024-06-30": 150 * M, "2025-06-30": 181 * M}


def _q(d: str) -> str:
    return f"Q{(int(d[5:7]) - 1) // 3 + 1}"


def _inc(d: str, shares: float = 4.3e9) -> dict:
    return {"date": d, "period": _q(d), "fiscalYear": d[:4], "weightedAverageShsOut": shares}


def _intc_cf(cik: object = CIK) -> list:
    rows = []
    for d in _QEND:
        row = {"date": d, "commonStockRepurchased": -_WITHHOLDING_Q.get(d, 0)}
        if cik is not None:
            row["cik"] = cik
        rows.append(row)
    return rows


def _usd(*rows: tuple) -> dict:
    return {"units": {"USD": [
        {"start": s, "end": e, "val": v, "filed": "2025-08-01", "form": "10-Q"} for s, e, v in rows
    ]}}


#: What the SEC concept service answers for the INTC shape: no repurchase concept (404),
#: withholding tagged only as half-year spans.
INTC_CONCEPTS = {
    REPURCHASE_TAG: None,
    WITHHOLDING_TAG: _usd(("2024-01-01", "2024-06-30", 150 * M), ("2025-01-01", "2025-06-30", 181 * M)),
}


class _FMP:
    def __init__(self, *, cashflow, income, cap=400e9, profile=None):
        self._cf, self._inc, self._cap = cashflow, income, cap
        self._profile = profile if profile is not None else [{"lastDividend": 0}]

    async def get_cash_flow_statement(self, ticker, period="quarter", limit=20):
        return self._cf if period == "quarter" else []

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return self._inc

    async def get_financial_ratios(self, ticker, period="annual", limit=10):
        return [{"date": "2025-12-31", "fiscalYear": "2025", "dividendPerShare": 0}]

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        return []

    async def get_historical_market_cap(self, ticker, from_date=None, to_date=None, limit=2000):
        return [{"date": r["date"], "marketCap": self._cap} for r in self._inc]

    async def get_company_profile(self, ticker):
        return self._profile

    async def get_stock_price_quote(self, ticker):
        return {"marketCap": self._cap, "price": 89.85}


class _NoSpinOff:
    async def get_ex_dividend_dates(self, symbol, from_date=None, to_date=None):
        return []

    async def unclassified_adjustment_or_none(self, symbol, from_date=None, to_date=None, *,
                                              effective_from=None, effective_to=None):
        return False


class _SecService:
    """Stand-in for `sec_concept_service`: an answer, an exception, or a hang. Like the real
    service it answers ONLY the tags it is asked for, so a caller that asks for too few
    gets too little. ``cached`` is what the cache-only read holds (None: nothing)."""

    def __init__(self, answer=None, *, raises=None, hang=False, stale=False, cached=None):
        self.answer, self.raises, self.hang, self.stale = answer, raises, hang, stale
        self.cached = cached
        self.calls = []
        self.cache_calls = []

    async def get_concepts(self, cik, tags, *, newest_period_end=None):
        self.calls.append((cik, tuple(tags), newest_period_end))
        if self.hang:
            await asyncio.sleep(3600)
        if self.raises is not None:
            raise self.raises
        return SimpleNamespace(cik=cik, concepts={t: (self.answer or {}).get(t) for t in tags},
                               stale=self.stale)

    async def get_cached_concepts(self, cik, tags):
        self.cache_calls.append((cik, tuple(tags)))
        if self.cached is None:
            return None
        return SimpleNamespace(cik=cik, concepts={t: self.cached.get(t) for t in tags}, stale=True)


def _wire(fmp) -> S:
    from tests._price_fakes import PriceFromFMPFake
    svc = S.__new__(S)
    svc.fmp = fmp
    svc.price = PriceFromFMPFake(fmp)
    svc.corporate_actions = _NoSpinOff()
    return svc


@pytest.fixture
def sec(monkeypatch):
    """The SEC check ON, with a stand-in service the test configures."""
    fake = _SecService(INTC_CONCEPTS)
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", UA)
    monkeypatch.setattr(sos, "get_sec_concept_service", lambda: fake)
    return fake


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
    from app.services.agents.ticker_report_data_collector import _refuse_degraded_financials

    out = SimpleNamespace(
        ticker="T", degraded_sections=[], signal_of_confidence=resp,
        growth_chart=None, profit_power=None, earnings=None, revenue_breakdown=None,
    )
    _refuse_degraded_financials(out)
    return out.signal_of_confidence, list(out.degraded_sections)


def _by_period(resp) -> dict:
    return {p.period: p for p in resp.data_points}


# ── The INTC shape, end to end ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_intc_withholding_charts_as_no_buyback_and_is_persisted(sec, monkeypatch):
    out, writes = await _served(_wire(_FMP(cashflow=_intc_cf(), income=[_inc(d) for d in _QEND])),
                                "INTC", monkeypatch)
    assert sec.calls == [(CIK, tuple(SEC_BUYBACK_TAGS), "2025-12-31")]
    assert all(p.buyback_amount == 0.0 and p.buyback_yield == 0.0 for p in out.data_points)
    assert out.summary.buyback_yield == 0.0
    assert "buyback_check" not in out.degraded
    assert writes, "a successful check is a healthy build: it reaches the 24h tier"


@pytest.mark.asyncio
async def test_without_the_check_the_same_input_charts_the_withholding(monkeypatch):
    """Non-vacuity: the INTC fixture DOES chart buybacks when the check is off, in the
    quarter and in the three trailing windows after it."""
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "")
    out, _ = await _served(_wire(_FMP(cashflow=_intc_cf(), income=[_inc(d) for d in _QEND])),
                           "INTC", monkeypatch)
    pts = _by_period(out)
    assert pts["Q2 '25"].buyback_amount == 181.0
    assert all(pts[q].buyback_yield > 0 for q in ("Q2 '25", "Q3 '25", "Q4 '25"))


def test_an_override_reaches_every_trailing_window_that_touches_the_quarter():
    cf = _intc_cf()
    inc = [_inc(d) for d in _QEND]
    mcap = {d: 400e9 for d in _QEND}
    plain = S.__new__(S)._build_data_points(cf, inc, 400e9, mcap, "INTC", pays_common_dividend=False)
    fixed = S.__new__(S)._build_data_points(
        cf, inc, 400e9, mcap, "INTC", pays_common_dividend=False,
        buyback_override={"2025-06-30": 0.0, "2024-06-30": 0.0},
    )
    before, after = _by_period(SimpleNamespace(data_points=plain)), _by_period(SimpleNamespace(data_points=fixed))
    for q in ("Q2 '25", "Q3 '25", "Q4 '25"):
        assert before[q].buyback_yield > 0 and after[q].buyback_yield == 0.0
    assert after["Q2 '25"].buyback_amount == 0.0


def test_an_override_can_never_raise_a_figure():
    cf = _intc_cf()
    inc = [_inc(d) for d in _QEND]
    pts = S.__new__(S)._build_data_points(
        cf, inc, 400e9, {d: 400e9 for d in _QEND}, "INTC", pays_common_dividend=False,
        buyback_override={"2025-06-30": 9e12, "2025-03-31": 5e9, "2024-06-30": float("nan")},
    )
    by = _by_period(SimpleNamespace(data_points=pts))
    assert by["Q2 '25"].buyback_amount == 181.0
    assert by["Q1 '25"].buyback_amount == 0.0
    assert by["Q2 '24"].buyback_amount == 150.0


# ── A real repurchaser is untouched ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_real_repurchaser_gets_the_same_response_with_the_check_on(sec, monkeypatch):
    cf = [{"date": d, "cik": "320193", "commonStockRepurchased": -20_000 * M} for d in _QEND]
    concepts = {
        REPURCHASE_TAG: _usd(*[(f"{d[:4]}-{int(d[5:7]) - 2:02d}-01", d, 20_000 * M) for d in _QEND]),
        WITHHOLDING_TAG: None,
    }
    sec.answer = concepts
    fmp = _FMP(cashflow=cf, income=[_inc(d, 15e9) for d in _QEND], cap=3.5e12)
    on, _ = await _served(_wire(fmp), "AAPL", monkeypatch)
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "")
    off, _ = await _served(_wire(fmp), "AAPL", monkeypatch)
    assert len(sec.calls) == 1 and sec.calls[0][0] == "0000320193"
    assert [p.model_dump() for p in on.data_points] == [p.model_dump() for p in off.data_points]
    assert on.summary.model_dump() == off.summary.model_dump()
    assert on.summary.buyback_yield > 0


# ── Failures and steady states ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sec_down_keeps_fmp_degrades_and_the_report_refuses(sec, monkeypatch, caplog):
    sec.raises = SECUnavailableException("503 from SEC")
    cf = [{"date": d, "cik": "320193", "commonStockRepurchased": -20_000 * M} for d in _QEND]
    fmp = _FMP(cashflow=cf, income=[_inc(d, 15e9) for d in _QEND], cap=3.5e12)
    with caplog.at_level(logging.WARNING, logger=sos.__name__):
        out, writes = await _served(_wire(fmp), "AAPL", monkeypatch)
    assert "buyback_check" in out.degraded
    assert writes == [], "an unchecked build never reaches the 24h tier"
    assert out.data_points[-1].buyback_amount == 20_000.0, "FMP's figure is kept, never zeroed"
    assert out.summary.buyback_status != "Low", "a top repurchaser is not demoted by an outage"
    assert any("[soc-buyback-check-unavailable]" in r.message and "SECUnavailableException" in r.message
               for r in caplog.records)
    kept, lost = _report_refuses(out)
    assert kept is None and lost, "the report must not freeze unchecked buybacks"


@pytest.mark.asyncio
async def test_a_hanging_sec_with_no_cache_is_bounded_and_degrades(sec, monkeypatch):
    sec.hang = True
    monkeypatch.setattr(sos, "_BUYBACK_CHECK_TIMEOUT_SECONDS", 0.05)
    svc = _wire(_FMP(cashflow=_intc_cf(), income=[_inc(d) for d in _QEND]))
    overrides, failed = await asyncio.wait_for(svc._verify_buybacks("INTC", _intc_cf(), {}), 2)
    assert overrides == {} and failed is True
    assert sec.cache_calls == [(CIK, tuple(SEC_BUYBACK_TAGS))], "the cache was asked first"


@pytest.mark.asyncio
async def test_a_slow_sec_falls_back_to_cached_filing_data(sec, monkeypatch):
    """SEC slow, not failed: the service's own stale fallback only runs after a fetch FAILS
    (a 20 s read timeout), past this 10 s bound — so the check asks the cache itself."""
    sec.hang, sec.cached = True, INTC_CONCEPTS
    monkeypatch.setattr(sos, "_BUYBACK_CHECK_TIMEOUT_SECONDS", 0.05)
    overrides, failed = await asyncio.wait_for(
        S.__new__(S)._verify_buybacks("INTC", _intc_cf(), {}), 2)
    assert overrides == {"2024-06-30": 0.0, "2025-06-30": 0.0} and failed is False


@pytest.mark.asyncio
async def test_a_failing_cache_read_after_a_timeout_degrades(sec, monkeypatch):
    sec.hang = True

    async def broken(cik, tags):
        raise RuntimeError("PGRST205")

    monkeypatch.setattr(sec, "get_cached_concepts", broken)
    monkeypatch.setattr(sos, "_BUYBACK_CHECK_TIMEOUT_SECONDS", 0.05)
    assert await S.__new__(S)._verify_buybacks("INTC", _intc_cf(), {}) == ({}, True)


@pytest.mark.asyncio
async def test_an_unexpected_error_degrades_instead_of_raising(sec, caplog):
    sec.raises = RuntimeError("boom")
    with caplog.at_level(logging.ERROR, logger=sos.__name__):
        overrides, failed = await S.__new__(S)._verify_buybacks("INTC", _intc_cf(), {})
    assert overrides == {} and failed is True
    assert any("[soc-buyback-check-error]" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_a_config_error_from_the_service_is_the_check_off_not_a_failure(sec):
    sec.raises = SECConfigException("no user agent")
    overrides, failed = await S.__new__(S)._verify_buybacks("INTC", _intc_cf(), {})
    assert overrides == {} and failed is False


@pytest.mark.asyncio
async def test_stale_cached_filing_data_still_corrects_and_is_not_degraded(sec):
    sec.stale = True
    overrides, failed = await S.__new__(S)._verify_buybacks("INTC", _intc_cf(), {})
    assert overrides == {"2024-06-30": 0.0, "2025-06-30": 0.0} and failed is False


@pytest.mark.asyncio
async def test_the_check_off_never_asks_sec(sec, monkeypatch, caplog):
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", "   ")
    monkeypatch.setattr(sos, "_buyback_check_off_logged", False)
    with caplog.at_level(logging.ERROR, logger=sos.__name__):
        first = await S.__new__(S)._verify_buybacks("INTC", _intc_cf(), {})
        second = await S.__new__(S)._verify_buybacks("INTC", _intc_cf(), {})
    assert first == second == ({}, False) and sec.calls == []
    assert sum("[soc-buyback-check-off]" in r.message for r in caplog.records) == 1


@pytest.mark.asyncio
async def test_no_cik_never_asks_sec(sec):
    assert await S.__new__(S)._verify_buybacks("X", _intc_cf(cik=None), {}) == ({}, False)
    assert await S.__new__(S)._verify_buybacks("X", _intc_cf(cik="n/a"), {"cik": None}) == ({}, False)
    assert sec.calls == []


@pytest.mark.asyncio
async def test_the_cik_falls_back_to_the_profile(sec):
    overrides, failed = await S.__new__(S)._verify_buybacks("INTC", _intc_cf(cik=None), {"cik": "50863"})
    assert sec.calls[0][0] == CIK and overrides and failed is False


@pytest.mark.asyncio
async def test_a_company_with_no_buyback_never_asks_sec(sec):
    rows = [{"date": d, "cik": CIK, "commonStockRepurchased": v}
            for d, v in zip(_QEND, [0, 5 * M, None, True, "n/a", float("nan")] * 4)]
    assert await S.__new__(S)._verify_buybacks("AMZN", rows, {}) == ({}, False)
    assert sec.calls == []


@pytest.mark.asyncio
async def test_a_foreign_filer_with_no_sec_concepts_keeps_fmp_without_degrading(sec):
    sec.answer = {}  # every concept 404s (an IFRS filer)
    overrides, failed = await S.__new__(S)._verify_buybacks("TSM", _intc_cf(), {})
    assert overrides == {} and failed is False


def test_the_payload_version_was_bumped_for_the_check():
    assert sos._PAYLOAD_VERSION == 15


def test_the_report_keeps_buyback_check_blocking():
    from app.services.agents.ticker_report_data_collector import _FROZEN_FINANCIALS_IGNORABLE

    assert "buyback_check" not in _FROZEN_FINANCIALS_IGNORABLE["signal_of_confidence"]


@pytest.mark.asyncio
@pytest.mark.parametrize("ua", ["Caydex — ops@example.com", "Caydex\nops@example.com"])
async def test_an_unsendable_user_agent_is_the_check_off_not_a_failure(sec, monkeypatch, ua):
    """An em dash or a newline cannot be sent as a header: the same steady state as a blank
    value (one ERROR per process), never a degraded build on every view."""
    monkeypatch.setattr(settings, "SEC_EDGAR_USER_AGENT", ua)
    monkeypatch.setattr(sos, "_buyback_check_off_logged", False)
    assert await S.__new__(S)._verify_buybacks("INTC", _intc_cf(), {}) == ({}, False)
    assert sec.calls == []



# ── Review round (2026-10-22) ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_data_that_ends_before_a_quarter_it_left_unchecked_degrades(sec, monkeypatch):
    """SEC failing, cached data through 2024-06-30 only: the 2025-06-30 withholding cannot
    be seen, so that quarter is an unchecked figure — a failed check, not a pass."""
    sec.stale = True
    sec.answer = {
        REPURCHASE_TAG: None,
        WITHHOLDING_TAG: _usd(("2024-01-01", "2024-06-30", 150 * M)),
    }
    overrides, failed = await S.__new__(S)._verify_buybacks("INTC", _intc_cf(), {})
    assert overrides == {"2024-06-30": 0.0} and failed is True
    out, writes = await _served(
        _wire(_FMP(cashflow=_intc_cf(), income=[_inc(d) for d in _QEND])), "INTC", monkeypatch)
    assert out.degraded == ["buyback_check"] and writes == []
    kept, lost = _report_refuses(out)
    assert kept is None and lost


@pytest.mark.asyncio
async def test_fresh_data_that_cannot_see_a_quarter_is_a_steady_state(sec):
    """The same gap with FRESH data is the filing's own state (a 10-Q not yet in SEC's
    data): kept, unverified, not degraded."""
    sec.answer = {REPURCHASE_TAG: None, WITHHOLDING_TAG: _usd(("2024-01-01", "2024-06-30", 150 * M))}
    assert await S.__new__(S)._verify_buybacks("INTC", _intc_cf(), {}) == ({"2024-06-30": 0.0}, False)


def _amzn_cf(buyback_positions):
    """20 rows newest first; buybacks only at the given positions (AMZN's last ones are
    Q1/Q2 2022, positions 16-17 of its live feed)."""
    days = list(reversed(_QEND))
    return [{"date": d, "cik": "1018724",
             "commonStockRepurchased": -3_000 * M if i in buyback_positions else 0}
            for i, d in enumerate(days)]


@pytest.mark.asyncio
async def test_a_buyback_older_than_the_chart_reads_never_asks_sec(sec, monkeypatch):
    sec.raises = SECUnavailableException("SEC down")
    rows = _amzn_cf({16, 17})
    assert await S.__new__(S)._verify_buybacks("AMZN", rows, {}) == ({}, False)
    assert sec.calls == []
    out, writes = await _served(_wire(_FMP(cashflow=rows, income=[_inc(d) for d in _QEND])),
                                "AMZN", monkeypatch)
    assert "buyback_check" not in out.degraded and writes


@pytest.mark.asyncio
async def test_a_buyback_inside_the_window_still_asks_sec(sec):
    sec.raises = SECUnavailableException("SEC down")
    rows = _amzn_cf({13})
    assert await S.__new__(S)._verify_buybacks("AMZN", rows, {}) == ({}, True)
    assert len(sec.calls) == 1


def test_the_window_covers_every_row_the_chart_reads():
    assert sos._BUYBACK_CHECK_WINDOW_ROWS == 8 + (sos._TRAILING_POINTS - 1) + 3
    rows = _amzn_cf(set()) + [{"date": _QEND[-1], "commonStockRepurchased": -1}]  # a duplicate day
    window = sos._rows_the_chart_reads(rows)
    assert [r["date"] for r in window] == sorted(_QEND, reverse=True)[:14]
    assert window[0]["commonStockRepurchased"] == 0, "first row of a day wins, like the chart"
