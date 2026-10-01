"""Earnings deep-check (2026-09-30): revenue plausibility, has_estimate, EPS fallbacks.

Trigger: TestFlight 1.0(9), AVGO → Financials → Earnings (Revenue). The per-symbol
earnings feed served Q2 FY26 revenueActual 2,218,700,000 — a dropped digit of the FILED
22,187,000,000 — against a 22,130,300,000 consensus. A +0.26% beat rendered as a -89.97%
miss whose bar ran through half the screen, and the payload was frozen in both cache
tiers. The filed income statement for the same quarter was in hand the whole time.

Owner decision 2: feed vs filed disagree by >25% → use whichever is closer to the
consensus; if neither is within 50% keep the feed value with no surprise. Banks are why
the filing cannot simply win: their feed revenue is NET revenue and agrees with its own
consensus while the income statement's gross figure does not.

Every case asserts the CORRECT DEGRADED behaviour (no surprise, has_estimate False), never
a wrong number. Hermetic: the FMP client is faked; nothing touches the network.
"""

from __future__ import annotations

import logging

import pytest

from app.services.earnings_service import (
    EarningsService,
    _reconcile_revenue,
)


class _FakeFMP:
    def __init__(self, income, estimates=None, feed=None, prices=None):
        self.income = income
        self.estimates = estimates or []
        self.feed = feed or []
        self.prices = prices or []

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return self.income

    async def get_analyst_estimates(self, ticker, period="quarter", limit=20):
        return self.estimates

    async def get_historical_prices(self, ticker, from_date=None, to_date=None):
        return self.prices

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        return self.feed

    async def _make_request(self, endpoint, params=None):
        raise AssertionError(f"no raw FMP call expected, got {endpoint}")


def _svc(fake) -> EarningsService:
    svc = EarningsService.__new__(EarningsService)
    svc.fmp = fake
    svc.supabase = None
    return svc


def _income(date_, period, fy, revenue, eps=1.0, currency="USD"):
    row = {"date": date_, "period": period, "fiscalYear": fy, "revenue": revenue,
           "epsDiluted": eps, "eps": eps}
    if currency is not None:
        row["reportedCurrency"] = currency
    return row


def _q(quarters, fiscal_date):
    return next((q for q in quarters if q.fiscal_date == fiscal_date), None)


# ── The AVGO fixture ─────────────────────────────────────────────────────────

_AVGO_INCOME = [
    _income("2026-02-01", "Q1", 2026, 19_311_000_000, eps=1.14),
    _income("2026-05-03", "Q2", 2026, 22_187_000_000, eps=1.03),
]
_AVGO_ESTIMATES = [
    {"date": "2026-02-01", "epsAvg": 1.53, "revenueAvg": 19_300_000_000},
    {"date": "2026-05-03", "epsAvg": 1.57, "revenueAvg": 22_130_300_000},
    {"date": "2026-08-02", "epsAvg": 1.70, "revenueAvg": 24_000_000_000},
]


def _avgo_feed(q2_revenue_actual=2_218_700_000, q2_revenue_est=22_130_300_000):
    return [
        {"date": "2026-03-05", "epsActual": 1.60, "epsEstimated": 1.53,
         "revenueActual": 19_311_000_000, "revenueEstimated": 19_300_000_000},
        # The glitched row: revenueActual carries a dropped digit.
        {"date": "2026-06-03", "epsActual": 1.58, "epsEstimated": 1.57,
         "revenueActual": q2_revenue_actual, "revenueEstimated": q2_revenue_est},
    ]


@pytest.mark.asyncio
async def test_avgo_dropped_digit_revenue_uses_the_filed_figure(caplog):
    svc = _svc(_FakeFMP(_AVGO_INCOME, _AVGO_ESTIMATES, _avgo_feed()))
    with caplog.at_level(logging.WARNING):
        resp = await svc._build_earnings("AVGO")

    q2 = _q(resp.revenue_quarters, "2026-05-03")
    assert q2 is not None
    assert q2.quarter == "Q2 '26"
    assert q2.actual_value == pytest.approx(22_187_000_000)   # the filing, not 2.2187e9
    assert q2.estimate_value == pytest.approx(22_130_300_000)
    assert q2.surprise_percent is not None and abs(q2.surprise_percent) < 1
    assert q2.has_estimate is True
    # The intervention is greppable, with every number.
    assert "AVGO" in caplog.text and "2218700000" in caplog.text.replace(",", "")
    # EPS is untouched: the feed's adjusted 1.58 vs 1.57.
    eq2 = _q(resp.eps_quarters, "2026-05-03")
    assert eq2.actual_value == pytest.approx(1.58)
    assert eq2.surprise_percent == pytest.approx(0.64)
    assert eq2.has_estimate is True
    # A repair is not a PARTIAL build: the corrected payload may be cached.
    assert resp.degraded == []


