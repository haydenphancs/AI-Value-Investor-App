"""A card rated 0 is NOT rated, and every surface says so (2026-10-07).

A bank's / insurer's / lender's Financial Health card keeps one comparable row once its
liquidity and coverage rows are omitted, and is rated 0 (`health_snapshot_service`
`_MIN_SCORED_FOR_RATING`). Before: the verdict fallback called it "In Line With Industry",
the paid report's model context read "Financial Health (0/5)", and the iOS report card
painted the 0-star footer red. Hermetic.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.agents.card_verdict import generate_card_verdict
from app.services.agents.ticker_report_data_collector import _format_snapshot_card_values

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios/Views/Molecules/ReportDeepDiveMetricCard.swift"


@pytest.mark.parametrize("peer", ["industry", "sector", None])
def test_an_unrated_card_with_no_scores_gets_no_verdict(peer):
    assert generate_card_verdict("Health", 0, peer, [("debt_to_equity", None)]) == ("Not Rated", "neutral")
    assert generate_card_verdict("Health", 0, peer, []) == ("Not Rated", "neutral")


def test_a_rated_card_keeps_its_fallback_wording():
    label, sentiment = generate_card_verdict("Health", 3, "industry", [])
    assert label.startswith("In Line With") and sentiment == "neutral"
    assert generate_card_verdict("Health", 2, "industry", [])[1] == "negative"


def _snap(rating):
    return SimpleNamespace(rating=rating, metrics=[SimpleNamespace(name="Debt to Equity", value="1.2")])


def test_the_model_context_says_unrated_not_zero_of_five():
    out = SimpleNamespace(snap_profitability=_snap(4), snap_growth=None,
                          snap_valuation=None, snap_health=_snap(0))
    text = _format_snapshot_card_values(out)
    assert "Financial Health (unrated):" in text
    assert "(0/5)" not in text
    assert "Profitability (4/5):" in text


def _rating_color_block() -> str:
    src = "\n".join(l for l in _IOS.read_text().splitlines() if not l.strip().startswith("//"))
    start = src.index("private var ratingColor: Color {")
    return src[start:src.index("\n    }\n", start)]


def test_the_ios_report_card_never_paints_an_unrated_card_red():
    block = _rating_color_block()
    zero = re.search(r"case \.\.\.0: return AppColors\.(\w+)", block)
    assert zero and zero.group(1) == "textSecondary", block
    # the 0 case must come BEFORE the default (bearish) arm
    assert block.index("case ...0:") < block.index("default:")


# ── Round 3 (2026-10-08): the REAL unrated shape — one SCORED row at rating 0 ──────────
#
# The cases above feed `[("debt_to_equity", None)]`, a shape production never builds: a
# bank's / insurer's / lender's Health card keeps its D/E row WITH its peer score (4 / 3 /
# 2) and is rated 0 because one scored row is too few. That shape reached the per-metric
# composition and came back "Light Debt Load" (green), "Heavy Debt Load" (red) or "In
# Line With Industry" beside 0 stars (RPT3-1 / R3-CARDS-2 / R3-CONTRACT-1 / XC-4).

from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse  # noqa: E402
from app.services.agents.narrative_prompts import build_module_digest  # noqa: E402
from app.services.agents.ticker_report_data_collector import _snapshot_to_card  # noqa: E402


@pytest.mark.parametrize("peer", ["industry", "sector", None])
@pytest.mark.parametrize("de_score", [1, 2, 3, 4, 5])
def test_an_unrated_card_with_a_scored_row_gets_no_verdict(peer, de_score):
    for title in ("Health", "Financial Health"):
        for rows in ([("debt_to_equity", de_score)],
                     [("debt_to_equity", de_score), ("altman_z", None)]):
            assert generate_card_verdict(title, 0, peer, rows) == ("Not Rated", "neutral"), rows


@pytest.mark.parametrize("bad_rating", [-1, None])
def test_a_negative_or_missing_rating_is_not_rated_either(bad_rating):
    assert generate_card_verdict("Health", bad_rating, "industry",
                                 [("debt_to_equity", 4)]) == ("Not Rated", "neutral")


def test_a_rated_card_still_composes_from_its_rows():
    """The early return is for rating 0 only: one star or more keeps the drivers."""
    assert generate_card_verdict("Health", 1, "industry", [("debt_to_equity", 4)]) == (
        "Light Debt Load", "positive")
    assert generate_card_verdict("Health", 3, "industry", [("debt_to_equity", 3)]) == (
        "In Line With Industry", "neutral")


def _bank_snapshot(de_score: int) -> SnapshotItemResponse:
    """What `health_snapshot_service` builds for C / JPM (pinned by
    test_cards_round2_2026_10_07::test_a_banks_card_is_not_rated_on_debt_to_equity_alone)."""
    return SnapshotItemResponse(
        category="Financial Health", rating=0, weighted_score=None,
        metrics=[SnapshotMetricResponse(
            name="Debt-to-Equity (vs sector 1.50)", value="1.20",
            metric_key="debt_to_equity", score=de_score, peer_level="industry")],
    )


@pytest.mark.parametrize("de_score", [2, 3, 4])
def test_the_report_card_of_a_bank_says_not_rated(de_score):
    card = _snapshot_to_card("Health", _bank_snapshot(de_score), peer_group_level="industry")
    assert card["star_rating"] == 0
    assert (card["quality_label"], card["quality_sentiment"]) == ("Not Rated", "neutral")


@pytest.mark.asyncio
@pytest.mark.parametrize("de_status", ["positive", "neutral", "negative"])
async def test_the_real_bank_build_reaches_the_report_as_not_rated(monkeypatch, de_status):
    """End to end through the producer: Health Check rows → health snapshot → report card."""
    import test_cards_round2_2026_10_07 as r2
    import test_snapshot_cards_2026_10_07_profitability_health as sph

    _hs, svc = sph._health_service(monkeypatch, r2._bank_health(de_status), profile=r2._BANK)
    snap, _degraded = await svc._compute_with_status("C")
    assert snap.rating == 0 and [m.metric_key for m in snap.metrics] == ["debt_to_equity"]
    assert snap.metrics[0].score is not None, "the harness no longer builds the scored shape"
    card = _snapshot_to_card("Health", snap, peer_group_level="industry")
    assert (card["quality_label"], card["quality_sentiment"]) == ("Not Rated", "neutral")


# RPT3-2: the thesis / critical-factors digest is the OTHER model-context renderer.

def _report_with_cards(*cards):
    return {"fundamental_metrics": [
        {"title": t, "star_rating": s, "metrics": [{"label": lbl, "value": v}]}
        for t, s, lbl, v in cards
    ]}


def test_the_thesis_digest_says_unrated_not_zero_of_five():
    digest = build_module_digest(_report_with_cards(
        ("Profitability", 4, "ROE", "12%"),
        ("Health", 0, "Debt-to-Equity", "1.20"),
    ))
    assert "Health unrated [Debt-to-Equity 1.20]" in digest
    assert "0/5" not in digest
    assert "Profitability 4/5 [ROE 12%]" in digest


@pytest.mark.parametrize("stars,expected", [(-1, "Health unrated ["), (True, "Health ["),
                                            (None, "Health ["), ("3", "Health [")])
def test_the_digest_never_prints_a_malformed_rating_as_a_score(stars, expected):
    digest = build_module_digest(_report_with_cards(("Health", stars, "D/E", "1.2")))
    assert expected in digest and "/5" not in digest
