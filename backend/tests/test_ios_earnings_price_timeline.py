"""The Earnings card's price-line time axis (`EarningsPriceTimeline`, EarningsChartView.swift),
ported to Python and run on REAL dates (2026-11-14).

The bug: every dot sat on its quarter's fiscal PERIOD END, so the report-day move drew to
the right of its own dot by the reporting lag. Measured on 14 live tickers: median ~25 days,
worst ~80; CSCO two days after its 2026-11-12 report already ran 20 days (+0.22 slot) past
the Q1 '27 dot, and DIS before its report ran 1.53 slots past its newest dot — under the
gray estimate dot. Now each reported dot is pinned to the day it was ANNOUNCED, the first
gray dot to the next report day, and the axis is piecewise-linear between pinned dots.

The port mirrors the Swift expression for expression (Swift `/` on Int truncates; `_tdiv`
does too); `test_ios_earnings_deepcheck_guards.py` pins the Swift branches the port relies
on, and the constants are read from the Swift source below so the two cannot drift.
The fixtures are the dates FMP returned on 2026-11-14 (workflow wf_dfe21699-736).
"""
from __future__ import annotations

import pathlib
import random
import re
from datetime import date, timedelta

import pytest

_CHART = (pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios" / "Views"
          / "Molecules" / "EarningsChartView.swift")

REPORT_LAG_DAYS = (1, 200)
NEXT_LAG_DAYS = (7, 110)
NEXT_LAG_TOLERANCE_DAYS = 45


# ── Port ──────────────────────────────────────────────────────────────────────


def _tdiv(a: int, b: int) -> int:
    """Swift Int division: truncates toward zero (Python's // floors)."""
    q = abs(a) // abs(b)
    return q if (a >= 0) == (b > 0) else -q


