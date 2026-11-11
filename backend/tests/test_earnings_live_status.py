"""Live earnings results: the PURE rules — statuses, per-metric figures and their guards,
the card fields, and the history extras (owner request 2026-10-29, INTC's report day).

The card goes LIVE on report day and the sheet shows estimate vs actual for EPS and revenue
the moment EACH lands. Everything a user reads there is decided by the functions below, so
they are pinned directly, with hand-built records — no clock, no network.
"""
from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from itertools import product
from types import SimpleNamespace

import pytest

import app.services.earnings_live_service as els
from app.services.earnings_filing_service import FilingFigures
from app.utils import market_hours
from app.utils.market_hours import ET, is_trading_day, next_trading_day, previous_trading_day

REPORT = date(2026, 10, 29)           # a Thursday, INTC's report day
NEXT = date(2026, 10, 30)


def _et(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET)


def _rec(**kw) -> els._Record:
    base = dict(symbol="INTC", day=REPORT, eps_estimate=0.41, revenue_estimate=16.6e9)
    base.update(kw)
    return els._Record(**base)


def _filing(**kw) -> FilingFigures:
    base = dict(accession="0000050863-26-000123", accepted_at=None, url="https://sec/idx")
    base.update(kw)
    return FilingFigures(**base)


def _filed(**kw) -> els._Record:
    """A report complete from the company's own release: preliminary until FMP has it."""
    return _rec(filing=_filing(revenue=17.1e9, eps_adjusted=0.44), **kw)


# ── statuses ─────────────────────────────────────────────────────────────────


def _status(rec: els._Record, now_et: datetime) -> str:
    return els.live_status(rec.day, now_et.date(), now_et, els.eps_metric(rec),
                           els.revenue_metric(rec))


def test_a_future_report_is_upcoming():
    assert _status(_rec(day=NEXT), _et(REPORT, 12)) == els.STATUS_UPCOMING


def test_report_day_before_six_et_is_still_upcoming():
    assert _status(_rec(), _et(REPORT, 5, 59)) == els.STATUS_UPCOMING


def test_report_day_is_live_from_six_until_results_complete():
    assert _status(_rec(), _et(REPORT, 6)) == els.STATUS_LIVE
    assert _status(_rec(), _et(REPORT, 16, 5)) == els.STATUS_LIVE


def test_whatever_lands_first_keeps_the_card_live_until_both_are_in():
    """"If revenue comes out, show it right away" — and the report stays LIVE for EPS."""
    revenue_only = _rec(fmp_revenue=17.1e9)
    assert els.revenue_metric(revenue_only).actual == 17.1e9
    assert els.eps_metric(revenue_only).actual is None
    assert _status(revenue_only, _et(REPORT, 16, 10)) == els.STATUS_LIVE
    both = replace(revenue_only, fmp_eps=0.44)
    assert _status(both, _et(REPORT, 16, 30)) == els.STATUS_REPORTED


def test_eps_alone_completes_a_report_that_has_no_revenue_estimate():
    rec = _rec(revenue_estimate=None, fmp_eps=0.44)
    assert _status(rec, _et(REPORT, 16, 30)) == els.STATUS_REPORTED


def test_a_guard_only_revenue_consensus_keeps_eps_alone_from_completing_a_report():
    rec = _rec(revenue_estimate=None, revenue_guard_estimate=16.6e9, fmp_eps=0.44)
    assert els.revenue_metric(rec).estimate is None, "guard-only: never shown"
    assert _status(rec, _et(REPORT, 16, 30)) == els.STATUS_LIVE
    assert _status(replace(rec, fmp_revenue=17.1e9), _et(REPORT, 16, 30)) == els.STATUS_REPORTED


def test_a_held_revenue_is_not_in_even_with_no_estimate_to_show():
    rec = _rec(revenue_estimate=None, revenue_guard_estimate=16.6e9, fmp_eps=0.44,
               fmp_revenue=1.71e9)
    held = els.revenue_metric(rec)
    assert held.checking and (held.actual, held.estimate) == (None, None)
    assert _status(rec, _et(REPORT, 16, 30)) == els.STATUS_LIVE


def test_after_twenty_two_et_a_report_with_nothing_is_awaiting_never_late():
    assert _status(_rec(), _et(REPORT, 22)) == els.STATUS_AWAITING
    assert _status(_rec(fmp_revenue=17.1e9), _et(REPORT, 22)) == els.STATUS_REPORTED


def test_the_morning_after_a_partial_report_is_reported_and_an_empty_one_awaiting():
    now = _et(NEXT, 8)
    assert _status(_rec(fmp_eps=0.44), now) == els.STATUS_REPORTED
    assert _status(_rec(), now) == els.STATUS_AWAITING


def test_an_early_filing_before_six_makes_the_card_live_not_upcoming():
    rec = _rec(filing=_filing(revenue=17.1e9))
    assert _status(rec, _et(REPORT, 5, 30)) == els.STATUS_LIVE


@pytest.mark.parametrize("hour,visible", [(8, True), (11, True), (12, False), (18, False)])
def test_an_awaiting_card_drops_off_at_noon_et_the_next_day(hour, visible):
    assert els.recent_card_visible(els.STATUS_AWAITING, REPORT, _et(NEXT, hour)) is visible


def test_a_reported_card_stays_all_next_day():
    assert els.recent_card_visible(els.STATUS_REPORTED, REPORT, _et(NEXT, 21))


@pytest.mark.parametrize("status", [els.STATUS_UPCOMING, els.STATUS_LIVE])
def test_a_card_that_never_ended_its_window_is_not_a_recent_card(status):
    assert not els.recent_card_visible(status, REPORT, _et(NEXT, 8))


def test_an_awaiting_card_past_its_next_trading_day_stays_down_on_a_closed_morning():
    """Monday's card on Saturday morning: Friday was its next trading day, and that is over."""
    monday, saturday = date(2026, 10, 26), date(2026, 10, 31)
    assert not els.recent_card_visible(els.STATUS_AWAITING, monday, _et(saturday, 9))


def _first_trading_day_after(day: date) -> date:
    day += timedelta(days=1)
    while not is_trading_day(day):
        day += timedelta(days=1)
    return day


def _every_day_2026_to_2028():
    day = date(2026, 1, 1)
    while day.year < 2029:
        yield day
        day += timedelta(days=1)


def test_an_awaiting_card_drops_once_at_noon_on_its_first_trading_day_on_every_calendar_day():
    """Walks every live day the card can be read on, closed days and holiday runs included."""
    for today in _every_day_2026_to_2028():
        for day in els.live_days(today)[:-1]:
            ends = _first_trading_day_after(day)
            for hour in (0, 11, 12, 23):
                assert els.recent_card_visible(els.STATUS_AWAITING, day, _et(today, hour)) is (
                    (today, hour) < (ends, 12)), (day, today, hour)


# ── the live days ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("today,first,count", [
    (date(2026, 10, 29), date(2026, 10, 28), 2),    # an ordinary Thursday
    (date(2026, 11, 7), date(2026, 11, 6), 2),      # a Saturday
    (date(2026, 11, 8), date(2026, 11, 6), 3),      # a Sunday
    (date(2026, 11, 9), date(2026, 11, 6), 4),      # a Monday: Friday, the weekend, today
    (date(2026, 11, 10), date(2026, 11, 9), 2),     # a Tuesday
    (date(2026, 11, 26), date(2026, 11, 25), 2),    # Thanksgiving
    (date(2026, 11, 27), date(2026, 11, 25), 3),    # the Friday after it
    (date(2026, 9, 8), date(2026, 9, 4), 5),        # the Tuesday after Labor Day
])
def test_the_live_days_are_every_calendar_day_from_the_previous_trading_day(today, first, count):
    assert els.live_days(today) == [first + timedelta(days=i) for i in range(count)]


def test_the_window_stays_whole_and_never_moves_back_on_the_holiday_table():
    """A key leaves the sender's memory with its report only if the floor never comes back."""
    previous_floor = None
    for today in _every_day_2026_to_2028():
        days = els.live_days(today)
        assert days[0] == previous_trading_day(today) and days[-1] == today
        assert all(b - a == timedelta(days=1) for a, b in zip(days, days[1:]))
        assert not any(is_trading_day(d) for d in days[1:-1]), "only closed days in between"
        assert previous_floor is None or days[0] >= previous_floor
        previous_floor = days[0]


# ── per-metric figures and their guards ──────────────────────────────────────


def test_fmp_eps_is_adjusted_and_carries_its_surprise():
    m = els.eps_metric(_rec(fmp_eps=0.44))
    assert (m.actual, m.source, m.basis) == (0.44, els.SOURCE_FMP, els.BASIS_ADJUSTED)
    assert m.surprise_pct == pytest.approx(7.32, abs=0.01)


