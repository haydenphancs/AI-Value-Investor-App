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
