"""Earnings round 3 (2026-09-30): the EPS digit-shift gate on every path, a sign-aware
filing tie-break, and an NT 10-K Q4 release with no Q4 income row yet.

  * P1/P7 — the just-reported quarter (Phase A2: announced, no income row yet) never ran
    the digit-shift gate, so a dropped-digit epsActual (0.169 vs 1.70) shipped as a
    "-90% miss" on the Financials tab while the report's Track Record screened it out.
    Now the EPS point is OMITTED until the filing lands (revenue and price stay).
  * P21 — the matched-announcement arm replaced a suspect feed actual with the filed GAAP
    EPS and plotted it inside the "Adjusted EPS" series (a ~35% fake collapse). A suspect
    EPS is now omitted on every path; the null-actual and unmatched GAAP fallbacks stay.
  * P15 — the filing veto was a ratio band ([0.67, 1.5] of the feed actual), so a real
    ~90% small-cap miss near break-even whose GAAP figure had the OPPOSITE sign was read
    as a dropped digit and rewritten. The filing is now a positive tie-break: it marks the
    feed actual suspect only when it shares the estimate's sign and sits strictly closer
    (in log10) to the estimate's magnitude than to the feed's.
  * P8 — with FMP's newest income row at Q3 (Q4 not filed yet), a day-100 Q4 release was
    synthesized as Q1 (Q1 rendered REPORTED with Q4's numbers; Q4 vanished). An earlier
    still-unreported estimate quarter now makes the row ambiguous: evidence gives it to
    that quarter ("this") or keeps the later one ("next"); without evidence no quarter
    uses it.
  * P17 — `_EARNINGS_PAYLOAD_VERSION` 3 evicts rows written before these changes.

Every behavioural assertion here fails on the round-2 code. Hermetic: FMP and Supabase
are faked; dates that depend on "today" are built relative to it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import earnings_service as es
from app.services._earnings_common import eps_digit_shift_suspect
from app.services.earnings_service import EarningsService

_TODAY = datetime.now(timezone.utc).date()


def _d(days_from_today: int) -> str:
    return (_TODAY + timedelta(days=days_from_today)).strftime("%Y-%m-%d")


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


def _svc(fake, supabase=None) -> EarningsService:
    svc = EarningsService.__new__(EarningsService)
    svc.fmp = fake
    svc.supabase = supabase
    return svc


def _inc(date_, period, fy, eps=0.3, revenue=1e9):
    return {"date": date_, "period": period, "fiscalYear": fy, "epsDiluted": eps,
            "eps": eps, "revenue": revenue, "reportedCurrency": "USD"}


def _q(quarters, fiscal_date):
    return next((q for q in quarters if q.fiscal_date == fiscal_date), None)


# ═══════════════════════════════════════════════════════════════════════════
# P15 — the shared rule: a sign-aware filing tie-break
# ═══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "actual, estimate, gaap, suspect",
    [
        # Must flag: the filing sits at the ESTIMATE's magnitude, not the feed's.
        (0.169, 1.69, 1.40, True),     # AVGO-shaped dropped digit
        (0.03, 0.28, 0.25, True),      # a small feed actual's dropped digit
        # Must NOT flag: a filing of the opposite sign can never confirm the estimate's
        # magnitude — these are real ~90% misses near break-even.
        (0.05, 0.50, -0.30, False),
        (0.01, 0.10, -0.04, False),
        (0.02, 0.20, -0.10, False),
        # An exact tie (GAAP 0.03 is 3x from both): not proven, so not suspect.
        (0.01, 0.09, 0.03, False),
        # A loss quarter whose filing sits at the FEED's magnitude: a real 10x loss.
        (-0.50, -0.05, -0.80, False),
    ],
)
def test_filing_is_a_sign_aware_tie_break(actual, estimate, gaap, suspect):
    assert eps_digit_shift_suspect(actual, estimate, gaap) is suspect


def test_no_filing_keeps_the_signature_rule():
    assert eps_digit_shift_suspect(0.05, 0.50, None) is True
    assert eps_digit_shift_suspect(0.05, 0.50, 0) is True     # a zero filing is no evidence
    assert eps_digit_shift_suspect(0.05, 0.50, "NaN") is True


# ═══════════════════════════════════════════════════════════════════════════
# P21 + P15 — the matched-announcement arm (an income row exists)
# ═══════════════════════════════════════════════════════════════════════════


def _matched(q2_feed_eps, q2_feed_est, q2_gaap):
    income = [
        {"date": "2026-02-01", "period": "Q1", "fiscalYear": 2026,
         "revenue": 19e9, "epsDiluted": 0.9},
        {"date": "2026-05-03", "period": "Q2", "fiscalYear": 2026,
         "revenue": 22e9, "epsDiluted": q2_gaap},
    ]
    feed = [
        {"date": "2026-03-05", "epsActual": 1.6, "epsEstimated": 1.53,
         "revenueActual": 19e9, "revenueEstimated": 18.9e9},
        {"date": "2026-06-03", "epsActual": q2_feed_eps, "epsEstimated": q2_feed_est,
         "revenueActual": 22e9, "revenueEstimated": 21.9e9},
    ]
    estimates = [{"date": "2099-08-02", "epsAvg": 1.7, "revenueAvg": 24e9}]
    return _FakeFMP(income, estimates, feed)


@pytest.mark.asyncio
async def test_matched_suspect_eps_is_omitted_not_replaced_with_gaap(caplog):
    """Before: Q2 rendered GAAP 1.03 in the adjusted series (has_estimate False)."""
    resp = await _svc(_matched(0.158, 1.57, 1.03))._build_earnings("AVGO")

    assert _q(resp.eps_quarters, "2026-05-03") is None
    assert 1.03 not in [q.actual_value for q in resp.eps_quarters]
    assert _q(resp.revenue_quarters, "2026-05-03").actual_value == pytest.approx(22e9)
    q1 = _q(resp.eps_quarters, "2026-02-01")
    assert q1.actual_value == pytest.approx(1.6) and q1.has_estimate is True
    assert resp.degraded == []
    assert "digit-shift suspect" in caplog.text and "AVGO" in caplog.text


@pytest.mark.asyncio
async def test_matched_miss_the_filing_confirms_is_kept():
    resp = await _svc(_matched(0.158, 1.57, 0.16))._build_earnings("AVGO")
    q2 = _q(resp.eps_quarters, "2026-05-03")
    assert q2.actual_value == pytest.approx(0.158)
    assert q2.surprise_percent == pytest.approx(-89.94) and q2.has_estimate is True


@pytest.mark.asyncio
async def test_opposite_sign_filing_keeps_the_real_small_cap_miss():
    """Before: the real adjusted 0.05 was rewritten to GAAP -0.30 (a NEGATIVE bar for a
    positive quarter) and the -90% miss vanished from both the tab and the report."""
    resp = await _svc(_matched(0.05, 0.50, -0.30))._build_earnings("SMOL")
    q2 = _q(resp.eps_quarters, "2026-05-03")
    assert q2.actual_value == pytest.approx(0.05) and q2.estimate_value == pytest.approx(0.50)
    assert q2.surprise_percent == pytest.approx(-90.0) and q2.has_estimate is True


@pytest.mark.asyncio
async def test_null_actual_gaap_fallback_is_unchanged():
    """The matched-but-null-actual arm still shows the filed GAAP EPS (no surprise)."""
    resp = await _svc(_matched(None, 1.57, 1.03))._build_earnings("AVGO")
    q2 = _q(resp.eps_quarters, "2026-05-03")
    assert q2.actual_value == pytest.approx(1.03) and q2.surprise_percent is None
    assert q2.has_estimate is False


# ═══════════════════════════════════════════════════════════════════════════
# P1/P7 — Phase A2 (announced, no income row yet)
# ═══════════════════════════════════════════════════════════════════════════


def _a2(eps_actual, eps_estimated=1.70):
    d0, d1, d2 = _d(-130), _d(-39), _d(52)
    income = [_inc(_d(-221), "Q1", 2026, eps=1.4, revenue=1e9),
              _inc(d0, "Q2", 2026, eps=1.5, revenue=1.2e10)]
    estimates = [
        {"date": d0, "epsAvg": 1.5, "revenueAvg": 1.2e10},
        {"date": d1, "epsAvg": 1.70, "revenueAvg": 1.3e10},
        {"date": d2, "epsAvg": 1.80, "revenueAvg": 1.4e10},
    ]
    feed = [
        {"date": _d(-190), "epsActual": 1.4, "epsEstimated": 1.38,
         "revenueActual": 1e9, "revenueEstimated": 1e9},
        {"date": _d(-100), "epsActual": 1.55, "epsEstimated": 1.5,
         "revenueActual": 1.2e10, "revenueEstimated": 1.19e10},
        {"date": _d(-9), "epsActual": eps_actual, "epsEstimated": eps_estimated,
         "revenueActual": 1.35e10, "revenueEstimated": 1.3e10},
    ]
    return income, estimates, feed, d1, d2


@pytest.mark.asyncio
async def test_a2_dropped_digit_eps_is_omitted(caplog):
    income, estimates, feed, d1, d2 = _a2(0.169)
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("AVGO")

    assert _q(resp.eps_quarters, d1) is None, "no -90% 'miss' for the just-reported quarter"
    assert 0.169 not in [q.actual_value for q in resp.eps_quarters]
    r1 = _q(resp.revenue_quarters, d1)
    assert r1 is not None and r1.actual_value == pytest.approx(1.35e10)
    assert [q.fiscal_date for q in resp.revenue_quarters].count(d1) == 1, (
        "the quarter is still 'reported' — no pending duplicate appears"
    )
    nxt = _q(resp.eps_quarters, d2)
    assert nxt.actual_value is None and nxt.estimate_value == pytest.approx(1.80)
    assert resp.degraded == []
    assert "digit-shift suspect" in caplog.text and "AVGO" in caplog.text


@pytest.mark.asyncio
async def test_a2_gate_also_applies_to_the_analyst_average_estimate():
    income, estimates, feed, d1, _ = _a2(0.169, eps_estimated=None)
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("AVGO")
    assert _q(resp.eps_quarters, d1) is None


@pytest.mark.asyncio
async def test_a2_normal_beat_is_still_emitted():
    income, estimates, feed, d1, _ = _a2(1.75)
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("XYZ")
    e1 = _q(resp.eps_quarters, d1)
    assert e1.actual_value == pytest.approx(1.75)
    assert e1.surprise_percent == pytest.approx(2.94) and e1.has_estimate is True


@pytest.mark.asyncio
async def test_tab_and_report_screen_agree_on_the_a2_quarter():
    """The report's `_screen_eps_digit_glitches` finds nothing left to screen: the tab
    no longer serves a suspect surprise the report would have to hide."""
    from app.services.agents.ticker_report_data_collector import (
        CollectedTickerData,
        _screen_eps_digit_glitches,
    )

    income, estimates, feed, d1, _ = _a2(0.169)
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("AVGO")
    out = CollectedTickerData(ticker="AVGO", persona_key="warren_buffett")
    out.earnings = resp
    out.income_q = income
    _screen_eps_digit_glitches(out)
    assert out.earnings is resp, "the screen had nothing to change"


# ═══════════════════════════════════════════════════════════════════════════
# P8 — a late Q4 release while FMP's newest income row is still Q3
# ═══════════════════════════════════════════════════════════════════════════

# Q2 and Q3 are filed (Q3 is the NEWEST income row and owns its release). Q4 ended T-110
# and has no income row; it is released at T-10 = day 100 (NT 10-K). Q1 ended T-19 and
# has not reported (its pending row may be listed at T+26).
_P8_Q2, _P8_Q3, _P8_Q4, _P8_Q1, _P8_Q2N = _d(-292), _d(-201), _d(-110), _d(-19), _d(72)
_P8_INCOME = [_inc(_P8_Q2, "Q2", 2025, eps=0.2), _inc(_P8_Q3, "Q3", 2025, eps=0.28)]
_P8_FEED = [
    {"date": _d(-262), "epsActual": 0.22, "epsEstimated": 0.20},
    {"date": _d(-171), "epsActual": 0.30, "epsEstimated": 0.28},
]


@pytest.mark.asyncio
async def test_late_q4_without_income_row_is_not_synthesized_as_q1():
    """The finding's repro: before, Q1 rendered REPORTED with 0.40/0.35 (+14.29%) and Q4
    vanished. The row's consensus matches Q4 and Q1's own release is listed later, so
    the release is Q4's; Q1 stays pending with its own consensus."""
    estimates = [{"date": _P8_Q4, "epsAvg": 0.35}, {"date": _P8_Q1, "epsAvg": 0.12},
                 {"date": _P8_Q2N, "epsAvg": 0.20}]
    feed = [*_P8_FEED,
            {"date": _d(-10), "epsActual": 0.40, "epsEstimated": 0.35},
            {"date": _d(26), "epsActual": None, "epsEstimated": 0.12}]
    resp = await _svc(_FakeFMP(_P8_INCOME, estimates, feed))._build_earnings("NTFL")

    q1 = _q(resp.eps_quarters, _P8_Q1)
    assert q1 is not None and q1.actual_value is None, "Q1 has not reported"
    assert q1.estimate_value == pytest.approx(0.12)
    q4 = _q(resp.eps_quarters, _P8_Q4)
    assert q4 is not None, "Q4 must not vanish"
    assert (q4.actual_value, q4.estimate_value) == (0.40, 0.35)
    assert q4.surprise_percent == pytest.approx(14.29) and q4.has_estimate is True
    assert 0.40 not in [q.actual_value for q in resp.eps_quarters if q.fiscal_date != _P8_Q4]
    assert resp.next_earnings_date.date == _d(26)


