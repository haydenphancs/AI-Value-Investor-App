"""
Tests for persona_scoring.compute_quality_score.

Lock the contract that
  - per-persona weights live in PERSONA_WEIGHTS, sum to 1.0, and cover
    all eight vitals,
  - the same `ticker_report_data` produces persona-specific scores
    (Buffett caring about moat ≠ Wood caring about moat),
  - missing vitals are RENORMALIZED out (a dimension we couldn't measure
    redistributes its weight rather than deflating the score),
  - unknown persona keys fall back to equal weights and never crash.
"""

from __future__ import annotations

import math

import pytest

from app.services.agents.persona_scoring import (
    _HEALTH_SCALE,
    PERSONA_WEIGHTS,
    compute_quality_score,
)
from app.services.agents.ticker_report_data_collector import (
    _build_forecast_vital,
    _build_health_vital,
    _build_wall_street_sections,
    _derive_moat_vital,
)
from app.services.agents.narrative_prompts import _thesis_target_counts


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


def test_buffett_top_weight_is_moat():
    # Locks the brief — Buffett's #1 priority is moat. If this drifts, the
    # scoring is no longer faithful to the persona prompt. The exact weight
    # is tunable; that moat is his single highest weight is not.
    w = PERSONA_WEIGHTS["warren_buffett"]
    assert max(w, key=w.get) == "moat"


def test_each_persona_covers_all_eight_vitals():
    # The redesign weights every dimension (incl. macro, forecast, wall_street)
    # for every persona — no vital is silently dead weight.
    eight = {
        "valuation", "moat", "financial_health", "revenue",
        "insider", "macro", "forecast", "wall_street",
    }
    for persona_key, w in PERSONA_WEIGHTS.items():
        assert set(w) == eight, f"{persona_key} is missing {eight - set(w)}"


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


def test_missing_vitals_renormalize_over_present_weights():
    # If the agent drops vitals, the score is RENORMALIZED over the vitals
    # actually present — a dimension we couldn't measure redistributes its
    # weight instead of dragging the score down as if the company failed it.
    incomplete = {"_scoring_inputs": {
        "moat": {"overall_rating": "wide"},
        # everything else missing
    }}

    score = compute_quality_score("warren_buffett", incomplete)

    # Only moat present (wide → 8.5/10). Renormalized over the single present
    # weight → 8.5 * 10 = 85.0, NOT deflated to 0.26 * 8.5 * 10 = 22.1.
    assert score == 85.0


def test_partial_data_not_capped_by_missing_weight():
    # A company measured strong on the two dimensions we COULD assess should
    # not be capped below 100 just because other vitals are absent.
    partial = {"_scoring_inputs": {
        "moat":             {"overall_rating": "wide"},   # 8.5
        "financial_health": {"level": "strong"},          # 8.5
    }}
    # Buffett: both present (weights .26 + .22), both 8.5 → renormalized 85.0.
    assert compute_quality_score("warren_buffett", partial) == 85.0


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


# ── Vital builders: deterministic sub-scores are RESPONSIVE ─────────


def test_health_vital_responsive_and_levels_all_mappable():
    # The old _build_health_vital set `level` from Altman-Z only and left a
    # numeric score absent for non-"strong" companies, so the scorer dropped
    # the dimension. Now every level carries a numeric, monotonic score AND is
    # mappable by _HEALTH_SCALE (the fallback path) — the root of the original
    # bug where moderate/weak/critical → None → dimension dropped.
    strong = _build_health_vital(altman_z=4.0, debt_equity=0.4, fcf_negative=False)
    moderate = _build_health_vital(altman_z=2.6, debt_equity=0.4, fcf_negative=False)
    weak = _build_health_vital(altman_z=2.0, debt_equity=0.4, fcf_negative=False)
    critical = _build_health_vital(altman_z=1.0, debt_equity=0.4, fcf_negative=False)

    scores = [v["score"]["value"] for v in (critical, weak, moderate, strong)]
    assert scores == sorted(scores), f"health score not monotonic: {scores}"
    assert strong["score"]["value"] > critical["score"]["value"]

    for v in (strong, moderate, weak, critical):
        assert _HEALTH_SCALE.get(v["level"]) is not None


