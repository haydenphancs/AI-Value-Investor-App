"""Plan entitlements — what each tier is allowed, in one place.

This is the app's FIRST tier-based feature gate. Everything monetised before it was
metered in credits (`credit_service`), where the tier only sets the monthly allocation
and the gate itself is a balance check. So there is no existing `require_pro` dependency
to copy, and this module exists to make sure the second gate does not invent its own
vocabulary.

WHY A TABLE AND NOT AN `if tier == "pro"` AT THE CALL SITE
----------------------------------------------------------
The same three failures the notification registry was built to prevent apply here:

  1. **A limit with no upgrade path.** A gate that only answers "allowed / not allowed"
     cannot tell the client WHICH plan lifts it, so the UI has to hardcode a second copy
     of the ladder. `required_tier_for_more_tickers` returns it, so the client renders
     the upsell from the same source that enforced the limit.
  2. **Silent drift between the gate and the paywall.** A number duplicated in an
     endpoint and in a marketing string diverges on the first pricing change.
  3. **Unknown tiers falling open.** `users.tier` is a Postgres enum today, but the
     value also arrives from identity dicts that hardcode `"free"` and, in principle,
     from a newer app version. Anything unrecognised must resolve to the LEAST
     privileged tier, never the most.

PURE DATA + PURE FUNCTIONS. No I/O, no Supabase, no service imports — so it is trivially
unit-testable and safe to import from an endpoint, a service, or a source-scan guard.
"""

from __future__ import annotations

from typing import Iterable, List, Optional

# Tier keys as stored in `public.users.tier` (enum `public.user_tier`) and mirrored by the
# iOS `UserTier` enum. "premium" is displayed as "Max" — see
# `subscription_service.TIER_DISPLAY_NAMES`; this module deals in keys only so it can stay
# free of the catalog's Supabase reads.
TIER_FREE = "free"
TIER_PRO = "pro"
TIER_MAX = "premium"

# Least → most privileged. Drives `required_tier_for_more_tickers`.
TIER_ORDER = (TIER_FREE, TIER_PRO, TIER_MAX)

# Anything not in TIER_ORDER resolves here. Falling open on an unrecognised tier would
# hand the paid surface to every guest and to every degraded identity dict.
DEFAULT_TIER = TIER_FREE

# ── Updates screen: how many ticker chips beside the always-free Market pill ─────────
#
# These are a PACKAGING decision, not a cost recovery one, and the distinction matters
# for anyone retuning them. The Updates AI insight cards are produced by a GLOBAL
# background sweeper over the top-200 most-watched tickers across all users
# (`updates_insight_sweeper._universe`), written to a SHARED cache, and hard-capped at
# `_GLOBAL_DAILY_CAP` generations per day. The read path (`GET /updates/tabs`,
# `/updates/feed`) never calls Gemini, and `/tabs` costs exactly one batch-quote call
# regardless of how many chips it returns. So one user showing 30 chips instead of 1
# costs essentially nothing extra — raising `_MAX_UNIVERSE` or `_GLOBAL_DAILY_CAP` is
# what costs money, and neither is per-user.
#
# Free is deliberately 1 rather than 0: the tab has to demonstrate what it does. Per-
# ticker news itself stays free on every ticker's detail News tab, so what is gated here
# is the aggregated feed and the AI insight card, not the content — which is what keeps
# App Store Guideline 5.1.1(v) reasoning intact (see auth.md §1a).
UPDATES_TICKER_LIMITS = {
    TIER_FREE: 1,
    TIER_PRO: 15,
    TIER_MAX: 30,
}

# ── Home screen: App-Exclusive Signals (the CAYDEX-badged card) ──────────────────────
#
# A FLOOR, not the next-rung ladder `required_tier_for_more_tickers` walks: every paid
# tier sees the same thing, so a free user is always pointed at Pro — the cheapest plan
# that unlocks — rather than at "whatever is one step up".
#
# What is gated is the TICKER, not the card. All three rows stay on screen with their
# icon, title, description and aggregate stat ("3 members buying"); only the symbol is
# withheld, and the drill-down leaders with it. That keeps the same 5.1.1(v) posture as
# the Updates limit above — the screen still demonstrates what it does, and the
# underlying holder data remains free on every ticker's own Holders tab.
SIGNALS_UNLOCKED_TIERS = frozenset({TIER_PRO, TIER_MAX})

# ── Whales: how many investors an account may TRACK ──────────────────────────────────
#
# ``None`` means unlimited (Max). A dict lookup that can legitimately return None is why
# this is NOT modelled on UPDATES_TICKER_LIMITS' plain-int table — callers must branch on
# None rather than compare against a sentinel like -1, which reads as a real limit.
#
# Browsing is deliberately NOT limited: all 53 whales stay listed, searchable and openable
# on every tier. What Free gets is one TRACKED whale, and the roster keeps demonstrating
# what tracking would do — the same 5.1.1(v) posture as the two gates above.
WHALE_FOLLOW_LIMITS = {
    TIER_FREE: 1,
    TIER_PRO: 10,
    TIER_MAX: None,
}

# Free's single slot is PRE-ASSIGNED rather than user-chosen, so every free account sees
# the same worked example and the marketing copy can name it. Matched case-insensitively
# against `whales.name`; resolution to a uuid lives in whale_service (this module stays
# I/O-free), which is why changing the free whale is a one-line edit with no migration.
FREE_TIER_WHALE_NAME = "Bill Gates"

