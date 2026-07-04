"""EPS beat/miss surprise must use the ANNOUNCED non-GAAP actual vs estimate for
every quarter — including a fiscal-Q4 whose 10-K is filed well after the earnings
release.

Regression (Oracle FY26 Q4): announced 2.11 vs 1.96 = a +8% BEAT on 2026-06-10,
but its 10-K was accepted 2026-06-22 (12 days later). The old code (a) fetched the
earnings-calendar only in a ``[acceptedDate-10, acceptedDate+1]`` window that
MISSED the 06-10 announcement, and (b) matched with a 10-day tolerance — so Q4 fell
through to the GAAP income-statement EPS (1.45) compared against the non-GAAP
consensus (1.96) = a bogus -26% "miss". The fix pulls the per-symbol
``/stable/earnings`` feed (all announcements, paired) and matches each quarter to
the FIRST announcement after its fiscal period-end (``_match_announcement``,
``max_days=80``) instead of by filing-date proximity.

Offline: the FMP client is faked; no live calls (see tests/testing rules).
"""
from __future__ import annotations

import pytest

from app.services.earnings_service import (
    EarningsService,
    _compute_surprise,
    _match_announcement,
)


class _FakeFMP:
    """Minimal FMP stand-in. The WINDOWED earnings-calendar (`_make_request`)
    returns nothing — the exact blind spot for a lagging 10-K — so the per-symbol
    `get_earning_calendar_full` feed must carry the Q4 announcement."""

    def __init__(self, income, estimates, full_ec):
        self._income = income
        self._estimates = estimates
        self._full_ec = full_ec

    async def get_income_statement(self, ticker, period="quarter", limit=20):
        return self._income

    async def get_analyst_estimates(self, ticker, period="quarter", limit=20):
        return self._estimates

    async def get_historical_prices(self, ticker, from_date=None, to_date=None):
        return []

    async def get_earning_calendar_full(self, ticker):
        return self._full_ec

    async def _make_request(self, endpoint, params=None):
        return []  # windowed earnings-calendar finds nothing (the lagging-10-K blind spot)


def _income_q(date_, period, fy, eps_diluted, accepted, revenue):
    return {
        "date": date_, "period": period, "fiscalYear": fy,
        "epsDiluted": eps_diluted, "eps": eps_diluted,
        "acceptedDate": accepted, "filingDate": accepted, "revenue": revenue,
    }


@pytest.mark.asyncio
async def test_fiscal_q4_uses_announced_nongaap_beat_not_gaap_miss():
    # Oracle-shaped: Q4 FY26 GAAP epsDiluted 1.45, 10-K accepted 2026-06-22 — 12
    # days after the 2026-06-10 announcement (2.11 actual / 1.96 estimate).
    income = [
        _income_q("2026-02-28", "Q3", 2026, 1.27, "2026-03-11", 17_190_000_000),
        _income_q("2026-05-31", "Q4", 2026, 1.45, "2026-06-22", 19_184_000_000),
    ]
    estimates = [  # non-GAAP consensus keyed by fiscal period-end (the fallback's estimate)
        {"date": "2026-02-28", "epsAvg": 1.70, "revenueAvg": 16_925_000_000},
        {"date": "2026-05-31", "epsAvg": 1.96, "revenueAvg": 19_100_000_000},
    ]
    full_ec = [  # per-symbol /stable/earnings — the authoritative paired announcements
        {"date": "2026-03-10", "epsActual": 1.79, "epsEstimated": 1.70,
         "revenueActual": 17_190_000_000, "revenueEstimated": 16_925_000_000},
        {"date": "2026-06-10", "epsActual": 2.11, "epsEstimated": 1.96,
         "revenueActual": 19_184_000_000, "revenueEstimated": 19_100_000_000},
    ]

    svc = EarningsService()
    svc.fmp = _FakeFMP(income, estimates, full_ec)
    resp = await svc._build_earnings("ORCL")

    q4 = next(q for q in resp.eps_quarters if getattr(q, "fiscal_date", None) == "2026-05-31")
    # Uses the ANNOUNCED non-GAAP actual (2.11), NOT the GAAP epsDiluted (1.45).
    assert q4.actual_value == pytest.approx(2.11)
    assert q4.estimate_value == pytest.approx(1.96)
    # A BEAT (~+7.65%), not the -26% GAAP-vs-non-GAAP mismatch.
    assert q4.surprise_percent is not None and q4.surprise_percent > 0