@pytest.mark.parametrize("actual,estimate", [
    (0.01, 0.0148), (0.41, 0.4134), (0.41, 0.4062), (-0.41, -0.4134), (1.2, 1.2034),
    (0.41, 0.415), (0.41, 0.405),
], ids=["a_penny", "above", "below", "a_loss", "one_decimal", "top_of_the_band",
        "bottom_of_the_band"])
def test_a_cent_print_from_fmp_has_no_verdict_with_the_consensus_inside_its_half_cent(
        actual, estimate):
    """$0.41 is anything from $0.405 to $0.415, whichever source carries it, so the data cannot say
    which side of a 0.4134 consensus the company landed on."""
    m = els.eps_metric(_rec(eps_estimate=estimate, fmp_eps=actual))
    assert (m.actual, m.source, m.surprise_pct) == (actual, els.SOURCE_FMP, None)


def test_the_same_print_reads_the_same_from_the_release_and_from_fmp():
    """No colour flip when FMP replaces the filing's figure with the same number."""
    filed = els.eps_metric(_rec(eps_estimate=0.4134,
                                filing=_filing(eps_adjusted=0.41, eps_rounding=0.005)))
    confirmed = els.eps_metric(_rec(eps_estimate=0.4134, fmp_eps=0.41))
    assert (filed.actual, filed.surprise_pct) == (confirmed.actual, confirmed.surprise_pct)


@pytest.mark.parametrize("actual,estimate,pct", [
    (0.41, 0.4151, -1.23),                # past the top of the band
    (0.41, 0.4049, 1.26),                 # past the bottom
    (0.4134, 0.41, 0.83),                 # finer digits are an exact figure, not a print
    (0.41, 0.41, 0.0),                    # equal to the consensus is in line
    (0.60, 0.41, 46.34),
])
def test_an_fmp_eps_outside_that_band_keeps_its_verdict(actual, estimate, pct):
    m = els.eps_metric(_rec(eps_estimate=estimate, fmp_eps=actual))
    assert m.surprise_pct == pytest.approx(pct, abs=0.01)


def test_a_cent_print_with_no_consensus_or_a_tiny_one_stays_as_it_was():
    assert els.eps_metric(_rec(eps_estimate=None, fmp_eps=0.41)).surprise_pct is None
    assert els.eps_metric(_rec(eps_estimate=0.004, fmp_eps=0.40)).surprise_pct is None


def test_the_band_is_no_data_guard_a_held_figure_stays_held():
    rec = _rec(eps_estimate=1.65, fmp_eps=0.169)
    assert els.eps_metric(rec).checking is True


def test_the_financials_tab_keeps_its_raw_compare_for_the_same_print():
    """The Financials tab and the sheet's last four quarters compare the raw figures, so a quarter
    this band leaves without a verdict on report day reads "Miss -0.8%" there. Pinned so that gap
    is a choice: it has shipped, its chart reads it, and closing it is the owner's call."""
    from app.services.earnings_service import _compute_surprise

    assert _compute_surprise(0.41, 0.4134) == -0.82
    assert els._quarter_result(0.41, 0.4134, True) == "missed"
    assert els.eps_metric(_rec(eps_estimate=0.4134, fmp_eps=0.41)).surprise_pct is None


def test_fmp_replaces_the_filing_figure_the_moment_it_has_one():
    rec = _rec(filing=_filing(eps_adjusted=0.45, revenue=17.0e9))
    assert els.eps_metric(rec).source == els.SOURCE_FILING
    assert els.revenue_metric(rec).source == els.SOURCE_FILING
    rec = replace(rec, fmp_eps=0.44, fmp_revenue=17.1e9)
    assert (els.eps_metric(rec).actual, els.eps_metric(rec).source) == (0.44, els.SOURCE_FMP)
    assert els.revenue_metric(rec).actual == 17.1e9


def test_a_gaap_only_release_is_shown_as_gaap_with_no_beat_or_miss():
    """The consensus is ADJUSTED. A GAAP-vs-adjusted comparison is the fake 'miss' the owner
    ruled out on 2026-09-27 (`news_insight_service._earnings_line`)."""
    m = els.eps_metric(_rec(filing=_filing(eps_gaap=-0.10)))
    assert (m.actual, m.basis, m.surprise_pct) == (-0.10, els.BASIS_GAAP, None)


def test_the_adjusted_figure_wins_over_gaap_from_the_same_release():
    m = els.eps_metric(_rec(filing=_filing(eps_adjusted=0.23, eps_gaap=0.90)))
    assert (m.actual, m.basis) == (0.23, els.BASIS_ADJUSTED)


def test_a_dropped_digit_eps_is_held_as_checking_not_shown_as_a_ninety_percent_miss():
    rec = _rec(eps_estimate=1.65, fmp_eps=0.169)
    m = els.eps_metric(rec)
    assert m.actual is None and m.checking is True


def test_a_held_fmp_eps_falls_back_to_a_sane_filing_figure():
    rec = _rec(eps_estimate=1.65, fmp_eps=0.169, filing=_filing(eps_adjusted=1.69))
    m = els.eps_metric(rec)
    assert (m.actual, m.source) == (1.69, els.SOURCE_FILING)


def test_no_surprise_percentage_against_a_near_zero_estimate():
    m = els.eps_metric(_rec(eps_estimate=0.001, fmp_eps=0.40))
    assert m.actual == 0.40 and m.surprise_pct is None


def test_a_revenue_more_than_twice_off_a_material_consensus_is_held():
    m = els.revenue_metric(_rec(fmp_revenue=1.66e9))           # the AVGO-shaped dropped digit
    assert m.actual is None and m.checking is True


def test_a_held_revenue_shows_once_the_other_source_agrees():
    rec = _rec(revenue_estimate=16.6e9, fmp_revenue=40.0e9, filing=_filing(revenue=40.2e9))
    m = els.revenue_metric(rec)
    assert m.actual == 40.0e9 and not m.checking


def test_a_small_company_revenue_is_never_held_against_a_tiny_consensus():
    """Pre-revenue names (JOBY, ACHR) land far from a $30M consensus all the time."""
    m = els.revenue_metric(_rec(revenue_estimate=30e6, fmp_revenue=1e6))
    assert m.actual == 1e6 and not m.checking


def test_a_held_fmp_revenue_falls_back_to_a_sane_filing_figure():
    """FMP's dropped digit must not hide the company's own number."""
    m = els.revenue_metric(_rec(fmp_revenue=1.69e9, filing=_filing(revenue=16.9e9)))
    assert (m.actual, m.source, m.checking) == (16.9e9, els.SOURCE_FILING, False)
    assert m.surprise_pct == pytest.approx(1.81, abs=0.01), "in the band, so a real comparison"


@pytest.mark.parametrize("filed", [90e9, 40e9, 8.2e9], ids=["far_high", "just_high", "just_low"])
def test_a_revenue_is_held_when_neither_source_passes(filed):
    m = els.revenue_metric(_rec(fmp_revenue=1.69e9, filing=_filing(revenue=filed)))
    assert m == els.LiveMetric(16.6e9, checking=True)


def test_a_sound_fmp_revenue_keeps_its_place_ahead_of_a_sound_filing_figure():
    m = els.revenue_metric(_rec(fmp_revenue=16.0e9, filing=_filing(revenue=17.1e9)))
    assert (m.actual, m.source) == (16.0e9, els.SOURCE_FMP)


@pytest.mark.parametrize("value,shown", [(5e9, True), (4.99e9, False), (20e9, True),
                                         (20.01e9, False)])
def test_the_plausibility_band_keeps_its_edges(value, shown):
    m = els.revenue_metric(_rec(revenue_estimate=10e9, fmp_revenue=value))
    assert (m.actual is not None, m.checking) == (shown, not shown)


@pytest.mark.parametrize("est,fmp,filed", [
    (1.66e9, 16.9e9, 16.9e9),      # a dropped digit in the estimate: was +918%
    (166e9, 16.9e9, 17.0e9),       # an added one: was -90%
    (16.6e9, 40.0e9, 40.2e9),      # a true result this far off is shown too, with no percentage
], ids=["estimate_dropped_a_digit", "estimate_added_a_digit", "far_above_the_estimate"])
def test_a_figure_that_passes_only_on_agreement_carries_no_surprise(est, fmp, filed):
    """Two sources prove the actual, not the consensus it failed against: that consensus may be
    the glitch, as `earnings_service._reconcile_revenue` found, and +918% is not a result."""
    rec = _rec(revenue_estimate=est, fmp_revenue=fmp, filing=_filing(revenue=filed))
    m = els.revenue_metric(rec)
    assert (m.actual, m.source, m.surprise_pct, m.checking) == (fmp, els.SOURCE_FMP, None, False)
    assert m.estimate == est


