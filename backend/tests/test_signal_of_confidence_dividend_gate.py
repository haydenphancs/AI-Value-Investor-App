"""Signal of Confidence must not chart a dividend for a company that pays none.

TestFlight (build 1.0 (8)): PLUG — never a common payer, diluting 946M → 1.39B shares —
showed a 1.75% annualised dividend yield in Q2'26. The brief blamed a missing sign check,
but the live row reads ``commonDividendsPaid: -16,474,000`` with the outflow sign intact;
FMP's own per-share record (``ratios.dividendPerShare`` = 0 every year, profile
``lastDividend`` = 0) is what says "non-payer". The dividend CARD already gated on that
record; the BARS did not, so one screen said "no dividend" and "1.75%" at once.

These are math tests over plain dicts (`.claude/rules/testing.md` §1) plus one wiring
test that drives the real builder, because a mutation that stops threading the verdict
stays green under leaf-only tests.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from app.services import signal_of_confidence_service as sos
from app.services.signal_of_confidence_service import SignalOfConfidenceService as S

MCAP = {"2026-06-30": 2.5e9, "2026-03-31": 2.4e9}


def _svc() -> S:
    return S.__new__(S)


def _inc(date: str, period: str = "Q2", fy: str = "2026", shares: float = 1.39e9) -> dict:
    return {"date": date, "period": period, "fiscalYear": fy, "weightedAverageShsOut": shares}


def _cf(date: str, **fields) -> dict:
    return {"date": date, **fields}


def _points(cf: dict, pays=None, inc=None, mcap=None):
    return _svc()._build_data_points(
        [cf], [inc or _inc(cf["date"])], 2.5e9, mcap or MCAP, "PLUG", pays_common_dividend=pays,
    )


ALL_ZERO_RATIOS = [{"date": f"{y}-12-31", "dividendPerShare": 0} for y in (2021, 2022, 2023, 2024, 2025)]
PAYER_RATIOS = [{"date": "2024-09-28", "dividendPerShare": 0.98}, {"date": "2025-09-27", "dividendPerShare": 1.01}]


# ── the bars ─────────────────────────────────────────────────────────────────

def test_a_positive_dividend_line_is_not_a_dividend():
    pt = _points(_cf("2026-06-30", commonDividendsPaid=10_000_000))[0]
    assert pt.dividend_yield == 0.0 and pt.dividend_amount == 0.0


def test_preferred_only_net_dividends_is_not_a_common_dividend():
    # `netDividendsPaid` = common + preferred; with common present as 0 it must read 0.
    row = _cf("2026-06-30", commonDividendsPaid=0, preferredDividendsPaid=-9e6, netDividendsPaid=-9e6)
    pt = _points(row)[0]
    assert pt.dividend_yield == 0.0 and pt.dividend_amount == 0.0
    # …and with the common key ABSENT and an unknown payer verdict, the net fallback is withheld.
    row = _cf("2026-06-30", preferredDividendsPaid=-9e6, netDividendsPaid=-9e6)
    pt = _points(row, pays=None)[0]
    assert pt.dividend_yield == 0.0


def test_net_dividends_fallback_is_used_only_for_a_known_payer():
    row = _cf("2026-06-30", netDividendsPaid=-1.5e10)   # the live fixture shape in test_negative_earnings_display
    known = _points(row, pays=True, mcap={"2026-06-30": 3e12})[0]
    assert known.dividend_amount == 15000.0
    assert known.dividend_yield == round(1.5e10 / 3e12 * 100 * 4, 2)
    unknown = _points(row, pays=None, mcap={"2026-06-30": 3e12})[0]
    assert unknown.dividend_amount == 0.0 and unknown.dividend_yield == 0.0


def test_a_present_null_common_key_falls_back_like_an_absent_one():
    row = _cf("2026-06-30", commonDividendsPaid=None, dividendsPaid=-4e6)
    pt = _points(row, pays=True)[0]
    assert pt.dividend_amount == 4.0


def test_the_plug_case_a_negative_line_for_a_non_payer_is_zeroed():
    """The live PLUG Q2'26 row: outflow sign present, company has never paid."""
    row = _cf("2026-06-30", commonDividendsPaid=-16_474_000, netDividendsPaid=-16_474_000,
              preferredDividendsPaid=0, commonStockRepurchased=-122_000)
    pts = _points(row, pays=False)
    assert pts[0].dividend_yield == 0.0 and pts[0].dividend_amount == 0.0
    # the (real, tiny) buyback still charts
    assert pts[0].buyback_amount == 0.12 and pts[0].buyback_yield > 0
    summary = _svc()._build_summary(pts, 2.5e9)
    assert summary.dividend_yield == 0.0
    assert summary.total_yield == summary.buyback_yield


