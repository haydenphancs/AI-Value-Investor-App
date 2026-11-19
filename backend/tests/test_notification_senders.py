"""Sender SELECTION logic — the half where the bugs actually live.

None of these tests touch FMP, Supabase or APNs. They feed synthetic upstream payloads
to the pure selectors and assert what comes out, because "which rows deserve a
notification" is the decision that is wrong in production while the APNs POST works
fine.

Two failures this file exists to prevent are already on the record in this repo:

  * the `or ""` operator-precedence bug, which turns a date filter into a no-op and
    would announce a fund's entire historical book as this week's activity;
  * a NaN/None arithmetic input silently answering False for every comparison, which
    DISABLES a materiality gate rather than tripping it.
"""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.earnings_live_service import (
    BASIS_ADJUSTED,
    SOURCE_FILING,
    SOURCE_FMP,
    STATUS_REPORTED,
    LiveMetric,
    LiveSnapshot,
)
from app.services.notification_kinds import KIND_CONGRESS_TRADE, KIND_WHALE_13F
from app.services.notification_senders.earnings_sender import (
    MIN_SURPRISE,
    FoldedRecord,
    in_result_window,
    result_copy,
    select_results,
    select_upcoming,
    surprise_pct,
    upcoming_copy,
)
from app.services.notification_senders.smart_money_sender import (
    MIN_INSIDER_AMOUNT,
    _max_created_at,
    _recent_whale_rows,
    _whale_kind,
    insider_copy,
    notable_insider_trade,
    whale_copy,
)

TODAY = date(2026, 8, 7)
TOMORROW = TODAY + timedelta(days=1)
YESTERDAY = TODAY - timedelta(days=1)


# ══ EARNINGS ═════════════════════════════════════════════════════════════════

def _cal(symbol="AAPL", when=None, time="amc", est=None, act=None):
    row = {"symbol": symbol, "date": (when or TOMORROW).isoformat(), "time": time}
    if est is not None:
        row["epsEstimated"] = est
    if act is not None:
        row["epsActual"] = act
    return row


# ── upcoming ─────────────────────────────────────────────────────────────────

def test_tomorrows_reporters_are_selected():
    picked = select_upcoming([_cal("AAPL"), _cal("MSFT")], TODAY)
    assert {p[0] for p in picked} == {"AAPL", "MSFT"}


def test_todays_reporters_are_NOT_announced_as_upcoming():
    """The job runs after the close, so a today-dated row has already happened. Telling
    someone to expect a print that reported eight hours ago is worse than silence."""
    assert select_upcoming([_cal("AAPL", when=TODAY, time="bmo"),
                            _cal("MSFT", when=TODAY, time="amc")], TODAY) == []


def test_next_weeks_reporters_are_not_upcoming_yet():
    assert select_upcoming([_cal(when=TODAY + timedelta(days=6))], TODAY) == []


def test_a_symbol_listed_twice_produces_one_notification():
    """FMP can carry two rows for one company across a reschedule. Two banners for one
    earnings date is the noise that gets notifications switched off."""
    picked = select_upcoming([_cal("AAPL", time="amc"), _cal("AAPL", time="bmo")], TODAY)
    assert len(picked) == 1


@pytest.mark.parametrize("bad", [
    {"symbol": "AAPL"},                                    # no date at all
    {"symbol": "AAPL", "date": ""},
    {"symbol": "AAPL", "date": "not-a-date"},
    {"symbol": "AAPL", "date": "08/08/2026"},              # non-ISO
    {"symbol": "", "date": "2026-08-08"},                  # no symbol
    {"date": "2026-08-08"},                                # no symbol key
])
def test_unusable_calendar_rows_are_skipped_not_crashed(bad):
    assert select_upcoming([bad], TODAY) == []


def test_a_full_timestamp_date_is_normalised():
    row = {"symbol": "AAPL", "date": f"{TOMORROW.isoformat()}T00:00:00.000Z", "time": "amc"}
    picked = select_upcoming([row], TODAY)
    assert picked and picked[0][1] == TOMORROW.isoformat()


@pytest.mark.parametrize("raw,expect_phrase", [
    ("bmo", "before market open"),
    ("amc", "after market close"),
    ("", None),
    ("--", None),
    ("garbage", None),
])
def test_unknown_timing_tokens_degrade_to_no_phrase(raw, expect_phrase):
    """`timing_sentence` returns None for unspecified so the clause is DROPPED rather
    than defaulted to 'after market close' — a guess here is misinformation."""
    picked = select_upcoming([_cal(time=raw)], TODAY)
    _, body = upcoming_copy("AAPL", picked[0][2])
    if expect_phrase:
        assert expect_phrase in body
    else:
        assert body == "AAPL is scheduled to report tomorrow."


# ── surprise math ────────────────────────────────────────────────────────────

def test_a_clean_beat_is_a_positive_fraction():
    assert surprise_pct({"epsEstimated": 1.00, "epsActual": 1.20}) == pytest.approx(0.20)


def test_a_miss_is_negative():
    assert surprise_pct({"epsEstimated": 1.00, "epsActual": 0.80}) == pytest.approx(-0.20)


def test_a_negative_estimate_uses_the_MAGNITUDE_as_the_denominator():
    """A loss-making company narrowing a -$1.00 estimate to -$0.50 is a 50% BEAT.
    Dividing by the signed estimate would report -50% and invert the verb."""
    assert surprise_pct({"epsEstimated": -1.00, "epsActual": -0.50}) == pytest.approx(0.5)


@pytest.mark.parametrize("est,act", [
    (None, 1.0), (1.0, None), (None, None),
    (float("nan"), 1.0), (1.0, float("nan")),
    (float("inf"), 1.0), (1.0, float("-inf")),
    ("n/a", 1.0), (1.0, "n/a"),
])
def test_a_missing_or_non_finite_side_yields_no_surprise(est, act):
    """None, not 0.0. FMP emits NaN/Infinity JSON tokens on thin names, and NaN silently
    answers False for every comparison — DISABLING the materiality gate rather than
    tripping it."""
    assert surprise_pct({"epsEstimated": est, "epsActual": act}) is None


@pytest.mark.parametrize("est", [0.0, 0.001, -0.001, 0.009])
def test_a_near_zero_estimate_has_no_honest_percentage(est):
    """`est != 0` is not enough: $0.001 vs $0.40 is a 39,900% 'surprise' — arithmetically
    true and completely meaningless."""
    assert surprise_pct({"epsEstimated": est, "epsActual": 0.40}) is None


