"""
Holders service — fetches institutional holder data, insider trading, and
insider roster from FMP, computes shareholder breakdown, recent activities,
and returns a response matching the iOS HoldersData struct.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``holders_cache`` table (24-hour TTL)

Smart Money tabs currently return placeholder data.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.services._insider_common import (
    classify_insider_transaction,
    normalize_insider_name,
)
from app.services._whale_common import (
    parse_congress_amount_dollars,
    calc_13f_trade_dollars,
)
from app.schemas.holders import (
    CongressActivitiesDataSchema,
    CongressActivitySchema,
    CongressActivitySummarySchema,
    DailyPricePointSchema,
    HoldersResponse,
    InstitutionalActivitySchema,
    InstitutionalHolderSchema,
    InsiderActivitiesDataSchema,
    InsiderActivitySchema,
    InsiderActivitySummarySchema,
    RecentActivitiesFlowSummarySchema,
    RecentActivitiesSchema,
    ShareholderBreakdownSchema,
    SmartMoneyDataSchema,
    SmartMoneyFlowDataPointSchema,
    SmartMoneyFlowSummarySchema,
    StockPriceDataPointSchema,
    Top10OwnersSchema,
    TopInsiderSchema,
    TopInstitutionSchema,
)

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


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(record: Dict[str, Any], key: str, default: float = 0.0) -> float:
    """Safely extract a float value from a dict."""
    val = record.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


# Backward-compat alias — callers use the private name; shared impl lives in _insider_common.
_classify_insider_transaction = classify_insider_transaction


_SUPABASE_CACHE_TTL_HOURS = 24

# The most recent N quarters are NOT settled: 13F filings arrive over the ~45
# days after quarter-end and keep getting amended for months. A row cached
# early (few filers reported) can be wildly wrong, so we never trust the
# hedge_fund_quarters cache for these — always recompute + re-upsert them.
_REFRESH_RECENT_QUARTERS = 2

# Hedge-fund quarterly flow is now stored in MILLIONS OF SHARES (not dollars):
# 13F share changes are comparable across quarters, whereas dollar values are
# distorted by price drift. Any cached row computed before this instant holds
# the legacy dollar values and must be recomputed. Bump this when the flow
# unit/formula changes again.
_HFQ_SHARES_FLOOR = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)


# ── Service class ─────────────────────────────────────────────────

class HoldersService:
    """Builds the HoldersData payload for a given ticker."""

    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    # ── Public API ────────────────────────────────────────────────

    async def get_holders(self, ticker: str) -> HoldersResponse:
        ticker = _validate_ticker(ticker)
        cache_key = f"holders:{ticker}"

        # Tier 1: in-memory
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Holders in-memory cache HIT for {ticker}")
            return cached

        # In-flight dedup
        if cache_key in _inflight:
            logger.info(f"Holders in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            # Tier 2: Supabase persistent cache
            db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
            if db_cached is not None:
                logger.info(f"Holders Supabase cache HIT for {ticker}")
                _cache_set(cache_key, db_cached)
                future.set_result(db_cached)
                return db_cached

            # Cache miss — build from FMP
            logger.info(f"Holders cache MISS for {ticker} — fetching from FMP")
            result = await self._build_holders(ticker)
            _cache_set(cache_key, result)
            future.set_result(result)

            # Upsert to Supabase in background (non-blocking)
            asyncio.ensure_future(
                asyncio.to_thread(self._upsert_supabase_cache_safe, ticker, result)
            )

            return result
        except Exception as e:
            future.set_exception(e)
            raise
        finally:
            _inflight.pop(cache_key, None)

    # ── Supabase cache helpers ────────────────────────────────────

    def _check_supabase_cache(self, ticker: str) -> Optional[HoldersResponse]:
        try:
            row = (
                self.supabase.table("holders_cache")
                .select("response_json, cached_at")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at", "")
            if cached_at_str:
                cached_at = datetime.fromisoformat(
                    cached_at_str.replace("Z", "+00:00")
                )
                age = datetime.now(timezone.utc) - cached_at
                if age > timedelta(hours=_SUPABASE_CACHE_TTL_HOURS):
                    logger.info(
                        f"Holders Supabase cache STALE for {ticker} "
                        f"(age={age.total_seconds()/3600:.1f}h)"
                    )
                    return None

            response_json = entry.get("response_json")
            if response_json:
                result = HoldersResponse(**response_json)
                # Validate hedge fund flow data — reject one-sided cached data
                if self._has_one_sided_hedge_fund_data(result):
                    logger.info(
                        f"Holders Supabase cache STALE for {ticker} "
                        f"(one-sided hedge fund flow data detected)"
                    )
                    return None
                return result
            return None
        except Exception as e:
            logger.warning(f"Holders Supabase cache check failed for {ticker}: {e}")
            return None

    @staticmethod
    def _has_one_sided_hedge_fund_data(result: HoldersResponse) -> bool:
        """Detect stale cache where hedge fund quarters have only buy OR sell.

        When both buyers and sellers exist (which is nearly always the case),
        we expect both buy_volume > 0 and sell_volume > 0.  One-sided data
        indicates the row was computed before the _estimate_buy_sell logic
        was added.
        """
        try:
            hf = result.hedge_funds_data
            if not hf or not hf.flow_data:
                return False
            for pt in hf.flow_data:
                if not pt.has_activity:
                    continue
                # If there's activity but only one side has volume → stale
                if (pt.buy_volume > 0) != (pt.sell_volume > 0):
                    return True
            return False
        except Exception:
            return False

    def _upsert_supabase_cache_safe(self, ticker: str, result: HoldersResponse):
        try:
            self.supabase.table("holders_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker",
            ).execute()
            logger.info(f"Holders Supabase cache UPSERTED for {ticker}")
        except Exception as e:
            logger.warning(f"Holders Supabase upsert failed for {ticker}: {e}")

    # ── Build holders data from FMP ───────────────────────────────

    async def _build_holders(self, ticker: str) -> HoldersResponse:
        """Fetch all FMP data in parallel and assemble the response."""

        # Phase 1: Core data (all in parallel)
        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(days=760)).strftime("%Y-%m-%d")  # ~2 years for hedge fund chart
        # 365-day window for the insider chart/table/list — page the insider
        # fetch back to here so an active ticker's "last 12 months" isn't
        # truncated by a single 200-row page. Business cutoff lives here, not
        # in fmp.py.
        insider_since = (now - timedelta(days=365)).strftime("%Y-%m-%d")

        # Determine data quarter for aggregate institutional fetch
        month = now.month
        if month <= 3:
            data_year, data_quarter = now.year - 1, 4
        elif month <= 6:
            data_year, data_quarter = now.year, 1
        elif month <= 9:
            data_year, data_quarter = now.year, 2
        else:
            data_year, data_quarter = now.year, 3

        (
            shares_float,
            quote_data,
            institutional_holders,
            inst_ownership_summary,
            inst_quarter_aggregate,
            insider_trading,
            insider_roster,
            historical_prices,
            senate_latest,
            house_latest,
            senate_disclosure,
            house_disclosure,
        ) = await asyncio.gather(
            self.fmp.get_shares_float(ticker),
            self.fmp.get_stock_price_quote(ticker),
            self.fmp.get_institutional_holder(ticker, limit=20),
            self.fmp.get_institutional_ownership_summary(ticker),
            self.fmp.get_institutional_ownership_for_quarter(ticker, data_year, data_quarter),
            self.fmp.get_insider_trading(ticker, since_date=insider_since),
            self.fmp.get_insider_roster(ticker),
            self.fmp.get_historical_prices(ticker, from_date=from_date),
            self.fmp.get_senate_latest(limit=1000),
            self.fmp.get_house_latest(limit=1000),
            self.fmp.get_senate_disclosure(ticker),
            self.fmp.get_house_disclosure(ticker),
            return_exceptions=True,
        )

        # Handle failures gracefully
        def _unwrap(val, name, default):
            if isinstance(val, Exception):
                logger.warning(f"{name} fetch failed for {ticker}: {val}")
                return default
            return val

        shares_float = _unwrap(shares_float, "Shares float", {})
        quote_data = _unwrap(quote_data, "Quote", {})
        institutional_holders = _unwrap(institutional_holders, "Institutional holders", [])
        inst_ownership_summary = _unwrap(inst_ownership_summary, "Inst ownership summary", {})
        inst_quarter_aggregate = _unwrap(inst_quarter_aggregate, "Inst quarter aggregate", None)
        insider_trading = _unwrap(insider_trading, "Insider trading", [])
        insider_roster = _unwrap(insider_roster, "Insider roster", [])
        historical_prices = _unwrap(historical_prices, "Historical prices", [])
        senate_latest = _unwrap(senate_latest, "Senate latest", [])
        house_latest = _unwrap(house_latest, "House latest", [])
        senate_disclosure = _unwrap(senate_disclosure, "Senate disclosure", [])
        house_disclosure = _unwrap(house_disclosure, "House disclosure", [])

        current_price = _safe_float(quote_data, "price", 0.0)
        company_profile = shares_float

        # Extract monthly price data from historical prices
        monthly_prices = self._extract_monthly_prices(historical_prices)
        daily_prices = self._extract_daily_prices(historical_prices)

        # Merge congressional trades: symbol-filtered disclosure + latest, deduplicated
        senate_from_latest = [s for s in senate_latest if s.get("symbol", "").upper() == ticker]
        house_from_latest = [h for h in house_latest if h.get("symbol", "").upper() == ticker]
        senate_for_ticker = self._dedup_congress_trades(senate_disclosure, senate_from_latest)
        house_for_ticker = self._dedup_congress_trades(house_disclosure, house_from_latest)

        # Build each section
        breakdown = self._build_shareholder_breakdown(
            company_profile, institutional_holders, insider_roster, current_price,
            inst_ownership_summary,
        )
        recent = self._build_recent_activities(
            institutional_holders, insider_trading, insider_roster, current_price,
            inst_quarter_aggregate=inst_quarter_aggregate,
            daily_prices=daily_prices,
            senate_trades=senate_for_ticker,
            house_trades=house_for_ticker,
        )

        # Build live smart money data
        insider_sm = self._build_insider_smart_money(insider_trading, monthly_prices, daily_prices)
        hedge_sm = await self._build_hedge_fund_smart_money(ticker, daily_prices)
        congress_sm = self._build_congress_smart_money(
            senate_for_ticker, house_for_ticker, monthly_prices, daily_prices
        )

        return HoldersResponse(
            symbol=ticker,
            shareholder_breakdown=breakdown,
            insider_data=insider_sm,
            hedge_funds_data=hedge_sm,
            congress_data=congress_sm,
            recent_activities=recent,
        )

    # ── Shareholder Breakdown ─────────────────────────────────────

    def _build_shareholder_breakdown(
        self,
        profile: Dict[str, Any],
        inst_holders: List[Dict[str, Any]],
        insider_roster: List[Dict[str, Any]],
        current_price: float,
        inst_summary: Optional[Dict[str, Any]] = None,
    ) -> ShareholderBreakdownSchema:
        # ── Derive ownership percentages ──────────────────────────
        # Insiders: from shares-float endpoint (freeFloat %)
        free_float = _safe_float(profile, "freeFloat", 0.0)

        if free_float > 0:
            insiders_pct = max(0.0, 100.0 - free_float)
        else:
            insiders_pct = _safe_float(profile, "insidersPercentage", 0.0)
            if 0 < insiders_pct < 1.0:
                insiders_pct *= 100.0

        # Institutional %: use real total from positions-summary endpoint
        inst_summary = inst_summary or {}
        real_inst_pct = _safe_float(inst_summary, "ownershipPercent", 0.0)

        if real_inst_pct > 0:
            institutions_pct = real_inst_pct
        elif inst_holders:
            # Fallback: sum top holders (partial — less accurate)
            institutions_pct = sum(
                _safe_float(h, "ownership",
                    _safe_float(h, "percentOfSharesHeld", 0.0))
                for h in inst_holders
            )
        else:
            institutions_pct = 0.0

        # Clamp and compute public/other
        insiders_pct = max(0.0, min(100.0, insiders_pct))
        institutions_pct = max(0.0, min(100.0 - insiders_pct, institutions_pct))
        public_other_pct = max(0.0, 100.0 - insiders_pct - institutions_pct)

        # Build top holders (legacy list)
        top_holders = self._build_top_holders(inst_holders[:10])

        # Build top 10 owners
        top_10_institutions = self._build_top_institutions(inst_holders[:10])
        outstanding_shares = _safe_float(profile, "outstandingShares", 0.0)
        top_10_insiders = self._build_top_insiders(
            insider_roster, current_price, outstanding_shares
        )

        return ShareholderBreakdownSchema(
            insiders_percent=round(insiders_pct, 1),
            institutions_percent=round(institutions_pct, 1),
            public_other_percent=round(public_other_pct, 1),
            top_holders=top_holders,
            top_10_owners=Top10OwnersSchema(
                institutions=top_10_institutions,
                insiders=top_10_insiders,
            ),
        )

    def _build_top_holders(
        self, holders: List[Dict[str, Any]]
    ) -> List[InstitutionalHolderSchema]:
        """Build legacy top holders list from analytics data."""
        result = []
        for h in holders:
            # Analytics endpoint uses "ownership" field (already 0-100 scale)
            pct = _safe_float(h, "ownership",
                    _safe_float(h, "percentOfSharesHeld", 0.0))
            change = _safe_float(h, "changeInSharesNumberPercentage",
                        _safe_float(h, "changeInSharesPercentage", 0.0))

            result.append(InstitutionalHolderSchema(
                name=h.get("investorName", h.get("holder", "Unknown")),
                shares_held=_safe_float(h, "sharesNumber", 0.0),
                percent_ownership=round(pct, 2),
                change_percent=round(change, 1) if change != 0.0 else None,
            ))
        return result

    def _build_top_institutions(
        self, holders: List[Dict[str, Any]]
    ) -> List[TopInstitutionSchema]:
        """Build top 10 institutions from analytics data."""
        # Sort by marketValue descending
        sorted_holders = sorted(
            holders,
            key=lambda h: _safe_float(h, "marketValue",
                            _safe_float(h, "value", 0.0)),
            reverse=True,
        )[:10]

        result = []
        for rank, h in enumerate(sorted_holders, 1):
            value = _safe_float(h, "marketValue",
                        _safe_float(h, "value", 0.0))
            pct = _safe_float(h, "ownership",
                    _safe_float(h, "percentOfSharesHeld", 0.0))

            result.append(TopInstitutionSchema(
                rank=rank,
                name=h.get("investorName", h.get("holder", "Unknown")),
                category=self._categorize_institution(
                    h.get("investorName", "")
                ),
                value_in_billions=round(value / 1_000_000_000, 1) if value > 0 else 0.0,
                percent_ownership=round(pct, 1),
            ))
        return result

    def _build_top_insiders(
        self,
        roster: List[Dict[str, Any]],
        current_price: float,
        outstanding_shares: float = 0.0,
    ) -> List[TopInsiderSchema]:
        if not roster:
            return []

        # Build insiders with their share value
        insiders = []
        for r in roster:
            shares = _safe_float(r, "numberOfShares", 0.0)
            value_millions = (shares * current_price) / 1_000_000 if current_price > 0 else 0.0
            # Compute ownership % from shares / outstanding shares
            if outstanding_shares > 0 and shares > 0:
                ownership_pct = (shares / outstanding_shares) * 100.0
            else:
                ownership_pct = _safe_float(r, "ownershipPercent", 0.0)
                if 0 < ownership_pct < 1.0:
                    ownership_pct *= 100.0

            insiders.append({
                "name": normalize_insider_name(r.get("owner")),
                "title": r.get("title", r.get("typeOfOwner", "Officer")),
                "valueInMillions": value_millions,
                "percentOwnership": ownership_pct,
            })

        # Sort by value descending, take top 10
        insiders.sort(key=lambda x: x["valueInMillions"], reverse=True)

        result = []
        for rank, ins in enumerate(insiders[:10], 1):
            result.append(TopInsiderSchema(
                rank=rank,
                name=ins["name"],
                title=ins["title"],
                value_in_millions=round(ins["valueInMillions"], 1),
                percent_ownership=round(ins["percentOwnership"], 4),
            ))
        return result

    @staticmethod
    def _categorize_institution(name: str) -> str:
        """Derive a category label from the institution name."""
        name_lower = name.lower()
        if any(kw in name_lower for kw in ["vanguard", "blackrock", "state street", "fidelity", "geode"]):
            return "Asset Management"
        if any(kw in name_lower for kw in ["morgan stanley", "goldman sachs", "jp morgan", "jpmorgan"]):
            return "Investment Banking"
        if any(kw in name_lower for kw in ["bank of america", "northern trust", "wells fargo"]):
            return "Financial Services"
        if any(kw in name_lower for kw in ["capital", "advisors", "advisory"]):
            return "Investment Advisor"
        if any(kw in name_lower for kw in ["mutual", "fund"]):
            return "Mutual Funds"
        return "Asset Management"

    # ── Recent Activities ─────────────────────────────────────────

    def _build_recent_activities(
        self,
        inst_holders: List[Dict[str, Any]],
        insider_trades: List[Dict[str, Any]],
        insider_roster: List[Dict[str, Any]],
        current_price: float,
        inst_quarter_aggregate: Optional[Dict[str, Any]] = None,
        daily_prices: Optional[List[DailyPricePointSchema]] = None,
        senate_trades: Optional[List[Dict[str, Any]]] = None,
        house_trades: Optional[List[Dict[str, Any]]] = None,
    ) -> RecentActivitiesSchema:
        # Build institutional activities (all, before truncation)
        all_inst_activities = self._build_institutional_activities(inst_holders)
        # Flow summary: prefer aggregate data from ALL institutions (not just top 15)
        flow_summary = self._build_institutional_flow_summary(
            all_inst_activities,
            aggregate_data=inst_quarter_aggregate,
            daily_prices=daily_prices,
        )
        # Sort by absolute value for the display list (return ALL for frontend pagination)
        inst_activities = sorted(
            all_inst_activities, key=lambda a: abs(a.change_in_millions), reverse=True
        )

        # Build insider activities — summary from 12-month window, return all for frontend
        all_insider_acts = self._build_insider_activities(
            insider_trades, insider_roster
        )
        # Insider summary window = trailing 365 days (day-level), the SAME cutoff
        # the insider flow chart and the report's Insider table use — so the
        # summary card's volumes/buyer counts describe the same span as the bars
        # beside them (was 12 calendar months, which dropped the partial 13th
        # month and could disagree with the chart at the boundary).
        insider_cutoff = (
            datetime.now(timezone.utc) - timedelta(days=365)
        ).strftime("%Y-%m-%d")
        acts_in_window = [a for a in all_insider_acts if a.date >= insider_cutoff]
        insider_summary = self._build_insider_activity_summary(acts_in_window)

        # Build congress activities — return all for frontend.
        # Congress summary keeps the calendar-month window (unchanged).
        month_keys_set = set(self._generate_month_keys(12))
        all_congress_acts = self._build_congress_activities(
            senate_trades or [], house_trades or [], daily_prices
        )
        congress_in_window = [
            a for a in all_congress_acts
            if f"{a.date[5:7]}/{a.date[:4]}" in month_keys_set
        ]
        congress_summary = self._build_congress_activity_summary(congress_in_window)

        return RecentActivitiesSchema(
            institutional_flow_summary=flow_summary,
            institutional_activities=inst_activities,
            insider_activities=InsiderActivitiesDataSchema(
                summary=insider_summary,
                activities=all_insider_acts,
            ),
            congress_activities=CongressActivitiesDataSchema(
                summary=congress_summary,
                activities=all_congress_acts,
            ),
        )

    def _build_institutional_activities(
        self, holders: List[Dict[str, Any]]
    ) -> List[InstitutionalActivitySchema]:
        """Convert institutional holder analytics into recent activity entries."""
        result = []
        for h in holders[:15]:
            # Skip unattributed FMP 13F rows — a blank investorName + null holder
            # (often a bogus "+100% / new" multi-billion stake) can't be shown
            # to a user as anything but the generic "Asset Management" category.
            name = (h.get("investorName") or h.get("holder") or "").strip()
            if not name:
                continue

            # Shared 13F formula — same one whale_service uses to populate
            # Supabase whale_trades.amount — so alert totals match this view.
            shares_change = _safe_float(h, "changeInSharesNumber", 0.0)
            total_shares = _safe_float(h, "sharesNumber", 0.0)
            total_value = _safe_float(
                h, "marketValue", _safe_float(h, "value", 0.0)
            )
            # Reconstruct prev from current + delta so we can reuse the helper.
            prev_shares = max(total_shares - shares_change, 0.0)
            implied_price = (
                total_value / total_shares if total_shares > 0 else 0.0
            )
            prev_value = prev_shares * implied_price

            action, amount = calc_13f_trade_dollars(
                curr_shares=total_shares,
                curr_value=total_value,
                prev_shares=prev_shares,
                prev_value=prev_value,
                min_amount=0.0,  # institutional section shows tiny deltas too
            )

            change_pct = _safe_float(h, "changeInSharesNumberPercentage",
                            _safe_float(h, "changeInSharesPercentage", 0.0))

            if action is None or amount == 0.0:
                continue

            # Preserve sign (negative = sold) for the UI's existing contract.
            signed_amount = amount if action == "BOUGHT" else -amount
            change_value_millions = signed_amount / 1_000_000

            # Filing date
            date_str = h.get("filingDate", h.get("dateReported", ""))
            if not date_str:
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # Brand-new position: prior quarter held nothing. FMP encodes this as
            # change% = 100, which misreads as "doubled" — flag it so the UI says
            # "New" instead.
            is_new = prev_shares <= 0.0 and total_shares > 0.0 and action == "BOUGHT"

            result.append(InstitutionalActivitySchema(
                institution_name=name,
                category=self._categorize_institution(name),
                date=date_str[:10],
                change_in_millions=round(change_value_millions, 2),
                change_percent=round(change_pct, 2),
                total_held_in_billions=round(total_value / 1_000_000_000, 1) if total_value > 0 else 0.0,
                is_new_position=is_new,
            ))

        # Sort by absolute change value descending (caller truncates to top 10)
        result.sort(key=lambda a: abs(a.change_in_millions), reverse=True)
        return result

    def _build_institutional_flow_summary(
        self,
        activities: List[InstitutionalActivitySchema],
        aggregate_data: Optional[Dict[str, Any]] = None,
        daily_prices: Optional[List[DailyPricePointSchema]] = None,
    ) -> RecentActivitiesFlowSummarySchema:
        """Aggregate institutional activities into a flow summary.

        When ``aggregate_data`` is available (from symbol-positions-summary),
        uses ALL-institution buyer/seller counts + net share change to
        compute realistic gross buy/sell via ``_estimate_buy_sell()``.
        Falls back to summing the (now price-appreciation-stripped)
        per-holder activities when aggregate data is unavailable.
        """
        # Use PREVIOUS quarter to match FMP fetch logic in get_institutional_holder.
        now = datetime.now(timezone.utc)
        month = now.month
        if month <= 3:
            data_year, data_quarter = now.year - 1, 4
        elif month <= 6:
            data_year, data_quarter = now.year, 1
        elif month <= 9:
            data_year, data_quarter = now.year, 2
        else:
            data_year, data_quarter = now.year, 3

        if aggregate_data is not None:
            # ── Primary path: use ALL-institution aggregate data ──
            buyers = (
                int(aggregate_data.get("newPositions") or 0)
                + int(aggregate_data.get("increasedPositions") or 0)
            )
            sellers = (
                int(aggregate_data.get("closedPositions") or 0)
                + int(aggregate_data.get("reducedPositions") or 0)
            )
            shares_change = float(aggregate_data.get("numberOf13FsharesChange") or 0)

            # Quarter-end price (same approach as _build_hedge_fund_smart_money)
            qtr_prices = self._quarter_end_prices(daily_prices)
            price = qtr_prices.get((data_year, data_quarter), 0.0)
            if price <= 0:
                total_inv = float(aggregate_data.get("totalInvested") or 0)
                total_shares = float(aggregate_data.get("numberOf13Fshares") or 1)
                price = total_inv / total_shares if total_shares > 0 else 0

            net_millions = (shares_change * price) / 1_000_000
            buy_vol, sell_vol = self._estimate_buy_sell(net_millions, buyers, sellers)
            inflow = buy_vol / 1000   # millions → billions
            outflow = sell_vol / 1000
        else:
            # ── Fallback: sum per-holder activities (already price-stripped) ──
            inflow_m = 0.0
            outflow_m = 0.0
            for act in activities:
                val = act.change_in_millions
                if val >= 0:
                    inflow_m += val
                else:
                    outflow_m += abs(val)
            inflow = inflow_m / 1000
            outflow = outflow_m / 1000

        quarter_start_month = (data_quarter - 1) * 3 + 1
        month_names = [
            "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
        ]
        period_start = month_names[quarter_start_month]
        period_end = month_names[quarter_start_month + 2]

        return RecentActivitiesFlowSummarySchema(
            period_description=f"{period_start} - {period_end} {data_year}",
            quarter_description=f"Q{data_quarter}",
            in_flow_in_billions=round(inflow, 1),
            out_flow_in_billions=round(outflow, 1),
        )

    def _build_insider_activities(
        self,
        trades: List[Dict[str, Any]],
        roster: List[Dict[str, Any]],
    ) -> List[InsiderActivitySchema]:
        """Convert FMP insider trading data into InsiderActivity entries."""
        # Build a title lookup from roster
        title_map: Dict[str, str] = {}
        for r in roster:
            name = (r.get("owner") or "").strip()
            title = r.get("title") or r.get("typeOfOwner") or "Officer"
            if name:
                title_map[name.lower()] = title

        result = []
        for tx in trades:
            # Only show equity trades (common stock), not RSUs/options/warrants
            security_name = (tx.get("securityName") or "").lower()
            if security_name and "common stock" not in security_name:
                continue

            reporting_name = tx.get("reportingName", tx.get("reportingCik", "Unknown"))
            tx_type_raw = tx.get("transactionType", "")
            classified = _classify_insider_transaction(tx_type_raw)

            # Get shares and price
            shares = abs(_safe_float(tx, "securitiesTransacted", 0.0))
            price = _safe_float(tx, "price", 0.0)
            value_millions = (shares * price) / 1_000_000 if price > 0 else 0.0

            # Skip zero-value entries (RSU conversions at price $0). We keep this
            # price-based filter to exclude the same non-open-market trades as
            # before, even though the stored figure below is now in SHARES.
            if value_millions == 0.0:
                continue

            # Insider activity is now denominated in SHARES (millions of shares)
            # to match the insider flow chart — Form 4 reports exact share
            # counts, comparable over time without a price.
            change_millions = shares / 1_000_000

            # Sign convention: negative for sells
            if "Sell" in classified:
                change_millions = -change_millions

            # Get date
            date_str = tx.get("filingDate", tx.get("transactionDate", ""))
            if not date_str:
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            # Lookup title
            title = title_map.get(reporting_name.lower(), "Officer")

            result.append(InsiderActivitySchema(
                name=reporting_name,
                title=title,
                date=date_str[:10],
                # 6 dp keeps single-share precision (millions → 1e-6 = 1 share);
                # 2 dp would zero out small trades (a 200-share sale = 0.0002M).
                change_in_millions=round(change_millions, 6),  # millions of SHARES
                transaction_type=classified,
                price_at_transaction=round(price, 2),
            ))

        # Sort by date descending; caller truncates for display
        result.sort(key=lambda a: a.date, reverse=True)
        return result

    def _build_insider_activity_summary(
        self, activities: List[InsiderActivitySchema]
    ) -> InsiderActivitySummarySchema:
        """Aggregate insider activities into a summary."""
        informative_buys = 0.0
        informative_sells = 0.0
        buyer_names = set()
        seller_names = set()

        for act in activities:
            if act.transaction_type == "Informative Buy":
                informative_buys += abs(act.change_in_millions)
                buyer_names.add(act.name)
            elif act.transaction_type == "Informative Sell":
                informative_sells += abs(act.change_in_millions)
                seller_names.add(act.name)

        return InsiderActivitySummarySchema(
            period_description="Last 12 Months",
            # 6 dp: these are now millions of SHARES — keep share-level precision.
            informative_buys_in_millions=round(informative_buys, 6),
            informative_sells_in_millions=round(informative_sells, 6),
            num_buyers=len(buyer_names),
            num_sellers=len(seller_names),
        )

    # ── Congress Recent Activities ─────────────────────────────────

    def _build_congress_activities(
        self,
        senate_trades: List[Dict[str, Any]],
        house_trades: List[Dict[str, Any]],
        daily_prices: Optional[List[DailyPricePointSchema]] = None,
    ) -> List[CongressActivitySchema]:
        """Convert FMP congressional trades into CongressActivity entries."""
        # Build a date→price lookup from daily prices
        price_lookup: Dict[str, float] = {}
        if daily_prices:
            for dp in daily_prices:
                price_lookup[dp.date] = dp.price

        def _find_price_at_date(date_str: str) -> float:
            """Find closest price on or before the given date."""
            if date_str in price_lookup:
                return price_lookup[date_str]
            # Try up to 5 days back (weekends/holidays)
            from datetime import timedelta
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                for i in range(1, 6):
                    prev = (dt - timedelta(days=i)).strftime("%Y-%m-%d")
                    if prev in price_lookup:
                        return price_lookup[prev]
            except ValueError:
                pass
            return 0.0

        def _format_district(district: str, chamber: str) -> str:
            """Format district into role string.

            Senate: district='KY' → 'Senator (KY)'
            House: district='TX11' → 'Representative (TX-11)'
            """
            if not district:
                return "Senator" if chamber == "senate" else "Representative"
            if chamber == "senate":
                return f"Senator ({district})"
            # House district: 'TX11' → 'TX-11', 'CA12' → 'CA-12'
            # Extract state letters and district number
            match = re.match(r"([A-Z]{2})(\d+)", district)
            if match:
                return f"Representative ({match.group(1)}-{match.group(2)})"
            return f"Representative ({district})"

        result: List[CongressActivitySchema] = []

        for chamber, trades in [("senate", senate_trades), ("house", house_trades)]:
            for trade in trades:
                first = (trade.get("firstName") or trade.get("first_name") or "").strip()
                last = (trade.get("lastName") or trade.get("last_name") or "").strip()
                if not last:
                    continue

                name = f"{last}, {first}" if first else last
                district = (trade.get("district") or "").strip()
                role = _format_district(district, chamber)

                tx_date = (trade.get("transactionDate") or "")[:10]
                if not tx_date:
                    continue

                tx_type_raw = (trade.get("type") or "").lower()
                amount_str = trade.get("amount", "")
                amount_millions = self._parse_congress_amount(amount_str)
                amount_max_millions = self._parse_congress_amount_max(amount_str)

                if amount_millions <= 0:
                    continue

                # Determine buy/sell
                is_buy = "purchase" in tx_type_raw or "buy" in tx_type_raw or "exchange" in tx_type_raw
                is_sell = "sale" in tx_type_raw or "sell" in tx_type_raw

                if not is_buy and not is_sell:
                    continue

                tx_type = "Purchase" if is_buy else "Sale"
                signed_amount = amount_millions if is_buy else -amount_millions

                # Owner field
                owner_raw = (trade.get("owner") or "").strip()
                owner = owner_raw if owner_raw else "Self"

                # Price at transaction date
                price = _find_price_at_date(tx_date)

                result.append(CongressActivitySchema(
                    name=name,
                    role=role,
                    date=tx_date,
                    change_in_millions=round(signed_amount, 4),
                    amount_range=amount_str,
                    amount_range_max_millions=round(amount_max_millions, 4),
                    owner=owner,
                    transaction_type=tx_type,
                    price_at_transaction=round(price, 2),
                ))

        # Sort by date descending
        result.sort(key=lambda a: a.date, reverse=True)
        return result

    @staticmethod
    def _build_congress_activity_summary(
        activities: List[CongressActivitySchema],
    ) -> CongressActivitySummarySchema:
        """Aggregate congress activities into a summary."""
        total_buys = 0.0
        total_sells = 0.0
        buyer_names: set = set()
        seller_names: set = set()

        for act in activities:
            if act.transaction_type == "Purchase":
                total_buys += abs(act.change_in_millions)
                buyer_names.add(act.name)
            elif act.transaction_type == "Sale":
                total_sells += abs(act.change_in_millions)
                seller_names.add(act.name)

        return CongressActivitySummarySchema(
            period_description="Last 12 Months",
            total_buys_in_millions=round(total_buys, 2),
            total_sells_in_millions=round(total_sells, 2),
            num_buyers=len(buyer_names),
            num_sellers=len(seller_names),
        )

    # ── Smart Money Helpers ──────────────────────────────────────────

    @staticmethod
    def _generate_month_keys(count: int = 12) -> List[str]:
        """Generate last N month keys like '03/2026'.

        Uses proper calendar arithmetic instead of timedelta(days=30)
        to avoid drift that can duplicate or skip months.
        """
        now = datetime.now(timezone.utc)
        months = []
        for i in range(count - 1, -1, -1):
            year = now.year
            month = now.month - i
            while month <= 0:
                month += 12
                year -= 1
            months.append(f"{month:02d}/{year}")
        return months

    @staticmethod
    def _extract_monthly_prices(
        historical: Any,
    ) -> Dict[str, float]:
        """Extract last closing price per month from historical EOD data."""
        monthly: Dict[str, float] = {}
        if isinstance(historical, dict):
            historical = historical.get("historical", [])
        if not isinstance(historical, list):
            return monthly

        for rec in historical:
            date_str = rec.get("date", "")[:10]
            price = _safe_float(rec, "close", _safe_float(rec, "price", 0.0))
            if not date_str or price <= 0:
                continue
            # Key: MM/YYYY
            try:
                month_key = f"{date_str[5:7]}/{date_str[:4]}"
            except (IndexError, ValueError):
                continue
            # Keep the LAST date per month (most recent close)
            monthly[month_key] = price

        return monthly

    @staticmethod
    def _extract_daily_prices(
        historical: Any,
    ) -> List[DailyPricePointSchema]:
        """Extract daily closing prices from historical EOD data, sorted oldest-first."""
        if isinstance(historical, dict):
            historical = historical.get("historical", [])
        if not isinstance(historical, list):
            return []

        points = []
        for rec in historical:
            date_str = rec.get("date", "")[:10]
            price = _safe_float(rec, "close", _safe_float(rec, "price", 0.0))
            if date_str and price > 0:
                points.append(DailyPricePointSchema(date=date_str, price=round(price, 2)))

        # FMP returns newest-first; reverse to oldest-first for chart rendering
        points.sort(key=lambda p: p.date)
        return points

    def _build_price_data(
        self, monthly_prices: Dict[str, float], month_keys: List[str]
    ) -> List[StockPriceDataPointSchema]:
        """Build StockPriceDataPoint list from monthly price map.

        Forward-fills missing months with the last known price instead
        of defaulting to 0.0 (which would create a misleading chart dip).
        """
        result = []
        last_known_price = 0.0
        for m in month_keys:
            price = monthly_prices.get(m, 0.0)
            if price > 0:
                last_known_price = price
            else:
                price = last_known_price
            result.append(StockPriceDataPointSchema(
                month=m, price=round(price, 2)
            ))
        return result

    @staticmethod
    def _build_summary(
        flow_data: List[SmartMoneyFlowDataPointSchema],
    ) -> SmartMoneyFlowSummarySchema:
        """Compute summary from flow data points."""
        total_buy = sum(f.buy_volume for f in flow_data)
        total_sell = sum(f.sell_volume for f in flow_data)
        net = total_buy - total_sell
        return SmartMoneyFlowSummarySchema(
            total_net_flow=round(net, 2),
            total_buy=round(total_buy, 2),
            total_sell=round(total_sell, 2),
            is_positive=net >= 0,
            period_description="12-Month",
        )

    # ── Insider Smart Money Tab ───────────────────────────────────

    def _build_insider_smart_money(
        self,
        insider_trades: List[Dict[str, Any]],
        monthly_prices: Dict[str, float],
        daily_prices: Optional[List[DailyPricePointSchema]] = None,
    ) -> SmartMoneyDataSchema:
        """Build insider smart money from individual trade data.

        Uses insider-trading/search results which have per-transaction
        shares and price. Falls back to quarterly stats if needed.
        """
        # Trailing-365-day window — the SAME cutoff the report's Insider table
        # and recent-transactions list use, so the chart bars, the table totals,
        # and the list can't describe different time spans. 365 days touches up
        # to 13 calendar months, so generate 13 buckets; the oldest is partial
        # and a day-level cutoff (below) keeps only its post-cutoff trades.
        cutoff = datetime.now(timezone.utc) - timedelta(days=365)
        month_keys = self._generate_month_keys(13)
        monthly_flows: Dict[str, Dict[str, float]] = {
            m: {"buy": 0.0, "sell": 0.0} for m in month_keys
        }

        informative_count = 0
        skipped_count = 0

        for tx in insider_trades:
            # Only count informative trades (open-market buys/sells).
            # Skip option grants, 10b5-1 plan sales, gifts, tax withholding,
            # and Form 3 initial ownership filings (empty transactionType).
            tx_type = tx.get("transactionType", "")
            classified = _classify_insider_transaction(tx_type)
            if "Uninformative" in classified:
                skipped_count += 1
                continue

            # Only count equity trades (common stock).
            # Skip RSU vestings, stock options, warrants, phantom stock, etc.
            security_name = (tx.get("securityName") or "").lower()
            if security_name and "common stock" not in security_name:
                skipped_count += 1
                continue

            # Use transactionDate or filingDate
            date_str = (tx.get("transactionDate") or tx.get("filingDate") or "")[:10]
            if not date_str:
                continue

            # Day-level window so the chart's totals equal the report table's
            # (which filters on the same 365-day cutoff). A trade in the partial
            # oldest month but dated before the cutoff is excluded.
            try:
                tx_dt = datetime.strptime(date_str, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc
                )
            except (ValueError, TypeError):
                continue
            if tx_dt < cutoff:
                continue

            m_key = f"{date_str[5:7]}/{date_str[:4]}"
            if m_key not in monthly_flows:
                continue

            # Insider flow is denominated in SHARES (millions of shares), like
            # the hedge-fund chart. Form 4 reports the exact share count
            # (`securitiesTransacted`); shares are comparable over time and
            # need no price. (Price is still used below for the price line.)
            shares = abs(_safe_float(tx, "securitiesTransacted", 0.0))
            if shares <= 0:
                continue
            shares_millions = shares / 1_000_000

            # Buy vs sell from the transactionType classification (P=Buy /
            # S=Sell) — the SAME rule the report table and the recent-trade list
            # use. (Was acquisitionOrDisposition A/D, which could disagree with
            # the table and silently dropped any row whose A/D field was blank.)
            if "Buy" in classified:
                monthly_flows[m_key]["buy"] += shares_millions
                informative_count += 1
            else:  # "Informative Sell"
                monthly_flows[m_key]["sell"] += shares_millions
                informative_count += 1

        logger.info(
            f"Insider smart money: {informative_count} informative trades, "
            f"{skipped_count} uninformative skipped"
        )

        flow_data = [
            SmartMoneyFlowDataPointSchema(
                month=m,
                # 6 dp keeps single-share fidelity (1 share = 1e-6 M). The old
                # round(,2) zeroed out any month under ~5,000 shares, so a real
                # small insider buy arrived as 0.0 and rendered as a 0-height
                # (invisible) bar. iOS floors tiny non-zero bars to a visible
                # minimum, but only if the value survives to it.
                buy_volume=round(monthly_flows[m]["buy"], 6),
                sell_volume=round(monthly_flows[m]["sell"], 6),
                has_activity=(monthly_flows[m]["buy"] > 0 or monthly_flows[m]["sell"] > 0),
            )
            for m in month_keys
        ]

        # Window the daily price line to the SAME trailing-365-day cutoff as the
        # bars. The raw daily series spans ~2 years (sized for the hedge-fund
        # chart, which keeps the full series); stretched over 13-month bars it
        # would sit each bar under the wrong date — misreading "did insiders sell
        # into strength or weakness?". Monthly price_data is already windowed via
        # _build_price_data(month_keys), so it's left as-is.
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        windowed_daily = [
            dp for dp in (daily_prices or []) if dp.date >= cutoff_str
        ]

        return SmartMoneyDataSchema(
            tab="Insider",
            price_data=self._build_price_data(monthly_prices, month_keys),
            daily_prices=windowed_daily,
            flow_data=flow_data,
            summary=self._build_summary(flow_data),
        )

    # ── Hedge Fund Smart Money Tab (quarterly, incremental DB) ────

    @staticmethod
    def _generate_quarter_keys(count: int = 8) -> List[Tuple[int, int]]:
        """Return the last *count* (year, quarter) pairs, oldest-first.

        Starts from the most recently *completed* quarter and works
        backwards.  E.g. in March 2026 the latest completed quarter is
        Q4 2025, so 8 quarters → Q1'24 … Q4'25.
        """
        now = datetime.now(timezone.utc)
        month = now.month
        if month <= 3:
            cur_year, cur_q = now.year - 1, 4
        elif month <= 6:
            cur_year, cur_q = now.year, 1
        elif month <= 9:
            cur_year, cur_q = now.year, 2
        else:
            cur_year, cur_q = now.year, 3

        pairs: List[Tuple[int, int]] = []
        y, q = cur_year, cur_q
        for _ in range(count):
            pairs.append((y, q))
            q -= 1
            if q == 0:
                q = 4
                y -= 1
        pairs.reverse()  # oldest-first
        return pairs

    @staticmethod
    def _quarter_label(year: int, quarter: int) -> str:
        """Format a quarter as a chart label, e.g. ``Q3\\n'25``."""
        return f"Q{quarter}\n'{year % 100:02d}"

    @staticmethod
    def _estimate_buy_sell(
        net_millions: float, buyers: int, sellers: int
    ) -> Tuple[float, float]:
        """Estimate gross buy/sell volumes from net change + buyer/seller counts.

        13F filings only provide the NET dollar change across all
        institutional filers.  We use the ratio of buyers to sellers
        to estimate the gross buy and sell volumes so that each quarter
        shows both a green (buy) and red (sell) bar.

        The constraint ``buy - sell = net_millions`` always holds.
        A safety cap prevents extreme inflation when the
        buyer/seller ratio is close to 50/50.
        """
        total = buyers + sellers
        if total == 0 or net_millions == 0:
            if net_millions >= 0:
                return round(net_millions, 2), 0.0
            return 0.0, round(abs(net_millions), 2)

        buyer_ratio = buyers / total
        ratio_diff = buyer_ratio - (1.0 - buyer_ratio)  # buyer_ratio - seller_ratio
        abs_net = abs(net_millions)

        MAX_GROSS_MULTIPLIER = 5.0

        if abs(ratio_diff) < 1e-6:
            gross = abs_net * MAX_GROSS_MULTIPLIER
        else:
            gross = abs_net / abs(ratio_diff)
            gross = min(gross, abs_net * MAX_GROSS_MULTIPLIER)

        # Derive buy/sell from gross while preserving net constraint:
        # buy - sell = net_millions, buy + sell = gross
        buy_vol = (gross + net_millions) / 2.0
        sell_vol = (gross - net_millions) / 2.0

        # Clamp to non-negative (shouldn't happen with valid cap)
        if buy_vol < 0:
            buy_vol = 0.0
            sell_vol = abs_net
        if sell_vol < 0:
            sell_vol = 0.0
            buy_vol = abs_net

        return round(buy_vol, 2), round(sell_vol, 2)

    @staticmethod
    def _compute_quarter_flow(
        data: Dict[str, Any],
        split_ratio: float = 1.0,
    ) -> Tuple[float, float, float, int, int]:
        """Pure per-quarter flow math, in MILLIONS OF SHARES.

        Returns ``(buy_shares_m, sell_shares_m, net_shares_m, buyers, sellers)``.

        The net is the real net 13F share change from the positions-summary —
        the ONE institutional-flow figure FMP reports completely — in millions
        of shares. We use shares, not dollars: share counts are comparable
        across quarters, whereas dollar values are distorted by price drift.

        ``split_ratio`` corrects a stock split that took effect DURING this
        quarter. FMP reports raw (unadjusted) 13F counts, so ``lastNumberOf13F
        shares`` is pre-split while ``numberOf13Fshares`` is post-split, making
        the raw ``numberOf13FsharesChange`` mostly the split (e.g. NVDA Q2'24 =
        +14.2B from the 10:1, not buying). We restate last quarter onto the
        post-split basis: ``net = cur - last*ratio``. Default ``1.0`` = no split
        = identical to the raw change. (See ``_quarter_split_ratios``.)

        Only the net is measured — 13F discloses end-of-quarter positions, not
        transactions — so the gross buy/sell SPLIT is estimated from the net +
        buyer/seller counts via ``_estimate_buy_sell`` (the green/red bars).
        """
        buyers = (
            int(data.get("newPositions") or 0)
            + int(data.get("increasedPositions") or 0)
        )
        sellers = (
            int(data.get("closedPositions") or 0)
            + int(data.get("reducedPositions") or 0)
        )
        net_shares = float(data.get("numberOf13FsharesChange") or 0)
        if split_ratio and split_ratio != 1.0:
            cur = float(data.get("numberOf13Fshares") or 0)
            last_raw = data.get("lastNumberOf13Fshares")
            last = float(last_raw) if last_raw is not None else (cur - net_shares)
            # Only treat it as a real split when the reported share count
            # actually moved by ~the split ratio (the physical signature of a
            # split). FMP's /splits ALSO returns spinoffs, ADR-ratio changes,
            # and reverse splits with odd ratios where the count did NOT
            # multiply — adjusting those fabricates a huge false change
            # (e.g. GE's 1.253 "split" is a spinoff: shares didn't grow 1.25x).
            if last > 0 and abs(cur / last - split_ratio) <= 0.15 * split_ratio:
                net_shares = cur - last * split_ratio
        net_shares_m = net_shares / 1_000_000
        # Magnitude safety guard: a quarterly net change can't plausibly exceed
        # ~half the institutional shares HELD. Anything above that is a corporate
        # action / data artifact (reverse-split micro-caps, mergers like SIRI) —
        # not real flow. Suppress it (zero flow, but keep the real holder counts)
        # so the chart renders NO bar for that quarter instead of garbage.
        total_m = float(data.get("numberOf13Fshares") or 0) / 1_000_000
        if total_m > 0 and abs(net_shares_m) > 0.5 * total_m:
            return 0.0, 0.0, 0.0, buyers, sellers
        buy_m, sell_m = HoldersService._estimate_buy_sell(
            net_shares_m, buyers, sellers
        )
        return buy_m, sell_m, round(net_shares_m, 2), buyers, sellers

    @staticmethod
    def _quarter_split_ratios(
        splits: Optional[List[Dict[str, Any]]],
        pairs: List[Tuple[int, int]],
    ) -> Dict[Tuple[int, int], float]:
        """Map each (year, quarter) → product of stock-split ratios that took
        effect DURING that quarter (prev quarter-end < split date <= quarter-end).

        A split inflates the prior quarter's reported 13F share count, so the
        split quarter's net must restate last quarter onto the post-split basis
        (see ``_compute_quarter_flow``). FMP ``/splits`` rows carry ``date`` +
        ``numerator``/``denominator`` (e.g. 10/1 = 10:1). Quarters with no split
        map to ``1.0``.
        """
        events: List[Tuple[str, float]] = []
        for s in splits or []:
            d = str(s.get("date") or "")[:10]
            num = s.get("numerator")
            den = s.get("denominator")
            if d and num and den:
                try:
                    events.append((d, float(num) / float(den)))
                except (ValueError, ZeroDivisionError, TypeError):
                    continue
        _Q_END = {1: "-03-31", 2: "-06-30", 3: "-09-30", 4: "-12-31"}
        out: Dict[Tuple[int, int], float] = {}
        for (y, q) in pairs:
            qend = f"{y}{_Q_END[q]}"
            pend = f"{y - 1}-12-31" if q == 1 else f"{y}{_Q_END[q - 1]}"
            ratio = 1.0
            for sdate, sr in events:
                if pend < sdate <= qend:
                    ratio *= sr
            out[(y, q)] = ratio
        return out

    # ── DB helpers for hedge_fund_quarters ──────────────────────

    def _load_existing_quarters(
        self, ticker: str, pairs: List[Tuple[int, int]]
    ) -> Dict[Tuple[int, int], Dict[str, Any]]:
        """Read already-computed quarters from Supabase."""
        try:
            years = list({y for y, _ in pairs})
            rows = (
                self.supabase.table("hedge_fund_quarters")
                .select("*")
                .eq("ticker", ticker)
                .in_("year", years)
                .execute()
            )
            target_set = {tuple(p) for p in pairs}
            result: Dict[Tuple[int, int], Dict[str, Any]] = {}
            for r in rows.data or []:
                key = (r["year"], r["quarter"])
                if key not in target_set:
                    continue
                # Skip rows that need re-computation:
                # 1) Truly empty rows (no flow AND no holders) — recompute.
                #    A magnitude-suppressed quarter (see _compute_quarter_flow)
                #    has zero flow but REAL buyer/seller counts; KEEP it so it
                #    isn't refetched forever — the chart just renders no bar.
                # 2) Old logic: one side is zero while both buyers & sellers exist
                bv = r.get("buy_volume", 0) or 0
                sv = r.get("sell_volume", 0) or 0
                nf = r.get("net_flow", 0) or 0
                bc = r.get("buyers_count", 0) or 0
                sc = r.get("sellers_count", 0) or 0
                if bv == 0 and sv == 0 and nf == 0 and bc == 0 and sc == 0:
                    continue
                if nf != 0 and bc > 0 and sc > 0 and (bv == 0 or sv == 0):
                    continue
                # 3) Legacy DOLLAR rows (computed before the shares switch):
                #    flow is now stored in millions of SHARES, so the old dollar
                #    values are the wrong unit — recompute.
                ca = r.get("computed_at")
                try:
                    ca_dt = datetime.fromisoformat(str(ca).replace("Z", "+00:00"))
                    if ca_dt < _HFQ_SHARES_FLOOR:
                        continue
                except (ValueError, TypeError, AttributeError):
                    continue  # missing/unparseable timestamp → recompute to be safe
                result[key] = r
            return result
        except Exception as e:
            logger.warning(f"hedge_fund_quarters read failed for {ticker}: {e}")
            return {}

    def _save_quarters(
        self, ticker: str, rows: List[Dict[str, Any]]
    ) -> None:
        """Upsert computed quarter rows into Supabase."""
        if not rows:
            return
        # Stamp computed_at before upsert: Postgres DEFAULT now() only fires on
        # INSERT, so an upsert-UPDATE of an existing (ticker, year, quarter) row
        # would keep its OLD computed_at — leaving it permanently below
        # _HFQ_SHARES_FLOOR, so _load_existing_quarters treats it as stale and
        # every holders build re-fetches all 8 quarters from FMP. setdefault lets
        # a caller (e.g. the bulk hydrator) pass an explicit stamp.
        now_iso = datetime.now(timezone.utc).isoformat()
        for r in rows:
            r.setdefault("computed_at", now_iso)
        try:
            self.supabase.table("hedge_fund_quarters").upsert(
                rows, on_conflict="ticker,year,quarter"
            ).execute()
            logger.info(
                f"hedge_fund_quarters: upserted {len(rows)} rows for {ticker}"
            )
        except Exception as e:
            logger.warning(f"hedge_fund_quarters upsert failed for {ticker}: {e}")

    # ── Main builder ───────────────────────────────────────────

    @staticmethod
    def _quarter_end_prices(
        daily_prices: Optional[List[DailyPricePointSchema]],
    ) -> Dict[Tuple[int, int], float]:
        """Extract the last closing price per quarter from daily price data."""
        qtr_prices: Dict[Tuple[int, int], float] = {}
        if not daily_prices:
            return qtr_prices
        for dp in daily_prices:
            try:
                m = int(dp.date[5:7])
                y = int(dp.date[:4])
            except (IndexError, ValueError):
                continue
            q = (m - 1) // 3 + 1
            qtr_prices[(y, q)] = dp.price  # last wins (sorted oldest-first)
        return qtr_prices

    async def _build_hedge_fund_smart_money(
        self,
        ticker: str,
        daily_prices: Optional[List[DailyPricePointSchema]] = None,
    ) -> SmartMoneyDataSchema:
        """Build hedge fund smart money with incremental quarterly DB store.

        NAMING: "hedge fund" / ``hedge_fund_*`` / the ``hedge_fund_quarters`` table
        here = FMP 13F institutional-ownership data. The UI labels it "Institutions"
        (iOS SmartMoneyTab.hedgeFunds = "Institutions"), not "Hedge Funds".

        Flow is expressed in MILLIONS OF SHARES (not dollars): the net is
        ``numberOf13FsharesChange`` straight from the positions-summary — the
        real net 13F share change, comparable across quarters (dollar values
        would be distorted by price drift). Gross buy/sell are estimated from
        the net + buyer/seller counts (only the net is measured; 13F reports
        positions, not transactions).

        1. Determine the 8 target quarters (last 2 years).
        2. Load already-computed rows from ``hedge_fund_quarters``.
        3. Fetch only missing quarters from FMP (parallel).
        4. Compute buy/sell volumes and persist new rows.
        5. Return 8 quarterly flow-data points.
        """
        target_pairs = self._generate_quarter_keys(8)

        # 1. Load existing rows from DB
        existing = await asyncio.to_thread(
            self._load_existing_quarters, ticker, target_pairs
        )

        # Drop the most recent (unsettled) quarters from the cache-hit set so
        # they are always recomputed from FMP and re-upserted. Without this, a
        # quarter cached early — when 13F filings were incomplete — stays frozen
        # at a bad value (e.g. an inflated price fallback) that dominates the
        # chart's shared y-axis and flattens every other quarter to ~0.
        volatile = set(target_pairs[-_REFRESH_RECENT_QUARTERS:])
        existing = {k: v for k, v in existing.items() if k not in volatile}

        logger.info(
            f"Hedge fund quarters for {ticker}: "
            f"{len(existing)}/{len(target_pairs)} in DB "
            f"(forcing refresh of {sorted(volatile)})"
        )

        # 2. Identify missing quarters
        missing = [p for p in target_pairs if p not in existing]

        # 3. Fetch missing from FMP in parallel
        new_rows: List[Dict[str, Any]] = []
        if missing:
            # Split ratios for the quarters we're (re)computing, so a split
            # quarter's raw 13F change isn't mistaken for buying (see
            # _compute_quarter_flow). One extra FMP call per build.
            split_ratios = HoldersService._quarter_split_ratios(
                await self.fmp.get_stock_splits(ticker), missing
            )
            fmp_results = await asyncio.gather(
                *[
                    self.fmp.get_institutional_ownership_for_quarter(
                        ticker, y, q
                    )
                    for y, q in missing
                ]
            )
            for (y, q), data in zip(missing, fmp_results):
                if data is None:
                    continue

                buy_m, sell_m, net_m, buyers, sellers = (
                    self._compute_quarter_flow(data, split_ratios.get((y, q), 1.0))
                )
                row = {
                    "ticker": ticker,
                    "year": y,
                    "quarter": q,
                    "quarter_date": (data.get("date") or "")[:10],
                    "buy_volume": buy_m,
                    "sell_volume": sell_m,
                    "net_flow": net_m,
                    "buyers_count": buyers,
                    "sellers_count": sellers,
                }
                new_rows.append(row)
                existing[(y, q)] = row

        # 4. Persist new rows in background
        if new_rows:
            asyncio.ensure_future(
                asyncio.to_thread(self._save_quarters, ticker, new_rows)
            )

        # 5. Build flow_data from all quarters (oldest-first)
        flow_data: List[SmartMoneyFlowDataPointSchema] = []
        total_buy = 0.0
        total_sell = 0.0

        for y, q in target_pairs:
            label = self._quarter_label(y, q)
            row = existing.get((y, q))
            if row:
                bv = float(row.get("buy_volume", 0))
                sv = float(row.get("sell_volume", 0))
                # Real signals straight from the positions-summary (no estimate).
                nf_raw = row.get("net_flow")
                nf = float(nf_raw) if nf_raw is not None else round(bv - sv, 2)
                bc_raw = row.get("buyers_count")
                sc_raw = row.get("sellers_count")
                bc = int(bc_raw) if bc_raw is not None else None
                sc = int(sc_raw) if sc_raw is not None else None
            else:
                bv, sv, nf, bc, sc = 0.0, 0.0, 0.0, None, None
            total_buy += bv
            total_sell += sv
            flow_data.append(SmartMoneyFlowDataPointSchema(
                month=label,
                buy_volume=round(bv, 2),
                sell_volume=round(sv, 2),
                has_activity=(bv > 0 or sv > 0),
                net_flow=round(nf, 2),
                buyers_count=bc,
                sellers_count=sc,
            ))

        net = round(total_buy - total_sell, 2)
        summary = SmartMoneyFlowSummarySchema(
            total_net_flow=net,
            total_buy=round(total_buy, 2),
            total_sell=round(total_sell, 2),
            is_positive=net >= 0,
            period_description="2-Year",
        )

        # 6. Build quarterly price data from daily prices
        price_data = self._build_quarterly_price_data(daily_prices, target_pairs)

        return SmartMoneyDataSchema(
            tab="Institutions",
            price_data=price_data,
            daily_prices=daily_prices or [],
            flow_data=flow_data,
            summary=summary,
        )

    @staticmethod
    def _build_quarterly_price_data(
        daily_prices: Optional[List[DailyPricePointSchema]],
        quarter_pairs: List[Tuple[int, int]],
    ) -> List[StockPriceDataPointSchema]:
        """Pick the last closing price of each quarter for the price overlay."""
        if not daily_prices:
            return []

        # Index daily prices by (year, quarter)
        qtr_prices: Dict[Tuple[int, int], float] = {}
        for dp in daily_prices:
            try:
                m = int(dp.date[5:7])
                y = int(dp.date[:4])
            except (IndexError, ValueError):
                continue
            q = (m - 1) // 3 + 1
            qtr_prices[(y, q)] = dp.price  # last wins (sorted oldest-first)

        result: List[StockPriceDataPointSchema] = []
        last_known = 0.0
        for y, q in quarter_pairs:
            label = f"Q{q}\n'{y % 100:02d}"
            price = qtr_prices.get((y, q), 0.0)
            if price > 0:
                last_known = price
            else:
                price = last_known
            result.append(StockPriceDataPointSchema(month=label, price=round(price, 2)))
        return result

    # ── Congress Smart Money Tab ──────────────────────────────────

    @staticmethod
    def _dedup_congress_trades(
        primary: List[Dict[str, Any]],
        secondary: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Merge two trade lists, deduplicating by key fields."""
        seen: set = set()
        result: List[Dict[str, Any]] = []
        for trade in primary + secondary:
            # Normalize type to base form: "Sale (Full)" / "Sale (Partial)" → "sale"
            # FMP returns each trade twice with different type variants.
            raw_type = (trade.get("type") or "").lower()
            base_type = raw_type.split("(")[0].strip()
            key = (
                trade.get("transactionDate", ""),
                (trade.get("firstName") or trade.get("first_name", "")).lower(),
                (trade.get("lastName") or trade.get("last_name", "")).lower(),
                base_type,
                trade.get("amount", ""),
            )
            if key not in seen:
                seen.add(key)
                result.append(trade)
        return result

    @staticmethod
    def _parse_congress_amount(amount_str: str) -> float:
        """Congressional amount range → midpoint in MILLIONS.

        Thin adapter over the shared dollar-returning helper so this service
        and ``whale_service`` never disagree on the same range.
        """
        return parse_congress_amount_dollars(amount_str) / 1_000_000

    @staticmethod
    def _parse_congress_amount_max(amount_str: str) -> float:
        """Parse FMP congressional amount range to MAX value in millions.

        Used for sorting — users care about the maximum potential exposure.
        """
        if not amount_str:
            return 0.0

        clean = amount_str.replace("$", "").replace(",", "").strip()

        if " - " in clean:
            parts = clean.split(" - ")
            try:
                high = float(parts[1].strip())
                return high / 1_000_000
            except (ValueError, IndexError):
                pass

        if clean.lower().startswith("over "):
            try:
                base = float(clean[5:].strip())
                return base / 1_000_000
            except ValueError:
                pass

        try:
            return float(clean) / 1_000_000
        except ValueError:
            return 0.0

    def _build_congress_smart_money(
        self,
        senate_trades: List[Dict[str, Any]],
        house_trades: List[Dict[str, Any]],
        monthly_prices: Dict[str, float],
        daily_prices: Optional[List[DailyPricePointSchema]] = None,
    ) -> SmartMoneyDataSchema:
        """Build congress smart money from senate + house latest trades."""
        month_keys = self._generate_month_keys(12)
        monthly_flows: Dict[str, Dict[str, float]] = {
            m: {"buy": 0.0, "sell": 0.0} for m in month_keys
        }

        all_trades = senate_trades + house_trades
        for trade in all_trades:
            tx_date = (trade.get("transactionDate") or "")[:10]
            tx_type = (trade.get("type") or "").lower()
            amount_str = trade.get("amount", "")
            amount_millions = self._parse_congress_amount(amount_str)

            if not tx_date or amount_millions <= 0:
                continue

            try:
                m_key = f"{tx_date[5:7]}/{tx_date[:4]}"
            except (IndexError, ValueError):
                continue

            if m_key not in monthly_flows:
                continue

            if "purchase" in tx_type or "buy" in tx_type or "exchange" in tx_type:
                monthly_flows[m_key]["buy"] += amount_millions
            elif "sale" in tx_type or "sell" in tx_type:
                monthly_flows[m_key]["sell"] += amount_millions

        flow_data = [
            SmartMoneyFlowDataPointSchema(
                month=m,
                buy_volume=round(monthly_flows[m]["buy"], 2),
                sell_volume=round(monthly_flows[m]["sell"], 2),
                has_activity=(monthly_flows[m]["buy"] > 0 or monthly_flows[m]["sell"] > 0),
            )
            for m in month_keys
        ]

        # Window the daily price line to the SAME 12-month span as the bars. The
        # raw daily series spans ~2 years (sized for the hedge-fund chart, which
        # keeps the full series); stretched over 12-month bars it would sit each
        # bar under the wrong date — misreading "did Congress buy into strength or
        # weakness?". The precise left edge is the first day of the OLDEST of the
        # 12 month_keys. Monthly price_data is already windowed via
        # _build_price_data(month_keys), so it's left as-is. (Mirrors the insider
        # fix in _build_insider_smart_money.)
        o_month, o_year = month_keys[0].split("/")  # "MM/YYYY"
        cutoff_str = f"{o_year}-{o_month}-01"
        windowed_daily = [
            dp for dp in (daily_prices or []) if dp.date >= cutoff_str
        ]

        return SmartMoneyDataSchema(
            tab="Congress",
            price_data=self._build_price_data(monthly_prices, month_keys),
            daily_prices=windowed_daily,
            flow_data=flow_data,
            summary=self._build_summary(flow_data),
        )


# ── Singleton accessor ────────────────────────────────────────────

_service: Optional[HoldersService] = None


def get_holders_service() -> HoldersService:
    global _service
    if _service is None:
        _service = HoldersService()
    return _service