def test_a_figure_in_the_band_keeps_its_surprise_when_the_sources_also_agree():
    m = els.revenue_metric(_rec(fmp_revenue=17.1e9, filing=_filing(revenue=17.1e9)))
    assert m.surprise_pct == pytest.approx(3.01, abs=0.01)


def test_a_guard_only_consensus_never_licenses_a_surprise_against_the_shown_one():
    """The shown consensus judges the figure. Read beside the guard-only one, $16.9B would pass
    and print +918% against the $1.66B on show."""
    rec = _rec(revenue_estimate=1.66e9, revenue_guard_estimate=16.6e9, fmp_revenue=16.9e9)
    assert els.revenue_metric(rec) == els.LiveMetric(1.66e9, checking=True)


@pytest.mark.parametrize("fmp,filed,shown,source", [
    (40.0e9, 40.8e9, 40.0e9, els.SOURCE_FMP),
    (40.0e9, 40.81e9, 40.81e9, els.SOURCE_FILING),
    (40.0e9, 40.9e9, None, None),
], ids=["at_the_edge", "only_the_filings_two_percent_reaches", "past_it"])
def test_each_source_is_judged_against_the_other_from_its_own_figure(fmp, filed, shown, source):
    m = els.revenue_metric(_rec(fmp_revenue=fmp, filing=_filing(revenue=filed)))
    assert (m.actual, m.source, m.surprise_pct) == (shown, source, None)
    assert m.checking is (shown is None)


def test_a_filing_figure_behind_a_held_fmp_figure_keeps_its_rounding_rule():
    """A print cannot say which side of a consensus inside its own rounding the true figure is."""
    print_ = _filing(revenue=17.1e9, revenue_rounding=5e7)
    m = els.revenue_metric(_rec(revenue_estimate=17.08e9, fmp_revenue=1.71e9, filing=print_))
    assert (m.actual, m.source, m.surprise_pct) == (17.1e9, els.SOURCE_FILING, None)
    m = els.revenue_metric(_rec(revenue_estimate=16.6e9, fmp_revenue=1.71e9, filing=print_))
    assert (m.actual, m.surprise_pct) == (17.1e9, pytest.approx(3.01, abs=0.01))


def test_the_rounding_rule_never_reaches_an_fmp_figure_that_won():
    print_ = _filing(revenue=17.1e9, revenue_rounding=5e7)
    m = els.revenue_metric(_rec(revenue_estimate=17.08e9, fmp_revenue=17.06e9, filing=print_))
    assert (m.source, m.surprise_pct) == (els.SOURCE_FMP, -0.12)


def test_a_zero_revenue_is_held_at_the_metric():
    assert els.revenue_metric(_rec(fmp_revenue=0.0)) == els.LiveMetric(16.6e9, checking=True)


@pytest.mark.parametrize("est,shown", [(30e6, True), (99.9e6, True), (100e6, False)])
def test_a_zero_shows_only_under_the_floor_the_fold_stores_it_by(est, shown):
    m = els.revenue_metric(_rec(revenue_estimate=est, fmp_revenue=0.0))
    assert (m.actual == 0.0, m.checking) == (shown, not shown)


def test_a_zero_with_no_consensus_at_all_does_not_show():
    m = els.revenue_metric(_rec(revenue_estimate=None, fmp_revenue=0.0))
    assert (m.actual, m.checking) == (None, True)


def test_a_consensus_turning_material_takes_a_stored_zero_off():
    """The zero was stored against the tiny consensus it came with, and an estimate moves only
    with a figure, so `revenue_estimate` stays tiny: only the guard-only figure knows."""
    rec = _rec(revenue_estimate=30e6, revenue_guard_estimate=16.6e9, fmp_revenue=0.0)
    assert els.revenue_metric(rec) == els.LiveMetric(None, checking=True)


@pytest.mark.parametrize("rec", [
    _rec(revenue_estimate=30e6, revenue_guard_estimate=16.6e9, fmp_revenue=0.0,
         filing=_filing(revenue=16.9e9)),
    _rec(revenue_estimate=30e6, revenue_guard_estimate=16.6e9, filing=_filing(revenue=16.9e9)),
], ids=["behind_a_zero_taken_off", "with_no_fmp_figure"])
def test_a_filing_figure_is_never_measured_against_an_overtaken_estimate(rec):
    """$16.9B against the $30M a zero left on the record is +56,233%: the zero stepping aside
    must not swap one false number for another. It reads the consensus it sits on."""
    assert els.revenue_metric(rec) == els.LiveMetric(16.6e9, 16.9e9, 1.81, els.SOURCE_FILING)


def test_a_figure_from_a_row_with_a_tiny_estimate_is_guarded_by_its_twins_consensus():
    """Any material consensus means a real business, whichever row FMP filled."""
    rec = _rec(revenue_estimate=30e6, revenue_guard_estimate=16.6e9, fmp_revenue=16.9e9)
    m = els.revenue_metric(rec)
    assert (m.actual, m.surprise_pct, m.checking) == (16.9e9, None, False)
    rec = _rec(revenue_estimate=30e6, revenue_guard_estimate=16.6e9, fmp_revenue=1e6)
    assert els.revenue_metric(rec) == els.LiveMetric(None, checking=True)


def test_a_tiny_consensus_that_stays_tiny_still_judges_its_own_figure():
    rec = _rec(revenue_estimate=30e6, revenue_guard_estimate=31e6, fmp_revenue=2e6)
    assert els.revenue_metric(rec).surprise_pct == pytest.approx(-93.33, abs=0.01)


@pytest.mark.parametrize("est", [30e6, 16.6e9], ids=["tiny_consensus", "material_consensus"])
def test_a_zero_gives_way_to_a_positive_filing_figure(est):
    filed = est * 1.1
    m = els.revenue_metric(_rec(revenue_estimate=est, fmp_revenue=0.0,
                                filing=_filing(revenue=filed)))
    assert (m.actual, m.source, m.checking) == (filed, els.SOURCE_FILING, False)


def test_two_zeros_are_not_an_agreement():
    rec = _rec(fmp_revenue=0.0, filing=_filing(revenue=0.0))
    assert els.revenue_metric(rec) == els.LiveMetric(16.6e9, checking=True)


def test_a_positive_figure_is_never_read_as_contradicted_by_a_zero():
    m = els.revenue_metric(_rec(revenue_estimate=30e6, fmp_revenue=2e6,
                                filing=_filing(revenue=0.0)))
    assert (m.actual, m.source) == (2e6, els.SOURCE_FMP)


def test_a_negative_revenue_never_shows_whatever_the_consensus():
    m = els.revenue_metric(_rec(revenue_estimate=30e6, fmp_revenue=-5e6))
    assert m == els.LiveMetric(30e6, checking=True)


_REVENUES = [None, 0.0, 1e6, 15.5e6, 31e6, 310e6, 3.1e9, 1.69e9, 8.2e9, 16.9e9, 17.1e9, 33.2e9,
             40e9]


def test_no_mix_of_sources_and_consensus_shows_a_wrong_revenue():
    """Whatever the two sources and the two consensus figures say. What this cannot name is
    which figure wins, and the cases above pin that; what it pins is that nothing here prints
    a number the guards exist to stop: an overtaken estimate, a verdict from one, a percentage
    against a consensus a second source fell outside, or FMP far off the filing on its own word."""
    floor = els._REVENUE_GUARD_MIN_ESTIMATE
    for est, guard, fmp, filed, rounding in product(
            [None, 30e6, 99.9e6, 100e6, 1.66e9, 16.6e9], [None, 30e6, 150e6, 16.6e9],
            _REVENUES, _REVENUES, [None, 5e7]):
        filing = None if filed is None else _filing(revenue=filed, revenue_rounding=rounding)
        rec = _rec(revenue_estimate=est, revenue_guard_estimate=guard, fmp_revenue=fmp,
                   filing=filing)
        m = els.revenue_metric(rec)
        case = (est, guard, fmp, filed, rounding, m)
        any_figure = fmp is not None or filed is not None
        judged_by = est if est is not None else guard
        figures = [v for v in (fmp, filed) if v is not None]
        read_by_filing = m.source == els.SOURCE_FILING and not fmp and m.estimate == guard
        printed = m.estimate == est or read_by_filing or (m.estimate is None and any(
            els._estimate_overtaken(rec, v) for v in figures))
        assert printed, case
        assert m.checking is (any_figure and m.actual is None), case
        assert m.due is (not any_figure and judged_by is not None), case
        if m.actual is None:
            assert m.surprise_pct is None and m.source is None, case
            continue
        assert m.actual == (fmp if m.source == els.SOURCE_FMP else filed), case
        if m.actual == 0:
            assert max(est or 0, guard or 0, filed or 0) < floor and not filed, case
            assert judged_by is not None, case
        if m.source == els.SOURCE_FMP and filed and filed > 0:
            stated = [c for c in (est, guard) if c]
            fmp_borne_out = any(0.5 <= fmp / c <= 2 for c in stated)
            filing_refused = not els._revenue_passes(filed, els._guard_estimate(rec, filed), fmp)
            if els._far_apart(fmp, filed):
                assert fmp_borne_out or filing_refused, case
            if any(0.5 <= filed / c <= 2 for c in stated):
                agree = abs(filed - fmp) <= 0.02 * fmp
                assert fmp_borne_out or filing_refused or agree, case
        if est is not None and guard is not None and est < floor <= guard and m.actual:
            one_consensus = 0.5 <= guard / est <= 2
            lost_a_digit = not 0.2 <= m.actual / guard <= 5
            if one_consensus and lost_a_digit and not 0.5 <= m.actual / est <= 2:
                assert m.surprise_pct is None, case
        if m.surprise_pct is not None:
            read = m.estimate
            assert read_by_filing or (
                read == est and not els._estimate_overtaken(rec, m.actual)), case
            assert read < floor or -50 <= m.surprise_pct <= 100, case
            other = filed if m.source == els.SOURCE_FMP else fmp
            assert not other or 0.5 <= m.actual / read <= 2, case