def test_an_exactly_at_the_floor_estimate_still_divides():
    assert surprise_pct({"epsEstimated": 0.01, "epsActual": 0.02}) == pytest.approx(1.0)


# ── results ──────────────────────────────────────────────────────────────────

def test_a_material_surprise_is_selected():
    picked = select_results([_cal(when=TODAY, est=1.0, act=1.5)], TODAY)
    assert picked and picked[0][0] == "AAPL"


def test_an_in_line_print_is_not_news():
    """Analysts cluster tightly around consensus; a 1% beat is a rounding difference."""
    assert select_results([_cal(when=TODAY, est=1.00, act=1.01)], TODAY) == []


def test_the_materiality_floor_is_inclusive_at_the_boundary():
    below = select_results([_cal(when=TODAY, est=1.0, act=1.0 + MIN_SURPRISE * 0.9)], TODAY)
    at = select_results([_cal(when=TODAY, est=1.0, act=1.0 + MIN_SURPRISE)], TODAY)
    assert below == [] and at != []


def test_yesterdays_after_close_print_is_still_selected():
    """An after-close print lands in the evening and a calendar row can take until the
    next morning to carry epsActual."""
    assert select_results([_cal(when=YESTERDAY, est=1.0, act=1.5)], TODAY) != []


def test_last_weeks_earnings_are_not_re_announced():
    old = _cal(when=TODAY - timedelta(days=6), est=1.0, act=2.0)
    assert select_results([old], TODAY) == []


@pytest.mark.parametrize("days_old,kept", [(-1, False), (0, True), (1, True), (2, False), (9, False)])
def test_the_result_window_is_today_and_calendar_yesterday(days_old, kept):
    assert in_result_window(TODAY - timedelta(days=days_old), TODAY) is kept


def test_the_result_window_is_calendar_days_not_trading_days():
    monday, friday, sunday = date(2026, 11, 9), date(2026, 11, 6), date(2026, 11, 8)
    assert not in_result_window(friday, monday), "Friday is the previous trading day, not yesterday"
    assert in_result_window(sunday, monday)


def _with_revenue(row, rev, rev_est):
    return {**row, "revenueActual": rev, "revenueEstimated": rev_est}


@pytest.mark.parametrize("rev,rev_est", [
    (0, 16.6e9), (-5e6, 16.6e9), (0, None), (0, 0), (0, 100e6),
], ids=["zero", "negative", "no_consensus", "zero_consensus", "at_the_floor"])
@pytest.mark.parametrize("est", [0.41, -0.30], ids=["read_as_a_miss", "read_as_a_beat"])
def test_an_eps_of_zero_beside_a_refused_revenue_is_no_result(rev, rev_est, est):
    """FMP's unfilled shape. A row with nothing in it must not go out as "missed by 100%"."""
    row = _with_revenue(_cal(when=TODAY, est=est, act=0), rev, rev_est)
    assert surprise_pct(row) in (-1.0, 1.0), "the arithmetic alone would send it"
    assert select_results([row], TODAY) == []


@pytest.mark.parametrize("act,est,rev,rev_est,surprise", [
    (0, -0.02, 0, 30e6, 1.0),               # a pre-revenue name can report exactly nothing
    (0, -0.02, 0, 99.9e6, 1.0),             # the fold's own floor
    (0, -0.02, 17.1e9, 16.6e9, 1.0),        # a real breakeven beside a real revenue
    (0, 0.41, None, 16.6e9, -1.0),          # with no revenue on the row it is one too
    (0.60, 0.41, 0, 16.6e9, 0.4634),        # an unfilled revenue says nothing about the EPS
    (0.60, 0.41, None, None, 0.4634),
], ids=["tiny_consensus", "under_the_floor", "real_revenue", "no_revenue", "real_eps",
        "plain_row"])
def test_every_other_row_still_sends_as_it_did(act, est, rev, rev_est, surprise):
    row = _with_revenue(_cal(when=TODAY, est=est, act=act), rev, rev_est)
    (picked,) = select_results([row], TODAY)
    assert picked[:2] == ("AAPL", TODAY.isoformat())
    assert picked[2] == pytest.approx(surprise, abs=1e-4)


@pytest.mark.parametrize("zero_first", [True, False])
def test_an_unfilled_row_leaves_its_reported_twin_to_speak(zero_first):
    zero = _with_revenue(_cal(when=TODAY, est=0.41, act=0), 0, 16.6e9)
    real = _with_revenue(_cal(when=TODAY, est=0.41, act=0.60), 17.1e9, 16.6e9)
    (picked,) = select_results([zero, real] if zero_first else [real, zero], TODAY)
    assert picked[2] == pytest.approx(0.4634, abs=1e-4)


def test_the_legacy_eps_key_is_read_the_same_way():
    row = {"symbol": "AAPL", "date": TODAY.isoformat(), "epsEstimated": 0.41, "eps": 0,
           "revenueActual": 0, "revenueEstimated": 16.6e9}
    assert select_results([row], TODAY) == []


def test_a_row_with_no_eps_is_not_an_unfilled_eps(caplog):
    """No result, and nothing logged as refused: there was no EPS to refuse."""
    row = {"symbol": "AAPL", "date": TODAY.isoformat(), "epsEstimated": 0.41,
           "epsActual": None, "revenueActual": 0, "revenueEstimated": 16.6e9}
    with caplog.at_level("WARNING", logger="app.services.notification_senders.earnings_sender"):
        assert select_results([row], TODAY) == []
    assert caplog.records == []


def test_an_unfilled_row_is_logged_with_its_symbol_and_day(caplog):
    row = _with_revenue(_cal(when=TODAY, est=0.41, act=0), 0, 16.6e9)
    with caplog.at_level("WARNING", logger="app.services.notification_senders.earnings_sender"):
        select_results([row], TODAY)
    (line,) = [r.getMessage() for r in caplog.records]
    assert "AAPL" in line and TODAY.isoformat() in line


def test_a_row_with_no_actual_yet_is_not_a_result():
    """Tomorrow's row is in the same response; it must not produce a 'results are out'
    notification just because it has an estimate."""
    assert select_results([_cal(when=TOMORROW, est=1.0)], TODAY) == []


# ── a report FMP lists twice ─────────────────────────────────────────────────
#
# The owner's choice, 2026-11-11: "Follow the record". The live record takes a report's EPS from
# the row it ranks strongest (results first, then FMP's own `lastUpdated`), and the pass reads
# that row too, so the banner says what the sheet it opens says. Another row of the same report
# never speaks for it, even when the record's own row says nothing. These read the rows alone,
# as the pass does with no live record to follow; the record's own reading is further down.

