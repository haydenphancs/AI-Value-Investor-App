"""Activity / dormancy classification — `_whale_common.compute_activity`.

A tracked filer can stop producing data at any time. Without a signal the app renders
that as a BROKEN screen: Michael Burry's profile served a confident $1.37B portfolio and
+11.06% return next to zero holdings and zero trades, with the only hint being a
"Q3 2025" tile caption.

Two things this file exists to pin, because getting either wrong is worse than shipping
nothing:

1. **13F staleness is counted in MISSED QUARTERS, never in days.** A 13F is due 45 days
   after quarter end, so EVERY healthy filer is ~51 days stale the moment a quarter
   closes. A day-based threshold would flag all 45 of them.
2. **Congress is not 13F.** A member who does not trade files nothing, so silence is not
   evidence of retirement. `dormant`/`inactive` must be unreachable for a congressional
   filer — the strongest honest statement is the DATE of their last disclosure.

Dates below are the REAL production values measured on 2026-08-20.
"""

from datetime import datetime, timezone

import pytest

from app.services._whale_common import (
    ACTIVITY_CURRENT,
    ACTIVITY_DORMANT,
    ACTIVITY_LATE,
    ACTIVITY_NONE,
    ACTIVITY_QUIET,
    ACTIVITY_UNKNOWN,
    CONGRESS_QUIET_DAYS,
    compute_activity,
)

# Expected 13F quarter on this date is Q2 2026 (Q2 ended Jun 30; +45d deadline passed).
NOW = datetime(2026, 8, 20, tzinfo=timezone.utc)


# ── The 51-day trap ──────────────────────────────────────────────────────────


def test_a_healthy_filer_is_current_despite_being_51_days_stale():
    """THE trap. Q2 2026 ended 2026-06-30, so on 2026-08-20 every one of the 43 current
    filers is 51 days past their newest filing. A day-based rule flags all of them."""
    a = compute_activity("13f", "2026-Q2", "2026-06-30", now=NOW)
    assert a.status == ACTIVITY_CURRENT
    assert a.label == "", "a current filer must carry no chip copy"
    assert a.needs_disclosure is False


def test_the_whole_healthy_cohort_stays_silent():
    for date in ("2026-06-30", "2026-07-15", "2026-08-01"):
        assert compute_activity("13f", "2026-Q2", date, now=NOW).needs_disclosure is False


# ── 13F: missed quarters ─────────────────────────────────────────────────────


def test_one_missed_quarter_is_late_not_dormant():
    """Bill Ackman, real. One quarter can be a late filer or an NT 13F; it is not
    evidence that they have stopped."""
    a = compute_activity("13f", "2026-Q1", "2026-03-31", now=NOW)
    assert a.status == ACTIVITY_LATE
    assert a.label == "Last filed Q1 2026"


def test_three_missed_quarters_is_dormant():
    """Michael Burry, real — Scion's last 13F covers Q3 2025."""
    a = compute_activity("13f", "2025-Q3", "2025-09-30", now=NOW)
    assert a.status == ACTIVITY_DORMANT
    assert a.label == "Last filed Q3 2025"
    assert a.as_of == "Q3 2025"


def test_exactly_two_missed_quarters_is_the_dormant_boundary():
    assert compute_activity("13f", "2025-Q4", None, now=NOW).status == ACTIVITY_DORMANT
    assert compute_activity("13f", "2026-Q1", None, now=NOW).status == ACTIVITY_LATE


def test_quarter_arithmetic_crosses_a_year_boundary():
    """Q4 2025 -> Q2 2026 is 2 quarters, not 2 years or -2."""
    a = compute_activity("13f", "2025-Q4", None, now=NOW)
    assert a.status == ACTIVITY_DORMANT
    # And a January reference: expected quarter is Q3 2025, so Q3 2025 is current.
    jan = datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert compute_activity("13f", "2025-Q3", None, now=jan).status == ACTIVITY_CURRENT


def test_a_filer_ahead_of_the_expected_quarter_is_current_not_negative():
    """An early filer must not wrap into 'dormant' through a negative diff."""
    a = compute_activity("13f", "2026-Q4", "2026-12-31", now=NOW)
    assert a.status == ACTIVITY_CURRENT


