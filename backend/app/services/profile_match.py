"""Match today's App-Exclusive Signals against a reader's stated interests.

PURE. No I/O, no LLM, no FMP. Given the already-computed global signals payload plus a
ticker→sector map the caller assembled from existing caches, it decides which signal rows
a given profile should hear about, and in what order.

WHY DETERMINISTIC, AND WHY IT MATTERS MORE HERE THAN ANYWHERE ELSE
------------------------------------------------------------------
Two independent reasons, and either alone would settle it.

1. Correctness. An LLM asked to "pick relevant tickers" can hallucinate a match, and the
   output is a push notification — the one surface with no undo, no context, and no way
   for the reader to see what it was derived from. A set intersection cannot invent a
   ticker that was not in the feed.

2. Regulatory posture. `SYSTEM_DESIGN_GUIDELINES` §11.7 records that FINRA and the SEC
   treat push as a supervised digital-engagement practice, so copy must be
   *informational, never directive*. Filtering content that is already computed
   identically for everyone is exactly the "impersonal" side of that line; generating a
   bespoke pitch per reader is not. Everything here narrows a shared list — it never
   composes a new claim.

The LLM's only possible future role is a one-line "why" per EVENT (not per reader), and
even that must be cached per event, never per user.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set

logger = logging.getLogger(__name__)

# Signal family → the `follow_signals` vocabulary entry that opts into it. `insiders` has
# no signal card of its own (insider flow is its own sender), so it is absent here rather
# than silently mapped onto something else.
SIGNAL_FAMILY_TO_FOLLOW: Dict[str, str] = {
    "whale": "whales",
    "congress": "congress",
    "earnings": "earnings",
}

# FMP sector strings → the `topics` vocabulary. FMP's sector list is small and stable, so
# an explicit map beats fuzzy matching; an unmapped sector simply yields no topic, which
# is the honest outcome (a wrong topic is worse than none).
_SECTOR_TO_TOPIC: Dict[str, str] = {
    "technology": "technology",
    "communication services": "technology",
    "energy": "energy",
    "utilities": "energy",
    "healthcare": "healthcare",
    "health care": "healthcare",
}

# Topics that describe a STYLE or an asset class rather than a sector. They cannot be
# derived from a company's sector, so a sector-based match never claims them — matching
# "dividends" would require a yield the signal row does not carry, and asserting it
# without one is exactly the kind of fabricated relevance this module exists to avoid.
_NON_SECTOR_TOPICS = frozenset({
    "value", "growth", "dividends", "crypto", "etf_index", "macro", "small_cap",
})


@dataclass(frozen=True)
class Match:
    """One signal row worth telling a specific reader about."""

    family: str          # "whale" | "congress" | "earnings"
    symbol: str
    company_name: str
    value: float         # polymorphic by family — see SignalRowResponse
    rank: int            # the row's rank within its card (1 = strongest)
    topic: Optional[str]  # the topic that matched, or None when the family alone did
    as_of_date: Optional[str]


def topic_for_sector(sector: Optional[str]) -> Optional[str]:
    """Map an FMP sector string to a `topics` value. PURE, and returns None freely."""
    if not isinstance(sector, str):
        return None
    return _SECTOR_TO_TOPIC.get(sector.strip().lower())


def topics_for_signal(symbol: str, sectors: Mapping[str, Optional[str]]) -> Set[str]:
    """The topic(s) a signal row can honestly claim, from its sector alone.

    Deliberately narrow: only sector-derivable topics. `sectors` is whatever the caller
    could assemble from EXISTING caches, so a miss is normal and must mean "no topic",
    never a guess.
    """
    if not isinstance(symbol, str) or not symbol:
        return set()
    topic = topic_for_sector(sectors.get(symbol.upper()))
    return {topic} if topic else set()


def _rows(group: Any) -> Sequence[Any]:
    """Entries of a signal group, whether it is a Pydantic model or a plain dict."""
    if group is None:
        return ()
    entries = getattr(group, "entries", None)
    if entries is None and isinstance(group, Mapping):
        entries = group.get("entries")
    return entries or ()


def _attr(row: Any, name: str, default=None):
    if isinstance(row, Mapping):
        return row.get(name, default)
    return getattr(row, name, default)


def _as_of(group: Any) -> Optional[str]:
    if isinstance(group, Mapping):
        return group.get("as_of_date")
    return getattr(group, "as_of_date", None)


def match_profile(
    profile: Mapping[str, Any],
    signals: Any,
    sectors: Mapping[str, Optional[str]],
    *,
    limit: int = 3,
) -> List[Match]:
    """Signal rows this reader opted into, best first. PURE — never mutates `signals`.

    ⚠️ `signals` is the SHARED, process-wide `signals_v3` cache object served to every
    user (`signals_service`). Mutating it here would corrupt the Home card for everyone,
    which is the same bug `redact_signals` carries a warning about. Everything below only
    reads.

    Two ways a row can qualify, and the distinction shapes the copy:
      * the reader follows that signal FAMILY (`follow_signals`) — a direct opt-in;
      * the row's sector maps to a topic the reader follows (`topics`).
    A row qualifying on both ranks above one qualifying on either.
    """
    if not isinstance(profile, Mapping):
        return []
    follows = {f for f in (profile.get("follow_signals") or []) if isinstance(f, str)}
    topics = {t for t in (profile.get("topics") or []) if isinstance(t, str)}
    # Style/asset-class topics cannot be sector-derived. Belt-and-braces rather than the
    # sole barrier: `topics_for_signal` only ever returns sector-derived topics, so the
    # intersection below already excludes these. Kept because it makes the early return
    # correct — a reader whose ONLY topic is "dividends" has nothing matchable, and
    # walking every signal row to rediscover that is wasted work — and because it states
    # the rule where a future editor of `_SECTOR_TO_TOPIC` will see it.
    sector_topics = topics - _NON_SECTOR_TOPICS
    if not follows and not sector_topics:
        return []

    scored: List[tuple] = []
    for family in ("congress", "whale", "earnings"):
        group = getattr(signals, family, None)
        if group is None and isinstance(signals, Mapping):
            group = signals.get(family)
        if group is None:
            continue
        family_followed = SIGNAL_FAMILY_TO_FOLLOW.get(family) in follows
        as_of = _as_of(group)

        for row in _rows(group):
            symbol = _attr(row, "symbol")
            if not isinstance(symbol, str) or not symbol.strip():
                continue
            symbol = symbol.strip().upper()
            matched_topics = topics_for_signal(symbol, sectors) & sector_topics
            topic = sorted(matched_topics)[0] if matched_topics else None
            if not family_followed and topic is None:
                continue

            rank = _attr(row, "rank", 99)
            rank = rank if isinstance(rank, int) and rank > 0 else 99
            value = _attr(row, "value", 0.0)
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = 0.0

            # Both reasons beats one; then the card's own ranking. Symbol last so the
            # order is total and stable — an unstable sort would make the "top" match
            # flip between runs for no reason the reader could perceive.
            strength = (1 if family_followed else 0) + (1 if topic else 0)
            scored.append((
                -strength, rank, symbol,
                Match(
                    family=family, symbol=symbol,
                    company_name=str(_attr(row, "name") or "") or symbol,
                    value=value, rank=rank, topic=topic, as_of_date=as_of,
                ),
            ))

    scored.sort(key=lambda item: item[:3])

    # DEDUPE BY SYMBOL, keeping the strongest occurrence. A ticker can legitimately
    # appear in two cards at once (Congress bought it AND funds are adding), and without
    # this the notification read "GOOGL is seeing congressional filings … Also active:
    # TSM, GOOGL" — the same ticker twice in one sentence. `limit` means N distinct
    # tickers, which is what a reader assumes it means. Sorted first, so the survivor is
    # the highest-ranked reason.
    cap = max(0, limit)
    out: List[Match] = []
    seen: Set[str] = set()
    for item in scored:
        # Checked BEFORE appending: the post-append form returned one row for limit=0,
        # because `len(out) >= 0` is true the moment anything is in the list.
        if len(out) >= cap:
            break
        match = item[3]
        if match.symbol in seen:
            continue
        seen.add(match.symbol)
        out.append(match)
    return out


def sectors_from_rows(rows: Iterable[Mapping[str, Any]]) -> Dict[str, Optional[str]]:
    """Build a ticker→sector map from already-cached rows. Never raises.

    Accepts both shapes the caller has to hand: `watchlist_items` (a flat `sector`
    column) and `company_profile_cache` (a `profile_json` blob). A ticker present in
    neither is simply absent, and `topics_for_signal` then claims no topic for it.
    """
    out: Dict[str, Optional[str]] = {}
    for row in rows or ():
        if not isinstance(row, Mapping):
            continue
        ticker = row.get("ticker") or row.get("symbol")
        if not isinstance(ticker, str) or not ticker.strip():
            continue
        sector = row.get("sector")
        if not sector:
            blob = row.get("profile_json")
            if isinstance(blob, Mapping):
                sector = blob.get("sector")
        if isinstance(sector, str) and sector.strip():
            out.setdefault(ticker.strip().upper(), sector.strip())
    return out
