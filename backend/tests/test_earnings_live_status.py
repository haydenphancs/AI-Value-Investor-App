"""Live earnings results: the PURE rules — statuses, per-metric figures and their guards,
the card fields, and the history extras (owner request 2026-10-29, INTC's report day).

The card goes LIVE on report day and the sheet shows estimate vs actual for EPS and revenue
the moment EACH lands. Everything a user reads there is decided by the functions below, so
they are pinned directly, with hand-built records — no clock, no network.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import app.services.earnings_live_service as els
from app.services.earnings_filing_service import FilingFigures
from app.utils.market_hours import ET, is_trading_day, previous_trading_day

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


def test_the_payload_asks_for_a_poll_only_while_live():
    snap = els.snapshot_of(_rec(), REPORT, _et(REPORT, 9))
    live = els.live_payload("INTC", REPORT, snap, company_name=None, eps_estimate=None,
                            revenue_estimate=None, extras=None, today=REPORT)
    assert live["status"] == "live" and live["poll_seconds"] == 30
    done = els.snapshot_of(_rec(fmp_eps=0.44, fmp_revenue=17.1e9), REPORT, _et(REPORT, 17))
    reported = els.live_payload("INTC", REPORT, done, company_name=None, eps_estimate=None,
                                revenue_estimate=None, extras=None, today=REPORT)
    assert reported["status"] == "reported" and reported["poll_seconds"] is None


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