def test_health_vital_penalizes_leverage_and_negative_fcf():
    # Same Altman-Z, but heavy debt + cash burn must score materially lower —
    # the ORCL case (D/E 4.21, negative FCF) the old code ignored.
    clean = _build_health_vital(altman_z=3.2, debt_equity=0.3, fcf_negative=False)
    levered = _build_health_vital(altman_z=3.2, debt_equity=4.21, fcf_negative=True)
    assert levered["score"]["value"] < clean["score"]["value"]
    # D/E > 2.5 (−1.5) + negative FCF (−1.0) = −2.5 off the same base.
    assert round(clean["score"]["value"] - levered["score"]["value"], 1) == 2.5


def test_forecast_vital_responsive_to_cagr():
    # Was hard-coded 7.0 regardless of growth.
    fast = _build_forecast_vital(revenue_cagr=35.0, eps_cagr=35.0)
    flat = _build_forecast_vital(revenue_cagr=0.0, eps_cagr=0.0)
    shrinking = _build_forecast_vital(revenue_cagr=-20.0, eps_cagr=-20.0)
    assert (
        fast["score"]["value"]
        > flat["score"]["value"]
        > shrinking["score"]["value"]
    )
    assert flat["score"]["value"] == 5.0  # neutral base


def test_wall_street_vital_responsive_to_upside():
    # Was hard-coded 7.0. analyst=None path: score driven by fair-value upside.
    high, _ = _build_wall_street_sections(None, None, 100.0, 150.0, [])
    low, _ = _build_wall_street_sections(None, None, 100.0, 90.0, [])
    assert high["score"]["value"] > low["score"]["value"]


def test_moat_vital_rewards_breadth_not_just_max():
    # The old code took the single MAX dimension, so these scored identically.
    one_wall = _derive_moat_vital([
        {"name": "Switching Costs", "score": 9.0},
        {"name": "Network", "score": 2.0},
        {"name": "Cost", "score": 2.0},
        {"name": "Brand", "score": 2.0},
    ])
    many_walls = _derive_moat_vital([
        {"name": "Switching Costs", "score": 9.0},
        {"name": "Network", "score": 8.5},
        {"name": "Cost", "score": 8.0},
        {"name": "Brand", "score": 8.0},
    ])
    assert many_walls["score"]["value"] > one_wall["score"]["value"]


def test_moat_vital_docked_by_high_competitor_threat():
    dims = [{"name": "Brand", "score": 8.0}, {"name": "Scale", "score": 7.5}]
    safe = _derive_moat_vital(dims, competitors=[{"threat_level": "low"}])
    threatened = _derive_moat_vital(dims, competitors=[{"threat_level": "high"}])
    assert threatened["score"]["value"] < safe["score"]["value"]


# ── Signal-driven Bull/Bear count (same substrate as the score) ─────


def _scoring_report(**scores) -> dict:
    """Minimal assembled-report shape for _thesis_target_counts: numeric
    sub-scores under _scoring_inputs."""
    return {"_scoring_inputs": {
        name: {"score": {"value": v}} for name, v in scores.items()
    }}


def test_target_counts_quiet_stock_floors_at_two():
    # All eight vitals neutral (~5/10) → no strong, no weak → floor 2/2.
    report = _scoring_report(
        valuation=5.0, moat=5.0, financial_health=5.0, revenue=5.0,
        insider=5.0, macro=5.0, forecast=5.0, wall_street=5.0,
    )
    assert _thesis_target_counts(report) == (2, 2)


def test_target_counts_many_strong_signals_raise_bull_to_cap():
    # Six strong dimensions → bull clamps to the UI cap of 5; no weak → bear 2.
    report = _scoring_report(
        valuation=8.0, moat=8.5, financial_health=9.0, revenue=8.0,
        insider=7.5, macro=8.0, forecast=5.0, wall_street=5.0,
    )
    bull, bear = _thesis_target_counts(report)
    assert bull == 5
    assert bear == 2


def test_target_counts_many_weak_signals_raise_bear():
    report = _scoring_report(
        valuation=3.0, moat=3.0, financial_health=2.0, revenue=3.5,
        insider=5.0, macro=4.0, forecast=5.0, wall_street=5.0,
    )
    bull, bear = _thesis_target_counts(report)
    assert bear >= 4
    assert bull == 2