def test_match_announcement_pairs_period_end_first_after_no_cross_quarter():
    """Period-end → the FIRST announcement after it, robust to when the filing lands
    — the fix for both the Oracle (lagging Q4 10-K) and Disney (very late 10-K)
    failure modes that filing-date proximity got wrong."""
    orcl = [
        {"date": "2026-06-10", "epsActual": 2.11, "epsEstimated": 1.96},  # Q4 FY26 announce
        {"date": "2026-09-09", "epsActual": 1.50, "epsEstimated": 1.48},  # next quarter
    ]
    # Q4 period end 2026-05-31 → its own 06-10 announcement, regardless of a 06-22 10-K.
    m = _match_announcement("2026-05-31", orcl)
    assert m is not None and m["epsActual"] == 2.11

    # Disney-shaped: FQ4'22 announced 40 days after quarter-end, but the 10-K is filed
    # MONTHS later (near FQ1'23's announcement). Must still pick FQ4'22's numbers.
    dis = [
        {"date": "2022-11-08", "epsActual": 0.30, "epsEstimated": 0.50},  # FQ4 '22
        {"date": "2023-02-08", "epsActual": 0.99, "epsEstimated": 0.69},  # FQ1 '23
    ]
    m2 = _match_announcement("2022-09-30", dis)  # FQ4 '22 period end
    assert m2 is not None and m2["epsActual"] == 0.30  # NOT 0.99 (next quarter)

    # A period end with no announcement inside the window → None (never cross-matches
    # a far-future announcement).
    assert _match_announcement("2020-01-01", dis) is None


# ── Outlier / edge cases ──────────────────────────────────────────────


def test_compute_surprise_negative_estimate_sign_is_correct():
    """Loss quarters: abs(estimate) in the denominator keeps the sign tracking
    (actual - estimate), so a SMALLER-than-expected loss is a positive surprise."""
    assert _compute_surprise(-0.10, -0.20) == 50.0    # lost less than expected → beat
    assert _compute_surprise(-0.30, -0.20) == -50.0   # lost more than expected → miss
    assert _compute_surprise(0.05, -0.20) == pytest.approx(125.0)  # profit vs expected loss


def test_compute_surprise_zero_estimate_is_none():
    """A zero estimate has no defined surprise % — no divide-by-zero, returns None."""
    assert _compute_surprise(1.23, 0.0) is None


def test_match_announcement_window_boundaries():
    """The (period_end, period_end + 80d] window is inclusive at 80, exclusive at 81,
    and never matches an announcement on/before the period end (they come after)."""
    ec = [{"date": "2026-08-20", "epsActual": 1.0, "epsEstimated": 0.9}]
    assert _match_announcement("2026-06-01", ec) is not None   # exactly 80 days → matched
    assert _match_announcement("2026-05-31", ec) is None       # 81 days → outside window
    same_day = [{"date": "2026-06-01", "epsActual": 1.0, "epsEstimated": 0.9}]
    assert _match_announcement("2026-06-01", same_day) is None  # on the period end → skipped


def test_match_announcement_slow_reporter_no_cross_quarter():
    """A slow reporter (~55 days after quarter-end) still pairs to ITS OWN
    announcement, never the next quarter's (~90 days out)."""
    ec = [
        {"date": "2026-05-25", "epsActual": 2.0, "epsEstimated": 1.8},  # ~55d after 03-31
        {"date": "2026-08-24", "epsActual": 2.2, "epsEstimated": 2.0},  # next quarter
    ]
    m = _match_announcement("2026-03-31", ec)
    assert m is not None and m["epsActual"] == 2.0