def test_a_bad_fmp_revenue_leaves_a_filing_complete_report_reported():
    rec = _rec(fmp_revenue=1.69e9, filing=_filing(revenue=16.9e9, eps_adjusted=0.44))
    snap = els.snapshot_of(rec, REPORT, _et(REPORT, 17))
    assert snap.status == els.STATUS_REPORTED and snap.preliminary is True
    assert els.card_fields(snap, REPORT)["revenue_actual"] == 16.9e9


# ── a filing against an FMP figure far off it ────────────────────────────────


@pytest.mark.parametrize("est,fmp,filed,surprise", [
    (50e6, 5.2e6, 52e6, 4.0),
    (50e6, 520e6, 52e6, 4.0),
    (None, 1.69e9, 16.9e9, None),
    (None, 169e9, 16.9e9, None),
    (None, 52e6, 5.2e6, None),
    (30e6, 1.69e9, 16.9e9, None),
], ids=["dropped_digit_under_the_floor", "added_digit_under_the_floor", "no_consensus",
        "no_consensus_added_digit", "no_consensus_the_filing_is_the_smaller",
        "a_consensus_behind_neither"])
def test_with_nothing_material_to_judge_them_fmp_far_off_the_filing_gives_way(
        est, fmp, filed, surprise):
    """The band has nothing to say here, so FMP's dropped digit used to replace the company's own
    figure and read as confirmed. A dropped digit always lands nearer a tiny consensus, so being
    nearer one proves nothing: only a consensus inside the band stands behind a figure."""
    m = els.revenue_metric(_rec(revenue_estimate=est, fmp_revenue=fmp,
                                filing=_filing(revenue=filed)))
    assert (m.actual, m.source, m.surprise_pct, m.checking) == (
        filed, els.SOURCE_FILING, surprise, False)


def test_fmp_giving_way_keeps_the_figure_labelled_preliminary():
    rec = _rec(revenue_estimate=None, fmp_eps=0.44, fmp_revenue=1.69e9,
               filing=_filing(revenue=16.9e9))
    snap = els.snapshot_of(rec, REPORT, _et(REPORT, 17))
    assert (snap.status, snap.preliminary) == (els.STATUS_REPORTED, True)
    assert els.needs_fmp(snap.eps, snap.revenue), "only FMP's own figure confirms it"


@pytest.mark.parametrize("fmp,source", [
    (2.0e6, els.SOURCE_FMP), (1.99e6, els.SOURCE_FILING),
    (50.0e6, els.SOURCE_FMP), (50.1e6, els.SOURCE_FILING),
], ids=["at_the_low_edge", "past_it", "at_the_high_edge", "past_that"])
def test_fmp_keeps_its_place_up_to_five_times_off_the_filing(fmp, source):
    m = els.revenue_metric(_rec(revenue_estimate=None, fmp_revenue=fmp,
                                filing=_filing(revenue=10e6)))
    assert m.source == source


def test_the_far_apart_ratio_is_the_financials_tabs_own():
    from app.services import earnings_service

    assert els._FEED_FILED_RATIO == (earnings_service._FEED_FILED_MIN_RATIO,
                                     earnings_service._FEED_FILED_MAX_RATIO)


@pytest.mark.parametrize("filed", [5.2e6, 520e6], ids=["below", "above"])
def test_fmp_with_a_consensus_behind_it_keeps_its_place_over_a_far_filing(filed):
    """A dropped digit does not land on the consensus by chance, and a release read off a segment
    line can be this far off. `earnings_service._reconcile_revenue` keeps the feed here too."""
    m = els.revenue_metric(_rec(revenue_estimate=50e6, fmp_revenue=52e6,
                                filing=_filing(revenue=filed)))
    assert (m.actual, m.source, m.surprise_pct) == (52e6, els.SOURCE_FMP, 4.0)


@pytest.mark.parametrize("est,fmp,filed,surprise", [
    (50e6, 11e6, 52e6, 4.0),
    (50e6, 24e6, 52e6, 4.0),
    (50e6, 120e6, 52e6, 4.0),
    (95e6, 40e6, 96e6, 1.05),
], ids=["under_the_estimate", "just_past_the_band", "over_the_estimate", "beside_the_floor"])
def test_fmp_no_consensus_bears_out_gives_way_to_a_filing_one_does_at_any_gap(
        est, fmp, filed, surprise):
    """A figure 78% under the consensus used to replace the one sitting on it, and to read as
    confirmed. `earnings_service._reconcile_revenue` takes the figure nearer the consensus too."""
    m = els.revenue_metric(_rec(revenue_estimate=est, fmp_revenue=fmp,
                                filing=_filing(revenue=filed)))
    assert (m.actual, m.source, m.surprise_pct, m.checking) == (
        filed, els.SOURCE_FILING, surprise, False)


def test_a_guard_only_consensus_can_bear_a_filing_out_too():
    rec = _rec(revenue_estimate=None, revenue_guard_estimate=50e6, fmp_revenue=11e6,
               filing=_filing(revenue=52e6))
    assert els.revenue_metric(rec) == els.LiveMetric(None, 52e6, None, els.SOURCE_FILING)


@pytest.mark.parametrize("fmp,surprise", [(30e6, -40.0), (99e6, 98.0)],
                         ids=["under_the_filing", "over_the_filing"])
def test_fmp_with_a_consensus_behind_it_keeps_its_place_at_any_gap(fmp, surprise):
    m = els.revenue_metric(_rec(revenue_estimate=50e6, fmp_revenue=fmp,
                                filing=_filing(revenue=52e6)))
    assert (m.actual, m.source, m.surprise_pct) == (fmp, els.SOURCE_FMP, surprise)


def test_with_no_consensus_at_all_fmp_keeps_its_place_inside_five_times():
    m = els.revenue_metric(_rec(revenue_estimate=None, fmp_revenue=11e6,
                                filing=_filing(revenue=52e6)))
    assert (m.actual, m.source) == (11e6, els.SOURCE_FMP)


def test_two_sources_that_agree_never_swap_on_the_edge_of_the_band():
    """FMP's exact figure replaces the filing's when they agree, wherever the band falls."""
    m = els.revenue_metric(_rec(revenue_estimate=100e6, fmp_revenue=49.9e6,
                                filing=_filing(revenue=50.4e6)))
    assert (m.actual, m.source, m.surprise_pct) == (49.9e6, els.SOURCE_FMP, None)


def test_a_filing_with_the_material_consensus_behind_it_outranks_fmp_on_its_tiny_row():
    rec = _rec(revenue_estimate=30e6, revenue_guard_estimate=16.6e9, fmp_revenue=31e6,
               filing=_filing(revenue=16.9e9))
    m = els.revenue_metric(rec)
    assert (m.actual, m.source, m.estimate, m.surprise_pct) == (
        16.9e9, els.SOURCE_FILING, None, None)


def test_a_filing_the_guard_refuses_leaves_fmp_on_show():
    """Outranking only moves it up the queue: a figure must still pass."""
    rec = _rec(revenue_estimate=16.6e9, revenue_guard_estimate=100e9, fmp_revenue=17e9,
               filing=_filing(revenue=101e9))
    m = els.revenue_metric(rec)
    assert (m.actual, m.source) == (17e9, els.SOURCE_FMP)


@pytest.mark.parametrize("fmp", [0.0, -5e6, None], ids=["zero", "negative", "no_figure"])
def test_only_two_positive_figures_can_be_far_apart(fmp):
    assert els._far_apart(fmp, 16.9e9) is False
    assert els._far_apart(16.9e9, fmp) is False


# ── a verdict needs a consensus that can give one ────────────────────────────