@pytest.mark.asyncio
async def test_ambiguous_late_q4_without_income_row_is_used_by_no_quarter(caplog):
    """No later row and one shared consensus: the row could be either quarter's, so no
    quarter uses it. Q1 stays pending and Q4's estimate stays."""
    estimates = [{"date": _P8_Q4, "epsAvg": 0.35}, {"date": _P8_Q1, "epsAvg": 0.35},
                 {"date": _P8_Q2N, "epsAvg": 0.35}]
    feed = [*_P8_FEED, {"date": _d(-10), "epsActual": 0.40, "epsEstimated": 0.35}]
    resp = await _svc(_FakeFMP(_P8_INCOME, estimates, feed))._build_earnings("NTFL")

    q1, q4 = _q(resp.eps_quarters, _P8_Q1), _q(resp.eps_quarters, _P8_Q4)
    assert q1 is not None and q1.actual_value is None
    assert q4 is not None and q4.actual_value is None
    assert q4.estimate_value == pytest.approx(0.35)
    reported = [q.fiscal_date for q in resp.eps_quarters if q.actual_value is not None]
    assert reported == [_P8_Q2, _P8_Q3]
    assert "NTFL" in caplog.text and "used by no quarter" in caplog.text


@pytest.mark.asyncio
async def test_day_120_late_q4_is_not_pinned_onto_q1():
    """Past _LATE_RELEASE_MAX_DAYS: Q4 ended T-130 and is released at T-10 (day 120,
    29 days after Q1 closed). Its consensus is Q4's, so it is still not Q1's result."""
    q3, q4, q1 = _d(-221), _d(-130), _d(-39)
    income = [_inc(_d(-312), "Q2", 2025, eps=0.2), _inc(q3, "Q3", 2025, eps=0.28)]
    estimates = [{"date": q4, "epsAvg": 0.35}, {"date": q1, "epsAvg": 0.12},
                 {"date": _d(52), "epsAvg": 0.20}]
    feed = [{"date": _d(-282), "epsActual": 0.22, "epsEstimated": 0.20},
            {"date": _d(-191), "epsActual": 0.30, "epsEstimated": 0.28},
            {"date": _d(-10), "epsActual": 0.40, "epsEstimated": 0.35}]
    resp = await _svc(_FakeFMP(income, estimates, feed))._build_earnings("NTFL")

    assert _q(resp.eps_quarters, q1).actual_value is None
    assert _q(resp.eps_quarters, q4).actual_value == pytest.approx(0.40)