OLDER, NEWER = f"{TODAY.isoformat()} 13:40:00", f"{TODAY.isoformat()} 13:50:00"
_LISTED_ORDER = {"argnames": "newer_first", "argvalues": [False, True],
                 "ids": ["older_listed_first", "newer_listed_first"]}
_SENDER_LOG = "app.services.notification_senders.earnings_sender"


def _listed(est, act, touched, *, when=TODAY):
    """One of a report's rows: its (estimate, actual) beside a real revenue, touched then."""
    return {**_with_revenue(_cal(when=when, est=est, act=act), 17.1e9, 16.6e9),
            "lastUpdated": touched}


def _in_order(older, newer, newer_first):
    return [newer, older] if newer_first else [older, newer]


@pytest.mark.parametrize(**_LISTED_ORDER)
@pytest.mark.parametrize("older,newer,surprise", [
    ((0.41, 0.60), (0.70, 0.60), -0.1429),
    ((0.70, 0.60), (0.41, 0.60), 0.4634),
    ((0.41, 0.30), (0.41, 0.60), 0.4634),
], ids=["the_newer_consensus_says_missed", "the_newer_consensus_says_beat",
        "a_stale_miss_beside_the_newer_beat"])
def test_a_report_listed_twice_is_told_from_the_row_its_record_reads(
        older, newer, surprise, newer_first):
    rows = _in_order(_listed(*older, OLDER), _listed(*newer, NEWER), newer_first)
    (picked,) = select_results(rows, TODAY)
    assert picked[:2] == ("AAPL", TODAY.isoformat())
    assert picked[2] == pytest.approx(surprise, abs=1e-4)


@pytest.mark.parametrize(**_LISTED_ORDER)
@pytest.mark.parametrize("newer", [
    _listed(0.41, 0.40, NEWER),
    _listed(None, 0.44, NEWER),
    _listed(1.65, 0.169, NEWER),
], ids=["under_the_bar", "no_estimate_of_its_own", "in_doubt"])
def test_a_twin_past_the_bar_never_speaks_for_a_report_whose_own_row_says_nothing(
        newer, newer_first):
    """The newer row is the one the record reads: a 2% miss, a figure with no estimate of its own
    to divide by, a figure in doubt. The stale row's 46% beat must not speak for it."""
    older = _listed(0.41, 0.60, OLDER)
    assert select_results([older], TODAY) != [], "alone, the stale row would be told"
    assert select_results(_in_order(older, newer, newer_first), TODAY) == []


def test_a_report_passed_over_leaves_the_symbols_report_under_another_date_free_to_speak():
    """One push per symbol per pass, and today's report, read as its record reads it, is under
    the bar: yesterday's report speaks."""
    today = [_listed(0.41, 0.60, OLDER), _listed(0.41, 0.40, NEWER)]
    yesterday = _listed(1.0, 1.5, OLDER, when=YESTERDAY)
    (picked,) = select_results([*today, yesterday], TODAY)
    assert picked[1] == YESTERDAY.isoformat()


@pytest.mark.parametrize("zero_first", [True, False])
def test_a_zero_beside_a_reported_revenue_never_goes_out_as_a_100_percent_move(zero_first):
    """Alone, a zero against a tiny consensus is a pre-revenue name's real report. Beside a row
    with a revenue, that row holds the report's results, and the record keeps no EPS."""
    zero = _with_revenue(_cal(when=TODAY, est=-0.02, act=0), 0, 30e6)
    revenue = _with_revenue(_cal(when=TODAY, est=-0.02), 31e6, 30e6)
    assert select_results([zero], TODAY) != [], "alone, the zero is a result"
    assert select_results([zero, revenue] if zero_first else [revenue, zero], TODAY) == []


@pytest.mark.parametrize("zero_first", [True, False])
def test_a_zero_is_judged_by_the_largest_consensus_any_row_of_its_report_states(zero_first):
    """The record reads every row's consensus before it folds one: a material one makes the
    zero FMP's unfilled field, whichever row states it."""
    zero = _with_revenue(_cal(when=TODAY, est=-0.02, act=0), 0, 30e6)
    pending = _with_revenue(_cal(when=TODAY, est=-0.02), None, 16.6e9)
    assert select_results([zero, pending] if zero_first else [pending, zero], TODAY) == []


@pytest.mark.parametrize("revenue_first", [False, True])
def test_an_eps_row_beside_a_newer_revenue_only_twin_is_told_from_the_eps_row(revenue_first):
    """The card's one row is the newer twin, which has only the revenue: the EPS, and the push,
    come from the other row."""
    eps_row = {**_cal(when=TODAY, est=0.41, act=0.44), "lastUpdated": OLDER}
    revenue_row = {**_with_revenue(_cal(when=TODAY, est=0.41), 17.1e9, 16.6e9),
                   "lastUpdated": NEWER}
    rows = [revenue_row, eps_row] if revenue_first else [eps_row, revenue_row]
    (picked,) = select_results(rows, TODAY)
    assert picked[2] == pytest.approx(0.0732, abs=1e-4)
    (picked,) = select_results(rows, TODAY, record=_record_reading(0.44, 0.41, took=eps_row))
    assert picked[2] == pytest.approx(0.0732, abs=1e-4)


def _twin_line(caplog) -> str:
    (line,) = [r.getMessage() for r in caplog.records]
    assert line.startswith(f"earnings notifications: AAPL {TODAY.isoformat()} is listed more "
                           "than once — ")
    return line


@pytest.mark.parametrize(**_LISTED_ORDER)
@pytest.mark.parametrize("older,newer,told,passed_over", [
    ((0.41, 0.60), (0.70, 0.60), "EPS 0.6 vs estimate 0.7", "EPS 0.6 vs estimate 0.41"),
    ((0.41, 0.30), (0.41, 0.60), "EPS 0.6 vs estimate 0.41", "EPS 0.3 vs estimate 0.41"),
], ids=["two_estimates", "two_actuals"])
def test_a_twin_passed_over_is_logged_once_with_what_was_told_first(
        caplog, newer_first, older, newer, told, passed_over):
    """How often FMP lists one report with two verdicts is unknown: each pass says so, once, with
    the figures it told and the ones it passed over, in that order."""
    rows = _in_order(_listed(*older, OLDER), _listed(*newer, NEWER), newer_first)
    with caplog.at_level("WARNING", logger=_SENDER_LOG):
        (picked,) = select_results(rows, TODAY)
    line = _twin_line(caplog)
    assert line.endswith(f"— told, from the row its live record would read: {told}; passed over: "
                         f"{passed_over}")