# ── Congress: date only, never a claim about office ──────────────────────────


@pytest.mark.parametrize(
    "name,last",
    [("John Boozman", "2026-08-17"), ("Nancy Pelosi", "2026-06-24"),
     ("Markwayne Mullin", "2026-03-10")],
)
def test_recently_disclosing_members_are_current(name, last):
    assert compute_activity("congressional_senate", None, last, now=NOW).status == ACTIVITY_CURRENT


def test_a_long_quiet_member_states_the_date_only():
    """Ted Cruz, real: last disclosure 2025-11-12, a sitting senator."""
    a = compute_activity("congressional_senate", None, "2025-11-12", now=NOW)
    assert a.status == ACTIVITY_QUIET
    assert a.label == "No trades disclosed since Nov 2025"


def test_a_member_who_never_traded_says_so_plainly():
    """Mark Kelly, real: FMP returns zero rows for him."""
    a = compute_activity("congressional_senate", None, None, now=NOW)
    assert a.status == ACTIVITY_NONE
    assert a.label == "No trades disclosed yet"


@pytest.mark.parametrize("source", ["congressional_senate", "congressional_house"])
@pytest.mark.parametrize("last", ["2025-11-12", "2019-01-01", None, "", "garbage"])
def test_congress_is_never_dormant_or_inactive(source, last):
    """The load-bearing one. Labelling a sitting member 'inactive' because they did not
    trade is a false statement about a real named person."""
    a = compute_activity(source, None, last, now=NOW)
    assert a.status != ACTIVITY_DORMANT
    assert a.status != ACTIVITY_LATE
    assert "inactive" not in a.label.lower()
    assert "dormant" not in a.label.lower()
    assert "retire" not in a.label.lower()


def test_the_quiet_boundary_is_exactly_the_constant():
    from datetime import timedelta
    just_inside = (NOW - timedelta(days=CONGRESS_QUIET_DAYS - 1)).strftime("%Y-%m-%d")
    just_outside = (NOW - timedelta(days=CONGRESS_QUIET_DAYS + 1)).strftime("%Y-%m-%d")
    assert compute_activity("congressional_house", None, just_inside, now=NOW).status == ACTIVITY_CURRENT
    assert compute_activity("congressional_house", None, just_outside, now=NOW).status == ACTIVITY_QUIET


# ── Malformed / degenerate inputs ────────────────────────────────────────────


def test_a_congressional_month_period_never_parses_as_a_quarter():
    """Congressional snapshots key on a wall-clock month (`2026-08`). Parsing that as a
    quarter would render a politician as having filed a 13F they never file."""
    a = compute_activity("13f", "2026-08", "2025-11-12", now=NOW)
    assert a.status != ACTIVITY_DORMANT or a.as_of != "Q8 2026"
    assert "Q8" not in (a.label or ""), a.label


@pytest.mark.parametrize(
    "period", [None, "", "  ", "garbage", "2026-Q5", "2026-Q0", "26-Q1", "2026Q1", 2026, {}]
)
def test_malformed_filing_periods_do_not_raise_or_fabricate(period):
    a = compute_activity("13f", period, "2026-06-30", now=NOW)
    assert a.status in {ACTIVITY_CURRENT, ACTIVITY_QUIET, ACTIVITY_NONE}
    assert "None" not in (a.label or "")


@pytest.mark.parametrize("bad", [None, "", "not-a-date", "2026-13-45", 12345, {}, []])
def test_malformed_dates_degrade_to_none_not_a_crash(bad):
    a = compute_activity("congressional_house", None, bad, now=NOW)
    assert a.status == ACTIVITY_NONE


@pytest.mark.parametrize("source", [None, "", "manual", "crypto", 123])
def test_an_unknown_data_source_falls_back_to_the_date_rule(source):
    """Never treated as 13F, so an unknown source can never be called 'dormant'."""
    a = compute_activity(source, "2025-Q3", "2026-08-01", now=NOW)
    assert a.status == ACTIVITY_CURRENT