@pytest.mark.asyncio
async def test_missing_announcement_degrades_to_gaap_and_logs_warning(caplog):
    """A quarter with NO announcement in the per-symbol feed degrades to GAAP
    epsDiluted vs the non-GAAP analyst estimate — and must LOG a warning so the
    apples-to-oranges comparison is never silent (CLAUDE.md degrade rule)."""
    import logging

    income = [
        {"date": "2024-05-31", "period": "Q4", "fiscalYear": 2024, "epsDiluted": 1.10,
         "eps": 1.12, "acceptedDate": "2024-06-20", "filingDate": "2024-06-20",
         "revenue": 10_000_000_000},
    ]
    estimates = [{"date": "2024-05-31", "epsAvg": 1.55, "revenueAvg": 10_000_000_000}]
    svc = EarningsService()
    svc.fmp = _FakeFMP(income, estimates, full_ec=[])  # no announcements → GAAP fallback

    with caplog.at_level(logging.WARNING):
        resp = await svc._build_earnings("XYZ")

    q = next(q for q in resp.eps_quarters if getattr(q, "fiscal_date", None) == "2024-05-31")
    assert q.actual_value == pytest.approx(1.10)     # GAAP epsDiluted
    assert q.estimate_value == pytest.approx(1.55)   # non-GAAP estimate
    assert "DEGRADED to GAAP" in caplog.text


@pytest.mark.asyncio
async def test_matched_announcement_null_eps_actual_falls_back_to_gaap(caplog):
    """Regression (adversarial review): a MATCHED announcement whose epsActual is
    null (a not-yet-reported placeholder, or an FMP gap) must NOT silently drop the
    quarter's EPS bar. It falls back to the income-statement GAAP epsDiluted — the
    same degrade path as no-match — while revenue keeps the announcement's real
    revenueActual. Without the fix the EPS quarter vanished entirely."""
    import logging

    income = [
        {"date": "2026-05-31", "period": "Q4", "fiscalYear": 2026, "epsDiluted": 1.45,
         "eps": 1.45, "acceptedDate": "2026-06-22", "filingDate": "2026-06-22",
         "revenue": 19_000_000_000},
    ]
    estimates = [{"date": "2026-05-31", "epsAvg": 1.96, "revenueAvg": 19_000_000_000}]
    # An announcement lands inside the window after the period end, but its epsActual
    # is null (revenueActual IS present) — the matched record lacks a usable EPS.
    full_ec = [
        {"date": "2026-06-10", "epsActual": None, "epsEstimated": 1.96,
         "revenueActual": 19_184_000_000, "revenueEstimated": 19_100_000_000},
    ]

    svc = EarningsService()
    svc.fmp = _FakeFMP(income, estimates, full_ec)
    with caplog.at_level(logging.WARNING):
        resp = await svc._build_earnings("ORCL")

    # EPS quarter is PRESENT (not silently dropped) via the GAAP fallback.
    q = next((q for q in resp.eps_quarters if getattr(q, "fiscal_date", None) == "2026-05-31"), None)
    assert q is not None, "EPS quarter was silently dropped (the bug)"
    assert q.actual_value == pytest.approx(1.45)     # GAAP epsDiluted fallback
    assert q.estimate_value == pytest.approx(1.96)   # non-GAAP analyst estimate
    assert "DEGRADED to GAAP" in caplog.text
    # Revenue is unaffected — still the announcement's real revenueActual.
    rq = next((r for r in resp.revenue_quarters if getattr(r, "fiscal_date", None) == "2026-05-31"), None)
    assert rq is not None and rq.actual_value == pytest.approx(19_184_000_000)
