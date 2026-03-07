#!/usr/bin/env python3
"""
Whale Data Hydration Engine
============================
Pre-computes all whale profiles so the API serves instant, fully-populated
payloads to the WhaleProfileView screen.

Pipeline per whale:
  1. Fetch raw data from FMP (13F or Congressional)
  2. Build holdings, diff quarters for trades, compute sectors
  3. Enrich holdings with company logos (batch FMP profiles)
  4. Calculate change_percent (current vs previous allocation)
  5. Fill sector data for politicians (from company profiles)
  6. Generate AI behavior + sentiment summaries via Gemini
  7. Persist to whale_filing_snapshots + denormalized tables

Usage:
    cd backend
    python -m scripts.hydrate_whales              # All whales
    python -m scripts.hydrate_whales --whale-id X # Single whale
    python -m scripts.hydrate_whales --force       # Skip hash dedup
    python -m scripts.hydrate_whales --dry-run     # No DB writes
"""

import asyncio
import argparse
import hashlib
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Ensure backend app package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402
from app.database import get_supabase  # noqa: E402
from app.integrations.fmp import FMPClient  # noqa: E402
from app.integrations.gemini import GeminiClient  # noqa: E402

logger = logging.getLogger("hydrate_whales")

# ── Rate-Limit Controls ──────────────────────────────────────────────

FMP_SEMAPHORE = asyncio.Semaphore(5)
GEMINI_SEMAPHORE = asyncio.Semaphore(3)
FMP_BATCH_SIZE = 30

# ── Sector Color Map (mirrors whale_service.py) ─────────────────────

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

# Congressional amount range → midpoint
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

CONGRESSIONAL_TYPE_MAP: Dict[str, str] = {
    "purchase": "BOUGHT",
    "sale_full": "SOLD",
    "sale_partial": "SOLD",
    "sale (full)": "SOLD",
    "sale (partial)": "SOLD",
    "sale": "SOLD",
    "exchange": "BOUGHT",
}


# ── Hydration Engine ─────────────────────────────────────────────────


