"""
Persona-specific scoring math.

The agent's per-vital judgments stay AI-driven — what changes per
persona is how those vital scores roll up into the headline
`overall_score` (0-100). Buffett caring about moat ≠ Wood caring about
moat, and that should change the number, not just the prose.

`PERSONA_WEIGHTS[persona_key][vital_name]` weights sum to 1.0. Vitals
not in the dict count as 0. Three vitals lack a numeric `score` in the
iOS DTO (valuation, moat, financial_health) — derive 0-10 from their
string status fields without changing the iOS Codable contract.
"""

from typing import Any, Dict, Optional


# ── Per-Persona Weight Vectors ──────────────────────────────────────
#
# Each persona's investing philosophy mapped to a probability mass over
# the 8 vitals. Each row sums to 1.0.

PERSONA_WEIGHTS: Dict[str, Dict[str, float]] = {
    "warren_buffett": {
        "moat":             0.30,
        "financial_health": 0.25,
        "valuation":        0.20,
        "insider":          0.15,
        "revenue":          0.10,
    },
    "cathie_wood": {
        "revenue":          0.25,
        "forecast":         0.25,
        "moat":             0.15,
        "wall_street":      0.10,
        "valuation":        0.10,
        "insider":          0.10,
        "financial_health": 0.05,
    },
    "peter_lynch": {
        "forecast":         0.25,
        "revenue":          0.20,
        "valuation":        0.20,
        "financial_health": 0.15,
        "insider":          0.10,
        "moat":             0.10,
    },
    "bill_ackman": {
        "financial_health": 0.25,
        "valuation":        0.25,
        "moat":             0.20,
        "forecast":         0.15,
        "revenue":          0.15,
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


# ── String → 0-10 Score Derivation ──────────────────────────────────
#
# Three vitals carry status labels instead of numeric scores in the
# iOS DTO. Map the labels to numbers here — this is the bridge that
# lets persona weights apply uniformly across all 8 vitals.

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

_HEALTH_SCALE: Dict[str, float] = {
    "strong":     8.5,
    "healthy":    7.0,
    "stressed":   4.0,
    "distressed": 2.0,
}


def _vital_score(
    vitals: Dict[str, Any], vital_name: str
) -> Optional[float]:
    """Return a 0-10 score for `vital_name`, or None if missing/unparseable.

    For revenue/insider/macro/forecast/wall_street, the iOS DTO carries
    a nested `score.value` (VitalScoreDTO). For valuation/moat/financial_health,
    derive the score from the status/label string field.
    """
    vital = vitals.get(vital_name)
    if not isinstance(vital, dict):
        return None

    if vital_name == "valuation":
        status = (vital.get("status") or "").lower()
        return _VALUATION_SCALE.get(status)

    if vital_name == "moat":
        rating = (vital.get("overall_rating") or "").lower()
        return _MOAT_SCALE.get(rating)

    if vital_name == "financial_health":
        level = (vital.get("level") or "").lower()
        return _HEALTH_SCALE.get(level)

    # Numeric-score vitals: revenue, insider, macro, forecast, wall_street
    score_obj = vital.get("score")
    if isinstance(score_obj, dict):
        value = score_obj.get("value")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def compute_quality_score(
    persona_key: str, ticker_report_data: Dict[str, Any]
) -> float:
    """Persona-weighted overall score in [0, 100].

    Rolls the 8 vital scores (each 0-10) into a single number using
    the persona's weight vector. Missing vitals contribute 0, which
    is correct — a company we couldn't measure on a dimension a
    persona cares about should score lower for that persona.

    Unknown persona keys fall back to equal weights across all 8
    vitals, so calling this with a bad key never crashes and never
    silently returns a Buffett-default.
    """
    weights = PERSONA_WEIGHTS.get(persona_key, _EQUAL_WEIGHTS)
    vitals = ticker_report_data.get("key_vitals") or {}

    weighted_sum = 0.0
    for vital_name, weight in weights.items():
        score = _vital_score(vitals, vital_name)
        if score is None:
            continue
        # Clip into [0, 10] in case the AI emits something out of band
        clipped = max(0.0, min(10.0, score))
        weighted_sum += weight * clipped

    # Scale 0-10 → 0-100, clip into bounds, round to one decimal so
    # iOS sees stable numbers that don't jitter on float reformatting.
    overall = weighted_sum * 10.0
    return round(max(0.0, min(100.0, overall)), 1)