@pytest.mark.parametrize(**_LISTED_ORDER)
def test_a_twin_passed_over_beside_a_report_not_told_says_it_was_not_told_and_why(
        caplog, newer_first):
    rows = _in_order(_listed(0.41, 0.60, OLDER), _listed(0.41, 0.40, NEWER), newer_first)
    with caplog.at_level("WARNING", logger=_SENDER_LOG):
        assert select_results(rows, TODAY) == []
    assert _twin_line(caplog).endswith(
        "— not told (no surprise past the bar), from the row its live record would read: "
        "EPS 0.4 vs estimate 0.41; passed over: EPS 0.6 vs estimate 0.41")


@pytest.mark.parametrize(**_LISTED_ORDER)
def test_a_report_listed_twice_with_the_same_figures_is_told_once_and_logs_nothing(
        caplog, newer_first):
    rows = _in_order(_listed(0.41, 0.60, OLDER), _listed(0.41, 0.60, NEWER), newer_first)
    with caplog.at_level("WARNING", logger=_SENDER_LOG):
        (picked,) = select_results(rows, TODAY)
    assert picked[2] == pytest.approx(0.4634, abs=1e-4)
    assert caplog.records == []


# ── a report FMP lists twice, told as its live record reads it ───────────────
#
# With live results on, the pass folds what it read into the live records before it tells anyone
# (`_fold_into_live_records`), and hands `select_results` a reader of them, each with the row the
# fold took its EPS from. A report FMP lists more than once is told as its record reads it, which
# can hold what no row of the answer says: the consensus read while the report was pending, or
# one the zero rules weigh a row of zeros against. So is a report whose EPS is 0, which the zero
# rules weigh against what its record already holds. Only an EPS this answer gave the record is
# told. Any other report listed once is told from its row, as the pass always was: what that
# leaves untold is `earnings_live_sender`'s list of what off costs.

def _record_reading(eps, est, *, took=None, source=SOURCE_FMP, checking=False, reads_as=None,
                    symbol="AAPL", day=TODAY):
    """A reader of one report's live record, which shows `eps` against `est` after a fold that
    took its EPS from the row `took`, None when it took none. Notes each ask."""
    snap = LiveSnapshot(
        symbol=symbol, day=day, status=STATUS_REPORTED, company_name=None,
        eps=LiveMetric(est, eps, None, source if eps is not None else None,
                       BASIS_ADJUSTED if eps is not None else None, checking=checking),
        revenue=LiveMetric(16.6e9), preliminary=False, checked_at=None, filing=None,
        reads_as=reads_as)
    asked = []

    def read(sym, when):
        asked.append((sym, when))
        return FoldedRecord(snap, took) if (sym, when) == (symbol, day) else None

    read.asked = asked
    return read


@pytest.mark.parametrize(**_LISTED_ORDER)
@pytest.mark.parametrize("newer,record,surprise", [
    ((None, 0.60), (0.60, 0.41), 0.4634),
    ((None, 0.44), (0.44, 0.41), 0.0732),
    ((None, 0.60), (0.60, 0.70), -0.1429),
], ids=["the_record_keeps_the_consensus_the_row_lacks", "a_newer_figure_without_one",
        "the_record_keeps_an_older_consensus"])
def test_a_report_listed_twice_is_told_as_its_live_record_reads_it(
        newer, record, surprise, newer_first):
    """The newer row has no estimate of its own, so the record keeps the one it read while the
    report was pending, and the sheet shows that surprise. So does the push."""
    newer_row = _listed(*newer, NEWER)
    rows = _in_order(_listed(0.41, 0.60, OLDER), newer_row, newer_first)
    (picked,) = select_results(rows, TODAY, record=_record_reading(*record, took=newer_row))
    assert picked[:2] == ("AAPL", TODAY.isoformat())
    assert picked[2] == pytest.approx(surprise, abs=1e-4)


@pytest.mark.parametrize("row,told", [
    (_listed(None, 0.60, NEWER), None),
    (_listed(0.41, 0.60, NEWER), 0.4634),
], ids=["no_estimate_of_its_own", "its_own_estimate"])
def test_a_report_listed_once_with_an_eps_other_than_0_is_told_from_its_row_alone(row, told):
    """The pass as it always was, its record never read: the record reads such a row as it reads
    alone. A row filled with no estimate of its own stays untold, a cost of the live result push
    being off, whatever the record holds."""
    read = _record_reading(0.60, 0.70, took=row)
    picked = select_results([row], TODAY, record=read)
    assert [p[2] for p in picked] == ([] if told is None else [pytest.approx(told, abs=1e-4)])
    assert read.asked == []


@pytest.mark.parametrize(**_LISTED_ORDER)
@pytest.mark.parametrize("record", [
    dict(eps=0.30, est=0.41),
    dict(eps=None, est=0.41, checking=True),
    dict(eps=None, est=0.41),
    dict(eps=0.60, est=0.41, source=SOURCE_FILING),
    dict(eps=0.60, est=0.41, reads_as=YESTERDAY),
], ids=["a_figure_it_held_before", "being_confirmed", "no_figure", "the_filings_figure",
        "read_as_the_report_before"])
def test_a_report_listed_twice_is_told_only_with_an_eps_this_answer_gave_its_record(
        caplog, record, newer_first):
    """FMP's own figure from this answer, on the record's own report, or nobody is told: a
    figure the record kept from before is one FMP has since taken back."""
    newer = _listed(0.41, 0.60, NEWER)
    rows = _in_order(_listed(0.41, 0.60, OLDER), newer, newer_first)
    with caplog.at_level("WARNING", logger=_SENDER_LOG):
        assert select_results(rows, TODAY, record=_record_reading(**record, took=newer)) == []
    assert "not told (its live record does not show this answer's EPS 0.6)" in _twin_line(caplog)


@pytest.mark.parametrize(**_LISTED_ORDER)
def test_a_report_listed_twice_with_no_record_to_follow_is_told_from_its_row(newer_first):
    """Live results off, or the pass could not fold that day: the row the record would read."""
    rows = _in_order(_listed(0.41, 0.60, OLDER), _listed(0.70, 0.60, NEWER), newer_first)
    for record in (None, _record_reading(0.60, 0.41, symbol="MSFT")):
        (picked,) = select_results(rows, TODAY, record=record)
        assert picked[2] == pytest.approx(-0.1429, abs=1e-4)


