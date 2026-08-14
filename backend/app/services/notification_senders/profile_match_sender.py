"""Alert readers when today's signals land in a topic or feed they follow.

ZERO LLM AND ZERO NEW UPSTREAM CALLS. The signal rows come from the same global
`signals_v3` payload the Home card already serves, and the sectors come from caches other
features already populated (`watchlist_items`, `company_profile_cache`). The matching
itself is a pure set intersection in `services/profile_match.py`.

That is a deliberate design choice, not a shortcut — see that module's header for the
correctness and regulatory reasons. Copy is templated for the same reason: §11.7 of
SYSTEM_DESIGN_GUIDELINES requires push copy to be *informational, never directive*, and
a template is auditable in one place where generated prose is not.

⚠️ THE TIER GATE HERE IS LEAK PREVENTION, NOT PACKAGING.
`signals_service.redact_signals` masks the ticker symbols on the Home card for Free
users, because the tickers ARE the paid product. A notification naming those tickers
would hand a Free user exactly what the paywall hides — so the audience is filtered by
`signals_unlocked` before anything is sent. It lives in this sender rather than in the
dispatch ladder because that ladder is a single-responsibility
who/whether/how-many/when/once chain, and a tier check belongs to the sender that knows
why it matters.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence

from app.database import get_supabase
from app.services.entitlements import signals_unlocked
from app.services.notification_jobs import JOB_PROFILE_MATCH, claimed_job
from app.services.notification_kinds import KIND_PROFILE_MATCH
from app.services.profile_match import Match, match_profile, sectors_from_rows

logger = logging.getLogger(__name__)

# One notification per reader per run, and the category cap is 1/day on top. This is a
# derived alert; a digest of one is the honest size.
_MAX_MATCHES = 3

# Ceiling on the audience scanned in a single pass. A backstop against a runaway table,
# not a product limit — logged when it bites, per the "no silent caps" rule.
_MAX_PROFILES = 5000

_FAMILY_LABEL = {
    "whale": "institutional buying",
    "congress": "congressional filings",
    "earnings": "earnings surprises",
}

_TOPIC_LABEL = {
    "technology": "Technology",
    "energy": "Energy",
    "healthcare": "Healthcare",
}


def _title(match: Match) -> str:
    """Informational. Names WHAT happened and WHERE, never what to do about it."""
    if match.topic:
        return f"{_TOPIC_LABEL.get(match.topic, match.topic.title())} activity"
    return _FAMILY_LABEL.get(match.family, "New signal").capitalize()


def _body(matches: Sequence[Match]) -> str:
    """Describes the feed, never the merit of owning anything.

    Deliberately avoids "for you", "matches", "fits" and "pick": those are the phrases
    `chat_guardrails._SUITABILITY_PATTERNS` exists to catch on the chat surface, and a
    notification is a worse place to say them — there is no room for the qualification
    that would make them accurate.
    """
    lead = matches[0]
    label = _FAMILY_LABEL.get(lead.family, "activity")
    if lead.topic:
        topic = _TOPIC_LABEL.get(lead.topic, lead.topic.title())
        head = f"{lead.symbol} is seeing {label} in {topic}, a topic you follow."
    else:
        head = f"{lead.symbol} is seeing {label}, which you follow."
    if len(matches) > 1:
        others = ", ".join(m.symbol for m in matches[1:])
        return f"{head} Also active: {others}."
    return head


def _load_profiles() -> List[Dict[str, Any]]:
    """Readers who CONSENTED and stated something to match on. Never raises → [].

    ⚠️ `consented_at` is not optional here. This function's docstring said "opted in" while
    the query selected only `user_id, topics, follow_signals` — consent was never fetched, so
    a profile captured during onboarding (which never grants consent: `OnboardingViewModel`
    leaves `accepted_personalization_terms` nil) drove a personalized push saying "…a topic
    you follow" to someone who had not accepted the personalization terms.

    Every other consumer of this table already refuses such a row — `may_apply_profile`
    returns False on a null `consented_at`, and `_profile_response` reports `applied=False`.
    Migration 131 states the invariant flatly: "NULL until they do; the feature must not
    apply a profile captured before the disclosure existed." Push is the surface where
    getting it wrong is least recoverable, because the notification is already delivered.

    Filtered in SQL rather than in Python so the row cap and the tier read below are spent
    on candidates that can actually be notified.
    """
    try:
        res = (
            get_supabase().table("user_investor_profile")
            .select("user_id, topics, follow_signals, consented_at")
            .not_.is_("consented_at", "null")
            # Deterministic order: an unordered LIMIT returns an arbitrary but STABLE
            # subset (physical heap order), so once the table exceeds the cap the same
            # readers were excluded on every daily run, forever, while the log below
            # implied a transient miss.
            .order("user_id")
            .limit(_MAX_PROFILES)
            .execute()
        )
        rows = res.data or []
    except Exception as e:
        logger.warning(
            "profile match: profile read failed (%s: %s) — no sends this pass",
            type(e).__name__, e,
        )
        return []
    if len(rows) >= _MAX_PROFILES:
        logger.warning(
            "profile match: hit the %d-profile scan ceiling — some readers were not "
            "considered this pass", _MAX_PROFILES,
        )
    # Belt and braces on the SQL filter: a row whose consent was revoked between the read
    # and the send is still refused, and the check cannot be lost by a future query edit.
    return [
        r for r in rows
        if r.get("consented_at") and (r.get("topics") or r.get("follow_signals"))
    ]


def _tiers_for(user_ids: Sequence[str]) -> Dict[str, str]:
    """user_id → tier, in ONE batched read. Missing → "free" (falls closed)."""
    if not user_ids:
        return {}
    try:
        res = (
            get_supabase().table("users")
            .select("id, tier").in_("id", list(user_ids)).execute()
        )
        return {r["id"]: (r.get("tier") or "free") for r in (res.data or []) if r.get("id")}
    except Exception as e:
        logger.warning(
            "profile match: tier read failed (%s: %s) — treating everyone as free, so "
            "nothing is sent rather than risking a paid-ticker leak",
            type(e).__name__, e,
        )
        return {}


def _sector_map(symbols: Sequence[str]) -> Dict[str, Optional[str]]:
    """ticker → sector from EXISTING caches only. Never raises → {}.

    Two sources, both already populated by other features, so this costs no FMP call:
    `company_profile_cache` (written whenever anyone opens a stock) and `watchlist_items`
    (written when anyone tracks one). A ticker in neither simply has no sector, and
    `topics_for_signal` then claims no topic for it — an honest miss beats a guess.
    """
    if not symbols:
        return {}
    rows: List[Mapping[str, Any]] = []
    sb = get_supabase()
    for table, columns in (
        ("company_profile_cache", "ticker, profile_json"),
        ("watchlist_items", "ticker, sector"),
    ):
        try:
            res = sb.table(table).select(columns).in_("ticker", list(symbols)).execute()
            rows.extend(res.data or [])
        except Exception as e:
            logger.warning(
                "profile match: sector lookup on %s failed (%s: %s) — continuing with "
                "fewer sectors", table, type(e).__name__, e,
            )
    return sectors_from_rows(rows)


def _all_symbols(signals: Any) -> List[str]:
    out: List[str] = []
    for family in ("congress", "whale", "earnings"):
        group = getattr(signals, family, None)
        if group is None and isinstance(signals, Mapping):
            group = signals.get(family)
        entries = getattr(group, "entries", None) if group is not None else None
        if entries is None and isinstance(group, Mapping):
            entries = group.get("entries")
        for row in entries or ():
            symbol = row.get("symbol") if isinstance(row, Mapping) else getattr(row, "symbol", None)
            if isinstance(symbol, str) and symbol.strip():
                out.append(symbol.strip().upper())
    return sorted(set(out))


async def run_profile_match_notifications(now: Optional[datetime] = None) -> int:
    """One claimed pass. Returns how many notifications were delivered."""
    now = now or datetime.now(timezone.utc)

    async with claimed_job(JOB_PROFILE_MATCH) as run:
        if run is None:
            logger.debug("profile match: not claimed (already run today, or held)")
            return 0

        from app.services.signals_service import get_signals_service

        # The SHARED cached payload every Home card is served from. Read-only —
        # `redact_signals` is deliberately NOT called: it exists to mask tickers for
        # Free users, and this audience is already tier-filtered, so applying it would
        # blank the very symbols the notification is about.
        signals = await get_signals_service().get_signals()
        symbols = _all_symbols(signals)
        if not symbols:
            logger.info("profile match: no signal rows today — nothing to match")
            run.success = True
            return 0

        profiles = await asyncio.to_thread(_load_profiles)
        if not profiles:
            run.success = True
            return 0

        tiers = await asyncio.to_thread(_tiers_for, [p["user_id"] for p in profiles if p.get("user_id")])
        sectors = await asyncio.to_thread(_sector_map, symbols)

        # Imported here, not at module scope: push_dispatch_service pulls in the
        # notification stack and importing it at load time risks a cycle.
        from app.services.push_dispatch_service import (
            get_push_dispatch_service,
            trading_date_et,
        )

        dispatcher = get_push_dispatch_service()
        # One market-wide bucket per ET trading day: the signals payload is the same for
        # everyone, so two readers matching the same day share the dedup date even in
        # different timezones (the "three clocks" rule — dedup is a property of the
        # market, caps are a property of the user).
        day = trading_date_et()
        sent = 0

        for profile in profiles:
            user_id = profile.get("user_id")
            if not user_id:
                continue
            # Leak prevention, not packaging — see the module header.
            if not signals_unlocked(tiers.get(user_id, "free")):
                continue
            matches = match_profile(profile, signals, sectors, limit=_MAX_MATCHES)
            if not matches:
                continue
            lead = matches[0]
            sent += await dispatcher.notify_users(
                [user_id],
                kind=KIND_PROFILE_MATCH,
                title=_title(lead),
                body=_body(matches),
                # Per user per day: the matched set is derived from their own profile,
                # so a shared key would let the first reader's send suppress everyone's.
                dedup_key=f"{KIND_PROFILE_MATCH}:{day}:{user_id}",
                route={"kind": "ticker", "ticker": lead.symbol},
                now=now,
            )

        run.notified = sent
        run.success = True

    logger.info("profile match notifications: %d send(s)", sent)
    return sent