def test_bank_twin_keeps_the_feed_value_and_its_surprise():
    """Bank: feed NET revenue 46e9 vs its own consensus 45e9; the income statement's gross
    revenue is 70e9. Disagreement >25%, but the feed is the one close to consensus."""
    r = _reconcile_revenue(46e9, 45e9, 70e9, True, None, ticker="JPM", fiscal_key="2026-06-30")
    assert r.actual == pytest.approx(46e9)
    assert r.estimate == pytest.approx(45e9)
    assert r.surprise == pytest.approx(2.22)
    assert r.has_estimate is True


def test_bank_with_null_feed_actual_gets_no_cross_source_surprise():
    """revenueActual null → the filed GROSS revenue against a NET-revenue consensus was a
    fake +55% beat with no bad vendor data at all."""
    r = _reconcile_revenue(None, 45e9, 70e9, True, None, ticker="JPM", fiscal_key="2026-06-30")
    assert r.actual == pytest.approx(70e9)
    assert r.surprise is None
    assert r.has_estimate is False
    assert r.estimate == r.actual       # no estimate dot below/above the actual


def test_estimate_glitch_gives_no_surprise():
    """The ESTIMATE carries the dropped digit: actual and filing agree, so the ratio check
    alone never fires and a +902% beat shipped. The analyst revenueAvg exposes it."""
    r = _reconcile_revenue(
        22_187_000_000, 2_213_030_000, 22_187_000_000, True, 22_130_300_000,
        ticker="AVGO", fiscal_key="2026-05-03",
    )
    assert r.actual == pytest.approx(22_187_000_000)
    assert r.surprise is None
    assert r.has_estimate is False


@pytest.mark.asyncio
async def test_estimate_glitch_end_to_end():
    svc = _svc(_FakeFMP(
        _AVGO_INCOME, _AVGO_ESTIMATES,
        _avgo_feed(q2_revenue_actual=22_187_000_000, q2_revenue_est=2_213_030_000),
    ))
    resp = await svc._build_earnings("AVGO")
    q2 = _q(resp.revenue_quarters, "2026-05-03")
    assert q2.actual_value == pytest.approx(22_187_000_000)
    assert q2.surprise_percent is None
    assert q2.has_estimate is False


def test_genuine_big_beat_with_both_sources_agreeing_is_kept():
    """Not over-blocking: a real +100% revenue beat (filing agrees, consensus agrees with
    the analyst feed) is a result, not a glitch."""
    r = _reconcile_revenue(30e9, 15e9, 30.1e9, True, 15.2e9, ticker="X", fiscal_key="2026-03-31")
    assert r.actual == pytest.approx(30e9)
    assert r.surprise == pytest.approx(100.0)
    assert r.has_estimate is True


def test_genuine_big_miss_with_filing_agreeing_is_kept():
    r = _reconcile_revenue(6e9, 15e9, 6e9, True, 15e9, ticker="X", fiscal_key="2026-03-31")
    assert r.surprise == pytest.approx(-60.0)
    assert r.has_estimate is True


def test_big_surprise_without_analyst_avg_is_kept_when_filing_corroborates():
    """The estimate cross-check nulls only on POSITIVE evidence (a >3x disagreement with
    revenueAvg). With no revenueAvg, a filing-corroborated biotech-style beat stands."""
    r = _reconcile_revenue(20e6, 5e6, 20e6, True, None, ticker="BIO", fiscal_key="2026-03-31")
    assert r.surprise == pytest.approx(300.0)
    assert r.has_estimate is True


def test_neither_source_within_fifty_percent_keeps_feed_without_surprise():
    r = _reconcile_revenue(2.2e9, 22e9, 50e9, True, None, ticker="X", fiscal_key="2026-03-31")
    assert r.actual == pytest.approx(2.2e9)     # decision 2: the feed value is kept
    assert r.surprise is None
    assert r.has_estimate is False


def test_disagreement_boundary_is_not_flagged():
    """Exactly 25% apart is not a disagreement (strict >), so the feed value stands."""
    r = _reconcile_revenue(125.0, 120.0, 100.0, True, None, ticker="X", fiscal_key="k")
    assert r.actual == pytest.approx(125.0)
    assert r.surprise == pytest.approx(4.17)


