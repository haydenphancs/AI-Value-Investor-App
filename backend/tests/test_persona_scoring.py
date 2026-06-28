"""
Tests for persona_scoring.compute_quality_score.

Lock the contract that
  - per-persona weights live in PERSONA_WEIGHTS, sum to 1.0, and cover
    all ten vitals,
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
    _lin,
    PERSONA_WEIGHTS,
    STYLE_FIT_CAP,
    compute_quality_score,
    style_fit_adjustment,
)
from app.services.agents.ticker_report_data_collector import (
    _build_forecast_vital,
    _build_health_vital,
    _build_wall_street_sections,
    _derive_moat_vital,
    _valuation_score_from_upside,
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


def test_each_persona_covers_all_ten_vitals():
    # Every dimension (incl. the new card-driven `profitability` factor + macro/
    # forecast/wall_street/capital_allocation) is weighted for every persona —
    # no vital is silently dead weight.
    ten = {
        "valuation", "moat", "financial_health", "profitability", "revenue",
        "insider", "macro", "forecast", "wall_street", "capital_allocation",
    }
    for persona_key, w in PERSONA_WEIGHTS.items():
        assert set(w) == ten, f"{persona_key} differs: {ten ^ set(w)}"


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


# ── Full 4-persona argmax (winner selection across the whole roster) ──


def _full_scoring_inputs(**scores) -> dict:
    """Full 10-vital `_scoring_inputs` (every factor present incl. profitability +
    capital_allocation, which the `_vitals` fixture omits) so argmax is well-defined.
    Unspecified vitals default to a neutral 5.0."""
    keys = (
        "valuation", "moat", "financial_health", "profitability", "revenue",
        "insider", "macro", "forecast", "wall_street", "capital_allocation",
    )
    return {"_scoring_inputs": {k: {"score": {"value": float(scores.get(k, 5.0))}} for k in keys}}


def _all_persona_scores(inputs: dict) -> dict:
    return {p: compute_quality_score(p, inputs) for p in PERSONA_WEIGHTS}


def test_compounder_ranks_buffett_top():
    # Wide moat + high profitability + strong health + good capital allocation,
    # only modest growth — the quintessential Buffett compounder. Buffett must be
    # the argmax over ALL four personas (a weight edit that bled his moat/profitability
    # onto a neutral dim — which the single-persona profitability test would NOT catch —
    # flips this to Ackman).
    inputs = _full_scoring_inputs(
        moat=9.5, financial_health=9.0, profitability=9.5, capital_allocation=8.5,
        valuation=6.0, revenue=5.0, forecast=4.5,
    )
    scores = _all_persona_scores(inputs)
    winner = max(scores, key=scores.get)
    runner_up = sorted(scores.values())[-2]
    assert winner == "warren_buffett", scores
    assert scores["warren_buffett"] > runner_up


def test_hypergrower_ranks_wood_top():
    # Blistering revenue + forecast, weak moat/valuation/profitability — Wood's setup;
    # she must beat Lynch and Ackman (also growth-aware) too, not just Buffett.
    inputs = _full_scoring_inputs(
        revenue=9.5, forecast=9.5, macro=7.0, wall_street=6.0,
        moat=2.5, valuation=2.5, profitability=3.0, financial_health=4.0,
        capital_allocation=3.0,
    )
    scores = _all_persona_scores(inputs)
    winner = max(scores, key=scores.get)
    runner_up = sorted(scores.values())[-2]
    assert winner == "cathie_wood", scores
    assert scores["cathie_wood"] > runner_up


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


def _fake_analyst(target, low, high, *, consensus=None, up=0, down=0, maint=0, dists=None):
    """Minimal stand-in for the AnalystAnalysisResponse attributes that
    _build_wall_street_sections reads — enough to exercise the vital score."""
    import types
    return types.SimpleNamespace(
        consensus=consensus,
        target_price=target,
        price_target=types.SimpleNamespace(low_price=low, high_price=high),
        actions_summary=types.SimpleNamespace(upgrades=up, downgrades=down, maintains=maint),
        distributions=dists or [],
    )


def test_wall_street_vital_responsive_to_analyst_target_upside():
    # The Wall Street dimension reflects ANALYST conviction (real price-target
    # upside + consensus + momentum) — NOT the DCF fair value (that's the
    # valuation dimension's job, and borrowing it double-counts). Same
    # fair_value on both; only the analyst target differs, so a difference
    # proves the score is target-driven, not fair-value-driven.
    high, _ = _build_wall_street_sections(
        _fake_analyst(150.0, 130.0, 170.0), None, 100.0, 120.0, []
    )
    low, _ = _build_wall_street_sections(
        _fake_analyst(90.0, 80.0, 100.0), None, 100.0, 120.0, []
    )
    assert high["score"]["value"] > low["score"]["value"]


def test_wall_street_vital_unmeasured_without_analyst_coverage():
    # No analyst coverage at all (no targets, grades, or rating actions) → the
    # dimension is UNMEASURED (score.value=None) so compute_quality_score
    # renormalizes it out instead of voting a neutral / DCF-borrowed score.
    vital, _ = _build_wall_street_sections(None, None, 100.0, 150.0, [])
    assert vital["score"]["value"] is None


def test_forecast_vital_unmeasured_without_estimates():
    # No forward revenue/EPS estimates → UNMEASURED (None), renormalized out.
    assert _build_forecast_vital(None, None)["score"]["value"] is None
    # Any estimate present → a real numeric score.
    assert _build_forecast_vital(20.0, None)["score"]["value"] is not None


def test_valuation_score_continuous_and_monotone():
    # Continuous (not 4 buckets): a deeper overvaluation scores strictly lower
    # than a mild one, and the scale rises monotonically through fair → under.
    deep_over = _valuation_score_from_upside(-50.0)
    mild_over = _valuation_score_from_upside(-12.0)
    fair = _valuation_score_from_upside(0.0)
    under = _valuation_score_from_upside(25.0)
    assert deep_over < mild_over < fair < under
    assert math.isclose(fair, 5.5, abs_tol=0.6)   # fair anchored near neutral


def test_unmeasured_vital_renormalizes_out():
    # A vital whose score.value is None must NOT drag the headline toward 50:
    # the score equals what the MEASURED vitals alone produce.
    measured_only = compute_quality_score("warren_buffett", {"_scoring_inputs": {
        "moat": {"score": {"value": 9.0}},
        "financial_health": {"score": {"value": 9.0}},
    }})
    with_unmeasured = compute_quality_score("warren_buffett", {"_scoring_inputs": {
        "moat": {"score": {"value": 9.0}},
        "financial_health": {"score": {"value": 9.0}},
        "wall_street": {"score": {"value": None}},   # unmeasured → dropped
        "insider": {"score": {"value": None}},       # unmeasured → dropped
    }})
    assert math.isclose(measured_only, with_unmeasured, abs_tol=1e-9)
    assert measured_only == 90.0


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


# ── Persona style-fit modifier ──────────────────────────────────────
#
# On top of the weighted vital rollup, each persona nudges the score by a
# bounded amount using a few RAW signals through its own lens, so the SAME
# stock diverges more (Wood rewards 45% growth even with no moat; Buffett
# penalizes it). The nudge is capped at ±STYLE_FIT_CAP and contributes nothing
# when a signal is absent.

_EXTREME_SIGNAL_GRID = [
    {},
    # everything a persona could love
    {"roe": 40, "roic": 30, "debt_equity": 0.0, "gross_margin": 90, "gross_margin_prev": 10,
     "pe_ratio": 5, "fcf": 1e9, "net_income": 5e8, "mkt_cap": 1e9,
     "revenue_growth": 120, "revenue_cagr": 100, "eps_cagr": 90, "moat_score": 10, "mos_pct": 90},
    # everything a persona could hate
    {"roe": -50, "roic": -40, "debt_equity": 8.0, "gross_margin": 5, "gross_margin_prev": 60,
     "pe_ratio": 500, "fcf": -1e9, "net_income": 1e8, "mkt_cap": 1e12,
     "revenue_growth": -30, "revenue_cagr": -40, "eps_cagr": -50, "moat_score": 0, "mos_pct": -90},
]


@pytest.mark.parametrize("persona_key", sorted(PERSONA_WEIGHTS.keys()))
@pytest.mark.parametrize("signals", _EXTREME_SIGNAL_GRID)
def test_style_fit_adjustment_is_bounded(persona_key, signals):
    adj = style_fit_adjustment(persona_key, signals)
    assert -STYLE_FIT_CAP <= adj <= STYLE_FIT_CAP


def test_style_fit_zero_when_no_signals():
    for k in PERSONA_WEIGHTS:
        assert style_fit_adjustment(k, {}) == 0.0
        assert style_fit_adjustment(k, None) == 0.0


def test_style_fit_unknown_persona_zero():
    assert style_fit_adjustment("nobody", {"roe": 30, "revenue_growth": 50}) == 0.0


def test_style_fit_all_none_signals_is_zero():
    # _style_signals present but every value None (cyclical / pre-profit / no
    # PEG) → every sub-score drops out → 0.0, never a fabricated penalty.
    none_signals = {k: None for k in (
        "roe", "roic", "debt_equity", "gross_margin", "gross_margin_prev",
        "pe_ratio", "fcf", "net_income", "mkt_cap", "revenue_growth",
        "revenue_cagr", "eps_cagr", "moat_score", "mos_pct",
    )}
    for k in PERSONA_WEIGHTS:
        assert style_fit_adjustment(k, none_signals) == 0.0


def test_buffett_style_fit_rewards_quality_penalizes_junk():
    good = style_fit_adjustment("warren_buffett",
                                {"roe": 22, "debt_equity": 0.3, "moat_score": 9, "mos_pct": 35})
    bad = style_fit_adjustment("warren_buffett",
                               {"roe": 6, "debt_equity": 2.2, "moat_score": 2, "mos_pct": -25})
    assert good > 0 > bad


def test_wood_style_fit_rewards_hypergrowth():
    fast = style_fit_adjustment("cathie_wood", {"revenue_growth": 48, "revenue_cagr": 40})
    slow = style_fit_adjustment("cathie_wood", {"revenue_growth": 8, "revenue_cagr": 6})
    assert fast > 0 > slow


def test_lynch_style_fit_peg_directional():
    # Low PEG (cheap vs growth) beats high PEG (expensive vs growth).
    cheap = style_fit_adjustment("peter_lynch",
                                 {"pe_ratio": 12, "eps_cagr": 25, "revenue_growth": 25, "debt_equity": 0.2})
    pricey = style_fit_adjustment("peter_lynch",
                                  {"pe_ratio": 60, "eps_cagr": 10, "revenue_growth": 8, "debt_equity": 1.8})
    assert cheap > pricey


def test_ackman_style_fit_rewards_fcf_quality():
    # High FCF conversion (0.9) + yield (~7.5%) + ROIC, low leverage beats the
    # cash-poor, highly levered, low-ROIC case.
    quality = style_fit_adjustment("bill_ackman",
                                   {"fcf": 9e8, "net_income": 1e9, "mkt_cap": 1.2e10, "roic": 20, "debt_equity": 0.4})
    junk = style_fit_adjustment("bill_ackman",
                                {"fcf": 2e8, "net_income": 1e9, "mkt_cap": 4e10, "roic": 5, "debt_equity": 3.5})
    assert quality > junk


def test_burry_style_fit_inverts_on_price():
    # Contrarian: cheap + safe is rewarded, an expensive darling is PENALIZED.
    cheap = style_fit_adjustment("michael_burry",
                                 {"pe_ratio": 7, "mos_pct": 45, "debt_equity": 0.2, "fcf": 5e9, "mkt_cap": 5e10})
    expensive = style_fit_adjustment("michael_burry",
                                     {"pe_ratio": 50, "mos_pct": -30, "debt_equity": 2.5, "fcf": 1e8, "mkt_cap": 5e11})
    assert cheap > 0 > expensive
    # Isolate the P/E sub: a rich multiple ALONE must push DOWN. Pins the contrarian
    # inversion so a sign-flip on the P/E _lin args fails loudly (the bounded test alone
    # would not catch it).
    assert style_fit_adjustment("michael_burry", {"pe_ratio": 40}) < 0


def test_burry_negative_or_zero_pe_not_counted_as_cheap():
    # Loss-makers (pe <= 0) must NOT read as "maximally cheap" — the _pe_pos guard drops it.
    mos_only = style_fit_adjustment("michael_burry", {"mos_pct": 30})
    assert style_fit_adjustment("michael_burry", {"pe_ratio": -10, "mos_pct": 30}) == mos_only
    assert style_fit_adjustment("michael_burry", {"pe_ratio": 0.0, "mos_pct": 30}) == mos_only
    # A valid positive P/E DOES move it (proves the sub is wired, not always-None).
    assert style_fit_adjustment("michael_burry", {"pe_ratio": 8, "mos_pct": 30}) > mos_only


def test_burry_top_weight_is_valuation_health_second():
    # The contrarian/skeptic leads with cheapness + balance-sheet safety and keeps the
    # hype axes (analyst consensus, forward forecast) near-zero. If this drifts, Burry
    # stops being a contrarian (e.g. rewarding the crowded analyst darlings he shorts).
    w = PERSONA_WEIGHTS["michael_burry"]
    ranked = sorted(w, key=w.get, reverse=True)
    assert ranked[0] == "valuation"
    assert ranked[1] == "financial_health"
    assert w["forecast"] <= 0.03 and w["wall_street"] <= 0.03


@pytest.mark.parametrize("persona_key", ["warren_buffett", "peter_lynch", "bill_ackman", "michael_burry"])
def test_negative_equity_de_never_rewards(persona_key):
    # NEGATIVE debt_equity = negative shareholder equity = DISTRESS. The "lower is
    # better" _lin map would otherwise send de<0 to +1.0 (a maximal "fortress balance
    # sheet") — the inverse of reality. de=-120 must score <= a genuine low-debt de=0.3,
    # and must never be the max-favorable +cap.
    neg = style_fit_adjustment(persona_key, {"debt_equity": -120})
    fortress = style_fit_adjustment(persona_key, {"debt_equity": 0.3})
    assert neg <= fortress
    assert neg < STYLE_FIT_CAP


def test_lin_non_finite_drops_out():
    from math import inf, nan
    # NaN/inf must drop out (None), never resolve to +1.0 via the order-dependent
    # max(-1, min(1, NaN)) clamp.
    assert _lin(nan, 10.0, 40.0) is None
    assert _lin(inf, 10.0, 40.0) is None
    assert _lin(-inf, 10.0, 40.0) is None
    assert _lin(5.0, 10.0, 40.0) is not None   # finite still scores


@pytest.mark.parametrize("persona_key", sorted(PERSONA_WEIGHTS.keys()))
@pytest.mark.parametrize("signal", ["mos_pct", "debt_equity", "roe", "pe_ratio", "revenue_growth"])
def test_style_fit_single_non_finite_signal_is_zero(persona_key, signal):
    # A lone NaN/inf signal (corrupted FMP data) must contribute NOTHING (drop out),
    # never become the most-favorable sub-score. Same hazard guarded+tested in
    # test_cards_become_score.py for _card_weighted_to_score10.
    assert style_fit_adjustment(persona_key, {signal: float("nan")}) == 0.0
    assert style_fit_adjustment(persona_key, {signal: float("inf")}) == 0.0


def _growth_archetype(with_signals: bool) -> dict:
    si = {
        "revenue": {"score": {"value": 9.0}}, "forecast": {"score": {"value": 9.0}},
        "moat": {"score": {"value": 3.0}}, "valuation": {"score": {"value": 2.0}},
        "financial_health": {"score": {"value": 4.0}},
    }
    if with_signals:
        si["_style_signals"] = {
            "revenue_growth": 45, "revenue_cagr": 38, "roe": 6, "debt_equity": 0.9,
            "moat_score": 3, "mos_pct": -15, "pe_ratio": 80,
        }
    return {"_scoring_inputs": si}


def test_style_signals_widen_persona_gap_for_growth_archetype():
    # Same vitals; adding the raw style signals should WIDEN the Wood-over-Buffett
    # gap (Wood rewards the 45% growth; Buffett penalizes no-moat / no-MOS / low-ROE).
    base_gap = (compute_quality_score("cathie_wood", _growth_archetype(False))
                - compute_quality_score("warren_buffett", _growth_archetype(False)))
    fit_gap = (compute_quality_score("cathie_wood", _growth_archetype(True))
               - compute_quality_score("warren_buffett", _growth_archetype(True)))
    assert fit_gap > base_gap > 0


def test_explicit_signals_equal_derived_signals():
    # The collector scores from {"_scoring_inputs": ...}; research_service
    # re-scores from the full report dict. Both derive signals from
    # _scoring_inputs._style_signals, so passing signals explicitly must equal
    # deriving them — the two scoring sites can't drift.
    report = _growth_archetype(True)
    derived = compute_quality_score("cathie_wood", report)
    explicit = compute_quality_score(
        "cathie_wood", report, signals=report["_scoring_inputs"]["_style_signals"]
    )
    assert derived == explicit


def test_extra_top_level_keys_do_not_change_score():
    # research_service passes the FULL report dict (symbol, quality_score, …);
    # the collector passes just {"_scoring_inputs": ...}. Extra top-level keys
    # must not change the computed score.
    report = _growth_archetype(True)
    full = dict(report)
    full["symbol"] = "TST"
    full["quality_score"] = 99
    assert (compute_quality_score("warren_buffett", report)
            == compute_quality_score("warren_buffett", full))
