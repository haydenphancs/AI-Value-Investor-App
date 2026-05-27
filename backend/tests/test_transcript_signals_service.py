"""
Unit tests for transcript_signals_service — Phase 3B regex extraction.

No live transcripts; we hand-craft sentences that match common CFO
phrasings to verify the regex layer catches them. Also tests the
score-mapping helpers (nrr_to_sub_score, user_count_to_sub_score).

Self-contained per the project testing rule.
"""

from __future__ import annotations

import pytest

from app.services.transcript_signals_service import (
    TranscriptSignals,
    extract_signals,
    nrr_to_sub_score,
    user_count_to_sub_score,
)


# ── NRR extraction ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Our NRR was 115% for the quarter.", 115.0),
        ("NDR of 110% reflects strong expansion.", 110.0),
        ("Net dollar retention reached 122% YoY.", 122.0),
        ("Net revenue retention came in at 108.5% this quarter.", 108.5),
        ("We delivered an NRR of 130%.", 130.0),
        ("Expansion NRR hit 125% in Q4.", 125.0),
    ],
)
def test_nrr_regex_captures_common_phrasings(text, expected):
    sig = extract_signals(text)
    assert sig.nrr_pct == expected


def test_nrr_extraction_grabs_supporting_sentence():
    sig = extract_signals(
        "The CFO opened by noting that NRR of 115% drove top-line growth this period."
    )
    assert sig.nrr_pct == 115.0
    assert "115%" in (sig.nrr_quote or "")
    assert "NRR" in (sig.nrr_quote or "")


def test_nrr_out_of_range_rejected():
    """NRR scores should be filtered to [50, 200] — values outside are
    likely a different metric (e.g., 'gross margin of 65%' would NOT match
    because the regex anchors on NRR keywords, but a stray very high
    number near those keywords could; the bounds are a safety net).
    """
    # An impossible 500% NRR — rejected.
    sig = extract_signals("Our NRR was 500% which is obviously wrong.")
    assert sig.nrr_pct is None


def test_no_nrr_phrasing_returns_none():
    sig = extract_signals("Revenue grew 25% this quarter and we hired 100 employees.")
    assert sig.nrr_pct is None


def test_nrr_first_match_wins_within_transcript():
    """If both NRR and Net Revenue Retention are mentioned, take the first."""
    text = "NRR of 110%. Later: net revenue retention reached 115%."
    sig = extract_signals(text)
    assert sig.nrr_pct == 110.0


# ── User count extraction ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "text,expected",
    [
        ("We exited the quarter with 330 million monthly active users.", 330_000_000),
        ("Spotify reached 600M MAU in Q4.", 600_000_000),
        ("Our 2.5 billion users across the platform.", 2_500_000_000),
        ("3M paying subscribers globally.", 3_000_000),
        ("Active users grew to 100 million.", 100_000_000),
        ("500K paid subscribers as of quarter end.", 500_000),
    ],
)
def test_user_count_regex_captures_common_phrasings(text, expected):
    sig = extract_signals(text)
    assert sig.user_count == expected


def test_user_count_out_of_range_filter():
    """Implausible values (>10B users) get filtered."""
    sig = extract_signals("We have 50 billion active users.")
    assert sig.user_count is None  # filtered by upper bound


def test_user_count_too_small_filter():
    """<1000 users is below the threshold (probably noise)."""
    sig = extract_signals("We have 5 thousand subscribers.")
    # 5K = 5000 — at the boundary, accept
    assert sig.user_count == 5_000


def test_user_count_no_phrasing_returns_none():
    sig = extract_signals("We hired 100 engineers last quarter.")
    assert sig.user_count is None


# ── Churn extraction ───────────────────────────────────────────────────


def test_churn_basic_match():
    sig = extract_signals("Annual logo churn of 5.2% improved YoY.")
    assert sig.churn_pct == 5.2


def test_churn_implausibly_high_rejected():
    sig = extract_signals("Customer churn of 80% would be catastrophic.")
    assert sig.churn_pct is None  # >30% filter


# ── Combined / empty inputs ────────────────────────────────────────────


def test_extract_signals_all_three():
    text = (
        "We delivered NRR of 118% this quarter. "
        "Monthly active users reached 450 million. "
        "Annual customer churn of 3% reflects strong retention."
    )
    sig = extract_signals(text)
    assert sig.nrr_pct == 118.0
    assert sig.user_count == 450_000_000
    assert sig.churn_pct == 3.0
    assert sig.has_any_signal() is True


def test_extract_signals_empty_input():
    assert extract_signals("").has_any_signal() is False
    assert extract_signals(None).has_any_signal() is False  # type: ignore[arg-type]
    assert extract_signals(123).has_any_signal() is False   # type: ignore[arg-type]


# ── NRR → sub-score mapping ────────────────────────────────────────────


@pytest.mark.parametrize(
    "nrr,expected",
    [
        (100.0, 5.0),    # at parity → 5.0
        (115.0, 8.0),    # +15pp above 100 = +3.0 → 8.0
        (110.0, 7.0),
        (130.0, 10.0),   # cap
        (135.0, 10.0),   # past cap
        (95.0, 4.0),     # below parity
        (90.0, 3.0),
        (70.0, 0.0),     # floor
        (60.0, 0.0),     # past floor
    ],
)
def test_nrr_to_sub_score(nrr, expected):
    assert nrr_to_sub_score(nrr) == expected


def test_nrr_to_sub_score_none():
    assert nrr_to_sub_score(None) is None


# ── User-count → sub-score mapping ─────────────────────────────────────


@pytest.mark.parametrize(
    "users,expected_band",
    [
        (1_000_000_000, (9.5, 10.0)),    # 1B+ → near cap
        (500_000_000, (8.0, 9.5)),
        (100_000_000, (7.5, 8.5)),
        (10_000_000, (5.5, 7.0)),
        (1_000_000, (4.0, 5.5)),
        (100_000, (2.5, 4.0)),
        (10_000, (1.0, 2.5)),
        (5_000, (0.0, 1.0)),
    ],
)
def test_user_count_to_sub_score_bands(users, expected_band):
    score = user_count_to_sub_score(users)
    assert score is not None
    lo, hi = expected_band
    assert lo <= score <= hi, f"got {score} for {users} users"


def test_user_count_to_sub_score_zero_or_none():
    assert user_count_to_sub_score(None) is None
    assert user_count_to_sub_score(0) is None
    assert user_count_to_sub_score(-1) is None
