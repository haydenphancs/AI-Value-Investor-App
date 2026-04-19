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


_RATING_RANK = {
    "strong sell": 0, "sell": 1, "underperform": 1, "underweight": 1,
    "hold": 2, "neutral": 2, "market perform": 2, "equal-weight": 2, "equal weight": 2,
    "buy": 3, "overweight": 3, "outperform": 3, "accumulate": 3,
    "strong buy": 4, "conviction buy": 4,
}


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
