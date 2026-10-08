"""NET-1 (2026-10-08): a Financial Health card none of whose rows printed a peer median
names no peer group in its verdict.

The shape: a listed payment network (V, MA) in "Financial - Credit Services". Altman Z
does not apply in Financial Services, and every kept row — D/E, current, quick, interest
coverage — is judged on absolute bands (`financials_metric_gate.peer_median_comparable`),
so no row carries a `peer_level`. The ticker-wide `peer_group_level` still says
"industry", and the verdict used to read "In Line With Industry" / "Beats Industry
Average": a comparison nobody made. Every other card, and a Health card with at least one
compared row, keeps its peer wording.
"""

import pytest

from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
from app.services.agents.card_verdict import _VOCAB, _WEIGHTS, generate_card_verdict
from app.services.agents.narrative_prompts import _classify_label_sentiment
from app.services.agents.ticker_report_data_collector import _snapshot_to_card

_HEALTH_ROWS = ("debt_to_equity", "current_ratio", "quick_ratio", "interest_coverage")


def _health_snap(scores, *, levels=None, rating=3):
    levels = levels or {}
    return SnapshotItemResponse(
        category="Financial Health",
        rating=rating,
        metrics=[
            SnapshotMetricResponse(
                name=key, value="1.00", metric_key=key, score=score,
                peer_level=levels.get(key),
            )
            for key, score in scores.items()
        ],
    )


# ── generate_card_verdict ────────────────────────────────────────────────────────────


def test_peer_free_neutral_names_no_peer():
    label, sent = generate_card_verdict(
        "Health", 3, "industry", [(k, 3) for k in _HEALTH_ROWS], peer_compared=False,
    )
    assert label == "Neither Strong Nor Weak"
    assert sent == "neutral"
    assert "Industry" not in label and "Sector" not in label


def test_default_still_compares_with_the_peer_group():
    label, _ = generate_card_verdict("Health", 3, "industry", [(k, 3) for k in _HEALTH_ROWS])
    assert label == "In Line With Industry"


@pytest.mark.parametrize(
    "scores, expected, sentiment",
    [
        ({"debt_to_equity": 5, "current_ratio": 3}, "Light Debt Load", "positive"),
        ({"quick_ratio": 1, "current_ratio": 3}, "Weak Quick Liquidity", "negative"),
        (
            {"interest_coverage": 5, "debt_to_equity": 1},
            "Easily Covers Interest, Heavy Debt Load",
            "neutral",
        ),
    ],
)
def test_peer_free_strength_and_drag_phrases_are_unchanged(scores, expected, sentiment):
    label, sent = generate_card_verdict(
        "Health", 3, "industry", list(scores.items()), peer_compared=False,
    )
    assert (label, sent) == (expected, sentiment)


@pytest.mark.parametrize(
    "rating, expected",
    [
        (5, ("Strong on Absolute Measures", "positive")),
        (4, ("Strong on Absolute Measures", "positive")),
        (3, ("Neither Strong Nor Weak", "neutral")),
        (2, ("Weak on Absolute Measures", "negative")),
        (1, ("Weak on Absolute Measures", "negative")),
        (0, ("Not Rated", "neutral")),
    ],
)
def test_peer_free_rating_fallback(rating, expected):
    # No usable per-metric score: the label comes from the rating alone.
    assert generate_card_verdict(
        "Health", rating, "industry", [("debt_to_equity", None)], peer_compared=False,
    ) == expected


@pytest.mark.parametrize("rating", [4, 3, 2])
def test_peer_aware_rating_fallback_is_unchanged(rating):
    label, _ = generate_card_verdict("Health", rating, "sector", [("altman_z", None)])
    assert "Sector" in label


def test_no_health_phrase_names_a_peer():
    # peer_compared=False changes only the neutral / fallback labels; that is enough only
    # while no Health strength or drag phrase carries "{peer}".
    for key in _WEIGHTS["Health"]:
        assert all("{peer}" not in phrase for phrase in _VOCAB[key]), key


@pytest.mark.parametrize(
    "label", ["Neither Strong Nor Weak", "Strong on Absolute Measures", "Weak on Absolute Measures"],
)
def test_peer_free_labels_classify_like_the_sentiment_they_carry(label):
    rating = {"Neither": 3, "Strong": 5, "Weak": 1}[label.split()[0]]
    _, sent = generate_card_verdict("Health", rating, "industry", [], peer_compared=False)
    assert _classify_label_sentiment(label) == sent


