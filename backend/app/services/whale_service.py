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
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
import logging

from app.integrations.fmp import get_fmp_client, FMPClient
from app.database import get_supabase
from app.services._whale_common import (
    parse_congress_amount_dollars,
    calc_13f_trade_dollars,
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

# ── In-Memory TTL Caches ────────────────────────────────────────────

_whale_list_cache: Dict[str, Tuple[float, Any]] = {}
WHALE_LIST_CACHE_TTL = 300  # 5 minutes

_whale_profile_cache: Dict[str, Tuple[float, Any]] = {}
WHALE_PROFILE_CACHE_TTL = 3600  # 1 hour

_whale_activity_cache: Dict[str, Tuple[float, Any]] = {}
WHALE_ACTIVITY_CACHE_TTL = 600  # 10 minutes

_filing_dates_cache: Dict[str, Tuple[float, Any]] = {}
FILING_DATES_CACHE_TTL = 86400  # 24 hours — filing dates change quarterly


def _cache_get(cache: Dict, key: str, ttl: int) -> Optional[Any]:
    entry = cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if _time.monotonic() - ts > ttl:
        del cache[key]
        return None
    return value


def _cache_set(cache: Dict, key: str, value: Any) -> None:
    cache[key] = (_time.monotonic(), value)


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
    ) -> List[TrendingWhaleResponse]:
        """List whales, optionally filtered by category."""
        cache_key = f"whales:{category or 'all'}:{user_id or 'anon'}"
        cached = _cache_get(_whale_list_cache, cache_key, WHALE_LIST_CACHE_TTL)
        if cached is not None:
            return cached

        sb = get_supabase()

        # Fetch whales
        query = sb.table("whales").select("*")
        if category:
            query = query.eq("category", category)
        query = query.order("followers_count", desc=True)
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
        cutoff = (datetime.now() - timedelta(days=90)).isoformat()
        trade_counts: Dict[str, int] = {}
        try:
            tg_result = (
                sb.table("whale_trade_groups")
                .select("whale_id, trade_count")
                .gte("created_at", cutoff)
                .execute()
            )
            for tg in tg_result.data or []:
                wid = tg["whale_id"]
                trade_counts[wid] = trade_counts.get(wid, 0) + tg["trade_count"]
        except Exception as e:
            logger.warning("Failed to fetch recent trade counts: %s", e)

        response = [
            TrendingWhaleResponse(
                id=str(w["id"]),
                name=w["name"],
                category=w.get("category", "investors"),
                avatar_url=w.get("avatar_url"),
                followers_count=w.get("followers_count", 0),
                is_following=str(w["id"]) in followed_ids,
                title=w.get("title", ""),
                description=w.get("description", ""),
                recent_trade_count=trade_counts.get(str(w["id"]), 0),
            )
            for w in whales
        ]

        _cache_set(_whale_list_cache, cache_key, response)
        return response

    # ── Cache-Aside Constants ───────────────────────────────────────
    PROFILE_CACHE_TTL_HOURS = 24

    async def get_whale_profile(
        self,
        whale_id: str,
        user_id: Optional[str] = None,
        force_refresh: bool = False,
    ) -> Optional[WhaleProfileResponse]:
        """Get full whale profile — 3-tier cache-aside pattern.

        Tier 1: In-memory dict (1 hr TTL, lost on restart)
        Tier 2: Supabase whale_profile_cache (24 hr TTL, survives restart)
        Tier 3: Live FMP processing → cache result in both tiers

        Follow state is ALWAYS read fresh (not cached) since it's per-user.
        Pass force_refresh=True to bypass all caches and rebuild from FMP.
        """
        sb = get_supabase()
        mem_key = f"profile:{whale_id}"

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
                    cached_at = datetime.fromisoformat(
                        row["cached_at"].replace("Z", "+00:00")
                    )
                    age_hours = (
                        datetime.now(cached_at.tzinfo) - cached_at
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
            except Exception as e:
                logger.warning(
                    "whale_profile_cache read failed for %s: %s", whale_id, e
                )

        # ── Tier 3: Build from FMP + DB (slow, authoritative) ──────
        profile = await self._build_whale_profile(whale_id, user_id)
        if profile is None:
            return None

        # Persist to both caches (without follow state — that's per-user)
        profile_no_follow = profile.model_copy(update={"is_following": False})
        _cache_set(_whale_profile_cache, mem_key, profile_no_follow)
        try:
            sb.table("whale_profile_cache").upsert(
                {
                    "whale_id": whale_id,
                    "profile_json": profile_no_follow.model_dump(),
                    "cached_at": datetime.now().isoformat(),
                },
                on_conflict="whale_id",
            ).execute()
        except Exception as e:
            logger.warning(
                "whale_profile_cache write failed for %s: %s", whale_id, e
            )

        return profile

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
        try:
            snapshot = await self._get_or_process_latest(whale_id, whale)
        except Exception as e:
            logger.error(
                "[whale_profile] All data sources failed for %s (%s): %s",
                whale["name"], whale.get("data_source"), e,
            )

        # Re-read whale record to pick up updates from _sync_to_whale_tables
        try:
            refreshed = sb.table("whales").select("*").eq("id", whale_id).execute()
            if refreshed.data:
                whale = refreshed.data[0]
        except Exception:
            pass

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
                pct = float(s.get("allocation", 0))
                name = s.get("name", "Other")
                if pct < 0.5 or name == "Other":
                    other_pct += pct
                else:
                    sectors.append(
                        WhaleSectorAllocationResponse(
                            id=str(uuid.uuid4()),
                            name=name,
                            percentage=round(pct, 1),
                            color_hex=s.get(
                                "color_hex",
                                SECTOR_COLORS.get(name, DEFAULT_SECTOR_COLOR),
                            ),
                        )
                    )
            if other_pct > 0:
                sectors.append(
                    WhaleSectorAllocationResponse(
                        id=str(uuid.uuid4()),
                        name="Other",
                        percentage=round(other_pct, 1),
                        color_hex=SECTOR_COLORS.get("Other", DEFAULT_SECTOR_COLOR),
                    )
                )

        # Holdings
        holdings: List[WhaleHoldingResponse] = []
        if snapshot and snapshot.get("holdings_data"):
            for h in snapshot["holdings_data"][:30]:
                holdings.append(
                    WhaleHoldingResponse(
                        id=str(uuid.uuid4()),
                        ticker=h.get("ticker", ""),
                        company_name=h.get("company_name", ""),
                        logo_url=h.get("logo_url"),
                        allocation=float(h.get("allocation", 0)),
                        change_percent=float(h.get("change_percent", 0)),
                    )
                )

        # Trade groups from snapshot + DB
        trade_groups: List[WhaleTradeGroupResponse] = []
        all_trades: List[WhaleTradeResponse] = []

        if snapshot and snapshot.get("trade_group"):
            tg = snapshot["trade_group"]
            trades_for_group = self._build_trade_responses(
                tg.get("trades", [])
            )
            trade_groups.append(
                WhaleTradeGroupResponse(
                    id=str(uuid.uuid4()),
                    date=tg.get("date", ""),
                    trade_count=tg.get("trade_count", 0),
                    net_action=tg.get("net_action", "BOUGHT"),
                    net_amount=float(tg.get("net_amount", 0)),
                    summary=tg.get("summary"),
                    insights=tg.get("insights", []),
                    trades=trades_for_group,
                )
            )
            all_trades.extend(trades_for_group)

        # Historical trade groups from DB
        try:
            db_groups = (
                sb.table("whale_trade_groups")
                .select("*")
                .eq("whale_id", whale_id)
                .order("created_at", desc=True)
                .limit(10)
                .execute()
            )
            for tg in db_groups.data or []:
                if any(g.date == tg.get("date", "") for g in trade_groups):
                    continue
                db_trades = (
                    sb.table("whale_trades")
                    .select("*")
                    .eq("trade_group_id", tg["id"])
                    .order("amount", desc=True)
                    .execute()
                )
                trades_for_group = self._build_trade_responses_from_db(
                    db_trades.data or []
                )
                trade_groups.append(
                    WhaleTradeGroupResponse(
                        id=str(tg["id"]),
                        date=tg.get("date", ""),
                        trade_count=tg.get("trade_count", 0),
                        net_action=tg.get("net_action", "BOUGHT"),
                        net_amount=float(tg.get("net_amount", 0)),
                        summary=tg.get("summary"),
                        insights=tg.get("insights") or [],
                        trades=trades_for_group,
                    )
                )
                all_trades.extend(trades_for_group)
        except Exception as e:
            logger.warning(
                "[whale_profile] DB trade groups failed for %s: %s",
                whale_id, e,
            )

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

        portfolio_value = float(
            (snapshot or {}).get("total_value")
            or whale.get("portfolio_value")
            or 0
        )
        ytd_return = float(whale.get("ytd_return") or 0)

        profile = WhaleProfileResponse(
            id=str(whale["id"]),
            name=whale["name"],
            title=whale.get("title", ""),
            description=whale.get("description", ""),
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
            data_source=whale.get("data_source", ""),
            return_source=whale.get("return_source", ""),
            return_label=whale.get("return_label", ""),
        )

        return profile

    async def get_whale_activity_feed(
        self, user_id: str
    ) -> List[WhaleTradeGroupActivityResponse]:
        """Get recent trade activity from user's followed whales."""
        cache_key = f"activity:{user_id}"
        cached = _cache_get(
            _whale_activity_cache, cache_key, WHALE_ACTIVITY_CACHE_TTL
        )
        if cached is not None:
            return cached

        sb = get_supabase()

        # Get followed whale IDs
        follows = (
            sb.table("whale_follows")
            .select("whale_id")
            .eq("user_id", user_id)
            .execute()
        )
        whale_ids = [f["whale_id"] for f in (follows.data or [])]
        if not whale_ids:
            return []

        # Fetch trade groups for followed whales
        trade_groups = (
            sb.table("whale_trade_groups")
            .select("*")
            .in_("whale_id", whale_ids)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )

        # Fetch whale names
        whales = (
            sb.table("whales")
            .select("id, name, avatar_url, category")
            .in_("id", whale_ids)
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
                    entity_name=whale.get("name", "Unknown"),
                    entity_avatar_name=whale.get("avatar_url", ""),
                    category=whale.get("category"),
                    action=tg.get("net_action", "BOUGHT"),
                    trade_count=tg.get("trade_count", 0),
                    total_amount=_format_amount(
                        float(tg.get("net_amount", 0)),
                        tg.get("net_action", "BOUGHT"),
                    ),
                    summary=tg.get("summary"),
                    date=tg.get("date", ""),
                )
            )

        _cache_set(_whale_activity_cache, cache_key, response)
        return response

    async def get_trade_groups(
        self, whale_id: str
    ) -> List[WhaleTradeGroupResponse]:
        """Get all trade groups for a whale."""
        sb = get_supabase()
        result = (
            sb.table("whale_trade_groups")
            .select("*")
            .eq("whale_id", whale_id)
            .order("created_at", desc=True)
            .limit(20)
            .execute()
        )

        groups = []
        for tg in result.data or []:
            db_trades = (
                sb.table("whale_trades")
                .select("*")
                .eq("trade_group_id", tg["id"])
                .order("amount", desc=True)
                .execute()
            )
            groups.append(
                WhaleTradeGroupResponse(
                    id=str(tg["id"]),
                    date=tg.get("date", ""),
                    trade_count=tg.get("trade_count", 0),
                    net_action=tg.get("net_action", "BOUGHT"),
                    net_amount=float(tg.get("net_amount", 0)),
                    summary=tg.get("summary"),
                    insights=tg.get("insights") or [],
                    trades=self._build_trade_responses_from_db(
                        db_trades.data or []
                    ),
                )
            )
        return groups

    async def get_trade_group_detail(
        self, whale_id: str, group_id: str
    ) -> Optional[WhaleTradeGroupResponse]:
        """Get a single trade group with all trades."""
        sb = get_supabase()
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
            .execute()
        )

        return WhaleTradeGroupResponse(
            id=str(tg["id"]),
            date=tg.get("date", ""),
            trade_count=tg.get("trade_count", 0),
            net_action=tg.get("net_action", "BOUGHT"),
            net_amount=float(tg.get("net_amount", 0)),
            summary=tg.get("summary"),
            insights=tg.get("insights") or [],
            trades=self._build_trade_responses_from_db(
                db_trades.data or []
            ),
        )

    async def toggle_follow(
        self, user_id: str, whale_id: str, follow: bool
    ) -> FollowResponse:
        """Follow or unfollow a whale."""
        sb = get_supabase()

        if follow:
            sb.table("whale_follows").upsert(
                {"user_id": user_id, "whale_id": whale_id},
                on_conflict="user_id,whale_id",
            ).execute()
        else:
            sb.table("whale_follows").delete().eq(
                "user_id", user_id
            ).eq("whale_id", whale_id).execute()

        # Read updated followers count
        whale = (
            sb.table("whales")
            .select("followers_count")
            .eq("id", whale_id)
            .execute()
        )
        count = (
            whale.data[0]["followers_count"] if whale.data else 0
        )

        # Invalidate caches
        _whale_list_cache.clear()
        _whale_activity_cache.clear()
        _whale_profile_cache.clear()

        return FollowResponse(is_following=follow, followers_count=count)

    async def get_whale_alerts(
        self, user_id: Optional[str] = None
    ) -> Optional[WhaleAlertBannerResponse]:
        """Get most recent active whale alert, optionally scoped to followed whales."""
        sb = get_supabase()

        try:
            query = (
                sb.table("whale_alerts")
                .select("*")
                .eq("is_active", True)
                .order("created_at", desc=True)
                .limit(1)
            )

            # If user is logged in, prefer alerts from followed whales
            if user_id:
                follows = (
                    sb.table("whale_follows")
                    .select("whale_id")
                    .eq("user_id", user_id)
                    .execute()
                )
                whale_ids = [f["whale_id"] for f in (follows.data or [])]
                if whale_ids:
                    # Try followed whales first
                    followed_result = (
                        sb.table("whale_alerts")
                        .select("*")
                        .eq("is_active", True)
                        .in_("whale_id", whale_ids)
                        .order("created_at", desc=True)
                        .limit(1)
                        .execute()
                    )
                    if followed_result.data:
                        alert = followed_result.data[0]
                        return WhaleAlertBannerResponse(
                            id=str(alert["id"]),
                            title=alert["title"],
                            description=alert["description"],
                            ticker=alert.get("ticker"),
                            action_title=alert.get("action_title", "View Full Alert"),
                        )

            # Fallback: any active alert
            result = query.execute()
            if result.data:
                alert = result.data[0]
                # Check expiry
                expires = alert.get("expires_at")
                if expires:
                    from datetime import timezone
                    exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                    if exp_dt < datetime.now(timezone.utc):
                        return None
                return WhaleAlertBannerResponse(
                    id=str(alert["id"]),
                    title=alert["title"],
                    description=alert["description"],
                    ticker=alert.get("ticker"),
                    action_title=alert.get("action_title", "View Full Alert"),
                )
        except Exception as e:
            logger.warning("Failed to fetch whale alerts: %s", e)

        return None

    # ── Dual-Source Router ───────────────────────────────────────────

    async def _get_or_process_latest(
        self, whale_id: str, whale: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Route to correct FMP source based on data_source column.

        Prefers pre-hydrated snapshots when available (set by
        scripts/hydrate_whales.py). Falls through to live FMP
        processing only if no snapshot exists.
        """
        # Prefer pre-hydrated snapshot if the hydration engine has run
        if whale.get("last_hydrated_at"):
            snapshot = await self._read_from_supabase(whale_id)
            if snapshot:
                return snapshot

        data_source = whale.get("data_source", "manual")

        try:
            if data_source == "13f":
                return await self._process_13f_path(whale_id, whale["cik"], whale=whale)
            elif data_source in ("congressional_house", "congressional_senate"):
                chamber = "house" if "house" in data_source else "senate"
                return await self._process_congressional_path(
                    whale_id, whale["fmp_name"], chamber
                )
            else:
                return await self._read_from_supabase(whale_id)
        except Exception as e:
            logger.error(
                "Failed to process whale %s (source=%s): %s",
                whale_id,
                data_source,
                e,
            )
            try:
                return await self._read_from_supabase(whale_id)
            except Exception as fallback_err:
                logger.error(
                    "Supabase fallback also failed for whale %s: %s",
                    whale_id,
                    fallback_err,
                )
                return None

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
        trade_group = self._diff_quarters(
            current_raw, prev_raw, filing_date, total_value
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
            whale_id, holdings_data, sector_data, trade_group,
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
        now = datetime.now()
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

        # Aggregate trades
        holdings_data, trade_group, sector_data = (
            self._aggregate_congressional_trades(raw_trades, now.isoformat()[:10])
        )
        total_value = sum(h.get("value", 0) for h in holdings_data)

        # Single enrichment pass: logos + names + sectors
        holdings_data, enriched_sectors = await self._enrich_from_profiles(
            holdings_data, need_sectors=(not sector_data),
        )
        if not sector_data and enriched_sectors:
            sector_data = enriched_sectors

        behavior = self._generate_behavior_summary(trade_group, sector_data)
        sentiment = self._generate_sentiment_summary(
            holdings_data, trade_group, sector_data
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
            logger.error("Failed to persist congressional snapshot: %s", e)

        await self._sync_to_whale_tables(
            whale_id, holdings_data, sector_data, trade_group,
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

    def _diff_quarters(
        self,
        current_raw: List[Dict],
        previous_raw: List[Dict],
        filing_date: str,
        total_current_value: float,
    ) -> Optional[Dict[str, Any]]:
        """Diff two 13F snapshots to compute individual trades."""
        if not current_raw:
            return None

        # Build ticker maps
        current_map: Dict[str, Dict] = {}
        for h in current_raw:
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if not sym or sym == "--":
                continue
            current_map[sym] = {
                "symbol": sym,
                "name": h.get("securityName") or h.get("companyName") or sym,
                "value": float(h.get("value") or 0),
                "shares": int(float(h.get("sharesNumber") or h.get("shares") or 0)),
            }

        prev_map: Dict[str, Dict] = {}
        prev_total = 0.0
        for h in previous_raw:
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if not sym or sym == "--":
                continue
            val = float(h.get("value") or 0)
            prev_map[sym] = {
                "symbol": sym,
                "name": h.get("securityName") or h.get("companyName") or sym,
                "value": val,
                "shares": int(float(h.get("sharesNumber") or h.get("shares") or 0)),
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
    ) -> Tuple[List[Dict], Optional[Dict], List[Dict]]:
        """Aggregate congressional trades into holdings + trade group + sectors.

        Uses a chronological walk (oldest → newest) to reconstruct
        `previous_allocation` and `new_allocation` at each trade. This is a
        directional estimate, NOT a live portfolio:

        - Starting portfolio assumed empty (STOCK Act has no pre-disclosure baseline).
        - Dollar values use bucket midpoints — absolute amounts drift 20–40% per trade.
        - No market-price adjustment between disclosures (trade-flow only).
        - Sales exceeding known holdings clamp to zero (no negative allocations).

        The raw STOCK Act bucket string (e.g. ``"$1,001 - $15,000"``) is
        preserved on each trade as ``amount_range`` so the UI can display the
        honest range instead of the midpoint.
        """
        full_sale_types = {"sale_full", "sale (full)"}

        # ── Pass 1: normalize + sort by date ────────────────────────────
        normalized: List[Dict] = []
        for t in raw_trades:
            symbol = (t.get("symbol") or "").upper().strip()
            if not symbol or symbol == "--" or symbol == "N/A":
                continue

            raw_type = (t.get("type") or "").lower().strip()
            action = CONGRESSIONAL_TYPE_MAP.get(raw_type, "BOUGHT")
            amount_range = t.get("amount") or "$1,001 - $15,000"
            amount = parse_congress_amount_dollars(amount_range)
            if amount == 0.0:
                amount = 8_000  # safety fallback
            tx_date = (
                t.get("transactionDate")
                or t.get("transaction_date")
                or as_of_date
            )
            name = t.get("assetDescription") or t.get("asset_description") or symbol

            normalized.append({
                "ticker": symbol,
                "company_name": name,
                "action": action,
                "raw_type": raw_type,
                "amount": amount,
                "amount_range": amount_range,
                "date": tx_date,
            })

        # Sort oldest → newest; stable preserves FMP order for same-day trades
        normalized.sort(key=lambda t: t["date"])

        # ── Pass 2: chronological walk with running portfolio ──────────
        running_portfolio: Dict[str, float] = {}
        seen_tickers: set = set()
        trades: List[Dict] = []

        for t in normalized:
            symbol = t["ticker"]
            action = t["action"]
            raw_type = t["raw_type"]
            amount = t["amount"]

            # Trade type classification (uses running portfolio knowledge)
            if action == "BOUGHT" and symbol not in seen_tickers:
                trade_type = "New"
            elif action == "SOLD" and raw_type in full_sale_types:
                trade_type = "Closed"
            elif action == "BOUGHT":
                trade_type = "Increased"
            else:
                trade_type = "Decreased"
            seen_tickers.add(symbol)

            # Allocation BEFORE applying this trade
            total_before = sum(running_portfolio.values())
            prev_value = running_portfolio.get(symbol, 0.0)
            previous_allocation = (
                round(prev_value / total_before * 100, 2)
                if total_before > 0
                else 0.0
            )

            # Apply trade to running portfolio; clamp at zero on oversell
            if action == "BOUGHT":
                running_portfolio[symbol] = prev_value + amount
            else:
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
                "previous_allocation": previous_allocation,
                "new_allocation": new_allocation,
                "date": t["date"],
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

        # Build trade group from recent trades (last 90 days relative to as_of_date)
        try:
            as_of_dt = datetime.strptime(as_of_date, "%Y-%m-%d")
        except (TypeError, ValueError):
            as_of_dt = datetime.now()
        cutoff = (as_of_dt - timedelta(days=90)).strftime("%Y-%m-%d")
        recent = [t for t in trades if (t.get("date", "") >= cutoff)]
        if not recent:
            recent = trades[:20]

        total_bought = sum(t["amount"] for t in recent if t["action"] == "BOUGHT")
        total_sold = sum(t["amount"] for t in recent if t["action"] == "SOLD")
        net_dollar = total_bought - total_sold
        net_action = "BOUGHT" if net_dollar >= 0 else "SOLD"

        new_count = sum(1 for t in recent if t["trade_type"] == "New")
        closed_count = sum(1 for t in recent if t["trade_type"] == "Closed")
        summary = self._generate_trade_group_summary(
            recent, new_count, closed_count, net_action
        )
        insights = self._generate_trade_group_insights(
            recent, new_count, closed_count, total_bought, total_sold
        )

        # Use the most recent trade date (not today) to avoid duplicate groups
        trade_group_date = max(
            (t.get("date", as_of_date) for t in recent), default=as_of_date
        )

        trade_group = {
            "date": trade_group_date,
            "trade_count": len(recent),
            "net_action": net_action,
            "net_amount": abs(net_dollar),
            "summary": summary,
            "insights": insights,
            "trades": sorted(recent, key=lambda t: t["amount"], reverse=True)[:50],
        } if recent else None

        # Sectors (basic — from holdings tickers)
        sectors: List[Dict] = []

        return holdings[:30], trade_group, sectors

    # ── Build Helpers ────────────────────────────────────────────────

    def _build_holdings(
        self, raw_holdings: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Transform raw FMP 13F holdings into UI shape."""
        total = sum(float(h.get("value") or 0) for h in raw_holdings)
        if total <= 0:
            return []

        holdings = []
        for h in raw_holdings:
            val = float(h.get("value") or 0)
            if val <= 0:
                continue
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if not sym or sym == "--":
                continue
            holdings.append({
                "ticker": sym,
                "company_name": (
                    h.get("securityName") or h.get("companyName") or sym
                ),
                "logo_url": None,
                "allocation": round(val / total * 100, 2),
                "change_percent": 0,
                "value": val,
                "shares": int(float(h.get("sharesNumber") or h.get("shares") or 0)),
            })

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
                    "allocation": round(weight, 1),
                    "color_hex": SECTOR_COLORS.get(name, DEFAULT_SECTOR_COLOR),
                })
        named.sort(key=lambda x: x["allocation"], reverse=True)

        if other_weight > 0:
            named.append({
                "name": "Other",
                "allocation": round(other_weight, 1),
                "color_hex": DEFAULT_SECTOR_COLOR,
            })

        return named[:11]

    def _apply_change_percent(
        self,
        holdings: List[Dict],
        prev_raw: List[Dict],
    ) -> List[Dict]:
        """Compute change_percent by comparing current vs previous quarter allocations."""
        prev_total = sum(float(h.get("value") or 0) for h in prev_raw)
        if prev_total <= 0:
            return holdings

        prev_alloc: Dict[str, float] = {}
        for h in prev_raw:
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if sym and sym != "--":
                val = float(h.get("value") or 0)
                prev_alloc[sym] = val / prev_total * 100

        for h in holdings:
            prev_pct = prev_alloc.get(h["ticker"], 0)
            curr_pct = h.get("allocation", 0)
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
            now = datetime.now(datetime.now().astimezone().tzinfo)
            for row in cache_result.data or []:
                cached_at = datetime.fromisoformat(
                    row["cached_at"].replace("Z", "+00:00")
                )
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
                for p in fmp_profiles:
                    sym = (p.get("symbol") or "").upper()
                    if sym:
                        profile_map[sym] = p
                        # Persist to cache for next time
                        try:
                            sb.table("company_profile_cache").upsert(
                                {
                                    "ticker": sym,
                                    "profile_json": p,
                                    "cached_at": datetime.now().isoformat(),
                                },
                                on_conflict="ticker",
                            ).execute()
                        except Exception:
                            pass
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
                        "allocation": round(weight, 1),
                        "color_hex": SECTOR_COLORS.get(name, DEFAULT_SECTOR_COLOR),
                    })
            named.sort(key=lambda x: x["allocation"], reverse=True)
            if other_weight > 0:
                named.append({
                    "name": "Other",
                    "allocation": round(other_weight, 1),
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
                ticker=t.get("ticker", ""),
                company_name=t.get("company_name", ""),
                action=t.get("action", "BOUGHT"),
                trade_type=t.get("trade_type", "Increased"),
                amount=float(t.get("amount", 0)),
                amount_range=t.get("amount_range"),
                previous_allocation=float(t.get("previous_allocation", 0)),
                new_allocation=float(t.get("new_allocation", 0)),
                date=t.get("date", ""),
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
                ticker=t.get("ticker", ""),
                company_name=t.get("company_name", ""),
                action=t.get("action", "BOUGHT"),
                trade_type=t.get("trade_type", "Increased"),
                amount=float(t.get("amount", 0)),
                amount_range=t.get("amount_range"),
                previous_allocation=float(t.get("previous_allocation") or 0),
                new_allocation=float(t.get("new_allocation") or 0),
                date=t.get("date", ""),
            )
            for t in db_trades
        ]

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
    ) -> str:
        """Generate a rule-based sentiment summary paragraph."""
        top_tickers = ", ".join(h["ticker"] for h in holdings[:5])
        top_sector = sector_data[0]["name"] if sector_data else "various sectors"

        if trade_group:
            activity = trade_group.get("summary", "active rebalancing")
            net_action = trade_group.get("net_action", "BOUGHT")
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

    async def _compute_ticker_cagr(
        self, ticker: str
    ) -> Optional[float]:
        """Compute long-term CAGR from a stock's max price change + IPO date."""
        try:
            price_change = await self.fmp.get_stock_price_change(ticker)
            max_return = price_change.get("max")
            if max_return is None or max_return <= -100:
                return None

            # Get IPO date from company profile
            profiles = await self.fmp.get_company_profiles_batch([ticker])
            if not profiles:
                return None
            ipo_date_str = profiles[0].get("ipoDate")
            if not ipo_date_str:
                return None

            ipo_date = datetime.strptime(ipo_date_str, "%Y-%m-%d")
            years = (datetime.now() - ipo_date).days / 365.25
            if years < 1:
                return None

            total_multiplier = 1 + max_return / 100
            if total_multiplier <= 0:
                return None
            cagr = (total_multiplier ** (1 / years) - 1) * 100
            return round(cagr, 1)
        except Exception as e:
            logger.warning("Ticker CAGR failed for %s: %s", ticker, e)
            return None

    async def _compute_best_annual_return(
        self,
        whale: Dict,
        perf_list: List[Dict],
    ) -> Tuple[Optional[float], str, str]:
        """Tiered annual return: associated ticker CAGR → 13F avg → N/A.

        Returns: (return_value, return_source, return_label)
        """
        # Tier 1: Associated ticker CAGR
        ticker = whale.get("associated_ticker")
        if ticker:
            cagr = await self._compute_ticker_cagr(ticker)
            if cagr is not None:
                return cagr, "stock_cagr", f"{ticker} CAGR"

        # Tier 2: 13F portfolio CAGR (compound annualized)
        cagr = self._compute_avg_annual_return(perf_list)
        if cagr is not None:
            return cagr, "13f_avg", "13F Portfolio CAGR"

        return None, "", ""

    @staticmethod
    def _compute_avg_annual_return(perf_list: List[Dict]) -> Optional[float]:
        """Compute compound annualized return (CAGR) from year-end 13F records.

        Takes Q4 (Dec 31) snapshots' 1-year performance as calendar-year
        returns, then compounds them geometrically to produce a true CAGR
        over the observed history. Filters outliers (|return| > 200%).
        """
        if not perf_list:
            return None

        year_end_returns = [
            d["performancePercentage1year"]
            for d in perf_list
            if d.get("date", "").endswith("-12-31")
            and d.get("performancePercentage1year") is not None
            and -200 < d["performancePercentage1year"] < 500
        ]

        if not year_end_returns:
            # Fallback: use latest 1-year return if no year-ends available
            latest = perf_list[0] if perf_list else {}
            val = latest.get("performancePercentage1year")
            if val is not None and -200 < val < 500:
                return round(float(val), 2)
            return None

        product = math.prod(1 + r / 100 for r in year_end_returns)
        if product <= 0:
            return None
        cagr = (product ** (1 / len(year_end_returns)) - 1) * 100
        return round(cagr, 2)

    async def _sync_to_whale_tables(
        self,
        whale_id: str,
        holdings: List[Dict],
        sectors: List[Dict],
        trade_group: Optional[Dict],
        behavior: Dict,
        sentiment: str,
        total_value: float,
        perf_data: Any,
        whale: Optional[Dict] = None,
    ) -> None:
        """Write aggregated data into the existing whale_* tables."""
        sb = get_supabase()

        try:
            # Update whale record
            whale_update: Dict[str, Any] = {
                "portfolio_value": total_value,
                "behavior_summary": behavior,
                "sentiment_summary": sentiment,
            }

            # Compute best annual return (tiered: ticker CAGR → 13F avg)
            perf_list = perf_data if isinstance(perf_data, list) else []
            if whale and whale.get("associated_ticker"):
                ret_val, ret_source, ret_label = await self._compute_best_annual_return(
                    whale, perf_list
                )
            else:
                ret_val = self._compute_avg_annual_return(perf_list)
                ret_source = "13f_avg" if ret_val is not None else ""
                ret_label = "13F Portfolio Avg." if ret_val is not None else ""

            if ret_val is not None:
                whale_update["ytd_return"] = ret_val
            if ret_source:
                whale_update["return_source"] = ret_source
            if ret_label:
                whale_update["return_label"] = ret_label
            sb.table("whales").update(whale_update).eq("id", whale_id).execute()

            # Upsert holdings
            sb.table("whale_holdings").delete().eq(
                "whale_id", whale_id
            ).execute()
            for h in holdings[:30]:
                sb.table("whale_holdings").insert({
                    "whale_id": whale_id,
                    "ticker": h["ticker"],
                    "company_name": h.get("company_name", h["ticker"]),
                    "logo_url": h.get("logo_url"),
                    "allocation": h.get("allocation", 0),
                    "change_percent": h.get("change_percent", 0),
                }).execute()

            # Upsert sector allocations
            sb.table("whale_sector_allocations").delete().eq(
                "whale_id", whale_id
            ).execute()
            for s in sectors:
                sb.table("whale_sector_allocations").insert({
                    "whale_id": whale_id,
                    "sector": s["name"],
                    "allocation": s["allocation"],
                }).execute()

            # Insert trade group + trades
            if trade_group:
                existing_tg = (
                    sb.table("whale_trade_groups")
                    .select("id")
                    .eq("whale_id", whale_id)
                    .eq("date", trade_group["date"])
                    .execute()
                )
                if not existing_tg.data:
                    tg_result = sb.table("whale_trade_groups").insert({
                        "whale_id": whale_id,
                        "date": trade_group["date"],
                        "trade_count": trade_group["trade_count"],
                        "net_action": trade_group["net_action"],
                        "net_amount": trade_group["net_amount"],
                        "summary": trade_group.get("summary"),
                        "insights": trade_group.get("insights", []),
                    }).execute()

                    if tg_result.data:
                        tg_id = tg_result.data[0]["id"]
                        for trade in trade_group.get("trades", [])[:50]:
                            sb.table("whale_trades").insert({
                                "whale_id": whale_id,
                                "trade_group_id": tg_id,
                                "ticker": trade["ticker"],
                                "company_name": trade.get(
                                    "company_name", trade["ticker"]
                                ),
                                "action": trade["action"],
                                "trade_type": trade["trade_type"],
                                "amount": trade["amount"],
                                "previous_allocation": trade.get(
                                    "previous_allocation"
                                ),
                                "new_allocation": trade.get("new_allocation"),
                                "date": trade.get("date", ""),
                            }).execute()

        except Exception as e:
            logger.error(
                "Failed to sync whale tables for %s: %s", whale_id, e
            )


# ── Module-Level Helpers ─────────────────────────────────────────────


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
    """Format a dollar amount for display: +$4.34B, -$2.1M, etc."""
    prefix = "+" if action == "BOUGHT" else "-"
    abs_val = abs(value)

    if abs_val >= 1_000_000_000:
        formatted = f"${abs_val / 1_000_000_000:.2f}B"
    elif abs_val >= 1_000_000:
        formatted = f"${abs_val / 1_000_000:.1f}M"
    elif abs_val >= 1_000:
        formatted = f"${abs_val / 1_000:.0f}K"
    else:
        formatted = f"${abs_val:,.0f}"

    return f"{prefix}{formatted}"