@pytest.mark.asyncio
async def test_bank_twin_q4_then_fast_q1_are_both_synthesized():
    """Over-block twin: Q4 released at day 15 (synthesized first), then Q1 at day 104
    (13 days after Q1 closed). Q4 is already used, so nothing blocks Q1."""
    estimates = [{"date": _P8_Q4, "epsAvg": 0.95, "revenueAvg": 20e9},
                 {"date": _P8_Q1, "epsAvg": 1.05, "revenueAvg": 22e9},
                 {"date": _P8_Q2N, "epsAvg": 1.15, "revenueAvg": 23e9}]
    feed = [*_P8_FEED,
            {"date": _d(-95), "epsActual": 0.97, "epsEstimated": 0.95},    # Q4, day 15
            {"date": _d(-6), "epsActual": 1.10, "epsEstimated": 1.05}]     # Q1, day 104
    resp = await _svc(_FakeFMP(_P8_INCOME, estimates, feed))._build_earnings("BANK")

    assert _q(resp.eps_quarters, _P8_Q4).actual_value == pytest.approx(0.97)
    q1 = _q(resp.eps_quarters, _P8_Q1)
    assert q1.actual_value == pytest.approx(1.10)
    assert q1.surprise_percent == pytest.approx(4.76)


@pytest.mark.asyncio
async def test_q4_feed_hole_with_q1_consensus_still_synthesizes_q1():
    """Over-block twin: Q4 has neither an income row nor a release in the feed; the
    day-104 row's consensus matches Q1 and no later row contradicts it ("next")."""
    estimates = [{"date": _P8_Q4, "epsAvg": 0.95}, {"date": _P8_Q1, "epsAvg": 1.05},
                 {"date": _P8_Q2N, "epsAvg": 1.15}]
    feed = [*_P8_FEED, {"date": _d(-6), "epsActual": 1.10, "epsEstimated": 1.05}]
    resp = await _svc(_FakeFMP(_P8_INCOME, estimates, feed))._build_earnings("BANK")

    q1 = _q(resp.eps_quarters, _P8_Q1)
    assert q1.actual_value == pytest.approx(1.10)
    assert q1.surprise_percent == pytest.approx(4.76)
    assert 1.10 not in [q.actual_value for q in resp.eps_quarters if q.fiscal_date != _P8_Q1]


