"""The SEC check on FMP's quarterly buyback figure (`app/services/_buyback_verification.py`).

The user saw INTC's Signal of Confidence chart draw green "Buybacks" of $181M (Q2'25) and
$617M (Q2'26). Intel bought back nothing: those are "Restricted stock unit withholdings"
(XBRL `PaymentsRelatedToTaxWithholdingForShareBasedCompensation`), each a half-year total
FMP put into Q2. A survey of 15 issuers x 8 quarters found 11 such rows. Every fixture
below is the SHAPE of a real filing from that survey (2026-10-22), with its real figures.

Math tests over plain dicts (`.claude/rules/testing.md` §1). The rule only ever LOWERS a
figure, and only when the filing explains the excess; the wiring into the service is
pinned in tests/test_soc_buyback_wiring.py.
"""
from __future__ import annotations

import math
from datetime import date

import pytest

from app.services._buyback_verification import (
    PREFERRED_TAGS,
    REPURCHASE_TAG,
    SEC_BUYBACK_TAGS,
    WITHHOLDING_TAG,
    SecBuybackFacts,
    buyback_overrides,
    check_quarter_buyback,
    facts_from_concept,
)

M = 1_000_000


def _row(start: str, end: str, val, filed: str = "2026-07-30", form: str = "10-Q") -> dict:
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form,
            "accn": "0000000000-26-000001", "fy": 2026, "fp": "Q2"}


def _concept(*rows: dict) -> dict:
    return {"taxonomy": "us-gaap", "units": {"USD": list(rows)}}


def _facts(repurchase=None, withholding=None, **preferred) -> SecBuybackFacts:
    concepts = {REPURCHASE_TAG: repurchase, WITHHOLDING_TAG: withholding}
    for tag, payload in zip(PREFERRED_TAGS, (preferred.get("redeemable"), preferred.get("preference"))):
        concepts[tag] = payload
    return SecBuybackFacts.from_concepts(concepts)


def _cf(day: str, repurchased) -> dict:
    return {"date": day, "period": "Q2", "commonStockRepurchased": repurchased}


# ── The real shapes ──────────────────────────────────────────────────────────

# INTC: the repurchase concept was last used for FY2023 (0); withholding is tagged only as
# half-year and full-year spans (its Q1 10-Qs never broke the line out).
INTC = _facts(
    repurchase=_concept(_row("2022-12-25", "2023-12-30", 0, "2024-01-26", "10-K")),
    withholding=_concept(
        _row("2023-12-31", "2024-12-28", 631 * M, "2025-01-31", "10-K"),
        _row("2024-12-29", "2025-06-28", 181 * M, "2026-07-24"),
        _row("2024-12-29", "2025-12-27", 423 * M, "2026-01-30", "10-K"),
        _row("2025-12-28", "2026-06-27", 617 * M, "2026-07-24"),
    ),
)

INTC_ROWS = [
    _cf("2026-06-27", -617 * M),
    _cf("2026-03-28", 0),
    _cf("2025-12-27", 0),
    _cf("2025-09-27", 0),
    _cf("2025-06-28", -181 * M),
    _cf("2025-03-29", 0),
]


def test_intc_withholding_is_not_a_buyback():
    overrides, checks = buyback_overrides(INTC_ROWS, INTC)
    assert overrides == {"2026-06-27": 0.0, "2025-06-28": 0.0}
    assert [d for d, _ in checks] == ["2026-06-27", "2025-06-28"]  # only rows with a buyback
    assert all(c.outcome == "corrected" and "tax withholding" in c.detail for _, c in checks)


# AAPL: a real repurchaser — FMP copies the filing, nothing changes.
AAPL = _facts(
    repurchase=_concept(
        _row("2025-09-28", "2025-12-27", 25_105 * M),
        _row("2025-09-28", "2026-03-28", 47_100 * M),
        _row("2025-09-28", "2026-06-27", 62_094 * M, "2026-07-31"),
    ),
    withholding=_concept(
        _row("2025-09-28", "2025-12-27", 3_210 * M),
        _row("2025-09-28", "2026-06-27", 6_462 * M, "2026-07-31"),
    ),
)


def test_a_real_repurchaser_is_untouched():
    rows = [_cf("2026-06-27", -14_994 * M), _cf("2026-03-28", -21_995 * M), _cf("2025-12-27", -25_105 * M)]
    overrides, checks = buyback_overrides(rows, AAPL)
    assert overrides == {}
    assert [c.outcome for _, c in checks] == ["verified"] * 3
    assert [c.dollars for _, c in checks] == [14_994 * M, 21_995 * M, 25_105 * M]