@pytest.mark.parametrize(**_LISTED_ORDER)
def test_a_report_listed_twice_keeps_the_dropped_digit_guard_off_the_filings_gaap_eps(
        caplog, newer_first):
    """The record shows 0.46 against 0.05 because the filing's GAAP EPS bears it out. The pass
    judges the signature without it and tells nobody: the owner's choice of 2026-11-11, "Keep it
    quiet", for a report listed twice as for one listed once."""
    filled = _listed(0.05, 0.46, OLDER)
    pending = {**_cal(when=TODAY, est=0.05), "lastUpdated": NEWER}
    with caplog.at_level("WARNING", logger=_SENDER_LOG):
        assert select_results(_in_order(filled, pending, newer_first), TODAY,
                              record=_record_reading(0.46, 0.05, took=filled)) == []
    assert "not told (the dropped-digit signature)" in _twin_line(caplog)


def _zeros(revenue_estimate=None):
    """FMP's row of zeros for a small name expected to lose 5 cents a share."""
    return {**_with_revenue(_cal(when=TODAY, est=-0.05, act=0), 0, revenue_estimate),
            "lastUpdated": OLDER}


def _zeros_and_a_twin(zero_first):
    zeros = _zeros()
    twin = {**_with_revenue(_cal(when=TODAY, est=-0.05), None, 95e6), "lastUpdated": OLDER}
    return [zeros, twin] if zero_first else [twin, zeros]


@pytest.mark.parametrize("zero_first", [True, False])
def test_a_row_of_zeros_listed_twice_is_told_only_when_its_record_shows_the_zero(
        caplog, zero_first):
    """The twin's $95M stands behind the zero, read alone. Beside a $105M consensus the record
    read in the morning it is FMP's unfilled row, and the record shows no EPS: no 100% beat."""
    rows = _zeros_and_a_twin(zero_first)
    (zeros,) = [row for row in rows if "epsActual" in row]
    assert [p[2] for p in select_results(rows, TODAY)] == [1.0]
    assert [p[2] for p in select_results(
        rows, TODAY, record=_record_reading(0.0, -0.05, took=zeros))] == [1.0]
    with caplog.at_level("WARNING", logger=_SENDER_LOG):
        assert select_results(rows, TODAY, record=_record_reading(None, -0.05, took=zeros)) == []
    assert _twin_line(caplog).endswith(
        "— not told (its live record does not show this answer's EPS 0.0), from its live record: "
        "EPS None vs estimate -0.05")


@pytest.mark.parametrize("listed", ["beside_a_pending_twin", "the_pending_twin_first", "twice"])
def test_a_row_of_zeros_listed_twice_that_its_record_keeps_is_told_as_the_record_reads_it(
        caplog, listed):
    """No row states a revenue consensus, so read alone the zeros are FMP's unfilled row and say
    nothing. The record kept them against the $50M it read in the morning, under the floor, and
    shows a 100% beat: that is told, and nothing is logged as refused."""
    zeros = _zeros()
    pending = {**_with_revenue(_cal(when=TODAY, est=-0.05), None, None), "lastUpdated": OLDER}
    rows = {"beside_a_pending_twin": [zeros, pending], "the_pending_twin_first": [pending, zeros],
            "twice": [zeros, dict(zeros)]}[listed]
    assert select_results(rows, TODAY) == []
    caplog.clear()
    with caplog.at_level("WARNING", logger=_SENDER_LOG):
        picked = select_results(rows, TODAY, record=_record_reading(0.0, -0.05, took=zeros))
    assert [p[2] for p in picked] == [1.0]
    assert caplog.records == []


@pytest.mark.parametrize(**_LISTED_ORDER)
def test_a_row_of_zeros_listed_twice_beside_a_figure_its_record_holds_is_told_to_nobody(
        caplog, newer_first):
    """Zeros under a tiny consensus beside a pending twin: read alone, a small name's 100% miss.
    The record holds 0.60 and takes no EPS from this answer, so nobody is told, and the line
    says what the record shows."""
    zeros = {**_with_revenue(_cal(when=TODAY, est=0.41, act=0), 0, 30e6), "lastUpdated": OLDER}
    pending = {**_with_revenue(_cal(when=TODAY, est=0.41), None, 30e6), "lastUpdated": NEWER}
    rows = _in_order(zeros, pending, newer_first)
    assert [p[2] for p in select_results(rows, TODAY)] == [-1.0]
    with caplog.at_level("WARNING", logger=_SENDER_LOG):
        assert select_results(rows, TODAY, record=_record_reading(0.60, 0.41)) == []
    assert _twin_line(caplog).endswith(
        "— not told (its live record keeps no EPS from this answer), from its live record: "
        "EPS 0.6 vs estimate 0.41; passed over: EPS 0.0 vs estimate 0.41")


def _only_row_with_an_eps_of_0(case):
    """(the row, its record after the fold, told read alone, told from the record) for a report
    whose only row has an EPS of 0, in cases where what the record already held decides."""
    if case == "kept_on_an_earlier_consensus":
        row = _zeros()
        return row, _record_reading(0.0, -0.05, took=row), [], [1.0]
    if case == "an_estimate_the_row_lacks":
        row = {**_with_revenue(_cal(when=TODAY, act=0), 31e6, 30e6), "lastUpdated": NEWER}
        return row, _record_reading(0.0, -0.05, took=row), [], [1.0]
    if case == "unfilled_beside_a_held_zero":
        row = {**_with_revenue(_cal(when=TODAY, est=-0.30, act=0), None, 105e6),
               "lastUpdated": NEWER}
        return row, _record_reading(None, -0.30, took=row), [1.0], []
    row = {**_with_revenue(_cal(when=TODAY, est=0.41, act=0), 0, 30e6), "lastUpdated": NEWER}
    return row, _record_reading(0.60, 0.41), [-1.0], []


@pytest.mark.parametrize("case", ["kept_on_an_earlier_consensus", "an_estimate_the_row_lacks",
                                  "unfilled_beside_a_held_zero", "refused_beside_a_held_figure"])
def test_a_report_listed_once_with_an_eps_of_0_is_told_as_its_live_record_reads_it(case):
    """The zero rules weigh a 0 against what the record already holds, which the row cannot show.
    Zeros with no consensus of their own, kept on the $50M the record read in the morning: a 100%
    beat. A breakeven filled with no estimate of its own: the record keeps the one it held, as for
    a report listed twice. A 0 with no revenue beside the 0 revenue the record holds, under a
    consensus now material: FMP's unfilled row, so the sheet shows no EPS. Zeros under a tiny
    consensus beside the 0.60 the record holds: no EPS from this answer, and the sheet keeps 0.60.
    The push says what the sheet says."""
    row, record, alone, told = _only_row_with_an_eps_of_0(case)
    assert [p[2] for p in select_results([row], TODAY)] == alone
    assert [p[2] for p in select_results([row], TODAY, record=record)] == told
    assert record.asked == [("AAPL", TODAY)]