def test_a_naive_now_is_accepted():
    """The service passes an aware clock, but a caller must not crash on a naive one."""
    a = compute_activity("13f", "2025-Q3", None, now=datetime(2026, 8, 20))
    assert a.status == ACTIVITY_DORMANT


def test_unknown_status_never_asks_for_a_chip():
    from app.services._whale_common import Activity
    assert Activity(ACTIVITY_UNKNOWN, "", None).needs_disclosure is False


# ── The roster chip must stay a chip ───────────────────────────────────────────
#
# A curated `lifecycle_note` used to be substituted straight into `activity_label`.
# `activity_label` is the ROSTER CHIP — `WhaleResponse.activity_label`, documented as e.g.
# "Last filed Q3 2025" — and iOS renders it in a `TintedTagBadge` capsule. Michael Burry's
# note is 172 characters, so on the Tracking roster the capsule wrapped into a near-square
# block and `Capsule()` drew it as a CIRCLE with the sentence clipped inside. Captured on
# device. The prose has its own channel (`lifecycle_note` → the profile's multi-line
# `WhaleActivityNotice`), so nothing is lost by keeping the chip short.

_BURRY_NOTE = (
    "Scion Asset Management deregistered as an investment adviser in 2025. Its last "
    "Form 13F covers Q3 2025, so the positions shown here are that filing and will not "
    "be updated."
)

# Longer than any legitimate chip. The real ones today are 18-34 characters
# ("Last filed Q1 2026", "No trades disclosed since Nov 2025").
_MAX_CHIP_LEN = 48


def test_a_curated_note_never_becomes_the_roster_chip():
    from app.services.whale_service import _activity_fields

    fields = _activity_fields({
        "data_source": "13f",
        "last_filing_period": "2025-Q3",
        "last_activity_date": None,
        "lifecycle_status": "inactive",
        "lifecycle_note": _BURRY_NOTE,
    })

    assert fields["activity_status"] == "inactive"
    assert _BURRY_NOTE not in fields["activity_label"], (
        "the curated prose is being served as the roster chip again — it wraps the "
        "TintedTagBadge capsule into a circle with the text clipped inside"
    )
    assert len(fields["activity_label"]) <= _MAX_CHIP_LEN, (
        f"activity_label is {len(fields['activity_label'])} chars; it is rendered in a "
        f"capsule badge and must stay short"
    )
    # Anti-vacuity: it must still SAY something, and say the useful derived thing.
    assert fields["activity_label"] == "Last filed Q3 2025"


def test_a_curated_retirement_still_shows_a_chip_when_filings_look_current():
    """The case that makes the fallback load-bearing.

    A filer curated inactive who is still filing on cadence derives ACTIVITY_CURRENT with an
    EMPTY label. iOS renders a chip only when `activityStatus` AND `activityLabel` are both
    non-empty, so simply dropping the note would have made the disclosure vanish entirely —
    a silent regression that looks like "no chip needed".
    """
    from app.services.whale_service import _activity_fields

    fields = _activity_fields({
        "data_source": "congressional_house",
        "last_filing_period": None,
        "last_activity_date": datetime.now(timezone.utc).date().isoformat(),
        "lifecycle_status": "retired",
        "lifecycle_note": "Retired from Congress in January 2027.",
    })

    assert fields["activity_status"] == "inactive"
    assert fields["activity_label"], "a curated-inactive filer must still carry a chip"
    assert len(fields["activity_label"]) <= _MAX_CHIP_LEN


def test_an_active_filer_carries_no_chip_even_with_a_note():
    """A note on an ACTIVE row is not a disclosure — it must not raise a chip."""
    from app.services._whale_common import latest_filed_13f_quarter
    from app.services.whale_service import _activity_fields

    year, quarter = latest_filed_13f_quarter()

    fields = _activity_fields({
        "data_source": "13f",
        "last_filing_period": f"{year}-Q{quarter}",
        "last_activity_date": None,
        "lifecycle_status": "active",
        "lifecycle_note": _BURRY_NOTE,
    })
    assert fields["activity_label"] == ""
    assert fields["activity_status"] == ""
