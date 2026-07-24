"""
Insiders & Ownership Snapshot service — reuses the existing HoldersService
(Holders tab) to extract shareholder breakdown and smart money flow signals.

Metrics displayed:
  1. Institutional Ownership %
  2. Insider Ownership %
  3. Insider Activity (12M net flow)
  4. Institutional Activity (12M net flow)
  5. Public & Other %

Rating (1-5) based on insider/institutional activity signals:
  insider buying = bullish signal.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``snapshot_cache`` table (24-hour TTL)

Matches the iOS SnapshotItemDTO struct.
"""

import asyncio
import logging
import math
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────
_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300  # 5 minutes


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > _CACHE_TTL:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


# ── In-flight deduplication ───────────────────────────────────────
_inflight: Dict[str, asyncio.Future] = {}

# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


# ── Helpers ──────────────────────────────────────────────────────

def _fmt_pct(val: Optional[float]) -> str:
    """Format ownership percentage."""
    if val is None or val == 0:
        return "—"
    return f"{val:.1f}%"


def _fmt_share_flow(net_flow_millions: float, is_positive: bool) -> str:
    """Format a smart-money net flow that is denominated in MILLIONS OF SHARES.

    Both ``insider_data.summary`` and ``hedge_funds_data.summary`` on the Holders
    response carry share counts, not dollars: ``_build_insider_smart_money`` and
    ``_build_hedge_fund_smart_money`` sum ``securitiesTransacted`` /
    ``numberOf13FsharesChange`` divided by 1e6 (see ``_HFQ_SHARES_FLOOR``).

    The previous implementation treated the value as raw DOLLARS, which was wrong
    twice over: the unit ("$" on a share count) and the scale (a 2.5-million-share
    net buy arrives as ``2.5`` and fell into the ``else`` branch as "Net Buy $2").
    Every real ticker rendered "$0"–"$900" on the Overview card while the Holders
    tab showed "+2.50M shares" for the same quantity.

    Mirrors the iOS ``SmartMoneyFlowSummary.formattedNetFlow`` ``.shares`` branch
    so the Overview snapshot and the Holders tab read identically.
    """
    if not math.isfinite(net_flow_millions):
        return "—"
    mag = abs(net_flow_millions)
    if mag == 0:
        return "Neutral"
    if mag >= 1_000:
        formatted = f"{mag / 1_000:.2f}B shares"
    elif mag >= 1:
        formatted = f"{mag:.2f}M shares"
    else:
        formatted = f"{mag * 1_000:.0f}K shares"
    label = "Net Buy" if is_positive else "Net Sell"
    return f"{label} {formatted}"


# ── Service ───────────────────────────────────────────────────────

