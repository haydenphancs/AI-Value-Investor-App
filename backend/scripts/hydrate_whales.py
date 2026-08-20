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
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

# Ensure backend app package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402
from app.database import get_supabase  # noqa: E402
from app.utils.supabase_errors import (  # noqa: E402
    is_transient_supabase_error,
    retry_idempotent_sync,
)
from app.integrations.fmp import FMPClient  # noqa: E402
from app.integrations.gemini import GeminiClient  # noqa: E402
from app.services.whale_service import (  # noqa: E402
    SIC_TO_SECTOR, SECTOR_COLORS, DEFAULT_SECTOR_COLOR, _map_sic_to_sector,
    # Split detection is IMPORTED, never re-implemented. A second copy of this logic
    # is what let the two 13F diff paths drift apart in the first place.
    _quarter_end_date, _split_ratio_in_window,
)
from app.services.whale_service import WhaleService as _WhaleService  # noqa: E402

# `_suspicious_split_tickers` is a @staticmethod on WhaleService; bind it to a plain
# name so the call sites below read the same as the service's.
_suspicious_split_tickers = _WhaleService._suspicious_split_tickers
from app.services._whale_common import (  # noqa: E402
    parse_congress_amount_dollars,
    parse_congress_amount_bounds,
    sum_amount_bounds,
    format_amount_range,
    snapshot_db_row,
    calc_13f_trade_dollars,
    resolve_congress_action,
    AnnualReturn,
    compute_13f_cagr,
    compute_ticker_cagr,
    return_label_for,
    unavailable_return_label,
    RETURN_UNAVAILABLE,
    RETURN_INSUFFICIENT,
)
from app.services.agents.persona_config import _IDENTITY_RULE  # noqa: E402

logger = logging.getLogger("hydrate_whales")

# ── Rate-Limit Controls ──────────────────────────────────────────────

FMP_SEMAPHORE = asyncio.Semaphore(5)

# `YYYY-MM-DD` and `YYYY-Qn`. The second is deliberately strict so a congressional
# snapshot's `YYYY-MM` period can never be mistaken for a 13F quarter.
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_QUARTER_PERIOD_RE = re.compile(r"^\d{4}-Q[1-4]$")

# Cap on per-whale /splits lookups. A filer whose entire book was restated would
# otherwise fan out one unthrottled call per suspect ticker.
_MAX_SPLIT_LOOKUPS = 25
GEMINI_SEMAPHORE = asyncio.Semaphore(3)
FMP_BATCH_SIZE = 30

# SECTOR_COLORS, DEFAULT_SECTOR_COLOR, SIC_TO_SECTOR, _map_sic_to_sector
# are imported from app.services.whale_service (single source of truth).


