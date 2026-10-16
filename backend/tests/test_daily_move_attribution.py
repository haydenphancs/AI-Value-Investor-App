"""Same-day move attribution: what the widget is allowed to claim caused today's move.

Pure module, so every branch is testable with plain values — no network, no Supabase,
per `.claude/rules/testing.md`.

The failure this file exists to prevent is a **confident lie**: a stale rating, a
multi-day rally narrative, or a sector claim that is really a coincidence of small
numbers, printed under a red percentage where a reader takes it as the cause and cannot
interrogate it. Most tests here therefore assert that we say NOTHING, or say something
weaker than the caller might hope.

`test_the_achr_case_*` are the regression: ACHR fell 5.02% on 2026-08-14 with σ 4.55%,
its industry down only 1.23%, no company news, and its most recent analyst action dated
2026-05-12. Every plausible wrong answer was available; the right answer was "no
identifiable cause, and here is how it compares with its industry."
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.daily_move_attribution import (
    Attribution,
    CauseKind,
    attribute,
    compute_gap,
    describe_no_cause,
    detect_analyst_action,
    detect_company_news,
    detect_earnings,
    detect_group_move,
)

TODAY = date(2026, 8, 14)
YESTERDAY = date(2026, 8, 13)


def _attr(**kw) -> Attribution:
    kw.setdefault("ticker", "TEST")
    kw.setdefault("today", TODAY)
    return attribute(**kw)


# ── The ACHR regression ───────────────────────────────────────────────


def test_the_achr_case_finds_no_cause_and_says_so_usefully():
    a = _attr(
        ticker="ACHR",
        change_percent=-5.02152,
        sigma_daily=0.0454823,
        industry_name="Aerospace & Defense",
        industry_change_percent=-1.23,
        market_change_percent=-0.15,
        grade_rows=[
            {
                "date": "2026-05-12",
                "gradingCompany": "Canaccord Genuity",
                "previousGrade": "Buy",
                "newGrade": "Buy",
                "action": "maintain",
            }
        ],
        had_news=True,
    )
    assert a.kind is CauseKind.NONE
    assert a.tag is None
    # It must still be informative: the industry comparison is the useful true fact.
    assert "Aerospace & Defense" in a.detail
    assert "moved far more" in a.detail
    assert a.considered == ["earnings", "analyst", "company_news", "group"]


def test_the_achr_case_does_not_call_a_4x_industry_underperformance_a_sector_move():
    """−5.0% against an industry −1.2% is the opposite of "moved with the sector"."""
    assert (
        detect_group_move("ACHR", -5.02, "Aerospace & Defense", -1.23, -0.15) is None
    )


def test_a_three_month_old_rating_is_never_todays_cause():
    assert (
        detect_analyst_action(
            "ACHR",
            [{"date": "2026-05-12", "gradingCompany": "X", "action": "downgrade"}],
            TODAY,
        )
        is None
    )


# ── Earnings ──────────────────────────────────────────────────────────


def test_earnings_beat_today():
    a = detect_earnings("AAPL", 3.0, {"date": "2026-08-14", "epsActual": 1.4, "epsEstimated": 1.25}, TODAY)
    assert a.kind is CauseKind.EARNINGS and a.tag == "Earnings Beat"
    assert "12.0%" in a.detail and "this morning" in a.detail


def test_earnings_wording_follows_the_stamped_session_not_the_wall_clock():
    """Pre-market Tuesday the widget attributes MONDAY's session (`today` = Monday). Monday's
    BMO print is not "this morning" on Tuesday, and a Friday AMC print viewed Monday
    pre-market is not "after yesterday's close" — three days ago. The sibling detectors
    already took `session_word`; the headline line must not assert a false WHEN."""
    monday = date(2026, 9, 14)
    bmo = {"date": "2026-09-14", "epsActual": 1.5, "epsEstimated": 1.3, "time": "bmo"}
    live = detect_earnings("ORCL", 9.0, bmo, today=monday)
    assert live is not None and "this morning" in live.detail
    stamped = detect_earnings("ORCL", 9.0, bmo, today=monday, session_word="on Mon")
    assert stamped is not None
    assert "this morning" not in stamped.detail and "before Monday's open" in stamped.detail

    # A t-1 AMC print, attributed for a stamped (not live) session.
    tuesday = date(2026, 9, 15)
    amc_prior = {"date": "2026-09-14", "epsActual": 1.0, "epsEstimated": 1.2, "time": "amc"}
    live = detect_earnings("ORCL", -6.0, amc_prior, today=tuesday)
    assert live is not None and "after yesterday's close" in live.detail
    stamped = detect_earnings("ORCL", -6.0, amc_prior, today=tuesday, session_word="on Tue")
    assert stamped is not None
    assert "yesterday" not in stamped.detail and "after the prior close" in stamped.detail


def test_earnings_miss_from_last_night():
    a = detect_earnings("AAPL", -3.0, {"date": "2026-08-13", "epsActual": 1.0, "epsEstimated": 1.25}, TODAY)
    assert a.tag == "Earnings Miss"
    assert "after yesterday's close" in a.detail


def test_a_scheduled_but_unreported_row_is_not_an_event():
    """A CALENDAR ROW IS A SCHEDULE, NOT AN EVENT.

    This replaces `test_earnings_without_numbers_still_reports_the_event`, which pinned
    the opposite and was wrong — it contradicted its own sibling three tests below
    (`test_future_dated_earnings_are_not_a_cause`: "An upcoming report has not happened
    yet and cannot have moved the stock"). The calendar lists what is DUE, so a
    today-dated row is routinely a company reporting TONIGHT.

    Live shape of the bug: at 11:00 the tile rendered badge "Earnings" + "NVDA reported
    earnings this morning" about a company that had not reported at all — and because
    earnings is top of the precedence chain, it also discarded the real cause.
    """
    assert detect_earnings("AAPL", 3.0, {"date": "2026-08-14"}, TODAY) is None
    assert (
        detect_earnings(
            "AAPL", 3.0,
            {"date": "2026-08-14", "time": "amc", "epsEstimated": 1.01},
            TODAY,
        )
        is None
    )


def test_a_reported_print_this_morning_is_an_event():
    a = detect_earnings(
        "AAPL", 3.0,
        {"date": "2026-08-14", "time": "bmo", "epsActual": 1.4, "epsEstimated": 1.0},
        TODAY,
    )
    assert a is not None and a.tag == "Earnings Beat"
    assert "this morning" in a.detail


def test_a_same_day_after_close_print_cannot_explain_the_session_before_it():
    """Numbers that landed at 16:05 did not move the 09:30-16:00 tape."""
    assert (
        detect_earnings(
            "AAPL", 3.0,
            {"date": "2026-08-14", "time": "amc", "epsActual": 1.4, "epsEstimated": 1.0},
            TODAY,
        )
        is None
    )


def test_yesterdays_after_close_print_is_todays_cause():
    a = detect_earnings(
        "AAPL", 3.0,
        {"date": "2026-08-13", "time": "amc", "epsActual": 1.4, "epsEstimated": 1.0},
        TODAY,
    )
    assert a is not None and "after yesterday's close" in a.detail


def test_yesterdays_before_open_print_belongs_to_yesterdays_session():
    """A BMO print is ~30 hours old — it moved YESTERDAY's tape, not today's."""
    assert (
        detect_earnings(
            "AAPL", 3.0,
            {"date": "2026-08-13", "time": "bmo", "epsActual": 1.4, "epsEstimated": 1.0},
            TODAY,
        )
        is None
    )


def test_an_in_line_print_is_neither_a_beat_nor_a_miss():
    """"beat EPS estimates by 0.0%" contradicts itself in a single sentence."""
    a = detect_earnings(
        "AAPL", 3.0,
        {"date": "2026-08-14", "epsActual": 1.0, "epsEstimated": 1.0},
        TODAY,
    )
    assert a is not None and a.tag == "Earnings"
    assert "beat" not in a.detail and "missed" not in a.detail


def test_a_miss_reports_a_positive_magnitude():
    a = detect_earnings(
        "AAPL", -3.0,
        {"date": "2026-08-14", "epsActual": 0.8, "epsEstimated": 1.0},
        TODAY,
    )
    assert a is not None and a.tag == "Earnings Miss"
    assert "-" not in a.detail.split("by")[1]  # "missed ... by 20.0%", never "by -20.0%"


def test_stale_earnings_are_not_todays_cause():
    assert detect_earnings("AAPL", 3.0, {"date": "2026-08-01", "epsActual": 1.4, "epsEstimated": 1.0}, TODAY) is None


def test_future_dated_earnings_are_not_a_cause():
    """An upcoming report has not happened yet and cannot have moved the stock."""
    assert detect_earnings("AAPL", 3.0, {"date": "2026-08-20"}, TODAY) is None


@pytest.mark.parametrize("row", [None, {}, "nonsense", 42, {"date": "bad"}])
def test_malformed_earnings_rows_are_ignored(row):
    assert detect_earnings("AAPL", 3.0, row, TODAY) is None


def test_a_zero_estimate_does_not_divide_by_zero():
    a = detect_earnings("X", 3.0, {"date": "2026-08-14", "epsActual": 0.5, "epsEstimated": 0.0}, TODAY)
    assert a is not None and a.tag == "Earnings"   # no surprise computed, event still real


# ── Analyst actions ───────────────────────────────────────────────────


def test_a_downgrade_today_is_the_cause():
    a = detect_analyst_action(
        "CRM",
        [{"date": "2026-08-14", "gradingCompany": "Morgan Stanley",
          "previousGrade": "Overweight", "newGrade": "Equal Weight", "action": "downgrade"}],
        TODAY,
    )
    assert a.kind is CauseKind.ANALYST and a.tag == "Analyst Downgrade"
    assert "Morgan Stanley" in a.detail and "Equal Weight" in a.detail


@pytest.mark.parametrize("action", ["maintain", "maintains", "reiterate", "hold", "Maintains"])
def test_a_maintained_rating_is_not_a_cause(action):
    """"Maintain" is the ABSENCE of a change — it cannot explain a 5% move."""
    assert (
        detect_analyst_action(
            "X", [{"date": "2026-08-14", "gradingCompany": "Y", "action": action}], TODAY
        )
        is None
    )


def test_the_first_dated_action_wins_over_older_ones():
    a = detect_analyst_action(
        "X",
        [
            {"date": "2026-08-14", "gradingCompany": "New", "action": "upgrade", "newGrade": "Buy"},
            {"date": "2026-01-01", "gradingCompany": "Old", "action": "downgrade"},
        ],
        TODAY,
    )
    assert "New" in a.detail and a.tag == "Analyst Upgrade"


def test_an_unknown_action_verb_is_skipped_not_guessed():
    assert detect_analyst_action("X", [{"date": "2026-08-14", "action": "sideways"}], TODAY) is None


def test_a_real_cut_mislabelled_maintain_is_still_a_downgrade():
    """FMP labels genuine cuts "maintain"; the grades are the ground truth.

    Substring-matching the raw `action` dropped these, and the SECTOR detector then
    fired instead — substituting a false explanation for a true one, which is worse
    than saying nothing. `_analyst_common.normalize_fmp_action` exists precisely to
    rank-compare previousGrade against newGrade when the label cannot be trusted, and
    is already pinned by `test_analyst_actions.py`.
    """
    a = detect_analyst_action(
        "X",
        [{"date": "2026-08-14", "gradingCompany": "RBC", "action": "maintain",
          "previousGrade": "Outperform", "newGrade": "Sector Perform"}],
        TODAY,
    )
    assert a is not None and a.tag == "Analyst Downgrade"
    assert "RBC" in a.detail


def test_a_true_reiteration_is_still_not_a_cause():
    """Same rating on both sides really is the absence of a change."""
    assert (
        detect_analyst_action(
            "X",
            [{"date": "2026-08-14", "gradingCompany": "RBC", "action": "maintain",
              "previousGrade": "Outperform", "newGrade": "Outperform"}],
            TODAY,
        )
        is None
    )


@pytest.mark.parametrize("rows", [None, [], ["junk"], [None], [{"date": None}]])
def test_malformed_grade_rows_are_ignored(rows):
    assert detect_analyst_action("X", rows, TODAY) is None


# ── Company news ──────────────────────────────────────────────────────


def test_a_classified_headline_becomes_the_cause():
    a = detect_company_news("BIIB", [("FDA Approval", "FDA approves Biogen's new therapy")])
    assert a.kind is CauseKind.COMPANY_NEWS and a.tag == "FDA Approval"
    assert a.detail.endswith(".")


@pytest.mark.parametrize("entries", [None, [], [("", "x")], [("tag", "")], ["bad"], [(1,)]])
def test_malformed_or_empty_news_yields_nothing(entries):
    assert detect_company_news("X", entries) is None


# ── Group moves ───────────────────────────────────────────────────────


def test_a_genuine_sector_day_is_named():
    a = detect_group_move("XYZ", -2.4, "Semiconductors", -2.1, -0.3)
    assert a.kind is CauseKind.SECTOR and "Semiconductors" in a.detail


def test_the_market_is_the_fallback_when_the_industry_does_not_fit():
    a = detect_group_move("XYZ", -1.8, "Utilities", -0.1, -1.6)
    assert a.kind is CauseKind.MARKET


def test_a_tiny_group_move_can_never_explain_anything():
    """−0.3% "in line with" −0.2% is a coincidence of small numbers, not a cause."""
    assert detect_group_move("X", -0.3, "Utilities", -0.2, -0.2) is None


def test_opposite_directions_are_not_a_group_move():
    assert detect_group_move("X", -3.0, "Tech", +2.5, +2.0) is None


def test_a_stock_that_massively_outruns_its_group_is_not_a_group_move():
    assert detect_group_move("X", -9.0, "Tech", -1.5, -0.5) is None


@pytest.mark.parametrize("bad", [None, float("nan"), float("inf"), "x"])
def test_unusable_group_values_are_ignored(bad):
    assert detect_group_move("X", -3.0, "Tech", bad, bad) is None


# ── The gap split ─────────────────────────────────────────────────────


def test_a_dominant_gap_is_detected():
    gap, intraday, dominant = compute_gap(94.2, 100.0, -6.0)
    assert gap == pytest.approx(-5.8, abs=0.01)
    assert intraday == pytest.approx(-0.2, abs=0.01)
    assert dominant is True


def test_a_small_gap_is_not_dominant():
    _, _, dominant = compute_gap(99.9, 100.0, -6.0)
    assert dominant is False


def test_a_faded_gap_is_not_called_dominant():
    """Opened +3%, closed −1%: the gap did NOT carry the day, it reversed."""
    _, _, dominant = compute_gap(103.0, 100.0, -1.0)
    assert dominant is False


@pytest.mark.parametrize(
    "op, pc, chg",
    [(None, 100.0, -5.0), (94.0, None, -5.0), (94.0, 0.0, -5.0),
     (94.0, -10.0, -5.0), (float("nan"), 100.0, -5.0), (94.0, 100.0, float("nan")),
     # A batch-quote row can carry `open: 0` for a name that has not traded yet in the
     # session. Guarding only the DENOMINATOR turned that into a gap of exactly
     # -100.0%, which then read as gap-dominant — a number this function's own
     # docstring promises it cannot produce.
     (0.0, 100.0, -5.0), (-3.0, 100.0, -5.0)],
)
def test_unusable_prices_yield_no_gap_rather_than_a_wrong_one(op, pc, chg):
    gap, intraday, dominant = compute_gap(op, pc, chg)
    assert gap is None and intraday is None and dominant is False


# ── Precedence ────────────────────────────────────────────────────────


def test_earnings_outranks_everything_else():
    a = _attr(
        change_percent=-6.0, sigma_daily=0.02,
        earnings_row={"date": "2026-08-14", "epsActual": 1.0, "epsEstimated": 1.5},
        grade_rows=[{"date": "2026-08-14", "gradingCompany": "MS", "action": "downgrade"}],
        classified_news=[("Guidance Cut", "Co cuts guidance")],
        industry_name="Tech", industry_change_percent=-5.5,
    )
    assert a.kind is CauseKind.EARNINGS


def test_an_analyst_action_outranks_news_and_sector():
    a = _attr(
        change_percent=-4.0, sigma_daily=0.02,
        grade_rows=[{"date": "2026-08-14", "gradingCompany": "MS", "action": "downgrade"}],
        classified_news=[("Layoffs", "Co lays off staff")],
        industry_name="Tech", industry_change_percent=-3.8,
    )
    assert a.kind is CauseKind.ANALYST


def test_company_news_outranks_a_sector_move():
    a = _attr(
        change_percent=-4.0, sigma_daily=0.02,
        classified_news=[("M&A", "Co to acquire rival")],
        industry_name="Tech", industry_change_percent=-3.8,
    )
    assert a.kind is CauseKind.COMPANY_NEWS


# ── Context is always present and always true ─────────────────────────


def test_context_is_attached_to_every_outcome():
    for kw in (
        {"earnings_row": {"date": "2026-08-14"}},
        {"grade_rows": [{"date": "2026-08-14", "gradingCompany": "X", "action": "upgrade"}]},
        {"classified_news": [("M&A", "deal")]},
        {"industry_name": "Tech", "industry_change_percent": -2.0},
        {},
    ):
        a = _attr(change_percent=-2.2, sigma_daily=0.02, **kw)
        assert a.context is not None
        assert a.context.change_percent == pytest.approx(-2.2)


def test_z_is_none_not_zero_when_sigma_is_unknown():
    """0.0 would read as "judged, perfectly normal" for a stock we cannot judge."""
    a = _attr(change_percent=-5.0, sigma_daily=None)
    assert a.context.z is None


def test_an_unreadable_move_returns_none_rather_than_a_flat_day():
    for bad in (None, float("nan"), float("inf"), "abc"):
        assert _attr(change_percent=bad) is None


def test_a_detector_that_raises_does_not_break_the_widget():
    """A malformed upstream row must degrade, never 500 the endpoint."""
    class Exploding(list):
        def __iter__(self):
            raise RuntimeError("upstream garbage")

    a = _attr(change_percent=-3.0, sigma_daily=0.02, grade_rows=Exploding())
    assert a is not None
    assert a.kind in (CauseKind.NONE, CauseKind.SECTOR, CauseKind.MARKET)


# ── The no-cause sentence ─────────────────────────────────────────────


def test_the_no_cause_line_is_never_empty():
    from app.services.daily_move_attribution import MoveContext

    for ctx in (
        MoveContext(change_percent=-1.0),
        MoveContext(change_percent=5.0, z=2.2),
        MoveContext(change_percent=-3.0, industry_name="Tech", industry_change_percent=-0.4),
        MoveContext(change_percent=-3.0, gap_percent=-2.8, gap_dominant=True),
    ):
        for had_news in (True, False):
            assert describe_no_cause("X", ctx, had_news).strip()


def test_the_no_cause_line_reports_going_against_the_industry():
    from app.services.daily_move_attribution import MoveContext

    text = describe_no_cause(
        "X", MoveContext(change_percent=+3.0, industry_name="Tech", industry_change_percent=-2.0), False
    )
    assert "the other way" in text


def test_the_no_cause_line_distinguishes_no_news_from_no_catalyst():
    from app.services.daily_move_attribution import MoveContext

    ctx = MoveContext(change_percent=-3.0)
    assert "No company news today" in describe_no_cause("X", ctx, had_news=False)
    assert "No clear catalyst" in describe_no_cause("X", ctx, had_news=True)


def test_an_unchecked_news_lookup_never_asserts_that_there_was_no_news():
    """The third state. Silence about what we could not check, not a false negative.

    "No company news today" is an assertion about the WORLD. Making it because a
    Supabase read errored, or because the sweeper has never covered this ticker, is a
    confident lie the reader cannot distinguish from a real finding — and it is printed
    under a red percentage on a Home Screen, where there is nothing to tap for more.
    """
    from app.services.daily_move_attribution import MoveContext

    ctx = MoveContext(change_percent=-3.0)
    for had_news in (True, False):
        text = describe_no_cause("X", ctx, had_news, news_checked=False)
        assert "No company news today" not in text
        assert "No clear catalyst" not in text
        assert text.strip()


def test_an_unchecked_lookup_still_reports_the_arithmetic_it_does_know():
    """Losing the news must not cost the industry comparison, which is still true."""
    from app.services.daily_move_attribution import MoveContext

    ctx = MoveContext(
        change_percent=-5.0, industry_name="Aerospace & Defense",
        industry_change_percent=-1.2,
    )
    text = describe_no_cause("X", ctx, had_news=False, news_checked=False)
    assert "Aerospace & Defense" in text and "moved far more" in text


def test_attribute_threads_the_unchecked_state_through():
    a = _attr(change_percent=-5.0, sigma_daily=0.045, had_news=False, news_checked=False)
    assert a.kind is CauseKind.NONE
    assert "No company news today" not in a.detail


def test_no_output_anywhere_contains_a_multi_day_window_phrase():
    """The bug that started the rebuild: a "Last 15 Days" narrative on a daily tile."""
    banned = ("last 15 days", "last 30 days", "since ", "past month", "over the last")
    samples = [
        _attr(change_percent=-5.0, sigma_daily=0.045, industry_name="Aerospace & Defense",
              industry_change_percent=-1.2, had_news=True),
        _attr(change_percent=-2.4, sigma_daily=0.02, industry_name="Semis", industry_change_percent=-2.1),
        _attr(change_percent=3.0, sigma_daily=0.016,
              earnings_row={"date": "2026-08-14", "epsActual": 1.4, "epsEstimated": 1.25}),
    ]
    for a in samples:
        low = a.detail.lower()
        assert not any(b in low for b in banned), a.detail


# ── Earnings are TRADING-day aware (2026-09-30) ────────────────────────────────────────
#
# "Last night" is the previous TRADING day's close. A calendar-day gate rejected a Friday
# after-close print on Monday (3 days) and on the Tuesday after a Monday holiday (4 days),
# so the headline fell through to "No clear catalyst" on exactly the days a weekend print
# moves a stock most.

MON = date(2026, 9, 28)
FRI = date(2026, 9, 25)
TUE_AFTER_LABOR_DAY = date(2026, 9, 8)


def _row(day, time=None, actual=1.47, est=1.30):
    r = {"date": day.isoformat(), "epsActual": actual, "epsEstimated": est}
    if time is not None:
        r["time"] = time
    return r


@pytest.mark.parametrize("session_word", ["today", "on Mon"])
def test_a_friday_after_close_print_explains_monday(session_word):
    a = detect_earnings("ORCL", 9.0, _row(FRI, "amc"), MON, session_word=session_word)
    assert a is not None and a.kind is CauseKind.EARNINGS
    assert "after Friday's close" in a.detail
    assert "yesterday" not in a.detail, "Friday is not yesterday on a Monday"


@pytest.mark.parametrize("time", [None, "", "bmo", "dmh", "--", "pre-market"])
def test_across_a_weekend_only_an_explicit_after_close_print_counts(time):
    """FMP's `time` is often blank, and a blank Friday row may be a before-open print the
    Friday tape already priced. A fabricated cause is worse than a missed one."""
    assert detect_earnings("ORCL", 9.0, _row(FRI, time), MON) is None


def test_after_a_monday_holiday_fridays_after_close_print_explains_tuesday():
    a = detect_earnings("AVGO", -6.0, _row(date(2026, 9, 4), "amc", actual=1.0, est=1.2),
                        TUE_AFTER_LABOR_DAY)
    assert a is not None and a.tag == "Earnings Miss"
    assert "after Friday's close" in a.detail


@pytest.mark.parametrize("day", [date(2026, 9, 24), date(2026, 9, 23)])
def test_a_print_two_trading_days_back_is_still_not_mondays_cause(day):
    """Thursday's after-close print moved FRIDAY's tape, not Monday's."""
    assert detect_earnings("ORCL", 9.0, _row(day, "amc"), MON) is None


def test_thursday_amc_is_not_the_cause_after_a_monday_holiday_either():
    assert detect_earnings("AVGO", -6.0, _row(date(2026, 9, 3), "amc"), TUE_AFTER_LABOR_DAY) is None


def test_the_ordinary_overnight_print_is_unchanged():
    """The calendar-day-before case keeps its old rule (not-BEFORE_OPEN) and wording."""
    tue = date(2026, 9, 29)
    live = detect_earnings("ORCL", 9.0, _row(MON, None), tue)
    assert live is not None and "after yesterday's close" in live.detail
    stamped = detect_earnings("ORCL", 9.0, _row(MON, "amc"), tue, session_word="on Tue")
    assert stamped is not None and "after the prior close" in stamped.detail


# ── A flat stock did not "go the other way" ────────────────────────────────────────────


@pytest.mark.parametrize("chg", [0.0, -0.0, 0.004, -0.004, 0.00499])
@pytest.mark.parametrize("ind", [1.2, -1.2])
def test_a_flat_stock_is_described_as_flat(chg, ind):
    """A move that DISPLAYS as 0.00% has no direction. The sign test read 0.0 (and
    −0.004) as the opposite of a rising industry — a sentence contradicting the flat badge
    printed right beside it."""
    from app.services.daily_move_attribution import MoveContext

    text = describe_no_cause(
        "KO", MoveContext(change_percent=chg, industry_name="Beverages",
                          industry_change_percent=ind), had_news=False,
    )
    assert "the other way" not in text and "far more" not in text, text
    verb = "rose" if ind > 0 else "fell"
    assert f"KO was flat while Beverages {verb} 1.2%." in text, text


@pytest.mark.parametrize("chg", [-0.005, -0.3])
def test_a_real_counter_move_still_goes_the_other_way(chg):
    """CONTROL at the line: |chg| = 0.005 displays as ±0.01%, which has a direction."""
    from app.services.daily_move_attribution import MoveContext

    text = describe_no_cause(
        "KO", MoveContext(change_percent=chg, industry_name="Beverages",
                          industry_change_percent=1.2), had_news=False,
    )
    assert "went the other way" in text and "was flat" not in text


def test_a_flat_stock_through_attribute_never_claims_a_direction():
    a = _attr(change_percent=0.0, sigma_daily=0.01, industry_name="Beverages",
              industry_change_percent=-2.1, had_news=False)
    assert a.kind is CauseKind.NONE
    assert "the other way" not in a.detail and "was flat while Beverages fell 2.1%" in a.detail


# ── The news lean (owner, 2026-10-14) ─────────────────────────────────────────
#
# TestFlight CRWV 2026-10-14: −15.46% (z 2.57), its industry −4.0%, and a Bearish Updates
# Insights card written 13:09 ET that session — "CoreWeave Faces Investor Scrutiny Amid
# Rising Bond Yields". The keyword classifier matched none of its words, so the tile said
# "No clear catalyst in today's news." beside an app that plainly showed the catalyst.
# Owner: use the card ONLY when its sentiment agrees with the move, only on an Unusual
# move (z ≥ 2, else ≥ 5% when σ is unknown); otherwise say the news "leaned" that way.
# ACHR (all-bullish card under a red −5.02%) is the regression the gate must keep refusing.

from app.services.daily_move_attribution import (  # noqa: E402
    NEWS_LEAN_TAGS,
    NewsLean,
    context_note,
    describe_context,
    detect_news_lean,
    usable_lean_headline,
)

LEAN_DAY = date(2026, 10, 14)   # a Wednesday
CRWV_HEADLINE = "CoreWeave Faces Investor Scrutiny Amid Rising Bond Yields"
CRWV_TERMS = ("CRWV", "coreweave")
_IN_SESSION = "2026-10-14T17:09:05Z"   # 13:09 ET


def _lean(headline=CRWV_HEADLINE, sentiment="Bearish", terms=CRWV_TERMS,
          generated_at=_IN_SESSION, rolling=False, day=LEAN_DAY):
    return NewsLean(headline=headline, sentiment=sentiment, subject_terms=tuple(terms),
                    generated_at=generated_at, rolling=rolling, day=day)


def _crwv(**kw) -> Attribution:
    kw.setdefault("ticker", "CRWV")
    kw.setdefault("today", LEAN_DAY)
    kw.setdefault("change_percent", -15.46)
    kw.setdefault("z", 2.57)
    kw.setdefault("industry_name", "Software - Infrastructure")
    kw.setdefault("industry_change_percent", -4.0)
    kw.setdefault("had_news", True)
    kw.setdefault("news_lean", _lean())
    return attribute(**kw)


# The golden table: `describe_no_cause` was split into `describe_context` + the news clause.
# Every pre-existing wording must be byte-identical (5,040 cases were diffed once against
# the old code on 2026-10-14; these rows pin the shapes).
@pytest.mark.parametrize(
    "chg,ind,name,gap,had,checked,word,want",
    [
        (-15.46, -4.0, "Software - Infrastructure", False, True, True, "today",
         "Software - Infrastructure fell 4.0%; CRWV moved far more. No clear catalyst in today's news."),
        (3.0, -2.0, "Tech", False, False, True, "today",
         "Tech fell 2.0%; CRWV went the other way. No company news today."),
        (0.0, -1.2, "Beverages", False, False, True, "today",
         "CRWV was flat while Beverages fell 1.2%. No company news today."),
        (-2.0, -1.9, "Tech", False, True, True, "today", "No clear catalyst in today's news."),
        (-2.0, -0.05, "Tech", False, True, True, "today", "No clear catalyst in today's news."),
        (-5.0, None, None, True, True, True, "today",
         "Most of it was an overnight gap at the open. No clear catalyst in today's news."),
        (-5.0, None, None, False, True, False, "today", "Today's news could not be checked."),
        (-5.0, -1.2, "Aerospace & Defense", False, True, False, "today",
         "Aerospace & Defense fell 1.2%; CRWV moved far more."),
        (-5.0, None, None, False, True, True, "on Fri", "No clear catalyst in Friday's news."),
        (-5.0, None, None, False, False, True, "on Fri", "No company news on Fri."),
        (-5.0, None, None, False, True, False, "on Fri", "Friday's news could not be checked."),
    ],
)
def test_the_no_cause_wording_is_byte_identical_after_the_split(chg, ind, name, gap, had, checked, word, want):
    from app.services.daily_move_attribution import MoveContext

    ctx = MoveContext(change_percent=chg, industry_name=name, industry_change_percent=ind,
                      gap_dominant=gap, gap_percent=(chg * 0.8 if gap else None))
    assert describe_no_cause("CRWV", ctx, had, checked, word) == want


def test_the_crwv_case_shows_its_insights_headline_with_the_industry_note():
    a = _crwv()
    assert a.kind is CauseKind.COMPANY_NEWS
    assert a.tag == "Bearish News"
    assert a.detail == CRWV_HEADLINE + "."
    assert a.note == "Software - Infrastructure fell 4.0%; CRWV moved far more."
    assert a.lean == "bearish"
    assert "No clear catalyst" not in a.detail
    # The lean runs AFTER the four dated detectors; it is not one of them.
    assert a.considered == ["earnings", "analyst", "company_news", "group"]


def test_the_achr_card_under_a_red_move_is_still_refused():
    """The first build's bug: an all-bullish PR headline under −5.02%. Byte-equal to the same
    call with no card at all."""
    achr = dict(ticker="ACHR", change_percent=-5.02152, sigma_daily=0.0454823,
                industry_name="Aerospace & Defense", industry_change_percent=-1.23,
                market_change_percent=-0.15, had_news=True)
    bullish = NewsLean(headline="Archer Aviation explores new markets and strategic growth",
                       sentiment="Bullish", subject_terms=("ACHR", "archer aviation", "archer"))
    with_card = _attr(**achr, news_lean=bullish)
    without = _attr(**achr)
    assert with_card.kind is CauseKind.NONE
    assert with_card.detail == without.detail
    assert "No clear catalyst" in with_card.detail
    assert with_card.lean is None


def test_a_mirrored_achr_agrees_but_is_not_unusual_so_it_only_leans():
    """+5.02% at z 1.10 under a Bullish card: the sign agrees by coin flip on a routine day."""
    a = _attr(ticker="ACHR", change_percent=5.02152, sigma_daily=0.0454823,
              industry_name="Aerospace & Defense", industry_change_percent=1.23, had_news=True,
              news_lean=NewsLean(headline="Archer Aviation explores new markets and strategic growth",
                                 sentiment="Bullish", subject_terms=("ACHR", "archer")))
    assert a.kind is CauseKind.NONE
    assert a.detail.endswith("Today's news leaned bullish.")
    assert "Archer Aviation explores" not in a.detail
    assert a.lean == "bullish"


@pytest.mark.parametrize("sentiment", ["Neutral", None, "", "Mixed", 42, "Negative", "positive"])
def test_only_a_bearish_or_bullish_card_can_be_a_cause(sentiment):
    """Neutral is balanced news — under ORCL −8.84% it reads as a cause it is not. The pure
    module takes the card domain only; mapping "Negative" is the caller's job."""
    a = _crwv(news_lean=_lean(sentiment=sentiment))
    assert a.kind is CauseKind.NONE
    assert a.lean is None
    assert a.detail.endswith("No clear catalyst in today's news.")


@pytest.mark.parametrize("sentiment", ["bearish", " BEARISH ", "Bearish"])
def test_the_card_domain_is_read_case_and_space_insensitively(sentiment):
    assert _crwv(news_lean=_lean(sentiment=sentiment)).tag == "Bearish News"


def test_the_direction_must_match_in_both_directions():
    assert _crwv(news_lean=_lean(sentiment="Bullish")).kind is CauseKind.NONE
    up = _crwv(change_percent=15.46, industry_change_percent=4.0,
               news_lean=_lean(headline="CoreWeave Wins Major OpenAI Capacity Contract",
                               sentiment="Bullish"))
    assert up.kind is CauseKind.COMPANY_NEWS and up.tag == "Bullish News", up.detail
    assert _crwv(change_percent=15.46, news_lean=_lean()).kind is not CauseKind.COMPANY_NEWS


@pytest.mark.parametrize("chg", [0.0, -0.0, 0.004, -0.004, -0.00499])
def test_a_flat_move_has_no_direction_to_agree_with(chg):
    a = _crwv(change_percent=chg, z=None)
    assert a.kind is not CauseKind.COMPANY_NEWS
    assert a.lean is None


@pytest.mark.parametrize("z,shown", [(1.99, False), (2.0, True), (2.57, True), (None, True)])
def test_the_unusual_threshold(z, shown):
    a = _crwv(z=z, sigma_daily=None)
    assert (a.kind is CauseKind.COMPANY_NEWS) is shown, (z, a.detail)
    assert a.lean == "bearish"
    if not shown:
        assert a.detail.endswith("Today's news leaned bearish."), a.detail


@pytest.mark.parametrize("chg,shown", [(-4.99, False), (-5.0, True)])
def test_without_sigma_the_fixed_band_decides(chg, shown):
    # Industry −1.0%: far outrun, so no group move can explain it first.
    a = _crwv(change_percent=chg, z=None, sigma_daily=None, industry_change_percent=-1.0)
    assert (a.kind is CauseKind.COMPANY_NEWS) is shown, (chg, a.detail)


def test_nan_or_inf_z_falls_back_to_the_fixed_band():
    kw = dict(industry_change_percent=-1.0)
    assert _crwv(z=float("nan"), change_percent=-4.0, **kw).kind is CauseKind.NONE
    assert _crwv(z=float("inf"), change_percent=-6.0, **kw).kind is CauseKind.COMPANY_NEWS


# ── what may stand as the headline ──────────────────────────────────────────


def _usable(h, ticker="CRWV", terms=CRWV_TERMS, day=LEAN_DAY):
    return usable_lean_headline(h, ticker, terms, day)


def test_the_live_headlines_pass():
    assert _usable(CRWV_HEADLINE) == CRWV_HEADLINE
    assert _usable("Oracle Faces Regulatory Hurdles Amidst Cloud Demand", "ORCL", ("ORCL", "oracle"))
    # Honest twins of the denylist rows below.
    for honest in (
        "CoreWeave Revenue Slumps as Customers Delay Orders",   # a fundamental, not the tape
        "CoreWeave Signs 5-Year Capacity Deal With Meta",       # years are news, not a window
        "CoreWeave Reports Third-Quarter Loss Wider Than Expected",
        "CRWV Faces Debt Questions After Note Offering",        # the ticker names the subject
        "CoreWeave's Debt Load Draws Investor Scrutiny",
    ):
        assert _usable(honest) == honest, honest


@pytest.mark.parametrize(
    "headline",
    [
        # relative days (it is reprinted verbatim as `detail_aged`)
        "CoreWeave Falls Today on Debt Fears", "CoreWeave's Debt In Focus Today's Session",
        "CoreWeave Investors Brace Tonight", "CoreWeave Debt Talks Resume Tomorrow",
        "CoreWeave Yesterday Priced Notes", "CoreWeave Talks Debt This Morning",
        "CoreWeave Debt Plan This Week", "CoreWeave Pressure Right Now",
        # multi-day windows (the bug that started the rebuild)
        "CoreWeave Down Over the Last Three Weeks", "CoreWeave Worst Run Since July",
        "CoreWeave Lost Ground in the Past Month", "CoreWeave Has Struggled for 15 Days",
        "CoreWeave Surged After Deal", "CoreWeave Rally Fades on Debt",
        # trading activity and schedules the other detectors miss (verified 2026-10-14)
        "CoreWeave Losing Streak Extends Amid Debt Worries",
        "CoreWeave Sell-Off Deepens on Debt Concerns", "CoreWeave Selloff Deepens",
        "CoreWeave Rout Deepens on Debt", "Volatility Grips CoreWeave Amid Debt Fears",
        "CoreWeave Braces for Q3 Report", "CoreWeave Earnings Preview: Debt in Focus",
        "CoreWeave Raises Cash Ahead of Its Q3 Earnings",
        "CoreWeave Set to Report Third-Quarter Results",
        # price talk (insight_conclusion.price_claims with the company's own names)
        "CoreWeave Slides as Bond Yields Climb", "CRWV stock slides on debt worries",
        "CoreWeave Shares Tumble as Yields Rise", "CoreWeave Hits Record High",
        # shape
        "CoreWeave Debt Fears Grow…", "CoreWeave Debt Fears Grow...",
        "Is CoreWeave Overvalued?", "CoreWeave Debt Shock!",
        "CoreWeave", "", "   ",
        "CoreWeave " + "Faces Mounting Questions About Its Debt And Its Customers " * 2,
    ],
)
def test_headlines_that_cannot_stand_as_the_reason_are_refused(headline):
    assert _usable(headline) is None, headline


@pytest.mark.parametrize(
    "headline,ticker,terms",
    [
        ("AI Stocks Retreat as Investors Rotate", "CRWV", CRWV_TERMS),     # a peer group
        ("Tech Shares Slip as Yields Rise", "CRWV", CRWV_TERMS),           # the sector
        ("Wall Street Falls as Treasury Yields Jump", "SPY",
         ("SPY", "spdr s&p 500 etf trust", "spdr s&p", "spdr")),          # the index, circular
        ("Crypto Exchange Faces New Regulatory Scrutiny in Europe", "BTCUSD",
         ("BTCUSD", "BTC", "Bitcoin")),                                    # not the coin
        ("Metaverse Spending Faces Investor Scrutiny", "META", ("META", "meta platforms", "meta")),
        ("Nvidia Faces Investor Scrutiny Over Supply", "CRWV", CRWV_TERMS),  # another company
    ],
)
def test_a_headline_that_does_not_name_its_subject_is_refused(headline, ticker, terms):
    assert usable_lean_headline(headline, ticker, terms, LEAN_DAY) is None


def test_the_subject_match_is_case_sensitive_for_symbols_only():
    assert _usable("Coreweave Faces Investor Scrutiny Over Debt")          # a name, any case
    assert _usable("Crwv Faces Investor Scrutiny Over Debt") is None       # not the symbol
    assert usable_lean_headline("Bitcoin Faces New Regulatory Scrutiny in Europe", "BTCUSD",
                                ("BTCUSD", "BTC", "Bitcoin"), LEAN_DAY)


@pytest.mark.parametrize("weekday,ok", [("Wednesday", True), ("Tuesday", False), ("Friday", False)])
def test_a_headline_naming_another_weekday_is_about_another_day(weekday, ok):
    h = f"CoreWeave Debt Concerns Mount After {weekday} Filing"
    assert (_usable(h) is not None) is ok


def test_whitespace_is_collapsed_and_a_period_is_added_once():
    assert _usable("  CoreWeave   Faces   Investor Scrutiny  ") == "CoreWeave Faces Investor Scrutiny"
    hit, _ = detect_news_lean("CRWV", -15.0, 2.5, LEAN_DAY,
                              _lean(headline="CoreWeave Faces Investor Scrutiny."))
    assert hit.detail == "CoreWeave Faces Investor Scrutiny."


@pytest.mark.parametrize("bad", [None, 42, object(), ["CoreWeave"], b"CoreWeave Faces Scrutiny"])
def test_a_non_string_headline_is_refused_without_raising(bad):
    assert usable_lean_headline(bad, "CRWV", CRWV_TERMS, LEAN_DAY) is None


def test_every_phrase_the_window_invariants_ban_is_refused_as_a_headline():
    """The two existing no-multi-day-window invariants (this file and
    `test_widget_daily_scope.py`) plus the card prompt's own TIME list. Literals here, not
    the production regex: a production edit must not silently weaken the pin."""
    banned = [
        "last 15 days", "last 30 days", "since ", "past month", "over the last",
        "15 days", "30 days", "45 days", "since jul", "since aug", "surged", "rally",
        "today", "tonight", "tomorrow", "yesterday", "this morning", "this evening",
        "later today", "right now",
    ]
    for phrase in banned:
        h = f"CoreWeave debt {phrase.strip()} questions"
        assert _usable(h) is None, phrase


# ── every denylist row: a sample ONLY it refuses, and an honest twin ────────
#
# 2026-10-16: 122 of 145 one-row deletions from the gate left this file green — the review
# rounds' streak, after-the-bell, schedule, subject-verb, weekday-abbreviation and
# subject-position rows among them — because each of their samples above is ALSO refused by
# another layer (the price guard catches "CoreWeave Falls…", the subject-verb rule catches
# "…Falls for Third Straight Session"). A sample pins a row only when that row alone refuses
# it, so every row below names the ONE layer that refuses its sample and the text it
# matched (`_refusals`). Three rows ("Profit-Taking", "Gaps Lower", "Hits All-Time High")
# are refused by the price guard too in every wording found; they say so in `also` and are
# pinned at the layer. Adding a row: a sample only it refuses, plus an honest twin (a near
# miss where one exists), here.

import app.services.daily_move_attribution as dma  # noqa: E402
from typing import NamedTuple, Optional, Tuple  # noqa: E402

from app.services.insight_conclusion import price_claims, stale_timing_claims  # noqa: E402

_LAYER_REGEX = {
    "relative_day": "_LEAN_RELATIVE_DAY_RE",
    "window": "_LEAN_WINDOW_RE",
    "trading": "_LEAN_TRADING_RE",
}


def _refusals(h, ticker="CRWV", terms=CRWV_TERMS, day=LEAN_DAY):
    """{layer: the text it matched} for EVERY layer of `usable_lean_headline` that refuses
    `h`, each run on its own (the gate stops at the first). Read through the module at call
    time, so an edited row shows here. `test_the_layer_probe_agrees_with_the_gate` keeps
    this in step with the gate on every headline in this section."""
    found = {}
    for layer, name in _LAYER_REGEX.items():
        m = getattr(dma, name).search(h)
        if m:
            found[layer] = m.group(0)
    if dma._names_another_day(h, day):
        m = dma._LEAN_WEEKDAY_RE.search(h) or dma._LEAN_WEEKDAY_ABBR_RE.search(h)
        found["weekday"] = m.group(0) if m else "?"
    names = tuple(dict.fromkeys([ticker, *terms]))
    ends = dma._subject_ends(h, names)
    if not ends:
        found["no_subject"] = ""
    for end in ends:
        m = dma._LEAN_AFTER_SUBJECT_RE.match(h, end)
        if m:
            found["after_subject"] = m.group(0).strip()
            break
    if stale_timing_claims(h):
        found["stale_timing"] = stale_timing_claims(h)[0]
    if price_claims(h, names):
        found["price"] = price_claims(h, names)[0]
    return found


class _Row(NamedTuple):
    row: str
    layer: str
    refuse: str
    phrase: Optional[str]          # what the layer matched; None = not a text match
    twin: str                      # honest, and must stay accepted
    ticker: str = "CRWV"
    terms: Tuple[str, ...] = CRWV_TERMS
    also: Tuple[str, ...] = ()     # other layers that refuse the sample in every wording


_AI = ("AI", "c3.ai")              # the subject terms `news_insight_service.price_terms` builds
_IT = ("IT", "gartner")
_A = ("A", "agilent")
_T = ("T", "at&t")
_GE = ("GE", "ge aerospace")
_MSTR = ("MSTR", "strategy")
_XYZ = ("XYZ", "block")
_TGT = ("TGT", "target")
_V = ("V", "visa")
_GAP = ("GAP", "gap")

_LEAN_ROWS = [
    # ── _LEAN_RELATIVE_DAY_RE: reprinted verbatim as `detail_aged` ──
    _Row("rel_today", "relative_day", "CoreWeave Lenders Rework Debt Terms Today", "Today",
         "CoreWeave Lenders Rework Debt Terms"),
    _Row("rel_tonight", "relative_day", "CoreWeave Lenders Vote on Debt Terms Tonight", "Tonight",
         "CoreWeave Lenders Vote on Debt Terms"),
    _Row("rel_tomorrow", "relative_day", "CoreWeave Debt Talks Resume Tomorrow", "Tomorrow",
         "CoreWeave Debt Talks Resume With Lenders"),
    _Row("rel_yesterday", "relative_day", "CoreWeave Yesterday Priced Notes", "Yesterday",
         "CoreWeave Priced $2 Billion of Notes"),
    _Row("rel_overnight", "relative_day", "CoreWeave Lenders Rework Debt Terms Overnight",
         "Overnight", "CoreWeave Lenders Agree to Rework Debt Terms"),
    _Row("rel_weekend", "relative_day", "CoreWeave Lenders Rework Debt Terms Over the Weekend",
         "Weekend", "CoreWeave Lenders Rework Debt Terms Over Covenant Fears"),
    _Row("rel_this", "relative_day", "CoreWeave Talks Debt This Morning", "This Morning",
         "CoreWeave Talks Debt With Lenders"),
    _Row("rel_earlier_this", "relative_day", "CoreWeave Debt Load Doubled Earlier This Year",
         "Earlier This Year", "CoreWeave Debt Load Doubled After Acquisition"),
    _Row("rel_right_now", "relative_day", "CoreWeave Debt Matters Right Now", "Right Now",
         "CoreWeave Debt Matters to Lenders"),
    _Row("rel_next", "relative_day", "CoreWeave Lenders to Vote on Debt Terms Next Week",
         "Next Week", "CoreWeave Unveils Next-Generation GPU Cloud"),
    _Row("rel_last", "relative_day", "CoreWeave Lenders Reworked Debt Terms Last Night",
         "Last Night", "CoreWeave Wins Last Major Lender Approval"),
    # ── _LEAN_WINDOW_RE: a multi-day window or a multi-day tape story ──
    _Row("win_digits", "window", "CoreWeave Has Struggled for 15 Days", "15 Days",
         "CoreWeave Signs 5-Year Capacity Deal With Meta"),
    _Row("win_ordinal", "window", "CoreWeave Debt Fears Weigh for a Third Day", "Third Day",
         "CoreWeave Reports Third-Quarter Loss Wider Than Expected"),
    _Row("win_ordinal_digits", "window", "CoreWeave Debt Fears Weigh for an 11th Day", "11th Day",
         "CoreWeave Opens Its 11th Data Center"),
    _Row("win_ordinal_streak", "window", "CoreWeave Debt Worries Weigh for Third Straight Session",
         "Third Straight Session", "CoreWeave Wins Third Straight Contract From Meta"),
    _Row("win_hyphenated", "window", "CoreWeave's Three-Day Slide on Debt Fears",
         "Three-Day Slide", "CoreWeave Signs Three-Year Lease With Meta"),
    # round 2: hyphenated, a window only before a tape noun (each noun: `_HYPHEN_NOUNS`)
    _Row("win_hyphen_not_a_tape_noun", "window", "CoreWeave Debt Fears Fuel Two-Week Decline",
         "Two-Week Decline", "CoreWeave Faces Six-Month Lockup Expiry for Early Investors"),
    _Row("win_straight", "window", "CoreWeave Posts Consecutive Declines on Debt Fears",
         "Consecutive Declines", "CoreWeave Posts Consecutive Quarters of Revenue Growth"),
    _Row("win_running", "window", "CoreWeave Under Pressure for Days Running", "Days Running",
         "CoreWeave Running Short of Power in Texas"),
    _Row("win_long", "window", "CoreWeave Ends Week-Long Slide", "Week-Long",
         "CoreWeave Signs Long-Term Lease With Meta"),
    _Row("win_multi", "window", "CoreWeave Snaps Multi-Week Slide", "Multi-Week",
         "CoreWeave Signs Multi-Year Deal With Meta"),
    _Row("win_year_to_date", "window", "CoreWeave Year-to-Date Gains Draw Scrutiny",
         "Year-to-Date", "CoreWeave Year-End Debt Talks Draw Scrutiny"),
    _Row("win_ytd", "window", "CoreWeave YTD Gains Draw Scrutiny", "YTD",
         "CoreWeave Revenue Gains Draw Scrutiny"),
    _Row("win_past", "window", "CoreWeave Lost Ground in the Past Month", "Past Month",
         "CoreWeave Lost Ground to Rivals in Cloud Deals"),
    _Row("win_over_the_last", "window", "CoreWeave Debt Worries Mount Over the Last Year",
         "Over the Last", "CoreWeave Debt Worries Mount Over Covenant Terms"),
    _Row("win_since", "window", "CoreWeave Worst Run Since July", "Since",
         "CoreWeave Debt Worries Mount Into July Maturity"),
    _Row("win_surged", "window", "CoreWeave Options Activity Surged on AI Demand", "Surged",
         "CoreWeave Faces Surge in Power Costs"),
    _Row("win_rally", "window", "CoreWeave Rally Fades on Debt", "Rally",
         "CoreWeave Debt Plan Fades on Lender Pushback"),
    _Row("win_back_to_back", "window", "CoreWeave Posts Back-to-Back Declines",
         "Back-to-Back Declines", "CoreWeave Posts Back-to-Back Quarterly Losses"),
    # round 2: a net loss is news — "Back-to-Back Losses" / "Gains" are no longer refused
    _Row("win_back_to_back_spaced", "window", "CoreWeave Notes Post Back to Back Declines",
         "Back to Back Declines", "CoreWeave Posts Back-to-Back Losses Despite Delivery Gains"),
    _Row("win_back_to_back_weekly", "window", "CoreWeave Bonds Post Back-to-Back Weekly Falls",
         "Back-to-Back Weekly Falls", "CoreWeave Posts Back-to-Back Weekly Contract Wins"),
    _Row("win_back_to_back_daily", "window", "CoreWeave Bonds Post Back-to-Back Daily Falls",
         "Back-to-Back Daily Falls", "CoreWeave Posts Back-to-Back Losses Despite Revenue Growth"),
    _Row("win_back_to_back_advances", "window", "CoreWeave Notches Back-to-Back Advances",
         "Back-to-Back Advances", "CoreWeave Wins Back-to-Back Contracts From Meta"),
    _Row("win_back_to_back_sessions", "window", "CoreWeave Notes Slip for Back-to-Back Sessions",
         "Back-to-Back Sessions", "CoreWeave Hosts Back-to-Back Investor Sessions"),
    _Row("win_back_to_back_days", "window", "CoreWeave Notes Slip on Back-to-Back Days",
         "Back-to-Back Days", "CoreWeave Wins Back-to-Back Awards"),
    _Row("win_back_to_back_weeks", "window", "CoreWeave Notes Slip for Back-to-Back Weeks",
         "Back-to-Back Weeks", "CoreWeave Signs Back-to-Back Leases in Texas"),
    _Row("win_back_to_back_drops", "window", "CoreWeave Bonds Post Back-to-Back Drops",
         "Back-to-Back Drops", "CoreWeave Wins Dismissal of Back-to-Back Lawsuits"),
    _Row("win_back_to_back_falls", "window", "CoreWeave Bonds Post Back-to-Back Falls",
         "Back-to-Back Falls", "CoreWeave Posts Back-to-Back Revenue Records"),
    _Row("win_weekly", "window", "CoreWeave Weekly Loss Widens on Debt Fears", "Weekly Loss",
         "CoreWeave Q3 Net Loss Widens on Interest Costs"),
    _Row("win_monthly", "window", "CoreWeave Notes Post Monthly Loss Amid Debt Fears",
         "Monthly Loss", "CoreWeave Shifts to Monthly Billing for GPU Rentals"),
    _Row("win_losing_run", "window", "CoreWeave Losing Run Continues", "Losing Run",
         "CoreWeave Extends Run of Contract Wins"),
    _Row("win_adds_to", "window", "CoreWeave Adds to Recent Losses", "Adds to Recent Losses",
         "CoreWeave Interest Bill Adds to Net Losses"),
    _Row("win_adds_to_its", "window", "CoreWeave Debt Fears Add to Its Recent Losses",
         "Add to Its Recent Losses", "CoreWeave Interest Bill Adds to Its Net Losses"),
    # ── _LEAN_TRADING_RE: the tape, a schedule, or the session's edges ──
    _Row("tr_streak", "trading", "CoreWeave Losing Streak Extends Amid Debt Worries", "Streak",
         "CoreWeave Extends Hiring Spree in Europe"),
    _Row("tr_selloff", "trading", "CoreWeave Sell-Off Deepens on Debt Concerns", "Sell-Off",
         "CoreWeave Sells Off Stake in Data Center Venture"),
    _Row("tr_rout", "trading", "CoreWeave Rout Deepens on Debt", "Rout",
         "CoreWeave Reroutes Capacity to New Data Center"),
    _Row("tr_volatility", "trading", "CoreWeave Volatility Draws Investor Scrutiny", "Volatility",
         "CoreWeave Faces Investor Scrutiny Over Power Costs"),
    _Row("tr_slide_continues", "trading", "CoreWeave Slide Continues on Debt Worries",
         "Slide Continues", "CoreWeave Expansion Continues With New Data Center"),
    _Row("tr_top_losers", "trading", "CoreWeave Among Top Losers on Debt Fears", "Top Losers",
         "CoreWeave Among Top Cloud Providers by Capacity"),
    _Row("tr_pressure", "trading", "CoreWeave Faces Selling Pressure on Debt Fears",
         "Selling Pressure", "CoreWeave Faces Pricing Pressure From Rivals"),
    _Row("tr_profit_taking", "trading", "CoreWeave Sees Profit-Taking After Deal", "Profit-Taking",
         "CoreWeave Turns Profit on AI Demand", also=("price",)),
    _Row("tr_gaps", "trading", "CoreWeave Gaps Lower on Debt Fears", "Gaps Lower",
         "CoreWeave Closes Gaps in Debt Coverage", also=("price",)),
    _Row("tr_at_the_open", "trading", "CoreWeave Notes Draw Selling at the Open", "at the Open",
         "CoreWeave Opens Data Center in Texas"),
    _Row("tr_opening_bell", "trading", "CoreWeave CEO Rings Opening Bell at Nasdaq",
         "Opening Bell", "CoreWeave Opening New Data Center in Texas"),
    _Row("tr_from_peak", "trading", "CoreWeave Valuation Retreats From Peak", "From Peak",
         "CoreWeave Wins Record Contract From OpenAI"),
    _Row("tr_off_highs", "trading", "CoreWeave Off Highs on Debt Fears", "Off Highs",
         "CoreWeave Pays Off High-Cost Debt"),
    _Row("tr_off_record_highs", "trading", "CoreWeave Notes Trade Off Record Highs",
         "Off Record Highs", "CoreWeave Sells Off Peak-Hour GPU Capacity"),
    _Row("tr_off_session_highs", "trading", "CoreWeave Notes Trade Off Session Highs",
         "Off Session Highs", "CoreWeave Pays Off Session Fees Early"),
    _Row("tr_off_all_time_highs", "trading", "CoreWeave Notes Trade Off All-Time Highs",
         "Off All-Time Highs", "CoreWeave Pays Off All-Time Largest Loan"),
    _Row("tr_off_its_high", "trading", "CoreWeave Trades Well Off Its High", "Off Its High",
         "CoreWeave Pays Off Its Highest-Cost Debt"),
    _Row("tr_off_its_low", "trading", "CoreWeave Notes Trade Well Off Its Low", "Off Its Low",
         "CoreWeave Sells Off Its Low-Margin Hosting Unit"),
    _Row("tr_off_its_high_hyphen", "trading", "CoreWeave Notes Trade Well Off Their High",
         "Off Their High", "CoreWeave Pays Off Its High-Interest Bridge Loan"),
    _Row("tr_big_run", "trading", "CoreWeave Investors Cash In After Big Run", "Big Run",
         "CoreWeave Wins Big Contract From Meta"),
    _Row("tr_market_value", "trading", "CoreWeave Market Value Shrinks on Debt Fears",
         "Market Value Shrinks", "CoreWeave Market Share Grows in AI Cloud"),
    _Row("tr_erase_gains", "trading", "CoreWeave Debt Fears Erase Gains From Deal", "Erase Gains",
         "CoreWeave Erases Debt Covenant Concerns"),
    _Row("tr_gives_up_gains", "trading", "CoreWeave Gives Up Gains on Debt Fears",
         "Gives Up Gains", "CoreWeave Gives Up Board Seat at Core Scientific"),
    _Row("tr_reverses_gains", "trading", "CoreWeave Reverses Gains on Debt Fears",
         "Reverses Gains", "CoreWeave Reverses Course on Europe Expansion"),
    # round 2: "reverses … losses" only intraday — a turnaround is news
    _Row("tr_reverses_its_gains", "trading", "CoreWeave Reverses Its Gains on Debt Fears",
         "Reverses Its Gains", "CoreWeave Court Loss Reverses Earlier Patent Gains"),
    _Row("tr_reverses_early_gains", "trading", "CoreWeave Reverses Early Gains on Debt Fears",
         "Reverses Early Gains", "CoreWeave Reverses Losses With First Quarterly Profit"),
    _Row("tr_reverses_session_losses", "trading", "CoreWeave Reverses Session Losses on AI Deal",
         "Reverses Session Losses", "CoreWeave Reverses Losses, Posts First Operating Profit"),
    _Row("tr_stocks_lower", "trading", "CoreWeave Debt Fears Send AI Stocks Lower", "Stocks Lower",
         "CoreWeave Debt Fears Send Yields Higher"),
    _Row("tr_braces", "trading", "CoreWeave Braces for Q3 Report", "Braces for",
         "CoreWeave Braces Data Centers for Winter Storms"),
    _Row("tr_gears_up", "trading", "CoreWeave Gears Up for Q3 Report", "Gears Up for",
         "CoreWeave Gears Up New Data Center"),
    _Row("tr_preview", "trading", "CoreWeave Earnings Preview: Debt in Focus", "Preview",
         "CoreWeave Earnings Review: Debt in Focus"),
    _Row("tr_what_to_expect", "trading", "What to Expect From CoreWeave's Q3 Report",
         "What to Expect", "What Lenders Want From CoreWeave's Debt Deal"),
    _Row("tr_awaits", "trading", "CoreWeave Awaits Lender Vote on Debt Terms", "Awaits",
         "CoreWeave Wins Lender Vote on Debt Terms"),
    _Row("tr_on_deck", "trading", "CoreWeave Q3 Report on Deck", "on Deck",
         "CoreWeave Q3 Report Shows Backlog Growth"),
    _Row("tr_upcoming", "trading", "CoreWeave Investors Weigh Upcoming Debt Maturities",
         "Upcoming", "CoreWeave Investors Weigh Debt Maturities"),
    _Row("tr_ahead_of", "trading", "CoreWeave Raises Cash Ahead of Q3 Report",
         "Ahead of Q3 Report", "CoreWeave Raises Cash Ahead of Rivals"),
    _Row("tr_due_month", "trading", "CoreWeave Lender Vote Due Nov. 12", "Due Nov",
         "CoreWeave Debt Due in 2027 Draws Scrutiny"),
    # round 2: an EVENT due on any day — "Due Wednesday" names the session's own day
    _Row("tr_event_due", "trading", "CoreWeave Q3 Earnings Due Wednesday", "Earnings Due",
         "CoreWeave Delays Q3 Report Due to Audit Review"),
    _Row("tr_event_is_due", "trading", "CoreWeave Q3 Results Are Due Wednesday",
         "Results Are Due", "CoreWeave Results Are Strong as AI Demand Booms"),
    _Row("tr_event_due_to_be", "trading", "CoreWeave Q3 Results Due to Be Released Nov. 12",
         "Results Due", "CoreWeave Revises Q3 Results Due to Accounting Error"),
    _Row("tr_set_to", "trading", "CoreWeave Set to Unveil New GPU Cloud", "Set to Unveil",
         "CoreWeave Unveils New GPU Cloud"),
    # "Scheduled for Wednesday" names the session's own day: the weekday rule passes it
    _Row("tr_scheduled_for", "trading", "CoreWeave Q3 Report Scheduled for Wednesday",
         "Report Scheduled for", "CoreWeave Misses Scheduled Debt Payment"),
    _Row("tr_scheduled_on", "trading", "CoreWeave Q3 Report Scheduled on Nov. 12",
         "Report Scheduled on", "CoreWeave Debt Payment Scheduled on Nov. 12 Is Made Early"),
    _Row("tr_scheduled_to", "trading", "CoreWeave Q3 Call Scheduled to Begin at Noon",
         "Call Scheduled to", "CoreWeave Core Scientific Deal Scheduled to Close in First Quarter"),
    _Row("tr_slated", "trading", "CoreWeave Q3 Report Slated for Nov. 12", "Report Slated for",
         "CoreWeave Core Scientific Deal Slated to Close in First Quarter"),
    _Row("tr_event_set_for", "trading", "CoreWeave Q3 Report Set for Nov. 12",
         "Report Set for", "CoreWeave Set for S&P 500 Inclusion"),
    _Row("tr_event_is_scheduled", "trading", "CoreWeave Q3 Report Is Scheduled for Nov. 12",
         "Report Is Scheduled for", "CoreWeave Is Scheduled for S&P 500 Inclusion"),
    _Row("tr_set_for", "trading", "CoreWeave Set for Q3 Report on Oct 14", "Set for Q3 Report",
         "CoreWeave Set for S&P 500 Inclusion"),
    _Row("tr_to_report", "trading", "CoreWeave to Report Q3 Results on Nov. 12",
         "to Report Q3 Results", "CoreWeave Reports Q3 Results Below Estimates"),
    _Row("tr_to_announce", "trading", "CoreWeave to Announce Q3 Results After Debt Deal",
         "to Announce Q3 Results", "CoreWeave to Announce Data Center Deal With Meta"),
    _Row("tr_to_release", "trading", "CoreWeave to Release Q3 Earnings After Debt Deal",
         "to Release Q3 Earnings", "CoreWeave to Release New GPU Cloud in Europe"),
    _Row("tr_to_post", "trading", "CoreWeave to Post Q3 Results After Debt Deal",
         "to Post Q3 Results", "CoreWeave to Post Job Cuts After Debt Deal"),
    _Row("tr_to_publish", "trading", "CoreWeave to Publish Annual Results After Audit",
         "to Publish Annual Results", "CoreWeave to Publish AI Safety Report After Audit"),
    _Row("tr_will_report", "trading", "CoreWeave Will Report Q3 Results Nov. 12",
         "Will Report Q3 Results", "CoreWeave Will Restate Q3 Results After Audit"),
    # round 2: "before" an event only in the tape sense (each word: `_BEFORE_WORDS`)
    _Row("tr_before_results", "trading", "CoreWeave Investors Show Caution Before Q3 Results",
         "Caution Before Q3 Results", "CoreWeave Raises Cash Before Q3 Results"),
    # round 2: "into" an event only after a movement verb (each verb: `_INTO_VERBS`)
    _Row("tr_into_report", "trading", "CoreWeave Investors Head Into Q3 Report",
         "Head Into Q3 Report", "CoreWeave Faces SEC Probe Into Its Earnings Disclosures"),
    _Row("tr_eye", "trading", "CoreWeave Investors Eye Q3 Report", "Eye Q3 Report",
         "CoreWeave Eyes European Expansion"),
    _Row("tr_eyes_on", "trading", "CoreWeave Debt in Focus With All Eyes on Q3 Report",
         "Eyes on Q3 Report", "CoreWeave Eyes on Europe as AI Demand Grows"),
    _Row("tr_eyeing", "trading", "CoreWeave Investors Eyeing Q3 Report", "Eyeing Q3 Report",
         "CoreWeave Eyeing $5 Billion Acquisition of Core Scientific"),
    _Row("tr_watch", "trading", "CoreWeave Investors Watch Q3 Results Closely",
         "Watch Q3 Results", "CoreWeave Watches Debt Costs as Rates Rise"),
    _Row("tr_watch_for", "trading", "CoreWeave Investors Watch for Q3 Results",
         "Watch for Q3 Results", "CoreWeave Watches for Power Shortages in Texas"),
    _Row("tr_look_to", "trading", "CoreWeave Investors Look to Q3 Report", "Look to Q3 Report",
         "CoreWeave Looks to Raise $2 Billion in Debt"),
    _Row("tr_look_forward", "trading", "CoreWeave Investors Look Forward to Q3 Results",
         "Look Forward to Q3 Results", "CoreWeave Looks Forward to Expanding in Europe"),
    _Row("tr_qual_fy", "trading", "CoreWeave Investors Eye FY2026 Results", "Eye FY2026 Results",
         "CoreWeave Eyes FY2026 Expansion"),
    _Row("tr_qual_year", "trading", "CoreWeave Investors Eye 2026 Results", "Eye 2026 Results",
         "CoreWeave Eyes 2026 Expansion"),
    _Row("tr_nears", "trading", "CoreWeave Debt in Focus as Q3 Report Nears", "Report Nears",
         "CoreWeave Nears $9 Billion Core Scientific Deal"),
    # round 2: "Holiday Season Nears" and "Margin Call Looms" are news
    _Row("tr_season_nears", "trading", "CoreWeave Debt in Focus as Earnings Season Approaches",
         "Earnings Season Approaches", "CoreWeave Adds Staff as Holiday Season Approaches"),
    _Row("tr_ipo_nears", "trading", "CoreWeave Rival's IPO Nears Amid Debt Fears", "IPO Nears",
         "CoreWeave Files for IPO of Its Cloud Unit"),
    _Row("tr_looms", "trading", "CoreWeave Debt Questions Linger as Earnings Loom",
         "Earnings Loom", "CoreWeave Debt Fears Grow as Margin Call Looms for Founders"),
    # After the session — the TIMING sense only; "After <event>" is news, and so is news out
    # BEFORE the bell (`test_news_out_before_the_bell_is_todays_news`)
    _Row("tr_after_hours", "trading", "CoreWeave Debt Deal Announced After Hours", "After Hours",
         "CoreWeave Expands Support Hours for Enterprise Clients"),
    _Row("tr_post_close", "trading", "CoreWeave Q3 Results Expected Post-Close", "Post-Close",
         "CoreWeave Begins Post-Close Integration of Core Scientific"),
    _Row("tr_after_market_hyphen", "trading", "CoreWeave Q3 Results Released After-Market",
         "After-Market", "CoreWeave Launches After-Market Services for GPU Buyers"),
    _Row("tr_aftermarket", "trading", "CoreWeave Q3 Results Land in the Aftermarket",
         "Aftermarket", "CoreWeave Aftermarket Sales of Used GPUs Slow"),
    _Row("tr_after_the_bell", "trading", "CoreWeave Reports After the Bell", "After the Bell",
         "CoreWeave Cuts Guidance After Losing Key Customer"),
    _Row("tr_after_market_close", "trading", "CoreWeave Debt Deal Announced After Market Close",
         "After Market Close", "CoreWeave Cuts Prices After Market Share Loss"),
    _Row("tr_after_closing_bell", "trading",
         "CoreWeave Debt Deal Announced After the Closing Bell", "After the Closing Bell",
         "CoreWeave Wins Lender Support After Closing Core Scientific Deal"),
    _Row("tr_extended_trading", "trading", "CoreWeave Notes Weigh in Extended Trading",
         "Extended Trading", "CoreWeave Extended Credit Line Draws Scrutiny"),
    _Row("tr_extended_hours", "trading", "CoreWeave Notes Draw Extended-Hours Selling",
         "Extended-Hours", "CoreWeave Offers Extended Support Hours"),
    _Row("tr_premarket", "trading", "CoreWeave Debt Deal Draws Pre-Market Selling", "Pre-Market",
         "CoreWeave Signs Pre-Construction Deal for Texas Site"),
    _Row("tr_postmarket", "trading", "CoreWeave Notes Draw Post-Market Selling", "Post-Market",
         "CoreWeave Signs Post-Merger Integration Deal"),
    # ── _LEAN_AFTER_SUBJECT_RE: the subject's own verb narrating the tape ──
    _Row("as_verb", "after_subject", "CoreWeave Lags Cloud Peers on Debt Fears", "Lags",
         "CoreWeave Lowers Guidance on Debt Costs"),
    _Row("as_shares_prefix", "after_subject", "CoreWeave Shares Bounce on Debt Relief",
         "Shares Bounce", "CoreWeave Shares Data Center Roadmap With Investors"),
    _Row("as_is_down", "after_subject", "CoreWeave Is Down on Debt Fears", "Is Down",
         "CoreWeave Is Downgraded at Morgan Stanley"),
    _Row("as_extends", "after_subject", "CoreWeave Extends Slide on Debt Worries", "Extends Slide",
         "CoreWeave Extends Partnership With Nvidia"),
    _Row("as_resumes", "after_subject", "CoreWeave Resumes Slide on Debt Worries", "Resumes Slide",
         "CoreWeave Resumes Debt Talks With Lenders"),
    _Row("as_pulls_back", "after_subject", "CoreWeave Pulls Back on Debt Fears", "Pulls Back",
         "CoreWeave Pulls Out of Data Center Deal"),
    _Row("as_halves", "after_subject", "CoreWeave Halves as Debt Fears Mount", "Halves",
         "CoreWeave Cuts Capex Plan in Half"),
    _Row("as_doubles", "after_subject", "CoreWeave Doubles in Value on AI Demand", "Doubles in",
         "CoreWeave Doubles Data Center Capacity"),
    _Row("as_leads_lower", "after_subject", "CoreWeave Leads AI Names Lower",
         "Leads AI Names Lower", "CoreWeave Leads Funding Round for AI Startup"),
    _Row("as_hits_record", "after_subject", "CoreWeave Hits All-Time High on AI Demand",
         "Hits All-Time", "CoreWeave Hits Milestone With Texas Data Center", also=("price",)),
    _Row("as_swings_lower", "after_subject", "CoreWeave Swings Lower on Debt Fears",
         "Swings Lower", "CoreWeave Swings to Profit on AI Demand"),
    _Row("as_starts_higher", "after_subject", "CoreWeave Starts Higher on AI Demand",
         "Starts Higher", "CoreWeave Starts Higher-Margin Inference Service"),
    _Row("as_extends_continued", "after_subject", "CoreWeave Extends Continued Slide",
         "Extends Continued Slide", "CoreWeave Extends Continued Support for Older GPUs"),
    # round 2: the multi-day tape in SUBJECT position only (moved from `_LEAN_WINDOW_RE`) —
    # "Intel Warns of Continued Decline in PC Demand" and "GPU Demand Keeps Rising" are news.
    # Each word of the two lists: `_CONTINUE_WORDS`, `_KEEPS_WORDS`.
    _Row("as_continues", "after_subject", "CoreWeave Continues Slide on Debt Fears",
         "Continues Slide", "CoreWeave Continues Expansion With New Data Center"),
    _Row("as_continues_to", "after_subject", "CoreWeave Continues to Slide on Debt Fears",
         "Continues to Slide", "CoreWeave Continues to Expand in Europe"),
    _Row("as_continues_its", "after_subject", "CoreWeave Continues Its Slide on Debt Fears",
         "Continues Its Slide", "CoreWeave Reports Continued Decline in Gross Margin"),
    _Row("as_continue_their", "after_subject", "CoreWeave Shares Continue Their Slide",
         "Shares Continue Their Slide", "CoreWeave Posts Continued Losses as Interest Costs Mount"),
    _Row("as_shares_continue", "after_subject", "CoreWeave Shares Continue to Slide on Debt Worries",
         "Shares Continue to Slide", "CoreWeave Revenue Continues to Rise on AI Demand"),
    _Row("as_continued", "after_subject", "CoreWeave Continued to Slide on Debt Worries",
         "Continued to Slide", "CoreWeave Warns of Continued Decline in GPU Rental Prices"),
    _Row("as_continuing", "after_subject", "CoreWeave Continuing Slide Worries Lenders",
         "Continuing Slide", "CoreWeave Continuing Expansion Worries Lenders"),
    _Row("as_continues_advance_object", "after_subject",
         "Moderna Continues to Advance on Vaccine Hopes", "Continues to Advance",
         "Moderna Continues to Advance Cancer Vaccine Program", "MRNA", ("MRNA", "moderna")),
    _Row("as_continues_higher_hyphen", "after_subject", "CoreWeave Continues Higher on AI Demand",
         "Continues Higher", "CoreWeave Continues Higher-Margin Push Into Inference"),
    _Row("as_keeps", "after_subject", "CoreWeave Keeps Falling on Debt Fears", "Keeps Falling",
         "CoreWeave Keeps Growth Guidance Intact"),
    _Row("as_keep_on", "after_subject", "CoreWeave Shares Keep On Falling",
         "Shares Keep On Falling", "CoreWeave Raises Prices as GPU Demand Keeps Rising"),
    _Row("as_keeps_advancing_object", "after_subject",
         "Moderna Keeps Advancing on Vaccine Hopes", "Keeps Advancing",
         "Moderna Keeps Advancing Its Cancer Vaccine Program", "MRNA", ("MRNA", "moderna")),
    _Row("as_adds_to", "after_subject", "CoreWeave Adds to Losses After Debt Report",
         "Adds to Losses", "CoreWeave Adds to Board With Former Nvidia Executive"),
    _Row("as_shares_add_to", "after_subject", "CoreWeave Shares Add to Gains on AI Demand",
         "Shares Add to Gains", "CoreWeave Shares Add to Index Weight After Inclusion"),
    _Row("as_adds_to_its", "after_subject", "CoreWeave Adds to Its Losses on Debt Fears",
         "Adds to Its Losses", "CoreWeave Adds to Its Board After Activist Push"),
    _Row("as_deepens", "after_subject", "CoreWeave Deepens Losses on Debt Fears",
         "Deepens Losses", "CoreWeave Deepens Ties With Nvidia"),
    _Row("as_deepens_its", "after_subject", "CoreWeave Deepens Its Slide on Debt Fears",
         "Deepens Its Slide", "CoreWeave Deepens Its Partnership With Nvidia"),
    # ── _LEAN_WEEKDAY_RE / _LEAN_WEEKDAY_ABBR_RE: a day other than the session's (Wed) ──
    _Row("wd_full", "weekday", "CoreWeave Debt Concerns Mount After Tuesday Filing", "Tuesday",
         "CoreWeave Debt Concerns Mount After Wednesday Filing"),
    _Row("wd_abbr", "weekday", "CoreWeave Debt Concerns Mount After Tue. Filing", "Tue.",
         "CoreWeave Debt Concerns Mount After Wed. Filing"),
    _Row("wd_abbr_weekend", "weekday", "CoreWeave Lenders Met Sat. to Rework Debt Terms", "Sat.",
         "CoreWeave Signs Cloud Deal With Sun Life"),
    # ── _subject_ends: the headline names ITS OWN subject ──
    _Row("subj_stop_ai", "no_subject", "AI Spending Doubts Weigh on Software Makers", "",
         "C3.ai Faces Scrutiny Over Federal Contract Delays", "AI", _AI),
    _Row("subj_stop_it", "no_subject", "IT Budget Cuts Weigh on Research Firms", "",
         "Gartner Faces Weaker Demand for Research Subscriptions", "IT", _IT),
    _Row("subj_one_letter", "no_subject", "A Weak Outlook Weighs on Lab Equipment Makers", "",
         "Agilent Cuts Outlook as Lab Demand Softens", "A", _A),
    _Row("subj_t_mobile", "no_subject", "T-Mobile Faces Investor Scrutiny Over Network Spending",
         "", "AT&T Faces Investor Scrutiny Over Network Spending", "T", _T),
    _Row("subj_hyphen", "no_subject", "GE-Hitachi Reactor Venture Faces Delays", "",
         "GE Faces Engine Delivery Delays", "GE", _GE),
    _Row("subj_lead_mstr", "no_subject", "Bitcoin Miners Rethink Strategy Under New Rules", "",
         "Strategy Faces Scrutiny Over Bitcoin Holdings", "MSTR", _MSTR),
    _Row("subj_lead_xyz", "no_subject", "Senators Move to Block Crypto Tax Bill", "",
         "Regulators Probe Block's Cash App Lending", "XYZ", _XYZ),
    _Row("subj_lead_tgt", "no_subject", "Walmart Misses Target as Shoppers Pull Back", "",
         "Target Cuts Outlook as Shoppers Pull Back", "TGT", _TGT),
    _Row("subj_lead_v", "no_subject", "Student Visa Curbs Weigh on Universities", "",
         "Visa Faces Antitrust Suit Over Debit Fees", "V", _V),
    _Row("subj_possessive", "no_subject", "Debt Fears Cloud CoreWeave Expansion Plans", "",
         "Debt Fears Cloud CoreWeave's Expansion Plans"),
    _Row("subj_the", "no_subject", "Tariff Pressure Widens the Gap for Apparel Retailers", "",
         "The Gap Faces Tariff Pressure on Apparel Imports", "GAP", _GAP),
]

# Round 2 (2026-10-16): every word of the round-2 word lists, each pinned on its own — a
# sample only that word refuses. Literals, not the production constants, so an edit there
# cannot move the pin with it.
_CONTINUE_WORDS = (  # "CoreWeave Continues <word> on Debt Fears"
    "to Slide|Sliding|to Slip|Slipping|to Decline|Declining|to Fall|Falling|to Drop|Dropping"
    "|to Sink|Sinking|to Slump|Slumping|to Retreat|Retreating|to Tumble|Tumbling|to Climb"
    "|Climbing|to Rise|Rising|to Advance|Advancing|to Rebound|Rebounding|Its Losses|Its Gains"
    "|Lower|Higher"
).split("|")
_KEEPS_WORDS = (  # "CoreWeave Keeps <word> on Debt Fears"
    "Falling|Sliding|Slipping|Dropping|Sinking|Declining|Tumbling|Retreating|Climbing|Rising"
    "|Advancing|Losing Ground|Gaining Ground"
).split("|")
_INTO_VERBS = (  # "CoreWeave Notes <verb> Into Q3 Report"
    "Head Heads Headed Heading Go Goes Going Went Slide Slides Sliding Fall Falls Falling Fell "
    "Rise Rises Rising Rose Climb Climbs Climbed Climbing Dip Dips Dipped Dipping Drift Drifts "
    "Drifted Drifting Trade Trades Traded Trading Move Moves Moved Moving"
).split()
_BEFORE_WORDS = (  # "CoreWeave Notes <verb> Before Q3 Report"
    "Slide Slides Sliding Slip Slips Slipped Slipping Fall Falls Falling Fell Rise Rises Rising "
    "Rose Climb Climbs Climbed Climbing Dip Dips Dipped Dipping Drift Drifts Drifted Drifting"
).split()
_BEFORE_NOUNS = ("Jitters", "Positioning", "Caution", "Nerves")  # "CoreWeave Investor <noun> …"
_HYPHEN_NOUNS = (  # "CoreWeave Debt Fears Fuel Three-Day <noun>"
    "Slide Slides Decline Declines Drop Drops Fall Falls Gain Gains Loss Losses Losing Winning "
    "Run Runs Slump Slumps Surge Surges Climb Climbs Advance Advances Retreat Retreats Skid "
    "Skids Rebound Rebounds High Highs Low Lows "
    # 2026-10-16 (round-2 review): the tape nouns it found passing ("Three-Day Plunge")
    "Plunge Plunges Tumble Tumbles Pullback Pullbacks Jump Jumps Dive Dives Dip Dips Slip "
    "Slips Spike Spikes Pop Pops Swoon Swoons Downturn Downturns Correction Corrections "
    "Upswing Upswings Run-Up Run-Ups"
).split()
_ADVANCE_TAILS = ("", ".", ",", ":", ";", " on Vaccine Hopes", " as Trials Widen",
                  " After Upgrade", " Amid Trial Data", " Despite Delays", " While Peers Lag",
                  " Following Upgrade", " With Peers")


def _word_row(row, layer, refuse, phrase, twin, ticker="CRWV", terms=CRWV_TERMS):
    return _Row(row.replace(" ", "_").lower(), layer, refuse, phrase, twin, ticker, terms)


_LEAN_ROWS += [
    _word_row(f"as_continues_{w}", "after_subject", f"CoreWeave Continues {w} on Debt Fears",
              f"Continues {w}", "CoreWeave Continues Expansion With New Data Center")
    for w in _CONTINUE_WORDS
]
_LEAN_ROWS += [
    _word_row(f"as_keeps_{w}", "after_subject", f"CoreWeave Keeps {w} on Debt Fears",
              f"Keeps {w}", "CoreWeave Keeps Growth Guidance Intact")
    for w in _KEEPS_WORDS
]
_LEAN_ROWS += [
    _word_row(f"as_advance_tail_{i}", "after_subject", f"Moderna Continues to Advance{t}",
              "Continues to Advance", "Moderna Continues to Advance Cancer Vaccine Program",
              "MRNA", ("MRNA", "moderna"))
    for i, t in enumerate(_ADVANCE_TAILS)
]
_LEAN_ROWS += [
    _word_row(f"as_keeps_advancing_tail_{i}", "after_subject", f"Moderna Keeps Advancing{t}",
              "Keeps Advancing", "Moderna Keeps Advancing Its Cancer Vaccine Program",
              "MRNA", ("MRNA", "moderna"))
    for i, t in enumerate(_ADVANCE_TAILS)
]
_LEAN_ROWS += [
    _word_row(f"tr_into_{v}", "trading", f"CoreWeave Notes {v} Into Q3 Report",
              f"{v} Into Q3 Report", "CoreWeave Faces SEC Probe Into Its Earnings Disclosures")
    for v in _INTO_VERBS
]
_LEAN_ROWS += [
    _word_row(f"tr_before_{v}", "trading", f"CoreWeave Notes {v} Before Q3 Report",
              f"{v} Before Q3 Report", "CoreWeave CEO Sold Shares Before Earnings, Filing Shows")
    for v in _BEFORE_WORDS
]
_LEAN_ROWS += [
    _word_row(f"tr_before_{v}", "trading", f"CoreWeave Notes Trade {v} Before Q3 Report",
              f"{v} Before Q3 Report", "CoreWeave Raises Cash Before Q3 Results")
    for v in ("Lower", "Higher")
]
_LEAN_ROWS += [
    _word_row(f"tr_before_{n}", "trading", f"CoreWeave Investor {n} Before Q3 Report",
              f"{n} Before Q3 Report", "CoreWeave Investor Day Before Q3 Report Draws Crowd")
    for n in _BEFORE_NOUNS
]
_LEAN_ROWS += [
    _word_row(f"win_hyphen_{n}", "window", f"CoreWeave Debt Fears Fuel Three-Day {n}",
              f"Three-Day {n}", "CoreWeave Moves to a Four-Day Work Week")
    for n in _HYPHEN_NOUNS
]
_LEAN_ROWS += [
    _word_row(f"win_hyphen_{u}", "window", f"CoreWeave Debt Fears Fuel Three-{u} Slide",
              f"Three-{u} Slide", "CoreWeave Grants Six-Month Lockup Waiver to Insiders")
    for u in ("Week", "Month", "Session", "Days")
]

# 2026-10-16 (round-2 review follow-ups). "aftermarket" / "post-close" are the tape only as an
# adverb or before a trading noun (`_LEAN_ADVERBIAL`) — every branch of that look-ahead, so
# dropping one turns its row red. Twins: the adjective sense before any other noun.
_LEAN_ROWS += [
    _word_row(f"tr_aftermarket_link_{w}", "trading",
              f"CoreWeave Notes Slip in the Aftermarket {w} Debt News", "Aftermarket",
              "CoreWeave Aftermarket Sales of Used GPUs Slow")
    for w in ("On", "As", "After", "Amid", "Despite", "Following", "While")
]
_LEAN_ROWS += [
    _word_row(f"tr_aftermarket_noun_{n}", "trading", f"CoreWeave Q3 Results Spark Aftermarket {n}",
              "Aftermarket", "CoreWeave Aftermarket Demand for Used GPUs Grows")
    for n in ("Trading", "Trade", "Trades", "Session", "Move", "Moves", "Movers", "Action",
              "Selling", "Buying")
]
_LEAN_ROWS += [
    _word_row(f"tr_aftermarket_punct_{i}", "trading",
              f"CoreWeave Q3 Results Released After-Market{p} Notes Slip", "After-Market",
              "CoreWeave Launches After-Market Services for GPU Buyers")
    for i, p in enumerate((",", ";", ":"))
]
_LEAN_ROWS += [
    _Row("tr_aftermarket_paren", "trading",
         "CoreWeave Notes Slip (Aftermarket) on Debt News", "Aftermarket",
         "CoreWeave Aftermarket Sales of Used GPUs Slow"),
    _Row("tr_post_close_trading", "trading", "CoreWeave Q3 Results Lift Post-Close Trading",
         "Post-Close", "CoreWeave Faces Post-Close Adjustments on Core Scientific Deal"),
    # the erase / give-back row's own alternatives the review found unpinned
    _Row("tr_erase_losses", "trading", "CoreWeave Erases Losses on Contract News",
         "Erases Losses", "CoreWeave Erases Debt Covenant Concerns"),
    _Row("tr_pare_losses", "trading", "CoreWeave Shares Pare Losses on Debt Deal",
         "Pare Losses", "CoreWeave Pares Workforce After Contract Loss"),
    _Row("tr_gave_up_gains", "trading", "CoreWeave Gave Up Gains on Debt Fears",
         "Gave Up Gains", "CoreWeave Gave Up Board Seat at Core Scientific"),
]
# The rival exemption (`_LEAN_NOT_RIVAL`) never covers the subject against its peers or the
# market, and a bare "to" is never a rival ("Continues to Slide to New Low") — each a pin.
_RIVAL_TWIN = "CoreWeave Continues Gains Against Nebius in AI Cloud"
_LEAN_ROWS += [
    _Row("as_continues_to_new_low", "after_subject", "CoreWeave Continues to Slide to New Low",
         "Continues to Slide", "CoreWeave Continues to Fall Behind Nebius in Europe"),
    _Row("as_continues_vs_peers", "after_subject", "CoreWeave Continues Its Slide Against Peers",
         "Continues Its Slide", _RIVAL_TWIN),
    _Row("as_continues_vs_rivals", "after_subject",
         "CoreWeave Continues Losses Against Rivals", "Continues Losses", _RIVAL_TWIN),
    _Row("as_continues_vs_its_peers", "after_subject",
         "CoreWeave Continues Its Slide Against Its Peers", "Continues Its Slide", _RIVAL_TWIN),
    _Row("as_continues_vs_their_rivals", "after_subject",
         "CoreWeave Shares Continue Their Slide Against Their Rivals",
         "Shares Continue Their Slide", _RIVAL_TWIN),
    _Row("as_continues_vs_broader_market", "after_subject",
         "CoreWeave Continues Gains Against the Broader Market", "Continues Gains", _RIVAL_TWIN),
    _Row("as_keeps_climbing_to_record", "after_subject", "CoreWeave Keeps Climbing to Record",
         "Keeps Climbing", "CoreWeave Keeps Losing Ground to Nebius in Europe"),
]
_LEAN_ROWS += [
    _word_row(f"as_continues_vs_the_{x}", "after_subject",
              f"CoreWeave Continues Its Slide Against the {x}", "Continues Its Slide", _RIVAL_TWIN)
    for x in ("Market", "Index", "Sector", "S&P 500", "Nasdaq", "Dow")
]

# Any event set for a weekday is a schedule: on the session's own day (LEAN_DAY is a
# Wednesday) only this row refuses it; on another day the weekday rule does as well.
_LEAN_ROWS += [
    _word_row(f"tr_set_weekday_{v}_{p}", "trading", f"CoreWeave Investor Day {v} {p} Wednesday",
              f"{v} {p} Wednesday", "CoreWeave Set for S&P 500 Inclusion")
    for v in ("Set", "Scheduled", "Slated") for p in ("for", "on")
]
_LEAN_ROWS += [
    _Row(f"tr_set_weekday_{d.lower()}", "trading", f"CoreWeave Investor Day Set for {d}",
         f"Set for {d}", "CoreWeave Set for S&P 500 Inclusion", also=("weekday",))
    for d in ("Monday", "Tuesday", "Thursday", "Friday", "Saturday", "Sunday")
]

# Round 2: the schedule rows' closed vocabularies, every entry in every row — the qualifier
# words between a schedule word and its event, and the events themselves.
_QUALS = ("Its", "The", "Their", "Q3", "Fiscal", "Quarterly", "Annual", "Full-Year", "Year-End",
          "Next", "First-Quarter", "Second-Quarter", "Third-Quarter", "Fourth-Quarter", "FY2026",
          "FY 2026", "2026")
_SCHEDULE_TEMPLATES = (  # (row, sample with {}, phrase with {}, honest twin)
    ("set_for", "CoreWeave Set for {}", "Set for {}", "CoreWeave Set for S&P 500 Inclusion"),
    ("before", "CoreWeave Notes Slip Before {}", "Slip Before {}",
     "CoreWeave Raises Cash Before Q3 Results"),
    ("into", "CoreWeave Notes Head Into {}", "Head Into {}",
     "CoreWeave Faces SEC Probe Into Its Earnings Disclosures"),
    ("eye", "CoreWeave Investors Eye {}", "Eye {}", "CoreWeave Eyes European Expansion"),
)
for _name, _tpl, _phr, _twin in _SCHEDULE_TEMPLATES:
    for _q in _QUALS:
        _LEAN_ROWS.append(_word_row(f"tr_{_name}_qual_{_q}", "trading", _tpl.format(f"{_q} Report"),
                                    _phr.format(f"{_q} Report"), _twin))
    for _e in ("Earnings", "Results", "Report", "Print", "Call"):
        _LEAN_ROWS.append(_word_row(f"tr_{_name}_event_{_e}", "trading", _tpl.format(f"Q3 {_e}"),
                                    _phr.format(f"Q3 {_e}"), _twin))
for _q in _QUALS:
    _LEAN_ROWS.append(_word_row(
        f"tr_to_report_qual_{_q}", "trading", f"CoreWeave to Report {_q} Results",
        f"to Report {_q} Results", "CoreWeave Misses Deadline to Report Q3 Results"))
for _e in ("Earnings", "Results", "Numbers"):
    _LEAN_ROWS.append(_word_row(
        f"tr_to_report_event_{_e}", "trading", f"CoreWeave to Report Q3 {_e}",
        f"to Report Q3 {_e}", "CoreWeave Misses Deadline to Report Q3 Results"))
for _e in ("Earnings", "Results", "Report", "Print", "Call"):
    _LEAN_ROWS.append(_word_row(
        f"tr_due_event_{_e}", "trading", f"CoreWeave Q3 {_e} Due Wednesday", f"{_e} Due",
        "CoreWeave Delays Q3 Report Due to Audit Review"))
    _LEAN_ROWS.append(_word_row(
        f"tr_scheduled_event_{_e}", "trading", f"CoreWeave Q3 {_e} Scheduled for Nov. 12",
        f"{_e} Scheduled for", "CoreWeave Misses Scheduled Debt Payment"))
for _e in ("Earnings", "Results", "Report", "Print", "Call", "Meeting", "Vote", "Decision", "IPO",
           "Earnings Season"):
    _LEAN_ROWS.append(_word_row(
        f"tr_nears_event_{_e}", "trading", f"CoreWeave Debt in Focus as Q3 {_e} Nears",
        f"{_e} Nears", "CoreWeave Nears $9 Billion Core Scientific Deal"))
for _v in ("Nears", "Nearing", "Loom", "Looms", "Looming", "Approaches", "Approaching"):
    _LEAN_ROWS.append(_word_row(
        f"tr_nears_verb_{_v}", "trading", f"CoreWeave Debt in Focus With Q3 Report {_v}",
        f"Report {_v}", "CoreWeave Debt Maturity Looms Over Expansion Plans"))
# The window rows' number words, each in both the spaced and the hyphenated row.
_NUMBER_WORDS = ("Two Three Four Five Six Seven Eight Nine Ten Eleven Twelve First Second Third "
                 "Fourth Fifth Sixth Seventh Eighth Ninth Tenth 21st 22nd 23rd 11th").split()
_LEAN_ROWS += [
    _word_row(f"win_spaced_number_{w}", "window", f"CoreWeave Debt Fears Weigh for {w} Days",
              f"{w} Days", f"CoreWeave Signs {w} Data Center Leases")
    for w in _NUMBER_WORDS
]
_LEAN_ROWS += [
    _word_row(f"win_hyphen_number_{w}", "window", f"CoreWeave Debt Fears Fuel {w}-Day Slide",
              f"{w}-Day Slide", f"CoreWeave Hosts {w}-Day Developer Summit")
    for w in _NUMBER_WORDS
]
_LEAN_ROWS += [
    _word_row(f"tr_eye_verb_{v}", "trading", f"CoreWeave Bondholder {v} Q3 Report", f"{v} Q3 Report",
              "CoreWeave Looks to Raise $2 Billion in Debt")
    for v in ("Watches", "Watching", "Looks to", "Looking to", "Look Toward", "Look Towards",
              "Turns to", "Turning to", "Eyes", "Eye")
]
_LEAN_ROWS += [
    _Row("tr_after_the_close", "trading", "CoreWeave Debt Deal Announced After the Close",
         "After the Close", "CoreWeave Wins Lender Support After Closing Core Scientific Deal"),
    _Row("tr_after_market_closed", "trading", "CoreWeave Debt Deal Announced After the Market Closed",
         "After the Market Closed", "CoreWeave Cuts Prices After Market Share Loss"),
    _Row("tr_late_trading", "trading", "CoreWeave Notes Weigh in Late Trading", "Late Trading",
         "CoreWeave Late Filing Draws Scrutiny"),
    _Row("tr_off_its_highs", "trading", "CoreWeave Notes Trade Off Its Highs", "Off Its Highs",
         "CoreWeave Pays Off Its High-Interest Bridge Loan"),
    _Row("tr_off_their_highs", "trading", "CoreWeave Bonds Trade Off Their Highs",
         "Off Their Highs", "CoreWeave Pays Off Their High-Cost Notes"),
    _Row("tr_off_lows", "trading", "CoreWeave Notes Trade Well Off Lows", "Off Lows",
         "CoreWeave Sells Off Lower-Margin Hosting Unit"),
    _Row("tr_off_peak", "trading", "CoreWeave Notes Trade Well Off Peak", "Off Peak",
         "CoreWeave Sells Off Peak-Hour GPU Capacity"),
    _Row("tr_off_peaks", "trading", "CoreWeave Notes Trade Well Off Peaks", "Off Peaks",
         "CoreWeave Sells Off Peaks-and-Valleys Hedging Book"),
    _Row("win_adds_to_their", "window", "CoreWeave Bonds Add to Their Recent Losses",
         "Add to Their Recent Losses", "CoreWeave Interest Bill Adds to Their Net Losses"),
    _Row("win_daily", "window", "CoreWeave Notes Post Daily Loss Amid Debt Fears", "Daily Loss",
         "CoreWeave Shifts to Daily Billing for GPU Rentals"),
]
_LEAN_ROWS += [
    _Row("tr_event_is_due_is", "trading", "CoreWeave Q3 Report Is Due Wednesday", "Report Is Due",
         "CoreWeave Q3 Report Is Delayed by Audit Review"),
    _Row("tr_event_are_scheduled", "trading", "CoreWeave Q3 Results Are Scheduled for Nov. 12",
         "Results Are Scheduled for", "CoreWeave Results Are Strong as AI Demand Booms"),
    _Row("tr_reversed_early_gains", "trading", "CoreWeave Reversed Early Gains on Debt Fears",
         "Reversed Early Gains", "CoreWeave Reversed Losses With First Quarterly Profit"),
    _Row("tr_reversing_early_gains", "trading", "CoreWeave Notes Reversing Early Gains",
         "Reversing Early Gains", "CoreWeave Reversing Losses With First Quarterly Profit",
         also=("price",)),
    _Row("tr_reverses_their_gains", "trading", "CoreWeave Bonds Reverse Their Gains",
         "Reverse Their Gains", "CoreWeave Bonds Reverse Their Losses After Upgrade"),
    *[_word_row(f"tr_reverses_qual_{_w}", "trading", f"CoreWeave Reverses {_w} Losses on AI Deal",
                f"Reverses {_w} Losses", "CoreWeave Reverses Losses With First Quarterly Profit")
      for _w in ("Early", "Earlier", "Initial", "Session", "Intraday", "Morning", "Opening")],
    _Row("as_added_to", "after_subject", "CoreWeave Added to Losses After Debt Report",
         "Added to Losses", "CoreWeave Added to S&P 500 Index"),
    _Row("as_adding_to", "after_subject", "CoreWeave Adding to Gains on AI Demand",
         "Adding to Gains", "CoreWeave Adding to Board After Activist Push"),
    _Row("as_shares_add_to_their", "after_subject", "CoreWeave Shares Add to Their Gains",
         "Shares Add to Their Gains", "CoreWeave Shares Add to Their Index Weight"),
    _Row("as_deepened", "after_subject", "CoreWeave Deepened Losses on Debt Fears",
         "Deepened Losses", "CoreWeave Deepened Ties With Nvidia"),
    _Row("as_shares_deepen_their", "after_subject", "CoreWeave Shares Deepen Their Slide",
         "Shares Deepen Their Slide", "CoreWeave Shares Deepen Their Index Weight"),
    _Row("tr_call_nears", "trading", "CoreWeave Debt in Focus as Earnings Call Nears",
         "Call Nears", "CoreWeave Debt Fears Grow as Margin Call Looms for Founders"),
    _Row("win_hyphen_stock_level", "window", "CoreWeave Stock at Six-Month High After Deal",
         "Six-Month High", "CoreWeave Grants Six-Month Lockup Waiver to Insiders"),
]

# Round 2: the honest senses each exemption exists for — each must stay accepted, and only
# its own exemption saves it (drop the exemption and the twin is refused).
_EXEMPT_TWINS = [
    # FDA: "premarket approval", "postmarket study" — never the tape
    *[(h, "MDT", ("MDT", "medtronic")) for h in (
        "Medtronic Ordered to Run Post-Market Study of Heart Device",
        "Medtronic Completes Post-Market Studies of Heart Device",
        "Medtronic Expands Postmarket Surveillance of Insulin Pumps",
        "Medtronic Wins FDA Premarket Approval for New Pump",
        "Medtronic Wins Two Premarket Approvals for Heart Devices",
        "Medtronic Faces FDA Postmarket Safety Review of Insulin Pump",
        "Medtronic Meets FDA Post-Market Requirement for Pump",
        "Medtronic Meets FDA Post-Market Requirements for Pumps",
        "Medtronic Fulfills Post-Market Commitment to FDA",
        "Medtronic Fulfills Post-Market Commitments to FDA",
        "Medtronic Submits Premarket Data on New Heart Valve",
        "Medtronic Files Premarket Application for New Heart Valve",
        "Medtronic Files Two Premarket Applications for Valves",
        "Medtronic Wins Premarket Clearance for Surgical Robot",
        "Medtronic Wins Premarket Clearances for Two Devices",
        # 2026-10-16 (round-2 review): the FDA nouns it found refused
        "Medtronic Ordered to Run Post-Market Trial of Insulin Pump",
        "Medtronic Ordered to Run Post-Market Trials of Insulin Pumps",
        "Medtronic Faces Post-Market Monitoring of Insulin Pumps",
        "Medtronic Wins FDA Premarket Authorization for New Pump",
        "Medtronic Wins Two Premarket Authorizations for Pumps",
        "Medtronic Device Clears FDA Premarket Review",
        "Medtronic Devices Clear Two Premarket Reviews",
        "Medtronic Files Premarket Notification for New Stent",
        "Medtronic Files Two Premarket Notifications for Stents",
    )],
    # aerospace / auto parts: "aftermarket" before any non-trading noun is the business
    ("RTX Raises Outlook on Commercial Aftermarket Strength", "RTX", ("RTX", "rtx")),
    ("HEICO Beats Estimates as Aftermarket Growth Accelerates", "HEI", ("HEI", "heico")),
    ("GE Aerospace Lifts Forecast as Aftermarket Booms", "GE", _GE),
    ("TransDigm Profit Tops Estimates on Aftermarket Margins", "TDG", ("TDG", "transdigm")),
    ("LKQ Cuts Guidance on Weak Aftermarket Volumes", "LKQ", ("LKQ", "lkq")),
    ("Howmet Raises Outlook on Commercial Aftermarket Demand", "HWM",
     ("HWM", "howmet aerospace", "howmet")),
    # a hyphenated window before a hyphen is a compound, not the tape (`(?!-)`)
    ("CoreWeave Opens Two-Week Pop-Up Store in Austin", "CRWV", CRWV_TERMS),
    ("CoreWeave Prices Six-Month High-Yield Notes", "CRWV", CRWV_TERMS),
    # a period's decline "in" / "of" a business metric is the business, not the tape
    ("Starbucks Posts Back-to-Back Declines in Same-Store Sales", "SBUX", ("SBUX", "starbucks")),
    ("Starbucks Reports Back-to-Back Declines of Store Traffic", "SBUX", ("SBUX", "starbucks")),
    ("Tesla Reports Monthly Decline in China Deliveries", "TSLA", ("TSLA", "tesla")),
    ("Tesla Posts Monthly Decline of China Registrations", "TSLA", ("TSLA", "tesla")),
    # "Earnings <noun>" after eyes / looks to is the business, not the schedule
    *[(f"Netflix Eyes Earnings {n} From Ad-Supported Tier", "NFLX", ("NFLX", "netflix"))
      for n in ("Boost", "Lift", "Growth", "Gain", "Gains", "Upside", "Hit", "Drag", "Benefit",
                "Benefits", "Tailwind", "Tailwinds", "Headwind", "Headwinds")],
    # call options are never the earnings call
    ("Intel Turns to Call Options Market for Hedging", "INTC", ("INTC", "intel")),
    ("Intel Eyes Call Option on Mobileye Stake", "INTC", ("INTC", "intel")),
    # "aftermarket" after a segment word, even at the end, is the business
    ("Howmet Raises Outlook on Commercial Aerospace Aftermarket", "HWM",
     ("HWM", "howmet aerospace", "howmet")),
    ("RTX Raises Outlook on Strong Commercial Aftermarket", "RTX", ("RTX", "rtx")),
    ("LKQ Gains Share in the Auto Aftermarket", "LKQ", ("LKQ", "lkq")),
    ("LKQ Bets on Growth in the Automotive Aftermarket", "LKQ", ("LKQ", "lkq")),
    ("HEICO Expands in the Aviation Aftermarket", "HEI", ("HEI", "heico")),
    ("TransDigm Wins Share in the Defense Aftermarket", "TDG", ("TDG", "transdigm")),
    ("GE Aerospace Sees Strong Engine Aftermarket", "GE", _GE),
    # the subject against a RIVAL, or ground / gains / losses "in" a market, is competition
    ("Intel Continues to Fall Behind TSMC on Process Tech", "INTC", ("INTC", "intel")),
    ("Intel Keeps Falling Behind TSMC in Chip Race", "INTC", ("INTC", "intel")),
    ("AMD Continues Gains Against Intel in Server Chips", "AMD", ("AMD", "advanced micro devices")),
    ("Apple Keeps Gaining Ground Against Samsung", "AAPL", ("AAPL", "apple")),
    ("Apple Keeps Losing Ground to Huawei in China", "AAPL", ("AAPL", "apple")),
    ("Apple Keeps Losing Ground in China", "AAPL", ("AAPL", "apple")),
    ("Amazon Continues Gains in Grocery Market Share", "AMZN", ("AMZN", "amazon.com")),
    ("Rivian Continues Losses in Commercial Vans", "RIVN", ("RIVN", "rivian automotive", "rivian")),
    # auto parts: "aftermarket sales" — never the tape
    *[(h, "GPC", ("GPC", "genuine parts")) for h in (
        "Genuine Parts Sees Weaker Aftermarket Part Supply",
        "Genuine Parts Sees Weaker Aftermarket Parts Supply",
        "Genuine Parts Aftermarket Sales Slow on Mild Weather",
        "Genuine Parts Aftermarket Business Faces Tariff Costs",
        "Genuine Parts Aftermarket Segment Margin Narrows",
        "Genuine Parts Expands Aftermarket Product Line",
        "Genuine Parts Launches Aftermarket Products in Europe",
        "Genuine Parts Aftermarket Service Unit Wins Contract",
        "Genuine Parts Aftermarket Services Unit Wins Contract",
        "Genuine Parts Aftermarket Revenue Misses Estimates",
        "Genuine Parts Aftermarket Demand Softens as Repairs Slow",
        "Genuine Parts Expands After-Market Parts Network",
    )],
    # a late filing is a catalyst, not a schedule
    ("Super Micro Fails to Report Annual Results on Time", "SMCI",
     ("SMCI", "super micro computer", "super micro")),
    ("CoreWeave Failed to Report Q3 Results on Time, Auditor Says", "CRWV", CRWV_TERMS),
    ("CoreWeave Faces Delisting After Failing to Report Annual Results", "CRWV", CRWV_TERMS),
    ("CoreWeave Misses Deadline to Report Q3 Results", "CRWV", CRWV_TERMS),
    ("CoreWeave Seeks Delay to Report Q3 Results", "CRWV", CRWV_TERMS),
    ("CoreWeave Wins Extension to Report Q3 Results", "CRWV", CRWV_TERMS),
    ("CoreWeave Expects to Report Q3 Results Below Guidance", "CRWV", CRWV_TERMS),
    ("CoreWeave Gets More Time to Report Annual Results", "CRWV", CRWV_TERMS),
    ("CoreWeave Says It Is Unable to Report Q3 Results on Time", "CRWV", CRWV_TERMS),
    # news out before the open is today's (`test_news_out_before_the_bell_is_todays_news`)
    ("CoreWeave Debt Deal Announced Before the Opening Bell", "CRWV", CRWV_TERMS),
    ("CoreWeave Prices Notes Before Opening Bell", "CRWV", CRWV_TERMS),
]


@pytest.mark.parametrize("h,ticker,terms", _EXEMPT_TWINS)
def test_each_exemptions_honest_sense_is_kept(h, ticker, terms):
    assert usable_lean_headline(h, ticker, terms, LEAN_DAY) == h, (h, _refusals(h, ticker, terms))


# Round 2 (2026-10-16) — the review's own lists, end to end: the honest shapes the round-1
# rows refused, and the tape / timing samples that must still be refused.
_ROUND2_HONEST = [
    ("Intel Warns of Continued Decline in PC Demand", "INTC", ("INTC", "intel")),
    ("CoreWeave Reports Continued Decline in Gross Margin", "CRWV", CRWV_TERMS),
    ("CoreWeave Posts Continued Losses as Interest Costs Mount", "CRWV", CRWV_TERMS),
    ("CoreWeave Reports Continued Gains in Market Share", "CRWV", CRWV_TERMS),
    ("Pfizer Covid Revenue Continues Its Decline", "PFE", ("PFE", "pfizer")),
    ("Starbucks Same-Store Sales Keep Falling as Traffic Weakens", "SBUX", ("SBUX", "starbucks")),
    ("CoreWeave Raises Prices as GPU Demand Keeps Rising", "CRWV", CRWV_TERMS),
    ("CoreWeave Revenue Continues to Rise on AI Demand", "CRWV", CRWV_TERMS),
    ("Moderna Continues to Advance Cancer Vaccine Program", "MRNA", ("MRNA", "moderna")),
    ("Target Cuts Prices as Holiday Season Approaches", "TGT", _TGT),
    ("Macy's Cuts Forecast as Holiday Season Nears", "M", ("M", "macy's")),
    ("CoreWeave Debt Fears Grow as Margin Call Looms for Founders", "CRWV", CRWV_TERMS),
    ("CoreWeave Pays Off Its High-Interest Bridge Loan", "CRWV", CRWV_TERMS),
    ("CoreWeave Pays Off Its High-Cost Debt Early", "CRWV", CRWV_TERMS),
    ("CoreWeave Sells Off Its Low-Margin Hosting Unit", "CRWV", CRWV_TERMS),
    ("Super Micro Fails to Report Annual Results on Time", "SMCI",
     ("SMCI", "super micro computer", "super micro")),
    ("CoreWeave Misses Deadline to Report Q3 Results", "CRWV", CRWV_TERMS),
    ("CoreWeave Reverses Losses With First Quarterly Profit", "CRWV", CRWV_TERMS),
    ("CoreWeave Reverses Losses, Posts First Operating Profit", "CRWV", CRWV_TERMS),
    ("CoreWeave Faces Six-Month Lockup Expiry for Early Investors", "CRWV", CRWV_TERMS),
    ("CoreWeave Grants Six-Month Lockup Waiver to Insiders", "CRWV", CRWV_TERMS),
    ("CoreWeave Moves to a Four-Day Work Week", "CRWV", CRWV_TERMS),
    ("CoreWeave Lands First-Day Contract Win at Pentagon", "CRWV", CRWV_TERMS),
    ("Rivian Posts Back-to-Back Losses Despite Delivery Gains", "RIVN", ("RIVN", "rivian")),
    ("Medtronic Ordered to Run Post-Market Study", "MDT", ("MDT", "medtronic")),
    ("Pfizer Faces FDA Postmarket Safety Requirement", "PFE", ("PFE", "pfizer")),
    ("Intuitive Surgical Wins FDA Premarket Approval", "ISRG", ("ISRG", "intuitive surgical")),
    ("CoreWeave Set for S&P 500 Inclusion", "CRWV", CRWV_TERMS),
    ("CoreWeave Core Scientific Deal Slated to Close in First Quarter", "CRWV", CRWV_TERMS),
    ("CoreWeave CEO Sold Shares Before Earnings, Filing Shows", "CRWV", CRWV_TERMS),
    ("CoreWeave Faces SEC Probe Into Its Earnings Disclosures", "CRWV", CRWV_TERMS),
    ("CoreWeave Announces Acquisition Before Market Open", "CRWV", CRWV_TERMS),
    ("CoreWeave Adds to Board With Former Nvidia Executive", "CRWV", CRWV_TERMS),
]
_ROUND2_REFUSED = [
    "CoreWeave Continues Slide", "CoreWeave Shares Continue to Slide on Debt Worries",
    "CoreWeave Keeps Falling", "CoreWeave Extends Continued Slide",
    "CoreWeave's Continued Slide Worries Lenders", "CoreWeave Stock Continue Lower on Debt Fears",
    "CoreWeave Reverses Early Gains", "CoreWeave Reverses Session Losses",
    "CoreWeave Gives Up Gains", "CoreWeave's Three-Day Slide Deepens",
    "CoreWeave Notes Post Two-Week Decline",
    "CoreWeave Q3 Report Scheduled for Wednesday", "CoreWeave Set for Q3 Report on Oct 14",
    "CoreWeave Q3 Report Slated for Nov. 12", "CoreWeave Q3 Report Scheduled on Nov. 12",
    "CoreWeave Adds to Losses After Debt Report", "CoreWeave Shares Add to Losses on Debt",
    "CoreWeave Deepens Losses on Debt Fears", "CoreWeave Q3 Earnings Due Wednesday",
    "CoreWeave Will Report Q3 Results Nov. 12", "CoreWeave Q3 Results Expected Post-Close",
    "CoreWeave Q3 Results Released After-Market",
    "CoreWeave Q3 Results Land in the Aftermarket",
    "CoreWeave Investors Look Forward to Q3 Results", "CoreWeave Investors Watch for Q3 Results",
]


@pytest.mark.parametrize("h,ticker,terms", _ROUND2_HONEST)
def test_round_twos_honest_shapes_are_kept(h, ticker, terms):
    assert usable_lean_headline(h, ticker, terms, LEAN_DAY) == h, (h, _refusals(h, ticker, terms))


@pytest.mark.parametrize("headline", _ROUND2_REFUSED)
def test_round_twos_tape_and_timing_samples_are_refused(headline):
    assert _usable(headline) is None, headline


# Round 2: news released before the bell / the open IS a same-day cause — those rows are
# gone. The tape before the bell is still refused: by the subject's own verb, or by the price
# guard ("Shares Fall": the subject-verb list has "falls", not the plural "fall").
_BEFORE_THE_BELL_NEWS = [
    "CoreWeave Announces Acquisition Before Market Open",
    "CoreWeave Debt Deal Announced Before Markets Open",
    "CoreWeave Debt Deal Announced Before the Bell",
    "CoreWeave Prices Notes Before the Open",
    "CoreWeave Announces Buyback Before the Closing Bell",
    "CoreWeave Debt Deal Announced Before the Opening Bell",
    "CoreWeave Debt Deal Announced Before the Close",
]


@pytest.mark.parametrize("headline", _BEFORE_THE_BELL_NEWS)
def test_news_out_before_the_bell_is_todays_news(headline):
    assert _usable(headline) == headline, (headline, _refusals(headline))


@pytest.mark.parametrize(
    "headline",
    ["CoreWeave Shares Fall Before the Bell", "CoreWeave Slides Before the Open",
     "CoreWeave Jumps Before the Opening Bell"],
)
def test_the_tape_before_the_bell_is_refused_by_the_subject_verb_or_the_price_guard(headline):
    got = _refusals(headline)
    assert got and set(got) <= {"after_subject", "price"}, (headline, got)
    assert _usable(headline) is None, headline


# Other spellings of the rows above: refused end to end (their row is pinned by its sample).
_ROW_SPELLINGS = [
    "CoreWeave Notes Draw Postmarket Selling",
    "CoreWeave Debt Deal Draws Premarket Selling",
    "CoreWeave Debt Deal Announced After-Hours",
    "CoreWeave Debt Deal Announced After the Market Closes",
    "CoreWeave Investors Turn to Q3 Results",
    "CoreWeave Investors Look Ahead to Fiscal Q3 Earnings",
    "CoreWeave Investors Look Toward Fiscal 2026 Earnings",
    "CoreWeave Debt in Focus as Earnings Season Approaches",
    "CoreWeave Debt in Focus as Its Q3 Report Nears",
    "CoreWeave Notes Slip Before Its Third-Quarter Earnings",
    "CoreWeave Stock Continues Its Slide on Debt Fears",
    "CoreWeave Keeps Losing Ground on Debt Fears",
    "CoreWeave Debt Fears Add to Recent Losses",
    "CoreWeave Debt Fears Fuel Two-Week Decline",
    "CoreWeave Debt Fears Weigh for Third Straight Session",
    "CoreWeave Posts Biggest Weekly Drop on Debt Fears",
    "CoreWeave Winning Streak Stretches to Five Sessions",
    "CoreWeave Q3 Report Is Slated for Nov. 12",
    "CoreWeave Q3 Earnings Call Set to Begin at Noon",
    "CoreWeave Q3 Results Released Postclose",
    "CoreWeave Notes Post Back to Back Weekly Declines",
]

# The ambiguous verbs' news senses, named in the module's own comments.
_HONEST_COLLOCATIONS = [
    "CoreWeave Extends Partnership With Nvidia",
    "CoreWeave Gains Approval for Texas Data Center",
    "CoreWeave Leads Funding Round for AI Startup",
    "CoreWeave Hits Milestone With Texas Data Center",
]


def _row_id(r):
    return r.row


@pytest.mark.parametrize("r", _LEAN_ROWS, ids=_row_id)
def test_each_rows_sample_is_refused(r):
    assert usable_lean_headline(r.refuse, r.ticker, r.terms, LEAN_DAY) is None, r.refuse


@pytest.mark.parametrize("r", _LEAN_ROWS, ids=_row_id)
def test_each_rows_honest_twin_is_kept(r):
    assert usable_lean_headline(r.twin, r.ticker, r.terms, LEAN_DAY) == r.twin, r.twin


@pytest.mark.parametrize("r", _LEAN_ROWS, ids=_row_id)
def test_each_rows_sample_is_refused_by_that_row_alone(r):
    """The pin: delete the row and this sample passes the gate (or matches other text).
    A failure that adds a layer means a new row overlaps this sample — pick a sample only
    this row refuses, never widen `also` to make it green."""
    got = _refusals(r.refuse, r.ticker, r.terms)
    assert set(got) == {r.layer, *r.also}, (r.refuse, got)
    if r.phrase is not None:
        assert got[r.layer].lower() == r.phrase.lower(), (r.refuse, got)


def test_the_row_ids_are_unique():
    ids = [r.row for r in _LEAN_ROWS]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("headline", _ROW_SPELLINGS)
def test_other_spellings_of_the_rows_are_refused(headline):
    assert _usable(headline) is None, headline


@pytest.mark.parametrize("headline", _HONEST_COLLOCATIONS)
def test_the_ambiguous_verbs_news_senses_are_kept(headline):
    assert _usable(headline) == headline


# Every verb `_LEAN_AFTER_SUBJECT_RE` lists on its own. Literals, not the production regex:
# most of them are ALSO refused by the price guard, so only this list notices one deleted.
_SUBJECT_VERBS = (
    "falls fell falling slides slid sliding drops dropped dropping plunges plunged plummets "
    "plummeted tumbles tumbled sinks sank sinking slumps slumped retreats retreated dips "
    "dipped tanks tanked craters cratered slips slipped skids skidded rises rose rising jumps "
    "jumped climbs climbed soars soared surges surged rallies rallied rebounds rebounded "
    "bounce bounces bounced recovers recovered spikes spiked pops popped underperforms "
    "outperforms outpaces lags lagged trails trailed down up lower higher"
).split()


@pytest.mark.parametrize("verb", _SUBJECT_VERBS)
def test_every_tape_verb_right_after_the_subject_is_refused_by_that_rule(verb):
    h = f"CoreWeave {verb.title()} on Debt Fears"
    assert _usable(h) is None, h
    assert _refusals(h).get("after_subject", "").lower() == verb, (h, _refusals(h))


@pytest.mark.parametrize(
    "tape,news",
    [
        ("CoreWeave Falls as Q3 Results Disappoint", "CoreWeave Q3 Results Disappoint on Wider Loss"),
        ("CoreWeave Slides on Debt Concerns", "CoreWeave Faces Debt Concerns Over Note Offering"),
        ("CoreWeave Drops After Guidance Cut", "CoreWeave Cuts Guidance on Weaker Demand"),
        ("CoreWeave Falls After Losing Key Customer",
         "CoreWeave Cuts Guidance After Losing Key Customer"),
    ],
)
def test_after_an_event_is_news_and_only_the_tape_verb_refuses_these(tape, news):
    """"After <event>" is never the timing rows' business — only "after the bell / close /
    market close / hours" is. These four are refused for their OWN subject's tape verb
    ("CoreWeave Falls…": the move offered as its own cause, which the price guard refuses
    too); the same story told without it stands."""
    got = _refusals(tape)
    assert "after_subject" in got and set(got) <= {"after_subject", "price"}, (tape, got)
    assert _usable(news) == news, news


def test_the_layer_probe_agrees_with_the_gate():
    """`_refusals` re-implements the gate's layers to tell them apart; this keeps the two in
    step on every headline in this section (a probe that drifted would pin nothing)."""
    cases = [(r.refuse, r.ticker, r.terms) for r in _LEAN_ROWS]
    cases += [(r.twin, r.ticker, r.terms) for r in _LEAN_ROWS]
    cases += [(h, "CRWV", CRWV_TERMS) for h in _ROW_SPELLINGS + _HONEST_COLLOCATIONS]
    cases += [(h, "CRWV", CRWV_TERMS) for h in _ROUND2_REFUSED + _BEFORE_THE_BELL_NEWS]
    cases += _EXEMPT_TWINS + _ROUND2_HONEST
    cases += [(f"CoreWeave {v.title()} on Debt Fears", "CRWV", CRWV_TERMS)
              for v in _SUBJECT_VERBS]
    for h, ticker, terms in cases:
        refused = usable_lean_headline(h, ticker, terms, LEAN_DAY) is None
        assert refused == bool(_refusals(h, ticker, terms)), (h, _refusals(h, ticker, terms))


# ── the post-close rule ─────────────────────────────────────────────────────


_AMC_TODAY = {"date": "2026-10-14", "time": "amc", "epsActual": -0.21, "epsEstimated": 0.05}


def test_a_card_written_after_an_after_close_print_cannot_explain_the_session():
    """NVDA-shaped: the stock fell in the session, then missed after the bell; a 16:35 card
    about the miss is not why the session fell (`detect_earnings` refuses the same)."""
    a = _crwv(news_lean=_lean(headline="CoreWeave Misses Revenue Estimates",
                              generated_at="2026-10-14T20:35:00Z"),
              earnings_row=_AMC_TODAY)
    assert a.kind is CauseKind.NONE
    assert a.detail.endswith("Today's news leaned bearish."), a.detail


def test_the_same_card_inside_the_session_is_still_used_on_a_report_day():
    a = _crwv(news_lean=_lean(generated_at="2026-10-14T19:59:00Z"), earnings_row=_AMC_TODAY)
    assert a.kind is CauseKind.COMPANY_NEWS


def test_the_post_close_slack_is_fifteen_minutes():
    ok = _crwv(news_lean=_lean(generated_at="2026-10-14T20:15:00Z"), earnings_row=_AMC_TODAY)
    late = _crwv(news_lean=_lean(generated_at="2026-10-14T20:16:00Z"), earnings_row=_AMC_TODAY)
    assert ok.kind is CauseKind.COMPANY_NEWS
    assert late.kind is CauseKind.NONE


def test_a_half_day_closes_at_one():
    """Black Friday 2026-11-27 closes at 13:00 ET: a 13:30 card is post-close."""
    from app.utils.market_hours import US_MARKET_EARLY_CLOSES

    half = date(2026, 11, 27)
    if (half.year, half.month, half.day) not in US_MARKET_EARLY_CLOSES:
        pytest.skip("2026-11-27 is not in the early-close table")
    row = {"date": "2026-11-27", "time": "amc", "epsActual": -0.2, "epsEstimated": 0.1}
    late = _crwv(today=half, news_lean=_lean(generated_at="2026-11-27T18:30:00Z", day=half),
                 earnings_row=row)
    early = _crwv(today=half, news_lean=_lean(generated_at="2026-11-27T17:00:00Z", day=half),
                  earnings_row=row)
    assert late.kind is CauseKind.NONE
    assert early.kind is CauseKind.COMPANY_NEWS


def test_a_post_close_card_without_a_report_is_accepted():
    """Documented residual (owner-facing, SYSTEM_DESIGN_GUIDELINES): after-hours articles on an
    ordinary big day are usually ABOUT the day's move — refusing them would hide the
    headline every evening, exactly when the tile is read."""
    a = _crwv(news_lean=_lean(generated_at="2026-10-14T22:30:00Z"))
    assert a.kind is CauseKind.COMPANY_NEWS


@pytest.mark.parametrize("stamp", [None, "", "garbage"])
def test_an_unplaceable_card_on_a_report_day_is_refused(stamp):
    a = _crwv(news_lean=_lean(generated_at=stamp), earnings_row=_AMC_TODAY)
    assert a.kind is CauseKind.NONE


def test_a_round_the_clock_card_has_no_close_to_be_after():
    a = _crwv(ticker="BTCUSD", news_lean=_lean(
        headline="Bitcoin Faces New Regulatory Scrutiny in Europe",
        terms=("BTCUSD", "BTC", "Bitcoin"), generated_at="2026-10-14T23:30:00Z", rolling=True),
        earnings_row={"date": "2026-10-14", "time": "amc", "epsActual": 1.0})
    assert a.kind is CauseKind.COMPANY_NEWS, a.detail


# ── precedence and the note ─────────────────────────────────────────────────


def test_dated_causes_outrank_the_lean():
    earn = _crwv(earnings_row={"date": "2026-10-14", "time": "bmo",
                               "epsActual": -0.4, "epsEstimated": 0.1})
    assert earn.kind is CauseKind.EARNINGS
    assert earn.note == "Software - Infrastructure fell 4.0%; CRWV moved far more."
    grade = _crwv(grade_rows=[{"date": "2026-10-14", "gradingCompany": "Acme", "action": "downgrade",
                               "previousGrade": "Buy", "newGrade": "Sell"}])
    assert grade.kind is CauseKind.ANALYST
    classified = _crwv(classified_news=[("Guidance Cut", "CoreWeave lowers guidance")])
    assert classified.kind is CauseKind.COMPANY_NEWS and classified.tag == "Guidance Cut"
    assert classified.note is not None


def test_a_move_in_line_with_its_industry_stays_a_sector_move():
    a = _crwv(change_percent=-4.2, z=2.5, industry_change_percent=-4.0)
    assert a.kind is CauseKind.SECTOR
    assert a.note is None


def test_the_note_is_only_beside_a_cause_that_does_not_already_say_it():
    assert _crwv(news_lean=None).note is None                                  # NONE
    assert _crwv(change_percent=-4.2, industry_change_percent=-4.0).note is None  # SECTOR
    assert _crwv(industry_name=None, industry_change_percent=None).note is None  # nothing true to add


def test_the_note_names_an_in_line_industry_only_under_an_established_cause():
    from app.services.daily_move_attribution import MoveContext

    ctx = MoveContext(change_percent=-4.2, industry_name="Tech", industry_change_percent=-4.0)
    assert context_note("X", ctx) == "Tech fell 4.0%; X moved with it."
    assert describe_context("X", ctx) == []        # never reaches describe_no_cause
    flat = MoveContext(change_percent=0.0, industry_name="Beverages", industry_change_percent=-1.2)
    assert context_note("KO", flat) == "KO was flat while Beverages fell 1.2%."


@pytest.mark.parametrize("word", ["today", "on Fri", "on Tue"])
def test_the_lean_and_its_note_are_identical_on_an_aged_tile(word):
    """`detail_aged` reuses the lean verbatim and the note has no session word, so neither
    can contradict a "Tue close" footer."""
    a = _crwv(session_word=word)
    assert a.detail == CRWV_HEADLINE + "."
    for w in ("today", "yesterday", "tonight", "this morning"):
        assert w not in (a.note or "").lower()


def test_a_withheld_lean_names_the_session_it_describes():
    a = _crwv(z=1.5, session_word="on Fri")
    assert a.detail.endswith("Friday's news leaned bearish."), a.detail


def test_an_unchecked_lookup_never_states_a_lean():
    a = _crwv(z=1.5, news_checked=False)
    assert "leaned" not in a.detail


def test_a_malformed_lean_degrades_rather_than_breaking_the_widget():
    a = _crwv(news_lean=object())
    assert a.kind is CauseKind.NONE
    assert a.detail


def test_the_lean_tags_are_the_two_the_chat_pins():
    assert NEWS_LEAN_TAGS == {"bearish": "Bearish News", "bullish": "Bullish News"}