class OwnershipSnapshotService:
    def __init__(self):
        self.supabase = get_supabase()

    async def get_ownership_snapshot(self, ticker: str) -> SnapshotItemResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"ownership_snapshot:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Ownership snapshot in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Ownership snapshot Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Ownership snapshot in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Ownership snapshot cache MISS for {ticker} — computing")
            result = await self._compute(ticker)

            asyncio.get_running_loop().run_in_executor(
                None, self._upsert_supabase_cache, ticker, result,
            )

            _cache_set(cache_key, result)
            future.set_result(result)
            return result
        except Exception as e:
            future.set_exception(e)
            raise
        finally:
            _inflight.pop(cache_key, None)

    # ── Supabase helpers ──────────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[SnapshotItemResponse]:
        try:
            row = (
                self.supabase.table("snapshot_cache")
                .select("response_json, cached_at")
                .eq("ticker", ticker)
                .eq("category", "Insiders & Ownership")
                .limit(1)
                .execute()
            )
            if not row.data:
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Ownership snapshot Supabase STALE (age={age}) for {ticker}")
                return None

            json_data = entry["response_json"]
            return SnapshotItemResponse(**json_data)

        except Exception as e:
            logger.warning(f"Ownership snapshot cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache(self, ticker: str, result: SnapshotItemResponse) -> None:
        try:
            self.supabase.table("snapshot_cache").upsert(
                {
                    "ticker": ticker,
                    "category": "Insiders & Ownership",
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker,category",
            ).execute()
        except Exception as e:
            logger.warning(f"Ownership snapshot upsert failed for {ticker}: {e}")

    # ── Core computation ──────────────────────────────────────────

    async def _compute(self, ticker: str) -> SnapshotItemResponse:
        """Reuse HoldersService (Holders tab) to get exact same data the user sees."""
        from app.services.holders_service import get_holders_service

        holders = await get_holders_service().get_holders(ticker)

        # Extract shareholder breakdown
        breakdown = holders.shareholder_breakdown
        inst_pct = breakdown.institutions_percent
        insider_pct = breakdown.insiders_percent
        public_pct = breakdown.public_other_percent

        # Extract smart money flow summaries
        insider_summary = holders.insider_data.summary
        # `hedge_funds_data` = FMP 13F institutional ownership (UI label
        # "Institutions"); hence the `inst_summary` name here.
        inst_summary = holders.hedge_funds_data.summary

        insider_flow = insider_summary.total_net_flow
        insider_positive = insider_summary.is_positive
        inst_flow = inst_summary.total_net_flow
        inst_positive = inst_summary.is_positive

        # Build metrics
        metrics = [
            SnapshotMetricResponse(
                name="Institutional Ownership",
                value=_fmt_pct(inst_pct),
            ),
            SnapshotMetricResponse(
                name="Insider Ownership",
                value=_fmt_pct(insider_pct),
            ),
            SnapshotMetricResponse(
                name="Insider Activity (12M)",
                value=_fmt_share_flow(insider_flow, insider_positive),
            ),
            SnapshotMetricResponse(
                # Label the REAL window. `hedge_funds_data.summary` spans 8
                # quarters (period_description == "2-Year"); the bare
                # "Institutional Activity" read as if it matched the 12M insider
                # row above it.
                name="Institutional Activity (2Y)",
                value=_fmt_share_flow(inst_flow, inst_positive),
            ),
            SnapshotMetricResponse(
                name="Public & Other",
                value=_fmt_pct(public_pct),
            ),
        ]

        # Weighted rating across 4 factors
        rating = self._compute_rating(
            insider_pct, inst_pct,
            insider_flow, insider_positive,
            inst_flow, inst_positive,
        )

        return SnapshotItemResponse(
            category="Insiders & Ownership",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
        )

    def _compute_rating(
        self,
        insider_pct: float,
        inst_pct: float,
        insider_flow: float,
        insider_positive: bool,
        inst_flow: float,
        inst_positive: bool,
    ) -> int:
        """
        Weighted rating across 4 factors:
          - Insider Ownership level (25%)
          - Institutional Ownership level (15%)
          - Insider Activity / net flow (40%) — strongest signal
          - Institutional Activity / net flow (20%)
        """
        # 1. Insider Ownership score (25%)
        #    1-5% ideal (skin in the game), 0% bad, >30% risky
        if insider_pct <= 0:
            s_insider_own = 1
        elif insider_pct < 1:
            s_insider_own = 2
        elif insider_pct <= 10:
            s_insider_own = 5  # sweet spot
        elif insider_pct <= 20:
            s_insider_own = 4
        elif insider_pct <= 30:
            s_insider_own = 3
        else:
            s_insider_own = 2  # too concentrated

        # 2. Institutional Ownership score (15%)
        #    40-80% healthy, <20% low confidence, >90% crowded
        if inst_pct < 10:
            s_inst_own = 1
        elif inst_pct < 20:
            s_inst_own = 2
        elif inst_pct < 40:
            s_inst_own = 3
        elif inst_pct <= 80:
            s_inst_own = 5  # sweet spot
        elif inst_pct <= 90:
            s_inst_own = 3
        else:
            s_inst_own = 2  # crowded

        # 3. Insider Activity score (40%) — strongest signal
        #    Net buying = very bullish, net selling = could be routine or bearish.
        #
        #    UNITS: `insider_flow` is MILLIONS OF SHARES (Form 4
        #    `securitiesTransacted` / 1e6), not dollars. The old thresholds
        #    (10_000_000 / 50_000_000) were dollar-scale, so a real 2.5M-share
        #    net sale arrived as 2.5 and could never clear them — the whole
        #    40%-weight factor was pinned to 4 (any buying) or 3 (any selling)
        #    on every ticker in the app. Tiers below are share counts.
        abs_insider = abs(insider_flow)
        if abs_insider == 0:
            s_insider_act = 3  # no activity = neutral
        elif insider_positive:
            # Buying — scale by magnitude
            if abs_insider >= 0.1:
                s_insider_act = 5  # meaningful buying (100K+ shares)
            else:
                s_insider_act = 4  # token buying
        else:
            # Selling — scale by magnitude
            if abs_insider >= 5.0:
                s_insider_act = 1  # heavy selling (5M+ shares)
            elif abs_insider >= 0.5:
                s_insider_act = 2  # notable selling (500K+ shares)
            else:
                s_insider_act = 3  # minor selling (likely routine)

        # 4. Institutional Activity score (20%) — also MILLIONS OF SHARES, over
        #    8 quarters. Same dead-threshold bug as above (1_000_000_000 could
        #    never be reached by a share count in millions).
        abs_inst = abs(inst_flow)
        if abs_inst == 0:
            s_inst_act = 3  # neutral
        elif inst_positive:
            s_inst_act = 5 if abs_inst >= 10.0 else 4  # 10M+ net shares added
        else:
            s_inst_act = 1 if abs_inst >= 10.0 else 2

        # Weighted average
        weighted = (
            s_insider_own * 0.25
            + s_inst_own * 0.15
            + s_insider_act * 0.40
            + s_inst_act * 0.20
        )
        return max(1, min(5, round(weighted)))


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[OwnershipSnapshotService] = None


def get_ownership_snapshot_service() -> OwnershipSnapshotService:
    global _service
    if _service is None:
        _service = OwnershipSnapshotService()
    return _service