def test_nvda_buyback_plus_withholding_keeps_the_buyback():
    nvda = _facts(
        repurchase=_concept(_row("2026-01-26", "2026-04-26", 19_312 * M)),
        withholding=_concept(_row("2026-01-26", "2026-04-26", 2_129 * M)),
    )
    check = check_quarter_buyback(21_441 * M, date(2026, 4, 26), nvda)
    assert check.outcome == "corrected" and check.dollars == 19_312 * M


def test_amd_derived_quarters_buyback_plus_withholding():
    # Q3'24 is the 9-month YTD minus the 6-month YTD, for both concepts.
    amd = _facts(
        repurchase=_concept(
            _row("2023-12-31", "2024-06-29", 1_000 * M),
            _row("2023-12-31", "2024-09-28", 1_250 * M),
        ),
        withholding=_concept(
            _row("2023-12-31", "2024-06-29", 540 * M),
            _row("2023-12-31", "2024-09-28", 1_000 * M),
        ),
    )
    check = check_quarter_buyback(710 * M, date(2024, 9, 28), amd)
    assert check.outcome == "corrected" and check.dollars == 250 * M


def test_meta_q4_is_the_full_year_minus_nine_months():
    meta = _facts(
        repurchase=_concept(
            _row("2024-01-01", "2024-09-30", 29_750 * M),
            _row("2024-01-01", "2024-12-31", 29_750 * M, "2025-01-30", "10-K"),
        ),
        withholding=_concept(_row("2024-01-01", "2024-12-31", 13_000 * M, "2025-01-30", "10-K")),
    )
    check = check_quarter_buyback(3_857 * M, date(2024, 12, 31), meta)
    assert check.outcome == "corrected" and check.dollars == 0.0


def test_jpm_preferred_redemptions_are_removed():
    jpm = _facts(
        repurchase=_concept(_row("2025-01-01", "2025-03-31", 7_528 * M)),
        withholding=None,
        redeemable=_concept(_row("2025-01-01", "2025-03-31", 3_000 * M)),
    )
    check = check_quarter_buyback(10_528 * M, date(2025, 3, 31), jpm)
    assert check.outcome == "corrected" and check.dollars == 7_528 * M
    assert "preferred" in check.detail


def test_withholding_plus_preferred_together_is_removed():
    sec = _facts(
        repurchase=_concept(_row("2025-01-01", "2025-03-31", 1_000 * M)),
        withholding=_concept(_row("2025-01-01", "2025-03-31", 200 * M)),
        preference=_concept(_row("2025-01-01", "2025-03-31", 300 * M)),
    )
    check = check_quarter_buyback(1_500 * M, date(2025, 3, 31), sec)
    assert check.outcome == "corrected" and check.dollars == 1_000 * M


def test_googl_restatement_gap_is_kept_and_reported():
    # Alphabet restated H1'25 from 28,706M to 28,306M; FMP kept the original Q2.
    googl = _facts(
        repurchase=_concept(
            _row("2025-01-01", "2025-03-31", 15_068 * M, "2025-04-25"),
            _row("2025-01-01", "2025-06-30", 28_706 * M, "2025-07-24"),
            _row("2025-01-01", "2025-06-30", 28_306 * M, "2026-07-24"),
        ),
        withholding=_concept(_row("2025-01-01", "2025-06-30", 6_000 * M)),
    )
    check = check_quarter_buyback(13_638 * M, date(2025, 6, 30), googl)
    # The restated half year was never re-filed beside its Q1, so the two are on different
    # bases and no quarter is derived: kept, unverified (it read "mismatch" before the
    # basis guard — kept either way).
    assert check.outcome == "unverified" and check.dollars == 13_638 * M


def test_the_check_never_raises_a_figure():
    sec = _facts(repurchase=_concept(_row("2025-07-01", "2025-09-30", 11_904 * M)))
    check = check_quarter_buyback(11_504 * M, date(2025, 9, 30), sec)
    assert check.outcome == "sec_higher" and check.dollars == 11_504 * M


def test_a_filing_not_yet_in_sec_data_keeps_fmp():
    # KO's Q2'26 10-Q (filed 07-29) was not yet in companyfacts.
    ko = _facts(repurchase=_concept(_row("2025-01-01", "2025-12-31", 1_000 * M, form="10-K")))
    check = check_quarter_buyback(186 * M, date(2026, 7, 3), ko)
    assert check.outcome == "unverified" and check.dollars == 186 * M


