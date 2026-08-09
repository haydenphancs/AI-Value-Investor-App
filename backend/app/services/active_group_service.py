"""The active group — the ONE list that drives Home, Updates and Tracking.

A "group" is a `portfolios` row: a user-named subset of their master `watchlist_items`.
Exactly one is active per user (migration 126), and this module is the only place that
resolves it, so the three surfaces cannot drift apart again.

WHY THIS IS A SERVICE AND NOT THREE SIMILAR QUERIES
---------------------------------------------------
It already was three similar queries, and they disagreed:

    Home     `home_dashboard_service._build_watchlist`  → watchlist_items, 12 newest
    Updates  `endpoints/updates.py get_updates_tabs`    → watchlist_items, 30 newest
    Tracking `TrackingViewModel.filteredAssets`         → feed ∩ the active portfolio

Only the third one knew about groups, so renaming "Holdings" changed one screen out of
three, and a ticker added while two groups existed showed on two screens and not the
third. Any future surface that wants "the user's tickers" should call this, not write a
fourth variant.

THE READ RAISES; "NO GROUP" RETURNS None
----------------------------------------
These two must never look alike. `tracking_service` once answered a failed watchlist read
with an empty 200 that was byte-identical to a genuinely empty watchlist, and the client
read that as "the user removed everything" and propagated the deletion server-side. So a
Supabase failure raises `ActiveGroupUnavailable` and a user who simply has no active group
returns `None`; each caller then decides its own honest degradation.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from app.database import get_supabase

logger = logging.getLogger(__name__)

# A defensive ceiling on how many rows one group can contribute. Nothing in the product
# creates groups this large — the Updates strip caps at 30 chips and Home at 12 tiles — so
# hitting it means either a runaway client or a data problem, and it is logged rather than
# silently truncated.
MAX_GROUP_TICKERS = 500


class ActiveGroupUnavailable(RuntimeError):
    """The group could not be READ. Distinct from "the user has no group", which is None."""


@dataclass(frozen=True)
class ActiveGroup:
    """The user's active group and its membership, in the user's own order."""

    id: str
    name: str
    # Uppercased, de-duplicated, ordered by `portfolio_items.position` — i.e. the order the
    # user arranged on the Tracking tab. Callers that need a different order (the Updates
    # tier truncation sorts alphabetically) re-sort a copy.
    tickers: List[str] = field(default_factory=list)

    @property
    def ticker_count(self) -> int:
        return len(self.tickers)


def _rows_to_tickers(rows: Sequence[Dict[str, Any]]) -> List[str]:
    """Pure: uppercase, drop blanks, de-duplicate, preserve incoming (position) order."""
    seen: set[str] = set()
    out: List[str] = []
    for row in rows:
        raw = row.get("ticker")
        if not isinstance(raw, str):
            continue
        ticker = raw.strip().upper()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        out.append(ticker)
    return out


async def get_active_group(user_id: str) -> Optional[ActiveGroup]:
    """Resolve the caller's active group, or None if they have none.

    Raises ``ActiveGroupUnavailable`` when the read itself fails — never returns an empty
    group to mean "the database is down".
    """
    if not user_id:
        return None

    def _read() -> Optional[ActiveGroup]:
        supabase = get_supabase()
        rows = (
            supabase.table("portfolios")
            .select("id, name")
            .eq("user_id", user_id)
            .eq("is_active", True)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not rows:
            return None

        group = rows[0]
        group_id = str(group["id"])
        item_rows = (
            supabase.table("portfolio_items")
            .select("ticker, position")
            .eq("portfolio_id", group_id)
            .order("position")
            .limit(MAX_GROUP_TICKERS)
            .execute()
            .data
            or []
        )
        if len(item_rows) >= MAX_GROUP_TICKERS:
            logger.warning(
                "Active group %s for user=%s hit the %d-ticker ceiling — membership truncated",
                group_id, user_id, MAX_GROUP_TICKERS,
            )
        return ActiveGroup(
            id=group_id,
            name=str(group.get("name") or ""),
            tickers=_rows_to_tickers(item_rows),
        )

    try:
        return await asyncio.to_thread(_read)
    except Exception as exc:
        logger.error(
            "Active group read failed for user=%s: %s: %s",
            user_id, type(exc).__name__, exc, exc_info=True,
        )
        raise ActiveGroupUnavailable(
            f"{type(exc).__name__}: {exc}"
        ) from exc


async def fetch_ticker_metadata(
    user_id: str, tickers: Sequence[str]
) -> Dict[str, Dict[str, Any]]:
    """Company name / logo / asset type for the given tickers, keyed by UPPERCASE ticker.

    Group membership lives in `portfolio_items`, which carries only the symbol; every
    human-readable field lives on the user's `watchlist_items` row. `set_portfolio_tickers`
    enforces `portfolio_items ⊆ watchlist_items`, so a miss here means a repair path failed
    rather than a normal state — the caller falls back to the bare symbol.

    Best-effort: a failed lookup yields {} and the caller renders symbols. Never raises,
    because a missing display name must not cost the user their tab bar.
    """
    symbols = [t for t in dict.fromkeys(tickers) if t]
    if not user_id or not symbols:
        return {}

    def _read() -> List[Dict[str, Any]]:
        return (
            get_supabase().table("watchlist_items")
            .select("ticker, company_name, logo_url, asset_type")
            .eq("user_id", user_id)
            .in_("ticker", symbols)
            .execute()
            .data
            or []
        )

    try:
        rows = await asyncio.to_thread(_read)
    except Exception as exc:
        logger.warning(
            "Ticker metadata lookup failed for user=%s (%d symbol(s)): %s: %s — "
            "rendering bare symbols",
            user_id, len(symbols), type(exc).__name__, exc,
        )
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        raw = row.get("ticker")
        if isinstance(raw, str) and raw.strip():
            out[raw.strip().upper()] = row
    return out