@pytest.mark.asyncio
async def test_non_usd_statement_never_substitutes_or_cross_compares():
    """A TWD filer: the filed revenue is a different UNIT. It must never replace the feed
    figure (that would be a +3000% "beat") and never be compared with a USD consensus."""
    income = [_income("2026-03-31", "Q1", 2026, 700_000_000_000, currency="TWD")]
    estimates = [{"date": "2026-03-31", "epsAvg": 1.0, "revenueAvg": 22_000_000_000}]

    # (a) AVGO-shaped feed glitch with a TWD filing: no corroboration → no surprise.
    feed = [{"date": "2026-04-17", "epsActual": 1.1, "epsEstimated": 1.0,
             "revenueActual": 2_200_000_000, "revenueEstimated": 22_000_000_000}]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("TSM")
    q = _q(resp.revenue_quarters, "2026-03-31")
    assert q.actual_value == pytest.approx(2_200_000_000)
    assert q.surprise_percent is None and q.has_estimate is False

    # (b) Cross-source fallback (feed revenueActual null): no TWD-vs-USD surprise.
    feed_b = [{"date": "2026-04-17", "epsActual": 1.1, "epsEstimated": 1.0,
               "revenueActual": None, "revenueEstimated": 22_000_000_000}]
    resp = await _svc(_FakeFMP(income, estimates, feed_b))._build_earnings("TSM")
    q = _q(resp.revenue_quarters, "2026-03-31")
    assert q.surprise_percent is None and q.has_estimate is False

    # (c) No announcement at all: filed TWD vs analyst USD revenueAvg — no surprise.
    resp = await _svc(_FakeFMP(income, estimates, []))._build_earnings("TSM")
    q = _q(resp.revenue_quarters, "2026-03-31")
    assert q.surprise_percent is None and q.has_estimate is False

    # (e) A EUR filer, where FX puts the filed figure INSIDE the ±50% band — so only the
    # currency check (not the band) stands between it and a wrong-unit number.
    eur_income = [_income("2026-03-31", "Q1", 2026, 10_000_000_000, currency="EUR")]
    eur_est = [{"date": "2026-03-31", "epsAvg": 1.0, "revenueAvg": 10_800_000_000}]
    #   cross-source: 10e9 EUR vs a 10.8e9 USD consensus is not a -7.4% miss
    feed_e = [{"date": "2026-04-17", "epsActual": 1.1, "epsEstimated": 1.0,
               "revenueActual": None, "revenueEstimated": 10_800_000_000}]
    resp = await _svc(_FakeFMP(eur_income, eur_est, feed_e))._build_earnings("SAP")
    q = _q(resp.revenue_quarters, "2026-03-31")
    assert q.surprise_percent is None and q.has_estimate is False
    #   reconciliation: a glitched USD feed figure is never "repaired" with EUR revenue
    feed_f = [{"date": "2026-04-17", "epsActual": 1.1, "epsEstimated": 1.0,
               "revenueActual": 1_080_000_000, "revenueEstimated": 10_800_000_000}]
    resp = await _svc(_FakeFMP(eur_income, eur_est, feed_f))._build_earnings("SAP")
    q = _q(resp.revenue_quarters, "2026-03-31")
    assert q.actual_value == pytest.approx(1_080_000_000)   # not the EUR 10e9
    assert q.surprise_percent is None and q.has_estimate is False

    # (d) Sanity twin: a plausible feed pair from a TWD filer keeps its surprise.
    feed_d = [{"date": "2026-04-17", "epsActual": 1.1, "epsEstimated": 1.0,
               "revenueActual": 23_000_000_000, "revenueEstimated": 22_000_000_000}]
    resp = await _svc(_FakeFMP(income, estimates, feed_d))._build_earnings("TSM")
    q = _q(resp.revenue_quarters, "2026-03-31")
    assert q.surprise_percent == pytest.approx(4.55) and q.has_estimate is True


@pytest.mark.asyncio
async def test_no_match_cross_source_within_band_keeps_surprise():
    """USD filer, no announcement: filed revenue vs analyst revenueAvg within ±50% is a
    usable surprise (same basis for a non-bank)."""
    income = [_income("2020-03-31", "Q1", 2020, 10_500_000_000)]
    estimates = [{"date": "2020-03-31", "epsAvg": 1.0, "revenueAvg": 10_000_000_000}]
    resp = await _svc(_FakeFMP(income, estimates, []))._build_earnings("OLD")
    q = _q(resp.revenue_quarters, "2020-03-31")
    assert q.surprise_percent == pytest.approx(5.0)
    assert q.has_estimate is True


# ── has_estimate on every branch (#26 / #30 / #60) ──────────────────────────