@pytest.mark.parametrize("case,line", [
    ("unfilled_beside_a_held_zero",
     "not told (its live record does not show this answer's EPS 0.0), from its live record: "
     "EPS None vs estimate -0.3"),
    ("refused_beside_a_held_figure",
     "not told (its live record keeps no EPS from this answer), from its live record: EPS 0.6 vs "
     "estimate 0.41; passed over: EPS 0.0 vs estimate 0.41"),
], ids=["unfilled_beside_a_held_zero", "refused_beside_a_held_figure"])
def test_a_zero_its_record_does_not_tell_is_logged_once_with_what_the_record_shows(
        caplog, case, line):
    row, record, _alone, _told = _only_row_with_an_eps_of_0(case)
    with caplog.at_level("WARNING", logger=_SENDER_LOG):
        assert select_results([row], TODAY, record=record) == []
    (logged,) = [r.getMessage() for r in caplog.records]
    assert logged == (f"earnings notifications: AAPL {TODAY.isoformat()} is listed once, with an "
                      f"EPS of 0 — {line}")


def test_result_copy_states_direction_and_magnitude_without_a_verdict():
    """It does NOT say whether the surprise is good — a beat on a lowered bar is not
    good news, and a 180-character banner is not going to resolve that."""
    title, body = result_copy("NVDA", 0.42)
    assert "NVDA" in title
    assert "beat" in body and "42%" in body
    for directive in ("buy", "sell", "consider", "act now", "don't miss"):
        assert directive not in body.lower()

    _, miss = result_copy("NVDA", -0.30)
    assert "missed" in miss and "30%" in miss


def test_upcoming_copy_is_informational_only():
    _, body = upcoming_copy("AAPL", "after_close")
    for directive in ("buy", "sell", "consider", "position", "don't miss"):
        assert directive not in body.lower()


# ══ INSIDER ══════════════════════════════════════════════════════════════════

CUTOFF = (TODAY - timedelta(days=3)).isoformat()


def _tx(*, filed=None, name="Jane Doe", kind="P-Purchase", shares=1000, price=200.0):
    return {
        "filingDate": (filed or TODAY).isoformat(),
        "reportingName": name,
        "transactionType": kind,
        "securitiesTransacted": shares,
        "price": price,
    }


def test_a_large_informative_buy_is_selected():
    best = notable_insider_trade([_tx()], cutoff=CUTOFF)
    assert best is not None
    filed, name, action, dollars = best
    assert action == "bought" and dollars == pytest.approx(200_000)


def test_a_trade_below_the_dollar_floor_is_ignored():
    below = _tx(shares=1, price=100.0)   # $100
    assert notable_insider_trade([below], cutoff=CUTOFF) is None


def test_the_dollar_floor_is_inclusive():
    at = _tx(shares=1, price=float(MIN_INSIDER_AMOUNT))
    assert notable_insider_trade([at], cutoff=CUTOFF) is not None


@pytest.mark.parametrize("kind", ["M-Exempt", "F-InKind", "A-Award", "G-Gift", "", "???"])
def test_mechanical_transactions_carry_no_sentiment(kind):
    """Option exercises, tax withholding, awards and gifts are not decisions. Same
    filter the Holders tab applies, via the same shared classifier."""
    assert notable_insider_trade([_tx(kind=kind)], cutoff=CUTOFF) is None


def test_one_decision_split_across_forty_rows_becomes_one_notification():
    """A CFO selling in tranches files many rows for one decision. Forty banners for one
    decision is exactly how an app trains people to disable its notifications."""
    rows = [_tx(kind="S-Sale", shares=100, price=200.0) for _ in range(40)]
    best = notable_insider_trade(rows, cutoff=CUTOFF)
    assert best is not None
    assert best[3] == pytest.approx(40 * 100 * 200.0)   # aggregated, not the largest row


def test_an_old_filing_is_outside_the_window():
    assert notable_insider_trade(
        [_tx(filed=TODAY - timedelta(days=30))], cutoff=CUTOFF
    ) is None


def test_a_late_filing_of_an_old_trade_still_fires():
    """Keying on FILING date, not transaction date: a Form 4 for a trade three days ago
    that files today IS new information."""
    row = _tx(filed=TODAY)
    row["transactionDate"] = (TODAY - timedelta(days=3)).isoformat()
    assert notable_insider_trade([row], cutoff=CUTOFF) is not None


@pytest.mark.parametrize("shares,price", [
    (None, 200.0), (1000, None), (0, 200.0), (1000, 0),
    (-1000, 200.0), (1000, -200.0),
    (float("nan"), 200.0), (1000, float("nan")), (float("inf"), 200.0),
    ("many", 200.0), (1000, "cheap"),
])
def test_unusable_amounts_are_skipped_not_multiplied(shares, price):
    assert notable_insider_trade([_tx(shares=shares, price=price)], cutoff=CUTOFF) is None


@pytest.mark.parametrize("payload", [{}, [], None, "rows", [None], ["x"], [[]]])
def test_a_malformed_upstream_response_degrades_to_none(payload):
    assert notable_insider_trade(payload, cutoff=CUTOFF) is None


def test_a_missing_filing_date_is_skipped():
    row = _tx()
    del row["filingDate"]
    assert notable_insider_trade([row], cutoff=CUTOFF) is None


def test_the_biggest_decision_wins_when_several_qualify():
    # FMP sends `reportingName` SURNAME-FIRST ("ELLISON LAWRENCE JOSEPH"), and the
    # shared `normalize_insider_name` flips it to natural order. The fixtures below are
    # written in FMP's shape so the assertion pins the real pipeline rather than a
    # convenient fiction.
    small = _tx(name="Fry Small", shares=1000, price=200.0)                # $200K
    big = _tx(name="Ellison Lawrence", kind="S-Sale", shares=10_000, price=200.0)  # $2M
    best = notable_insider_trade([small, big], cutoff=CUTOFF)
    assert best[1] == "Lawrence Ellison" and best[2] == "sold"


