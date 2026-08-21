"""
Whale Service — Dual-source aggregation engine for the Whales tab.

Routes institutional funds to FMP 13F endpoints and politicians to
FMP Congressional Trading endpoints, then normalizes both into a
unified response model for the Swift frontend.

Design:
- Three-tier caching: in-memory TTL → Supabase snapshots → FMP origin.
- All external calls run concurrently via asyncio.gather.
- Each section degrades gracefully on failure.
- 13F data changes quarterly; congressional data monthly.
"""

import asyncio
import hashlib
import json
import math
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any, Tuple
import logging

from app.integrations.fmp import get_fmp_client, FMPClient
from app.database import get_supabase
from app.utils.period_labels import filing_period_display
from app.services._whale_common import (
    parse_congress_amount_dollars,
    parse_congress_amount_bounds,
    sum_amount_bounds,
    format_amount_range,
    format_amount_short,
    resolve_congress_action,
    compute_activity,
    Activity,
    ACTIVITY_UNKNOWN,
    snapshot_db_row,
    calc_13f_trade_dollars,
    AnnualReturn,
    compute_13f_cagr,
    compute_ticker_cagr,
    return_label_for,
    unavailable_return_label,
    RETURN_OK,
    RETURN_INSUFFICIENT,
    RETURN_UNAVAILABLE,
)
from app.services.entitlements import (
    FREE_TIER_WHALE_NAME,
    TIER_FREE,
    TIER_PRO,
    is_free_tier_whale,
    normalize_tier,
    required_tier_for_whales,
    whale_detail_unlocked,
    whale_follow_limit,
)
from app.schemas.whale import (
    TrendingWhaleResponse,
    WhaleProfileResponse,
    WhaleHoldingResponse,
    WhaleTradeGroupResponse,
    WhaleTradeResponse,
    WhaleSectorAllocationResponse,
    WhaleBehaviorSummaryResponse,
    WhaleTradeGroupActivityResponse,
    WhaleAlertBannerResponse,
    FollowResponse,
)

logger = logging.getLogger(__name__)


# ── Constants ────────────────────────────────────────────────────────

SECTOR_COLORS: Dict[str, str] = {
    "Technology": "3B82F6",
    "Financial Services": "22C55E",
    "Healthcare": "EF4444",
    "Energy": "F97316",
    "Consumer Cyclical": "8B5CF6",
    "Industrials": "6366F1",
    "Communication Services": "EC4899",
    "Consumer Defensive": "14B8A6",
    "Real Estate": "F59E0B",
    "Utilities": "6B7280",
    "Basic Materials": "A78BFA",
}
DEFAULT_SECTOR_COLOR = "6B7280"

# ── SIC Industry Code → GICS Sector Mapping ────────────────────────
# FMP returns granular SEC SIC industry codes (e.g. "ELECTRONIC COMPUTERS").
# We map them to the 11 standard GICS sectors for clean display.
# Keys MUST be lowercase for case-insensitive lookup.

SIC_TO_SECTOR: Dict[str, str] = {
    # Technology
    "electronic computers": "Technology",
    "services-prepackaged software": "Technology",
    "services-computer programming, data processing, etc.": "Technology",
    "computer peripheral equipment, nec": "Technology",
    "computer communications equipment": "Technology",
    "electronic components, nec": "Technology",
    "printed circuit boards": "Technology",
    "services-computer integrated systems design": "Technology",
    "services-computer processing & data preparation": "Technology",
    "services-computer rental & leasing": "Technology",
    "calculating & accounting machines": "Technology",
    "computer storage devices": "Technology",
    "semiconductors & related devices": "Technology",
    "electronic connectors": "Technology",
    "search, detection, navigation, guidance systems": "Technology",
    "measuring & controlling devices, nec": "Technology",
    # Financial Services
    "finance services": "Financial Services",
    "finance-services": "Financial Services",
    "national commercial banks": "Financial Services",
    "state chartered banks": "Financial Services",
    "state commercial banks-federal reserve members": "Financial Services",
    "security brokers, dealers & flotation companies": "Financial Services",
    "insurance carriers, nec": "Financial Services",
    "fire, marine & casualty insurance": "Financial Services",
    "life insurance": "Financial Services",
    "accident & health insurance": "Financial Services",
    "investment advice": "Financial Services",
    "services-management consulting services": "Financial Services",
    "security & commodity services": "Financial Services",
    "savings institutions, federally chartered": "Financial Services",
    "savings institution, federally chartered-no": "Financial Services",
    "short-term business credit institutions": "Financial Services",
    "functions related to depository banking": "Financial Services",
    "blank checks": "Financial Services",
    "investors, nec": "Financial Services",
    "services-misc business services nec": "Financial Services",
    # Healthcare
    "pharmaceutical preparations": "Healthcare",
    "surgical & medical instruments & apparatus": "Healthcare",
    "biological products, (no diagnostic substances)": "Healthcare",
    "services-health services": "Healthcare",
    "services-medical laboratories": "Healthcare",
    "electromedical & electrotherapeutic apparatus": "Healthcare",
    "in vitro & in vivo diagnostic substances": "Healthcare",
    "hospital & medical service plans": "Healthcare",
    "orthopedic, prosthetic & surgical appliances": "Healthcare",
    "medicinal chemicals & botanical products": "Healthcare",
    "medical instruments & supplies": "Healthcare",
    # Energy
    "petroleum refining": "Energy",
    "crude petroleum & natural gas": "Energy",
    "natural gas distribution": "Energy",
    "electric & other services combined": "Energy",
    "electric services": "Energy",
    "natural gas transmission & distribution": "Energy",
    "pipeline companies": "Energy",
    "petroleum & petroleum products wholesalers": "Energy",
    # Consumer Cyclical
    "retail-catalog & mail-order houses": "Consumer Cyclical",
    "retail-eating places": "Consumer Cyclical",
    "retail-auto dealers & gas stations": "Consumer Cyclical",
    "retail-variety stores": "Consumer Cyclical",
    "retail-department stores": "Consumer Cyclical",
    "retail-building materials, hardware": "Consumer Cyclical",
    "retail-home furniture, furnishings & equipment": "Consumer Cyclical",
    "motor vehicles & passenger car bodies": "Consumer Cyclical",
    "services-miscellaneous amusement & recreation": "Consumer Cyclical",
    "services-hotels, rooming houses, camps": "Consumer Cyclical",
    "motor vehicle parts & accessories": "Consumer Cyclical",
    "footwear, (no rubber)": "Consumer Cyclical",
    "apparel & other finished prods of fabrics": "Consumer Cyclical",
    "retail-family clothing stores": "Consumer Cyclical",
    "retail-drug stores and proprietary stores": "Consumer Cyclical",
    "retail-retail stores, nec": "Consumer Cyclical",
    "retail-lumber & other building materials dealers": "Consumer Cyclical",
    "retail-nonstore retailers": "Consumer Cyclical",
    # Industrials
    "railroads, line-haul operating": "Industrials",
    "air transportation, scheduled": "Industrials",
    "trucking & courier services (no air)": "Industrials",
    "construction-special trade contractors": "Industrials",
    "industrial & commercial machinery, nec": "Industrials",
    "general industrial machinery & equipment": "Industrials",
    "farm machinery & equipment": "Industrials",
    "misc industrial & commercial machinery": "Industrials",
    "aerospace product & parts": "Industrials",
    "services-engineering services": "Industrials",
    "services-detective, guard & armored car services": "Industrials",
    "services-equipment rental & leasing": "Industrials",
    "services-staffing services": "Industrials",
    "special industry machinery, nec": "Industrials",
    "heavy construction other than bldg const-contractors": "Industrials",
    "general bldg contractors-residential bldgs": "Industrials",
    "construction machinery & equip": "Industrials",
    "transportation services": "Industrials",
    # Communication Services
    "services-advertising": "Communication Services",
    "cable & other pay television services": "Communication Services",
    "telephone & telegraph apparatus": "Communication Services",
    "telephone communications (no radiotelephone)": "Communication Services",
    "radio broadcasting": "Communication Services",
    "services-motion picture & tape distribution": "Communication Services",
    "television broadcasting stations": "Communication Services",
    "radio & tv broadcasting & communications equipment": "Communication Services",
    "services-computer & computer software stores": "Communication Services",
    "services-educational services": "Communication Services",
    # Consumer Defensive
    "beverages": "Consumer Defensive",
    "retail-grocery stores": "Consumer Defensive",
    "food and kindred products": "Consumer Defensive",
    "tobacco products": "Consumer Defensive",
    "soap, detergent, cleaning preparations": "Consumer Defensive",
    "perfumes, cosmetics & other toilet preparations": "Consumer Defensive",
    "grain mill products": "Consumer Defensive",
    "canned, frozen & preserved fruit, veg & food": "Consumer Defensive",
    "fats & oils": "Consumer Defensive",
    # Real Estate
    "real estate investment trusts": "Real Estate",
    "real estate": "Real Estate",
    "land subdividers & developers (no cemeteries)": "Real Estate",
    "operators of apartment buildings": "Real Estate",
    # Utilities
    "water supply": "Utilities",
    "sanitary services": "Utilities",
    "gas & other services combined": "Utilities",
    # Basic Materials
    "gold mining": "Basic Materials",
    "steel works, blast furnaces": "Basic Materials",
    "plastic materials, synth resins & nonvulcan elastomers": "Basic Materials",
    "industrial chemicals": "Basic Materials",
    "agricultural chemicals": "Basic Materials",
    "mining & quarrying of nonmetallic minerals": "Basic Materials",
    "miscellaneous metal ores": "Basic Materials",
    "paper mills": "Basic Materials",
    "chemicals & allied products": "Basic Materials",
    "metal mining": "Basic Materials",
}


def _map_sic_to_sector(industry_title: str) -> str:
    """Map an FMP SIC industry title to a GICS sector name."""
    if not industry_title:
        return "Other"
    return SIC_TO_SECTOR.get(industry_title.lower().strip(), "Other")

RISK_PROFILE_LABELS: Dict[str, str] = {
    "conservative": "Safe, Long-term Value",
    "moderate": "Moderate",
    "aggressive": "Aggressive",
    "very_aggressive": "High Risk",
}

# Congressional amount range → midpoint in dollars
AMOUNT_RANGES: Dict[str, float] = {
    "$1,001 - $15,000": 8_000,
    "$15,001 - $50,000": 32_500,
    "$50,001 - $100,000": 75_000,
    "$100,001 - $250,000": 175_000,
    "$250,001 - $500,000": 375_000,
    "$500,001 - $1,000,000": 750_000,
    "$1,000,001 - $5,000,000": 3_000_000,
    "$5,000,001 - $25,000,000": 15_000_000,
    "$25,000,001 - $50,000,000": 37_500_000,
    "$50,000,001 - $100,000,000": 75_000_000,
    "Over $50,000,000": 75_000_000,
}

# Congressional type → our action
CONGRESSIONAL_TYPE_MAP: Dict[str, str] = {
    "purchase": "BOUGHT",
    "sale_full": "SOLD",
    "sale_partial": "SOLD",
    "sale (full)": "SOLD",
    "sale (partial)": "SOLD",
    "sale": "SOLD",
    "exchange": "BOUGHT",
}

# Sentinel for "this stored number could not be coerced to a finite float".
# Distinct from 0.0 so `_stat_disclosure` can tell "genuinely flat" from "unusable"
# and degrade to the honest em-dash tile instead of a confident green "+0.0%".
_UNUSABLE_NUMBER = object()

# Explicit row caps. PostgREST silently truncates any un-limited select at 1000 rows,
# so an absent .limit() is not "no limit" — it is an invisible one.
_TRADE_GROUPS_PAGE = 20
_TRADES_PER_PAGE = 50
_ACTIVITY_FEED_PAGE = 20
_ROSTER_PAGE = 500
_TRADE_COUNT_ROWS = 5000

# ── Profile-cache schema floor ───────────────────────────────────────────────
#
# `whale_profile_cache` stores an ASSEMBLED `WhaleProfileResponse`, so a code change to
# the assembly must invalidate it. That used to be done by DELETING EVERY ROW in the
# `main.py` lifespan — which also fired on an OOM, a health-check flap or an instance
# rotation, leaving all 56 whales Tier-2 cold for reasons that had nothing to do with a
# deploy. The first visitor to each then paid a full rebuild.
#
# This is the app's own sanctioned idiom instead (see `project_report_caching_layers`):
# a row is only fresh if it was written at or after the floor. Bump the floor when the
# assembled shape changes; leave it alone for every other deploy.
#
# ⚠️ The literal must be <= the actual deploy wall-clock. A FUTURE-dated floor makes even
# freshly-written rows fail freshness, turning the cache permanently cold — the exact
# failure mode `CACHE_SCHEMA_FLOOR` documents for the report caches.
#
# 2026-08-20: activity disclosure added `activity_status` / `activity_label` /
# `last_activity_date` / `lifecycle_note` to the profile shape.
WHALE_PROFILE_SCHEMA_FLOOR = datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc)

# How far a sector breakdown may sum from 100% before it is corrected. Wide enough to
# absorb per-slice rounding to 1 dp across ~12 slices, narrow enough that a genuinely
# wrong breakdown is caught.
_SECTOR_SUM_TOLERANCE = 1.5

# Fixed namespace for `_snapshot_group_id`. Must never change: it is what makes a
# snapshot-derived group keep one identity across requests and processes.
_SNAPSHOT_GROUP_NS = uuid.UUID("6f9d1c2a-4b7e-5d38-9a10-c7f2e5b41d90")

# ── In-Memory TTL Caches ────────────────────────────────────────────

_whale_list_cache: Dict[str, Tuple[float, Any]] = {}
WHALE_LIST_CACHE_TTL = 300  # 5 minutes

_whale_profile_cache: Dict[str, Tuple[float, Any]] = {}
WHALE_PROFILE_CACHE_TTL = 3600  # 1 hour

_whale_activity_cache: Dict[str, Tuple[float, Any]] = {}
WHALE_ACTIVITY_CACHE_TTL = 600  # 10 minutes

_filing_dates_cache: Dict[str, Tuple[float, Any]] = {}
FILING_DATES_CACHE_TTL = 86400  # 24 hours — filing dates change quarterly

# In-flight profile builds — dedup concurrent cache-miss rebuilds so N
# simultaneous requests for the same whale share ONE expensive FMP build
# (CLAUDE.md invariant #4), instead of a thundering herd of redundant fan-outs.
# The shared build is follow-state-free; each caller overlays its own per-user
# follow state on the result.
_whale_profile_inflight: Dict[str, "asyncio.Future"] = {}


def _cache_get(cache: Dict, key: str, ttl: int) -> Optional[Any]:
    entry = cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if _time.monotonic() - ts > ttl:
        del cache[key]
        return None
    return value


# Hard cap per cache. This module keeps SEVERAL dicts and passes the target in, which is why
# it needs its own bounded setter rather than the module-level one the other services use.
# Unbounded, each grew with the number of distinct whales/tickers ever requested and was
# never pruned — `_cache_get` only drops a key when that same key is read again after expiry.
_CACHE_MAX_ENTRIES = 1024


def _cache_set(cache: Dict, key: str, value: Any) -> None:
    # Move-to-end on write so the head is the least-recently-written, then evict from it.
    cache.pop(key, None)
    cache[key] = (_time.monotonic(), value)
    if len(cache) > _CACHE_MAX_ENTRIES:
        for _old in list(cache.keys())[: len(cache) - _CACHE_MAX_ENTRIES]:
            cache.pop(_old, None)


# ── Tier redaction (Whales are Pro/Max — entitlements.whale_detail_unlocked) ─────────

# Resolved once per process from `whales.name`. A miss is NOT cached as None: the registry
# sync may not have run yet on a cold database, and caching that would make the free whale
# permanently unavailable until the next deploy.
_free_whale_id: Optional[str] = None


def reset_free_whale_cache() -> None:
    """Forget the memoized free-tier whale id.

    `free_tier_whale_id` caches the uuid in a module global for the process lifetime,
    and `is_free_tier_whale` matches on the MUTABLE display name "Bill Gates". A
    registry sync resolves renames by CIK, so renaming that row silently strips every
    Free account of its one followable whale until the next deploy. Called from the
    registry-sync path and available to the app lifespan.
    """
    global _free_whale_id
    _free_whale_id = None


def whale_detail_allowed(tier: Optional[str], whale_id: str, sb) -> bool:
    """May this caller see the position-level detail for THIS whale?

    Paid tiers: always. Free: only for the designated free whale — they get one investor in
    full so the feature demonstrates itself end to end, rather than a feed whose every row
    dead-ends in a paywall.
    """
    if whale_detail_unlocked(tier):
        return True
    free_id = free_tier_whale_id(sb)
    return free_id is not None and str(whale_id) == str(free_id)