def test_a_real_payer_outflow_still_charts_at_scale():
    row = _cf("2026-06-30", commonDividendsPaid=-3.8e9)
    pt = _points(row, pays=True, mcap={"2026-06-30": 3e12})[0]
    assert pt.dividend_amount == 3800.0
    assert pt.dividend_yield == round(3.8e9 / 3e12 * 100 * 4, 2)


def test_buyback_sign_gate_is_unchanged():
    buy = _points(_cf("2026-06-30", commonStockRepurchased=-60_000))[0]
    assert buy.buyback_yield > 0
    issue = _points(_cf("2026-06-30", commonStockRepurchased=60_000))[0]
    assert issue.buyback_yield == 0.0 and issue.buyback_amount == 0.0


def test_nan_and_missing_cash_flow_fields_are_zero_not_nan():
    for row in (_cf("2026-06-30", commonDividendsPaid=float("nan")), _cf("2026-06-30"), _cf("2026-06-30", commonDividendsPaid="n/a")):
        pt = _points(row, pays=True)[0]
        for v in (pt.dividend_yield, pt.dividend_amount, pt.buyback_yield, pt.buyback_amount):
            assert math.isfinite(v) and v == 0.0


def test_a_payer_quarter_without_the_line_is_kept_and_zeroed_not_dropped():
    """Decision: keep the point (the shares line must stay continuous), chart 0.00%, warn."""
    inc = [_inc("2026-03-31", "Q1"), _inc("2026-06-30", "Q2")]
    cfs = [_cf("2026-03-31", commonDividendsPaid=-1e6), _cf("2026-06-30")]
    pts = _svc()._build_data_points(cfs, inc, 2.5e9, MCAP, "X", pays_common_dividend=True)
    assert [p.period for p in pts] == ["Q1 '26", "Q2 '26"]
    assert pts[0].dividend_amount == 1.0 and pts[1].dividend_amount == 0.0


def test_empty_inputs_produce_no_points():
    assert _svc()._build_data_points([], [], None, {}, "X", pays_common_dividend=False) == []
    assert _svc()._build_data_points([], [_inc("2026-06-30")], None, {}, "X") != []  # income alone still labels


# ── the verdict ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ratios, profile, exdiv, expected", [
    (ALL_ZERO_RATIOS, None, None, False),
    (ALL_ZERO_RATIOS, {"lastDividend": 0}, None, False),
    (ALL_ZERO_RATIOS, {"lastDividend": 0.25}, None, True),          # initiator this FY
    (ALL_ZERO_RATIOS, {"lastDividend": 0}, ["2026-05-08"], False),  # a derived date never overrides the record
    (PAYER_RATIOS, None, None, True),
    (PAYER_RATIOS, {"lastDividend": 0}, None, True),                # record wins over a stale profile
    ([], None, None, None),
    ([], {}, None, None),
    ([], {"lastDividend": 0}, None, False),                          # the profile's zero IS the record
    ([{"date": "2025-12-31", "someOtherKey": 1}], None, None, None),   # drift: key vanished → unknown, never False
    ([{"date": "2025-12-31", "someOtherKey": 1}], {"lastDividend": 0}, None, False),  # …unless the profile says zero
    ([], {"lastDiv": 0.5}, None, True),                             # legacy key
    ([], None, ["2026-05-08"], True),                                # last rung: a recent derived date
    ([], None, ["2023-05-08"], None),                                # …but not a stale one
    ([], None, ["garbage"], None),
    (None, None, None, None),
    ("not a list", {"lastDividend": "abc"}, None, None),
])
def test_pays_common_dividend_helper(ratios, profile, exdiv, expected):
    today = datetime(2026, 9, 17, tzinfo=timezone.utc)
    assert S._pays_common_dividend(ratios, profile, exdiv, today=today) is expected


