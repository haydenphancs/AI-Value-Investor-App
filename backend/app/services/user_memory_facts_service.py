"""What a reader has been asking about, across chat sessions.

Rung 2 of the memory ladder (rung 1 = the per-session rolling summary, migration 130).

⚠️ ZERO LLM, AND THAT IS THE DESIGN, NOT A COMPROMISE.
The original plan called for a flash-lite pass to extract facts from the user's messages.
Both facts worth keeping are already computed on every turn for free:

  * the question THEME — `chat_router.route_question` classifies every turn to pick a
    specialist lens, and that classification was being used for the prompt and thrown away;
  * the TICKER — the session already carries `stock_id` / the resolved reference.

Deriving them instead of extracting them removes the extraction cost, the extraction
latency, and — most importantly — an injection surface. A summariser reading the user's
own words would produce free prose, and free prose cannot be injected UNFENCED into the
system instruction. Both values here come from closed vocabularies, so the rendered block
is trusted the same way the preference block is.

⚠️ A FACT DERIVED FROM USER INPUT IS UNTRUSTED UNLESS ITS VOCABULARY IS CLOSED. If a
future fact type needs free text ("the user said they hold AAPL"), it must be fenced and
live in the volatile layer — never in this table and never in the per-user prompt layer.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from app.database import get_supabase
from app.services.agents.chat_specialists import SPECIALIST_KEYS
from app.utils.supabase_errors import is_unique_violation

logger = logging.getLogger(__name__)

TABLE = "user_memory_facts"

FACT_TICKER = "ticker_discussed"
FACT_THEME = "question_theme"

# Tickers as FMP renders them: letters plus the class/exchange punctuation (BRK-B, BF.B).
# Anchored and length-bounded so nothing else can pass as a symbol.
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")

# `general` is the router's fallback — it means "we could not classify this", so storing
# it would fill the table with the absence of information.
_MEANINGFUL_THEMES = frozenset(SPECIALIST_KEYS) - {"general"}

# Rows kept per user. Small on purpose: this is rendered into every turn's prompt, so the
# table is a working set, not a history. LRU-evicted rather than TTL'd — the value of a
# fact is that it is recent.
MAX_FACTS_PER_USER = 40

# How many of EACH kind reach the prompt.
#
# Per-key, not one shared budget: a single `LIMIT 8` over a recency-ordered window let a
# burst of ticker rows crowd out `question_theme` completely, so the "Usually asks about"
# line silently disappeared for the most active readers — the ones it describes best.
RENDER_LIMITS: Dict[str, int] = {FACT_TICKER: 6, FACT_THEME: 3}


def _valid_ticker(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper()
    return candidate if _TICKER_RE.match(candidate) else None


def _valid_theme(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    return candidate if candidate in _MEANINGFUL_THEMES else None


# key → validator. The CHECK constraint in migration 132 mirrors these keys; a new entry
# needs the migration, a validator, and a renderer line, or the value cannot reach a prompt.
FACT_VALIDATORS = {
    FACT_TICKER: _valid_ticker,
    FACT_THEME: _valid_theme,
}


def sanitize_facts(pairs: Iterable[Tuple[str, Any]]) -> List[Tuple[str, str]]:
    """Keep only (key, value) pairs whose value passes its key's validator. NEVER raises.

    The single choke point between "something we observed" and "something that may reach
    a system prompt". Deduped, order preserved.
    """
    out: List[Tuple[str, str]] = []
    seen = set()
    # `pairs` is caller-supplied and this function promises never to raise: a non-iterable
    # (an int, None) would otherwise blow up on the `for`, on a path whose whole contract
    # is that it degrades quietly.
    # Honour the `Iterable` in the signature. The old guard whitelisted four concrete
    # types, so a generator, `dict.items()`, `zip` or `map` — every one of them a valid
    # `Iterable[Tuple[str, Any]]` — fell straight through to an empty result with NO log,
    # indistinguishable from "nothing to record". A trap for the next caller rather than a
    # live bug, since the one production call site builds a list.
    try:
        pairs = list(pairs)
    except TypeError:
        return out
    for pair in pairs:
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        key, raw = pair
        validator = FACT_VALIDATORS.get(key)
        if validator is None:
            continue
        value = validator(raw)
        if value is None or (key, value) in seen:
            continue
        seen.add((key, value))
        out.append((key, value))
    return out


class UserMemoryFactsService:
    def __init__(self, supabase=None):
        self.supabase = supabase or get_supabase()

    def record(self, user_id: str, pairs: Iterable[Tuple[str, Any]]) -> int:
        """Upsert observed facts, bumping `hit_count`. Best-effort — NEVER raises.

        Called off the answer path: a failure here costs a little memory quality and must
        never surface to the reader or fail a turn that was already delivered.
        """
        facts = sanitize_facts(pairs)
        if not user_id or not facts:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        written = 0
        for key, value in facts:
            try:
                existing = (
                    self.supabase.table(TABLE)
                    .select("id, hit_count")
                    .eq("user_id", user_id).eq("fact_key", key).eq("fact_value", value)
                    .execute()
                ).data or []
                if existing:
                    row = existing[0]
                    self.supabase.table(TABLE).update({
                        "hit_count": int(row.get("hit_count") or 1) + 1,
                        "updated_at": now,
                    }).eq("id", row["id"]).execute()
                else:
                    self.supabase.table(TABLE).insert({
                        "user_id": user_id, "fact_key": key, "fact_value": value,
                        "hit_count": 1, "updated_at": now,
                    }).execute()
                written += 1
            except Exception as e:
                # A 23505 here is a benign RACE, not a failure: two turns for the same
                # reader both read "absent" and both inserted, and the loser's fact is
                # already stored by the winner. Adopting it keeps the log meaningful —
                # warning on every concurrent turn would bury the writes that really did
                # fail. Never retried: the row exists, so a retry would just lose again.
                if is_unique_violation(e):
                    written += 1
                    continue
                logger.warning(
                    "memory fact write failed user=%s key=%s (%s: %s)",
                    user_id, key, type(e).__name__, e,
                )
        if written:
            self._evict(user_id)
        return written

    def _evict(self, user_id: str) -> None:
        """Keep the newest `MAX_FACTS_PER_USER` rows. Best-effort, never raises.

        LRU by `updated_at`, so a ticker the reader keeps returning to survives and a
        one-off mention ages out. Without this the per-turn read degrades from an indexed
        lookup into a growing scan.
        """
        try:
            rows = (
                self.supabase.table(TABLE)
                .select("id, updated_at")
                .eq("user_id", user_id)
                .order("updated_at", desc=True)
                .execute()
            ).data or []
            doomed = rows[MAX_FACTS_PER_USER:]
            stale = [r["id"] for r in doomed if r.get("id")]
            if stale:
                # SELF-GUARDING DELETE, for two independent reasons.
                #
                # `updated_at` bound: the id list comes from a snapshot taken before this
                # statement runs. A concurrent turn can `record()` one of those exact rows
                # in the gap, bumping it to NEWEST — and it would still be deleted, so the
                # fact the reader is actively asking about is the one evicted. Re-checking
                # the timestamp makes a row that was touched in the interim survive.
                #
                # `user_id` filter: `stale` is derived from a user-filtered select today,
                # so an unfiltered `in_("id", ...)` is safe only by construction. One edit
                # upstream and this becomes a cross-user delete; the filter costs nothing.
                cutoff = doomed[0].get("updated_at")
                q = (
                    self.supabase.table(TABLE).delete()
                    .eq("user_id", user_id).in_("id", stale)
                )
                if cutoff:
                    q = q.lte("updated_at", cutoff)
                q.execute()
        except Exception as e:
            logger.warning(
                "memory fact eviction failed for user=%s (%s: %s)",
                user_id, type(e).__name__, e,
            )

    def top_facts(self, user_id: str) -> Dict[str, List[str]]:
        """Most recent facts, grouped and capped PER KIND. NEVER raises → {}.

        A plain indexed select, deliberately: at this size a vector search would return
        the same rows, minus a network round trip and an embedding call.

        Reads the user's whole (capped) working set rather than a small LIMIT, then caps
        each kind separately — one shared limit let tickers crowd themes out entirely.
        Bounded by construction: `_evict` holds each user to MAX_FACTS_PER_USER rows, so
        this is a fixed-size indexed read, not a growing one.
        """
        if not user_id:
            return {}
        try:
            rows = (
                self.supabase.table(TABLE)
                .select("fact_key, fact_value, hit_count")
                .eq("user_id", user_id)
                .order("updated_at", desc=True)
                .limit(MAX_FACTS_PER_USER)
                .execute()
            ).data or []
        except Exception as e:
            logger.warning(
                "memory fact read failed for user=%s (%s: %s) — answering without memory",
                user_id, type(e).__name__, e,
            )
            return {}
        return group_facts(rows, RENDER_LIMITS)


def group_facts(
    rows: Sequence[Any], limits: Optional[Dict[str, int]] = None,
) -> Dict[str, List[str]]:
    """Rows → {fact_key: [value, ...]}. PURE, never raises.

    Re-validates on the way OUT as well as in. A row could predate a vocabulary change,
    and this is the last gate before the value reaches a system prompt — the read path is
    exactly where a stale value would otherwise slip through.
    """
    out: Dict[str, List[str]] = {}
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        key = row.get("fact_key")
        validator = FACT_VALIDATORS.get(key)
        if validator is None:
            continue
        value = validator(row.get("fact_value"))
        if value is None:
            continue
        bucket = out.setdefault(key, [])
        cap = (limits or {}).get(key)
        if value not in bucket and (cap is None or len(bucket) < cap):
            bucket.append(value)
    return out


_service: Optional[UserMemoryFactsService] = None


def get_user_memory_facts_service() -> UserMemoryFactsService:
    global _service
    if _service is None:
        _service = UserMemoryFactsService()
    return _service