def test_withholding_equal_to_the_figure_is_kept_when_a_buyback_may_hide_in_the_half_year():
    """R is a POSITIVE half-year total with no Q1 to subtract: this quarter's buyback is
    unknown, so a figure that happens to equal the withholding is not zeroed."""
    sec = _facts(
        repurchase=_concept(_row("2025-01-01", "2025-06-30", 900 * M)),
        withholding=_concept(_row("2025-01-01", "2025-06-30", 50 * M)),
    )
    check = check_quarter_buyback(50 * M, date(2025, 6, 30), sec)
    assert check.outcome == "unverified" and check.dollars == 50 * M


def test_a_zero_half_year_repurchase_means_zero_in_each_quarter():
    sec = _facts(
        repurchase=_concept(_row("2025-01-01", "2025-06-30", 0)),
        withholding=_concept(_row("2025-01-01", "2025-06-30", 80 * M)),
    )
    check = check_quarter_buyback(80 * M, date(2025, 6, 30), sec)
    assert check.outcome == "corrected" and check.dollars == 0.0


def test_an_unexplained_figure_with_no_repurchase_tag_is_kept():
    """No concept at all (a company whose filing uses its own tag, or a 404): never read as
    zero — the figure stays."""
    sec = _facts(repurchase=None, withholding=_concept(_row("2025-01-01", "2025-06-30", 80 * M)))
    check = check_quarter_buyback(500 * M, date(2025, 6, 30), sec)
    assert check.outcome == "unverified" and check.dollars == 500 * M
    empty = SecBuybackFacts.from_concepts({})
    assert empty.is_empty
    assert check_quarter_buyback(500 * M, date(2025, 6, 30), empty).dollars == 500 * M


# ── Quarters from year-to-date facts ─────────────────────────────────────────

def test_a_missing_q1_leaves_the_q2_buyback_unknown():
    sec = _facts(repurchase=_concept(_row("2025-01-01", "2025-06-30", 900 * M)))
    check = check_quarter_buyback(400 * M, date(2025, 6, 30), sec)
    assert check.outcome == "unverified"


def test_a_negative_difference_is_a_restatement_not_a_quarter():
    sec = _facts(
        repurchase=_concept(
            _row("2025-01-01", "2025-03-31", 600 * M),
            _row("2025-01-01", "2025-06-30", 500 * M),
        ),
    )
    check = check_quarter_buyback(100 * M, date(2025, 6, 30), sec)
    assert check.outcome == "unverified" and check.dollars == 100 * M


def test_the_latest_filing_of_a_span_wins():
    payload = _concept(
        _row("2025-01-01", "2025-03-31", 100 * M, "2025-04-30"),
        _row("2025-01-01", "2025-03-31", 120 * M, "2026-04-30"),
    )
    facts = facts_from_concept(payload)
    assert len(facts) == 1 and facts[0].val == 120 * M


@pytest.mark.parametrize("sec_end, matches", [
    ("2025-06-28", True),   # same day
    ("2025-06-30", True),   # 2 days: a vendor's month-end normalisation
    ("2025-07-05", True),   # 7 days: the edge
    ("2025-07-06", False),  # 8 days: a different period
])
def test_period_ends_match_within_seven_days(sec_end, matches):
    sec = _facts(
        repurchase=_concept(_row("2025-03-30", sec_end, 0)),
        withholding=_concept(_row("2025-03-30", sec_end, 75 * M)),
    )
    check = check_quarter_buyback(75 * M, date(2025, 6, 28), sec)
    assert (check.outcome == "corrected") is matches


@pytest.mark.parametrize("fmp, withholding, corrected", [
    (75_001_000, 75_000_000, True),     # within 0.1%
    (75_080_000, 75_000_000, False),    # 0.1% of 75M is 75,000: 80,000 is outside
    (2_000, 1_000, True),               # a tiny figure: the $1,000 floor
    (2_001, 1_000, False),
])
def test_tolerance_is_a_thousand_dollars_or_a_tenth_of_a_percent(fmp, withholding, corrected):
    sec = _facts(
        repurchase=_concept(_row("2025-04-01", "2025-06-30", 0)),
        withholding=_concept(_row("2025-04-01", "2025-06-30", withholding)),
    )
    check = check_quarter_buyback(fmp, date(2025, 6, 30), sec)
    # R is 0 for the quarter, so ANY figure is corrected: test the R-unknown branch instead.
    assert check.outcome == "corrected"
    unknown_r = _facts(withholding=_concept(_row("2025-04-01", "2025-06-30", withholding)))
    check = check_quarter_buyback(fmp, date(2025, 6, 30), unknown_r)
    assert (check.outcome == "corrected") is corrected


# ── Malformed input ──────────────────────────────────────────────────────────