# ── _snapshot_to_card (the report's Health card) ─────────────────────────────────────


def test_network_shaped_health_card_says_nothing_about_peers():
    # V-shaped: four absolute rows, no Altman Z, no row printed a median.
    card = _snapshot_to_card(
        "Health", _health_snap({k: 3 for k in _HEALTH_ROWS}), peer_group_level="industry",
    )
    assert card["quality_label"] == "Neither Strong Nor Weak"
    assert card["quality_sentiment"] == "neutral"
    # The card keeps the ticker-wide level for the drill-down legend fallback.
    assert card["peer_group_level"] == "industry"


def test_health_card_with_one_compared_row_keeps_the_peer_wording():
    snap = _health_snap(
        {k: 3 for k in _HEALTH_ROWS}, levels={"debt_to_equity": "industry"},
    )
    card = _snapshot_to_card("Health", snap, peer_group_level="industry")
    assert card["quality_label"] == "In Line With Industry"


def test_sector_level_row_counts_as_compared():
    snap = _health_snap({k: 3 for k in _HEALTH_ROWS}, levels={"current_ratio": "sector"})
    card = _snapshot_to_card("Health", snap, peer_group_level="sector")
    assert card["quality_label"] == "In Line With Sector"


def test_unknown_peer_level_value_is_not_a_comparison():
    snap = _health_snap({k: 3 for k in _HEALTH_ROWS}, levels={"current_ratio": "global"})
    card = _snapshot_to_card("Health", snap, peer_group_level="industry")
    assert card["quality_label"] == "Neither Strong Nor Weak"


def test_unrated_health_card_stays_not_rated():
    card = _snapshot_to_card(
        "Health", _health_snap({"debt_to_equity": 3}, rating=0), peer_group_level="industry",
    )
    assert card["quality_label"] == "Not Rated"


def test_other_cards_are_out_of_scope():
    # A Profitability card's rows carry no peer_level in this fixture; its wording is
    # unchanged (NET-1 is the Health card only).
    snap = SnapshotItemResponse(
        category="Profitability", rating=3,
        metrics=[
            SnapshotMetricResponse(name=k, value="10%", metric_key=k, score=3)
            for k in ("gross_margin", "net_margin", "roe")
        ],
    )
    card = _snapshot_to_card("Profitability", snap, peer_group_level="industry")
    assert card["quality_label"] == "In Line With Industry"


def test_missing_health_snapshot_is_unchanged():
    card = _snapshot_to_card("Health", None, peer_group_level="industry")
    assert card["quality_label"] == "Data unavailable"


# ── A listed member VETOED as a lender (2026-10-09 review) ───────────────────────────
# The surfaces that read no quarters (profitability, valuation) keep the curated list's
# answer and print no median; those that read the quarters treat it as a lender. Its report
# verdicts therefore read each card's own rows.


def _prof_snap(levels=None):
    levels = levels or {}
    return SnapshotItemResponse(
        category="Profitability", rating=3,
        metrics=[
            SnapshotMetricResponse(name=k, value="10%", metric_key=k, score=3,
                                   peer_level=levels.get(k))
            for k in ("gross_margin", "net_margin", "roe")
        ],
    )


def test_a_vetoed_listed_members_uncompared_card_names_no_peer():
    card = _snapshot_to_card(
        "Profitability", _prof_snap(), peer_group_level="industry",
        non_lender_member=False, listed_non_lender=True,
    )
    assert card["quality_label"] == "Neither Strong Nor Weak"


def test_a_vetoed_listed_members_compared_card_keeps_the_peer_wording():
    card = _snapshot_to_card(
        "Profitability", _prof_snap({"roe": "industry"}), peer_group_level="industry",
        non_lender_member=False, listed_non_lender=True,
    )
    assert card["quality_label"] == "In Line With Industry"


def test_a_member_verdict_wins_over_the_listing_flag():
    card = _snapshot_to_card(
        "Profitability", _prof_snap({"roe": "industry"}), peer_group_level="industry",
        non_lender_member=True, listed_non_lender=True,
    )
    assert card["quality_label"] == "Neither Strong Nor Weak"
