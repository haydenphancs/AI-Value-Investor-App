"""
Search picks — the WRITE side of the "Trending searches" chips (read side:
`search_trending_service`).

A pick is a tap on a search RESULT row by a signed-in account. It becomes nothing more than
`+1` on an anonymous (ET day, ticker, asset type) counter in `search_pick_daily` (migration
179). No per-user row is ever written: the user chose anonymous counters so that search
activity is not linked to anyone (App Privacy: "Search History — not linked to you").

De-duplication is what keeps "at least 3" meaningful without storing who picked what:
  • the app sends a ticker at most once per 7 ET days (a UserDefaults set, cleared at
    session end); and
  • this module checks again in memory: `_PickDedup` keys a dict by an HMAC of
    (account, ticker, CLASS) under a random per-process key that is never persisted or
    logged, so the map is useless outside this process and gone on restart. The class is
    crypto vs everything else — the split `get_search_trending` sums over. Keyed on the
    declared type, one account sent AAPL as stock, etf and fund and reached the floor of 3
    alone (review, 2026-09-26).
A restart plus a second device or a reinstall can still count one person twice. That gap is
documented (migration 179, SYSTEM_DESIGN_GUIDELINES §10), not hidden.

Delivery is AT MOST ONCE: the pick is marked before the write, so a failed write loses a
count rather than risk counting one person twice.

Writes FAIL CLOSED where reads fail open: a symbol that cannot be verified (the
active-listing directory is still cold after a deploy) is not counted.

⚠️ Never log a user id on the same line as a ticker. That line would be exactly the linked
search history this design exists not to keep — in Railway logs and in Sentry.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import date, datetime
from typing import Dict, Optional, Tuple, Union

from app.database import get_supabase
from app.schemas.search_trending import PICK_TYPES, SYMBOL_PATTERN
from app.schemas.stock import StockSearchResult
from app.services import stock_search_service
from app.services.crypto_names import CRYPTO_NAMES
from app.utils.market_hours import ET
from app.utils.supabase_async import sb_exec

logger = logging.getLogger(__name__)

#: Same shape as migration 179's CHECK on search_pick_daily.ticker — a symbol the database
#: would refuse is refused here first. ASCII only: `[A-Z0-9]` never matches a look-alike.
_SYMBOL_RE = re.compile(SYMBOL_PATTERN)

PICK_DEDUP_DAYS = 7
PER_ACCOUNT_DAILY_CAP = 50
MAX_TRACKED_KEYS = 100_000

Outcome = str  # "counted" | "duplicate" | "capped" | "full" | "invalid" | "unverifiable" | ...


def validate_pick(
    symbol: object,
    asset_type: object,
    directory: Optional[Dict[str, str]],
) -> Union[Tuple[str, str], str]:
    """`(SYMBOL, type)` for a countable pick, else the reason it is not counted."""
    if not isinstance(symbol, str) or not (asset_type is None or isinstance(asset_type, str)):
        return "invalid"
    sym = symbol.strip().upper()
    kind = (asset_type or "stock").strip().lower()
    if not _SYMBOL_RE.fullmatch(sym) or kind not in PICK_TYPES:
        return "invalid"
    if kind == "crypto":
        return (sym, kind) if sym in CRYPTO_NAMES else "invalid"
    # Hidden by product decision. `would_keep` cannot see this one: its mutual-fund rule
    # needs the exchange, and a pick carries none.
    if stock_search_service.is_mutual_fund_symbol(sym):
        return "invalid"
    if directory is None:
        return "unverifiable"
    # The directory's name, not "": the same-issuer rule compares names, so an empty one
    # would let a note twin (AGNCL beside AGNC) count as a pick.
    candidate = StockSearchResult(symbol=sym, name=directory.get(sym, ""), type=kind)
    if not stock_search_service.would_keep(candidate, "", directory):
        return "invalid"
    return sym, kind


def pick_class(kind: str) -> str:
    """What one pick is de-duplicated on: a coin, or a listed security. stock/etf/fund for
    one symbol are the SAME security declared differently (the SQL sums them)."""
    return "crypto" if kind == "crypto" else "security"


class _PickDedup:
    """Per-process memory of which (account, ticker, type) was counted, and when.

    Keys are 16-byte HMAC digests under a random key made at construction — no raw account
    id is stored, and the digests mean nothing to anyone without this process's key.
    Pruned daily; bounded, and when full a NEW pick is not counted (dropping is always the
    privacy-safe direction)."""

    def __init__(
        self,
        key: Optional[bytes] = None,
        *,
        max_entries: int = MAX_TRACKED_KEYS,
        per_user_daily_cap: int = PER_ACCOUNT_DAILY_CAP,
        window_days: int = PICK_DEDUP_DAYS,
    ):
        self._key = key or secrets.token_bytes(32)
        self.max_entries = max_entries
        self.per_user_daily_cap = per_user_daily_cap
        self.window_days = window_days
        self._seen: Dict[bytes, int] = {}              # pick digest -> day ordinal counted
        self._daily: Dict[bytes, Tuple[int, int]] = {}  # account digest -> (day, count)
        self._pruned_on: Optional[int] = None

    def _digest(self, *parts: str) -> bytes:
        return hmac.new(self._key, "\x1f".join(parts).encode(), hashlib.sha256).digest()[:16]

    def _maybe_prune(self, day: int) -> None:
        if self._pruned_on == day:
            return
        self._pruned_on = day
        self._seen = {k: d for k, d in self._seen.items() if day - d < self.window_days}
        self._daily = {k: v for k, v in self._daily.items() if v[0] == day}

    def claim(self, account: str, symbol: str, asset_type: str, day: int) -> str:
        """"ok" (count it), "duplicate", "capped" or "full". Synchronous — no await
        between the check and the mark, so two concurrent requests cannot both count."""
        self._maybe_prune(day)
        pick = self._digest("pick", account, symbol, asset_type)
        last = self._seen.get(pick)
        if last is not None and day - last < self.window_days:
            return "duplicate"
        acct = self._digest("acct", account)
        acct_day, used = self._daily.get(acct, (day, 0))
        if acct_day != day:
            used = 0
        if used >= self.per_user_daily_cap:
            return "capped"
        if (last is None and len(self._seen) >= self.max_entries) or (
            acct not in self._daily and len(self._daily) >= self.max_entries
        ):
            return "full"
        self._seen[pick] = day
        self._daily[acct] = (day, used + 1)
        return "ok"


class SearchPickService:
    def __init__(self, dedup: Optional[_PickDedup] = None):
        self._dedup = dedup or _PickDedup()
        self._missing_logged = False

    async def record_pick(
        self,
        account: str,
        symbol: object,
        asset_type: object,
        *,
        today: Optional[date] = None,
    ) -> Outcome:
        """Count one search pick. Never raises; returns what happened (for tests and a
        debug log line — the endpoint answers 204 whatever it is)."""
        if not account:
            return "anonymous"
        try:
            day = today or datetime.now(ET).date()
            verdict = validate_pick(symbol, asset_type, stock_search_service.current_directory())
            if isinstance(verdict, str):
                logger.debug("search pick not counted: %s", verdict)
                return verdict
            sym, kind = verdict
            claim = self._dedup.claim(str(account), sym, pick_class(kind), day.toordinal())
            if claim != "ok":
                logger.debug("search pick not counted: %s", claim)
                return claim
        except Exception as e:  # noqa: BLE001 — a pick is a side effect; never fail the tap
            logger.warning("search pick rejected by an error (%s: %s)", type(e).__name__, e)
            return "error"

        try:
            await sb_exec(get_supabase().rpc("increment_search_pick", {
                "p_day": day.isoformat(),
                "p_ticker": sym,
                "p_asset_type": kind,
            }))
            return "counted"
        except Exception as e:  # noqa: BLE001
            # At most once: the claim stays, so a retry is a duplicate, not a second count.
            text = str(e)
            if "PGRST202" in text or "could not find the function" in text.lower():
                if not self._missing_logged:
                    self._missing_logged = True
                    logger.error(
                        "search picks: increment_search_pick is missing — migration 179 not "
                        "applied? Picks are dropped until it is."
                    )
            else:
                # No ticker on this line: beside the access log's IP and time it would link a
                # person to a pick.
                logger.warning("search pick write failed (%s: %s) — dropped", type(e).__name__, e)
            return "error"


_service: Optional[SearchPickService] = None


def get_search_pick_service() -> SearchPickService:
    global _service
    if _service is None:
        _service = SearchPickService()
    return _service