def test_malformed_facts_are_dropped_not_read_as_zero():
    payload = _concept(
        _row("2025-01-01", "2025-03-31", None),
        _row("2025-01-01", "2025-03-31", True),
        _row("2025-01-01", "2025-03-31", float("nan")),
        _row("2025-01-01", "2025-03-31", -5 * M),
        _row("2025-01-01", "2025-03-31", "12"),
        {"end": "2025-03-31", "val": 5 * M, "form": "10-Q", "filed": "x"},       # an instant
        _row("2025-03-31", "2025-01-01", 5 * M),                                 # end before start
        _row("2025-01-01", "2025-03-31", 5 * M, form="8-K"),
        _row("bad", "2025-03-31", 5 * M),
        "not a row",
    )
    assert facts_from_concept(payload) == []


@pytest.mark.parametrize("payload", [None, {}, {"units": None}, {"units": {"EUR": []}}, "x", 3])
def test_a_missing_concept_is_none(payload):
    assert facts_from_concept(payload) is None


@pytest.mark.parametrize("value", [0, 0.0, 5 * M, None, True, float("nan"), "−1", float("-inf")])
def test_rows_without_a_buyback_are_not_checked(value):
    overrides, checks = buyback_overrides([_cf("2025-06-28", value)], INTC)
    assert overrides == {} and checks == []


def test_rows_with_a_bad_date_are_not_checked():
    overrides, checks = buyback_overrides([{"date": "n/a", "commonStockRepurchased": -5 * M}, "x"], INTC)
    assert overrides == {} and checks == []


def test_never_above_fmp_and_never_negative_across_every_fixture():
    fixtures = [INTC, AAPL, SecBuybackFacts.from_concepts({}), _facts(repurchase=_concept())]
    for sec in fixtures:
        for day in ("2025-06-28", "2026-06-27", "2025-12-27", "2026-03-28"):
            for b in (1_000, 181 * M, 617 * M, 25_105 * M, 1e15):
                check = check_quarter_buyback(b, date.fromisoformat(day), sec)
                assert 0.0 <= check.dollars <= b and math.isfinite(check.dollars)


def test_every_tag_the_rule_reads_is_fetched():
    assert set(SEC_BUYBACK_TAGS) == {REPURCHASE_TAG, WITHHOLDING_TAG, *PREFERRED_TAGS}
    assert len(SEC_BUYBACK_TAGS) == len(set(SEC_BUYBACK_TAGS))


# ── Review round (2026-10-22): the live 130-ticker survey ───────────────────

def test_tgt_a_restated_year_minus_an_old_basis_nine_months_is_not_a_quarter():
    """TGT FY2021: the 2024 10-K re-filed the year WITHOUT withholding (7,356 → 7,188) while
    its nine months (5,042) were only ever filed WITH it. 7,188 − 5,042 = 2,146 lowered a
    real 2,314 buyback by exactly the year's 168 withholding. Real facts."""
    tgt = _facts(
        repurchase=_concept(
            _row("2021-01-31", "2021-10-30", 5_042 * M, "2021-11-24"),
            _row("2021-01-31", "2021-10-30", 5_042 * M, "2022-11-23"),
            _row("2021-01-31", "2022-01-29", 7_356 * M, "2022-03-09", "10-K"),
            _row("2021-01-31", "2022-01-29", 7_356 * M, "2023-03-08", "10-K"),
            _row("2021-01-31", "2022-01-29", 7_188 * M, "2024-03-13", "10-K"),
        ),
        withholding=_concept(_row("2021-01-31", "2022-01-29", 168 * M, "2024-03-13", "10-K")),
    )
    check = check_quarter_buyback(2_314 * M, date(2022, 1, 29), tgt)
    assert check.outcome != "corrected" and check.dollars == 2_314 * M


def test_a_restatement_both_spans_were_refiled_on_still_corrects():
    """DELL's shape: the later filing re-presents the nine months AND the year on the new
    basis, so their difference is a real quarter and the withholding comes out."""
    dell = _facts(
        repurchase=_concept(
            _row("2022-01-29", "2022-10-28", 2_718 * M, "2022-12-06"),
            _row("2022-01-29", "2022-10-28", 2_368 * M, "2023-12-05"),
            _row("2022-01-29", "2023-02-03", 2_933 * M, "2023-03-30", "10-K"),
            _row("2022-01-29", "2023-02-03", 2_533 * M, "2024-03-25", "10-K"),
        ),
        withholding=_concept(_row("2022-11-01", "2023-02-03", 18 * M, "2024-03-25", "10-K")),
    )
    check = check_quarter_buyback(183 * M, date(2023, 2, 3), dell)
    assert check.outcome == "corrected" and check.dollars == 165 * M