def test_the_insider_name_reaches_the_copy_in_natural_order():
    """A banner reading 'Ellison Lawrence sold…' is the surname-first raw value leaking
    through — the normalizer exists precisely to stop that."""
    best = notable_insider_trade(
        [_tx(name="ELLISON LAWRENCE JOSEPH", shares=10_000, price=200.0)], cutoff=CUTOFF
    )
    _, body = insider_copy("ORCL", best[1], best[2], best[3])
    assert body.startswith("Lawrence Joseph Ellison ")


def test_a_blank_insider_name_degrades_to_a_readable_label():
    best = notable_insider_trade([_tx(name="", shares=10_000, price=200.0)], cutoff=CUTOFF)
    assert best is not None and best[1] == "Insider"


def test_insider_copy_names_who_and_how_much_without_a_recommendation():
    """Insiders sell for tuition and divorces too — the copy must not imply a signal."""
    _, body = insider_copy("NVDA", "Jane Doe", "sold", 2_400_000)
    assert "Jane Doe" in body and "$2.4M" in body
    for directive in ("follow", "consider", "signal", "bearish", "bullish"):
        assert directive not in body.lower()


# ══ WHALE / CONGRESS ═════════════════════════════════════════════════════════

CUTOFF_DATE = (TODAY - timedelta(days=45)).isoformat()

# What production writes (C13, 2026-09-25). The fixtures here used to date "fresh" rows
# TODAY, a shape no hydrator produces, so they passed against a floor that dropped real
# filings:
#   * a 13F row's `date` is the QUARTER END it describes (FMP `institutional-ownership/
#     dates`), filed up to 45 days later — TODAY (2026-08-07) sits in Q3, so the latest
#     quarter a 13F can describe is Q2, ending 06-30;
#   * a congressional row's `date` is the TRANSACTION date; `disclosure_date` is the news.
Q2_END, Q1_END = "2026-06-30", "2026-03-31"
_FUND = {"name": "Fund", "firm_name": "Fund", "data_source": "13f"}
_MEMBER = {"name": "Member", "firm_name": None, "data_source": "congressional_house"}


def _cutoff_at(now: datetime) -> str:
    """The cutoff exactly as `_run_whale_phase` derives it."""
    from app.utils.market_hours import ET

    return (now.astimezone(ET).date() - timedelta(days=45)).isoformat()


def _13f(ticker="AAPL", when=Q2_END):
    return {"ticker": ticker, "date": when, "amount_range": None, "disclosure_date": None,
            "created_at": "2026-08-07T02:00:00Z", "whales": dict(_FUND)}


def _ptr(ticker="AAPL", traded="2026-07-20", disclosed="2026-08-05"):
    return {"ticker": ticker, "date": traded, "disclosure_date": disclosed,
            "amount_range": "$1,001 - $15,000", "created_at": "2026-08-07T02:00:00Z",
            "whales": dict(_MEMBER)}


def test_the_backfill_guard_drops_quarter_old_trades():
    """THE trap. First hydration of a new whale inserts hundreds of quarter-old filings
    with a brand-new created_at. Windowing on created_at alone would announce a fund's
    entire historical book as this week's activity — one notification per position."""
    assert _recent_whale_rows([_13f(when=Q1_END)], cutoff_date=CUTOFF_DATE) == []
    assert _recent_whale_rows([_13f(when="2026-01-15")], cutoff_date=CUTOFF_DATE) == []


def test_a_genuinely_recent_trade_survives_the_guard():
    assert len(_recent_whale_rows([_13f()], cutoff_date=CUTOFF_DATE)) == 1
    assert len(_recent_whale_rows([_ptr()], cutoff_date=CUTOFF_DATE)) == 1


def test_the_guard_is_not_a_no_op():
    """Pins the OPERATOR PRECEDENCE fix. `x.get("date") or "" < cutoff` parses as
    `x.get("date") or ("" < cutoff)` — truthy for ANY non-empty date, i.e. no guard at
    all. It looks like a filter and does nothing. If someone reintroduces that form, the
    mixed batch below comes back with both rows instead of one."""
    rows = [_13f("OLD", "2020-01-01"), _13f("NEW", Q2_END)]
    kept = _recent_whale_rows(rows, cutoff_date=CUTOFF_DATE)
    assert [r["ticker"] for r in kept] == ["NEW"]
    rows = [_ptr("OLD", "2020-01-01", "2020-02-01"), _ptr("NEW")]
    kept = _recent_whale_rows(rows, cutoff_date=CUTOFF_DATE)
    assert [r["ticker"] for r in kept] == ["NEW"]


def test_a_row_with_no_trade_date_is_kept():
    """created_at is then the only signal available. Dropping it would silently lose
    congressional rows whose transaction date FMP omits."""
    assert len(_recent_whale_rows([{"ticker": "AAPL", "date": None}], cutoff_date=CUTOFF_DATE)) == 1
    assert len(_recent_whale_rows([{"ticker": "AAPL"}], cutoff_date=CUTOFF_DATE)) == 1
    assert len(_recent_whale_rows([_ptr(traded=None, disclosed=None)], cutoff_date=CUTOFF_DATE)) == 1


# ── C13: each row type is dated by what its `date` actually means ──────────────

DEADLINE_PLUS_ONE = datetime(2026, 8, 15, 22, 0, tzinfo=timezone.utc)   # 18:00 ET run


def test_a_deadline_day_13f_is_kept_the_evening_after():
    """Q2 ends 06-30 and is due 08-14. A deadline-day filer is hydrated at 02:00 UTC and
    first read by the 18:00 ET run on 08-15 — when the old 45-day floor on `date` was
    07-01, one day above every row of the filing. The cursor then moved past them."""
    assert _cutoff_at(DEADLINE_PLUS_ONE) == "2026-07-01"
    kept = _recent_whale_rows([_13f()], cutoff_date=_cutoff_at(DEADLINE_PLUS_ONE))
    assert len(kept) == 1


def test_an_older_quarter_is_still_dropped_at_the_same_instant():
    assert _recent_whale_rows([_13f(when=Q1_END)], cutoff_date=_cutoff_at(DEADLINE_PLUS_ONE)) == []


def test_a_late_disclosed_congress_trade_is_kept_on_its_disclosure_date():
    """Traded 06-20, disclosed 08-10 (past the 45-day limit, which is common), read
    08-12: the transaction date is under the floor (06-28), the disclosure is not."""
    at = datetime(2026, 8, 12, 22, 0, tzinfo=timezone.utc)
    row = _ptr(traded="2026-06-20", disclosed="2026-08-10")
    assert len(_recent_whale_rows([row], cutoff_date=_cutoff_at(at))) == 1