@pytest.mark.parametrize("est,fmp,filed", [
    (95e6, 961e6, 960e6),
    (5e6, 20e6, 20.1e6),
    (30e6, 300e6, 320e6),
], ids=["a_dropped_digit_in_a_consensus_under_the_floor", "a_small_consensus",
        "sources_close_but_not_agreeing"])
def test_two_sources_far_off_the_estimate_on_show_carry_no_percentage_at_any_size(est, fmp, filed):
    """+912% was printed and pushed: two sources prove the actual, never the consensus."""
    m = els.revenue_metric(_rec(revenue_estimate=est, fmp_revenue=fmp,
                                filing=_filing(revenue=filed)))
    assert (m.actual, m.source, m.surprise_pct, m.checking) == (fmp, els.SOURCE_FMP, None, False)
    assert m.estimate == est, "not overtaken: nothing says the consensus is out of date"


def test_a_lone_figure_far_off_a_tiny_consensus_keeps_its_percentage():
    """Pre-revenue names land there all the time, and with one source nothing says otherwise."""
    m = els.revenue_metric(_rec(revenue_estimate=95e6, fmp_revenue=961e6))
    assert m.surprise_pct == pytest.approx(911.58, abs=0.01)
    m = els.revenue_metric(_rec(revenue_estimate=95e6, filing=_filing(revenue=961e6)))
    assert m.surprise_pct == pytest.approx(911.58, abs=0.01)


def test_a_zero_beside_a_figure_is_no_second_source():
    """The zero was unfilled, so this is one source, as if the zero had never been stored."""
    m = els.revenue_metric(_rec(revenue_estimate=30e6, fmp_revenue=0.0,
                                filing=_filing(revenue=300e6)))
    assert (m.actual, m.source, m.surprise_pct) == (300e6, els.SOURCE_FILING, 900.0)


def test_two_sources_in_the_band_of_a_tiny_consensus_keep_their_verdict():
    m = els.revenue_metric(_rec(revenue_estimate=95e6, fmp_revenue=96e6,
                                filing=_filing(revenue=96.1e6)))
    assert m.surprise_pct == pytest.approx(1.05, abs=0.01)


# ── which of two consensus figures judges a figure ───────────────────────────


@pytest.mark.parametrize("est,stated,fmp,surprise", [
    (60e6, 600e6, 61e6, 1.67),
    (30e6, 16.6e9, 31e6, 3.33),
], ids=["a_twin_ten_times_larger", "a_twin_five_hundred_times_larger"])
def test_a_figure_inside_its_own_estimates_band_keeps_its_verdict_beside_a_larger_twin(
        est, stated, fmp, surprise):
    """The figure and the estimate came on one row and bear each other out: a stale twin must not
    hold a real result, at any size of company."""
    rec = _rec(revenue_estimate=est, revenue_guard_estimate=stated, fmp_revenue=fmp)
    m = els.revenue_metric(rec)
    assert (m.actual, m.estimate, m.surprise_pct, m.checking) == (fmp, est, surprise, False)
    m = els.revenue_metric(replace(rec, fmp_revenue=None, filing=_filing(revenue=fmp)))
    assert (m.actual, m.source, m.surprise_pct) == (fmp, els.SOURCE_FILING, surprise), (
        "the company's own print, the same")


@pytest.mark.parametrize("est,stated,fmp,surprise", [
    (99.9e6, 100.1e6, 120e6, 20.12),
    (99e6, 101e6, 95e6, -4.04),
    (99e6, 101e6, 48e6, -51.52),
], ids=["a_beat", "a_near_miss", "past_the_band_of_the_larger_figure"])
def test_one_consensus_either_side_of_the_floor_judges_as_it_would_alone(
        est, stated, fmp, surprise):
    m = els.revenue_metric(_rec(revenue_estimate=est, revenue_guard_estimate=stated,
                                fmp_revenue=fmp))
    assert (m.actual, m.estimate, m.surprise_pct, m.checking) == (fmp, est, surprise, False)


@pytest.mark.parametrize("est,stated,fmp", [
    (99e6, 101e6, 9.9e6),
    (60e6, 120e6, 6e6),
    (51e6, 101e6, 5.1e6),
    (99e6, 101e6, 990e6),
], ids=["a_dropped_digit", "a_dropped_digit_with_the_figures_twice_apart",
        "the_twins_either_side_of_twice_the_estimate", "an_added_digit"])
def test_a_digit_off_one_consensus_either_side_of_the_floor_is_held(est, stated, fmp):
    """One consensus judges as it would alone only for a figure that could be a result of it. A
    "-90%" against a consensus that is material on its twin is what the guard exists to stop."""
    m = els.revenue_metric(_rec(revenue_estimate=est, revenue_guard_estimate=stated,
                                fmp_revenue=fmp))
    assert m == els.LiveMetric(est, checking=True)


@pytest.mark.parametrize("fmp,held", [(20e6, False), (19.9e6, True), (500e6, False),
                                      (501e6, True)],
                         ids=["a_fifth", "under_a_fifth", "five_times", "over_five_times"])
def test_one_consensus_stops_judging_alone_five_times_off_the_larger_figure(fmp, held):
    m = els.revenue_metric(_rec(revenue_estimate=99e6, revenue_guard_estimate=100e6,
                                fmp_revenue=fmp))
    assert (m.actual, m.checking) == ((None, True) if held else (fmp, False))


def test_two_sources_a_digit_off_one_consensus_show_with_no_percentage():
    """Agreement proves the figure, and nothing says the consensus is out of date: it stays."""
    m = els.revenue_metric(_rec(revenue_estimate=99e6, revenue_guard_estimate=101e6,
                                fmp_revenue=9.9e6, filing=_filing(revenue=9.9e6)))
    assert m == els.LiveMetric(99e6, 9.9e6, None, els.SOURCE_FMP)


def test_a_filing_on_one_consensus_replaces_fmps_digit_off_it():
    m = els.revenue_metric(_rec(revenue_estimate=99e6, revenue_guard_estimate=101e6,
                                fmp_revenue=9.9e6, filing=_filing(revenue=99.5e6)))
    assert (m.actual, m.source, m.surprise_pct) == (99.5e6, els.SOURCE_FILING, 0.51)


def test_a_zero_near_the_floor_reads_the_larger_consensus_as_the_fold_does():
    """The fold refuses a zero by the largest figure a response stated, so one stored before that
    figure arrived must not stand on the smaller."""
    rec = _rec(revenue_estimate=99e6, revenue_guard_estimate=101e6, fmp_revenue=0.0)
    assert els.revenue_metric(rec) == els.LiveMetric(99e6, checking=True)


def test_a_zero_estimate_is_inside_no_band():
    assert els._near(1e6, 0.0) is False


@pytest.mark.parametrize("est,stated,value,overtaken", [
    (30e6, 16.6e9, 16.9e9, True),
    (30e6, 16.6e9, 1e6, True),
    (30e6, 16.6e9, 0.0, True),
    (60e6, 600e6, 200e6, True),
    (60e6, 600e6, 120.1e6, True),
    (60e6, 120.6e6, 300e6, True),
    (30e6, 16.6e9, 31e6, False),
    (60e6, 600e6, 61e6, False),
    (60e6, 600e6, 120e6, False),
    (60e6, 120e6, 300e6, False),
    (99.9e6, 100e6, 300e6, False),
    (99.9e6, 99.8e6, 300e6, False),
    (100e6, 16.6e9, 16.9e9, False),
    (30e6, None, 16.9e9, False),
    (None, 16.6e9, 16.9e9, False),
    (99e6, 101e6, 9.9e6, True),
    (99e6, 101e6, 990e6, True),
    (99e6, 101e6, 48e6, False),
    (99e6, 101e6, 0.0, False),
    (60e6, 120e6, 30e6, False),
], ids=["its_own_digits_far_off", "far_off_both", "a_zero", "between_the_two",
        "just_past_its_own_band", "just_past_one_consensus", "inside_its_own_band",
        "inside_its_own_band_smaller", "at_the_edge_of_its_own_band",
        "twice_is_still_one_consensus", "one_consensus_either_side_of_the_floor",
        "both_under_the_floor", "the_estimate_is_material", "nothing_else_stated",
        "no_estimate_on_show", "a_digit_under_one_consensus", "a_digit_over_one_consensus",
        "past_the_band_within_five_times", "a_zero_beside_one_consensus",
        "at_the_edge_of_the_estimates_band"])
def test_only_a_tiny_estimate_far_from_consensus_and_from_the_figure_is_overtaken(
        est, stated, value, overtaken):
    rec = _rec(revenue_estimate=est, revenue_guard_estimate=stated)
    assert els._estimate_overtaken(rec, value) is overtaken


