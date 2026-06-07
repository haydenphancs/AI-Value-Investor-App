"""
Persona-specific scoring math.

The per-vital judgments are deterministic, module-sourced 0-10 scores
(see the `_build_*_vital` / `_derive_*_vital` builders in
ticker_report_data_collector.py). What changes per persona is how those
vital scores roll up into the headline `overall_score` (0-100). Buffett
caring about moat ≠ Wood caring about moat, and that should change the
number, not just the prose.

`PERSONA_WEIGHTS[persona_key][vital_name]` weights sum to 1.0 and cover
ALL eight vitals (valuation, moat, financial_health, revenue, insider,
macro, forecast, wall_street). Missing vitals are renormalized out (not
counted as 0) so unmeasurable data redistributes weight instead of
capping the score.

Every vital now emits a numeric `score.value`; valuation/moat/
financial_health additionally keep their status/label strings for their
report cards, and the scorer falls back to those strings (via the
`_*_SCALE` maps) for reports cached before the numeric scores existed.
This is an INTERNAL substrate — the user-facing Key Vitals UI was
removed; `_scoring_inputs` is stripped from every client response.
"""

from typing import Any, Dict, Optional


# ── Per-Persona Weight Vectors ──────────────────────────────────────
#
# Each persona's investing philosophy mapped to a probability mass over
# the 8 vitals. Each row sums to 1.0.

PERSONA_WEIGHTS: Dict[str, Dict[str, float]] = {
    # Quality-at-a-fair-price: moat + balance-sheet durability dominate;
    # growth/analyst noise is de-emphasised but macro tail-risk still counts.
    "warren_buffett": {
        "moat":             0.26,
        "financial_health": 0.22,
        "valuation":        0.18,
        "insider":          0.12,
        "revenue":          0.08,
        "macro":            0.06,
        "forecast":         0.04,
        "wall_street":      0.04,
    },
    # Innovation/growth: revenue + forward forecast lead; traditional value
    # and balance-sheet weight is light; macro/secular regime matters.
    "cathie_wood": {
        "revenue":          0.24,
        "forecast":         0.24,
        "moat":             0.14,
        "valuation":        0.08,
        "insider":          0.08,
        "wall_street":      0.08,
        "macro":            0.08,
        "financial_health": 0.06,
    },
    # GARP: growth (forecast/revenue) balanced against price (valuation) and
    # a solid balance sheet — Lynch's "buy what you know, at a sane price".
    "peter_lynch": {
        "forecast":         0.22,
        "revenue":          0.18,
        "valuation":        0.18,
        "financial_health": 0.14,
        "insider":          0.08,
        "moat":             0.08,
        "wall_street":      0.06,
        "macro":            0.06,
    },
    # Concentrated activist value: balance-sheet quality + valuation + moat
    # carry the thesis; insider noise is minimal.
    "bill_ackman": {
        "financial_health": 0.22,
        "valuation":        0.22,
        "moat":             0.18,
        "forecast":         0.12,
        "revenue":          0.12,
        "macro":            0.08,
        "wall_street":      0.04,
        "insider":          0.02,
    },
}

# Equal weight across all 8 vitals — used for unknown persona keys so
# we never crash on a missing config and instead degrade to a neutral
# average.
_EQUAL_WEIGHTS: Dict[str, float] = {
    "valuation":        0.125,
    "moat":             0.125,
    "financial_health": 0.125,
    "revenue":          0.125,
    "insider":          0.125,
    "macro":            0.125,
    "forecast":         0.125,
    "wall_street":      0.125,
}


# ── String → 0-10 Score Derivation (FALLBACK only) ──────────────────
#
# New reports give valuation/moat/financial_health a numeric `score.value`
# (preferred by `_vital_score`). These maps are the back-compat bridge for
# reports cached before those numeric scores existed — they translate the
# status/label string each card still carries into a 0-10 number.

_VALUATION_SCALE: Dict[str, float] = {
    "deep_undervalued": 9.5,
    "underpriced":      7.5,
    "fair_value":       5.5,
    "overpriced":       3.0,
}

_MOAT_SCALE: Dict[str, float] = {
    "wide":   8.5,
    "narrow": 6.0,
    "none":   3.0,
}

# Covers BOTH the current builder levels (critical/weak/moderate/strong, see
# _build_health_vital) AND the legacy synonyms older rows carry. The previous
# version mapped only the legacy synonyms, so every grey/distress-zone company
# fell through to None and had its health dimension silently dropped — fixed.
_HEALTH_SCALE: Dict[str, float] = {
    "strong":     8.5,
    "moderate":   5.5,
    "weak":       3.5,
    "critical":   1.5,
    # legacy synonyms (pre-2026 builder output)
    "healthy":    7.0,
    "stressed":   4.0,
    "distressed": 2.0,
}


def _vital_score(
    vitals: Dict[str, Any], vital_name: str
) -> Optional[float]:
    """Return a 0-10 score for `vital_name`, or None if missing/unparseable.

    Prefers an explicit numeric `score.value` — all eight vitals now emit
    one. Falls back to the status/label string scales for
    valuation/moat/financial_health on reports cached before those vitals
    gained a numeric score, so user-history rows still resolve.
    """
    vital = vitals.get(vital_name)
    if not isinstance(vital, dict):
        return None

    # Preferred path: continuous numeric score (new reports, every vital).
    score_obj = vital.get("score")
    if isinstance(score_obj, dict):
        value = score_obj.get("value")
        if isinstance(value, (int, float)):
            return float(value)

    # Back-compat: derive 0-10 from the status/label string fields.
    if vital_name == "valuation":
        return _VALUATION_SCALE.get((vital.get("status") or "").lower())
    if vital_name == "moat":
        return _MOAT_SCALE.get((vital.get("overall_rating") or "").lower())
    if vital_name == "financial_health":
        return _HEALTH_SCALE.get((vital.get("level") or "").lower())
    return None


def compute_quality_score(
    persona_key: str, ticker_report_data: Dict[str, Any]
) -> float:
    """Persona-weighted overall score in [0, 100].

    Rolls the 8 vital scores (each 0-10) into a single number using the
    persona's weight vector, then RENORMALIZES over the vitals actually
    present so missing data redistributes weight instead of capping the
    score. A dimension we couldn't measure should drop out and let the
    measured ones speak — not be scored as if the company FAILED it.
    When nothing is measurable, returns 0.0.

    Unknown persona keys fall back to equal weights across all 8 vitals,
    so calling this with a bad key never crashes and never silently
    returns a Buffett-default.
    """
    weights = PERSONA_WEIGHTS.get(persona_key, _EQUAL_WEIGHTS)
    # `_scoring_inputs` is the internal per-dimension score layer; the legacy
    # "key_vitals" fallback covers reports cached before the key was renamed.
    vitals = (
        ticker_report_data.get("_scoring_inputs")
        or ticker_report_data.get("key_vitals")
        or {}
    )

    weighted_sum = 0.0
    present_weight = 0.0
    for vital_name, weight in weights.items():
        score = _vital_score(vitals, vital_name)
        if score is None:
            continue
        # Clip into [0, 10] in case a builder emits something out of band.
        clipped = max(0.0, min(10.0, score))
        weighted_sum += weight * clipped
        present_weight += weight

    # No measurable vital → honest 0.0 (better than a confident neutral).
    if present_weight <= 0.0:
        return 0.0

    # Renormalize over the measured weight mass (≤ 1.0), scale 0-10 → 0-100,
    # clip, round to one decimal so iOS sees stable, non-jittering numbers.
    overall = (weighted_sum / present_weight) * 10.0
    return round(max(0.0, min(100.0, overall)), 1)
