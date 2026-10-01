"""Earnings round 2 (2026-09-30): a dropped-digit revenueActual with NO consensus to test.

R9. The first pass repaired the AVGO glitch (feed revenueActual 2,218,700,000 vs the filed
22,187,000,000) only when the feed also carried a revenueEstimated. Two doors stayed open,
and both CHARTED the 10x-off figure (iOS puts every actual into the axis domain, so one
$2.2B dot among ~$22B quarters drags the axis to 0) and fed it to the report and Cay AI:

  (a) the just-reported quarter (Phase A2) has no filing yet, so a >50% consensus miss was
      kept as the actual with only the surprise removed;
  (b) a matched quarter whose feed row has a null revenueEstimated returned the feed value
      before the feed-vs-filed check ever ran, ignoring the filed figure on the same row.

Now: (b) a feed/filed ratio outside [0.2, 5] — far beyond a bank's net-vs-gross gap — lets
the filing win even with no estimate, and with no feed consensus the analyst revenueAvg
decides a smaller (>25%) disagreement (owner decision 2: the one closer to consensus);
(a) an A2 revenue more than 50% off consensus is OMITTED, never charted and never shown as
pending (iOS would read a nil actual as an upcoming quarter).
Hermetic: the FMP client is faked.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest

from app.services.earnings_service import EarningsService, _reconcile_revenue


class _FakeFMP:
    def __init__(self, income, estimates=None, feed=None):
        self.income, self.estimates, self.feed = income, estimates or [], feed or []

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return self.income

    async def get_analyst_estimates(self, ticker, period="quarter", limit=20):
        return self.estimates

    async def get_historical_prices(self, ticker, from_date=None, to_date=None):
        return []

    async def get_earning_calendar_full(self, ticker, raise_errors=False):
        return self.feed


def _svc(fake) -> EarningsService:
    svc = EarningsService.__new__(EarningsService)
    svc.fmp = fake
    svc.supabase = None
    return svc


def _q(quarters, fiscal_date):
    return next((q for q in quarters if q.fiscal_date == fiscal_date), None)


def _rr(*args):
    return _reconcile_revenue(*args, ticker="T", fiscal_key="2026-05-03")


# ── (b) matched quarter, no feed consensus ──────────────────────────────────


def test_dropped_digit_with_no_estimate_takes_the_filing(caplog):
    with caplog.at_level(logging.WARNING):
        r = _reconcile_revenue(2.2187e9, None, 22.187e9, True, None,
                               ticker="AVGO", fiscal_key="2026-05-03")
    assert r.actual == pytest.approx(22.187e9)
    assert r.surprise is None and r.has_estimate is False
    assert "AVGO" in caplog.text and "2218700000" in caplog.text


def test_dropped_digit_with_no_feed_estimate_compares_the_filing_to_the_analyst_avg():
    """The feed figure is discarded, so the quarter is treated exactly like one with no
    feed revenue: filed USD revenue vs the analyst revenueAvg, within the ±50% guard."""
    r = _rr(2.2187e9, None, 22.187e9, True, 22.1303e9)
    assert r.actual == pytest.approx(22.187e9)
    assert r.estimate == pytest.approx(22.1303e9)
    assert r.surprise == pytest.approx(0.26) and r.has_estimate is True


def test_extra_digit_with_no_estimate_takes_the_filing():
    r = _rr(221.87e9, None, 22.187e9, True, None)
    assert r.actual == pytest.approx(22.187e9) and r.has_estimate is False


@pytest.mark.parametrize("feed", [0.0, -5e8])
def test_zero_or_negative_feed_against_a_positive_filing_takes_the_filing(feed):
    r = _rr(feed, None, 22.187e9, True, None)
    assert r.actual == pytest.approx(22.187e9)


def test_bank_net_vs_gross_with_no_estimate_keeps_the_feed():
    """A small bank's gross revenue can exceed 2.2x its net: inside [0.2, 5], so the
    feed's net figure stands (no consensus to pick by)."""
    r = _rr(46e9, None, 101e9, True, None)
    assert r.actual == pytest.approx(46e9)
    assert r.surprise is None and r.has_estimate is False