@pytest.mark.parametrize("fmp,filed,estimate,surprise", [
    (16.9e9, None, None, None), (1.69e9, 16.9e9, None, None),
    (None, 16.9e9, 16.6e9, 1.81), (0.0, 16.9e9, 16.6e9, 1.81),
], ids=["fmp", "a_filing_past_a_dropped_digit", "a_filing", "a_filing_past_a_zero"])
def test_an_overtaken_estimate_is_never_printed_as_the_consensus(fmp, filed, estimate, surprise):
    """"Revenue $16.9B vs $30.0M est" is the 563x comparison the verdict was dropped to avoid. A
    filing with no FMP figure beside it reads the consensus it sits on instead."""
    rec = _rec(revenue_estimate=30e6, revenue_guard_estimate=16.6e9, fmp_revenue=fmp,
               filing=None if filed is None else _filing(revenue=filed))
    m = els.revenue_metric(rec)
    assert (m.actual, m.estimate, m.surprise_pct) == (16.9e9, estimate, surprise)
    snap = els.snapshot_of(rec, REPORT, _et(REPORT, 17))
    assert els.live_payload("INTC", REPORT, snap, company_name=None, eps_estimate=None,
                            revenue_estimate=None, extras=None, today=REPORT,
                            )["revenue"]["estimate"] == estimate


def test_an_overtaken_estimate_is_not_printed_beside_a_held_figure_either():
    rec = _rec(revenue_estimate=30e6, revenue_guard_estimate=16.6e9, fmp_revenue=1e6)
    assert els.revenue_metric(rec) == els.LiveMetric(None, checking=True)


def test_an_estimate_a_figure_has_not_ruled_on_stays_on_show():
    rec = _rec(revenue_estimate=30e6, revenue_guard_estimate=16.6e9)
    assert els.revenue_metric(rec) == els.LiveMetric(30e6, due=True)


# ── a filing figure reads against the consensus it sits on ───────────────────


def _twins(**kw) -> els._Record:
    """$600M on show (the row FMP touched last), its twin's $60M guard-only."""
    return _rec(revenue_estimate=600e6, revenue_guard_estimate=600e6,
                revenue_smallest_estimate=60e6, **kw)


def test_a_filing_figure_reads_against_the_twin_it_sits_on():
    rec = _twins(filing=_filing(revenue=61e6))
    assert els.revenue_metric(rec) == els.LiveMetric(60e6, 61e6, 1.67, els.SOURCE_FILING)


def test_the_rounding_rule_holds_against_the_twin_too():
    """The print cannot say which side of the consensus the true figure is on, whichever
    consensus that is."""
    rec = _twins(filing=_filing(revenue=60.2e6, revenue_rounding=0.5e6))
    assert els.revenue_metric(rec) == els.LiveMetric(60e6, 60.2e6, None, els.SOURCE_FILING)


@pytest.mark.parametrize("filed", [5.9e6, 150e6, 2e9],
                         ids=["under_both", "between_the_two", "over_both"])
def test_a_filing_on_neither_twin_stays_held(filed):
    assert els.revenue_metric(_twins(filing=_filing(revenue=filed))) == els.LiveMetric(
        600e6, checking=True)


def test_a_filing_on_the_estimate_on_show_never_reads_the_twin():
    rec = _twins(filing=_filing(revenue=590e6))
    assert els.revenue_metric(rec) == els.LiveMetric(600e6, 590e6, -1.67, els.SOURCE_FILING)


def test_a_filing_reads_a_larger_twin_it_sits_on_as_well():
    rec = replace(_twins(filing=_filing(revenue=590e6)), revenue_estimate=60e6)
    assert els.revenue_metric(rec) == els.LiveMetric(600e6, 590e6, -1.67, els.SOURCE_FILING)


def test_a_figure_from_fmp_keeps_its_own_rows_estimate():
    """Twins are read only with no FMP figure: a surprise never crosses to another row's
    consensus, and a filing beside FMP's figure cannot move that row's estimate either."""
    assert els.revenue_metric(_twins(fmp_revenue=61e6)) == els.LiveMetric(600e6, checking=True)
    assert els.revenue_metric(_twins(fmp_revenue=6.1e6, filing=_filing(revenue=61e6))
                              ) == els.LiveMetric(600e6, checking=True)


def test_a_zero_is_no_figure_to_keep_an_estimate_for():
    rec = _twins(fmp_revenue=0.0, filing=_filing(revenue=61e6))
    assert els.revenue_metric(rec) == els.LiveMetric(60e6, 61e6, 1.67, els.SOURCE_FILING)


def test_twins_inside_each_others_band_stay_with_the_row_touched_last():
    """One consensus and a stale row, with `lastUpdated` the only thing that can tell them apart."""
    rec = _rec(revenue_estimate=90e6, revenue_guard_estimate=90e6,
               revenue_smallest_estimate=60e6, filing=_filing(revenue=70e6))
    assert els.revenue_metric(rec) == els.LiveMetric(90e6, 70e6, -22.22, els.SOURCE_FILING)


def test_fmps_figure_with_no_estimate_of_its_own_gets_none_from_a_twin():
    rec = _rec(revenue_estimate=None, revenue_guard_estimate=50e6,
               revenue_smallest_estimate=50e6, fmp_revenue=5.2e6, filing=_filing(revenue=52e6))
    assert els.revenue_metric(rec) == els.LiveMetric(None, 52e6, None, els.SOURCE_FILING)


def test_a_zero_with_no_estimate_of_its_own_leaves_a_filing_free_to_read_one():
    rec = _rec(revenue_estimate=None, revenue_guard_estimate=30e6,
               revenue_smallest_estimate=30e6, fmp_revenue=0.0, filing=_filing(revenue=33e6))
    assert els.revenue_metric(rec) == els.LiveMetric(30e6, 33e6, 10.0, els.SOURCE_FILING)


# ── a stored row of zeros that turns out to be unfilled ──────────────────────


def _zeros(**kw) -> els._Record:
    """Stored as a pre-revenue name's real report: both zeros, against a tiny consensus."""
    return _rec(revenue_estimate=30e6, fmp_eps=0.0, fmp_revenue=0.0, **kw)


@pytest.mark.parametrize("filing,eps,revenue", [
    (_filing(revenue=33e6, eps_adjusted=0.44), 0.44, 33e6),
    (_filing(eps_adjusted=0.44), 0.44, None),
    (_filing(revenue=33e6), None, 33e6),
    (_filing(eps_gaap=-0.19), -0.19, None),
], ids=["revenue_and_eps", "eps_only", "revenue_only", "gaap_only"])
def test_the_companys_own_figure_shows_a_stored_row_of_zeros_was_unfilled(filing, eps, revenue):
    """Neither zero speaks for the report once the release says otherwise: "EPS missed consensus
    by 100%" over a verified $0.44 was the only push that report sent."""
    rec = _zeros(filing=filing)
    shown_eps, shown_revenue = els.eps_metric(rec), els.revenue_metric(rec)
    assert (shown_eps.actual, shown_revenue.actual) == (eps, revenue)
    assert els.SOURCE_FMP not in (shown_eps.source, shown_revenue.source)


def test_a_consensus_turning_material_shows_a_stored_row_of_zeros_was_unfilled():
    """Through the fold, `test_a_row_of_zeros_that_gave_way_leaves_no_trace` pins what shows."""
    rec = replace(_zeros(), revenue_guard_estimate=16.6e9)
    assert els.eps_metric(rec) == els.LiveMetric(0.41)
    assert els.revenue_metric(rec).actual is None


@pytest.mark.parametrize("rec,eps,revenue", [
    (_zeros(), 0.0, 0.0),
    (_zeros(filing=_filing(eps_adjusted=0.0)), 0.0, 0.0),
    (_rec(revenue_estimate=30e6, fmp_eps=0.0, fmp_revenue=17e6, filing=_filing(eps_adjusted=0.44)),
     0.0, 17e6),
    (_rec(revenue_estimate=30e6, fmp_eps=0.0, filing=_filing(eps_adjusted=0.44)), 0.0, None),
    (_rec(revenue_estimate=30e6, fmp_eps=-0.2, fmp_revenue=0.0, filing=_filing(revenue=33e6)),
     -0.2, 33e6),
], ids=["a_pre_revenue_report", "the_release_says_zero_too", "a_real_breakeven",
        "a_breakeven_with_no_revenue_on_the_row", "a_real_eps_beside_a_zero_taken_off"])
def test_every_other_zero_reads_as_it_did(rec, eps, revenue):
    assert (els.eps_metric(rec).actual, els.revenue_metric(rec).actual) == (eps, revenue)


def test_a_zero_eps_is_a_real_breakeven_report():
    m = els.eps_metric(_rec(eps_estimate=-0.02, fmp_eps=0.0))
    assert m.actual == 0.0


# ── card fields ──────────────────────────────────────────────────────────────


def test_a_card_with_no_record_reads_upcoming_with_its_date():
    assert els.card_fields(None, NEXT) == {"earnings_status": "upcoming",
                                           "report_date": "2026-10-30"}