def test_card_and_bars_share_one_gate():
    """The exact shape of the shipped defect: card None while the bars charted a yield."""
    svc = _svc()
    row = _cf("2026-06-30", commonDividendsPaid=-16_474_000)
    verdict = S._pays_common_dividend(ALL_ZERO_RATIOS, {"lastDividend": 0}, [])
    pts = svc._build_data_points([row], [_inc("2026-06-30")], 2.5e9, MCAP, "PLUG", pays_common_dividend=verdict)
    card = svc._build_dividend_info([], 1.75, 0.0, 0.0, data_points=pts, annual_ratios=ALL_ZERO_RATIOS,
                                    pays_common_dividend=verdict)
    assert card is None and all(p.dividend_yield == 0.0 for p in pts)


def test_card_without_an_explicit_verdict_derives_the_same_answer():
    svc = _svc()
    assert svc._build_dividend_info([], 1.75, 0.0, 0.0, data_points=[], annual_ratios=ALL_ZERO_RATIOS) is None
    assert svc._build_dividend_info([], 1.75, 0.0, 0.0, data_points=[], annual_ratios=PAYER_RATIOS) is not None


def test_payload_version_bumped_for_the_dividend_gate():
    assert sos._PAYLOAD_VERSION >= 4


# ── wiring: the real builder threads ONE verdict into bars and card ──────────

class _FMP:
    def __init__(self, *, cashflow, ratios, profile):
        self._cf, self._ratios, self._profile = cashflow, ratios, profile

    async def get_cash_flow_statement(self, ticker, period="quarter", limit=20):
        return self._cf

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return [_inc("2026-03-31", "Q1"), _inc("2026-06-30", "Q2")]

    async def get_financial_ratios(self, ticker, period="annual", limit=10):
        return self._ratios

    async def get_earning_calendar_full(self, ticker):
        return []

    async def get_historical_market_cap(self, ticker, from_date=None, to_date=None, limit=2000):
        return [{"date": "2026-03-31", "marketCap": 2.4e9}, {"date": "2026-06-30", "marketCap": 2.5e9}]

    async def get_company_profile(self, ticker):
        return self._profile

    async def get_stock_price_quote(self, ticker):
        return {"marketCap": 2.5e9, "price": 2.0}


class _CorporateActions:
    async def get_ex_dividend_dates(self, symbol, from_date=None, to_date=None):
        return []


def _wire(fmp) -> S:
    from tests._price_fakes import PriceFromFMPFake
    svc = _svc()
    svc.fmp = fmp
    svc.price = PriceFromFMPFake(fmp)
    svc.corporate_actions = _CorporateActions()
    return svc


PLUG_CF = [
    _cf("2026-03-31", commonDividendsPaid=0, commonStockRepurchased=0),
    _cf("2026-06-30", commonDividendsPaid=-16_474_000, netDividendsPaid=-16_474_000,
        preferredDividendsPaid=0, commonStockRepurchased=-122_000),
]
AAPL_CF = [
    _cf("2026-03-31", commonDividendsPaid=-3.7e9, commonStockRepurchased=-2.5e10),
    _cf("2026-06-30", commonDividendsPaid=-3.8e9, commonStockRepurchased=-2.1e10),
]


@pytest.mark.asyncio
async def test_the_builder_threads_one_payer_verdict_into_bars_and_card():
    plug = _wire(_FMP(cashflow=PLUG_CF, ratios=ALL_ZERO_RATIOS, profile=[{"lastDividend": 0}]))
    resp, _, degraded = await plug._build_signal_of_confidence("PLUG")
    assert degraded == []
    assert resp.data_points, "fixture must yield labelled quarters"
    assert all(p.dividend_yield == 0.0 and p.dividend_amount == 0.0 for p in resp.data_points)
    assert resp.summary.dividend_yield == 0.0
    assert resp.dividend_info is None
    assert any(p.buyback_amount > 0 for p in resp.data_points), "the real tiny buyback still charts"

    aapl = _wire(_FMP(cashflow=AAPL_CF, ratios=PAYER_RATIOS, profile=[{"lastDividend": 1.01}]))
    resp, _, _ = await aapl._build_signal_of_confidence("AAPL")
    assert all(p.dividend_yield > 0 for p in resp.data_points)
    assert resp.summary.dividend_yield > 0
    assert resp.dividend_info is not None