# ── Whales: which profile sections are paid ──────────────────────────────────────────
#
# Same floor as signals, and deliberately the same frozenset so the two cannot drift into
# "Pro unlocks signals but only Max unlocks whales".
#
# FREE keeps: header, bio, risk badge, portfolio stats, and Sector Exposure — enough to
# judge an investor and to see the shape of their book.
# PAID:       Current Picks (holdings + behaviour summary), Recent Trades (trade groups and
#             their drill-down), and the AI Sentiment Summary — the position-level detail.
WHALE_DETAIL_UNLOCKED_TIERS = SIGNALS_UNLOCKED_TIERS


def normalize_tier(tier: Optional[str]) -> str:
    """Pure: map any incoming tier value onto a known key, defaulting to the least privileged."""
    if not isinstance(tier, str):
        return DEFAULT_TIER
    key = tier.strip().lower()
    # Against TIER_ORDER, not one gate's limit table — there is more than one gate now,
    # and normalisation must not depend on which of them happens to list every tier.
    return key if key in TIER_ORDER else DEFAULT_TIER


def updates_ticker_limit(tier: Optional[str]) -> int:
    """Pure: how many watchlist chips this tier may see on the Updates screen."""
    return UPDATES_TICKER_LIMITS[normalize_tier(tier)]


def required_tier_for_more_tickers(tier: Optional[str]) -> Optional[str]:
    """Pure: the next tier up, or None when already on the most privileged one.

    Returned to the client so the upsell chip names the same plan the server enforced,
    instead of the view hardcoding a second copy of the ladder.
    """
    current = normalize_tier(tier)
    index = TIER_ORDER.index(current)
    if index + 1 >= len(TIER_ORDER):
        return None
    return TIER_ORDER[index + 1]


def signals_unlocked(tier: Optional[str]) -> bool:
    """Pure: may this tier see the App-Exclusive Signals tickers?

    False for Free, for guests (whose identity dict hardcodes ``"free"``), and for any
    unrecognised value — `normalize_tier` guarantees the unknown case degrades to the
    least privileged tier rather than falling open on the paid surface.
    """
    return normalize_tier(tier) in SIGNALS_UNLOCKED_TIERS


def required_tier_for_signals(tier: Optional[str]) -> Optional[str]:
    """Pure: the plan that unlocks the signals tickers, or None when already unlocked.

    Always Pro for a locked caller — see SIGNALS_UNLOCKED_TIERS on why this is a floor
    and not a ladder walk. Returned on the wire so the paywall names the same plan the
    server enforced.
    """
    return None if signals_unlocked(tier) else TIER_PRO


def whale_follow_limit(tier: Optional[str]) -> Optional[int]:
    """Pure: how many whales this tier may track. ``None`` means unlimited.

    Callers MUST branch on None rather than comparing it — ``count >= None`` raises, and a
    sentinel int would eventually be enforced as a real cap on the tier that has none.
    """
    return WHALE_FOLLOW_LIMITS[normalize_tier(tier)]


def whale_detail_unlocked(tier: Optional[str]) -> bool:
    """Pure: may this tier see a whale's holdings, trade groups and sentiment summary?

    False for Free, for guests (whose identity dict hardcodes ``"free"``), and for anything
    unrecognised — the unknown case must fall CLOSED onto the paid surface.
    """
    return normalize_tier(tier) in WHALE_DETAIL_UNLOCKED_TIERS


def required_tier_for_whales(tier: Optional[str]) -> Optional[str]:
    """Pure: the plan that unlocks whale detail + more follows, or None if already unlocked.

    A floor (always Pro), not a ladder walk: Pro already unlocks every gated section, so
    walking one rung would upsell Max to a Pro user who is not locked out of anything.
    """
    return None if whale_detail_unlocked(tier) else TIER_PRO


def is_free_tier_whale(name: Optional[str]) -> bool:
    """Pure: is this the one whale a Free account may track?

    Case- and whitespace-insensitive because the comparison is against a display name that
    reaches us from a JSON registry and a Postgres column, not an id.
    """
    if not isinstance(name, str):
        return False
    return name.strip().casefold() == FREE_TIER_WHALE_NAME.casefold()


def select_visible_tickers(tickers: Iterable[str], limit: int) -> List[str]:
    """Pure: the first ``limit`` tickers in case-insensitive alphabetical order.

    Alphabetical rather than "the user's own group order" so the choice is DETERMINISTIC
    and explainable in one sentence: given BDU, DIF, BDX a one-chip plan shows BDU. Group
    order is user-mutable and would make the visible chip change for reasons the user did
    not connect to their plan.

    The trade-off to know about: adding a ticker that sorts earlier swaps which chip a
    Free user sees. That is the price of determinism, and it is why the rule is stated in
    the UI copy rather than left implicit.

    Blank/whitespace-only entries are dropped and duplicates collapse case-insensitively,
    so a limit of N always yields at most N DISTINCT chips. Returns [] for a non-positive
    limit rather than raising — an unknown tier must degrade, not 500 the tab bar.
    """
    if limit <= 0:
        return []

    seen: set[str] = set()
    cleaned: List[str] = []
    for raw in tickers or []:
        if not isinstance(raw, str):
            continue
        ticker = raw.strip()
        if not ticker:
            continue
        key = ticker.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(ticker)

    # Secondary key on the raw string so two spellings that casefold identically (already
    # deduped above, but a Unicode fold can still tie two distinct strings) order stably
    # instead of depending on Python's sort landing.
    cleaned.sort(key=lambda t: (t.casefold(), t))
    return cleaned[:limit]