def free_tier_whale_id(sb) -> Optional[str]:
    """The uuid of the one whale a Free account may track, or None if unresolvable.

    Kept out of `entitlements.py` on purpose — that module is pure data + pure functions,
    and this needs a Supabase read. Returning None must make the caller fail CLOSED (no
    whale is followable on Free) rather than open.
    """
    global _free_whale_id
    if _free_whale_id is not None:
        return _free_whale_id
    try:
        result = (
            sb.table("whales")
            .select("id,name")
            .ilike("name", FREE_TIER_WHALE_NAME)
            .limit(1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            logger.warning(
                "Free-tier whale %r not found in the whales table — Free accounts can "
                "follow nobody until the registry sync runs",
                FREE_TIER_WHALE_NAME,
            )
            return None
        _free_whale_id = rows[0]["id"]
        return _free_whale_id
    except Exception as e:
        logger.warning(
            "Free-tier whale lookup failed (%s: %s) — treating as unresolved",
            type(e).__name__, e,
        )
        return None


# A neutral behaviour summary for a locked profile. `behavior_summary` is the one
# non-defaulted field on WhaleProfileResponse, so it cannot simply be dropped — and
# blanking it is better than shipping the real one, since the strings it holds
# ("Accumulating", "Technology") ARE the withheld position detail in prose form.
_LOCKED_BEHAVIOR = WhaleBehaviorSummaryResponse(
    action="", primary_focus="", secondary_action="", secondary_focus=""
)


class WhaleFollowLockedException(Exception):
    """The caller's plan does not allow tracking this whale.

    Carries the numbers so the endpoint can put them in `details` — the error copy stays
    number-free so the limit lives in exactly one place (`WHALE_FOLLOW_LIMITS`).
    """

    def __init__(self, tier_required: str, limit: Optional[int], reason: str) -> None:
        super().__init__(reason)
        self.tier_required = tier_required
        self.limit = limit
        self.reason = reason


def _apply_follow_locks(
    whales: List[TrendingWhaleResponse], tier: Optional[str]
) -> List[TrendingWhaleResponse]:
    """Return the roster with ``is_locked`` and ``is_following_inactive`` set for this tier.

    ``is_locked`` — may this caller START following this whale? Rules, in order:
      • already following  → NEVER locked. A user must always be able to unfollow, and a
        locked button on a whale they already track reads as data loss.
      • Free               → locked unless it is the designated free whale.
      • Pro                → locked only once the caller is AT the limit (so the 10th
        follow is offered and the 11th is not).
      • Max                → never locked.

    ``is_following_inactive`` — is this a follow the plan doesn't surface? It MUST mirror
    `get_whale_activity_feed`'s truncation exactly, including its `> limit` boundary: that
    is the whole point of the flag. A Free account holding exactly ONE follow keeps it even
    if it isn't Bill Gates (`1 > 1` is false, so the feed doesn't truncate), and the avatar
    row has to agree or the inconsistency simply moves.

    Non-mutating for the same reason `redact_whale_profile` is: the argument is the object
    in `_whale_list_cache`, and although that cache is per-user, an in-place write would
    still pin one plan's locks onto that user's next 5 minutes of requests.
    """
    limit = whale_follow_limit(tier)
    if limit is None:
        return whales                      # Max — unlimited, nothing to lock or truncate

    is_free = normalize_tier(tier) == TIER_FREE
    followed = [w for w in whales if w.is_following]
    at_cap = len(followed) >= limit

    # The subset the activity feed will actually serve. Same shape as whale_service
    # .get_whale_activity_feed: only truncate when the count EXCEEDS the limit; Free keeps
    # the designated free whale, Pro keeps a deterministic id-sorted prefix so the feed
    # doesn't reshuffle between requests.
    if len(followed) > limit:
        if is_free:
            active_ids = {w.id for w in followed if is_free_tier_whale(w.name)}
        else:
            active_ids = set(sorted(str(w.id) for w in followed)[:limit])
    else:
        active_ids = {w.id for w in followed}

    out: List[TrendingWhaleResponse] = []
    for w in whales:
        updates = {}
        if w.is_following:
            if w.id not in active_ids:
                updates["is_following_inactive"] = True
        elif is_free:
            if not is_free_tier_whale(w.name):
                updates["is_locked"] = True
        elif at_cap:
            updates["is_locked"] = True
        out.append(w.model_copy(update=updates) if updates else w)
    return out


def redact_whale_profile(
    profile: WhaleProfileResponse, tier_required: str
) -> WhaleProfileResponse:
    """Return a NEW profile with the PAID sections withheld. Never mutates ``profile``.

    Withheld: current_holdings, recent_trade_groups, recent_trades, sentiment_summary,
    behavior_summary. Kept: header, bio, risk profile, both stat tiles, and sector_exposure
    — enough to judge the investor and see the shape of the book, so the profile previews
    the product instead of walling it.

    ⚠️ **Must not mutate.** The argument is routinely the object held in the module-level
    ``_whale_profile_cache`` — one instance shared by every caller for an hour, as
    ``_whale_profile_inflight``'s own comment states ("the shared build is
    follow-state-free; each caller overlays its own per-user follow state"). Stripping it
    in place would empty the holdings for every PAYING user until the next rebuild, long
    after the free request that caused it. `model_copy(update=...)` is what
    `_overlay_follow_state` already uses for the same reason.
    """
    return profile.model_copy(
        update={
            "current_holdings": [],
            "recent_trade_groups": [],
            "recent_trades": [],
            "sentiment_summary": "",
            "behavior_summary": _LOCKED_BEHAVIOR,
            "is_locked": True,
            "tier_required": tier_required,
        }
    )


# ── Service ──────────────────────────────────────────────────────────


class WhaleService:
    """Builds aggregated whale data from FMP 13F + Congressional sources."""

    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()

    # ── Public API ───────────────────────────────────────────────────

    async def get_whale_list(
        self,
        category: Optional[str] = None,
        user_id: Optional[str] = None,
        tier: Optional[str] = None,
    ) -> List[TrendingWhaleResponse]:
        """List whales, optionally filtered by category.

        Every whale is always LISTED — the roster is the feature's own demo and stays free
        on all tiers. ``tier`` only decides which rows come back with ``is_locked``, i.e.
        whose Follow button is a paywall.
        """
        cache_key = f"whales:{category or 'all'}:{user_id or 'anon'}"
        cached = _cache_get(_whale_list_cache, cache_key, WHALE_LIST_CACHE_TTL)
        if cached is not None:
            return _apply_follow_locks(cached, tier)

        sb = get_supabase()

        # Fetch whales
        query = sb.table("whales").select("*")
        if category:
            query = query.eq("category", category)
        query = query.order("followers_count", desc=True).limit(_ROSTER_PAGE)
        result = query.execute()
        whales = result.data or []

        # Fetch followed whale IDs for this user
        followed_ids: set = set()
        if user_id:
            follows = (
                sb.table("whale_follows")
                .select("whale_id")
                .eq("user_id", user_id)
                .execute()
            )
            followed_ids = {f["whale_id"] for f in (follows.data or [])}

        # Fetch recent trade counts (last 90 days)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
        trade_counts: Dict[str, int] = {}
        try:
            # Explicit cap. PostgREST silently truncates an un-limited select at 1000
            # rows, so "no .limit()" is an INVISIBLE limit — and a silently truncated
            # aggregate here under-reports `recent_trade_count` on the roster with no
            # signal at all.
            # Cut on `date` — the FILING/DISCLOSURE date — not on `created_at`, which is
            # when WE ingested the row. The old form made this count measure our own job:
            # it read non-zero for a dormant whale that merely got re-ingested, and zero
            # for an active one we had not re-hydrated lately. `date` is TEXT in
            # `YYYY-MM-DD`, so a lexical `gte` is a chronological one.
            tg_result = (
                sb.table("whale_trade_groups")
                .select("whale_id, trade_count")
                .gte("date", cutoff)
                .limit(_TRADE_COUNT_ROWS)
                .execute()
            )
            rows = tg_result.data or []
            if len(rows) >= _TRADE_COUNT_ROWS:
                logger.warning(
                    "[whale_list] Recent-trade-count scan hit its %d-row cap — "
                    "counts may under-report; raise _TRADE_COUNT_ROWS or aggregate in SQL",
                    _TRADE_COUNT_ROWS,
                )
            for tg in rows:
                wid = str(tg.get("whale_id") or "")
                if not wid:
                    continue
                trade_counts[wid] = trade_counts.get(wid, 0) + (tg.get("trade_count") or 0)
        except Exception as e:
            logger.warning("Failed to fetch recent trade counts: %s", e)

        # `x or ""` (NOT .get(k, "")) throughout: a NULL column arrives as an
        # EXISTING key with value None, so .get's default never applies and an
        # explicit None fails pydantic's `str` fields with a 500 (this exact
        # trap broke /whales/activity for NULL avatar_url rows).
        response = [
            TrendingWhaleResponse(
                id=str(w["id"]),
                name=w["name"],
                category=w.get("category") or "investors",
                avatar_url=w.get("avatar_url"),
                followers_count=w.get("followers_count") or 0,
                is_following=str(w["id"]) in followed_ids,
                title=w.get("title") or "",
                description=w.get("description") or "",
                recent_trade_count=trade_counts.get(str(w["id"]), 0),
                **_activity_fields(w),
                # Blank/whitespace firm (bad row edit) → None so iOS
                # `!firm.isEmpty` guards keep the firm line hidden, not blank.
                firm_name=(w.get("firm_name") or "").strip() or None,
            )
            for w in whales
        ]

        # Cached WITHOUT locks, then locked per request on the way out — so a user who
        # upgrades sees every Follow button unlock immediately instead of waiting out the
        # 5-minute TTL on a list that was cached under their old plan.
        _cache_set(_whale_list_cache, cache_key, response)
        return _apply_follow_locks(response, tier)

    # ── Cache-Aside Constants ───────────────────────────────────────
    PROFILE_CACHE_TTL_HOURS = 24

    async def get_whale_profile(
        self,
        whale_id: str,
        user_id: Optional[str] = None,
        force_refresh: bool = False,
        tier: Optional[str] = None,
        is_guest: bool = True,
    ) -> Optional[WhaleProfileResponse]:
        """Get a whale profile, gated for this caller's plan.

        ``is_guest`` DEFAULTS TO TRUE on purpose: it disarms the destructive
        ``force_refresh`` lever below, so a call site that forgets to pass it fails
        closed rather than handing an anonymous caller a delete.

        A THIN wrapper over `_get_whale_profile_ungated`, and the split is deliberate: the
        builder underneath has FOUR separate success returns (Tier-1 hit, Tier-2 hit,
        in-flight join, fresh build), and gating them individually meant three of them
        silently served the paid sections. The Tier-1 hit is the common path, so that miss
        would have made the gate a no-op within an hour of any profile being opened once.
        One exit point makes a missed branch impossible rather than merely unlikely.
        """
        profile = await self._get_whale_profile_ungated(
            whale_id=whale_id, user_id=user_id,
            force_refresh=force_refresh, is_guest=is_guest,
        )
        if profile is None:
            return None
        # The designated free whale is exempt: Free gets ONE investor in full, which is the
        # entire point of designating one. Without this, a Free user could follow Gates and
        # see his trades in the Recent Trades feed but hit a paywall on every tap into them
        # — a feed that leads nowhere, and a worked example that never works.
        if whale_detail_unlocked(tier) or is_free_tier_whale(profile.name):
            return profile
        # Copy-on-read, AFTER every cache layer: both tiers store the unredacted build,
        # which is shared by every caller regardless of plan. See redact_whale_profile.
        return redact_whale_profile(profile, required_tier_for_whales(tier) or TIER_PRO)

    async def _get_whale_profile_ungated(
        self,
        whale_id: str,
        user_id: Optional[str] = None,
        force_refresh: bool = False,
        is_guest: bool = True,
    ) -> Optional[WhaleProfileResponse]:
        """Build/fetch the FULL profile — no tier gate. Callers must go through
        `get_whale_profile`; this is the cache machinery only.

        3-tier cache-aside pattern.

        Tier 1: In-memory dict (1 hr TTL, lost on restart)
        Tier 2: Supabase whale_profile_cache (24 hr TTL, survives restart)
        Tier 3: Live FMP processing → cache result in both tiers

        Follow state is ALWAYS read fresh (not cached) since it's per-user.
        Pass force_refresh=True to bypass all caches and rebuild from FMP.
        """
        sb = get_supabase()
        mem_key = f"profile:{whale_id}"

        # force_refresh DESTRUCTIVELY deletes the durable Tier-2 snapshot store
        # (whale_filing_snapshots) and triggers an unbounded FMP rebuild. It is
        # an authenticated operator/debug lever — iOS never sends it.
        #
        # ⚠️ The predicate is `is_guest`, NOT `user_id is None`. The old form was DEAD:
        # this route resolves through `get_watchlist_identity`, which NEVER returns a
        # None id — a signed-out caller gets the shared guest sentinel, and one sending
        # any `X-Guest-Id` gets a per-install uuid5. Both are truthy, so the guard never
        # fired and an unauthenticated GET could delete a whale's snapshot rows and then
        # drive an unbounded, un-deduped FMP rebuild (force_refresh is excluded from the
        # `_whale_profile_inflight` coalescing on purpose), looping over every whale id.
        # Worse, once FMP was exhausted the degraded empty-holdings build was written
        # back into the 24-hour cache — poisoning the profile for PAYING users too.
        #
        # Do NOT test `user_id == GUEST_USER_ID` instead: a per-install uuid5 never
        # equals the sentinel. That is the documented trap in .claude/rules/auth.md §1a.
        if force_refresh and is_guest:
            logger.warning(
                "Ignoring force_refresh for whale %s — requires an authenticated account",
                whale_id,
            )
            force_refresh = False

        if force_refresh:
            logger.info("Force refresh requested for whale %s — busting all caches", whale_id)
            _whale_profile_cache.pop(mem_key, None)
            try:
                sb.table("whale_profile_cache").delete().eq("whale_id", whale_id).execute()
                sb.table("whale_filing_snapshots").delete().eq("whale_id", whale_id).execute()
            except Exception as e:
                logger.warning("Cache bust failed for %s: %s", whale_id, e)
        else:
            # ── Tier 1: In-memory cache (fast, per-process) ────────────
            cached = _cache_get(_whale_profile_cache, mem_key, WHALE_PROFILE_CACHE_TTL)
            if cached is not None:
                # Overlay fresh follow state
                return self._overlay_follow_state(cached, user_id, sb)

            # ── Tier 2: Supabase profile cache (24h TTL) ───────────────
            try:
                cache_row = (
                    sb.table("whale_profile_cache")
                    .select("profile_json, cached_at")
                    .eq("whale_id", whale_id)
                    .execute()
                )
                if cache_row.data:
                    row = cache_row.data[0]
                    cached_at = _as_aware(row.get("cached_at"))
                    if cached_at is None:
                        raise ValueError(
                            f"unparseable cached_at {row.get('cached_at')!r}"
                        )
                    if cached_at < WHALE_PROFILE_SCHEMA_FLOOR:
                        # Written by an older profile shape — treat as a miss and rebuild
                        # rather than replaying JSON the current model may not accept.
                        logger.info(
                            "Whale profile %s predates the schema floor — rebuilding",
                            whale_id,
                        )
                        raise _SchemaFloorMiss
                    age_hours = (
                        datetime.now(timezone.utc) - cached_at
                    ).total_seconds() / 3600
                    if age_hours < self.PROFILE_CACHE_TTL_HOURS:
                        profile = WhaleProfileResponse(**row["profile_json"])
                        _cache_set(_whale_profile_cache, mem_key, profile)
                        logger.info(
                            "Whale profile %s served from Supabase cache (%.1fh old)",
                            whale_id, age_hours,
                        )
                        return self._overlay_follow_state(profile, user_id, sb)
                    else:
                        logger.info(
                            "Whale profile cache expired for %s (%.1fh old)",
                            whale_id, age_hours,
                        )
            except _SchemaFloorMiss:
                # A normal, expected miss — not a read failure. Caught before the
                # handler below so it does not log as one.
                pass
            except Exception as e:
                logger.warning(
                    "whale_profile_cache read failed for %s: %s", whale_id, e
                )

        # ── Tier 3: Build from FMP + DB (slow, authoritative) ──────
        # Dedup concurrent same-whale misses (invariant #4): the first caller
        # builds; others await its follow-state-free result and overlay their
        # own follow state. force_refresh always rebuilds (it owns the
        # destructive delete above), so it is excluded from the dedup.
        if not force_refresh:
            inflight = _whale_profile_inflight.get(whale_id)
            if inflight is not None:
                shared = await asyncio.shield(inflight)
                if shared is None:
                    return None
                return self._overlay_follow_state(shared, user_id, sb)

        fut: "asyncio.Future" = asyncio.get_running_loop().create_future()
        if not force_refresh:
            _whale_profile_inflight[whale_id] = fut
        # Consume the future's exception so a SOLO build failure (no concurrent
        # awaiter) doesn't emit a spurious "Future exception was never retrieved"
        # asyncio traceback. Real awaiters still get it via `await inflight`.
        fut.add_done_callback(lambda f: f.cancelled() or f.exception())
        try:
            # Build follow-state-free (user_id=None); each caller overlays its
            # own follow state below, so the shared result is user-agnostic.
            profile_no_follow = await self._build_whale_profile(whale_id, None)
            # Never cache a DEGRADED build. `_build_whale_profile` returns a valid,
            # fully-shaped object even when the snapshot read failed — the sections are
            # simply empty — so "not None" is not evidence that it has data. Caching one
            # pins an empty book for 24 hours; measured, it survives a restart and is
            # served verbatim with no rebuild attempted. Serve it to THIS caller (better
            # than an error), but let the next request try again.
            _degraded = getattr(self, "_last_build_degraded", False)
            if profile_no_follow is not None and not _degraded:
                _cache_set(_whale_profile_cache, mem_key, profile_no_follow)
                try:
                    sb.table("whale_profile_cache").upsert(
                        {
                            "whale_id": whale_id,
                            "profile_json": profile_no_follow.model_dump(),
                            # UTC-AWARE. A naive local stamp written into a `timestamptz`
                            # is interpreted as UTC by Postgres, so on any non-UTC host
                            # the row reads back in the future, `age_hours` goes negative
                            # and the 24h TTL never expires.
                            "cached_at": datetime.now(timezone.utc).isoformat(),
                        },
                        on_conflict="whale_id",
                    ).execute()
                except Exception as e:
                    logger.warning(
                        "whale_profile_cache write failed for %s: %s", whale_id, e
                    )
            if not fut.done():
                fut.set_result(profile_no_follow)
        except Exception as e:
            if not fut.done():
                fut.set_exception(e)
            raise
        finally:
            # SETTLE ON CANCELLATION. CancelledError is a BaseException and skips the
            # `except Exception` above, so a cancelled build left this future PENDING forever
            # while the pop below stopped only new joiners. Every caller already parked on
            # `shared = await inflight` (line ~478) hung for the process lifetime — and that
            # joiner has no try/except at all, so it cannot even fail out.
            #
            # A normal exception rather than `fut.cancel()`, so the joiner fails through the
            # endpoint's own error path instead of having its task cancelled.
            if not fut.done():
                fut.set_exception(
                    RuntimeError(f"whale profile build for {whale_id} was cancelled")
                )
                fut.exception()   # mark retrieved; silences the GC warning when unjoined
            if not force_refresh:
                _whale_profile_inflight.pop(whale_id, None)

        if profile_no_follow is None:
            return None
        return self._overlay_follow_state(profile_no_follow, user_id, sb)

    def _overlay_follow_state(
        self,
        profile: WhaleProfileResponse,
        user_id: Optional[str],
        sb,
    ) -> WhaleProfileResponse:
        """Merge fresh follow state onto a cached profile."""
        if not user_id:
            return profile
        try:
            follow_result = (
                sb.table("whale_follows")
                .select("id")
                .eq("user_id", user_id)
                .eq("whale_id", profile.id)
                .execute()
            )
            is_following = bool(follow_result.data)
            if is_following != profile.is_following:
                return profile.model_copy(update={"is_following": is_following})
        except Exception as e:
            logger.warning("Follow state check failed: %s", e)
        return profile

    async def _build_whale_profile(
        self,
        whale_id: str,
        user_id: Optional[str] = None,
    ) -> Optional[WhaleProfileResponse]:
        """Build a complete WhaleProfileResponse from FMP + DB data.

        This is the expensive path — called only on cache miss.
        """
        sb = get_supabase()

        # Step 1: Fetch whale record
        try:
            result = (
                sb.table("whales").select("*").eq("id", whale_id).execute()
            )
        except Exception as e:
            logger.error(
                "[whale_profile] DB read failed for whale_id=%s: %s",
                whale_id, e,
            )
            return None
        if not result.data:
            logger.warning("[whale_profile] Whale not found: %s", whale_id)
            return None
        whale = result.data[0]

        # Step 2: Route to data source → get snapshot
        snapshot: Optional[Dict[str, Any]] = None
        # Did the routing actually run a sync that could have rewritten the `whales` row?
        # Serving a stored snapshot does not, and that is the COMMON path (55 of 56).
        # Used ONLY for the degraded-build test below: a whale the nightly job has
        # hydrated is KNOWN to have a snapshot, so failing to read one means we lost it,
        # not that the filer has no data. It is deliberately NOT used to decide whether
        # to re-read the whales row — see `served_from_storage` for that.
        _hydrated_before = whale.get("last_hydrated_at")
        # Tracks whether this build was backed by real data. A whale that HAS been
        # hydrated but whose snapshot could not be read is DEGRADED — see the guard on
        # the cache write in `_get_whale_profile_ungated`.
        self._last_build_degraded = False
        served_from_storage = False
        try:
            snapshot, served_from_storage = await self._get_or_process_latest(
                whale_id, whale
            )
        except Exception as e:
            logger.error(
                "[whale_profile] All data sources failed for %s (%s): %s",
                whale["name"], whale.get("data_source"), e,
            )

        # Re-read the whale record ONLY when a BUILD ran, because only a build calls
        # `_sync_to_whale_tables` (which rewrites portfolio_value, the return columns and
        # the activity signals).
        #
        # ⚠️ Keyed on `served_from_storage`, NOT on `last_hydrated_at`. That column is
        # written only by the nightly job, so it answers "has hydration ever run", not
        # "did a sync just run" — and it was wrong for exactly the case this guard exists
        # for: a hydrated whale whose snapshot read fails, rebuilds, syncs, and then had
        # its fresh values discarded. Reachable today via `?force_refresh=true`, which
        # deletes the snapshot rows first.
        if not served_from_storage:
            try:
                refreshed = sb.table("whales").select("*").eq("id", whale_id).execute()
                if refreshed.data:
                    whale = refreshed.data[0]
            except Exception as e:
                logger.warning(
                    "[whale_profile] Whale re-read failed for %s (%s: %s) — using the "
                    "pre-sync row",
                    whale_id, type(e).__name__, e,
                )

        # Step 3: Fetch follow state
        is_following = False
        if user_id:
            try:
                follow_result = (
                    sb.table("whale_follows")
                    .select("id")
                    .eq("user_id", user_id)
                    .eq("whale_id", whale_id)
                    .execute()
                )
                is_following = bool(follow_result.data)
            except Exception as e:
                logger.warning("[whale_profile] Follow state failed: %s", e)

        # Step 4: Build response from snapshot + whale record
        risk_label = RISK_PROFILE_LABELS.get(
            whale.get("risk_profile") or "", whale.get("risk_profile") or ""
        )

        # Sectors — filter out 0% and roll them into "Other"
        sectors: List[WhaleSectorAllocationResponse] = []
        other_pct = 0.0
        if snapshot and snapshot.get("sector_data"):
            for s in snapshot["sector_data"]:
                # `or`-form + `_finite_float`, never `.get(k, default)`: this is
                # untyped JSONB, so a key present with a JSON `null` returns None and
                # the default never applies — `float(None)` is a TypeError that the
                # endpoint turns into a 503 for the whole profile.
                pct = _finite_float(s.get("allocation"))
                name = s.get("name") or "Other"
                if pct < 0.5 or name == "Other":
                    other_pct += pct
                else:
                    sectors.append(
                        WhaleSectorAllocationResponse(
                            id=str(uuid.uuid4()),
                            name=name,
                            percentage=round(pct, 1),
                            color_hex=s.get("color_hex")
                            or SECTOR_COLORS.get(name, DEFAULT_SECTOR_COLOR),
                        )
                    )
            sectors = _normalize_sector_exposure(sectors, other_pct)

        # Holdings
        holdings: List[WhaleHoldingResponse] = []
        if snapshot and snapshot.get("holdings_data"):
            for h in snapshot["holdings_data"][:30]:
                holdings.append(
                    WhaleHoldingResponse(
                        id=str(uuid.uuid4()),
                        ticker=h.get("ticker") or "",
                        company_name=h.get("company_name") or "",
                        logo_url=h.get("logo_url"),
                        allocation=_finite_float(h.get("allocation")),
                        change_percent=_finite_float(h.get("change_percent")),
                    )
                )

        # Trade groups — snapshot (per-filing list) + historical DB rows.
        trade_groups: List[WhaleTradeGroupResponse] = []
        all_trades: List[WhaleTradeResponse] = []

        snap_groups = (snapshot or {}).get("trade_groups")
        if not snap_groups:
            # Backward-compat: older snapshots stored a single group.
            single = (snapshot or {}).get("trade_group")
            snap_groups = [single] if single else []

        for tg in snap_groups:
            if not tg:
                continue
            trades_for_group = self._build_trade_responses(tg.get("trades", []))
            # A STABLE id, not `uuid.uuid4()`. These groups come from the snapshot
            # rather than from `whale_trade_groups`, so there is no row id to use — but
            # a fresh random one every request re-keyed SwiftUI's `Identifiable` list on
            # every refresh, tearing down and rebuilding every card. Derived from
            # (whale, filing date) so it is reproducible across requests and processes.
            trade_groups.append(
                self._assemble_group_response(
                    _snapshot_group_id(whale_id, tg.get("date")), tg, trades_for_group
                )
            )
            all_trades.extend(trades_for_group)

        # Historical trade groups from DB (dedup by date)
        try:
            db_groups = (
                sb.table("whale_trade_groups")
                .select("*")
                .eq("whale_id", whale_id)
                .order("date", desc=True)
                .limit(12)
                .execute()
            )
            existing_dates = {g.date for g in trade_groups}
            fresh_rows = [
                tg for tg in (db_groups.data or [])
                if (tg.get("date") or "") not in existing_dates
            ]
            # ONE trades query for every remaining group. This was a per-group query
            # inside the loop — up to 12 more sequential blocking round-trips on the
            # single hottest path in the feature.
            for group in self._assemble_groups_with_trades(sb, fresh_rows):
                trade_groups.append(group)
                all_trades.extend(group.trades)
        except Exception as e:
            logger.warning(
                "[whale_profile] DB trade groups failed for %s: %s",
                whale_id, e,
            )

        # ⚠️ A whale with `last_hydrated_at` set is KNOWN to have a snapshot. Reaching
        # here with `snapshot is None` therefore means we failed to read it — an FMP 429,
        # a Supabase blip — not that the filer has no data. The profile below is still
        # assembled and still VALID (empty holdings/sectors/trades), which is exactly why
        # it must never be cached: it would pin an empty book for 24h and, as measured,
        # does not self-heal across a restart.
        #
        # A whale that was never hydrated (Mark Kelly) is NOT degraded — an empty profile
        # is the truth for him, and caching it is correct.
        if snapshot is None and _hydrated_before:
            self._last_build_degraded = True
            logger.warning(
                "[whale_profile] DEGRADED build for %s (%s): hydrated at %s but no "
                "snapshot could be read — serving empty sections and NOT caching",
                whale.get("name"), whale_id, _hydrated_before,
            )

        # Combined timeline, most-recent filing first.
        trade_groups.sort(key=lambda g: g.date or "", reverse=True)
        # `all_trades` accumulated in BUILD order (snapshot groups, then DB groups), so
        # `all_trades[:20]` below was "the trades of whichever groups happened to be
        # built first" — not the twenty most recent. When the snapshot group was older
        # than the newest DB group, the profile's Recent Trades listed the wrong twenty.
        # Sorted by trade date desc, then by size, so the tie-break is stable.
        all_trades.sort(key=lambda t: ((t.date or ""), t.amount), reverse=True)

        # Behavior summary
        behavior_raw = (
            (snapshot or {}).get("behavior_summary")
            or whale.get("behavior_summary")
            or {}
        )
        behavior = WhaleBehaviorSummaryResponse(
            action=behavior_raw.get("action", "Holding"),
            primary_focus=behavior_raw.get("primaryFocus", "existing positions"),
            secondary_action=behavior_raw.get("secondaryAction", "Maintaining"),
            secondary_focus=behavior_raw.get(
                "secondaryFocus", "portfolio allocation"
            ),
        )

        sentiment = (
            (snapshot or {}).get("sentiment_text")
            or whale.get("sentiment_summary")
            or ""
        )

        # Classified from PERSISTED columns only — never from a live FMP probe, whose
        # empty result is indistinguishable from a 429 or a plan downgrade.
        activity = _activity_for(whale)

        disclosure = self._stat_disclosure(whale, snapshot)
        portfolio_value = disclosure["portfolio_value"]
        ytd_return = disclosure["ytd_return"]

        # `x or ""` not .get(k, ""): NULL columns arrive as existing keys with
        # None — .get defaults never apply, and None fails the str fields.
        profile = WhaleProfileResponse(
            id=str(whale["id"]),
            name=whale["name"],
            title=whale.get("title") or "",
            description=whale.get("description") or "",
            firm_name=(whale.get("firm_name") or "").strip() or None,
            avatar_url=whale.get("avatar_url"),
            risk_profile=risk_label,
            portfolio_value=portfolio_value,
            ytd_return=ytd_return,
            sector_exposure=sectors,
            current_holdings=holdings,
            recent_trade_groups=trade_groups[:5],
            recent_trades=all_trades[:20],
            behavior_summary=behavior,
            sentiment_summary=sentiment,
            is_following=is_following,
            data_source=whale.get("data_source") or "",
            return_source=whale.get("return_source") or "",
            return_label=disclosure["return_label"],
            return_status=disclosure["return_status"],
            return_window_years=disclosure["return_window_years"],
            portfolio_status=disclosure["portfolio_status"],
            portfolio_as_of=disclosure["portfolio_as_of"],
            filing_date=disclosure["filing_date"],
            activity_status=activity.status if activity.needs_disclosure else ACTIVITY_UNKNOWN,
            activity_label=activity.label if activity.needs_disclosure else "",
            last_activity_date=whale.get("last_activity_date") or None,
            lifecycle_note=(whale.get("lifecycle_note") or "").strip() or None,
        )

        return profile

    async def get_whale_activity_feed(
        self, user_id: str, tier: Optional[str] = None
    ) -> List[WhaleTradeGroupActivityResponse]:
        """Get recent trade activity from user's followed whales.

        ``tier`` decides how many of those follows are honoured. Omitting it defaults to
        Free, so a call site that forgets it withholds rather than over-serves.
        """
        # Tier is part of the key: the same user's feed differs by plan, and a shared entry
        # would serve one plan's feed to the other for up to 10 minutes across an upgrade.
        cache_key = f"activity:{user_id}:{normalize_tier(tier)}"
        cached = _cache_get(
            _whale_activity_cache, cache_key, WHALE_ACTIVITY_CACHE_TTL
        )
        if cached is not None:
            return cached

        sb = get_supabase()

        try:
            return await self._build_activity_feed(sb, cache_key, user_id, tier)
        except Exception as e:
            # This method had NO error boundary at all, so a transient Supabase 520
            # (a documented failure mode here) surfaced as an unhandled 500 on the
            # Whales tab. Re-raised deliberately — the tab must not silently render an
            # empty feed, which reads as "none of the investors you follow have traded".
            logger.error(
                "[whale_activity] Feed build failed for user=%s tier=%s: %s: %s",
                user_id, normalize_tier(tier), type(e).__name__, e, exc_info=True,
            )
            raise

    async def _build_activity_feed(
        self, sb, cache_key: str, user_id: str, tier: Optional[str]
    ) -> List[WhaleTradeGroupActivityResponse]:
        """The uncached body of `get_whale_activity_feed`. See its docstring."""
        # Get followed whale IDs
        follows = (
            sb.table("whale_follows")
            .select("whale_id")
            .eq("user_id", user_id)
            .limit(_ROSTER_PAGE)
            .execute()
        )
        whale_ids = [f["whale_id"] for f in (follows.data or [])]

        # Free is one whale, wherever the count is shown — not just on the Follow button.
        # TRUNCATE, never destroy: rows the plan doesn't cover (from before this gate, or
        # after a downgrade) stay in `whale_follows` and come straight back on upgrade.
        # Same rule Updates applies to watchlist tickers.
        limit = whale_follow_limit(tier)
        if limit is not None and len(whale_ids) > limit:
            free_id = free_tier_whale_id(sb)
            if normalize_tier(tier) == TIER_FREE:
                whale_ids = [w for w in whale_ids if str(w) == str(free_id)]
            else:
                # Pro over its cap (a downgrade from Max): keep a DETERMINISTIC subset so
                # the feed doesn't reshuffle between requests.
                whale_ids = sorted(str(w) for w in whale_ids)[:limit]

        if not whale_ids:
            # Cached like any other answer. Without this a user who follows nobody
            # re-queried `whale_follows` on EVERY request — the cheapest possible
            # response was the only uncached one.
            _cache_set(_whale_activity_cache, cache_key, [])
            return []

        # Fetch trade groups for followed whales, ordered by FILING date (not
        # insertion time). `date` is "YYYY-MM-DD" text, so desc == chronological
        # desc; ordering by created_at instead let a group hydrated later sort
        # ahead of a newer-dated one, and iOS buckets sections by consecutive
        # equal dates — so out-of-order rows produced repeated/misplaced date
        # headers. This matches the profile path (which also orders by date).
        trade_groups = (
            sb.table("whale_trade_groups")
            .select("*")
            .in_("whale_id", [str(w) for w in whale_ids])
            .order("date", desc=True)
            .limit(_ACTIVITY_FEED_PAGE)
            .execute()
        )

        # Fetch whale names
        whales = (
            sb.table("whales")
            .select("id, name, avatar_url, category, firm_name")
            .in_("id", [str(w) for w in whale_ids])
            .limit(_ROSTER_PAGE)
            .execute()
        )
        whale_map = {
            str(w["id"]): w for w in (whales.data or [])
        }

        response = []
        for tg in trade_groups.data or []:
            whale = whale_map.get(str(tg["whale_id"]), {})
            response.append(
                WhaleTradeGroupActivityResponse(
                    id=str(tg["id"]),
                    whale_id=str(tg["whale_id"]),
                    # `or`-form, NOT .get defaults: avatar_url is NULL for most
                    # whales (sync/hydration never write it) — the key EXISTS
                    # with None, .get's "" default is dead code, and None fails
                    # the `str` field → one followed avatar-less whale 500'd
                    # the ENTIRE activity feed.
                    entity_name=whale.get("name") or "Unknown",
                    entity_avatar_name=whale.get("avatar_url") or "",
                    entity_firm_name=(whale.get("firm_name") or "").strip() or None,
                    category=whale.get("category"),
                    # `or`-form for these three too. They are NOT NULL today, but the
                    # comment above exists because exactly this shape 500'd the feed
                    # once already; leaving three `.get(k, default)` calls beside it is
                    # leaving the trap armed.
                    action=tg.get("net_action") or "BOUGHT",
                    trade_count=tg.get("trade_count") or 0,
                    total_amount=_format_amount(
                        _finite_float(tg.get("net_amount")),
                        tg.get("net_action") or "BOUGHT",
                    ),
                    summary=tg.get("summary"),
                    date=tg.get("date") or "",
                )
            )

        _cache_set(_whale_activity_cache, cache_key, response)
        return response

    async def get_trade_groups(
        self, whale_id: str
    ) -> List[WhaleTradeGroupResponse]:
        """Get all trade groups for a whale, newest FILING first.

        Ordered by ``date``, matching `_build_whale_profile` and
        `get_whale_activity_feed`. It used to order by ``created_at`` — the row's
        INGESTION time — so a backfill or a re-hydration reshuffled this list relative
        to the very same groups on the profile screen: the same whale, two orders.

        The trades are fetched in ONE query for all groups rather than one per group.
        The old shape issued up to 21 SEQUENTIAL calls through the SYNCHRONOUS Supabase
        client from inside an ``async def``, blocking the event loop for every one of
        them (CLAUDE.md: never block the loop with sync I/O).
        """
        sb = get_supabase()
        try:
            result = (
                sb.table("whale_trade_groups")
                .select("*")
                .eq("whale_id", whale_id)
                .order("date", desc=True)
                .limit(_TRADE_GROUPS_PAGE)
                .execute()
            )
            rows = result.data or []
            if not rows:
                return []
            return self._assemble_groups_with_trades(sb, rows)
        except Exception as e:
            logger.error(
                "[whale_trade_groups] Failed for whale_id=%s: %s: %s",
                whale_id, type(e).__name__, e, exc_info=True,
            )
            raise

    def _assemble_groups_with_trades(
        self, sb, group_rows: List[Dict[str, Any]]
    ) -> List[WhaleTradeGroupResponse]:
        """Attach trades to ``group_rows`` using ONE ``in_`` query, preserving order.

        Shared by `get_trade_groups` and `_build_whale_profile` so the N+1 cannot creep
        back into one of them. Trades are re-sorted by amount IN PYTHON because a single
        multi-group query can only carry one global ordering.
        """
        group_ids = [str(g["id"]) for g in group_rows]
        by_group: Dict[str, List[Dict[str, Any]]] = {gid: [] for gid in group_ids}
        try:
            trades = (
                sb.table("whale_trades")
                .select("*")
                .in_("trade_group_id", group_ids)
                # Explicit cap: PostgREST silently truncates at 1000 rows, and a silent
                # truncation here would drop trades with no signal at all.
                .limit(_TRADES_PER_PAGE * max(1, len(group_ids)))
                .execute()
            ).data or []
        except Exception as e:
            logger.warning(
                "[whale_trade_groups] Trade fetch failed for %d group(s): %s: %s",
                len(group_ids), type(e).__name__, e,
            )
            trades = []
        for t in trades:
            bucket = by_group.get(str(t.get("trade_group_id")))
            if bucket is not None:
                bucket.append(t)

        out: List[WhaleTradeGroupResponse] = []
        for g in group_rows:
            gid = str(g["id"])
            rows = sorted(
                by_group.get(gid, []),
                key=lambda t: _finite_float(t.get("amount")),
                reverse=True,
            )[:_TRADES_PER_PAGE]
            out.append(
                self._assemble_group_response(
                    gid, g, self._build_trade_responses_from_db(rows)
                )
            )
        return out

    async def get_trade_group_detail(
        self, whale_id: str, group_id: str
    ) -> Optional[WhaleTradeGroupResponse]:
        """Get a single trade group with all trades.

        Scoped by BOTH ``id`` and ``whale_id`` so a group id cannot be read against a
        whale it does not belong to.

        A non-uuid ``group_id`` makes PostgREST raise ``22P02``; that used to escape as
        an unhandled 500. It is a bad path parameter, so it answers 404 like any other
        unknown group.
        """
        sb = get_supabase()
        if not _looks_like_uuid(group_id):
            logger.info(
                "[whale_trade_group] Malformed group_id=%r for whale=%s", group_id, whale_id
            )
            return None
        try:
            result = (
                sb.table("whale_trade_groups")
                .select("*")
                .eq("id", group_id)
                .eq("whale_id", whale_id)
                .execute()
            )
            if not result.data:
                return None
            tg = result.data[0]

            db_trades = (
                sb.table("whale_trades")
                .select("*")
                .eq("trade_group_id", group_id)
                .order("amount", desc=True)
                .limit(_TRADES_PER_PAGE)
                .execute()
            )

            return self._assemble_group_response(
                str(tg["id"]),
                tg,
                self._build_trade_responses_from_db(db_trades.data or []),
            )
        except Exception as e:
            logger.error(
                "[whale_trade_group] Failed for whale_id=%s group_id=%s: %s: %s",
                whale_id, group_id, type(e).__name__, e, exc_info=True,
            )
            raise

    async def toggle_follow(
        self, user_id: str, whale_id: str, follow: bool, tier: Optional[str] = None
    ) -> FollowResponse:
        """Follow or unfollow a whale.

        ``tier`` gates FOLLOWING only. Unfollowing is never blocked at any tier — a user
        who is over a limit (after a downgrade, or from before this gate existed) must
        always be able to get back under it, and a locked unfollow button would strand them.
        """
        sb = get_supabase()

        try:
            if follow:
                already_followed = self._assert_may_follow(sb, user_id, whale_id, tier)
                sb.table("whale_follows").upsert(
                    {"user_id": user_id, "whale_id": whale_id},
                    on_conflict="user_id,whale_id",
                ).execute()
                if not already_followed:
                    # `_assert_may_follow` is SELECT-then-count-then-INSERT — a textbook
                    # TOCTOU. Two taps racing at 9/10 both read 9, both pass, and the user
                    # ends at 11 follows on a 10-follow plan. There is no DB-level count
                    # constraint to lean on, so the write is confirmed AFTER the fact and
                    # compensated: whoever loses the race has their own row removed and
                    # gets the same paywall they would have got serially.
                    self._compensate_if_over_limit(sb, user_id, whale_id, tier)
            else:
                sb.table("whale_follows").delete().eq(
                    "user_id", user_id
                ).eq("whale_id", whale_id).execute()
        except WhaleFollowLockedException:
            raise                                  # the endpoint maps this to a paywall
        except Exception as e:
            # Had no boundary at all, so a transient Supabase failure reached the client
            # as a bare 500 and iOS reverted the pill with a generic message.
            logger.error(
                "[whale_follow] %s failed for user=%s whale=%s: %s: %s",
                "follow" if follow else "unfollow",
                user_id, whale_id, type(e).__name__, e, exc_info=True,
            )
            raise

        # OBSERVE the resulting state rather than asserting the intent. `is_following`
        # used to echo the `follow` argument, so a write that RLS filtered out — or one
        # the DB rejected without raising — told the client it had succeeded, and the
        # client persisted that lie to disk.
        is_following = follow
        count = 0
        try:
            confirm = (
                sb.table("whale_follows")
                .select("whale_id")
                .eq("user_id", user_id)
                .eq("whale_id", whale_id)
                .limit(1)
                .execute()
            )
            is_following = bool(confirm.data)
            whale = (
                sb.table("whales")
                .select("followers_count")
                .eq("id", whale_id)
                .limit(1)
                .execute()
            )
            count = (whale.data[0].get("followers_count") or 0) if whale.data else 0
        except Exception as e:
            # Non-fatal: the write above already landed. Report the intent rather than
            # failing the whole mutation, but say so loudly — a silent degradation here
            # is how a wrong follower count becomes unexplainable later.
            logger.warning(
                "[whale_follow] Post-write read-back failed for user=%s whale=%s "
                "(%s: %s) — reporting intent",
                user_id, whale_id, type(e).__name__, e,
            )

        _invalidate_follow_caches(user_id, whale_id)

        return FollowResponse(is_following=is_following, followers_count=count)

    def _assert_may_follow(
        self, sb, user_id: str, whale_id: str, tier: Optional[str]
    ) -> bool:
        """Raise WhaleFollowLockedException unless this tier may track this whale.

        Returns True when the caller ALREADY follows this whale, which is always allowed
        — the upsert is idempotent, so refusing it would turn a double-tap into a
        paywall. The caller uses the return value to skip the post-write limit check,
        since an idempotent re-follow cannot push anyone over a cap.
        """
        limit = whale_follow_limit(tier)
        if limit is None:
            return False                            # Max — unlimited

        required = required_tier_for_whales(tier) or TIER_PRO

        # Free: one designated whale, not one of the caller's choosing.
        if normalize_tier(tier) == TIER_FREE:
            free_id = free_tier_whale_id(sb)
            # Unresolvable free whale → fail CLOSED. An open failure here would hand every
            # free account unlimited follows the moment a registry sync lagged.
            if free_id is None or str(whale_id) != str(free_id):
                raise WhaleFollowLockedException(
                    required, limit,
                    f"Free tier may only follow {FREE_TIER_WHALE_NAME}",
                )
            # Free's allowance is one SPECIFIC whale, so identity is the whole check and
            # a count cannot be exceeded. Report whether the row already exists purely so
            # the caller can skip a pointless post-write count.
            return self._is_following(sb, user_id, whale_id)

        existing = (
            sb.table("whale_follows")
            .select("whale_id")
            .eq("user_id", user_id)
            .limit(_ROSTER_PAGE)
            .execute()
        )
        followed = {str(row["whale_id"]) for row in (existing.data or [])}
        if str(whale_id) in followed:
            return True                             # idempotent re-follow
        if len(followed) >= limit:
            raise WhaleFollowLockedException(
                required, limit,
                f"Follow limit reached ({len(followed)}/{limit})",
            )
        return False

    @staticmethod
    def _is_following(sb, user_id: str, whale_id: str) -> bool:
        """Does this row already exist? Read-only; never raises for a missing row."""
        try:
            existing = (
                sb.table("whale_follows")
                .select("whale_id")
                .eq("user_id", user_id)
                .eq("whale_id", whale_id)
                .limit(1)
                .execute()
            )
            return bool(existing.data)
        except Exception as e:
            logger.warning(
                "[whale_follow] Existence check failed for user=%s whale=%s: %s: %s",
                user_id, whale_id, type(e).__name__, e,
            )
            return False

    def _compensate_if_over_limit(
        self, sb, user_id: str, whale_id: str, tier: Optional[str]
    ) -> None:
        """Undo THIS follow if the post-write count exceeds the plan's allowance.

        The compensation is deliberately scoped to the row we just wrote: it is the only
        one this request is responsible for, and deleting somebody's older follow to make
        room would destroy state the user never asked to lose ("truncate, never destroy").
        """
        limit = whale_follow_limit(tier)
        if limit is None or normalize_tier(tier) == TIER_FREE:
            return
        try:
            after = (
                sb.table("whale_follows")
                .select("whale_id")
                .eq("user_id", user_id)
                .limit(_ROSTER_PAGE)
                .execute()
            )
            total = len(after.data or [])
        except Exception as e:
            logger.warning(
                "[whale_follow] Post-write count failed for user=%s: %s: %s — "
                "leaving the follow in place",
                user_id, type(e).__name__, e,
            )
            return
        if total <= limit:
            return
        logger.info(
            "[whale_follow] Concurrent follow raced past the cap for user=%s "
            "(%d/%d) — rolling back whale=%s",
            user_id, total, limit, whale_id,
        )
        try:
            sb.table("whale_follows").delete().eq(
                "user_id", user_id
            ).eq("whale_id", whale_id).execute()
        except Exception as e:
            logger.error(
                "[whale_follow] Rollback FAILED for user=%s whale=%s: %s: %s — "
                "the account is over its follow limit",
                user_id, whale_id, type(e).__name__, e, exc_info=True,
            )
        raise WhaleFollowLockedException(
            required_tier_for_whales(tier) or TIER_PRO, limit,
            f"Follow limit reached ({limit}/{limit})",
        )

    async def get_whale_alerts(
        self, user_id: Optional[str] = None
    ) -> Optional[WhaleAlertBannerResponse]:
        """Get the most recent active, UNEXPIRED whale alert, optionally scoped
        to followed whales.

        BOTH the followed-whale and global paths apply the same expiry check.
        Previously the followed path returned an alert with NO expiry check (so
        an is_active row whose expires_at had passed was shown) AND, by
        returning early, suppressed the valid global fallback. Each path now
        scans a small window and returns the first non-expired alert.
        """
        sb = get_supabase()

        def _to_banner(alert: Dict) -> WhaleAlertBannerResponse:
            return WhaleAlertBannerResponse(
                id=str(alert["id"]),
                title=alert["title"],
                description=alert["description"],
                ticker=alert.get("ticker"),
                action_title=alert.get("action_title", "View Full Alert"),
            )

        try:
            # Prefer a valid alert from a followed whale.
            if user_id:
                follows = (
                    sb.table("whale_follows")
                    .select("whale_id")
                    .eq("user_id", user_id)
                    .execute()
                )
                whale_ids = [f["whale_id"] for f in (follows.data or [])]
                if whale_ids:
                    followed_result = (
                        sb.table("whale_alerts")
                        .select("*")
                        # Expiry filtered IN THE QUERY. Taking the newest 5 and then
                        # filtering in Python meant that if all 5 happened to be expired,
                        # a perfectly valid 6th was invisible — a hard 5-row window
                        # masquerading as an expiry check. `expires_at IS NULL` means
                        # "never expires" and must still qualify.
                        .or_(f"expires_at.is.null,expires_at.gt.{_now_iso()}")
                        .eq("is_active", True)
                        .in_("whale_id", whale_ids)
                        .order("created_at", desc=True)
                        .limit(5)
                        .execute()
                    )
                    for alert in followed_result.data or []:
                        if not _alert_is_expired(alert):
                            return _to_banner(alert)
                    # All followed alerts expired → fall through to global.

            # Fallback: the most recent active, unexpired global alert. Scanning a
            # small window (not limit(1)) means a stale expired latest no longer
            # suppresses a still-valid older one.
            result = (
                sb.table("whale_alerts")
                .select("*")
                .or_(f"expires_at.is.null,expires_at.gt.{_now_iso()}")
                .eq("is_active", True)
                .order("created_at", desc=True)
                .limit(5)
                .execute()
            )
            for alert in result.data or []:
                if not _alert_is_expired(alert):
                    return _to_banner(alert)
        except Exception as e:
            logger.warning("Failed to fetch whale alerts: %s", e)

        return None

    # ── Dual-Source Router ───────────────────────────────────────────

    async def _get_or_process_latest(
        self, whale_id: str, whale: Dict[str, Any]
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """Route to correct FMP source based on data_source column.

        Prefers pre-hydrated snapshots when available (set by
        scripts/hydrate_whales.py). Falls through to live FMP
        processing only if no snapshot exists.

        Returns ``(snapshot, served_from_storage)``. The flag is what tells the caller
        whether a BUILD ran — and therefore whether `_sync_to_whale_tables` may have
        rewritten the `whales` row underneath it.

        ⚠️ The caller cannot infer this from `last_hydrated_at`: that column is written
        ONLY by `scripts/hydrate_whales.py`, never by `_sync_to_whale_tables`, so it
        answers "has the nightly job ever run for this whale", not "did a sync just run".
        Nor can it infer it from `snapshot is not None`, which is true on the
        served-from-storage path too and would re-instate the round-trip on the common
        55-of-56 route.
        """
        # Prefer pre-hydrated snapshot if the hydration engine has run
        if whale.get("last_hydrated_at"):
            snapshot = await self._read_from_supabase(whale_id)
            if snapshot:
                return snapshot, True

        data_source = whale.get("data_source", "manual")

        try:
            if data_source == "13f":
                return (
                    await self._process_13f_path(whale_id, whale["cik"], whale=whale),
                    False,
                )
            elif data_source in ("congressional_house", "congressional_senate"):
                chamber = "house" if "house" in data_source else "senate"
                return (
                    await self._process_congressional_path(
                        whale_id, whale["fmp_name"], chamber
                    ),
                    False,
                )
            else:
                # No build ran — this is a plain storage read.
                return await self._read_from_supabase(whale_id), True
        except Exception as e:
            logger.error(
                "Failed to process whale %s (source=%s): %s",
                whale_id,
                data_source,
                e,
            )
            try:
                # A build was ATTEMPTED and may have partially synced before failing, so
                # this is not "served from storage" for the caller's purposes.
                return await self._read_from_supabase(whale_id), False
            except Exception as fallback_err:
                logger.error(
                    "Supabase fallback also failed for whale %s: %s",
                    whale_id,
                    fallback_err,
                )
                return None, False

    # ── 13F Processing Path ──────────────────────────────────────────

    async def _process_13f_path(
        self, whale_id: str, cik: str, whale: Optional[Dict] = None
    ) -> Optional[Dict[str, Any]]:
        """Fetch and aggregate 13F institutional data."""
        sb = get_supabase()

        # Step 1: Get available filing dates (cached 24h — changes quarterly)
        fd_key = f"filing_dates:{cik}"
        filing_dates = _cache_get(
            _filing_dates_cache, fd_key, FILING_DATES_CACHE_TTL
        )
        if filing_dates is None:
            filing_dates = await self.fmp.get_institutional_filing_dates(cik)
            if filing_dates:
                _cache_set(_filing_dates_cache, fd_key, filing_dates)
        if not filing_dates:
            logger.warning("No 13F filing dates for CIK %s", cik)
            return await self._read_from_supabase(whale_id)

        # Determine latest filing
        latest = filing_dates[0]
        year = int(latest.get("year") or latest.get("date", "2025")[:4])
        quarter = int(latest.get("quarter", 1))
        period = f"{year}-Q{quarter}"
        filing_date = latest.get("date", f"{year}-{quarter * 3:02d}-30")

        # Step 2: Check Supabase cache
        existing = (
            sb.table("whale_filing_snapshots")
            .select("*")
            .eq("whale_id", whale_id)
            .eq("filing_period", period)
            .execute()
        )
        if existing.data:
            return existing.data[0]

        # Step 3: Fetch current + previous quarter concurrently
        prev = _find_previous_quarter(filing_dates, year, quarter)

        current_task = self.fmp.get_institutional_holdings(cik, year, quarter)
        prev_task = (
            self.fmp.get_institutional_holdings(
                cik, int(prev["year"]), int(prev["quarter"])
            )
            if prev
            else _noop_list()
        )
        industry_task = self.fmp.get_institutional_industry_breakdown(
            cik, year=year, quarter=quarter
        )
        perf_task = self.fmp.get_institutional_performance(cik)

        results = await asyncio.gather(
            current_task, prev_task, industry_task, perf_task,
            return_exceptions=True,
        )

        current_raw = results[0] if not isinstance(results[0], BaseException) else []
        prev_raw = results[1] if not isinstance(results[1], BaseException) else []
        industry_data = results[2] if not isinstance(results[2], BaseException) else []
        perf_data_list = results[3] if not isinstance(results[3], BaseException) else []
        perf_data = perf_data_list[0] if perf_data_list else {}

        for idx, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.error("13F fetch section %d failed: %s", idx, r)

        if not current_raw:
            return await self._read_from_supabase(whale_id)

        # Step 4: Build aggregated data
        holdings_data = self._build_holdings(current_raw)
        total_value = sum(h["value"] for h in holdings_data)

        # Compute change_percent from previous quarter
        if prev_raw:
            holdings_data = self._apply_change_percent(holdings_data, prev_raw)

        # Build sectors from FMP industry breakdown
        sector_data = self._build_sectors_from_industry(industry_data)

        # Single enrichment pass: logos + names + sectors (if missing)
        # Uses company_profile_cache table (7-day TTL) to avoid redundant FMP calls
        holdings_data, fallback_sectors = await self._enrich_from_profiles(
            holdings_data, need_sectors=(not sector_data),
        )
        if not sector_data and fallback_sectors:
            sector_data = fallback_sectors

        # Fetch stock-split ratios ONLY for tickers whose share count jumped
        # like a split (value ~preserved) — bounds FMP /splits calls to the rare
        # suspicious holdings instead of every position. Without this, a
        # held-through-split position (e.g. a 10:1) fabricates a huge BOUGHT
        # trade in the diff below.
        # Best-effort refinement: never let a splits lookup failure abort 13F
        # processing (which would degrade to a stale snapshot). Any error here
        # just leaves split_ratios empty → the raw diff, same as before.
        split_ratios: Dict[str, float] = {}
        try:
            suspects = self._suspicious_split_tickers(current_raw, prev_raw)
            if suspects:
                prev_end = (
                    _quarter_end_date(int(prev["year"]), int(prev["quarter"]))
                    if prev else None
                )
                curr_end = _quarter_end_date(year, quarter)
                split_lists = await asyncio.gather(
                    *[self.fmp.get_stock_splits(t) for t in suspects],
                    return_exceptions=True,
                )
                for t, sl in zip(suspects, split_lists):
                    if isinstance(sl, BaseException):
                        logger.warning("Split lookup failed for %s: %s", t, sl)
                        continue
                    r = _split_ratio_in_window(sl, prev_end, curr_end)
                    if r and r != 1.0:
                        split_ratios[t] = r
        except Exception as e:
            logger.warning("Split adjustment skipped for CIK %s: %s", cik, e)
            split_ratios = {}

        trade_group = self._diff_quarters(
            current_raw, prev_raw, filing_date, total_value, split_ratios
        )
        behavior = self._generate_behavior_summary(trade_group, sector_data)
        sentiment = self._generate_sentiment_summary(
            holdings_data, trade_group, sector_data
        )

        raw_hash = hashlib.sha256(
            json.dumps(current_raw, sort_keys=True, default=str).encode()
        ).hexdigest()

        # Step 5: Persist
        snapshot = {
            "whale_id": whale_id,
            "filing_period": period,
            "filing_date": filing_date,
            "total_value": total_value,
            "holdings_data": holdings_data,
            "sector_data": sector_data,
            "trade_group": trade_group,
            "behavior_summary": behavior,
            "sentiment_text": sentiment,
            "raw_hash": raw_hash,
        }

        try:
            sb.table("whale_filing_snapshots").upsert(
                snapshot, on_conflict="whale_id,filing_period"
            ).execute()
        except Exception as e:
            logger.error("Failed to persist filing snapshot: %s", e)

        # Sync to denormalized tables
        await self._sync_to_whale_tables(
            whale_id, holdings_data, sector_data,
            [trade_group] if trade_group else [],
            behavior, sentiment, total_value, perf_data_list,
            whale=whale,
        )

        return snapshot

    # ── Congressional Processing Path ────────────────────────────────

    async def _process_congressional_path(
        self, whale_id: str, fmp_name: str, chamber: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch and aggregate congressional trading data."""
        sb = get_supabase()

        # Use monthly periods for congressional data
        now = datetime.now(timezone.utc)
        period = now.strftime("%Y-%m")

        # Check cache
        existing = (
            sb.table("whale_filing_snapshots")
            .select("*")
            .eq("whale_id", whale_id)
            .eq("filing_period", period)
            .execute()
        )
        if existing.data:
            return existing.data[0]

        # Fetch trades from FMP
        if chamber == "senate":
            raw_trades = await self.fmp.get_senate_trades_by_name(fmp_name)
        else:
            raw_trades = await self.fmp.get_house_trades_by_name(fmp_name)

        if not raw_trades:
            return await self._read_from_supabase(whale_id)

        # Aggregate trades → LIST of per-disclosure-filing groups
        holdings_data, trade_groups, sector_data = (
            self._aggregate_congressional_trades(raw_trades, now.isoformat()[:10])
        )
        primary_group = trade_groups[0] if trade_groups else None
        total_value = sum(h.get("value", 0) for h in holdings_data)

        # Single enrichment pass: logos + names + sectors
        holdings_data, enriched_sectors = await self._enrich_from_profiles(
            holdings_data, need_sectors=(not sector_data),
        )
        if not sector_data and enriched_sectors:
            sector_data = enriched_sectors

        behavior = self._generate_behavior_summary(primary_group, sector_data)
        sentiment = self._generate_sentiment_summary(
            holdings_data, primary_group, sector_data, is_congress=True
        )

        raw_hash = hashlib.sha256(
            json.dumps(raw_trades[:50], sort_keys=True, default=str).encode()
        ).hexdigest()

        snapshot = {
            "whale_id": whale_id,
            "filing_period": period,
            "filing_date": now.isoformat()[:10],
            "total_value": total_value,
            "holdings_data": holdings_data,
            "sector_data": sector_data,
            "trade_group": primary_group,   # backward-compat (most recent)
            "trade_groups": trade_groups,   # full per-filing timeline (in-memory only)
            "behavior_summary": behavior,
            "sentiment_text": sentiment,
            "raw_hash": raw_hash,
        }

        try:
            # `trade_groups` is NOT a column on whale_filing_snapshots — it lives
            # only in-memory (used by _build_whale_profile for the immediate live
            # render and synced to the whale_trade_groups table below). Sending it
            # in the upsert would make PostgREST reject the whole row (PGRST204),
            # silently killing the snapshot cache tier. Strip it for the DB write.
            sb.table("whale_filing_snapshots").upsert(
                snapshot_db_row(snapshot), on_conflict="whale_id,filing_period"
            ).execute()
        except Exception as e:
            logger.exception(
                "Failed to persist congressional snapshot whale_id=%s period=%s: %s: %s",
                whale_id, period, type(e).__name__, e,
            )

        await self._sync_to_whale_tables(
            whale_id, holdings_data, sector_data, trade_groups,
            behavior, sentiment, total_value, {},
        )

        return snapshot

    # ── Fallback: Read from Supabase ─────────────────────────────────

    async def _read_from_supabase(
        self, whale_id: str
    ) -> Optional[Dict[str, Any]]:
        """Read the most recent snapshot from Supabase, or return None."""
        try:
            sb = get_supabase()
            result = (
                sb.table("whale_filing_snapshots")
                .select("*")
                .eq("whale_id", whale_id)
                .order("processed_at", desc=True)
                .limit(1)
                .execute()
            )
            return result.data[0] if result.data else None
        except Exception as e:
            logger.error(
                "Failed to read snapshot from Supabase for whale %s: %s",
                whale_id,
                e,
            )
            return None

    # ── Quarter Diffing (13F) ────────────────────────────────────────

    @staticmethod
    def _suspicious_split_tickers(
        current_raw: List[Dict], previous_raw: List[Dict]
    ) -> List[str]:
        """Tickers whose share count jumped like a split — the only holdings
        worth a FMP ``/splits`` lookup (keeps the calls bounded).

        A split moves shares up (or down, reverse-split) by a factor while the
        per-share price moves INVERSELY by ~the same factor, so the position
        VALUE is roughly preserved. A genuine large buy/sell instead changes the
        value proportionally, leaving the price ~flat — so it won't be flagged
        (and even a flagged ticker is only *confirmed* against real split data).
        """
        def _map(raw: List[Dict]) -> Dict[str, Tuple[float, float]]:
            m: Dict[str, Tuple[float, float]] = {}
            for h in raw or []:
                sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
                if not sym or sym == "--":
                    continue
                m[sym] = (
                    _finite_float(h.get("value")),
                    _finite_float(h.get("sharesNumber") or h.get("shares")),
                )
            return m

        cur = _map(current_raw)
        prev = _map(previous_raw)
        suspects: List[str] = []
        for sym in set(cur) & set(prev):
            cv, cs = cur[sym]
            pv, ps = prev[sym]
            if cs <= 0 or ps <= 0 or cv <= 0 or pv <= 0:
                continue
            share_ratio = cs / ps
            if 0.7 < share_ratio < 1.4:
                continue  # share count barely moved → not a split
            cur_price = cv / cs
            prev_price = pv / ps
            if cur_price <= 0 or prev_price <= 0:
                continue
            price_ratio = prev_price / cur_price
            # shares & price moved inversely by ~the same factor → value
            # ~preserved → smells like a split; confirm against real /splits.
            if abs(share_ratio - price_ratio) <= 0.35 * share_ratio:
                suspects.append(sym)
        return suspects

    def _diff_quarters(
        self,
        current_raw: List[Dict],
        previous_raw: List[Dict],
        filing_date: str,
        total_current_value: float,
        split_ratios: Optional[Dict[str, float]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Diff two 13F snapshots to compute individual trades.

        ``split_ratios`` maps ticker → product of stock-split ratios that took
        effect between the two filings (see ``_process_13f_path``). FMP reports
        RAW, unadjusted 13F share counts, so a 10:1 split makes a
        held-unchanged position look like a huge BOUGHT trade
        (``shares_change × implied_price``). We restate the previous quarter's
        shares onto the post-split basis before diffing — mirroring
        ``holders_service._compute_quarter_flow`` — so a split is not fabricated
        into a trade. Non-finite FMP tokens (NaN/Inf) coerce to 0.
        """
        if not current_raw:
            return None
        split_ratios = split_ratios or {}

        # Build ticker maps
        current_map: Dict[str, Dict] = {}
        for h in current_raw:
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if not sym or sym == "--":
                continue
            current_map[sym] = {
                "symbol": sym,
                "name": h.get("securityName") or h.get("companyName") or sym,
                "value": _finite_float(h.get("value")),
                "shares": int(_finite_float(h.get("sharesNumber") or h.get("shares"))),
            }

        prev_map: Dict[str, Dict] = {}
        prev_total = 0.0
        for h in previous_raw:
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if not sym or sym == "--":
                continue
            val = _finite_float(h.get("value"))
            prev_map[sym] = {
                "symbol": sym,
                "name": h.get("securityName") or h.get("companyName") or sym,
                "value": val,
                "shares": int(_finite_float(h.get("sharesNumber") or h.get("shares"))),
            }
            prev_total += val

        if not current_map:
            return None

        trades = []
        total_bought = 0.0
        total_sold = 0.0

        all_tickers = set(current_map.keys()) | set(prev_map.keys())

        for ticker in all_tickers:
            curr = current_map.get(ticker)
            prev = prev_map.get(ticker)

            curr_val = float(curr["value"]) if curr else 0.0
            prev_val = float(prev["value"]) if prev else 0.0
            curr_shares = float(curr["shares"]) if curr else 0.0
            prev_shares = float(prev["shares"]) if prev else 0.0

            # Split restatement (mirrors holders_service._compute_quarter_flow):
            # a split inflates the RAW share-count delta. Restate the previous
            # quarter onto the post-split basis so a held-through-split position
            # doesn't fabricate a large BOUGHT/Increased trade.
            ratio = split_ratios.get(ticker, 1.0)
            if ratio and ratio != 1.0 and curr and prev and prev_shares > 0:
                ratio_obs = curr_shares / prev_shares
                if abs(ratio_obs - ratio) <= 0.15 * ratio:
                    # Clean split signature → restate; residual is the real flow.
                    prev_shares = prev_shares * ratio
                elif ratio_obs >= (1.0 + ratio) / 2.0:
                    # Split + large concurrent real flow, inseparable → suppress
                    # rather than fabricate a wrong-sign trade ("no bar, not
                    # garbage"). Keeps the position out of the trade list.
                    continue
                # else: count didn't jump toward the split (ratio_obs ≈ 1.0) →
                # spinoff / ADR-ratio / already-adjusted; keep the raw diff.

            # Shared 13F formula — shares_change × implied_price. Keeps
            # Supabase whale_trades.amount aligned with what TickerDetailView's
            # Institutional Activities section shows for the same holding,
            # and strips out stock-price appreciation (which could otherwise
            # flip the action between BOUGHT and SOLD).
            action, amount = calc_13f_trade_dollars(
                curr_shares=curr_shares,
                curr_value=curr_val,
                prev_shares=prev_shares,
                prev_value=prev_val,
                min_amount=1_000.0,
            )
            if action is None:
                continue

            prev_alloc = (
                (prev_val / prev_total * 100)
                if prev_total > 0 and prev_val > 0
                else 0
            )
            new_alloc = (
                (curr_val / total_current_value * 100)
                if total_current_value > 0 and curr_val > 0
                else 0
            )
            name = (curr or prev)["name"]

            if prev is None and curr is not None:
                trade_type = "New"
            elif curr is None and prev is not None:
                trade_type = "Closed"
            elif action == "BOUGHT":
                trade_type = "Increased"
            else:
                trade_type = "Decreased"

            trades.append({
                "ticker": ticker,
                "company_name": name,
                "action": action,
                "trade_type": trade_type,
                "amount": amount,
                "previous_allocation": round(prev_alloc, 2),
                "new_allocation": round(new_alloc, 2),
                "date": filing_date,
            })
            if action == "BOUGHT":
                total_bought += amount
            else:
                total_sold += amount

        if not trades:
            return None

        trades.sort(key=lambda t: t["amount"], reverse=True)

        net_dollar = total_bought - total_sold
        net_action = "BOUGHT" if net_dollar >= 0 else "SOLD"
        net_amount = abs(net_dollar)

        new_count = sum(1 for t in trades if t["trade_type"] == "New")
        closed_count = sum(1 for t in trades if t["trade_type"] == "Closed")

        summary = self._generate_trade_group_summary(
            trades, new_count, closed_count, net_action
        )
        insights = self._generate_trade_group_insights(
            trades, new_count, closed_count, total_bought, total_sold
        )

        return {
            "date": filing_date,
            "trade_count": len(trades),
            "net_action": net_action,
            "net_amount": net_amount,
            "summary": summary,
            "insights": insights,
            "trades": trades[:50],
        }

    # ── Congressional Aggregation ────────────────────────────────────

    def _aggregate_congressional_trades(
        self,
        raw_trades: List[Dict],
        as_of_date: str,
    ) -> Tuple[List[Dict], List[Dict], List[Dict]]:
        """Aggregate congressional trades into holdings + trade groups + sectors.

        Uses a chronological walk (oldest → newest) to reconstruct
        `previous_allocation` and `new_allocation` at each trade. This is a
        directional estimate, NOT a live portfolio:

        - Starting portfolio assumed empty (STOCK Act has no pre-disclosure baseline).
        - Dollar values use bucket midpoints — absolute amounts drift 20–40% per trade.
        - No market-price adjustment between disclosures (trade-flow only).
        - Sales exceeding known holdings clamp to zero (no negative allocations).

        The raw STOCK Act bucket string (e.g. ``"$1,001 - $15,000"``) is
        preserved on each trade as ``amount_range`` (with parsed ``amount_low`` /
        ``amount_high`` bounds) so the UI can display the honest range instead
        of the fabricated-precision midpoint.

        Trades are grouped **by disclosure filing** (`disclosureDate`), not by
        an ``as_of``/``now`` stamp — congressional trades are disclosed on a
        30–45 day STOCK Act lag, so each returned group is dated by when it
        became public. Returns a LIST of groups (most-recent filing first) so
        the "Recent Trades" timeline reflects real, stable, de-dupable dates
        rather than one aggregate re-stamped "today" on every hydration run.
        """
        full_sale_types = {"sale_full", "sale (full)"}

        # ── Pass 1: normalize + sort by date ────────────────────────────
        normalized: List[Dict] = []
        for t in raw_trades:
            symbol = (t.get("symbol") or "").upper().strip()
            if not symbol or symbol == "--" or symbol == "N/A":
                continue

            raw_type = (t.get("type") or "").lower().strip()
            action = resolve_congress_action(t.get("type"))
            if action is None:
                logger.warning(
                    "[whale_congress] Skipping trade with unrecognised type=%r "
                    "(symbol=%s) — refusing to guess a direction",
                    t.get("type"), symbol,
                )
                continue
            amount_range = t.get("amount") or "$1,001 - $15,000"
            # Keep the REAL parsed amount (0.0 when FMP's bucket is unparseable,
            # e.g. "$50,000,000+") — do NOT fabricate an $8,000 midpoint. A
            # fabricated amount gets persisted to whale_trades.amount and summed
            # into the group net, understating a large disclosure by orders of
            # magnitude and making net_amount contradict net_amount_range. The
            # honest raw bucket string survives on `amount_range` for display.
            amount = parse_congress_amount_dollars(amount_range)
            amount_low, amount_high = parse_congress_amount_bounds(amount_range)

            # Every persisted / dedup-keyed group must carry a REAL date. A trade
            # with neither a transaction nor a disclosure date can't be placed on
            # a stable timeline; keying it to `as_of_date` (today) re-stamps the
            # group "today" on every hydration run, defeating UNIQUE(whale_id,
            # date). Drop it (mirrors the symbol filter above).
            raw_tx = t.get("transactionDate") or t.get("transaction_date")
            raw_disc = (
                t.get("disclosureDate")
                or t.get("disclosure_date")
                or t.get("dateRecieved")
                or t.get("dateReceived")
            )
            if not raw_tx and not raw_disc:
                continue
            tx_date = raw_tx or raw_disc
            disclosure_date = raw_disc or tx_date
            name = t.get("assetDescription") or t.get("asset_description") or symbol

            normalized.append({
                "ticker": symbol,
                "company_name": name,
                "action": action,
                "raw_type": raw_type,
                "amount": amount,
                "amount_range": amount_range,
                "amount_low": amount_low,
                "amount_high": amount_high,
                "date": tx_date,
                "disclosure_date": disclosure_date,
            })

        # Sort oldest → newest; stable preserves FMP order for same-day trades
        normalized.sort(key=lambda t: t["date"])

        # ── Pass 2: chronological walk with running portfolio ──────────
        running_portfolio: Dict[str, float] = {}
        trades: List[Dict] = []

        for t in normalized:
            symbol = t["ticker"]
            action = t["action"]
            raw_type = t["raw_type"]
            amount = t["amount"]

            # Position held BEFORE this trade. A full-sale Close zeroes it, so a
            # later re-buy sees prev_value == 0 and is correctly re-classified
            # "New" (the old seen_tickers set stayed populated after a Close and
            # mislabeled the re-open as "Increased").
            prev_value = running_portfolio.get(symbol, 0.0)

            # Trade type classification from the CURRENT position state.
            if action == "BOUGHT" and prev_value <= 0:
                trade_type = "New"
            elif action == "SOLD" and raw_type in full_sale_types:
                trade_type = "Closed"
            elif action == "BOUGHT":
                trade_type = "Increased"
            else:
                trade_type = "Decreased"

            # Allocation BEFORE applying this trade
            total_before = sum(running_portfolio.values())
            previous_allocation = (
                round(prev_value / total_before * 100, 2)
                if total_before > 0
                else 0.0
            )

            # Apply trade to running portfolio.
            if action == "BOUGHT":
                running_portfolio[symbol] = prev_value + amount
            elif raw_type in full_sale_types:
                # A full sale ("Closed") exits the ENTIRE position. Subtracting
                # the sale's bucket midpoint would leave a phantom residual (the
                # buy and full-sale bucket midpoints almost never match), so a
                # fully-exited ticker would linger in current holdings and skew
                # every allocation %. Zero it so "Closed" means closed.
                running_portfolio[symbol] = 0.0
            else:
                # Partial sale: clamp at zero on oversell (midpoint drift).
                running_portfolio[symbol] = max(prev_value - amount, 0.0)

            # Allocation AFTER applying this trade
            total_after = sum(running_portfolio.values())
            new_value = running_portfolio[symbol]
            new_allocation = (
                round(new_value / total_after * 100, 2)
                if total_after > 0
                else 0.0
            )

            trades.append({
                "ticker": symbol,
                "company_name": t["company_name"],
                "action": action,
                "trade_type": trade_type,
                "amount": amount,
                "amount_range": t["amount_range"],
                "amount_low": t["amount_low"],
                "amount_high": t["amount_high"],
                "previous_allocation": previous_allocation,
                "new_allocation": new_allocation,
                "date": t["date"],
                "disclosure_date": t["disclosure_date"],
            })

        # ── Build holdings from final running portfolio ─────────────────
        holdings = [
            {
                "ticker": sym,
                "company_name": next(
                    (t["company_name"] for t in trades if t["ticker"] == sym),
                    sym,
                ),
                "value": val,
                "allocation": 0,
                "change_percent": 0,
            }
            for sym, val in running_portfolio.items()
            if val > 0
        ]

        # Fallback: when no positive holdings (e.g. all sells), show
        # recently traded tickers so the profile isn't empty
        if not holdings and trades:
            traded: Dict[str, Dict] = {}
            for t in trades:
                ticker = t["ticker"]
                if ticker not in traded:
                    traded[ticker] = {
                        "ticker": ticker,
                        "company_name": t["company_name"],
                        "value": t["amount"],
                        "allocation": 0,
                        "change_percent": 0,
                    }
                else:
                    traded[ticker]["value"] += t["amount"]
            holdings = list(traded.values())

        total_value = sum(h["value"] for h in holdings) or 1
        for h in holdings:
            h["allocation"] = round(h["value"] / total_value * 100, 2)
        holdings.sort(key=lambda x: x["value"], reverse=True)

        # ── Group trades by disclosure filing → one group per disclosure ─
        by_disclosure: Dict[str, List[Dict]] = {}
        for t in trades:
            key = t.get("disclosure_date") or t.get("date") or as_of_date
            by_disclosure.setdefault(key, []).append(t)

        trade_groups: List[Dict] = []
        for disclosure_date, filing_trades in by_disclosure.items():
            group = self._build_congress_trade_group(filing_trades, disclosure_date)
            if group:
                trade_groups.append(group)

        # Most-recent disclosure first; keep a bounded, honest window
        trade_groups.sort(key=lambda g: g["date"], reverse=True)
        trade_groups = trade_groups[:12]

        # Sectors (basic — from holdings tickers)
        sectors: List[Dict] = []

        return holdings[:30], trade_groups, sectors

    def _build_congress_trade_group(
        self, filing_trades: List[Dict], disclosure_date: str
    ) -> Optional[Dict]:
        """Build ONE congressional trade group for a single disclosure filing.

        ``date`` is the disclosure date (stable dedup + sort key across
        hydration runs); ``transaction_date`` is when the trades actually
        happened. ``net_amount_range`` is the summed STOCK Act range of the
        trades in the net direction — the honest figure to display instead of
        the midpoint ``net_amount`` (kept for internal sort / behaviour only).
        """
        if not filing_trades:
            return None

        total_bought = sum(
            t["amount"] for t in filing_trades if t["action"] == "BOUGHT"
        )
        total_sold = sum(
            t["amount"] for t in filing_trades if t["action"] == "SOLD"
        )
        net_dollar = total_bought - total_sold
        net_action = "BOUGHT" if net_dollar >= 0 else "SOLD"

        new_count = sum(1 for t in filing_trades if t["trade_type"] == "New")
        closed_count = sum(1 for t in filing_trades if t["trade_type"] == "Closed")
        summary = self._generate_trade_group_summary(
            filing_trades, new_count, closed_count, net_action
        )

        # Summed honest range for the trades in the net direction. Emitted ONLY
        # for a single-directional filing (net_dollar != 0): a pure wash (buys
        # exactly offset sells) nets to 0 but would otherwise read "Net buying of
        # $X", overstating a zero-net filing. And when every bound is (0,0) (all
        # amounts unparseable) we emit no range rather than a fabricated "$0".
        net_amount_range = None
        if net_dollar != 0:
            direction_bounds = [
                (t.get("amount_low", 0.0), t.get("amount_high", 0.0))
                for t in filing_trades
                if t["action"] == net_action
            ]
            if direction_bounds:
                low, high = sum_amount_bounds(direction_bounds)
                if low > 0 or (high is not None and high > 0):
                    net_amount_range = format_amount_range(low, high)

        # Congress insights use the disclosed RANGE — never a precise dollar.
        insights: List[str] = []
        if net_amount_range:
            verb = "buying" if net_action == "BOUGHT" else "selling"
            insights.append(f"Net {verb} of {net_amount_range} (disclosed range)")
        new_tickers = [
            t["ticker"] for t in filing_trades if t["trade_type"] == "New"
        ][:3]
        if new_tickers:
            insights.append(f"New positions: {', '.join(new_tickers)}")
        closed_tickers = [
            t["ticker"] for t in filing_trades if t["trade_type"] == "Closed"
        ][:3]
        if closed_tickers:
            insights.append(f"Exited: {', '.join(closed_tickers)}")
        insights = insights[:4]

        transaction_date = max(
            (t.get("date", "") for t in filing_trades), default=""
        )

        return {
            "date": disclosure_date,
            "disclosure_date": disclosure_date,
            "transaction_date": transaction_date,
            "trade_count": len(filing_trades),
            "net_action": net_action,
            "net_amount": abs(net_dollar),
            "net_amount_range": net_amount_range,
            "summary": summary,
            "insights": insights,
            "trades": sorted(
                filing_trades, key=lambda t: t["amount"], reverse=True
            )[:50],
        }

    # ── Build Helpers ────────────────────────────────────────────────

    def _build_holdings(
        self, raw_holdings: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Transform raw FMP 13F holdings into UI shape.

        Dedups by RESOLVED ticker: FMP ``institutional-ownership/extract`` maps
        several CUSIP rows (common + options, dual listings) onto ONE symbol.
        Without merging, (a) the same issuer renders twice in Current Picks and
        (b) the denormalized ``whale_holdings`` insert hits its
        UNIQUE(whale_id, ticker) constraint and aborts the whole sync.

        The denominator sums POSITIVE values only — a stray negative/zero FMP
        value would otherwise deflate the total and push a legitimate holding's
        allocation past 100, violating the ``numeric(7,4)`` / CHECK(0..100)
        column. Non-finite FMP tokens (NaN/Inf) coerce to 0 via ``_finite_float``.
        """
        merged: Dict[str, Dict[str, Any]] = {}
        for h in raw_holdings:
            val = _finite_float(h.get("value"))
            if val <= 0:
                continue
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if not sym or sym == "--":
                continue
            shares = _finite_float(h.get("sharesNumber") or h.get("shares"))
            existing = merged.get(sym)
            if existing:
                existing["value"] += val
                existing["shares"] += shares
            else:
                merged[sym] = {
                    "ticker": sym,
                    "company_name": (
                        h.get("securityName") or h.get("companyName") or sym
                    ),
                    "logo_url": None,
                    "allocation": 0.0,
                    "change_percent": 0,
                    "value": val,
                    "shares": shares,
                }

        total = sum(h["value"] for h in merged.values())
        if total <= 0:
            return []

        holdings = list(merged.values())
        for h in holdings:
            h["allocation"] = round(h["value"] / total * 100, 2)
            h["shares"] = int(h["shares"])

        holdings.sort(key=lambda x: x["value"], reverse=True)
        return holdings

    def _build_sectors_from_industry(
        self, industry_data: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Build sector allocation from FMP industry breakdown.

        Maps ~100 granular SIC industry codes (e.g. "ELECTRONIC COMPUTERS")
        to the 11 clean GICS sectors (e.g. "Technology") using SIC_TO_SECTOR.
        Aggregates weights by sector, sorts descending with "Other" last.
        """
        if not industry_data:
            return []

        sector_accum: Dict[str, float] = {}
        for item in industry_data:
            raw_name = (
                item.get("industryTitle")
                or item.get("industry")
                or item.get("sector")
                or ""
            )
            sector = _map_sic_to_sector(raw_name)
            weight = float(
                item.get("weight")
                or item.get("weightPercentage")
                or 0
            )
            if weight > 0:
                sector_accum[sector] = sector_accum.get(sector, 0) + weight

        # Build list: named sectors first (desc), "Other" last
        named = []
        other_weight = 0.0
        for name, weight in sector_accum.items():
            if name == "Other":
                other_weight += weight
            else:
                named.append({
                    "name": name,
                    "allocation": min(100.0, round(weight, 1)),
                    "color_hex": SECTOR_COLORS.get(name, DEFAULT_SECTOR_COLOR),
                })
        named.sort(key=lambda x: x["allocation"], reverse=True)

        if other_weight > 0:
            named.append({
                "name": "Other",
                "allocation": min(100.0, round(other_weight, 1)),
                "color_hex": DEFAULT_SECTOR_COLOR,
            })

        return named[:11]

    def _apply_change_percent(
        self,
        holdings: List[Dict],
        prev_raw: List[Dict],
    ) -> List[Dict]:
        """Compute change_percent by comparing current vs previous quarter allocations.

        ⚠️ Every arithmetic input here goes through ``_finite_float``, NOT bare
        ``float(x or 0)``. This was the one site in the 13F pipeline that did not, and
        the omission was load-bearing: ``float("nan") or 0`` yields ``nan`` (NaN is
        truthy), so ``prev_total`` became NaN, ``prev_total <= 0`` evaluated **False**
        (every NaN comparison does), and the guard below was bypassed. Every
        ``change_percent`` then became NaN, reached ``WhaleHoldingResponse`` — whose
        ``JSONResponse`` renders with ``allow_nan=False`` — and 500'd the entire whale
        profile from inside the renderer, where the endpoint's own try/except cannot
        reach it. Same failure shape as the holders-tab NaN outage.
        """
        prev_total = sum(_finite_float(h.get("value")) for h in prev_raw)
        if not math.isfinite(prev_total) or prev_total <= 0:
            return holdings

        prev_alloc: Dict[str, float] = {}
        for h in prev_raw:
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if sym and sym != "--":
                val = _finite_float(h.get("value"))
                prev_alloc[sym] = val / prev_total * 100

        for h in holdings:
            prev_pct = _finite_float(prev_alloc.get(h.get("ticker")))
            curr_pct = _finite_float(h.get("allocation"))
            h["change_percent"] = round(curr_pct - prev_pct, 2)

        return holdings

    async def _enrich_from_profiles(
        self,
        holdings: List[Dict],
        need_sectors: bool = False,
    ) -> Tuple[List[Dict], List[Dict]]:
        """One-stop enrichment: logos, company names, and sectors.

        Uses the existing company_profile_cache table (24h TTL) to avoid
        redundant FMP API calls. Only fetches from FMP for tickers not
        in the Supabase cache.

        Returns: (enriched_holdings, sectors_list)
        """
        sb = get_supabase()
        tickers = [h["ticker"] for h in holdings[:30] if h.get("ticker")]
        if not tickers:
            return holdings, []

        # ── Step 1: Batch-read from company_profile_cache ──────────
        profile_map: Dict[str, Dict] = {}
        uncached_tickers: List[str] = []
        try:
            cache_result = (
                sb.table("company_profile_cache")
                .select("ticker, profile_json, cached_at")
                .in_("ticker", tickers)
                .execute()
            )
            now = datetime.now(timezone.utc)
            for row in cache_result.data or []:
                cached_at = _as_aware(row.get("cached_at"))
                if cached_at is None:
                    continue          # unusable stamp → treat as a miss, refetch this one
                age_hours = (now - cached_at).total_seconds() / 3600
                if age_hours < 168:  # 7 days for static data like logos/names
                    profile_map[row["ticker"]] = row["profile_json"]

            uncached_tickers = [t for t in tickers if t not in profile_map]
        except Exception as e:
            logger.warning("company_profile_cache batch read failed: %s", e)
            uncached_tickers = tickers

        # ── Step 2: Fetch only missing profiles from FMP ───────────
        if uncached_tickers:
            logger.info(
                "Fetching %d/%d company profiles from FMP (rest from cache)",
                len(uncached_tickers), len(tickers),
            )
            try:
                fmp_profiles = await self.fmp.get_company_profiles_batch(
                    uncached_tickers[:30]
                )
                # ONE bulk upsert, not one per ticker. The postgrest client is
                # SYNCHRONOUS, so each `.execute()` blocks the event loop — and this loop
                # ran up to 30 of them back to back, ~30 x 150-250ms of serialized
                # Railway->Supabase latency that also stalled every other in-flight
                # request on the same loop.
                now_iso = _now_iso()
                rows = []
                for p in fmp_profiles:
                    sym = (p.get("symbol") or "").upper()
                    if not sym:
                        continue
                    profile_map[sym] = p
                    rows.append(
                        {"ticker": sym, "profile_json": p, "cached_at": now_iso}
                    )
                if rows:
                    # Best-effort: the cache write is an optimization, and failing it must
                    # not lose the profiles we just fetched and already applied above.
                    try:
                        sb.table("company_profile_cache").upsert(
                            rows, on_conflict="ticker"
                        ).execute()
                    except Exception as e:
                        logger.warning(
                            "company_profile_cache bulk upsert failed for %d ticker(s): "
                            "%s: %s",
                            len(rows), type(e).__name__, e,
                        )
            except Exception as e:
                logger.warning("FMP batch profiles failed: %s", e)

        # ── Step 3: Apply logos + company names to holdings ─────────
        for h in holdings:
            p = profile_map.get(h["ticker"])
            if not p:
                continue
            if not h.get("logo_url") and p.get("image"):
                h["logo_url"] = p["image"]
            if p.get("companyName") and (
                h.get("company_name", "") == h.get("ticker", "")
                or h.get("company_name", "").isupper()
            ):
                h["company_name"] = p["companyName"]

        # ── Step 4: Build sectors from profiles (if needed) ────────
        sectors: List[Dict] = []
        if need_sectors:
            sector_accum: Dict[str, float] = {}
            for h in holdings[:20]:
                p = profile_map.get(h["ticker"])
                if p:
                    sector = p.get("sector") or "Other"
                    sector_accum[sector] = (
                        sector_accum.get(sector, 0) + h.get("allocation", 0)
                    )
            named = []
            other_weight = 0.0
            for name, weight in sector_accum.items():
                if name == "Other":
                    other_weight += weight
                else:
                    named.append({
                        "name": name,
                        "allocation": min(100.0, round(weight, 1)),
                        "color_hex": SECTOR_COLORS.get(name, DEFAULT_SECTOR_COLOR),
                    })
            named.sort(key=lambda x: x["allocation"], reverse=True)
            if other_weight > 0:
                named.append({
                    "name": "Other",
                    "allocation": min(100.0, round(other_weight, 1)),
                    "color_hex": DEFAULT_SECTOR_COLOR,
                })
            sectors = named[:11]

        return holdings, sectors

    def _build_trade_responses(
        self, trades: List[Dict]
    ) -> List[WhaleTradeResponse]:
        """Convert raw trade dicts to Pydantic response objects."""
        return [
            WhaleTradeResponse(
                id=str(uuid.uuid4()),
                ticker=t.get("ticker") or "",
                company_name=t.get("company_name") or "",
                action=t.get("action") or "BOUGHT",
                trade_type=t.get("trade_type") or "Increased",
                amount=_finite_float(t.get("amount")),
                amount_range=t.get("amount_range"),
                previous_allocation=_finite_float(t.get("previous_allocation")),
                new_allocation=_finite_float(t.get("new_allocation")),
                date=t.get("date") or "",
                disclosure_date=t.get("disclosure_date"),
            )
            for t in trades
        ]

    def _build_trade_responses_from_db(
        self, db_trades: List[Dict]
    ) -> List[WhaleTradeResponse]:
        """Convert DB trade records to Pydantic response objects."""
        return [
            WhaleTradeResponse(
                id=str(t["id"]),
                ticker=t.get("ticker") or "",
                company_name=t.get("company_name") or "",
                action=t.get("action") or "BOUGHT",
                trade_type=t.get("trade_type") or "Increased",
                amount=_finite_float(t.get("amount")),
                amount_range=t.get("amount_range"),
                previous_allocation=_finite_float(t.get("previous_allocation")),
                new_allocation=_finite_float(t.get("new_allocation")),
                date=t.get("date") or "",
                disclosure_date=t.get("disclosure_date"),
            )
            for t in db_trades
        ]

    def _assemble_group_response(
        self,
        group_id: str,
        tg: Dict[str, Any],
        trades: List[WhaleTradeResponse],
    ) -> WhaleTradeGroupResponse:
        """Build a WhaleTradeGroupResponse from a snapshot group dict OR a DB row.

        Congressional groups get the honest summed STOCK Act range
        (``net_amount_range``) plus split transaction/disclosure dates. These
        are read from the group dict when present (snapshot path) or derived
        from the trades when absent (DB path, whose row has no such columns).
        13F groups leave them ``None`` and keep the precise ``net_amount``.
        """
        # `or`-form: `net_action` is NOT NULL in the schema today, but this method also
        # receives untyped SNAPSHOT dicts where it can genuinely be absent or null, and
        # `.get(k, default)` does not defend against a present null.
        net_action = tg.get("net_action") or "BOUGHT"
        disclosure_date = tg.get("disclosure_date")
        transaction_date = tg.get("transaction_date")
        net_amount_range = tg.get("net_amount_range")

        # Congress trades carry a STOCK Act bucket string; 13F trades do not.
        if any(t.amount_range for t in trades):
            if not disclosure_date:
                disclosure_date = max(
                    (t.disclosure_date or "" for t in trades), default=""
                ) or None
            if not transaction_date:
                transaction_date = max(
                    (t.date or "" for t in trades), default=""
                ) or None
            if not net_amount_range:
                bounds = [
                    parse_congress_amount_bounds(t.amount_range)
                    for t in trades
                    if t.action == net_action and t.amount_range
                ]
                if bounds:
                    low, high = sum_amount_bounds(bounds)
                    if low > 0 or (high is not None and high > 0):
                        net_amount_range = format_amount_range(low, high)

        return WhaleTradeGroupResponse(
            id=group_id,
            date=tg.get("date") or "",
            trade_count=tg.get("trade_count") or 0,
            net_action=net_action,
            net_amount=_finite_float(tg.get("net_amount")),
            net_amount_range=net_amount_range,
            disclosure_date=disclosure_date,
            transaction_date=transaction_date,
            summary=tg.get("summary"),
            insights=tg.get("insights") or [],
            trades=trades,
        )

    # ── Summary Generation (Rule-Based) ──────────────────────────────

    def _generate_trade_group_summary(
        self,
        trades: List[Dict],
        new_count: int,
        closed_count: int,
        net_action: str,
    ) -> str:
        """Generate a one-line summary for a trade group."""
        buys = [t for t in trades if t["action"] == "BOUGHT"]
        sells = [t for t in trades if t["action"] == "SOLD"]

        parts = []
        if new_count > 0:
            parts.append(
                f"added {new_count} new position{'s' if new_count > 1 else ''}"
            )
        if closed_count > 0:
            parts.append(
                f"closed {closed_count} position{'s' if closed_count > 1 else ''}"
            )

        if len(buys) > len(sells) * 2:
            action_text = f"Heavy accumulation with {len(buys)} buys"
        elif len(sells) > len(buys) * 2:
            action_text = f"Significant reduction with {len(sells)} sells"
        elif not sells and buys:
            action_text = f"Pure buying activity with {len(buys)} positions"
        elif not buys and sells:
            action_text = f"Pure selling activity with {len(sells)} positions"
        else:
            action_text = "Portfolio rebalancing"

        if parts:
            return f"{action_text} ({', '.join(parts)})"
        return action_text

    def _generate_trade_group_insights(
        self,
        trades: List[Dict],
        new_count: int,
        closed_count: int,
        total_bought: float,
        total_sold: float,
    ) -> List[str]:
        """Generate insight strings for a trade group."""
        insights = []

        if total_bought > 0:
            insights.append(
                f"Net accumulating with {_format_amount(total_bought, 'BOUGHT')} in new buying"
            )
        if total_sold > 0:
            insights.append(
                f"Trimmed {_format_amount(total_sold, 'SOLD')} in positions"
            )
        if new_count > 0:
            top_new = [
                t["ticker"]
                for t in trades
                if t["trade_type"] == "New"
            ][:3]
            if top_new:
                insights.append(
                    f"New positions: {', '.join(top_new)}"
                )
        if closed_count > 0:
            top_closed = [
                t["ticker"]
                for t in trades
                if t["trade_type"] == "Closed"
            ][:3]
            if top_closed:
                insights.append(
                    f"Exited: {', '.join(top_closed)}"
                )

        return insights[:4]

    def _generate_behavior_summary(
        self,
        trade_group: Optional[Dict],
        sector_data: List[Dict],
    ) -> Dict[str, str]:
        """Generate behavior_summary JSONB."""
        top_sector = sector_data[0]["name"] if sector_data else "various sectors"
        second_sector = (
            sector_data[1]["name"]
            if len(sector_data) > 1
            else "core positions"
        )

        if not trade_group:
            return {
                "action": "Holding",
                "primaryFocus": "existing positions",
                "secondaryAction": "Maintaining",
                "secondaryFocus": "portfolio allocation",
            }

        buys = [
            t for t in trade_group.get("trades", []) if t["action"] == "BOUGHT"
        ]
        sells = [
            t for t in trade_group.get("trades", []) if t["action"] == "SOLD"
        ]

        if len(buys) > len(sells):
            return {
                "action": "Accumulating",
                "primaryFocus": f"{top_sector.lower()} stocks",
                "secondaryAction": "Holding",
                "secondaryFocus": f"core {second_sector.lower()} positions",
            }
        elif len(sells) > len(buys):
            return {
                "action": "Reducing",
                "primaryFocus": f"exposure to {top_sector.lower()}",
                "secondaryAction": "Maintaining",
                "secondaryFocus": f"{second_sector.lower()} allocations",
            }
        else:
            return {
                "action": "Rebalancing",
                "primaryFocus": "across sectors",
                "secondaryAction": "Adjusting",
                "secondaryFocus": "position sizes",
            }

    def _generate_sentiment_summary(
        self,
        holdings: List[Dict],
        trade_group: Optional[Dict],
        sector_data: List[Dict],
        is_congress: bool = False,
    ) -> str:
        """Generate a rule-based sentiment summary paragraph.

        For congressional whales, dollar figures are disclosed only as ranges
        (STOCK Act) on a 30–45 day lag, so we never state a precise net dollar
        amount — we describe direction + the summed range instead.
        """
        top_tickers = ", ".join(h["ticker"] for h in holdings[:5])
        top_sector = sector_data[0]["name"] if sector_data else "various sectors"

        if trade_group:
            activity = trade_group.get("summary", "active rebalancing")
            net_action = trade_group.get("net_action", "BOUGHT")

            if is_congress:
                direction = "net buying" if net_action == "BOUGHT" else "net selling"
                rng = trade_group.get("net_amount_range")
                amount_text = f" ({rng})" if rng else ""
                return (
                    f"Recent disclosures show {direction}{amount_text} across "
                    f"positions in {top_tickers}. Amounts are STOCK Act ranges "
                    f"disclosed on a 30–45 day lag. Activity summary: {activity}."
                )

            net_amount = trade_group.get("net_amount", 0)
            action_text = (
                f"net buying of {_format_amount(net_amount, net_action)}"
                if net_action == "BOUGHT"
                else f"net selling of {_format_amount(net_amount, net_action)}"
            )
            return (
                f"Portfolio concentrated in {top_sector} with top positions "
                f"in {top_tickers}. Recent filing shows {action_text}. "
                f"Activity summary: {activity}."
            )

        return (
            f"Portfolio concentrated in {top_sector} with top positions "
            f"in {top_tickers}. Recent activity shows stable positioning "
            f"with no significant changes."
        )

    # ── Sync to Denormalized Tables ──────────────────────────────────

    async def _compute_ticker_cagr(self, ticker: str) -> AnnualReturn:
        """FMP I/O only — the annualization itself lives in `_whale_common`.

        This is the associated public vehicle's SHARE PRICE since inception
        (BRK-A, PSH.L, ARKK, IEP, MKL), not a 13F figure. The caller labels it
        with the ticker so the screen never implies it describes the 13F sleeve
        in the tile beside it.
        """
        try:
            price_change = await self.fmp.get_stock_price_change(ticker)
            max_return = (price_change or {}).get("max")

            profiles = await self.fmp.get_company_profiles_batch([ticker])
            ipo_date_str = (profiles[0].get("ipoDate") if profiles else None)
            if not ipo_date_str:
                return AnnualReturn(None, None, "", RETURN_UNAVAILABLE)

            # `.replace(tzinfo=utc)`: strptime yields a NAIVE datetime, and subtracting
            # it from an aware `now` raises TypeError — which the broad `except` below
            # would swallow into "return data unavailable" for every proxy-ticker whale.
            ipo_date = datetime.strptime(ipo_date_str, "%Y-%m-%d").replace(
                tzinfo=timezone.utc
            )
            years = (datetime.now(timezone.utc) - ipo_date).days / 365.25
            if years < 1:
                # Less than a full year of trading — annualizing it would
                # extrapolate a partial year into a "per year" claim.
                return AnnualReturn(None, None, "", RETURN_INSUFFICIENT)

            return compute_ticker_cagr(max_return, years)
        except Exception as e:
            logger.warning("Ticker CAGR failed for %s: %s", ticker, e)
            return AnnualReturn(None, None, "", RETURN_UNAVAILABLE)

    async def _compute_best_annual_return(
        self,
        whale: Dict,
        perf_list: List[Dict],
    ) -> AnnualReturn:
        """Tiered annual return: associated-ticker CAGR → 13F CAGR → none.

        Returns an `AnnualReturn` rather than a bare float so the caller can
        tell "no usable history" (which may clear a stored value) apart from
        "could not read" (which must not), and so the window travels with the
        number instead of being discarded.
        """
        ticker = whale.get("associated_ticker")
        if ticker:
            result = await self._compute_ticker_cagr(ticker)
            if result.is_ok:
                return result

        return compute_13f_cagr(perf_list)

    @staticmethod
    def _compute_avg_annual_return(perf_list: List[Dict]) -> AnnualReturn:
        """Delegate to the shared helper — see `_whale_common.compute_13f_cagr`.

        Kept as a named method because the call sites read better with it, but
        it must stay a one-liner: this function having its OWN copy of the
        formula is precisely how it drifted from `hydrate_whales` (different
        outlier floor, different caption) in the first place.
        """
        return compute_13f_cagr(perf_list)


    @staticmethod
    def _stat_disclosure(
        whale: Dict[str, Any], snapshot: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Both stat tiles' values AND whether they can be believed.

        A static method rather than inline arithmetic because the old inline
        version — two `or 0` coercions — was untestable without Supabase, and it
        hid a real defect: a NULL `ytd_return` became `0.0`, which iOS rendered
        as a confident GREEN "+0.0%". "We have no data" and "this whale was
        flat" were the same pixels.

        ⚠️ `is None`, NOT falsy. A genuine 0.0% year is real data and must stay
        `ok`; `or 0` cannot tell it apart from a missing column.

        `ytd_return` / `portfolio_value` still go out as plain numbers even when
        unbelievable, and that is deliberate: the shipped iOS DTO declares them
        non-Optional `Double`, so a JSON null would fail the WHOLE profile
        decode on every installed build. The sibling `*_status` fields carry the
        truth for clients new enough to read them.
        """
        raw_value = (snapshot or {}).get("total_value")
        if raw_value is None:
            raw_value = whale.get("portfolio_value")
        # `_finite_float`, not bare `float()`: `float("NaN")` SUCCEEDS, so a
        # `try/except (TypeError, ValueError)` never fires for it. Postgres `numeric`
        # accepts the value 'NaN' and the `portfolio_value >= 0` CHECK passes for it
        # (Postgres orders NaN above every numeric), so a NaN can genuinely be stored
        # and read back — and it 500s the whole profile at serialization time.
        portfolio_value = (
            _finite_float(raw_value) if raw_value is not None else 0.0
        )
        portfolio_status = (
            RETURN_OK if portfolio_value > 0 else RETURN_UNAVAILABLE
        )

        raw_return = whale.get("ytd_return")
        stored_status = (whale.get("return_status") or "").strip()
        if stored_status:
            return_status = stored_status
        else:
            # Row predates migration 127. Trust the legacy value if there is
            # one, so nothing blanks during the rollout window.
            return_status = RETURN_OK if raw_return is not None else RETURN_INSUFFICIENT
        # Same NaN reasoning as `portfolio_value` above. A sentinel is used rather
        # than a silent 0.0 so an unusable stored value degrades to the honest
        # "not enough history" tile instead of a confident flat "+0.0%".
        _coerced = (
            _finite_float(raw_return, default=_UNUSABLE_NUMBER)
            if raw_return is not None
            else 0.0
        )
        if _coerced is _UNUSABLE_NUMBER:
            ytd_return, return_status = 0.0, RETURN_INSUFFICIENT
        else:
            ytd_return = _coerced

        # A legacy row can be `ok` with no window; the client then omits the
        # "· N-yr" suffix rather than blanking an otherwise good number.
        window = whale.get("return_window_years")
        try:
            return_window_years = int(window) if window is not None else None
        except (TypeError, ValueError):
            return_window_years = None

        stored_label = (whale.get("return_label") or "").strip()
        return_label = (
            stored_label
            if return_status == RETURN_OK
            else unavailable_return_label(return_status)
        )

        return {
            "portfolio_value": portfolio_value,
            "portfolio_status": portfolio_status,
            "portfolio_as_of": filing_period_display(
                (snapshot or {}).get("filing_period") or ""
            )
            or None,
            # ⚠️ 13F ONLY. The congressional path writes `filing_date` as
            # `now.strftime("%Y-%m-%d")` at hydration time, so a retired member's profile
            # showed a "filing date" that advanced every month forever — a hydration
            # timestamp wearing a filing date's name. Same reasoning that already makes
            # `portfolio_as_of` return None for them: a politician files no 13F, so there
            # is no filing date to show.
            "filing_date": (
                (snapshot or {}).get("filing_date") or None
                if (whale.get("data_source") or "").strip().lower() == "13f"
                else None
            ),
            "ytd_return": ytd_return,
            "return_status": return_status,
            "return_window_years": return_window_years,
            "return_label": return_label,
        }

    async def _sync_to_whale_tables(
        self,
        whale_id: str,
        holdings: List[Dict],
        sectors: List[Dict],
        trade_groups: List[Dict],
        behavior: Dict,
        sentiment: str,
        total_value: float,
        perf_data: Any,
        whale: Optional[Dict] = None,
    ) -> None:
        """Write aggregated data into the existing whale_* tables.

        ``trade_groups`` is a list — one entry per 13F quarter or per
        congressional disclosure filing. Each is upserted keyed by
        ``(whale_id, date)`` so re-running hydration on unchanged upstream data
        does NOT accumulate duplicate rows (the old daily-``now``-stamp bug).
        """
        sb = get_supabase()

        # Each denormalized write is isolated in its OWN try/except (mirroring
        # scripts/hydrate_whales.py). Previously all four shared one try, so a
        # single failing insert — e.g. a duplicate-ticker whale_holdings
        # UNIQUE(whale_id, ticker) violation, which fires AFTER the delete —
        # aborted the block and silently skipped the sector + trade-group syncs
        # that the activity feed, tracking alerts, home signals, and trade-group
        # detail all read from. Now one failure degrades only its own section.

        # 1. Update whale record (portfolio value + tiered annual return)
        try:
            whale_update: Dict[str, Any] = {
                "portfolio_value": total_value,
                "behavior_summary": behavior,
                "sentiment_summary": sentiment,
            }

            # Annual return. ONE branch, not a fork on `associated_ticker` —
            # the fork is why two different captions existed for the same
            # computation inside a single file (see `return_label_for`).
            # `_compute_best_annual_return` already tiers, and that helper is
            # the only thing allowed to name the result.
            perf_list = perf_data if isinstance(perf_data, list) else []
            result = await self._compute_best_annual_return(whale or {}, perf_list)

            # WRITE THE JUDGEMENT, INCLUDING A NEGATIVE ONE. The old code wrote
            # `if ret_val is not None`, so a bogus value — e.g. one the deleted
            # 1-year fallback produced — could never be cleared: recomputing it
            # as None simply skipped the write and left the bad number in place
            # forever. Now anything except an upstream MISS overwrites.
            #
            # `unavailable` is the one status that must not: it means we could
            # not read FMP, and an outage must never blank a good stored value.
            if result.status != RETURN_UNAVAILABLE:
                whale_update["ytd_return"] = result.value
                whale_update["return_source"] = result.source
                whale_update["return_window_years"] = result.window_years
                whale_update["return_status"] = result.status
                whale_update["return_label"] = (
                    return_label_for(
                        result.source, (whale or {}).get("associated_ticker")
                    )
                    if result.is_ok
                    # Sent so ALREADY-SHIPPED clients, which render this string
                    # verbatim under a green "+0.0%" and cannot be taught the
                    # em-dash, at least stop captioning that zero as a CAGR.
                    else unavailable_return_label(result.status)
                )
            sb.table("whales").update(whale_update).eq("id", whale_id).execute()
        except Exception as e:
            logger.error("[sync] whale record update failed for %s: %s", whale_id, e)

        # 2. Replace holdings
        try:
            sb.table("whale_holdings").delete().eq(
                "whale_id", whale_id
            ).execute()
            # ONE insert for all 30 rows. The DELETE-then-write shape is preserved
            # deliberately: a partial failure must replay from the DELETE, never resume
            # mid-loop, or it collides with `whale_holdings_whale_id_ticker_key`.
            # Bulking makes that stronger, not weaker — the insert is now atomic, so
            # there is no longer a partial state to resume into.
            rows = [
                {
                    "whale_id": whale_id,
                    "ticker": h["ticker"],
                    "company_name": h.get("company_name", h["ticker"]),
                    "logo_url": h.get("logo_url"),
                    "allocation": h.get("allocation", 0),
                    "change_percent": h.get("change_percent", 0),
                }
                for h in holdings[:30]
            ]
            if rows:
                sb.table("whale_holdings").insert(rows).execute()
        except Exception as e:
            logger.error("[sync] holdings sync failed for %s: %s", whale_id, e)

        # 3. Replace sector allocations
        try:
            sb.table("whale_sector_allocations").delete().eq(
                "whale_id", whale_id
            ).execute()
            rows = [
                {
                    "whale_id": whale_id,
                    "sector": sec["name"],
                    "allocation": sec["allocation"],
                }
                for sec in sectors
            ]
            if rows:
                sb.table("whale_sector_allocations").insert(rows).execute()
        except Exception as e:
            logger.error("[sync] sector sync failed for %s: %s", whale_id, e)

        # 4. Insert trade groups + trades (one row per filing, deduped by date).
        # The SELECT-then-INSERT is best-effort; the UNIQUE(whale_id, date)
        # index (migration 077) is the authoritative guard. A concurrent
        # writer that wins the race raises a unique violation here — we treat
        # that as "already inserted" and skip its children (they belong to
        # the other writer's group row), so no duplicate rows are created.
        # Each group is isolated so one bad filing doesn't skip the others.
        for trade_group in trade_groups or []:
            if not trade_group or not trade_group.get("date"):
                continue
            try:
                # UPSERT against `uq_whale_trade_groups_whale_date` (migration 077),
                # matching `hydrate_whales._persist`. The old select-then-insert was
                # check-then-act: a concurrent writer winning the race made this one
                # `continue` AFTER its group row existed, stranding that filing's trades
                # under nobody. It also cost an extra round-trip per group.
                tg_result = sb.table("whale_trade_groups").upsert({
                    "whale_id": whale_id,
                    "date": trade_group["date"],
                    "trade_count": trade_group["trade_count"],
                    "net_action": trade_group["net_action"],
                    "net_amount": trade_group["net_amount"],
                    "summary": trade_group.get("summary"),
                    "insights": trade_group.get("insights", []),
                }, on_conflict="whale_id,date").execute()

                if tg_result.data:
                    tg_id = tg_result.data[0]["id"]
                else:
                    # Some PostgREST configs return no representation for an upsert that
                    # changed nothing. Read the id back rather than `continue`-ing —
                    # that is exactly how trades used to be stranded.
                    lookup = (
                        sb.table("whale_trade_groups")
                        .select("id")
                        .eq("whale_id", whale_id)
                        .eq("date", trade_group["date"])
                        .limit(1)
                        .execute()
                    )
                    if not lookup.data:
                        logger.warning(
                            "[sync] Trade group upsert returned no id for whale=%s "
                            "date=%s — skipping its trades this run",
                            whale_id, trade_group["date"],
                        )
                        continue
                    tg_id = lookup.data[0]["id"]
                # ONE write for up to 50 trades. This loop was the single largest
                # contributor to a cold build: 50 sequential blocking round-trips PER
                # trade group, each one also stalling the shared event loop.
                trade_rows = [
                    {
                        "whale_id": whale_id,
                        "trade_group_id": tg_id,
                        "ticker": trade["ticker"],
                        "company_name": trade.get(
                            "company_name", trade["ticker"]
                        ),
                        "action": trade["action"],
                        "trade_type": trade["trade_type"],
                        "amount": trade["amount"],
                        # STOCK Act range + disclosure date preserved for
                        # congress trades (None for 13F). Requires migration
                        # #076 columns; harmless once applied.
                        "amount_range": trade.get("amount_range"),
                        "disclosure_date": trade.get("disclosure_date"),
                        "previous_allocation": trade.get(
                            "previous_allocation"
                        ),
                        "new_allocation": trade.get("new_allocation"),
                        "date": trade.get("date", ""),
                    }
                    for trade in trade_group.get("trades", [])[:50]
                ]
                if trade_rows:
                    _bulk_write_trades(sb, trade_rows)
            except Exception as tg_err:
                logger.warning(
                    "whale_trade_group sync skipped (likely concurrent "
                    "duplicate) whale_id=%s date=%s: %s: %s",
                    whale_id, trade_group.get("date"),
                    type(tg_err).__name__, tg_err,
                )
                continue


# ── Module-Level Helpers ─────────────────────────────────────────────


def _activity_for(row: Dict[str, Any]) -> Activity:
    """Classify one `whales` row. Curated status wins over the derived one."""
    derived = compute_activity(
        data_source=row.get("data_source"),
        last_filing_period=row.get("last_filing_period"),
        last_activity_date=row.get("last_activity_date"),
    )
    # A human-written note is a STATED reason; the derived label is an inferred one.
    # "Nancy Pelosi retired" can never be derived — she still shows recent trades — so
    # when somebody has written it down, it wins.
    note = str(row.get("lifecycle_note") or "").strip()
    status = str(row.get("lifecycle_status") or "").strip().lower()
    if status and status != "active":
        return Activity(status="inactive", label=note or derived.label, as_of=derived.as_of)
    return derived


def _activity_fields(row: Dict[str, Any]) -> Dict[str, str]:
    """The two roster-facing activity keys for a `whales` row.

    Returns `""` for both when the filer is current, so an active whale carries no chip
    and the client needs no knowledge of the status vocabulary to stay quiet.
    """
    a = _activity_for(row)
    if not a.needs_disclosure:
        return {"activity_status": ACTIVITY_UNKNOWN, "activity_label": ""}
    return {"activity_status": a.status, "activity_label": a.label}


def _invalidate_follow_caches(user_id: str, whale_id: str) -> None:
    """Drop only the cache entries a follow toggle actually invalidates.

    This used to be three unconditional ``.clear()`` calls, so ONE user tapping Follow
    wiped every other user's roster, activity feed and whale profile. On a busy instance
    that made the 1-hour profile cache effectively inoperative and pushed the load onto
    the expensive Tier-3 FMP rebuild.

    What each cache actually depends on:
      • ``_whale_activity_cache`` — keyed ``activity:{user_id}:{tier}``. Only THIS user's
        entries change. Dropped across every tier key, because the caller's tier is not
        known here and there are only a handful.
      • ``_whale_list_cache`` — keyed ``whales:{category}:{user_id}``. Only THIS user's
        entries carry the changed ``is_following``. Other users' rosters hold a
        ``followers_count`` that is now off by one; that is a soft counter with a 5-minute
        TTL, and staleness there is worth far less than a global stampede.
      • ``_whale_profile_cache`` — deliberately NOT dropped. Profiles are built
        follow-state-free and the per-user ``is_following`` is overlaid on read
        (``_overlay_follow_state``), and the shape carries no follower count. Clearing it
        on a follow was pure waste.
    """
    for key in [k for k in _whale_activity_cache if k.startswith(f"activity:{user_id}:")]:
        _whale_activity_cache.pop(key, None)
    for key in [k for k in _whale_list_cache if k.endswith(f":{user_id}")]:
        _whale_list_cache.pop(key, None)


def _snapshot_group_id(whale_id: str, filing_date: Any) -> str:
    """A stable, reproducible id for a trade group that exists only in the snapshot.

    Deterministic (uuid5 over whale + filing date) so the same group keeps the same id
    across requests and across processes. A random id per request re-keyed the iOS
    `Identifiable` list on every profile refresh.
    """
    return str(uuid.uuid5(_SNAPSHOT_GROUP_NS, f"{whale_id}:{filing_date or ''}"))


def _normalize_sector_exposure(
    sectors: List[WhaleSectorAllocationResponse], other_pct: float
) -> List[WhaleSectorAllocationResponse]:
    """Make the sector donut sum to 100% — honestly, in whichever direction it is off.

    ⚠️ These percentages were previously passed through untouched, and 20 of the 54
    whales in production rendered a pie chart that did not sum to 100: from 47.7%
    (Ray Dalio) to 123.4%. Seth Klarman's read 115.4%. A donut whose slices exceed the
    circle is not a rounding artefact, it is a wrong statement about a portfolio.

    Two DIFFERENT causes, so two different corrections:

    * **Over 100%** — the upstream industry weights are over-counted (a filer's holdings
      can be attributed to more than one industry line). Relative shape is still
      meaningful, so scale proportionally. Scaling is the only correction that does not
      invent or destroy a preference between sectors.

    * **Under 100%** — the breakdown genuinely does not cover the whole book: it is
      capped at 11 named sectors and any position FMP could not classify simply is not
      there. The remainder is REAL and unclassified, so it goes into "Other". Scaling up
      to 100 here would be a lie — it would claim coverage that was never reported.

    `other_pct` arrives separately because the caller has already rolled sub-0.5% slices
    and any explicit "Other" row into it.
    """
    named_total = sum(s.percentage for s in sectors)
    total = named_total + max(0.0, other_pct)
    if total <= 0:
        return []

    out = list(sectors)
    if total > 100.0 + _SECTOR_SUM_TOLERANCE:
        # Over-counted: scale everything, "Other" included, back onto the circle.
        scale = 100.0 / total
        out = [
            s.model_copy(update={"percentage": round(s.percentage * scale, 1)})
            for s in out
        ]
        other_pct = other_pct * scale
    elif total < 100.0 - _SECTOR_SUM_TOLERANCE:
        # Under-reported: the shortfall is unclassified book, not empty space.
        other_pct += 100.0 - total

    if other_pct > 0:
        out.append(
            WhaleSectorAllocationResponse(
                id=str(uuid.uuid4()),
                name="Other",
                percentage=round(other_pct, 1),
                color_hex=SECTOR_COLORS.get("Other", DEFAULT_SECTOR_COLOR),
            )
        )
    return out


def _bulk_write_trades(sb, rows: List[Dict[str, Any]]) -> None:
    """Write trades in ONE statement, repairing rather than duplicating on a replay.

    Upserts against `uq_whale_trades_group_ticker_action_date` (migration 143) so a
    partial replay tops the group up instead of duplicating it. If that index is not
    present yet PostgREST answers 42P10, and we fall back to a plain bulk insert — the
    same tolerance `hydrate_whales._upsert_trades` carries, so the two writers behave
    identically and deploy order does not matter.
    """
    if not rows:
        return
    # ⚠️ DEDUPE ON THE CONFLICT KEY FIRST — see the long note in
    # `hydrate_whales._upsert_trades`.
    #
    # Postgres refuses a multi-row `ON CONFLICT DO UPDATE` whose payload touches the same
    # row twice (SQLSTATE 21000). A single congressional filing routinely discloses the
    # same symbol/direction/transaction-date more than once — spouse + self, two amount
    # buckets, or an FMP page overlap; measured at 16-45% of live filings.
    #
    # This is the LIVE profile-build path, and the fallback below is a BULK insert that
    # cannot degrade per-row, so without this a duplicate strands a whole filing's trades
    # while its group row still advertises `trade_count = N`.
    #
    # Last-wins, exactly what the per-row upsert this replaced produced.
    deduped: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        deduped[
            (r.get("trade_group_id"), r.get("ticker"), r.get("action"), r.get("date"))
        ] = r
    rows = list(deduped.values())
    try:
        sb.table("whale_trades").upsert(
            rows, on_conflict="trade_group_id,ticker,action,date"
        ).execute()
    except Exception as e:
        msg = str(e).lower()
        if "42p10" in msg or "no unique or exclusion constraint" in msg:
            # Same wording rule as the hydrator twin: 42P10 also fires when the index
            # EXISTS but is PARTIAL, so never assert that the migration is unapplied.
            logger.warning(
                "whale_trades upsert could not infer a conflict target (42P10) — index "
                "missing, or present but PARTIAL. Falling back to insert for %d trade(s)",
                len(rows),
            )
            sb.table("whale_trades").insert(rows).execute()
        else:
            raise


# Distinct-whale concurrency cap.
#
# ⚠️ MEASURED TO BE INERT FOR LOOP-BLOCKING, and kept only as a guard against a future
# caller that DOES await inside the critical section. The snapshot-served build path has
# no genuine suspension point — its only `await` is on a coroutine that never yields, and
# every Supabase call under it is synchronous — so this semaphore is acquired and
# released within a single task step and never suspends. A heartbeat measurement showed
# an identical 18.2s contiguous stall at concurrency 1, 3 and 56.
#
# What actually bounds the stall is the pre-warmer running whales SEQUENTIALLY with an
# explicit `await asyncio.sleep(0)` between them (see `_run_whale_profile_pre_warmer`).
# Do not "simplify" that back into an asyncio.gather.
#
# Mirrors `ticker_data_cache._WARM_SEMAPHORE`; lazily built so it binds to the running loop.
_WHALE_WARM_SEMAPHORE: Optional["asyncio.Semaphore"] = None


def _get_whale_warm_semaphore() -> "asyncio.Semaphore":
    global _WHALE_WARM_SEMAPHORE
    if _WHALE_WARM_SEMAPHORE is None:
        from app.config import settings
        _WHALE_WARM_SEMAPHORE = asyncio.Semaphore(
            max(1, getattr(settings, "WHALE_PREWARM_CONCURRENCY", 3))
        )
    return _WHALE_WARM_SEMAPHORE


async def warm_whale_profile(whale_id: str) -> None:
    """Best-effort warm of `whale_profile_cache` for one whale. NEVER raises.

    Modelled on `ticker_data_cache.warm_ticker_collection`, and deliberately keeps its
    three load-bearing properties:

      1. **Freshness pre-check BEFORE taking a slot**, so steady state costs nothing.
      2. **Bounded across distinct whales** by `_WHALE_WARM_SEMAPHORE`.
      3. **Never raises** — a failed warm just means the next real request rebuilds.
         A lifespan task that dies takes the warmer with it for the process lifetime.

    Built with `user_id=None` so the cached artifact is follow-state-free, exactly as the
    request path caches it; each caller overlays its own follow state on read.
    """
    try:
        if not whale_id:
            return
        mem_key = f"profile:{whale_id}"
        if _cache_get(_whale_profile_cache, mem_key, WHALE_PROFILE_CACHE_TTL) is not None:
            return

        async with _get_whale_warm_semaphore():
            svc = WhaleService()
            # The ungated builder: the tier gate is applied per-request on read, so the
            # cached artifact must stay UNREDACTED or a warm performed "as Free" would
            # poison the cache for paying users.
            await svc._get_whale_profile_ungated(whale_id=whale_id, user_id=None)
        logger.info("whale_profile_cache WARMED for %s", whale_id)
    except Exception as e:
        logger.warning(
            "whale profile warm failed for %s: %s: %s — will build on demand",
            whale_id, type(e).__name__, e,
        )


class _SchemaFloorMiss(Exception):
    """Internal sentinel: the cached row predates `WHALE_PROFILE_SCHEMA_FLOOR`."""


def _now_iso() -> str:
    """Current UTC instant as an ISO-8601 string, for PostgREST filter literals."""
    return datetime.now(timezone.utc).isoformat()


def _as_aware(value: Any) -> Optional[datetime]:
    """Parse a Postgres timestamp into a UTC-AWARE datetime, or None.

    Rows written before the UTC fix carry a NAIVE local stamp. Subtracting a naive from
    an aware datetime raises ``TypeError`` — which, at the one call site that catches
    broadly, silently degraded into "refetch every ticker from FMP" on every build.
    A naive value is therefore ASSUMED UTC, which is what Postgres did with it on the
    way in.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _looks_like_uuid(value: Any) -> bool:
    """True when ``value`` can be parsed as a uuid.

    Used to reject a malformed path parameter BEFORE it reaches PostgREST, which
    answers a bad uuid literal with ``22P02`` — an error that read as an unhandled 500
    rather than as "no such group".
    """
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def _finite_float(value: Any, default: float = 0.0) -> float:
    """Coerce to a FINITE float; NaN / Inf / None / garbage → ``default``.

    FMP occasionally emits ``NaN`` / ``Infinity`` tokens. An unguarded
    non-finite float propagates into a response float field and — because
    Starlette's ``JSONResponse`` renders with ``allow_nan=False`` — raises at
    serialization, 500-ing the WHOLE whale-profile screen. It can also violate
    the ``numeric``/CHECK columns on the denormalized whale tables. Mirrors
    ``holders_service._safe_float``.
    """
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if math.isfinite(f) else default


def _alert_is_expired(alert: Dict) -> bool:
    """True if the alert's ``expires_at`` has passed. A missing or unparseable
    ``expires_at`` is treated as non-expiring (never blocks a live alert)."""
    expires = alert.get("expires_at")
    if not expires:
        return False
    try:
        exp_dt = datetime.fromisoformat(str(expires).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    return exp_dt < datetime.now(timezone.utc)


_QUARTER_END = {1: "-03-31", 2: "-06-30", 3: "-09-30", 4: "-12-31"}


def _quarter_end_date(year: int, quarter: int) -> str:
    """Calendar quarter-end ISO date, e.g. (2024, 2) → ``"2024-06-30"``."""
    return f"{year}{_QUARTER_END.get(quarter, '-12-31')}"


def _split_ratio_in_window(
    splits: Optional[List[Dict]],
    start_excl: Optional[str],
    end_incl: Optional[str],
) -> float:
    """Product of FMP stock-split ratios with ``start_excl < date <= end_incl``.

    FMP ``/splits`` rows carry ``date`` + ``numerator``/``denominator`` (10/1 =
    10:1). Quarters with no split map to ``1.0``. Non-finite / non-positive
    ratios are dropped (a NaN would silently disable the restatement).
    """
    if not splits or not end_incl:
        return 1.0
    ratio = 1.0
    for s in splits:
        d = str(s.get("date") or "")[:10]
        num = s.get("numerator")
        den = s.get("denominator")
        if not d or not num or not den:
            continue
        try:
            r = float(num) / float(den)
        except (ValueError, ZeroDivisionError, TypeError):
            continue
        if not math.isfinite(r) or r <= 0:
            continue
        if (start_excl is None or start_excl < d) and d <= end_incl:
            ratio *= r
    return ratio


def _find_previous_quarter(
    filing_dates: List[Dict], year: int, quarter: int
) -> Optional[Dict]:
    """Find the filing entry for the quarter before (year, quarter)."""
    for fd in filing_dates:
        fd_year = int(fd.get("year") or fd.get("date", "0000")[:4])
        fd_quarter = int(fd.get("quarter", 0))
        if fd_year == year and fd_quarter == quarter:
            continue
        if (fd_year < year) or (fd_year == year and fd_quarter < quarter):
            return fd
    return None


async def _noop_list() -> List:
    return []


def _format_amount(value: float, action: str) -> str:
    """Signed, human-scaled dollar string for the activity feed. iOS renders it VERBATIM.

    Delegates the magnitude to ``_whale_common.format_amount_short`` so the roll-up
    rule lives in exactly one place. It used to have its own ladder with no roll-up at
    the unit boundaries, which made $999,999,999 render "+$1000.0M" here while the very
    same trade group rendered "$1.00B" on the whale profile card — the same figure,
    two screens, two answers. ``format_amount_short`` also takes ``abs()``, so the sign
    is ours to add.

    The sign is derived from an explicit SOLD test rather than ``== "BOUGHT"``: the old
    form stamped a minus on ANY value that was not exactly "BOUGHT", so a blank or
    lower-cased ``net_action`` turned a buy into a loss on the timeline.
    """
    amount = _finite_float(value)
    # An exactly-zero group is a perfect wash, not a sale — "-$0" states a direction
    # the data does not have.
    if amount == 0:
        return format_amount_short(0.0)
    prefix = "-" if str(action).strip().upper() == "SOLD" else "+"
    return f"{prefix}{format_amount_short(amount)}"