def test_card_fields_carry_the_figures_and_the_preliminary_flag():
    rec = _rec(filing=_filing(revenue=17.1e9),
               checked_at=datetime(2026, 10, 29, 20, 5, tzinfo=timezone.utc))
    snap = els.snapshot_of(rec, REPORT, _et(REPORT, 16, 5))
    fields = els.card_fields(snap, REPORT)
    assert fields["earnings_status"] == "live"
    assert fields["revenue_actual"] == 17.1e9 and fields["eps_actual"] is None
    assert fields["results_preliminary"] is True
    assert fields["results_checked_at"] == "2026-10-29T20:05:00Z"
    assert fields["revenue_surprise_pct"] == pytest.approx(3.01, abs=0.01)


#: What `EarningsLiveDates.parse` reads: `ISO8601DateFormatter` takes whole seconds or exactly
#: three fractional digits, so Python's six would leave the sheet with no "updated" line.
_WHOLE_SECONDS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


@pytest.mark.parametrize("when,expected", [
    (datetime(2026, 10, 29, 20, 5, 7, tzinfo=timezone.utc), "2026-10-29T20:05:07Z"),
    (datetime(2026, 10, 29, 20, 5, 7, 123456, tzinfo=timezone.utc), "2026-10-29T20:05:07Z"),
    (datetime(2026, 10, 29, 20, 5, 7, 1, tzinfo=timezone.utc), "2026-10-29T20:05:07Z"),
    (datetime(2026, 10, 29, 23, 59, 59, 999999, tzinfo=timezone.utc), "2026-10-29T23:59:59Z"),
    (datetime(2026, 10, 29, 20, 5, 7, 500000), "2026-10-29T20:05:07Z"),
    (datetime(2026, 10, 29, 16, 5, 7, 250000, tzinfo=ET), "2026-10-29T20:05:07Z"),
], ids=["whole_second", "microseconds", "one_microsecond", "never_rounds_into_the_next_day",
        "naive_reads_as_utc", "another_zone"])
def test_a_stamp_reaches_the_wire_in_whole_seconds(when, expected):
    assert els.iso_utc(when) == expected


def test_no_stamp_stays_no_stamp():
    assert els.iso_utc(None) is None


def test_the_card_and_the_sheet_carry_the_loops_own_clock_in_whole_seconds():
    """`checked_at` comes from `datetime.now`, so it almost always has microseconds."""
    at = datetime(2026, 10, 29, 20, 5, 7, 654321, tzinfo=timezone.utc)
    rec = _rec(filing=_filing(revenue=17.1e9, accepted_at=at), checked_at=at)
    snap = els.snapshot_of(rec, REPORT, _et(REPORT, 16, 5))
    payload = els.live_payload("INTC", REPORT, snap, company_name=None, eps_estimate=None,
                               revenue_estimate=None, extras=None, today=REPORT)
    for stamp in (els.card_fields(snap, REPORT)["results_checked_at"], payload["checked_at"],
                  payload["filing"]["accepted_at"]):
        assert _WHOLE_SECONDS.fullmatch(stamp), stamp


def test_the_preliminary_flag_is_absent_until_something_landed():
    snap = els.snapshot_of(_rec(), REPORT, _et(REPORT, 9))
    assert els.card_fields(snap, REPORT)["results_preliminary"] is None


# ── history extras ───────────────────────────────────────────────────────────


def _q(label, fiscal, actual, estimate, surprise=None, has_estimate=True):
    return SimpleNamespace(quarter=label, fiscal_date=fiscal, actual_value=actual,
                           estimate_value=estimate, surprise_percent=surprise,
                           has_estimate=has_estimate)


def _response():
    eps = [
        _q("Q2 '25", "2025-06-28", 0.10, 0.01, 900.0),
        _q("Q3 '25", "2025-09-27", 0.23, 0.01, 2200.0),
        _q("Q4 '25", "2025-12-27", 0.15, 0.08, 87.5),
        _q("Q1 '26", "2026-03-28", 0.13, 0.11, 18.18),
        _q("Q2 '26", "2026-06-27", 0.20, 0.21, -4.76),
        _q("Q3 '26", "2026-09-26", None, 0.41),                 # the one being reported
        _q("Q4 '26", "2026-12-26", None, 0.45),                 # future
    ]
    rev = [
        _q("Q3 '25", "2025-09-27", 13.7e9, 13.1e9),
        _q("Q2 '26", "2026-06-27", 13.5e9, 13.4e9),
        _q("Q3 '26", "2026-09-26", None, 16.6e9),
    ]
    return SimpleNamespace(eps_quarters=eps, revenue_quarters=rev, degraded=[])


def test_the_last_four_quarters_exclude_the_one_being_reported():
    extras = els.history_extras_from(_response(), REPORT)
    assert [q.quarter for q in extras.last_quarters] == ["Q3 '25", "Q4 '25", "Q1 '26", "Q2 '26"]
    assert [q.result for q in extras.last_quarters] == ["beat", "beat", "beat", "missed"]


def test_the_year_ago_quarter_is_the_same_fiscal_quarter():
    extras = els.history_extras_from(_response(), REPORT)
    assert extras.year_ago_quarter == "Q3 '25"
    assert (extras.year_ago_revenue, extras.year_ago_eps) == (13.7e9, 0.23)
    assert extras.year_ago_eps_comparable is True


def test_a_year_ago_gaap_eps_is_never_compared_with_an_adjusted_actual():
    resp = _response()
    resp.eps_quarters[1] = _q("Q3 '25", "2025-09-27", 0.90, 0.90, None, has_estimate=False)
    extras = els.history_extras_from(resp, REPORT)
    assert extras.year_ago_eps_comparable is False
    payload = els.live_payload("INTC", REPORT, None, company_name="Intel",
                               eps_estimate=0.41, revenue_estimate=16.6e9, extras=extras,
                               today=REPORT)
    assert payload["year_ago"]["eps"] is None and payload["year_ago"]["eps_growth_pct"] is None


def test_yoy_growth_needs_two_positive_figures():
    assert els.yoy_growth_pct(17.1e9, 13.7e9) == pytest.approx(24.82, abs=0.01)
    assert els.yoy_growth_pct(0.10, -0.05) is None
    assert els.yoy_growth_pct(None, 13.7e9) is None


# ── payload ──────────────────────────────────────────────────────────────────


def _sheet(snap, *, found: bool = True) -> dict:
    return els.live_payload("INTC", REPORT, snap, company_name=None, eps_estimate=None,
                            revenue_estimate=None, extras=None, today=REPORT, found=found)


def test_the_payload_carries_the_snapshots_poll():
    live = _sheet(els.snapshot_of(_rec(), REPORT, _et(REPORT, 9)))
    assert live["status"] == "live" and live["poll_seconds"] == 30
    filed = _sheet(els.snapshot_of(_filed(), REPORT, _et(REPORT, 16, 5)))
    assert filed["status"] == "reported" and filed["poll_seconds"] == 120
    done = els.snapshot_of(_rec(fmp_eps=0.44, fmp_revenue=17.1e9), REPORT, _et(REPORT, 17))
    assert _sheet(done)["status"] == "reported" and _sheet(done)["poll_seconds"] is None


def test_a_sheet_with_no_snapshot_never_polls():
    assert _sheet(None)["poll_seconds"] is None
    assert _sheet(None, found=False)["poll_seconds"] is None


# ── what the loop still asks FMP, and how often the sheet asks again ─────────

FRIDAY, SATURDAY, SUNDAY, MONDAY = (date(2026, 11, 6), date(2026, 11, 7), date(2026, 11, 8),
                                    date(2026, 11, 9))
#: The trading day before Thanksgiving, the holiday, and the half day after it.
WEDNESDAY, THANKSGIVING, BLACK_FRIDAY = (date(2026, 11, 25), date(2026, 11, 26),
                                         date(2026, 11, 27))


def _poll(rec: els._Record, at: datetime):
    return els.snapshot_of(rec, at.date(), at).poll_seconds


def test_a_filing_complete_report_polls_until_fmp_confirms():
    rec = _filed()
    snap = els.snapshot_of(rec, REPORT, _et(REPORT, 16, 5))
    assert (snap.status, snap.preliminary, snap.poll_seconds) == (els.STATUS_REPORTED, True, 120)
    assert _poll(rec, _et(REPORT, 21, 59)) == 120
    assert _poll(rec, _et(REPORT, 22)) is None
    assert _poll(rec, _et(NEXT, 11, 59)) == 120
    assert _poll(rec, _et(NEXT, 12)) is None
    confirmed = replace(rec, fmp_eps=0.45, fmp_revenue=17.2e9)
    snap = els.snapshot_of(confirmed, NEXT, _et(NEXT, 8))
    assert (snap.preliminary, snap.poll_seconds) == (False, None)
    assert _poll(_rec(fmp_eps=0.44, fmp_revenue=17.1e9), _et(REPORT, 17)) is None