def test_ratio_band_edges_are_inclusive():
    assert _rr(20.0, None, 100.0, True, None).actual == pytest.approx(20.0)     # 0.2
    assert _rr(19.9, None, 100.0, True, None).actual == pytest.approx(100.0)
    assert _rr(500.0, None, 100.0, True, None).actual == pytest.approx(500.0)   # 5.0
    assert _rr(501.0, None, 100.0, True, None).actual == pytest.approx(100.0)


def test_no_feed_estimate_the_analyst_avg_picks_the_closer_figure():
    # Bank: the analyst consensus is NET revenue → the feed's net figure stays.
    r = _rr(46e9, None, 70e9, True, 45e9)
    assert r.actual == pytest.approx(46e9) and r.has_estimate is False
    # The filing is the one near consensus → it wins, compared like a feed-less quarter.
    r = _rr(46e9, None, 70e9, True, 69e9)
    assert r.actual == pytest.approx(70e9)
    assert r.surprise == pytest.approx(1.45) and r.has_estimate is True
    # Neither within 50% of the analyst consensus → decision 2: keep the feed value.
    r = _rr(46e9, None, 70e9, True, 200e9)
    assert r.actual == pytest.approx(46e9) and r.has_estimate is False


def test_non_usd_filing_never_replaces_a_feed_figure_without_estimate():
    r = _rr(2.2e9, None, 700e9, False, None)
    assert r.actual == pytest.approx(2.2e9) and r.has_estimate is False


def test_agreeing_figures_with_no_estimate_are_unchanged():
    r = _rr(22.0e9, None, 22.187e9, True, 22.13e9)
    assert r.actual == pytest.approx(22.0e9)
    assert r.surprise is None and r.has_estimate is False


@pytest.mark.asyncio
async def test_matched_quarter_with_null_revenue_estimate_uses_the_filing_end_to_end():
    income = [
        {"date": "2026-02-01", "period": "Q1", "fiscalYear": 2026, "revenue": 19.311e9,
         "epsDiluted": 1.14, "reportedCurrency": "USD"},
        {"date": "2026-05-03", "period": "Q2", "fiscalYear": 2026, "revenue": 22.187e9,
         "epsDiluted": 1.03, "reportedCurrency": "USD"},
    ]
    estimates = [{"date": "2026-02-01", "epsAvg": 1.53, "revenueAvg": 19.3e9},
                 {"date": "2026-05-03", "epsAvg": 1.57, "revenueAvg": 22.1303e9}]
    feed = [
        {"date": "2026-03-05", "epsActual": 1.60, "epsEstimated": 1.53,
         "revenueActual": 19.311e9, "revenueEstimated": 19.3e9},
        {"date": "2026-06-03", "epsActual": 1.58, "epsEstimated": 1.57,
         "revenueActual": 2.2187e9, "revenueEstimated": None},
    ]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("AVGO")
    q2 = _q(resp.revenue_quarters, "2026-05-03")
    assert q2.actual_value == pytest.approx(22.187e9)
    assert q2.surprise_percent == pytest.approx(0.26)
    assert resp.degraded == []


# ── (a) just-reported quarter (A2), no filing yet ───────────────────────────

_TODAY = datetime.now(timezone.utc).date()


def _d(days: int) -> str:
    return (_TODAY + timedelta(days=days)).strftime("%Y-%m-%d")