class WhaleHydrator:
    """Core engine that pre-computes whale profile data."""

    def __init__(
        self,
        fmp: FMPClient,
        gemini: GeminiClient,
        force: bool = False,
        dry_run: bool = False,
    ):
        self.fmp = fmp
        self.gemini = gemini
        self.force = force
        self.dry_run = dry_run
        self.sb = get_supabase()
        self.stats = {"processed": 0, "skipped": 0, "errors": 0}
        # Cache profile data across whales to avoid duplicate FMP calls
        self._profile_cache: Dict[str, Dict] = {}

    # ── Main Entry ───────────────────────────────────────────────────

    async def run(self, whale_id: Optional[str] = None):
        """Hydrate all whales or a single one by ID."""
        if whale_id:
            result = (
                self.sb.table("whales").select("*").eq("id", whale_id).execute()
            )
        else:
            result = self.sb.table("whales").select("*").execute()

        whales = result.data or []
        logger.info("Starting hydration for %d whale(s)...", len(whales))

        for whale in whales:
            t0 = time.monotonic()
            try:
                await self._hydrate_one(whale)
                elapsed = time.monotonic() - t0
                logger.info(
                    "  %s — done in %.1fs", whale["name"], elapsed
                )
            except Exception as e:
                logger.error(
                    "  %s — FAILED: %s", whale["name"], e, exc_info=True
                )
                self.stats["errors"] += 1

        logger.info(
            "Hydration complete. processed=%d  skipped=%d  errors=%d",
            self.stats["processed"],
            self.stats["skipped"],
            self.stats["errors"],
        )

    # ── Single Whale Pipeline ────────────────────────────────────────

    async def _hydrate_one(self, whale: Dict[str, Any]):
        whale_id = str(whale["id"])
        name = whale["name"]
        data_source = whale.get("data_source", "manual")

        logger.info("Processing: %s (source=%s)", name, data_source)

        # Step 1: Route to data source
        if data_source == "13f":
            raw = await self._process_13f(whale_id, whale["cik"])
        elif data_source in ("congressional_house", "congressional_senate"):
            chamber = "house" if "house" in data_source else "senate"
            raw = await self._process_congressional(
                whale_id, whale["fmp_name"], chamber
            )
        else:
            logger.info("  Skipping manual whale: %s", name)
            self.stats["skipped"] += 1
            return

        if not raw:
            logger.warning("  No data returned for %s", name)
            self.stats["skipped"] += 1
            return

        holdings = raw["holdings"]
        sectors = raw["sectors"]
        trade_group = raw["trade_group"]
        total_value = raw["total_value"]
        raw_hash = raw["raw_hash"]
        perf_data = raw.get("perf_data", {})
        filing_period = raw["filing_period"]
        filing_date = raw["filing_date"]
        prev_holdings = raw.get("prev_holdings", [])

        # Step 2: Idempotency — skip if hash unchanged
        existing_logo_cache: Dict[str, Any] = {}
        if not self.force:
            existing = (
                self.sb.table("whale_filing_snapshots")
                .select("raw_hash, logo_cache")
                .eq("whale_id", whale_id)
                .eq("filing_period", filing_period)
                .execute()
            )
            if existing.data:
                if existing.data[0].get("raw_hash") == raw_hash:
                    logger.info("  Skipping %s — data unchanged", name)
                    self.stats["skipped"] += 1
                    return
                existing_logo_cache = existing.data[0].get("logo_cache") or {}

        # Step 3: Calculate change_percent
        holdings = self._calculate_change_percent(holdings, prev_holdings)

        # Step 4: Enrich logos (batch)
        holdings, logo_cache = await self._enrich_logos(
            holdings, existing_logo_cache
        )

        # Step 5: Enrich sectors for politicians
        if data_source in ("congressional_house", "congressional_senate"):
            if not sectors:
                sectors = await self._enrich_sectors(holdings)

        # Step 6: AI summaries (parallel)
        behavior, sentiment = await self._generate_ai_summaries(
            name, holdings, trade_group, sectors
        )

        # Step 7: Compute ytd_return
        ytd_return = self._compute_ytd_return(
            whale_id, total_value, perf_data
        )

        # Step 8: Persist
        snapshot = {
            "whale_id": whale_id,
            "filing_period": filing_period,
            "filing_date": filing_date,
            "total_value": total_value,
            "holdings_data": holdings,
            "sector_data": sectors,
            "trade_group": trade_group,
            "behavior_summary": behavior,
            "sentiment_text": sentiment,
            "raw_hash": raw_hash,
            "logo_cache": logo_cache,
        }

        if not self.dry_run:
            await self._persist(whale_id, snapshot, ytd_return)

        self.stats["processed"] += 1

    # ── 13F Processing ───────────────────────────────────────────────

    async def _process_13f(
        self, whale_id: str, cik: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch 13F data: filing dates, current + previous holdings, sectors."""

        # Get filing dates
        async with FMP_SEMAPHORE:
            filing_dates = await self.fmp.get_institutional_filing_dates(cik)
        if not filing_dates:
            logger.warning("  No 13F filing dates for CIK %s", cik)
            return None

        latest = filing_dates[0]
        year = int(latest.get("year") or latest.get("date", "2025")[:4])
        quarter = int(latest.get("quarter", 1))
        period = f"{year}-Q{quarter}"
        filing_date = latest.get("date", f"{year}-{quarter * 3:02d}-30")

        # Find previous quarter
        prev_entry = _find_previous_quarter(filing_dates, year, quarter)

        # Fetch all data concurrently
        async with FMP_SEMAPHORE:
            current_task = self.fmp.get_institutional_holdings(
                cik, year, quarter
            )
        async with FMP_SEMAPHORE:
            if prev_entry:
                prev_task = self.fmp.get_institutional_holdings(
                    cik, int(prev_entry["year"]), int(prev_entry["quarter"])
                )
            else:
                prev_task = _noop_list()

        async with FMP_SEMAPHORE:
            industry_task = self.fmp.get_institutional_industry_breakdown(cik)
        async with FMP_SEMAPHORE:
            perf_task = self.fmp.get_institutional_performance(cik)

        results = await asyncio.gather(
            current_task, prev_task, industry_task, perf_task,
            return_exceptions=True,
        )

        current_raw = results[0] if not isinstance(results[0], BaseException) else []
        prev_raw = results[1] if not isinstance(results[1], BaseException) else []
        industry_data = results[2] if not isinstance(results[2], BaseException) else []
        perf_data = results[3] if not isinstance(results[3], BaseException) else {}

        for idx, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.error("  13F fetch section %d failed: %s", idx, r)

        if not current_raw:
            return None

        # Build holdings
        holdings = self._build_13f_holdings(current_raw)
        prev_holdings = self._build_13f_holdings(prev_raw)
        total_value = sum(h.get("value", 0) for h in holdings)

        # Build sectors from industry breakdown
        sectors = self._build_sectors_from_industry(industry_data)

        # Diff quarters for trade group
        trade_group = self._diff_quarters(
            current_raw, prev_raw, filing_date, total_value
        )

        raw_hash = hashlib.sha256(
            json.dumps(current_raw, sort_keys=True, default=str).encode()
        ).hexdigest()

        return {
            "holdings": holdings,
            "prev_holdings": prev_holdings,
            "sectors": sectors,
            "trade_group": trade_group,
            "total_value": total_value,
            "raw_hash": raw_hash,
            "perf_data": perf_data if isinstance(perf_data, dict) else {},
            "filing_period": period,
            "filing_date": filing_date,
        }

    # ── Congressional Processing ─────────────────────────────────────

    async def _process_congressional(
        self, whale_id: str, fmp_name: str, chamber: str
    ) -> Optional[Dict[str, Any]]:
        """Fetch and aggregate congressional trading data."""
        now = datetime.now()
        period = now.strftime("%Y-%m")

        async with FMP_SEMAPHORE:
            if chamber == "senate":
                raw_trades = await self.fmp.get_senate_trades_by_name(fmp_name)
            else:
                raw_trades = await self.fmp.get_house_trades_by_name(fmp_name)

        if not raw_trades:
            return None

        holdings, trade_group = self._aggregate_congressional(
            raw_trades, now.strftime("%Y-%m-%d")
        )
        total_value = sum(h.get("value", 0) for h in holdings)

        # Load previous snapshot for change_percent
        prev_snap = (
            self.sb.table("whale_filing_snapshots")
            .select("holdings_data")
            .eq("whale_id", whale_id)
            .neq("filing_period", period)
            .order("processed_at", desc=True)
            .limit(1)
            .execute()
        )
        prev_holdings = (
            prev_snap.data[0]["holdings_data"] if prev_snap.data else []
        )

        raw_hash = hashlib.sha256(
            json.dumps(raw_trades[:50], sort_keys=True, default=str).encode()
        ).hexdigest()

        return {
            "holdings": holdings,
            "prev_holdings": prev_holdings,
            "sectors": [],  # Filled later in step 5
            "trade_group": trade_group,
            "total_value": total_value,
            "raw_hash": raw_hash,
            "perf_data": {},
            "filing_period": period,
            "filing_date": now.strftime("%Y-%m-%d"),
        }

    # ── Holdings Builders ────────────────────────────────────────────

    def _build_13f_holdings(
        self, raw_holdings: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Transform raw FMP 13F data into normalized holdings."""
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
                "change_percent": 0.0,
                "value": val,
                "shares": int(
                    float(h.get("sharesNumber") or h.get("shares") or 0)
                ),
            })

        holdings.sort(key=lambda x: x["value"], reverse=True)
        return holdings

    def _aggregate_congressional(
        self, raw_trades: List[Dict], as_of_date: str
    ) -> Tuple[List[Dict], Optional[Dict]]:
        """Aggregate congressional trades into holdings + trade group."""
        trades = []
        holdings_accum: Dict[str, Dict] = {}

        for t in raw_trades:
            symbol = (t.get("symbol") or "").upper().strip()
            if not symbol or symbol in ("--", "N/A"):
                continue

            raw_type = (t.get("type") or "").lower().strip()
            action = CONGRESSIONAL_TYPE_MAP.get(raw_type, "BOUGHT")
            amount_str = t.get("amount") or "$1,001 - $15,000"
            amount = AMOUNT_RANGES.get(amount_str, 8_000)
            tx_date = (
                t.get("transactionDate")
                or t.get("transaction_date")
                or as_of_date
            )
            name = (
                t.get("assetDescription")
                or t.get("asset_description")
                or symbol
            )

            trade_type = "Increased" if action == "BOUGHT" else "Decreased"

            trades.append({
                "ticker": symbol,
                "company_name": name,
                "action": action,
                "trade_type": trade_type,
                "amount": amount,
                "previous_allocation": 0,
                "new_allocation": 0,
                "date": tx_date,
            })

            if symbol not in holdings_accum:
                holdings_accum[symbol] = {
                    "ticker": symbol,
                    "company_name": name,
                    "value": 0,
                    "allocation": 0,
                    "change_percent": 0.0,
                    "logo_url": None,
                }
            if action == "BOUGHT":
                holdings_accum[symbol]["value"] += amount
            else:
                holdings_accum[symbol]["value"] -= amount

        # Positive positions only
        holdings = [h for h in holdings_accum.values() if h["value"] > 0]
        total = sum(h["value"] for h in holdings) or 1
        for h in holdings:
            h["allocation"] = round(h["value"] / total * 100, 2)
        holdings.sort(key=lambda x: x["value"], reverse=True)

        # Trade group from recent 90 days
        cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        recent = [t for t in trades if t.get("date", "") >= cutoff]
        if not recent:
            recent = trades[:20]

        trade_group = None
        if recent:
            total_bought = sum(
                t["amount"] for t in recent if t["action"] == "BOUGHT"
            )
            total_sold = sum(
                t["amount"] for t in recent if t["action"] == "SOLD"
            )
            net_dollar = total_bought - total_sold
            net_action = "BOUGHT" if net_dollar >= 0 else "SOLD"

            buys = [t for t in recent if t["action"] == "BOUGHT"]
            sells = [t for t in recent if t["action"] == "SOLD"]
            summary = _generate_trade_summary(buys, sells, net_action)
            insights = _generate_trade_insights(
                recent, total_bought, total_sold
            )

            trade_group = {
                "date": as_of_date,
                "trade_count": len(recent),
                "net_action": net_action,
                "net_amount": abs(net_dollar),
                "summary": summary,
                "insights": insights,
                "trades": recent[:50],
            }

        return holdings[:30], trade_group

    # ── Quarter Diffing (13F) ────────────────────────────────────────

    def _diff_quarters(
        self,
        current_raw: List[Dict],
        previous_raw: List[Dict],
        filing_date: str,
        total_current_value: float,
    ) -> Optional[Dict[str, Any]]:
        """Diff two 13F snapshots to compute trades."""
        if not current_raw:
            return None

        current_map: Dict[str, Dict] = {}
        for h in current_raw:
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if not sym or sym == "--":
                continue
            current_map[sym] = {
                "symbol": sym,
                "name": h.get("securityName") or h.get("companyName") or sym,
                "value": float(h.get("value") or 0),
                "shares": int(
                    float(h.get("sharesNumber") or h.get("shares") or 0)
                ),
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
                "shares": int(
                    float(h.get("sharesNumber") or h.get("shares") or 0)
                ),
            }
            prev_total += val

        if not current_map:
            return None

        trades = []
        total_bought = 0.0
        total_sold = 0.0

        for ticker in set(current_map) | set(prev_map):
            curr = current_map.get(ticker)
            prev = prev_map.get(ticker)
            curr_val = curr["value"] if curr else 0
            prev_val = prev["value"] if prev else 0
            diff = curr_val - prev_val

            if abs(diff) < 1000:
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
                trade = {
                    "ticker": ticker, "company_name": name,
                    "action": "BOUGHT", "trade_type": "New",
                    "amount": curr_val,
                    "previous_allocation": 0,
                    "new_allocation": round(new_alloc, 2),
                    "date": filing_date,
                }
                total_bought += curr_val
            elif curr is None and prev is not None:
                trade = {
                    "ticker": ticker, "company_name": name,
                    "action": "SOLD", "trade_type": "Closed",
                    "amount": prev_val,
                    "previous_allocation": round(prev_alloc, 2),
                    "new_allocation": 0,
                    "date": filing_date,
                }
                total_sold += prev_val
            elif diff > 0:
                trade = {
                    "ticker": ticker, "company_name": name,
                    "action": "BOUGHT", "trade_type": "Increased",
                    "amount": abs(diff),
                    "previous_allocation": round(prev_alloc, 2),
                    "new_allocation": round(new_alloc, 2),
                    "date": filing_date,
                }
                total_bought += abs(diff)
            else:
                trade = {
                    "ticker": ticker, "company_name": name,
                    "action": "SOLD", "trade_type": "Decreased",
                    "amount": abs(diff),
                    "previous_allocation": round(prev_alloc, 2),
                    "new_allocation": round(new_alloc, 2),
                    "date": filing_date,
                }
                total_sold += abs(diff)

            trades.append(trade)

        if not trades:
            return None

        trades.sort(key=lambda t: t["amount"], reverse=True)
        net_dollar = total_bought - total_sold
        net_action = "BOUGHT" if net_dollar >= 0 else "SOLD"

        buys = [t for t in trades if t["action"] == "BOUGHT"]
        sells = [t for t in trades if t["action"] == "SOLD"]
        new_count = sum(1 for t in trades if t["trade_type"] == "New")
        closed_count = sum(1 for t in trades if t["trade_type"] == "Closed")

        summary = _generate_trade_summary(buys, sells, net_action)
        insights = _generate_trade_insights(trades, total_bought, total_sold)

        if new_count:
            top_new = [t["ticker"] for t in trades if t["trade_type"] == "New"][:3]
            insights.append(f"New positions: {', '.join(top_new)}")
        if closed_count:
            top_closed = [t["ticker"] for t in trades if t["trade_type"] == "Closed"][:3]
            insights.append(f"Exited: {', '.join(top_closed)}")

        return {
            "date": filing_date,
            "trade_count": len(trades),
            "net_action": net_action,
            "net_amount": abs(net_dollar),
            "summary": summary,
            "insights": insights[:4],
            "trades": trades[:50],
        }

    # ── Sector Builders ──────────────────────────────────────────────

    def _build_sectors_from_industry(
        self, industry_data: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Build sectors from FMP institutional industry breakdown."""
        if not industry_data:
            return []

        sectors = []
        for item in industry_data:
            name = item.get("industry") or item.get("sector") or "Other"
            weight = float(
                item.get("weight") or item.get("weightPercentage") or 0
            )
            if weight > 0:
                sectors.append({
                    "name": name,
                    "allocation": round(weight, 1),
                    "color_hex": SECTOR_COLORS.get(name, DEFAULT_SECTOR_COLOR),
                })
        sectors.sort(key=lambda x: x["allocation"], reverse=True)
        return sectors[:8]

    # ── Enrichment: change_percent ───────────────────────────────────

    def _calculate_change_percent(
        self, current: List[Dict], previous: List[Dict]
    ) -> List[Dict]:
        """change_percent = current_allocation - previous_allocation."""
        prev_map: Dict[str, float] = {}
        for h in previous:
            ticker = (h.get("ticker") or h.get("symbol") or "").upper()
            if ticker:
                prev_map[ticker] = float(h.get("allocation", 0))

        for h in current:
            prev_alloc = prev_map.get(h["ticker"].upper(), 0.0)
            h["change_percent"] = round(
                float(h.get("allocation", 0)) - prev_alloc, 2
            )
        return current

    # ── Enrichment: Logos ────────────────────────────────────────────

    async def _enrich_logos(
        self, holdings: List[Dict], existing_cache: Dict
    ) -> Tuple[List[Dict], Dict]:
        """Batch-fetch company logos via FMP profiles."""
        logo_cache = dict(existing_cache)

        tickers_to_fetch = [
            h["ticker"] for h in holdings if h["ticker"] not in logo_cache
        ]

        for i in range(0, len(tickers_to_fetch), FMP_BATCH_SIZE):
            batch = tickers_to_fetch[i: i + FMP_BATCH_SIZE]
            try:
                async with FMP_SEMAPHORE:
                    profiles = await self.fmp.get_company_profiles_batch(batch)

                for p in profiles:
                    sym = (p.get("symbol") or "").upper()
                    image = p.get("image") or p.get("logo") or None
                    sector = p.get("sector") or None
                    if sym:
                        logo_cache[sym] = image
                        # Also cache profile data for sector enrichment
                        self._profile_cache[sym] = p
            except Exception as e:
                logger.warning("  Logo batch fetch failed: %s", e)

        # Apply logos
        for h in holdings:
            h["logo_url"] = logo_cache.get(h["ticker"])

        return holdings, logo_cache

    # ── Enrichment: Sectors for Politicians ──────────────────────────

    async def _enrich_sectors(
        self, holdings: List[Dict]
    ) -> List[Dict[str, Any]]:
        """Look up sectors from company profiles for congressional holdings."""
        sector_accum: Dict[str, float] = {}

        # Fetch profiles for tickers not yet cached
        uncached = [
            h["ticker"]
            for h in holdings
            if h["ticker"] and h["ticker"] not in self._profile_cache
        ]

        for i in range(0, len(uncached), FMP_BATCH_SIZE):
            batch = uncached[i: i + FMP_BATCH_SIZE]
            try:
                async with FMP_SEMAPHORE:
                    profiles = await self.fmp.get_company_profiles_batch(batch)
                for p in profiles:
                    sym = (p.get("symbol") or "").upper()
                    if sym:
                        self._profile_cache[sym] = p
            except Exception as e:
                logger.warning("  Sector profile batch failed: %s", e)

        # Aggregate by sector
        for h in holdings:
            profile = self._profile_cache.get(h["ticker"], {})
            sector = profile.get("sector") or "Other"
            sector_accum[sector] = (
                sector_accum.get(sector, 0) + h.get("allocation", 0)
            )

        total = sum(sector_accum.values()) or 1
        sectors = []
        for name, weight in sorted(
            sector_accum.items(), key=lambda x: -x[1]
        ):
            sectors.append({
                "name": name,
                "allocation": round(weight / total * 100, 1),
                "color_hex": SECTOR_COLORS.get(name, DEFAULT_SECTOR_COLOR),
            })
        return sectors[:8]

    # ── AI Summaries ─────────────────────────────────────────────────

    async def _generate_ai_summaries(
        self,
        whale_name: str,
        holdings: List[Dict],
        trade_group: Optional[Dict],
        sectors: List[Dict],
    ) -> Tuple[Dict, str]:
        """Generate behavior + sentiment via Gemini in parallel."""
        behavior_task = self._ai_behavior(
            whale_name, holdings, trade_group, sectors
        )
        sentiment_task = self._ai_sentiment(
            whale_name, holdings, trade_group, sectors
        )

        results = await asyncio.gather(
            behavior_task, sentiment_task, return_exceptions=True
        )

        behavior = results[0]
        sentiment = results[1]

        if isinstance(behavior, BaseException):
            logger.warning(
                "  Gemini behavior failed for %s, using fallback: %s",
                whale_name, behavior,
            )
            behavior = _fallback_behavior(trade_group, sectors)

        if isinstance(sentiment, BaseException):
            logger.warning(
                "  Gemini sentiment failed for %s, using fallback: %s",
                whale_name, sentiment,
            )
            sentiment = _fallback_sentiment(holdings, trade_group, sectors)

        return behavior, sentiment

    async def _ai_behavior(
        self,
        whale_name: str,
        holdings: List[Dict],
        trade_group: Optional[Dict],
        sectors: List[Dict],
    ) -> Dict[str, str]:
        """Gemini JSON generation for behavior summary."""
        top_holdings_str = ", ".join(
            f"{h['ticker']} ({h.get('allocation', 0):.1f}%)"
            for h in holdings[:10]
        )
        top_sectors_str = ", ".join(
            f"{s['name']} ({s.get('allocation', 0):.1f}%)"
            for s in sectors[:5]
        )

        trade_info = "No recent trades recorded."
        if trade_group:
            buys = sum(
                1 for t in trade_group.get("trades", [])
                if t["action"] == "BOUGHT"
            )
            sells = sum(
                1 for t in trade_group.get("trades", [])
                if t["action"] == "SOLD"
            )
            trade_info = (
                f"{trade_group['trade_count']} trades: "
                f"{buys} buys, {sells} sells. "
                f"Net: {trade_group['net_action']} "
                f"${trade_group.get('net_amount', 0):,.0f}"
            )

        prompt = (
            f"Analyze the recent investment behavior of {whale_name}.\n\n"
            f"Top Holdings: {top_holdings_str}\n"
            f"Sector Exposure: {top_sectors_str}\n"
            f"Recent Trading: {trade_info}\n\n"
            "Return a JSON object with exactly these 4 string fields:\n"
            '- "action": Present-tense verb phrase (e.g. "Accumulating", '
            '"Reducing", "Rebalancing")\n'
            '- "primaryFocus": What they focus on, 2-5 words lowercase '
            '(e.g. "technology stocks")\n'
            '- "secondaryAction": Secondary verb phrase '
            '(e.g. "Holding", "Trimming")\n'
            '- "secondaryFocus": Secondary area, 2-5 words lowercase '
            '(e.g. "core financial positions")\n\n'
            f"Be specific to {whale_name}'s actual data."
        )

        system = (
            "You are a financial analyst summarizing institutional investor "
            "behavior. Return only valid JSON matching the schema. Be concise."
        )

        async with GEMINI_SEMAPHORE:
            result = await self.gemini.generate_json(
                prompt=prompt, system_instruction=system
            )

        parsed = json.loads(result["text"])

        # Validate required keys
        required = {"action", "primaryFocus", "secondaryAction", "secondaryFocus"}
        if not required.issubset(parsed.keys()):
            raise ValueError(f"Missing keys in behavior JSON: {parsed.keys()}")

        return parsed

    async def _ai_sentiment(
        self,
        whale_name: str,
        holdings: List[Dict],
        trade_group: Optional[Dict],
        sectors: List[Dict],
    ) -> str:
        """Gemini text generation for sentiment paragraph."""
        top_holdings_str = ", ".join(
            f"{h['ticker']} ({h.get('allocation', 0):.1f}%)"
            for h in holdings[:10]
        )
        top_sectors_str = ", ".join(
            f"{s['name']} ({s.get('allocation', 0):.1f}%)"
            for s in sectors[:5]
        )

        trade_info = "No significant recent trading activity."
        if trade_group:
            trades = trade_group.get("trades", [])
            top_buys = [
                t["ticker"] for t in trades if t["action"] == "BOUGHT"
            ][:3]
            top_sells = [
                t["ticker"] for t in trades if t["action"] == "SOLD"
            ][:3]
            trade_info = (
                f"Recent filing: {trade_group['trade_count']} trades, "
                f"net {trade_group['net_action'].lower()} "
                f"${trade_group.get('net_amount', 0):,.0f}. "
            )
            if top_buys:
                trade_info += f"Top buys: {', '.join(top_buys)}. "
            if top_sells:
                trade_info += f"Top sells: {', '.join(top_sells)}. "

        prompt = (
            f"Write a 2-3 sentence investment sentiment summary for "
            f"{whale_name}.\n\n"
            f"Portfolio Data:\n"
            f"- Top Holdings: {top_holdings_str}\n"
            f"- Sector Exposure: {top_sectors_str}\n"
            f"- {trade_info}\n\n"
            "Requirements:\n"
            "- Describe overall investment posture\n"
            "- Mention specific sectors or holdings being emphasized\n"
            "- Note any notable strategy shifts\n"
            "- Third person, present tense, under 60 words\n"
            "- Be specific to the actual data"
        )

        system = (
            "You are a financial analyst writing concise investor sentiment "
            "summaries. Write naturally without markdown. Return only the "
            "summary paragraph."
        )

        async with GEMINI_SEMAPHORE:
            result = await self.gemini.generate_text(
                prompt=prompt, system_instruction=system
            )

        return result["text"].strip()

    # ── ytd_return ───────────────────────────────────────────────────

    def _compute_ytd_return(
        self,
        whale_id: str,
        total_value: float,
        perf_data: Dict,
    ) -> Optional[float]:
        """Compute ytd_return with multiple fallback strategies."""
        if perf_data.get("ytdReturn"):
            return float(perf_data["ytdReturn"])
        if perf_data.get("oneYearReturn"):
            return float(perf_data["oneYearReturn"])

        # Fallback: compare against previous year Q4 snapshot
        try:
            prev_year = datetime.now().year - 1
            prev_snap = (
                self.sb.table("whale_filing_snapshots")
                .select("total_value")
                .eq("whale_id", whale_id)
                .like("filing_period", f"{prev_year}-Q4")
                .limit(1)
                .execute()
            )
            if prev_snap.data and prev_snap.data[0].get("total_value"):
                prev_val = float(prev_snap.data[0]["total_value"])
                if prev_val > 0 and total_value > 0:
                    return round(
                        (total_value - prev_val) / prev_val * 100, 1
                    )
        except Exception as e:
            logger.warning("  ytd_return fallback failed: %s", e)

        return None

    # ── Persistence ──────────────────────────────────────────────────

    async def _persist(
        self,
        whale_id: str,
        snapshot: Dict[str, Any],
        ytd_return: Optional[float],
    ):
        """Write to snapshot cache + denormalized tables."""
        sb = self.sb

        # 1. Upsert snapshot
        try:
            sb.table("whale_filing_snapshots").upsert(
                snapshot, on_conflict="whale_id,filing_period"
            ).execute()
        except Exception as e:
            logger.error("  Failed to upsert snapshot: %s", e)

        # 2. Update whales record
        try:
            whale_update: Dict[str, Any] = {
                "portfolio_value": snapshot["total_value"],
                "behavior_summary": snapshot["behavior_summary"],
                "sentiment_summary": snapshot["sentiment_text"],
                "last_hydrated_at": datetime.now().isoformat(),
            }
            if ytd_return is not None:
                whale_update["ytd_return"] = ytd_return
            sb.table("whales").update(whale_update).eq(
                "id", whale_id
            ).execute()
        except Exception as e:
            logger.error("  Failed to update whale record: %s", e)

        # 3. Replace holdings
        try:
            sb.table("whale_holdings").delete().eq(
                "whale_id", whale_id
            ).execute()
            for h in snapshot["holdings_data"][:30]:
                sb.table("whale_holdings").insert({
                    "whale_id": whale_id,
                    "ticker": h["ticker"],
                    "company_name": h.get("company_name", h["ticker"]),
                    "logo_url": h.get("logo_url"),
                    "allocation": h.get("allocation", 0),
                    "change_percent": h.get("change_percent", 0),
                }).execute()
        except Exception as e:
            logger.error("  Failed to sync holdings: %s", e)

        # 4. Replace sector allocations
        try:
            sb.table("whale_sector_allocations").delete().eq(
                "whale_id", whale_id
            ).execute()
            for s in snapshot["sector_data"]:
                sb.table("whale_sector_allocations").insert({
                    "whale_id": whale_id,
                    "sector": s["name"],
                    "allocation": s["allocation"],
                }).execute()
        except Exception as e:
            logger.error("  Failed to sync sectors: %s", e)

        # 5. Insert trade group + trades (skip if date already exists)
        tg = snapshot.get("trade_group")
        if tg:
            try:
                existing = (
                    sb.table("whale_trade_groups")
                    .select("id")
                    .eq("whale_id", whale_id)
                    .eq("date", tg["date"])
                    .execute()
                )
                if not existing.data:
                    tg_result = sb.table("whale_trade_groups").insert({
                        "whale_id": whale_id,
                        "date": tg["date"],
                        "trade_count": tg["trade_count"],
                        "net_action": tg["net_action"],
                        "net_amount": tg["net_amount"],
                        "summary": tg.get("summary"),
                        "insights": tg.get("insights", []),
                    }).execute()

                    if tg_result.data:
                        tg_id = tg_result.data[0]["id"]
                        for trade in tg.get("trades", [])[:50]:
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
                                "new_allocation": trade.get(
                                    "new_allocation"
                                ),
                                "date": trade.get("date", ""),
                            }).execute()
            except Exception as e:
                logger.error("  Failed to sync trade group: %s", e)


# ── Module-Level Helpers ─────────────────────────────────────────────


def _find_previous_quarter(
    filing_dates: List[Dict], year: int, quarter: int
) -> Optional[Dict]:
    """Find the entry for the quarter before (year, quarter)."""
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
    """Format dollar amount: +$4.34B, -$2.1M, etc."""
    prefix = "+" if action == "BOUGHT" else "-"
    abs_val = abs(value)
    if abs_val >= 1_000_000_000:
        return f"{prefix}${abs_val / 1_000_000_000:.2f}B"
    elif abs_val >= 1_000_000:
        return f"{prefix}${abs_val / 1_000_000:.1f}M"
    elif abs_val >= 1_000:
        return f"{prefix}${abs_val / 1_000:.0f}K"
    return f"{prefix}${abs_val:,.0f}"


def _generate_trade_summary(
    buys: List[Dict], sells: List[Dict], net_action: str
) -> str:
    """One-line trade group summary."""
    if len(buys) > len(sells) * 2:
        return f"Heavy accumulation with {len(buys)} buys"
    elif len(sells) > len(buys) * 2:
        return f"Significant reduction with {len(sells)} sells"
    elif not sells and buys:
        return f"Pure buying activity with {len(buys)} positions"
    elif not buys and sells:
        return f"Pure selling activity with {len(sells)} positions"
    return "Portfolio rebalancing"


def _generate_trade_insights(
    trades: List[Dict], total_bought: float, total_sold: float
) -> List[str]:
    """Generate insight strings for a trade group."""
    insights = []
    if total_bought > 0:
        insights.append(
            f"Net accumulating with {_format_amount(total_bought, 'BOUGHT')} in buying"
        )
    if total_sold > 0:
        insights.append(
            f"Trimmed {_format_amount(total_sold, 'SOLD')} in positions"
        )
    return insights[:4]


def _fallback_behavior(
    trade_group: Optional[Dict], sectors: List[Dict]
) -> Dict[str, str]:
    """Rule-based fallback when Gemini fails."""
    top_sector = sectors[0]["name"] if sectors else "various sectors"
    second = sectors[1]["name"] if len(sectors) > 1 else "core positions"

    if not trade_group:
        return {
            "action": "Holding",
            "primaryFocus": "existing positions",
            "secondaryAction": "Maintaining",
            "secondaryFocus": "portfolio allocation",
        }

    buys = [t for t in trade_group.get("trades", []) if t["action"] == "BOUGHT"]
    sells = [t for t in trade_group.get("trades", []) if t["action"] == "SOLD"]

    if len(buys) > len(sells):
        return {
            "action": "Accumulating",
            "primaryFocus": f"{top_sector.lower()} stocks",
            "secondaryAction": "Holding",
            "secondaryFocus": f"core {second.lower()} positions",
        }
    elif len(sells) > len(buys):
        return {
            "action": "Reducing",
            "primaryFocus": f"exposure to {top_sector.lower()}",
            "secondaryAction": "Maintaining",
            "secondaryFocus": f"{second.lower()} allocations",
        }
    return {
        "action": "Rebalancing",
        "primaryFocus": "across sectors",
        "secondaryAction": "Adjusting",
        "secondaryFocus": "position sizes",
    }


def _fallback_sentiment(
    holdings: List[Dict],
    trade_group: Optional[Dict],
    sectors: List[Dict],
) -> str:
    """Rule-based fallback when Gemini fails."""
    top_tickers = ", ".join(h["ticker"] for h in holdings[:5])
    top_sector = sectors[0]["name"] if sectors else "various sectors"

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


# ── CLI Entry Point ──────────────────────────────────────────────────


async def main():
    parser = argparse.ArgumentParser(
        description="Whale Data Hydration Engine — "
        "Pre-compute whale profiles for instant API serving."
    )
    parser.add_argument(
        "--whale-id",
        type=str,
        default=None,
        help="Hydrate a single whale by UUID",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Skip raw_hash deduplication check",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and compute only — no database writes",
    )
    args = parser.parse_args()

    fmp = FMPClient()
    gemini = GeminiClient()
    hydrator = WhaleHydrator(
        fmp=fmp,
        gemini=gemini,
        force=args.force,
        dry_run=args.dry_run,
    )

    try:
        await hydrator.run(whale_id=args.whale_id)
    finally:
        await fmp.close()


if __name__ == "__main__":
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s  %(name)s  %(levelname)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    asyncio.run(main())