_WINDOW_CASES = [
    # The report's own day.
    (REPORT, _et(REPORT, 5, 59), False),
    (REPORT, _et(REPORT, 6), True),
    (REPORT, _et(REPORT, 21, 59), True),
    (REPORT, _et(REPORT, 22), False),
    # The next trading morning.
    (REPORT, _et(NEXT, 5, 59), False),
    (REPORT, _et(NEXT, 6), True),
    (REPORT, _et(NEXT, 11, 59), True),
    (REPORT, _et(NEXT, 12), False),
    # A weekend waits for Monday, however many mornings pass.
    (FRIDAY, _et(SATURDAY, 8), False),
    (FRIDAY, _et(SUNDAY, 8), False),
    (FRIDAY, _et(MONDAY, 5, 59), False),
    (FRIDAY, _et(MONDAY, 6), True),
    (FRIDAY, _et(MONDAY, 11, 59), True),
    (FRIDAY, _et(MONDAY, 12), False),
    # So does a holiday, and a half day is a trading day.
    (WEDNESDAY, _et(THANKSGIVING, 8), False),
    (WEDNESDAY, _et(BLACK_FRIDAY, 8), True),
    # A closed day's own report is hot on its day and never again.
    (SATURDAY, _et(SATURDAY, 8), True),
    (SATURDAY, _et(SUNDAY, 8), False),
    (SATURDAY, _et(MONDAY, 8), False),
    (THANKSGIVING, _et(THANKSGIVING, 8), True),
    (THANKSGIVING, _et(BLACK_FRIDAY, 8), False),
    # A later date is nobody's window yet.
    (NEXT, _et(REPORT, 8), False),
]


@pytest.mark.parametrize("day,at,hot", _WINDOW_CASES)
def test_the_hot_window_is_the_report_day_and_the_next_trading_morning(day, at, hot):
    assert els.fmp_window_open(day, at) is hot


@pytest.mark.parametrize("at,pays", [
    (_et(REPORT, 0), False),
    (_et(REPORT, 5, 59), False),
    (_et(REPORT, 6), True),
    (_et(REPORT, 22), True),
    (_et(REPORT, 23, 59), True),
    (_et(SATURDAY, 10), False),
    (_et(THANKSGIVING, 10), False),
    (_et(BLACK_FRIDAY, 10), True),
])
def test_the_tick_pays_for_the_memo_from_six_to_midnight_on_a_trading_day(at, pays):
    assert els.tick_refreshes_memo(at) is pays


@pytest.mark.parametrize("day,at,hot", [c for c in _WINDOW_CASES if c[0] <= c[1].date()])
def test_a_preliminary_report_polls_exactly_while_the_hot_window_is_open(day, at, hot):
    """Polling past the window would re-read a record nothing is updating."""
    rec = _filed(day=day)
    assert els.snapshot_of(rec, at.date(), at).status == els.STATUS_REPORTED
    assert _poll(rec, at) == (120 if hot else None)


def test_a_live_report_polls_at_the_live_pace_with_a_filing_figure_in_or_not():
    assert _poll(_rec(), _et(REPORT, 9)) == 30
    part = _rec(filing=_filing(revenue=17.1e9))
    snap = els.snapshot_of(part, REPORT, _et(REPORT, 16, 5))
    assert (snap.status, snap.preliminary, snap.poll_seconds) == (els.STATUS_LIVE, True, 30)


def test_a_next_morning_report_with_one_filing_figure_and_a_held_one_still_polls():
    rec = _rec(fmp_eps=0.041, filing=_filing(revenue=17.1e9))
    snap = els.snapshot_of(rec, NEXT, _et(NEXT, 8))
    assert (snap.status, snap.preliminary, snap.poll_seconds) == (
        els.STATUS_REPORTED, True, 120)
    assert snap.eps.checking is True


@pytest.mark.parametrize("rec,at", [
    (_rec(day=NEXT), _et(REPORT, 12)),
    (_rec(), _et(REPORT, 5)),
    (_rec(), _et(NEXT, 8)),
    (_rec(filing=_filing()), _et(NEXT, 8)),
], ids=["upcoming", "before_the_window", "awaiting", "a_filing_with_no_figures"])
def test_a_report_with_nothing_to_confirm_never_polls(rec, at):
    assert _poll(rec, at) is None


@pytest.mark.parametrize("rec,wanted", [
    (_rec(), True),
    (_rec(fmp_eps=0.44), True),
    (_rec(fmp_eps=0.44, fmp_revenue=17.1e9), False),
    (_filed(), True),
    (_rec(filing=_filing(revenue=17.1e9, eps_gaap=0.38)), True),
    (_rec(fmp_eps=0.44, filing=_filing(revenue=17.1e9)), True),
    (_rec(fmp_revenue=17.1e9, filing=_filing(eps_adjusted=0.44)), True),
    (_rec(fmp_eps=0.45, fmp_revenue=17.2e9, filing=_filing(revenue=17.1e9, eps_adjusted=0.44)),
     False),
    (_rec(revenue_estimate=None, fmp_eps=0.44), False),
    (_rec(fmp_eps=0.041, fmp_revenue=17.1e9), True),
    (_rec(fmp_eps=0.44, fmp_revenue=90e9), True),
    (_rec(fmp_eps=0.041, fmp_revenue=17.1e9, filing=_filing(eps_adjusted=0.44)), True),
    (_rec(fmp_eps=0.44, fmp_revenue=1.69e9, filing=_filing(revenue=16.9e9)), True),
], ids=["pending", "a_due_revenue", "confirmed", "filing_complete", "filing_gaap_only",
        "filing_revenue_left", "filing_eps_left", "confirmed_over_a_filing",
        "no_revenue_consensus", "held_eps", "held_revenue", "filing_beside_a_held_eps",
        "filing_beside_a_held_revenue"])
def test_fmp_is_wanted_until_every_figure_shown_is_its_own(rec, wanted):
    assert els.needs_fmp(els.eps_metric(rec), els.revenue_metric(rec)) is wanted


def test_a_ticker_with_no_report_in_the_window_answers_none():
    payload = els.live_payload("ZZZZ", REPORT, None, company_name=None, eps_estimate=None,
                               revenue_estimate=None, extras=None, today=REPORT, found=False)
    assert payload["status"] == els.STATUS_NONE


def test_the_payload_carries_the_filing_link_and_guidance_only_from_the_release():
    rec = _rec(filing=_filing(revenue=17.1e9, guidance="Q4 revenue of $13.3–14.3 billion."))
    snap = els.snapshot_of(rec, REPORT, _et(REPORT, 16, 5))
    payload = els.live_payload("INTC", REPORT, snap, company_name=None, eps_estimate=None,
                               revenue_estimate=None, extras=None, today=REPORT)
    assert payload["filing"]["url"] == "https://sec/idx"
    assert payload["guidance"].startswith("Q4 revenue")
    assert payload["preliminary"] is True


@pytest.mark.parametrize("day,following", [
    (date(2026, 10, 28), date(2026, 10, 29)),    # an ordinary Wednesday
    (date(2026, 10, 30), date(2026, 11, 2)),     # Friday, over the weekend
    (date(2026, 10, 31), date(2026, 11, 2)),     # from a Saturday
    (date(2026, 11, 25), date(2026, 11, 27)),    # over Thanksgiving, to its half day
    (date(2026, 12, 24), date(2026, 12, 28)),    # over Christmas and the weekend
    (date(2026, 4, 2), date(2026, 4, 6)),        # over Good Friday and the weekend
    (date(2026, 1, 16), date(2026, 1, 20)),      # over MLK Day
    (date(2026, 10, 9), date(2026, 10, 12)),     # Columbus Day: SEC shut, the market open
    (date(2026, 11, 10), date(2026, 11, 11)),    # Veterans Day, the same
    (date(2026, 11, 26), date(2026, 11, 27)),    # from a holiday itself
])
def test_the_next_trading_day_steps_over_every_closed_day(day, following):
    assert next_trading_day(day) == following
    assert is_trading_day(following)


def test_the_next_trading_day_sees_an_unscheduled_closure(monkeypatch):
    monkeypatch.setattr(market_hours, "_OBSERVED_CLOSURES", {(2026, 10, 29)})
    assert next_trading_day(date(2026, 10, 28)) == date(2026, 10, 30)


def test_the_next_trading_day_and_the_previous_one_are_mirrors():
    for offset in range(800):
        day = date(2026, 1, 1) + timedelta(days=offset)
        if is_trading_day(day):
            assert previous_trading_day(next_trading_day(day)) == day


def test_a_calendar_with_no_trading_day_ends_instead_of_hanging(monkeypatch):
    monkeypatch.setattr(market_hours, "is_trading_day", lambda d: False)
    day = date(2026, 10, 29)
    assert next_trading_day(day) == day + timedelta(days=11)
