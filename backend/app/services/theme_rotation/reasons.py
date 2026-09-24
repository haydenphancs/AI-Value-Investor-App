"""User-facing one-line reasons for a theme change — fixed templates, never model output.

Only ADDED / RETURNED / REMOVED reasons reach the app ("What changed this month"). They
cite RELEVANCE or ELIGIBILITY only — never a price move, a return, momentum or a valuation:
a curated list whose changes are explained by performance reads as buy/sell advice (the SEC
counts curated lists as a possible recommendation), and the app shows "Not a
recommendation" beside them. `tests/test_theme_rotation_rules.py` scans these strings.

They also claim only what the RANK measures. The rank is the whole score — relevance plus
the small market and size tie-breakers — so "more closely tied companies ranked ahead" was
false whenever two equally related companies were separated by the tie-breakers alone.
"""
from __future__ import annotations

from typing import Mapping, Optional

from app.services.theme_rotation.models import Action, Reason

_ADDED_BY_SOURCE = {
    "segments": "Added: a large share of its revenue now comes from this theme.",
    "description": "Added: its core business is built around this theme.",
    "industry": "Added: it now ranks among the top on-theme companies in our monthly review.",
}
_ADDED_BY_FUNDS = "Added: now held by most of the leading funds that track this theme."
_ADDED_ADJACENT = "Added: a material part of its business now serves this theme."

_TEXT = {
    Reason.RETURNED_TOP_RANKS: "Back: it again ranks among the top on-theme companies in our "
                               "monthly review.",
    Reason.REFILL: "Added to keep the list complete after a company left.",
    # No "for two months": a member ranked far off the pace leaves on its FIRST strike.
    Reason.OUTRANKED: "Rotated out: still related, but other on-theme companies ranked ahead "
                      "of it in our monthly review.",
    Reason.OFF_THEME: "Removed: its business no longer centres on this theme.",
    Reason.BELOW_FLOORS: "Removed: its size or trading volume fell below our minimums.",
    Reason.DELISTED: "Removed: it no longer trades on a US exchange.",
    Reason.BLOCKED: "Removed after an editorial review.",
}


def user_reason(action: Action, reason: Reason,
                score_parts: Optional[Mapping[str, object]] = None) -> Optional[str]:
    """The line shown to users, or None for decisions users never see."""
    if action not in (Action.ADDED, Action.RETURNED, Action.REMOVED):
        return None
    if action is Action.ADDED and reason is Reason.ENTERED_TOP_RANKS:
        parts = score_parts or {}
        etf_pts = parts.get("etf_pts")
        if isinstance(etf_pts, (int, float)) and etf_pts >= 14:
            return _ADDED_BY_FUNDS
        if parts.get("fit") == "adjacent":
            return _ADDED_ADJACENT
        return _ADDED_BY_SOURCE.get(str(parts.get("exposure_source") or ""),
                                    _ADDED_BY_SOURCE["industry"])
    return _TEXT.get(reason)


ALL_USER_TEXT = (tuple(_ADDED_BY_SOURCE.values()) + (_ADDED_BY_FUNDS, _ADDED_ADJACENT)
                 + tuple(_TEXT.values()))