# Congressional amount parsing (midpoint for internal math, bounds for the
# honest display range) lives in app.services._whale_common — single source of
# truth shared with whale_service, so both agree on the same numbers.

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
        # `no_data` is its own bucket. It used to fall into `skipped` alongside
        # "unchanged since last run" and "manual whale", so the one counter that could
        # reveal a filer going dormant was averaged into two benign ones.
        self.stats = {"processed": 0, "skipped": 0, "errors": 0, "no_data": 0}
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
            "Hydration complete. processed=%d  skipped=%d  no_data=%d  errors=%d",
            self.stats["processed"],
            self.stats["skipped"],
            self.stats["no_data"],
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
            # Structured, and counted. This is the only LIVE hint that a filer may have
            # gone dormant, and it used to be an unstructured WARNING immediately
            # followed by run()'s "done in %.1fs" success line.
            #
            # ⚠️ It is NOT proof of dormancy: every FMP method swallows its exception and
            # returns [], so this fires identically for a 429, a plan downgrade and a
            # timeout. Dormancy is classified from PERSISTED data in
            # `_whale_common.compute_activity`; this counter is an operational hint only.
            self.stats["no_data"] += 1
            logger.warning(
                "  No data returned for %s (whale_id=%s data_source=%s) — upstream empty "
                "OR unavailable; NOT proof of dormancy",
                name, whale_id, data_source,
            )
            # Ticker-only fallback: if whale has an associated_ticker
            # (e.g. PSH.L, ARKK), we can still compute its Tier-1 CAGR
            # even without 13F holdings.
            if whale.get("associated_ticker"):
                result = await self._compute_ytd_return(
                    whale_id, 0.0, [], whale=whale
                )
                if result.is_ok:
                    # Computed ONCE and reused for both the write and the log, so the
                    # two can never disagree.
                    label = return_label_for(
                        result.source, whale.get("associated_ticker")
                    )
                    try:
                        # RETRY-SAFE: PK-keyed UPDATE, fixed payload.
                        retry_idempotent_sync(
                            lambda: self.sb.table("whales").update({
                                "ytd_return": result.value,
                                "return_source": result.source,
                                "return_window_years": result.window_years,
                                "return_status": result.status,
                                "return_label": label,
                            }).eq("id", whale_id).execute(),
                            what=f"whales ticker-only return whale_id={whale_id}",
                            logger=logger,
                        )
                    except Exception as e:
                        _transient = is_transient_supabase_error(e)
                        (logger.warning if _transient else logger.error)(
                            "  Ticker-only update failed for %s: %s: %s",
                            name, type(e).__name__, e, exc_info=not _transient,
                        )
                    else:
                        # try/except/ELSE on purpose. These two lines used to live
                        # INSIDE the try, right after a log line that referenced two
                        # names bound NOWHERE in this scope (`ytd_return`,
                        # `return_label` — the code around them was refactored to the
                        # `result` AnnualReturn and this call site was missed). The
                        # NameError fired AFTER the UPDATE had already committed, was
                        # swallowed by the handler above and reported as "Ticker-only
                        # update failed" — a lie — so `processed` never incremented
                        # and EVERY ticker-only whale was miscounted as skipped.
                        # Keeping the counter in `else` means it can never again be
                        # gated on a log line succeeding.
                        logger.info(
                            "  %s — ticker-only return updated: %s%% (%s)",
                            name, result.value, label,
                        )
                        self.stats["processed"] += 1
                        return
            self.stats["skipped"] += 1
            return

        holdings = raw["holdings"]
        sectors = raw["sectors"]
        # 13F returns a single "trade_group"; congress returns "trade_groups".
        single_group = raw.get("trade_group")
        trade_groups = raw.get("trade_groups") or (
            [single_group] if single_group else []
        )
        primary_group = trade_groups[0] if trade_groups else None
        is_congress = raw.get("is_congress", False)
        total_value = raw["total_value"]
        raw_hash = raw["raw_hash"]
        perf_data = raw.get("perf_data", {})
        perf_data_list = raw.get("perf_data_list", [])
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
            name, holdings, primary_group, sectors, is_congress=is_congress
        )

        # Step 7: Compute ytd_return + risk profile
        annual_return = await self._compute_ytd_return(
            whale_id, total_value, perf_data_list, whale=whale
        )
        risk_profile = self._compute_risk_profile(
            holdings, sectors, primary_group, perf_data
        )

        # Step 8: Persist
        snapshot = {
            "whale_id": whale_id,
            "filing_period": filing_period,
            "filing_date": filing_date,
            "total_value": total_value,
            "holdings_data": holdings,
            "sector_data": sectors,
            "trade_group": primary_group,   # backward-compat (most recent)
            "trade_groups": trade_groups,   # full per-filing timeline
            "behavior_summary": behavior,
            "sentiment_text": sentiment,
            "raw_hash": raw_hash,
            "logo_cache": logo_cache,
        }

        if not self.dry_run:
            await self._persist(
                whale_id, snapshot, annual_return, risk_profile,
                associated_ticker=whale.get("associated_ticker"),
            )

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

        # Fetch all data concurrently, THROTTLED.
        #
        # ⚠️ `async with FMP_SEMAPHORE:` around `self.fmp.get_...(...)` was a NO-OP: that
        # expression only CONSTRUCTS a coroutine, it does not await it. The semaphore was
        # acquired and released before any HTTP request existed, and all four calls then
        # ran unthrottled inside the gather below. `_throttled` puts the acquire inside
        # the awaited coroutine, which is where it has to be.
        async def _throttled(coro):
            async with FMP_SEMAPHORE:
                return await coro

        prev_coro = (
            self.fmp.get_institutional_holdings(
                cik, int(prev_entry["year"]), int(prev_entry["quarter"])
            )
            if prev_entry
            else _noop_list()
        )

        results = await asyncio.gather(
            _throttled(self.fmp.get_institutional_holdings(cik, year, quarter)),
            _throttled(prev_coro),
            _throttled(
                self.fmp.get_institutional_industry_breakdown(
                    cik, year=year, quarter=quarter
                )
            ),
            _throttled(self.fmp.get_institutional_performance(cik)),
            return_exceptions=True,
        )

        current_raw = results[0] if not isinstance(results[0], BaseException) else []
        prev_raw = results[1] if not isinstance(results[1], BaseException) else []
        industry_data = results[2] if not isinstance(results[2], BaseException) else []
        perf_data_list = results[3] if not isinstance(results[3], BaseException) else []
        perf_data = perf_data_list[0] if perf_data_list else {}

        for idx, r in enumerate(results):
            if isinstance(r, BaseException):
                logger.error("  13F fetch section %d failed: %s", idx, r)

        if not current_raw:
            return None

        # ⚠️ Distinguish "there IS no prior quarter" from "the prior-quarter fetch
        # FAILED". Both leave `prev_raw == []`, and an empty prior book makes
        # `_diff_quarters` book EVERY position as New/BOUGHT — so a single 429 or 5xx on
        # this one call rewrote a whale's entire history as "bought its whole book this
        # quarter", with a net amount equal to the full AUM. That trivially clears the
        # $500M alert threshold, so the transient error also pushed a fabricated alert.
        #
        # Abort the whale instead. The previous snapshot stays untouched and the next
        # scheduled run retries; the `raw_hash` is left alone so nothing is skipped.
        if prev_entry and not prev_raw:
            logger.error(
                "  13F previous-quarter fetch returned no rows for CIK %s "
                "(%s-Q%s) although a prior filing EXISTS — aborting this whale rather "
                "than booking its entire book as new positions",
                cik, prev_entry.get("year"), prev_entry.get("quarter"),
            )
            return None

        # Build holdings
        holdings = self._build_13f_holdings(current_raw)
        prev_holdings = self._build_13f_holdings(prev_raw)
        total_value = sum(h.get("value", 0) for h in holdings)

        # Build sectors from industry breakdown
        sectors = self._build_sectors_from_industry(industry_data)

        # Detect splits BEFORE diffing. A 10:1 split leaves the position value roughly
        # unchanged while the share count jumps 10x; without restatement that reads as a
        # ~9x share purchase. Best-effort — a failure here just leaves `split_ratios`
        # empty, i.e. exactly the previous behaviour.
        split_ratios: Dict[str, float] = {}
        try:
            suspects = _suspicious_split_tickers(current_raw, prev_raw)
            if suspects:
                prev_end = (
                    _quarter_end_date(
                        int(prev_entry["year"]), int(prev_entry["quarter"])
                    )
                    if prev_entry
                    else None
                )
                curr_end = _quarter_end_date(year, quarter)
                # Capped: an entire restated book would otherwise fan out one /splits
                # call per suspect ticker with no bound at all.
                suspects = suspects[:_MAX_SPLIT_LOOKUPS]
                split_lists = await asyncio.gather(
                    *[_throttled(self.fmp.get_stock_splits(t)) for t in suspects],
                    return_exceptions=True,
                )
                for t, sl in zip(suspects, split_lists):
                    if isinstance(sl, BaseException):
                        logger.warning("  Split lookup failed for %s: %s", t, sl)
                        continue
                    r = _split_ratio_in_window(sl, prev_end, curr_end)
                    if r and r != 1.0:
                        split_ratios[t] = r
        except Exception as e:
            logger.warning(
                "  Split detection failed for CIK %s (%s: %s) — using the raw diff",
                cik, type(e).__name__, e,
            )
            split_ratios = {}

        # Diff quarters for trade group
        trade_group = self._diff_quarters(
            current_raw, prev_raw, filing_date, total_value, split_ratios
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
            "perf_data_list": perf_data_list if isinstance(perf_data_list, list) else [],
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

        holdings, trade_groups = self._aggregate_congressional(
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
            "trade_groups": trade_groups,
            "is_congress": True,
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
        """Transform raw FMP 13F data into normalized holdings.

        Aggregates by ticker so multiple CUSIPs for the same symbol
        (share classes, call/put option tranches) collapse into one
        position. Without this, DB INSERT hits the UNIQUE(whale_id,
        ticker) constraint and allocations double-count.
        """
        by_ticker: Dict[str, Dict[str, Any]] = {}
        for h in raw_holdings:
            val = float(h.get("value") or 0)
            if val <= 0:
                continue
            sym = (h.get("symbol") or h.get("tickercusip") or "").upper()
            if not sym or sym == "--":
                continue
            shares = int(float(h.get("sharesNumber") or h.get("shares") or 0))
            name = h.get("securityName") or h.get("companyName") or sym

            existing = by_ticker.get(sym)
            if existing:
                existing["value"] += val
                existing["shares"] += shares
            else:
                by_ticker[sym] = {
                    "ticker": sym,
                    "company_name": name,
                    "logo_url": None,
                    "value": val,
                    "shares": shares,
                }

        total = sum(h["value"] for h in by_ticker.values())
        if total <= 0:
            return []

        holdings = list(by_ticker.values())
        for h in holdings:
            h["allocation"] = round(h["value"] / total * 100, 2)
            h["change_percent"] = 0.0

        holdings.sort(key=lambda x: x["value"], reverse=True)
        return holdings

    def _aggregate_congressional(
        self, raw_trades: List[Dict], as_of_date: str
    ) -> Tuple[List[Dict], List[Dict]]:
        """Aggregate congressional trades into holdings + per-filing groups.

        Groups by ``disclosureDate`` (STOCK Act filing), NOT by ``now`` — so
        each group is dated by when it became public and is stable/de-dupable
        across hydration runs (the old ``date=as_of_date`` stamp created a new
        "today" group on every daily/6-hourly run → the fake Today/Yesterday
        timeline). Each trade preserves its STOCK Act range string + parsed
        bounds so the UI shows honest ranges instead of midpoints.
        """
        trades = []
        holdings_accum: Dict[str, Dict] = {}

        for t in raw_trades:
            symbol = (t.get("symbol") or "").upper().strip()
            if not symbol or symbol in ("--", "N/A"):
                continue

            # Unknown type → SKIP, never a defaulted "BOUGHT". See
            # `_whale_common.resolve_congress_action`: the badge and the net sum are
            # both hard directional claims, so guessing states something false rather
            # than degrading.
            action = resolve_congress_action(t.get("type"))
            if action is None:
                logger.warning(
                    "  Skipping congressional trade with unrecognised type=%r "
                    "(symbol=%s) — refusing to guess a direction",
                    t.get("type"), symbol,
                )
                continue
            amount_str = t.get("amount") or "$1,001 - $15,000"
            # NO `or 8_000`. An unparseable bucket parses to 0.0, and `0.0 or 8_000`
            # replaced a real "we don't know" with a fabricated $8,000 that was then
            # persisted to `whale_trades.amount` and summed into the group net —
            # understating a large disclosure by orders of magnitude and making
            # `net_amount` contradict `net_amount_range`. The raw bucket string is kept
            # on the trade, so the UI still shows the honest range.
            # `whale_service._aggregate_congressional_trades` already does this; the two
            # paths disagreeing is what made the same filing render two different totals.
            amount = parse_congress_amount_dollars(amount_str)
            amount_low, amount_high = parse_congress_amount_bounds(amount_str)
            tx_date = (
                t.get("transactionDate")
                or t.get("transaction_date")
                or as_of_date
            )
            disclosure_date = (
                t.get("disclosureDate")
                or t.get("disclosure_date")
                or t.get("dateRecieved")
                or t.get("dateReceived")
                or tx_date
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
                "amount_range": amount_str,
                "amount_low": amount_low,
                "amount_high": amount_high,
                "previous_allocation": 0,
                "new_allocation": 0,
                "date": tx_date,
                "disclosure_date": disclosure_date,
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

        # Group trades by disclosure filing → one group per disclosure date
        by_disclosure: Dict[str, List[Dict]] = {}
        for t in trades:
            key = t.get("disclosure_date") or t.get("date") or as_of_date
            by_disclosure.setdefault(key, []).append(t)

        trade_groups: List[Dict] = []
        for disclosure_date, filing_trades in by_disclosure.items():
            total_bought = sum(
                t["amount"] for t in filing_trades if t["action"] == "BOUGHT"
            )
            total_sold = sum(
                t["amount"] for t in filing_trades if t["action"] == "SOLD"
            )
            net_dollar = total_bought - total_sold
            net_action = "BOUGHT" if net_dollar >= 0 else "SOLD"

            buys = [t for t in filing_trades if t["action"] == "BOUGHT"]
            sells = [t for t in filing_trades if t["action"] == "SOLD"]
            summary = _generate_trade_summary(buys, sells, net_action)

            direction_bounds = [
                (t.get("amount_low", 0.0), t.get("amount_high", 0.0))
                for t in filing_trades
                if t["action"] == net_action
            ]
            net_amount_range = (
                format_amount_range(*sum_amount_bounds(direction_bounds))
                if direction_bounds
                else None
            )

            # Congress insights use the disclosed RANGE — never a precise dollar.
            insights = []
            if net_amount_range:
                verb = "buying" if net_action == "BOUGHT" else "selling"
                insights.append(
                    f"Net {verb} of {net_amount_range} (disclosed range)"
                )
            new_tickers = [
                t["ticker"] for t in filing_trades if t["trade_type"] == "New"
            ][:3]
            if new_tickers:
                insights.append(f"New positions: {', '.join(new_tickers)}")
            insights = insights[:4]

            transaction_date = max(
                (t.get("date", "") for t in filing_trades), default=""
            )

            trade_groups.append({
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
            })

        trade_groups.sort(key=lambda g: g["date"], reverse=True)
        return holdings[:30], trade_groups[:12]

    # ── Quarter Diffing (13F) ────────────────────────────────────────

    def _diff_quarters(
        self,
        current_raw: List[Dict],
        previous_raw: List[Dict],
        filing_date: str,
        total_current_value: float,
        split_ratios: Optional[Dict[str, float]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Diff two 13F snapshots to compute trades.

        ``split_ratios`` maps ticker → the product of stock-split ratios effective
        between the two filing dates. Without it a 10:1 split reads as a 9x share
        increase at a 1/10th price and fabricates a huge BOUGHT. Mirrors
        `whale_service._diff_quarters`, which has carried this for a while — the two
        implementations diverging is the exact reason this one was diffing dollar value
        while the other diffed shares.
        """
        split_ratios = split_ratios or {}
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
            curr_shares = curr["shares"] if curr else 0
            prev_shares = prev["shares"] if prev else 0

            # Restate the PRIOR share count into post-split terms before diffing, but
            # only when the observed jump actually matches the split — a count that did
            # not move toward the ratio is a spinoff / ADR-ratio change / an
            # already-adjusted feed, and restating it there would invent a trade of its
            # own. Same three-way test as whale_service.
            ratio = split_ratios.get(ticker, 1.0)
            if ratio and ratio != 1.0 and curr and prev and prev_shares > 0:
                ratio_obs = curr_shares / prev_shares
                if abs(ratio_obs - ratio) <= 0.15 * ratio:
                    prev_shares = prev_shares * ratio
                elif ratio_obs >= (1.0 + ratio) / 2.0:
                    prev_shares = prev_shares * ratio

            # ⚠️ SHARES at an implied price — NOT `curr_val - prev_val`.
            #
            # A 13F reports positions, not transactions, and the reported VALUE moves
            # with the share price. Diffing value therefore books pure price
            # appreciation as a trade: a holder who did nothing while a stock rose 20%
            # was written to `whale_trades` as BOUGHT/Increased for the entire gain,
            # and a holder who SOLD into a rally could come out looking like a buyer.
            # `calc_13f_trade_dollars` exists precisely to strip that out, and
            # `whale_service._diff_quarters` and `holders_service` already use it — so
            # the same whale got a different answer depending on which path last wrote.
            #
            # It returns (None, 0.0) when the move is below the noise floor or when no
            # implied price can be derived, which subsumes the old `abs(diff) < 1000`
            # filter. New and Closed positions are handled below, where there is only
            # one side and the position's own value IS the trade.
            action_hint, amount_hint = calc_13f_trade_dollars(
                curr_shares=curr_shares,
                curr_value=curr_val,
                prev_shares=prev_shares,
                prev_value=prev_val,
            )
            both_sides = curr is not None and prev is not None
            if both_sides and action_hint is None:
                continue
            if not both_sides and max(curr_val, prev_val) < 1000:
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
            elif action_hint == "BOUGHT":
                trade = {
                    "ticker": ticker, "company_name": name,
                    "action": "BOUGHT", "trade_type": "Increased",
                    "amount": amount_hint,
                    "previous_allocation": round(prev_alloc, 2),
                    "new_allocation": round(new_alloc, 2),
                    "date": filing_date,
                }
                total_bought += amount_hint
            else:
                trade = {
                    "ticker": ticker, "company_name": name,
                    "action": "SOLD", "trade_type": "Decreased",
                    "amount": amount_hint,
                    "previous_allocation": round(prev_alloc, 2),
                    "new_allocation": round(new_alloc, 2),
                    "date": filing_date,
                }
                total_sold += amount_hint

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
        """Build sectors from FMP institutional industry breakdown.

        Maps granular SIC codes → 11 GICS sectors via _map_sic_to_sector.
        Aggregates weights, sorts descending, "Other" always last.
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
                item.get("weight") or item.get("weightPercentage") or 0
            )
            if weight > 0:
                sector_accum[sector] = sector_accum.get(sector, 0) + weight

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

        # Apply logos + company names from fetched profiles
        for h in holdings:
            h["logo_url"] = logo_cache.get(h["ticker"])
            profile = self._profile_cache.get(h["ticker"])
            if profile:
                fmp_name = profile.get("companyName")
                # Upgrade SEC filing names to proper company names
                if fmp_name and (
                    h.get("company_name", "") == h.get("ticker", "")
                    or h.get("company_name", "").isupper()
                ):
                    h["company_name"] = fmp_name

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
        is_congress: bool = False,
    ) -> Tuple[Dict, str]:
        """Generate behavior + sentiment via Gemini in parallel."""
        behavior_task = self._ai_behavior(
            whale_name, holdings, trade_group, sectors, is_congress
        )
        sentiment_task = self._ai_sentiment(
            whale_name, holdings, trade_group, sectors, is_congress
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
            sentiment = _fallback_sentiment(
                holdings, trade_group, sectors, is_congress
            )

        return behavior, sentiment

    async def _ai_behavior(
        self,
        whale_name: str,
        holdings: List[Dict],
        trade_group: Optional[Dict],
        sectors: List[Dict],
        is_congress: bool = False,
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
            if is_congress:
                # Congress: direction + range only, NEVER a precise dollar sum.
                rng = trade_group.get("net_amount_range")
                amount_txt = f" ({rng})" if rng else ""
                trade_info = (
                    f"{trade_group['trade_count']} disclosed trades: "
                    f"{buys} buys, {sells} sells. "
                    f"Net direction: {trade_group['net_action']}{amount_txt}"
                )
            else:
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

        system = _IDENTITY_RULE + (
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
        is_congress: bool = False,
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
            if is_congress:
                # Congress: NO precise dollar amount — only direction + range.
                rng = trade_group.get("net_amount_range")
                amount_txt = f" ({rng})" if rng else ""
                trade_info = (
                    f"Latest disclosure: {trade_group['trade_count']} trades, "
                    f"net {trade_group['net_action'].lower()}{amount_txt}. "
                )
            else:
                trade_info = (
                    f"Recent filing: {trade_group['trade_count']} trades, "
                    f"net {trade_group['net_action'].lower()} "
                    f"${trade_group.get('net_amount', 0):,.0f}. "
                )
            if top_buys:
                trade_info += f"Top buys: {', '.join(top_buys)}. "
            if top_sells:
                trade_info += f"Top sells: {', '.join(top_sells)}. "

        if is_congress:
            prompt = (
                f"Write a 2-3 sentence summary of the recent disclosed trading "
                f"activity of {whale_name}.\n\n"
                f"Disclosed Data:\n"
                f"- Recently Traded: {top_holdings_str}\n"
                f"- Sector Exposure: {top_sectors_str}\n"
                f"- {trade_info}\n\n"
                "Requirements:\n"
                "- These are congressional STOCK Act disclosures reported ONLY "
                "as dollar RANGES, on a 30-45 day lag. NEVER state a precise "
                "dollar figure — say 'a range of' or use the provided range.\n"
                "- Describe only the disclosed direction (net buying/selling) "
                "and the tickers/sectors listed above.\n"
                "- Do NOT infer motive, strategy, sentiment, or investment "
                "thesis (no 'profit-taking', 'cautious stance', 'conviction', "
                "etc.). Stick strictly to what was disclosed.\n"
                "- Third person, present tense, under 55 words.\n"
                "- Only reference the tickers and sectors provided above."
            )
        else:
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

        system = _IDENTITY_RULE + (
            "You are a financial analyst writing concise investor summaries. "
            "Write naturally without markdown. Only state facts present in the "
            "provided data — never fabricate figures. Return only the summary "
            "paragraph."
        )

        async with GEMINI_SEMAPHORE:
            result = await self.gemini.generate_text(
                prompt=prompt, system_instruction=system
            )

        return result["text"].strip()

    # ── ytd_return ───────────────────────────────────────────────────

    def _compute_risk_profile(
        self,
        holdings: List[Dict],
        sectors: List[Dict],
        trade_group: Optional[Dict],
        perf_data: Optional[Dict] = None,
    ) -> str:
        """Compute risk profile from holdings patterns and FMP performance data.

        Uses turnover rate, holding period, and sector tilt to classify
        investors. Concentration alone does not equal risk — Buffett is
        concentrated but very conservative.
        """
        score = 50  # Start at moderate
        perf = perf_data or {}

        # 1. Turnover rate — key risk signal
        # FMP provides `turnover` directly (0-1 range, 0 = buy-and-hold)
        turnover = perf.get("turnover", None)
        if turnover is not None:
            # ⚠️ Ordered MOST-EXTREME FIRST. The old order tested `> 0.50` before
            # `> 0.75`, so the `+25` branch was unreachable dead code and the most
            # aggressive traders in the registry scored identically to merely active
            # ones — a risk badge shown to the user as fact.
            if turnover > 0.75:
                score += 25  # Very high turnover = very aggressive
            elif turnover > 0.50:
                score += 15  # High turnover = aggressive
            elif turnover < 0.10:
                score -= 20  # Very low turnover = conservative buy-and-hold
            elif turnover < 0.25:
                score -= 10
        elif trade_group:
            # Fallback: estimate turnover from trade count
            trade_count = trade_group.get("trade_count", 0)
            holdings_count = len(holdings) or 1
            est_turnover = trade_count / holdings_count
            if est_turnover > 0.5:
                score += 15
            elif est_turnover < 0.1:
                score -= 10

        # 2. Average holding period — long hold = conservative
        # `_finite_float(..., default=None)`-style handling: FMP frequently omits this
        # field, and the old `perf.get("averageHoldingPeriod", 0)` turned ABSENT into
        # 0 → `< 3` → "+10 short-term trader". Every whale with no holding-period data
        # was therefore biased toward "aggressive" on the strength of a missing key.
        # A present-but-null value additionally raised TypeError on `None > 20`.
        raw_hold = perf.get("averageHoldingPeriod")
        try:
            avg_hold = float(raw_hold) if raw_hold is not None else None
        except (TypeError, ValueError):
            avg_hold = None
        if avg_hold is None or not math.isfinite(avg_hold):
            pass                      # no evidence → no adjustment, in either direction
        elif avg_hold > 20:
            score -= 15  # Very long-term holder
        elif avg_hold > 10:
            score -= 5
        elif avg_hold < 3:
            score += 10  # Short-term trader

        # 3. Sector diversity (only if we have data)
        if sectors:
            num_sectors = len(sectors)
            if num_sectors <= 2:
                score += 10
            elif num_sectors >= 6:
                score -= 5

            # Growth vs value tilt
            growth_sectors = {"Technology", "Consumer Cyclical", "Communication Services"}
            growth_weight = sum(
                s.get("allocation", 0) for s in sectors
                if s.get("name") in growth_sectors
            )
            if growth_weight > 60:
                score += 10

        # Map score to label
        if score <= 30:
            return "conservative"
        elif score <= 55:
            return "moderate"
        elif score <= 75:
            return "aggressive"
        else:
            return "very_aggressive"

    async def _compute_ytd_return(
        self,
        whale_id: str,
        total_value: float,
        perf_data: Any,
        whale: Optional[Dict] = None,
    ) -> AnnualReturn:
        """Best annual return: associated-ticker CAGR → 13F CAGR → none.

        FMP I/O only. Every piece of arithmetic lives in
        `app.services._whale_common`, which is the whole point: this function
        and `whale_service._compute_avg_annual_return` were independent copies
        of one formula and had drifted to DIFFERENT outlier floors (-200 here
        vs -100 there) and different captions for the same number — and this
        copy is the one that actually runs in production.
        """
        ticker = (whale or {}).get("associated_ticker")
        if ticker:
            try:
                price_change = await self.fmp.get_stock_price_change(ticker)
                max_return = (price_change or {}).get("max")
                profiles = await self.fmp.get_company_profiles_batch([ticker])
                ipo_str = profiles[0].get("ipoDate") if profiles else None
                if ipo_str:
                    ipo_date = datetime.strptime(ipo_str, "%Y-%m-%d")
                    years = (datetime.now() - ipo_date).days / 365.25
                    if years >= 1:
                        result = compute_ticker_cagr(max_return, years)
                        if result.is_ok:
                            return result
            except Exception as e:
                logger.warning("  Ticker CAGR failed for %s: %s", ticker, e)

        return compute_13f_cagr(perf_data if isinstance(perf_data, list) else [])

    # ── Persistence ──────────────────────────────────────────────────

    async def _persist(
        self,
        whale_id: str,
        snapshot: Dict[str, Any],
        annual_return: AnnualReturn,
        risk_profile: Optional[str] = None,
        associated_ticker: Optional[str] = None,
    ):
        """Write to snapshot cache + denormalized tables."""
        sb = self.sb

        # 0. Invalidate whale_profile_cache so stale assembled JSON is cleared
        try:
            sb.table("whale_profile_cache").delete().eq(
                "whale_id", whale_id
            ).execute()
        except Exception:
            pass  # Table may not exist yet

        # 1. Upsert snapshot.
        # `trade_groups` is NOT a column — it lives only in-memory (synced to the
        # whale_trade_groups table in step 5). Sending it would make PostgREST
        # reject the whole row (PGRST204) and silently kill the snapshot cache.
        snapshot_ok = False
        try:
            # RETRY-SAFE: a true UPSERT on the whale_id,filing_period unique key, with
            # a fixed payload (snapshot_db_row is a pure key-filter — no now(), no
            # counters), so replaying it converges to the identical row. Supabase's
            # Cloudflare edge intermittently answers 520 with an HTML body, which
            # postgrest raises as APIError('JSON could not be generated'); that blip
            # used to lose the snapshot for a whole day.
            retry_idempotent_sync(
                lambda: sb.table("whale_filing_snapshots").upsert(
                    snapshot_db_row(snapshot), on_conflict="whale_id,filing_period"
                ).execute(),
                what=f"whale_filing_snapshots upsert whale_id={whale_id}",
                logger=logger,
            )
            snapshot_ok = True
        except Exception as e:
            # Transient after retries → WARNING (the next run repairs it); a genuine
            # failure keeps ERROR + stack.
            _transient = is_transient_supabase_error(e)
            (logger.warning if _transient else logger.error)(
                "  Failed to upsert snapshot whale_id=%s period=%s: %s: %s",
                whale_id, snapshot.get("filing_period"),
                type(e).__name__, e,
                exc_info=not _transient,
            )

        # 2. Update whales record. Only advertise the cache as warm
        # (last_hydrated_at) if the snapshot actually persisted — otherwise the
        # serve path would see last_hydrated_at, find no snapshot, and re-run the
        # full FMP fan-out on every view.
        try:
            whale_update: Dict[str, Any] = {
                "portfolio_value": snapshot["total_value"],
                "behavior_summary": snapshot["behavior_summary"],
                "sentiment_summary": snapshot["sentiment_text"],
            }
            # Denormalized activity signals (migration 145). The roster needs them on a
            # single select and PostgREST has no GROUP BY, so they are written here
            # rather than aggregated at read time. Derived from the SNAPSHOT's own
            # filing/disclosure dates, never from the clock — otherwise the monthly
            # congressional period rollover would look like fresh activity even when
            # zero trades were disclosed.
            whale_update.update(_activity_signals(snapshot))
            if snapshot_ok:
                whale_update["last_hydrated_at"] = datetime.now().isoformat()
            # WRITE THE JUDGEMENT, INCLUDING A NEGATIVE ONE. The old code wrote
            # `if ytd_return is not None`, so a bogus value — e.g. one the
            # deleted 1-year fallback produced — could never be cleared:
            # recomputing it as None simply skipped the write and left the bad
            # number in the row forever.
            #
            # `unavailable` is the one status that must NOT overwrite: it means
            # FMP could not be read, and an outage must never blank good data.
            if annual_return.status != RETURN_UNAVAILABLE:
                whale_update["ytd_return"] = annual_return.value
                whale_update["return_source"] = annual_return.source
                whale_update["return_window_years"] = annual_return.window_years
                whale_update["return_status"] = annual_return.status
                whale_update["return_label"] = (
                    return_label_for(annual_return.source, associated_ticker)
                    if annual_return.is_ok
                    # Shipped clients render this verbatim under a green
                    # "+0.0%" they cannot be taught to suppress; at least stop
                    # captioning that zero as a CAGR.
                    else unavailable_return_label(annual_return.status)
                )
            if risk_profile:
                whale_update["risk_profile"] = risk_profile
            # RETRY-SAFE: PK-keyed UPDATE with a fixed payload — absorbing, so a
            # replay after a lost ack lands on the same row values.
            retry_idempotent_sync(
                lambda: sb.table("whales").update(whale_update).eq(
                    "id", whale_id
                ).execute(),
                what=f"whales update whale_id={whale_id}",
                logger=logger,
            )
        except Exception as e:
            _transient = is_transient_supabase_error(e)
            (logger.warning if _transient else logger.error)(
                "  Failed to update whale record: %s: %s",
                type(e).__name__, e, exc_info=not _transient,
            )

        # Track whether the denormalized writes (steps 3–5) all succeeded. The
        # dedup skip in _hydrate_one keys on raw_hash; if a denorm write fails
        # here after the snapshot upsert stamped raw_hash, the next run would
        # match the hash and skip ("data unchanged"), never repairing the
        # partially-written tables. We reset raw_hash below if any step failed.
        denorm_ok = True

        # 3. Replace holdings
        #
        # RETRY-SAFE ONLY AS A WHOLE BLOCK — hence the closure. The block OPENS with
        # a DELETE of every row for this whale, so replaying it wipes whatever a
        # partially-committed prior attempt left behind and rebuilds from scratch.
        # ⚠️ An individual insert must NEVER be retried on its own:
        # whale_holdings_whale_id_ticker_key means a committed-but-unacked row makes
        # the replay raise 23505, aborting the block and leaving holdings truncated.
        # That is exactly why retry_idempotent_sync takes a callable — you cannot
        # express "retry insert #17" through it.
        def _sync_holdings():
            sb.table("whale_holdings").delete().eq(
                "whale_id", whale_id
            ).execute()
            # ONE insert, not 30. The postgrest client is SYNCHRONOUS, and this job
            # shares an event loop with the live API — 30 sequential blocking round-trips
            # here (x56 whales, plus the trades loop below) is what made API requests
            # landing during the nightly run wait 40+ seconds.
            #
            # The delete-first, whole-block-only retry rule is UNCHANGED and is in fact
            # strengthened: a single statement cannot partially commit, so there is no
            # longer a mid-loop state for a replay to collide with.
            rows = [
                {
                    "whale_id": whale_id,
                    "ticker": h["ticker"],
                    "company_name": h.get("company_name", h["ticker"]),
                    "logo_url": h.get("logo_url"),
                    "allocation": h.get("allocation", 0),
                    "change_percent": h.get("change_percent", 0),
                }
                for h in snapshot["holdings_data"][:30]
            ]
            if rows:
                sb.table("whale_holdings").insert(rows).execute()

        try:
            retry_idempotent_sync(
                _sync_holdings,
                what=f"whale_holdings sync whale_id={whale_id}",
                logger=logger,
            )
        except Exception as e:
            denorm_ok = False
            _transient = is_transient_supabase_error(e)
            (logger.warning if _transient else logger.error)(
                "  Failed to sync holdings: %s: %s",
                type(e).__name__, e, exc_info=not _transient,
            )

        # 4. Replace sector allocations
        #
        # Same delete-first shape, same whole-block-only retry rule. This table has
        # NO unique key, which makes the granularity even more load-bearing: a
        # per-statement retry here would silently DUPLICATE allocation rows rather
        # than fail loudly.
        def _sync_sectors():
            sb.table("whale_sector_allocations").delete().eq(
                "whale_id", whale_id
            ).execute()
            rows = [
                {
                    "whale_id": whale_id,
                    "sector": sec["name"],
                    "allocation": sec["allocation"],
                }
                for sec in snapshot["sector_data"]
            ]
            if rows:
                sb.table("whale_sector_allocations").insert(rows).execute()

        try:
            retry_idempotent_sync(
                _sync_sectors,
                what=f"whale_sector_allocations sync whale_id={whale_id}",
                logger=logger,
            )
        except Exception as e:
            denorm_ok = False
            _transient = is_transient_supabase_error(e)
            (logger.warning if _transient else logger.error)(
                "  Failed to sync sectors: %s: %s",
                type(e).__name__, e, exc_info=not _transient,
            )

        # 5. Upsert trade groups + their trades (one group per filing).
        #
        # The KNOWN GAP recorded here previously is now closed. The old shape was
        # select-exists → INSERT → insert each trade, which had two failure modes that
        # turned a transient blip into PERMANENT damage:
        #   (a) If the group INSERT committed but its ack was lost, the replay hit the
        #       select-exists guard, `continue`d, and the trades were never written —
        #       and every FUTURE run took the same skip, so the loss was permanent.
        #       The raw_hash reset in 5b could not repair it: the guard defeated it.
        #   (b) `whale_trades` had no unique key, so replaying a mid-loop trade insert
        #       silently DUPLICATED the trade.
        #
        # Both are now upserts against real unique keys —
        # `uq_whale_trade_groups_whale_date` (migration 077) and
        # `uq_whale_trades_group_ticker_action_date` (migration 143) — so a replay
        # REPAIRS the group instead of skipping or duplicating it, and the block is
        # safely idempotent.
        #
        # ⚠️ Still NOT wrapped in a retry helper: `retry_idempotent_sync` replays from
        # the top of the block it guards, and the enclosing per-group try/except already
        # gives per-group isolation. Idempotency makes a replay SAFE, not free.
        #
        # Schema-tolerant on purpose: if migration 143 has not been applied yet, the
        # trades upsert falls back to the previous insert path, so deploy order does not
        # matter in either direction.
        groups = snapshot.get("trade_groups")
        if not groups:
            single = snapshot.get("trade_group")
            groups = [single] if single else []
        for tg in groups:
            if not tg or not tg.get("date"):
                continue
            try:
                # UPSERT, not select-then-insert. Concurrent writers (the hourly job and
                # a live profile build, or two Railway replicas) converge instead of one
                # of them stranding its trades under a losing group id.
                tg_result = sb.table("whale_trade_groups").upsert({
                    "whale_id": whale_id,
                    "date": tg["date"],
                    "trade_count": tg["trade_count"],
                    "net_action": tg["net_action"],
                    "net_amount": tg["net_amount"],
                    "summary": tg.get("summary"),
                    "insights": tg.get("insights", []),
                }, on_conflict="whale_id,date").execute()

                if tg_result.data:
                    tg_id = tg_result.data[0]["id"]
                else:
                    # Some PostgREST configurations return no representation on an
                    # upsert that changed nothing. Read the id back rather than
                    # `continue`-ing, which is precisely how trades used to be
                    # stranded.
                    lookup = (
                        sb.table("whale_trade_groups")
                        .select("id")
                        .eq("whale_id", whale_id)
                        .eq("date", tg["date"])
                        .limit(1)
                        .execute()
                    )
                    if not lookup.data:
                        logger.warning(
                            "  Trade group upsert returned no id for whale=%s date=%s "
                            "— skipping its trades this run",
                            whale_id, tg["date"],
                        )
                        continue
                    tg_id = lookup.data[0]["id"]

                # Existing trades for this group, so a REPAIR run tops up a partially
                # written group instead of either skipping it or duplicating it.
                # ONE write for up to 50 trades, per group. This was the single largest
                # blocking loop in the nightly job.
                self._upsert_trades(sb, whale_id, tg_id, tg.get("trades", [])[:50])
                continue
            except Exception as e:
                denorm_ok = False
                _transient = is_transient_supabase_error(e)
                (logger.warning if _transient else logger.error)(
                    "  Failed to sync trade group: %s: %s",
                    type(e).__name__, e, exc_info=not _transient,
                )

        # 5b. If any denormalized write failed, clear the snapshot's raw_hash so
        # the next scheduled run re-attempts the sync instead of matching the
        # hash and skipping ("data unchanged"). The snapshot JSONB stays intact,
        # so the profile still serves; only the dedup key is reset.
        if snapshot_ok and not denorm_ok:
            try:
                # RETRY-SAFE: key-scoped UPDATE to a constant. Worth retrying because
                # this write IS the self-heal signal — losing it to the same blip that
                # broke the denorm write leaves the partial state hash-skipped
                # indefinitely.
                retry_idempotent_sync(
                    lambda: sb.table("whale_filing_snapshots").update(
                        {"raw_hash": None}
                    ).eq("whale_id", whale_id).eq(
                        "filing_period", snapshot["filing_period"]
                    ).execute(),
                    what=f"raw_hash reset whale_id={whale_id}",
                    logger=logger,
                )
                logger.warning(
                    "  Reset raw_hash for whale_id=%s period=%s — a denormalized "
                    "write failed; next run will repair.",
                    whale_id, snapshot.get("filing_period"),
                )
            except Exception as e:
                # Genuinely worth an ERROR even when transient: the repair signal is
                # lost, so the partial denorm state will be hash-skipped until the
                # snapshot content itself changes.
                logger.error(
                    "  Failed to reset raw_hash for whale_id=%s period=%s — partial "
                    "denormalized state will be SKIPPED by the hash dedup next run: "
                    "%s: %s",
                    whale_id, snapshot.get("filing_period"),
                    type(e).__name__, e, exc_info=True,
                )

        # 6. Generate alert if significant activity detected
        self._maybe_generate_alert(whale_id, snapshot)

    @staticmethod
    def _upsert_trades(
        sb, whale_id: str, tg_id: str, trades: List[Dict[str, Any]]
    ) -> None:
        """Write a group's trades in ONE statement, repairing rather than duplicating.

        Bulk because this job shares an event loop with the live API and the postgrest
        client is synchronous: 50 sequential blocking round-trips per group, across 56
        whales, is what stalled API requests for 40+ seconds during the nightly run.

        Same migration-143 upsert + 42P10 fallback as the single-row version it replaces,
        so it still ships safely against a database without the unique index.
        """
        if not trades:
            return
        # ⚠️ DEDUPE ON THE CONFLICT KEY BEFORE THE BULK UPSERT.
        #
        # Postgres refuses a multi-row `ON CONFLICT DO UPDATE` whose payload touches the
        # same row twice: SQLSTATE 21000, "ON CONFLICT DO UPDATE command cannot affect
        # row a second time". That message contains neither "42p10" nor "no unique or
        # exclusion constraint", so it would fall through the fallback below and raise —
        # and the caller has ALREADY committed the `whale_trade_groups` row advertising
        # `trade_count = N`. The filing card would claim N trades with zero rows behind
        # it, and the 5b `raw_hash` reset guarantees the next run fails identically.
        #
        # One congressional filing routinely discloses the same symbol/direction/
        # transaction-date more than once — spouse + self, two amount buckets, or an FMP
        # page overlap. Measured against live FMP: ~16-45% of filings, including
        # Josh Gottheimer (GOOGL x3 on 2026-08-11) and Ro Khanna.
        #
        # LAST-WINS, which is exactly what the per-row upsert this replaced produced:
        # the second row simply took the DO UPDATE branch and overwrote the first. So
        # this restores the previous behaviour rather than inventing a new one.
        deduped: Dict[tuple, Dict[str, Any]] = {}
        for t in trades:
            row = WhaleHydrator._trade_row(whale_id, tg_id, t)
            deduped[(row["ticker"], row["action"], row["date"])] = row
        rows = list(deduped.values())
        try:
            sb.table("whale_trades").upsert(
                rows, on_conflict="trade_group_id,ticker,action,date"
            ).execute()
        except Exception as e:
            msg = str(e).lower()
            if "42p10" in msg or "no unique or exclusion constraint" in msg:
                # ⚠️ Do NOT claim "migration 143 is not applied" here. That message was
                # FALSE in production and sent readers hunting for an unapplied
                # migration: 42P10 also fires when the unique index EXISTS but is
                # PARTIAL, because PostgREST emits a bare `ON CONFLICT (cols)` and
                # Postgres cannot infer a partial index without a matching WHERE clause.
                logger.warning(
                    "  whale_trades upsert could not infer a conflict target (42P10) — "
                    "the unique index is missing, or it exists but is PARTIAL and so is "
                    "not inferable. Falling back to per-row inserts for %d trade(s).",
                    len(rows),
                )
                # PER-ROW, deliberately, and ONLY on this fallback path.
                #
                # A bulk insert is atomic: one already-present row raises 23505 and takes
                # the whole group's trades with it. That is the exact failure this
                # fallback exists to avoid — measured in production, 12 fallbacks aborted
                # 24 groups in one cycle, so a repair run could never top up a partially
                # written group.
                #
                # This path only runs on a database WITHOUT migration 143, so the slow
                # shape is confined to a schema that no longer exists in production.
                for row in rows:
                    try:
                        sb.table("whale_trades").insert(row).execute()
                    except Exception as row_err:
                        # 23505 = the row is already there, which is precisely the state
                        # the upsert was trying to reach. That is the fallback SUCCEEDING.
                        rmsg = str(row_err).lower()
                        if "23505" in rmsg or "duplicate key value" in rmsg:
                            continue
                        raise
            else:
                raise

    @staticmethod
    def _trade_row(whale_id: str, tg_id: str, trade: Dict[str, Any]) -> Dict[str, Any]:
        """One `whale_trades` row. Shared by the bulk writer and its fallback."""
        return {
            "whale_id": whale_id,
            "trade_group_id": tg_id,
            "ticker": trade["ticker"],
            "company_name": trade.get("company_name", trade["ticker"]),
            "action": trade["action"],
            "trade_type": trade["trade_type"],
            "amount": trade["amount"],
            # Congress STOCK Act range + disclosure date (None for 13F).
            # Requires migration #076 columns.
            "amount_range": trade.get("amount_range"),
            "disclosure_date": trade.get("disclosure_date"),
            "previous_allocation": trade.get("previous_allocation"),
            "new_allocation": trade.get("new_allocation"),
            "date": trade.get("date", ""),
        }

    def _maybe_generate_alert(
        self, whale_id: str, snapshot: Dict[str, Any]
    ):
        """Create a whale alert if the latest trade group has significant activity."""
        tg = snapshot.get("trade_group")
        if not tg:
            return

        trades = tg.get("trades", [])
        net_amount = abs(tg.get("net_amount", 0))
        trade_count = tg.get("trade_count", 0)
        net_action = tg.get("net_action", "BOUGHT")

        # Thresholds for alert generation
        # Institutional: >$500M move or 5+ new positions
        # Congressional: >$1M move
        new_positions = sum(1 for t in trades if t.get("trade_type") == "New")
        should_alert = (
            net_amount >= 500_000_000
            or new_positions >= 5
            or (net_amount >= 1_000_000 and trade_count >= 3)
        )
        if not should_alert:
            return

        # Find the biggest trade for ticker reference
        biggest = max(trades, key=lambda t: abs(t.get("amount", 0)), default=None)
        ticker = biggest.get("ticker") if biggest else None

        # Build alert text. Congress groups carry a summed STOCK Act range
        # (net_amount_range); NEVER show them a fabricated precise midpoint $.
        action_word = "bought" if net_action == "BOUGHT" else "sold"
        net_amount_range = tg.get("net_amount_range")  # congress-only; None for 13F
        if net_amount_range:
            amount_str = net_amount_range
        elif net_amount >= 1_000_000_000:
            amount_str = f"${net_amount / 1_000_000_000:.1f}B"
        elif net_amount >= 1_000_000:
            amount_str = f"${net_amount / 1_000_000:.0f}M"
        else:
            amount_str = f"${net_amount / 1_000:,.0f}K"

        title = f"Large whale move detected"
        if new_positions >= 5:
            description = (
                f"Opened {new_positions} new positions worth {amount_str} total"
            )
        else:
            ticker_text = f" in {ticker}" if ticker else ""
            description = f"Just {action_word} {amount_str}{ticker_text}"

        try:
            # Expire old alerts for this whale
            self.sb.table("whale_alerts").update(
                {"is_active": False}
            ).eq("whale_id", whale_id).eq("is_active", True).execute()

            # Insert new alert with 7-day expiry
            from datetime import timezone
            self.sb.table("whale_alerts").insert({
                "whale_id": whale_id,
                "title": title,
                "description": description,
                "ticker": ticker,
                "action_title": "View Full Alert",
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(days=7)
                ).isoformat(),
            }).execute()
            logger.info("  Created alert for whale %s: %s", whale_id, description)
        except Exception as e:
            logger.error("  Failed to create alert: %s", e)


# ── Module-Level Helpers ─────────────────────────────────────────────


def _activity_signals(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Denormalized activity columns for `whales` (migration 145).

    `last_activity_date` is MAX over the trade groups' OWN dates — the 13F filing date or
    the congressional disclosure date — so it reflects the filer, not this job. Using
    `datetime.now()` here (or leaning on `last_hydrated_at`) would make every monthly
    congressional snapshot rollover look like fresh activity even when zero trades were
    disclosed.

    `last_filing_period` is 13F-only: congressional snapshots key on a wall-clock month
    (`YYYY-MM`), and letting that through would render a politician as having filed a 13F
    quarter they never file.
    """
    groups = snapshot.get("trade_groups") or []
    if not groups:
        single = snapshot.get("trade_group")
        groups = [single] if single else []
    dates = [
        str(g.get("date"))
        for g in groups
        if g and g.get("date") and _ISO_DATE_RE.match(str(g.get("date")))
    ]

    out: Dict[str, Any] = {}
    if dates:
        out["last_activity_date"] = max(dates)
    period = snapshot.get("filing_period")
    if isinstance(period, str) and _QUARTER_PERIOD_RE.match(period.strip()):
        out["last_filing_period"] = period.strip()
    return out


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
    """One-line trade group summary. Rendered verbatim under the trade-group card.

    ⚠️ Order matters, and the old order made two branches unreachable: with
    ``sells == []`` the first test is ``len(buys) > 0``, which any single buy satisfies,
    so "Pure buying activity" could never be reached and a lone purchase was announced
    as "Heavy accumulation with 1 buys" — wrong in register AND ungrammatical. The
    one-sided cases are therefore checked FIRST, and the ratio tests now require enough
    trades for "heavy" to mean something.
    """
    n_buys, n_sells = len(buys), len(sells)

    if n_buys and not n_sells:
        return f"Pure buying activity with {n_buys} {_positions(n_buys)}"
    if n_sells and not n_buys:
        return f"Pure selling activity with {n_sells} {_positions(n_sells)}"
    if n_buys >= 3 and n_buys > n_sells * 2:
        return f"Heavy accumulation with {n_buys} buys"
    if n_sells >= 3 and n_sells > n_buys * 2:
        return f"Significant reduction with {n_sells} sells"
    return "Portfolio rebalancing"


def _positions(n: int) -> str:
    """"position" / "positions" — a count of 1 must not read "1 positions"."""
    return "position" if n == 1 else "positions"


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
    is_congress: bool = False,
) -> str:
    """Rule-based fallback when Gemini fails.

    For congress, dollar figures are STOCK Act ranges disclosed on a lag, so
    never state a precise net amount — describe direction + range instead.
    """
    top_tickers = ", ".join(h["ticker"] for h in holdings[:5])
    top_sector = sectors[0]["name"] if sectors else "various sectors"

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
                f"disclosed on a 30-45 day lag. Activity summary: {activity}."
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