@pytest.mark.asyncio
async def test_a_failed_profile_fetch_degrades_to_the_record_alone():
    class _Boom(_FMP):
        async def get_company_profile(self, ticker):
            raise RuntimeError("profile down")

    svc = _wire(_Boom(cashflow=PLUG_CF, ratios=ALL_ZERO_RATIOS, profile=None))
    resp, _, degraded = await svc._build_signal_of_confidence("PLUG")
    assert all(p.dividend_yield == 0.0 for p in resp.data_points)   # the record still says non-payer
    assert resp.dividend_info is None
    assert degraded == ["profile"], "a failed profile is a DEGRADED build — never pinned for 24h"


@pytest.mark.asyncio
async def test_the_profile_alone_rescues_a_this_fy_initiator():
    """The per-share record has only completed fiscal years; a company that started paying
    THIS year reads all-zero there. `lastDividend` (the profile's TTM total) is the only
    live evidence — drop the profile from the verdict and this goes red."""
    class _Recording(_FMP):
        def __init__(self, *a, **k):
            super().__init__(*a, **k); self.profile_calls = 0
        async def get_company_profile(self, ticker):
            self.profile_calls += 1
            return await super().get_company_profile(ticker)

    fmp = _Recording(cashflow=AAPL_CF, ratios=ALL_ZERO_RATIOS, profile=[{"lastDividend": 0.25}])
    resp, _, degraded = await _wire(fmp)._build_signal_of_confidence("META")
    assert fmp.profile_calls == 1
    assert all(p.dividend_yield > 0 for p in resp.data_points)
    assert resp.dividend_info is not None
    assert degraded == []


@pytest.mark.asyncio
async def test_a_failed_ratios_fetch_keeps_plug_at_zero_and_marks_the_build_degraded():
    """One 429 on `ratios` used to send PLUG back to the cash-flow line (1.75%) AND pin it
    for 24h. Now the profile's zero is the record, and the build is not persisted."""
    class _RatiosDown(_FMP):
        async def get_financial_ratios(self, ticker, period="annual", limit=10):
            raise RuntimeError("429")

    fmp = _RatiosDown(cashflow=PLUG_CF, ratios=None, profile=[{"lastDividend": 0}])
    resp, _, degraded = await _wire(fmp)._build_signal_of_confidence("PLUG")
    assert all(p.dividend_yield == 0.0 for p in resp.data_points)
    assert degraded == ["annual_ratios"]


@pytest.mark.asyncio
async def test_get_signal_of_confidence_persists_a_healthy_build_but_not_a_degraded_one(monkeypatch):
    """The positive control proves the spy sees the background upsert; the negative half is
    the gate. Without the control a broken spy would make the gate look present."""
    import asyncio

    class _RatiosDown(_FMP):
        async def get_financial_ratios(self, ticker, period="annual", limit=10):
            raise RuntimeError("429")

    async def _run(svc, ticker):
        writes = []
        monkeypatch.setattr(svc, "_check_supabase_cache", lambda t: None)
        monkeypatch.setattr(svc, "_upsert_supabase_cache_safe", lambda *a, **k: writes.append(a))
        sos._cache.clear(); sos._inflight.clear()
        out = await svc.get_signal_of_confidence(ticker)
        for _ in range(50):                      # the upsert runs on a worker thread
            if writes:
                break
            await asyncio.sleep(0.01)
        return out, writes

    healthy = _wire(_FMP(cashflow=PLUG_CF, ratios=ALL_ZERO_RATIOS, profile=[{"lastDividend": 0}]))
    out, writes = await _run(healthy, "PLUG")
    assert out.data_points and writes, "control: a healthy build must reach the 24h tier"

    degraded = _wire(_RatiosDown(cashflow=PLUG_CF, ratios=None, profile=[{"lastDividend": 0}]))
    out, writes = await _run(degraded, "PLUG")
    assert out.data_points and all(p.dividend_yield == 0.0 for p in out.data_points)
    assert writes == [], "a build that ran without the per-share record was written to the 24h tier"