def _a2_fixture(revenue_actual, revenue_estimated=1.3e10):
    d0, d1, d2 = _d(-130), _d(-39), _d(52)
    income = [
        {"date": _d(-221), "period": "Q1", "fiscalYear": 2026, "epsDiluted": 1.0,
         "revenue": 1.1e10, "reportedCurrency": "USD"},
        {"date": d0, "period": "Q2", "fiscalYear": 2026, "epsDiluted": 1.5,
         "revenue": 1.2e10, "reportedCurrency": "USD"},
    ]
    estimates = [{"date": d0, "epsAvg": 1.5, "revenueAvg": 1.2e10},
                 {"date": d1, "epsAvg": 1.70, "revenueAvg": 1.3e10},
                 {"date": d2, "epsAvg": 1.80, "revenueAvg": 1.4e10}]
    feed = [
        {"date": _d(-190), "epsActual": 1.4, "epsEstimated": 1.38,
         "revenueActual": 1.1e10, "revenueEstimated": 1.1e10},
        {"date": _d(-100), "epsActual": 1.55, "epsEstimated": 1.5,
         "revenueActual": 1.2e10, "revenueEstimated": 1.19e10},
        {"date": _d(-9), "epsActual": 1.75, "epsEstimated": 1.70,
         "revenueActual": revenue_actual, "revenueEstimated": revenue_estimated},
    ]
    return income, estimates, feed, d1


@pytest.mark.parametrize("bad_actual", [1.35e9, 1.35e11])
@pytest.mark.asyncio
async def test_a2_revenue_far_off_consensus_is_omitted_not_charted(caplog, bad_actual):
    income, estimates, feed, d1 = _a2_fixture(bad_actual)
    with caplog.at_level(logging.WARNING):
        resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")
    assert _q(resp.revenue_quarters, d1) is None
    # The EPS half of the announcement still shows, and no pending duplicate appears.
    assert _q(resp.eps_quarters, d1).actual_value == pytest.approx(1.75)
    assert [q.fiscal_date for q in resp.revenue_quarters].count(d1) == 0
    assert "XYZ" in caplog.text and d1 in caplog.text
    assert str(int(bad_actual)) in caplog.text
    assert resp.degraded == []


@pytest.mark.asyncio
async def test_a2_revenue_with_no_feed_estimate_uses_the_analyst_avg_to_omit():
    income, estimates, feed, d1 = _a2_fixture(1.35e9, revenue_estimated=None)
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")
    assert _q(resp.revenue_quarters, d1) is None


@pytest.mark.asyncio
async def test_a2_plausible_revenue_is_kept_with_its_surprise():
    income, estimates, feed, d1 = _a2_fixture(1.35e10)
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")
    r1 = _q(resp.revenue_quarters, d1)
    assert r1.actual_value == pytest.approx(1.35e10)
    assert r1.surprise_percent == pytest.approx(3.85) and r1.has_estimate is True


@pytest.mark.asyncio
async def test_a2_revenue_with_no_consensus_at_all_is_kept_without_surprise():
    income, estimates, feed, d1 = _a2_fixture(1.35e10, revenue_estimated=None)
    estimates = [{k: v for k, v in e.items() if k != "revenueAvg"} for e in estimates]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")
    r1 = _q(resp.revenue_quarters, d1)
    assert r1.actual_value == pytest.approx(1.35e10)
    assert r1.surprise_percent is None and r1.has_estimate is False


def test_matched_branch_keeps_its_uncorroborated_value_without_the_omit_flag():
    """Only A2 omits: a matched quarter with a non-USD filing keeps the feed value with no
    surprise (pinned by test_earnings_deepcheck's TWD case)."""
    r = _rr(2.2e9, 22e9, 700e9, False, None)
    assert r.actual == pytest.approx(2.2e9) and r.has_estimate is False


@pytest.mark.asyncio
async def test_a2_glitched_feed_consensus_keeps_an_actual_the_analyst_avg_corroborates():
    """Over-omit twin: the ESTIMATE carries the dropped digit (1.3e9) while the actual
    (1.35e10) matches the analyst revenueAvg (1.3e10) — kept, with no surprise."""
    income, estimates, feed, d1 = _a2_fixture(1.35e10, revenue_estimated=1.3e9)
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")
    r1 = _q(resp.revenue_quarters, d1)
    assert r1 is not None and r1.actual_value == pytest.approx(1.35e10)
    assert r1.surprise_percent is None and r1.has_estimate is False