# ═══════════════════════════════════════════════════════════════════════════
# P17 — the payload version evicts rows written before these changes
# ═══════════════════════════════════════════════════════════════════════════


class _FakeSupabase:
    def __init__(self, rows):
        self.rows = rows

    def table(self, name):
        assert name == "earnings_cache"
        return self

    def select(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows)


def _cached(version):
    return {"response_json": {"symbol": "AVGO", "eps_quarters": [], "revenue_quarters": [],
                              "price_history": [], "payload_version": version},
            "cached_at": datetime.now(timezone.utc).isoformat(),
            "next_earnings_date": None}


def test_round_two_rows_are_evicted():
    # 4 since 2026-10-01 (P16: a proven reschedule leftover is no longer the next date —
    # tests/test_earnings_p16_reschedule_leftover.py). Rows of every older version,
    # including round 3's, are rebuilt on their next read.
    assert es._EARNINGS_PAYLOAD_VERSION == 4
    assert _svc(None, _FakeSupabase([_cached(2)]))._check_supabase_cache("AVGO") is None
    assert _svc(None, _FakeSupabase([_cached(3)]))._check_supabase_cache("AVGO") is None
    hit = _svc(None, _FakeSupabase([_cached(4)]))._check_supabase_cache("AVGO")
    assert hit is not None and hit.symbol == "AVGO"