def test_pnc_preferred_redemptions_under_the_noncontrolling_interest_tag():
    pnc = _facts(
        repurchase=_concept(
            _row("2024-01-01", "2024-09-30", 466 * M),
            _row("2024-01-01", "2024-12-31", 687 * M, "2025-02-28", "10-K"),
        ),
        preference=None,
    )
    pnc.preferred["PaymentsForRepurchaseOfRedeemableNoncontrollingInterest"] = facts_from_concept(
        _concept(_row("2024-01-01", "2024-12-31", 500 * M, "2025-02-28", "10-K")))
    check = check_quarter_buyback(721 * M, date(2024, 12, 31), pnc)
    assert check.outcome == "corrected" and check.dollars == 221 * M
    assert "noncontrolling" in check.detail
    off = check_quarter_buyback(731 * M, date(2024, 12, 31), pnc)
    assert off.outcome == "mismatch" and off.dollars == 731 * M
    assert "PaymentsForRepurchaseOfRedeemableNoncontrollingInterest" in SEC_BUYBACK_TAGS


def test_a_duplicate_day_keeps_the_first_row_like_the_chart():
    """`_build_quarters` charts the FIRST row of a day (a restatement is listed first); a
    correction computed for the discarded duplicate must not lower it."""
    rows = [_cf("2025-06-28", -500 * M), _cf("2025-06-28", -181 * M), _cf("2025-06-28 00:00:00", -181 * M)]
    overrides, checks = buyback_overrides(rows, INTC)
    assert overrides == {}
    assert [(d, c.dollars) for d, c in checks] == [("2025-06-28", 500 * M)]


def test_override_keys_are_the_day_the_chart_looks_rows_up_by():
    overrides, _ = buyback_overrides([_cf("2025-06-28T00:00:00", -181 * M)], INTC)
    assert overrides == {"2025-06-28": 0.0}


def test_a_zero_half_year_repurchase_zeroes_a_figure_no_explainer_matches():
    sec = _facts(
        repurchase=_concept(_row("2025-01-01", "2025-06-30", 0)),
        withholding=_concept(_row("2025-01-01", "2025-06-30", 80 * M)),
    )
    check = check_quarter_buyback(500 * M, date(2025, 6, 30), sec)
    assert check.outcome == "corrected" and check.dollars == 0.0
    assert "no repurchase this quarter" in check.detail
    no_w = _facts(repurchase=_concept(_row("2025-01-01", "2025-06-30", 0)))
    assert check_quarter_buyback(500 * M, date(2025, 6, 30), no_w).outcome == "corrected"


@pytest.mark.parametrize("withholding, outcome, dollars", [
    (2_129 * M, "corrected", 19_312 * M),   # NVDA Q1 FY27, exact
    (2_134 * M, "mismatch", 21_441 * M),    # 5M off: inside 0.1% of B, outside 0.1% of the excess
])
def test_the_excess_is_matched_to_the_explainer_not_to_b(withholding, outcome, dollars):
    sec = _facts(
        repurchase=_concept(_row("2026-01-26", "2026-04-26", 19_312 * M)),
        withholding=_concept(_row("2026-01-26", "2026-04-26", withholding)),
    )
    check = check_quarter_buyback(21_441 * M, date(2026, 4, 26), sec)
    assert (check.outcome, check.dollars) == (outcome, dollars)


def test_a_sixteen_week_quarter_is_a_quarter():
    """KR's 16-week Q1 and COST / AZO's 16-week Q4 (112 days) were never checked."""
    direct = _facts(
        repurchase=_concept(_row("2025-02-02", "2025-05-25", 0)),
        withholding=_concept(_row("2025-02-02", "2025-05-25", 40 * M)),
    )
    assert check_quarter_buyback(40 * M, date(2025, 5, 25), direct).outcome == "corrected"
    q4 = _facts(
        repurchase=_concept(
            _row("2024-09-02", "2025-05-11", 600 * M),
            _row("2024-09-02", "2025-08-31", 900 * M, form="10-K"),
        ),
        withholding=_concept(_row("2025-05-12", "2025-08-31", 25 * M, form="10-K")),
    )
    check = check_quarter_buyback(325 * M, date(2025, 8, 31), q4)
    assert check.outcome == "corrected" and check.dollars == 300 * M


def test_newest_end_spans_every_concept():
    assert INTC.newest_end == date(2026, 6, 27)
    assert SecBuybackFacts.from_concepts({}).newest_end is None