def day_number(text):
    if text is None:
        return None
    parts = text[:10].split("-")
    if len(parts) != 3 or len(parts[0]) != 4 or len(parts[1]) != 2 or len(parts[2]) != 2:
        return None
    if not all(all("0" <= c <= "9" for c in p) for p in parts):
        return None
    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
    if not (1 <= month <= 12) or day < 1:
        return None
    is_leap = (year % 4 == 0 and year % 100 != 0) or year % 400 == 0
    lengths = [31, 29 if is_leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    if day > lengths[month - 1]:
        return None
    shifted_year = year - 1 if month <= 2 else year
    era = _tdiv(shifted_year, 400)
    year_of_era = shifted_year - era * 400
    shifted_month = (month + 9) % 12
    day_of_year = _tdiv(153 * shifted_month + 2, 5) + day - 1
    day_of_era = year_of_era * 365 + _tdiv(year_of_era, 4) - _tdiv(year_of_era, 100) + day_of_year
    return era * 146_097 + day_of_era - 719_468


def ordered_anchors(candidates):
    count = len(candidates)
    if count < 2:
        return list(candidates)
    latest_before, earliest_after = [None] * count, [None] * count
    running = None
    for i in range(count):
        latest_before[i] = running
        running = candidates[i][1] if running is None else max(running, candidates[i][1])
    running = None
    for i in range(count - 1, -1, -1):
        earliest_after[i] = running
        running = candidates[i][1] if running is None else min(running, candidates[i][1])
    kept = []
    for i in range(count):
        day = candidates[i][1]
        if latest_before[i] is not None and day <= latest_before[i]:
            continue
        if earliest_after[i] is not None and day >= earliest_after[i]:
            continue
        kept.append(candidates[i])
    return kept


def median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 == 1 else (ordered[middle - 1] + ordered[middle]) / 2


def median_days_per_slot(anchors):
    if len(anchors) < 2:
        return None
    rates = []
    for i in range(1, len(anchors)):
        slots = anchors[i][0] - anchors[i - 1][0]
        days = anchors[i][1] - anchors[i - 1][1]
        if slots <= 0 or days <= 0:
            return None
        rates.append(days / slots)
    return median(rates)


def placement(quarters, next_day, last_close_day):
    """`quarters`: dicts with `actual` (bool), `fiscal`, `report` ("yyyy-MM-dd" or None).
    Returns (anchors, days_per_slot, mode) or None."""
    historical = [i for i, q in enumerate(quarters) if q["actual"]]
    candidates = []
    for slot in historical:
        report, period_end = day_number(quarters[slot]["report"]), day_number(quarters[slot]["fiscal"])
        if report is None or period_end is None:
            continue
        if not (REPORT_LAG_DAYS[0] <= report - period_end <= REPORT_LAG_DAYS[1]):
            continue
        candidates.append((slot, report))
    report_anchors = ordered_anchors(candidates)
    rate = median_days_per_slot(report_anchors)
    if len(report_anchors) >= 2 and rate is not None:
        anchors = list(report_anchors)
        last_slot, last_day = report_anchors[-1]
        own_lags = []
        for slot, day in report_anchors:
            period_end = day_number(quarters[slot]["fiscal"])
            if period_end is not None:
                own_lags.append(float(day - period_end))
        typical_lag = median(own_lags)
        future = next((i for i in range(len(quarters))
                       if i > last_slot and not quarters[i]["actual"]), None)
        future_end = day_number(quarters[future]["fiscal"]) if future is not None else None
        if (next_day is not None and typical_lag is not None
                and future is not None and future_end is not None
                and next_day > last_day
                and next_day >= (last_close_day if last_close_day is not None else next_day)
                and NEXT_LAG_DAYS[0] <= next_day - future_end <= NEXT_LAG_DAYS[1]
                and abs(float(next_day - future_end) - typical_lag) <= NEXT_LAG_TOLERANCE_DAYS):
            anchors.append((future, next_day))
        return anchors, rate, "reportDay"
    candidates = []
    for slot in historical:
        period_end = day_number(quarters[slot]["fiscal"])
        if period_end is not None:
            candidates.append((slot, period_end))
    period_anchors = ordered_anchors(candidates)
    rate = median_days_per_slot(period_anchors)
    if len(period_anchors) < 2 or rate is None:
        return None
    return period_anchors, rate, "periodEnd"


def slot_for_day(placed, day):
    anchors, days_per_slot, _ = placed
    first, last = anchors[0], anchors[-1]
    if day <= first[1]:
        return first[0] - (first[1] - day) / days_per_slot
    if day >= last[1]:
        return last[0] + (day - last[1]) / days_per_slot
    lower, upper = 0, len(anchors) - 1
    while upper - lower > 1:
        middle = (lower + upper) // 2
        if anchors[middle][1] <= day:
            lower = middle
        else:
            upper = middle
    a, b = anchors[lower], anchors[upper]
    return a[0] + (day - a[1]) / (b[1] - a[1]) * (b[0] - a[0])


# ── Real fixtures (FMP, 2026-11-14) ───────────────────────────────────────────


def _q(fiscal, report=None, actual=True):
    return {"actual": actual, "fiscal": fiscal, "report": report}


def _f(fiscal):
    return _q(fiscal, None, actual=False)


LAST_CLOSE = "2026-11-13"

# CSCO: FY ends late July; reported Q1 '27 on 2026-11-12, next report 2027-02-10.
CSCO_1Y = [_q("2026-01-24", "2026-02-11"), _q("2026-04-25", "2026-05-13"),
           _q("2026-07-25", "2026-08-12"), _q("2026-10-24", "2026-11-12"),
           _f("2027-01-24"), _f("2027-04-24")]
CSCO_NEXT = "2027-02-10"

# NVDA: quarter ended 2026-10-26, not yet reported; next report 2026-11-17.
NVDA_1Y = [_q("2025-10-26", "2025-11-19"), _q("2026-01-25", "2026-02-25"),
           _q("2026-04-26", "2026-05-20"), _q("2026-07-26", "2026-08-26"),
           _f("2026-10-26"), _f("2027-01-26")]
NVDA_NEXT = "2026-11-17"

# DIS: the slowest quarterly reporter in the set (Q4 at day 47); next 2026-11-17.
DIS_1Y = [_q("2025-09-27", "2025-11-13"), _q("2025-12-27", "2026-02-02"),
          _q("2026-03-28", "2026-05-06"), _q("2026-06-27", "2026-08-05"),
          _f("2026-09-27"), _f("2026-12-27")]
DIS_NEXT = "2026-11-17"

# COST: 12/12/12/16-week quarters (report spacing 77-119 days).
COST_3Y = [_q("2023-11-26", "2023-12-14"), _q("2024-02-18", "2024-03-07"),
           _q("2024-05-12", "2024-05-30"), _q("2024-09-01", "2024-09-26"),
           _q("2024-11-24", "2024-12-12"), _q("2025-02-16", "2025-03-06"),
           _q("2025-05-11", "2025-05-29"), _q("2025-08-31", "2025-09-25"),
           _q("2025-11-23", "2025-12-11"), _q("2026-02-15", "2026-03-05"),
           _q("2026-05-10", "2026-05-28"), _q("2026-08-30", "2026-09-24"),
           _f("2026-11-30"), _f("2027-02-28")]
COST_NEXT = "2026-12-10"

# BHP: half-year reporter; FMP has no release row for H2 FY23 (2023-06-30).
BHP_3Y = [_q("2020-12-31", "2021-02-15"), _q("2021-06-30", "2021-08-17"),
          _q("2021-12-31", "2022-02-14"), _q("2022-06-30", "2022-08-15"),
          _q("2022-12-31", "2023-02-20"), _q("2023-06-30", None),
          _q("2023-12-31", "2024-02-19"), _q("2024-06-30", "2024-08-26"),
          _q("2024-12-31", "2025-02-17"), _q("2025-06-30", "2025-08-18"),
          _q("2025-12-31", "2026-02-16"), _q("2026-06-30", "2026-08-17"),
          _f("2026-12-30"), _f("2027-06-30")]
BHP_NEXT = "2027-02-15"

# BABA EPS: Q4 '26 (2026-03-31) omitted by the digit-shift gate → a 181-day hole.
BABA_EPS_3Y = [_q("2023-06-30", "2023-08-10"), _q("2023-09-30", "2023-11-16"),
               _q("2023-12-31", "2024-02-07"), _q("2024-03-31", "2024-05-14"),
               _q("2024-06-30", "2024-08-15"), _q("2024-09-30", "2024-11-15"),
               _q("2024-12-31", "2025-02-20"), _q("2025-03-31", "2025-05-15"),
               _q("2025-06-30", "2025-08-29"), _q("2025-09-30", "2025-11-25"),
               _q("2025-12-31", "2026-03-19"), _q("2026-06-30", "2026-08-20"),
               _f("2026-09-30"), _f("2026-12-30")]
BABA_NEXT = "2026-11-19"


def _place(quarters, next_date, last_close=LAST_CLOSE):
    return placement(quarters, day_number(next_date), day_number(last_close))


def _last_historical(quarters):
    return max(i for i, q in enumerate(quarters) if q["actual"])


# ── The fix, on the user's case and on the worst ones ─────────────────────────


def test_csco_line_ends_at_the_dot_it_just_reported():
    """The user's screen: reported 2026-11-12, last close 11-13 → one day past the dot,
    not the 20 days (+0.22 slot) the period-end axis drew."""
    placed = _place(CSCO_1Y, CSCO_NEXT)
    assert placed[2] == "reportDay"
    tail = slot_for_day(placed, day_number(LAST_CLOSE)) - 3
    assert 0 < tail <= 0.02
    # Each report day sits exactly on its own dot.
    for slot in range(4):
        assert slot_for_day(placed, day_number(CSCO_1Y[slot]["report"])) == pytest.approx(slot)


def test_csco_old_axis_reproduces_the_bug():
    """The fixture is the bug: the period-end axis puts the 11-13 close ~0.22 slot past
    the Q1 '27 dot, and each report-day move ~0.2 slot right of its own dot."""
    days = [day_number(q["fiscal"]) for q in CSCO_1Y[:4]]
    rate = 3 / (days[3] - days[0])
    assert (day_number(LAST_CLOSE) - days[3]) * rate == pytest.approx(0.22, abs=0.01)
    assert (day_number("2026-11-12") - days[3]) * rate == pytest.approx(0.21, abs=0.01)


@pytest.mark.parametrize("quarters, next_date", [(NVDA_1Y, NVDA_NEXT), (DIS_1Y, DIS_NEXT)])
def test_a_quarter_waiting_to_report_never_runs_past_its_gray_dot(quarters, next_date):
    """NVDA ran 1.21 slots and DIS 1.53 slots past the newest dot — under the gray dot of
    a quarter that had not reported yet. Now the line stops before that dot."""
    placed = _place(quarters, next_date)
    assert placed[0][-1] == (4, day_number(next_date)), "the gray dot is pinned to the next report"
    end = slot_for_day(placed, day_number(LAST_CLOSE))
    assert 3 < end < 4


def test_costco_uneven_quarters_each_report_day_lands_on_its_own_dot():
    placed = _place(COST_3Y, COST_NEXT)
    for slot in range(12):
        assert slot_for_day(placed, day_number(COST_3Y[slot]["report"])) == pytest.approx(slot)
    # The 16-week Q4 gap is drawn inside its own two dots, not spread over the series.
    assert slot_for_day(placed, day_number("2024-07-15")) == pytest.approx(
        2 + (day_number("2024-07-15") - day_number("2024-05-30"))
        / (day_number("2024-09-26") - day_number("2024-05-30")))


def test_bhp_quarter_without_a_report_day_is_interpolated_not_anchored():
    placed = _place(BHP_3Y, BHP_NEXT)
    slots = [a[0] for a in placed[0]]
    assert 5 not in slots and slots[:5] == [0, 1, 2, 3, 4] and slots[5] == 6
    middle = slot_for_day(placed, day_number("2023-08-20"))
    assert 4 < middle < 6
    assert placed[2] == "reportDay"


def test_baba_eps_hole_keeps_every_dot_on_its_own_report_day():
    """The 181-day EPS hole used to shift the middle dots by up to 81 days."""
    placed = _place(BABA_EPS_3Y, BABA_NEXT)
    for slot in range(12):
        assert slot_for_day(placed, day_number(BABA_EPS_3Y[slot]["report"])) == pytest.approx(slot)


# ── Degraded inputs ───────────────────────────────────────────────────────────


def test_fewer_than_two_report_days_falls_back_to_period_ends_without_the_next_date():
    """A build whose earnings feed failed (no report days) or an older backend."""
    quarters = [dict(q, report=None) for q in CSCO_1Y]
    quarters[3]["report"] = "2026-11-12"                 # one alone is not enough
    placed = _place(quarters, CSCO_NEXT)
    assert placed[2] == "periodEnd"
    assert [a[0] for a in placed[0]] == [0, 1, 2, 3], "never the next-report anchor"
    for slot in range(4):
        assert slot_for_day(placed, day_number(quarters[slot]["fiscal"])) == pytest.approx(slot)


def test_no_usable_date_at_all_draws_no_line():
    quarters = [dict(q, report=None, fiscal=None) for q in CSCO_1Y]
    assert _place(quarters, CSCO_NEXT) is None
    assert _place([_q("2026-10-24", "2026-11-12")], CSCO_NEXT) is None
    assert _place([], CSCO_NEXT) is None


def test_undated_last_quarter_extrapolates_at_the_median_rate():
    quarters = [dict(q) for q in CSCO_1Y]
    quarters[3]["report"] = None
    placed = _place(quarters, CSCO_NEXT)
    assert [a[0] for a in placed[0]] == [0, 1, 2, 4]
    # Q1 '27's dot is interpolated between Q4 '26's report and the next one.
    assert 2 < slot_for_day(placed, day_number("2026-11-12")) < 4


@pytest.mark.parametrize("next_date, why", [
    ("2026-11-12", "on the last report day"),
    ("2026-11-01", "before the last report day"),
    ("2026-11-10", "before the last close"),
    ("2027-01-28", "3 days after the period end: inside the min lag"),
    ("2027-05-20", "116 days after the period end: some later quarter's"),
    (None, "no next date"),
    ("garbage", "unparseable"),
])
def test_a_next_date_that_cannot_be_the_first_gray_dots_is_ignored(next_date, why):
    placed = _place(CSCO_1Y, next_date)
    assert [a[0] for a in placed[0]] == [0, 1, 2, 3], why
    # Past the last anchor the line runs at the median rate (~91 days per slot).
    assert placed[1] == pytest.approx(91, abs=1)


def test_next_date_on_report_day_before_the_actuals_land_ends_on_the_gray_dot():
    """Report day: the backend keeps TODAY as the next date until the actuals arrive."""
    placed = _place(NVDA_1Y, "2026-11-17", last_close="2026-11-17")
    assert slot_for_day(placed, day_number("2026-11-17")) == pytest.approx(4)


def test_no_future_slot_extrapolates_at_the_median_rate():
    placed = _place(CSCO_1Y[:4], CSCO_NEXT)
    assert [a[0] for a in placed[0]] == [0, 1, 2, 3]
    assert slot_for_day(placed, day_number(LAST_CLOSE)) == pytest.approx(3 + 1 / placed[1])


def test_omitted_eps_on_the_just_reported_quarter_accepts_the_long_tail():
    """A2 dropped a suspect EPS for the just-reported quarter, so the first gray EPS dot is
    the quarter AFTER it: ~182 days over one slot is legitimate, not rejected."""
    quarters = CSCO_1Y[:3] + [_f("2027-01-24"), _f("2027-04-24")]
    placed = _place(quarters, CSCO_NEXT)
    assert placed[0][-1] == (3, day_number(CSCO_NEXT))
    assert 2 < slot_for_day(placed, day_number(LAST_CLOSE)) < 3


def test_inverted_and_shared_report_days_lose_both_dots():
    quarters = [dict(q) for q in CSCO_1Y]
    # Both inside their own lag window (107 and 11 days), but out of order.
    quarters[1]["report"], quarters[2]["report"] = "2026-08-10", "2026-08-05"
    placed = _place(quarters, CSCO_NEXT)
    assert [a[0] for a in placed[0][:-1]] == [0, 3]
    quarters = [dict(q) for q in CSCO_1Y]
    quarters[1]["report"] = quarters[2]["report"] = "2026-08-10"   # one day, both in window
    assert [a[0] for a in _place(quarters, CSCO_NEXT)[0][:-1]] == [0, 3]


@pytest.mark.parametrize("report, kept", [
    ("2026-10-24", False),   # the period end itself: lag 0
    ("2026-10-25", True),    # lag 1
    ("2027-02-21", True),    # lag 120: a late Q4 the backend's A2 pairing may send
    ("2027-05-12", True),    # lag 200 (_A2_REPORT_MAX_LAG_DAYS)
    ("2027-05-13", False),   # lag 201
    ("2026-10-01", False),   # before the period ended
])
def test_report_lag_bounds(report, kept):
    quarters = [dict(q) for q in CSCO_1Y[:4]]
    quarters[3]["report"] = report
    anchored = [a[0] for a in _place(quarters, None)[0]]
    assert (3 in anchored) is kept


def test_the_following_quarters_next_date_never_pins_the_first_gray_dot():
    """Review finding (2026-11-14): CSCO reports after the close on 11-12; at 00:30 UTC
    FMP's row has no actual yet, so Q1 '27 is still gray and next_earnings_date is the
    FOLLOWING quarter's (2027-02-10, 109 days after Q1 '27's period end — inside 7-110).
    Pinned there, the 11-12 move drew at slot 3.5, half a slot left of the gray dot. The
    company's own lag (18 days) is 91 days away from 109, so the date is ignored."""
    quarters = CSCO_1Y[:3] + [_f("2026-10-24"), _f("2027-01-24")]
    placed = _place(quarters, "2027-02-10")
    assert [a[0] for a in placed[0]] == [0, 1, 2], "the next date is not this quarter's"
    assert slot_for_day(placed, day_number("2026-11-12")) == pytest.approx(3.0, abs=0.05)


def test_an_overdue_quarters_projected_successor_is_ignored_too():
    """The backend skips an overdue quarter and projects the one after it. For a fast
    reporter that projection still falls inside 7-110 days of the overdue quarter's end
    (CSCO: Q2 '27's 2027-01-24 + 18 = 2027-02-11, 110 days after Q1 '27's 10-24), so only
    the own-lag tolerance (18 vs 110) refuses it."""
    quarters = CSCO_1Y[:3] + [_f("2026-10-24"), _f("2027-01-24")]
    next_day = day_number("2027-02-11")
    assert next_day - day_number("2026-10-24") == NEXT_LAG_DAYS[1], "inside the window"
    placed = _place(quarters, "2027-02-11")
    assert [a[0] for a in placed[0]] == [0, 1, 2]


@pytest.mark.parametrize("next_date, pinned", [
    ("2027-03-28", True),    # 63 days: typical 18 + 45
    ("2027-03-29", False),   # 64 days
])
def test_the_next_date_tolerance_edge(next_date, pinned):
    placed = _place(CSCO_1Y, next_date)
    assert (placed[0][-1] == (4, day_number(next_date))) is pinned


def test_a_slow_q4_next_date_is_still_this_quarters():
    """DIS's Q4 comes ~47 days after the period end against a ~39-day typical lag."""
    placed = _place(DIS_1Y, DIS_NEXT)
    assert placed[0][-1] == (4, day_number(DIS_NEXT))


def test_ordered_anchors_is_not_greedy():
    candidates = [(0, 100), (1, 300), (2, 200), (3, 400), (4, 500)]
    assert ordered_anchors(candidates) == [(0, 100), (3, 400), (4, 500)]
    assert ordered_anchors([(0, 5)]) == [(0, 5)] and ordered_anchors([]) == []


def test_median_rate_ignores_one_long_segment():
    assert median_days_per_slot([(0, 0), (1, 91), (2, 182), (3, 364)]) == pytest.approx(91)
    assert median_days_per_slot([(0, 0), (1, 91)]) == pytest.approx(91)
    assert median_days_per_slot([(0, 0), (1, 80), (2, 180)]) == pytest.approx(90)
    assert median_days_per_slot([(0, 0), (0, 91)]) is None


@pytest.mark.parametrize("seed", range(40))
def test_x_strictly_rises_with_the_day(seed):
    """visiblePriceWindow assumes x grows with date (its in-window run is contiguous).
    Random report/period dates, gaps, nones and next dates never break that."""
    rng = random.Random(seed)
    start = date(2023, 1, 1) + timedelta(days=rng.randint(0, 400))
    quarters = []
    end = start
    for _ in range(rng.randint(2, 12)):
        end = end + timedelta(days=rng.choice([84, 91, 91, 98, 112, 182]))
        lag = rng.choice([None, 0, 5, 18, 30, 47, 90, 110, 125])
        report = None if lag is None else (end + timedelta(days=lag)).isoformat()
        quarters.append(_q(end.isoformat(), report))
    quarters += [_f((end + timedelta(days=91)).isoformat()), _f((end + timedelta(days=182)).isoformat())]
    last_close = end + timedelta(days=rng.randint(0, 120))
    next_date = (end + timedelta(days=rng.randint(60, 220))).isoformat()
    placed = _place(quarters, next_date, last_close.isoformat())
    if placed is None:
        return
    days = range(day_number(start.isoformat()) - 200, day_number(last_close.isoformat()) + 1)
    xs = [slot_for_day(placed, d) for d in days]
    assert all(b > a for a, b in zip(xs, xs[1:]))


# ── dayNumber ─────────────────────────────────────────────────────────────────


def test_day_number_matches_the_calendar():
    epoch = date(1970, 1, 1)
    d = date(1999, 12, 25)
    while d < date(2032, 3, 5):
        assert day_number(d.isoformat()) == (d - epoch).days
        d += timedelta(days=13)
    assert day_number("2000-02-29") == (date(2000, 2, 29) - epoch).days
    assert day_number("2026-11-12T16:05:00") == (date(2026, 11, 12) - epoch).days


@pytest.mark.parametrize("text", [None, "", "2026-11", "2026-1-12", "26-11-12", "2026/11/12",
                                  "2026-13-01", "2026-00-10", "2026-02-29", "2100-02-29",
                                  "2026-04-31", "2026-11-00", "2026-+1-12", "2026-11-1x"])
def test_day_number_refuses_malformed_text(text):
    assert day_number(text) is None


# ── The port is the Swift ─────────────────────────────────────────────────────


def _swift_range(name: str) -> tuple[int, int]:
    src = _CHART.read_text(encoding="utf-8")
    match = re.search(rf"static let {name}: ClosedRange<Int> = (\d+)\.\.\.(\d+)", src)
    assert match, f"EarningsPriceTimeline.{name} moved"
    return int(match.group(1)), int(match.group(2))


def test_the_ports_constants_are_the_swift_ones():
    assert _swift_range("reportLagDays") == REPORT_LAG_DAYS
    assert _swift_range("nextLagDays") == NEXT_LAG_DAYS
    src = _CHART.read_text(encoding="utf-8")
    match = re.search(r"static let nextLagToleranceDays: Int = (\d+)", src)
    assert match and int(match.group(1)) == NEXT_LAG_TOLERANCE_DAYS


def test_the_next_date_window_is_the_backends_pairing_window():
    from app.services import earnings_service as es
    assert NEXT_LAG_DAYS == (es._MIN_ANNOUNCE_LAG_DAYS, es._LATE_RELEASE_MAX_DAYS)
    # The client's report-day bound is never tighter than what the backend sends.
    assert REPORT_LAG_DAYS[0] <= es._MIN_ANNOUNCE_LAG_DAYS
    assert REPORT_LAG_DAYS[1] >= es._LATE_RELEASE_MAX_DAYS
    # ...nor than Phase A2's widest pairing (a late Q4 given to the earlier quarter).
    assert REPORT_LAG_DAYS[1] >= es._A2_REPORT_MAX_LAG_DAYS
