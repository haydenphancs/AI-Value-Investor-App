"""
Shared helpers for classifying analyst rating actions.

Both ``analyst_service`` (TickerDetailView → Analysis tab → Analyst Momentum →
Actions screen) and ``tracking_service`` (watchlist Analyst Ratings alert
card) need to agree on how to classify each FMP ``grades`` row.

Without this, an FMP row like ``action="maintain", prev="Buy", new="Buy"``
would be rendered as ``MAINTAIN`` on the Actions screen but filtered as
``reiterate`` (noise) from the alert, confusing users who drill through.

The normalized labels:
  - ``"upgrade"``    — firm raised its rating (material)
  - ``"downgrade"``  — firm lowered its rating (material)
  - ``"initiate"``   — firm started coverage (material)
  - ``"maintain"``   — firm reaffirmed existing rating (non-material; FMP
                      uses ``action="maintain"`` or ``"reiterated"`` here)
"""

from typing import Optional, Tuple


# Maps analyst rating labels → a 0-4 rank so a direction can be inferred when FMP
# omits/mislabels the `action` (e.g. labels a real cut "maintain"). Covers the
# common regional-broker vocabularies (RBC "Sector Perform/Outperform", JMP
# "Market Outperform", Oppenheimer "Perform", etc.) — an unranked label silently
# falls back to "maintain", which would HIDE a genuine downgrade.
_RATING_RANK = {
    "strong sell": 0, "sell": 1, "underperform": 1, "underweight": 1,
    "sector underperform": 1, "market underperform": 1, "reduce": 1, "negative": 1,
    "hold": 2, "neutral": 2, "market perform": 2, "sector perform": 2,
    "equal-weight": 2, "equal weight": 2, "equalweight": 2, "perform": 2,
    "in-line": 2, "in line": 2, "peer perform": 2, "sector weight": 2,
    "buy": 3, "overweight": 3, "outperform": 3, "accumulate": 3,
    "sector outperform": 3, "market outperform": 3, "positive": 3, "add": 3,
    "strong buy": 4, "conviction buy": 4, "long term buy": 4,
}


# ── Grade → distribution bucket ───────────────────────────────────────
#
# There used to be a SECOND, narrower table (`analyst_service._GRADE_TO_CATEGORY`)
# doing this job, and the two disagreed in BOTH directions:
#
#   * known to _RATING_RANK only  -> fell to the category table's `"Hold"` default,
#     so `add` (a Buy) and `sector underperform` / `market underperform` (Sells)
#     were all counted as HOLDs, biasing the distribution toward the middle;
#   * known to the category table only -> `long term buy` had no rank, so
#     `_infer_rating_direction` reported a genuine upgrade as "maintain".
#
# One table, one meaning. The rank IS the bucket: 0..4 maps onto the five columns.
RANK_TO_CATEGORY: dict[int, str] = {
    0: "Strong Sell",
    1: "Sell",
    2: "Hold",
    3: "Buy",
    4: "Strong Buy",
}

# A label neither table recognises ("mixed", "top pick", "average", "mkt perform"
# have all been seen on the wire). It is DELIBERATELY not one of the five: folding an
# uninterpretable rating into Hold lets it vote, and a firm whose opinion we cannot
# read should not cast a ballot. `_compute_distribution` builds a fixed five-key dict,
# so this value is counted nowhere and never reaches iOS.
RATING_CATEGORY_UNKNOWN = "Unknown"


def classify_grade(grade: str) -> str:
    """Map an FMP grade label to one of the five distribution buckets.

    Returns :data:`RATING_CATEGORY_UNKNOWN` when the label is in neither table, so
    the caller can log it rather than silently miscount it.
    """
    rank = _RATING_RANK.get((grade or "").strip().lower())
    if rank is None:
        return RATING_CATEGORY_UNKNOWN
    return RANK_TO_CATEGORY[rank]


def _infer_rating_direction(previous: str, new: str) -> str:
    """Infer direction from rating labels alone (FMP sometimes omits action)."""
    prev = _RATING_RANK.get((previous or "").strip().lower())
    curr = _RATING_RANK.get((new or "").strip().lower())
    if prev is None or curr is None:
        return "maintain"
    if curr > prev:
        return "upgrade"
    if curr < prev:
        return "downgrade"
    return "maintain"


def normalize_fmp_action(
    fmp_action: Optional[str],
    previous_grade: Optional[str] = None,
    new_grade: Optional[str] = None,
) -> str:
    """Normalize a raw FMP ``action`` string to one of four canonical labels.

    FMP's ``action`` field is inconsistent:
      - explicit: ``"upgrade"``, ``"downgrade"``, ``"init"`` / ``"initiate"``,
        ``"reiterated"``
      - ambiguous: ``"maintain"`` / ``"hold"`` → often used when the firm
        reaffirms an existing rating (same prev + new)
      - missing: blank → we fall back to ``prev vs new`` comparison

    When FMP labels a row ``action="maintain"`` AND the new/previous ratings
    are identical, that is semantically a reiteration — we collapse it into
    the ``"maintain"`` bucket to keep the alert and Actions screen aligned.
    """
    raw = (fmp_action or "").strip().lower()

    if raw == "upgrade":
        return "upgrade"
    if raw == "downgrade":
        return "downgrade"
    if raw in ("init", "initiate", "initiated"):
        return "initiate"
    if raw in ("maintain", "hold", "reiterated", "reiterate"):
        # "maintain" sometimes hides a real upgrade/downgrade if the firm
        # changed the rating while also "maintaining" coverage. Use
        # previous/new to catch that edge case.
        if previous_grade and new_grade:
            inferred = _infer_rating_direction(previous_grade, new_grade)
            if inferred in ("upgrade", "downgrade"):
                return inferred
        return "maintain"

    # Unknown / missing action: try to infer from ratings
    if previous_grade and new_grade:
        return _infer_rating_direction(previous_grade, new_grade)
    return "maintain"


def is_material_action(normalized_action: str) -> bool:
    """True when the normalized action carries real rating-change signal.

    Material = upgrade, downgrade, initiate. Maintain is non-material
    (firm reaffirmed existing view, no new information).
    """
    return normalized_action in ("upgrade", "downgrade", "initiate")


def classify_for_alerts(
    fmp_action: Optional[str],
    previous_grade: Optional[str] = None,
    new_grade: Optional[str] = None,
) -> Tuple[str, bool]:
    """Convenience for the alerts pipeline.

    Returns ``(normalized_action, is_material)``. Callers that want only
    meaningful rating changes can gate on the second element.
    """
    normalized = normalize_fmp_action(fmp_action, previous_grade, new_grade)
    return normalized, is_material_action(normalized)
