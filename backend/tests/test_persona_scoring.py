"""
Tests for persona_scoring.compute_quality_score.

Lock the contract that
  - per-persona weights live in PERSONA_WEIGHTS and sum to 1.0,
  - the same `ticker_report_data` produces persona-specific scores
    (Buffett caring about moat ≠ Wood caring about moat),
  - missing vitals contribute 0 (penalize companies that can't be
    measured on dimensions a persona cares about),
  - unknown persona keys fall back to equal weights and never crash.
"""

from __future__ import annotations

import math

import pytest

from app.services.agents.persona_scoring import (
    PERSONA_WEIGHTS,
    compute_quality_score,
)


# ── Fixtures: shape mirrors the internal _scoring_inputs layer ───────


def _vitals(
    *,
    valuation_status: str = "fair_value",
    moat_rating: str = "narrow",
    health_level: str = "healthy",
    revenue_score: float = 5.0,
    insider_score: float = 5.0,
    macro_score: float = 5.0,
    forecast_score: float = 5.0,
    wallstreet_score: float = 5.0,
) -> dict:
    return {
        "_scoring_inputs": {
            "valuation":        {"status": valuation_status},
            "moat":             {"overall_rating": moat_rating},
            "financial_health": {"level": health_level},
            "revenue":     {"score": {"value": revenue_score}},
            "insider":     {"score": {"value": insider_score}},
            "macro":       {"score": {"value": macro_score}},
            "forecast":    {"score": {"value": forecast_score}},
            "wall_street": {"score": {"value": wallstreet_score}},
        }
    }


# ── Weight sanity ───────────────────────────────────────────────────


@pytest.mark.parametrize("persona_key", list(PERSONA_WEIGHTS.keys()))
def test_persona_weights_sum_to_one(persona_key):
    total = sum(PERSONA_WEIGHTS[persona_key].values())
    assert math.isclose(total, 1.0, abs_tol=1e-9), (
        f"{persona_key} weights sum to {total}, expected 1.0"
    )


def test_buffett_weights_moat_at_30_percent():
    # Locks the brief — Buffett's #1 priority is moat. If this drifts,
    # the scoring is no longer faithful to the persona prompt.
    assert PERSONA_WEIGHTS["warren_buffett"]["moat"] == 0.30


def test_wood_weights_growth_axes_above_value_axes():
    # Wood's revenue + forecast axes (growth) must outweigh
    # her valuation + financial_health axes (traditional value).
    w = PERSONA_WEIGHTS["cathie_wood"]
    growth = w["revenue"] + w["forecast"]
    value = w["valuation"] + w["financial_health"]
    assert growth > value


# ── Persona discrimination ───────────────────────────────────────────


def test_wood_overall_score_higher_than_buffett_for_high_growth_low_moat_company():
    # ARKK-shaped target: weak moat ("none"), pricey ("overpriced"),
    # but blistering revenue + forecast. Wood should reward this
    # company; Buffett should not.
    data = _vitals(
        valuation_status="overpriced",
        moat_rating="none",
        health_level="healthy",
        revenue_score=9.5,
        forecast_score=9.5,
        insider_score=6.0,
        macro_score=5.0,
        wallstreet_score=7.0,
    )

    buffett_score = compute_quality_score("warren_buffett", data)
    wood_score = compute_quality_score("cathie_wood", data)

    assert wood_score > buffett_score, (
        f"Wood {wood_score} should exceed Buffett {buffett_score} "
        f"for a high-growth low-moat low-value company"
    )


def test_buffett_overall_score_higher_than_wood_for_wide_moat_value_company():
    # KO-shaped target: wide moat, undervalued, strong financials,
    # boring revenue growth. Buffett's setup; Wood doesn't care.
    data = _vitals(
        valuation_status="underpriced",
        moat_rating="wide",
        health_level="strong",
        revenue_score=4.0,
        forecast_score=3.5,
        insider_score=8.0,
        macro_score=5.0,
        wallstreet_score=5.0,
    )

    buffett_score = compute_quality_score("warren_buffett", data)
    wood_score = compute_quality_score("cathie_wood", data)

    assert buffett_score > wood_score


# ── Edge cases ───────────────────────────────────────────────────────


def test_missing_vitals_score_as_zero():
    # If the agent drops a vital this persona cares about, that
    # vital's contribution should be 0 — not silently averaged in
    # at neutral 5/10. The user should see a lower score reflecting
    # incomplete data, not a fake-confident neutral.
    incomplete = {"_scoring_inputs": {
        "moat": {"overall_rating": "wide"},
        # everything else missing
    }}

    score = compute_quality_score("warren_buffett", incomplete)

    # Buffett moat weight is 0.30, moat="wide" → 8.5/10. Other
    # vitals contribute 0. Expected: 0.30 * 8.5 * 10 = 25.5
    assert score == 25.5


def test_unknown_persona_falls_back_to_equal_weights():
    # Bad persona key should never crash and should never silently
    # return a Buffett-default (which would be very wrong for a
    # non-Buffett caller). Equal weights across all 8 vitals is the
    # safe fallback.
    data = _vitals(
        valuation_status="fair_value",
        moat_rating="narrow",
        health_level="healthy",
        revenue_score=5.0,
        insider_score=5.0,
        macro_score=5.0,
        forecast_score=5.0,
        wallstreet_score=5.0,
    )

    # For uniformly-mid vitals (fair_value=5.5, narrow=6.0,
    # healthy=7.0, all numeric=5.0), equal-weight average should
    # land around 55. Just check it's in a plausible band.
    score = compute_quality_score("nonexistent_persona", data)
    assert 40.0 <= score <= 70.0


def test_score_is_clipped_to_zero_to_hundred():
    # Even if the AI emits an out-of-band score (e.g. 12/10),
    # the final must stay in [0, 100] so the iOS UI ring renders.
    data = {"_scoring_inputs": {
        "revenue":     {"score": {"value": 99.0}},
        "insider":     {"score": {"value": 99.0}},
        "macro":       {"score": {"value": 99.0}},
        "forecast":    {"score": {"value": 99.0}},
        "wall_street": {"score": {"value": 99.0}},
        "valuation":        {"status": "deep_undervalued"},
        "moat":             {"overall_rating": "wide"},
        "financial_health": {"level": "strong"},
    }}

    for persona_key in PERSONA_WEIGHTS:
        score = compute_quality_score(persona_key, data)
        assert 0.0 <= score <= 100.0


def test_empty_data_returns_zero():
    # No vitals at all → score 0. Better than crashing or returning a
    # confident-looking neutral.
    assert compute_quality_score("warren_buffett", {}) == 0.0
    assert compute_quality_score("warren_buffett", {"_scoring_inputs": {}}) == 0.0


def test_legacy_key_vitals_alias_still_scores():
    # User-history reports cached before the rename carry "key_vitals"; the
    # scorer's back-compat fallback must read them identically to the new
    # "_scoring_inputs" key (those rows are never invalidated — see
    # patch_legacy_price_action).
    new = _vitals(moat_rating="wide")
    legacy = {"key_vitals": new["_scoring_inputs"]}
    assert (
        compute_quality_score("warren_buffett", legacy)
        == compute_quality_score("warren_buffett", new)
    )
    assert compute_quality_score("warren_buffett", legacy) > 0.0