@pytest.mark.asyncio
async def test_no_estimate_quarters_are_flagged_not_matched():
    """An announcement with actuals but NO estimates used to ship estimate == actual, which
    iOS drew as a green 'matched consensus exactly' dot for a consensus that never existed."""
    income = [_income("2026-03-31", "Q1", 2026, 1_000_000)]
    feed = [{"date": "2026-04-28", "epsActual": 0.42, "epsEstimated": None,
             "revenueActual": 1_000_000, "revenueEstimated": None}]
    resp = await _svc(_FakeFMP(income, [], feed))._build_earnings("SMOL")
    e = _q(resp.eps_quarters, "2026-03-31")
    r = _q(resp.revenue_quarters, "2026-03-31")
    for q in (e, r):
        assert q.has_estimate is False
        assert q.surprise_percent is None
        assert q.estimate_value == q.actual_value


@pytest.mark.asyncio
async def test_no_match_and_no_analyst_estimate_is_flagged():
    income = [_income("2019-03-31", "Q1", 2019, 1_000_000, eps=0.2)]
    resp = await _svc(_FakeFMP(income, [], []))._build_earnings("SMOL")
    assert _q(resp.eps_quarters, "2019-03-31").has_estimate is False
    assert _q(resp.revenue_quarters, "2019-03-31").has_estimate is False


@pytest.mark.asyncio
async def test_zero_estimate_is_a_real_estimate():
    """estimate 0 → no surprise % (undefined), but a consensus DID exist: has_estimate
    stays True so iOS keeps classifying beat/miss by sign."""
    income = [_income("2026-03-31", "Q1", 2026, 1_000_000)]
    feed = [{"date": "2026-04-28", "epsActual": 0.05, "epsEstimated": 0.0,
             "revenueActual": 1_000_000, "revenueEstimated": 1_000_000}]
    resp = await _svc(_FakeFMP(income, [], feed))._build_earnings("ZERO")
    e = _q(resp.eps_quarters, "2026-03-31")
    assert e.surprise_percent is None
    assert e.has_estimate is True
    assert e.estimate_value == 0.0


@pytest.mark.asyncio
async def test_matched_normal_and_future_quarters_have_estimates():
    resp = await _svc(_FakeFMP(_AVGO_INCOME, _AVGO_ESTIMATES, _avgo_feed()))._build_earnings("AVGO")
    assert all(q.has_estimate is not None for q in resp.eps_quarters + resp.revenue_quarters)
    future = [q for q in resp.eps_quarters if q.actual_value is None]
    assert future and all(q.has_estimate is True for q in future)


# ── EPS GAAP fallbacks carry no surprise (#23) ──────────────────────────────


@pytest.mark.asyncio
async def test_feed_outage_shape_never_shows_gaap_vs_adjusted_misses():
    """All quarters without an announcement (what a swallowed feed 429 produced): GAAP
    epsDiluted 1.03 vs adjusted epsAvg 1.57 used to be a run of -34% "misses" counted in
    the report's beat/miss record. Now: the GAAP actual, no surprise, no consensus."""
    income = [_income("2026-02-01", "Q1", 2026, 19e9, eps=0.9),
              _income("2026-05-03", "Q2", 2026, 22e9, eps=1.03)]
    estimates = [{"date": "2026-02-01", "epsAvg": 1.6, "revenueAvg": 19e9},
                 {"date": "2026-05-03", "epsAvg": 1.57, "revenueAvg": 22e9}]
    resp = await _svc(_FakeFMP(income, estimates, []))._build_earnings("AVGO")
    reported = [q for q in resp.eps_quarters if q.actual_value is not None]
    assert len(reported) == 2
    for q in reported:
        assert q.surprise_percent is None
        assert q.has_estimate is False
        assert q.estimate_value == q.actual_value


# ── Malformed rows never 502 the section (#93) ───────────────────────────────


@pytest.mark.asyncio
async def test_non_dict_price_rows_are_skipped():
    prices = ["junk", None, 7, {"date": "2026-05-01", "close": 400.0},
              {"date": "2026-06-01", "close": float("nan")}]
    svc = _svc(_FakeFMP(_AVGO_INCOME, _AVGO_ESTIMATES, _avgo_feed(), prices=prices))
    resp = await svc._build_earnings("AVGO")
    assert [p.price for p in resp.price_history] == [400.0]       # Q2 '26 within ±5d
    assert [p.price for p in resp.daily_price_history] == [400.0]  # NaN skipped
    assert resp.degraded == []   # malformed rows are skipped, not a failed leg