def test_a_congress_trade_disclosed_long_ago_is_dropped():
    at = datetime(2026, 8, 12, 22, 0, tzinfo=timezone.utc)
    row = _ptr(traded="2026-05-01", disclosed="2026-06-20")          # > 45 days before
    assert _recent_whale_rows([row], cutoff_date=_cutoff_at(at)) == []


def test_a_pre_076_congress_row_falls_back_to_its_transaction_date():
    """No `disclosure_date`, no range: the data source says congress, and the 45-day
    window applies to `date` as before — never the looser 13F quarter floor."""
    fresh = {"ticker": "AAPL", "date": "2026-07-30", "whales": dict(_MEMBER)}
    stale = {"ticker": "MSFT", "date": "2026-05-15", "whales": dict(_MEMBER)}   # in Q2
    kept = _recent_whale_rows([fresh, stale], cutoff_date=CUTOFF_DATE)
    assert [r["ticker"] for r in kept] == ["AAPL"]


@pytest.mark.parametrize("today,kept,dropped", [
    (date(2026, 2, 17), "2025-12-31", "2025-09-30"),   # year boundary: Q1 → last Q4
    (date(2026, 2, 17), "2025-12-30", "2025-09-30"),   # the hydrators' Q4 fallback date
    (date(2026, 5, 20), "2026-03-30", "2025-12-31"),   # the hydrators' Q1 fallback date
    (date(2026, 9, 30), "2026-06-30", "2026-03-31"),   # last day of a quarter
    (date(2026, 10, 1), "2026-09-30", "2026-06-30"),   # first day of the next one
])
def test_the_13f_floor_is_the_previous_quarters_first_day(today, kept, dropped):
    cutoff = (today - timedelta(days=45)).isoformat()
    rows = [_13f("KEEP", kept), _13f("DROP", dropped)]
    assert [r["ticker"] for r in _recent_whale_rows(rows, cutoff_date=cutoff)] == ["KEEP"]


def test_an_unusable_cutoff_falls_back_to_the_strict_floor(caplog):
    """Internal input, so garbage is a bug: fail to the OLD (strict) floor, loudly —
    never to no floor at all."""
    with caplog.at_level("WARNING"):
        kept = _recent_whale_rows([_13f(when=Q2_END), _13f("NODATE", None)],
                                  cutoff_date="garbage")
    # "2026-…" < "garbage": every dated row is held back, exactly as the raw string
    # comparison always did; the undated row keeps its documented created_at-only path.
    assert [r["ticker"] for r in kept] == ["NODATE"]
    assert any("unusable whale cutoff" in r.getMessage() for r in caplog.records)


def test_the_whale_read_selects_the_disclosure_date():
    """The congress gate reads `disclosure_date`; a select that omits it silently degrades
    every congress row to its transaction date again."""
    import inspect
    import re

    from app.services.notification_senders import smart_money_sender as sm

    src = "\n".join(ln.split("#", 1)[0] for ln in inspect.getsource(sm._run_whale_phase).splitlines())
    select = re.search(r"\.select\((.*?)\)\s*\.gt\(", src, re.S)
    assert select and "disclosure_date" in select.group(1)


@pytest.mark.parametrize("payload", [None, {}, "rows", [None], ["x"], [1]])
def test_malformed_whale_payloads_degrade_to_empty(payload):
    assert _recent_whale_rows(payload, cutoff_date=CUTOFF_DATE) == []


@pytest.mark.parametrize("source,expected", [
    ("13f", KIND_WHALE_13F),
    ("13F", KIND_WHALE_13F),
    ("congressional_house", KIND_CONGRESS_TRADE),
    ("congressional_senate", KIND_CONGRESS_TRADE),
    ("CONGRESSIONAL_SENATE", KIND_CONGRESS_TRADE),
])
def test_data_source_routes_to_the_right_kind(source, expected):
    assert _whale_kind(source) == expected


@pytest.mark.parametrize("source", ["manual", "", None, "unknown", "13g", 13])
def test_an_unknown_data_source_is_skipped_never_defaulted(source):
    """Defaulting to institutional would be silent (13F ships OFF); defaulting the other
    way would push congress-preference users about something else entirely."""
    assert _whale_kind(source) is None


def test_a_forty_position_filing_names_a_few_tickers_and_counts_the_rest():
    tickers = [f"T{i}" for i in range(40)]
    title, body = whale_copy("Pelosi", "bought", tickers, "$1.5M")
    assert "T0, T1, T2" in title and "+37 more" in title
    assert "$1.5M" in body


def test_whale_copy_is_informational():
    _, body = whale_copy("Bridgewater", "sold", ["AAPL"], "$50M")
    for directive in ("follow", "copy", "consider", "you should"):
        assert directive not in body.lower()


# ── the ingest cursor ────────────────────────────────────────────────────────

def _dt(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def test_the_cursor_advances_past_rows_the_guard_REJECTED():
    """Deliberately advanced past rejected rows: they were evaluated and correctly
    declined, so re-reading them tomorrow is pure waste. Advancing only past ACCEPTED
    rows would make a quiet week re-scan the same backfill forever."""
    fallback = _dt("2026-08-01T00:00:00Z")
    rows = [
        {"created_at": "2026-08-07T10:00:00Z", "date": "2020-01-01"},   # rejected
        {"created_at": "2026-08-07T09:00:00Z", "date": "2026-08-07"},   # kept
    ]
    assert _max_created_at(rows, fallback) == _dt("2026-08-07T10:00:00Z")


def test_the_cursor_never_goes_backwards():
    fallback = _dt("2026-08-07T00:00:00Z")
    rows = [{"created_at": "2026-01-01T00:00:00Z"}]
    assert _max_created_at(rows, fallback) == fallback


@pytest.mark.parametrize("rows", [
    [], None, "rows", [{}], [{"created_at": ""}], [{"created_at": "garbage"}], [None],
])
def test_an_unusable_cursor_input_falls_back(rows):
    fallback = _dt("2026-08-07T00:00:00Z")
    assert _max_created_at(rows, fallback) == fallback


def test_a_naive_timestamp_is_read_as_utc():
    """Supabase can return a naive stamp. Comparing naive to aware raises TypeError, so
    the whole pass would die on one row."""
    fallback = _dt("2026-08-01T00:00:00Z")
    got = _max_created_at([{"created_at": "2026-08-07T10:00:00"}], fallback)
    assert got == datetime(2026, 8, 7, 10, 0, tzinfo=timezone.utc)
