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
from app.schemas.holders import (
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


def _classify_insider_transaction(tx_type: str) -> str:
    """
    Classify FMP transactionType into informative/uninformative categories.

    Simple heuristic:
      - P-Purchase → Informative Buy
      - S-Sale     → Informative Sell
      - A-*/M-*/G-*→ Uninformative Buy (awards, exercises, gifts)
      - F-*/D-*    → Uninformative Sell (tax withholding, disposition)
    """
    tx = (tx_type or "").strip().upper()
    if tx.startswith("P"):
        return "Informative Buy"
    elif tx.startswith("S"):
        return "Informative Sell"
    elif tx.startswith(("A", "M", "G")):
        return "Uninformative Buy"
    elif tx.startswith(("F", "D")):
        return "Uninformative Sell"
    # Default: if positive value treat as buy, else sell
    return "Uninformative Sell"


_SUPABASE_CACHE_TTL_HOURS = 24


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
            loop.run_in_executor(
                None,
                self._upsert_supabase_cache_safe,
                ticker,
                result,
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
                return HoldersResponse(**response_json)
            return None
        except Exception as e:
            logger.warning(f"Holders Supabase cache check failed for {ticker}: {e}")
            return None

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
        from_date = (now - timedelta(days=400)).strftime("%Y-%m-%d")

        (
            shares_float,
            quote_data,
            institutional_holders,
            insider_trading,
            insider_roster,
            historical_prices,
            senate_latest,
            house_latest,
        ) = await asyncio.gather(
            self.fmp.get_shares_float(ticker),
            self.fmp.get_stock_price_quote(ticker),
            self.fmp.get_institutional_holder(ticker, limit=20),
            self.fmp.get_insider_trading(ticker, limit=100),
            self.fmp.get_insider_roster(ticker),
            self.fmp.get_historical_prices(ticker, from_date=from_date),
            self.fmp.get_senate_latest(limit=100),
            self.fmp.get_house_latest(limit=100),
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
        insider_trading = _unwrap(insider_trading, "Insider trading", [])
        insider_roster = _unwrap(insider_roster, "Insider roster", [])
        historical_prices = _unwrap(historical_prices, "Historical prices", [])
        senate_latest = _unwrap(senate_latest, "Senate latest", [])
        house_latest = _unwrap(house_latest, "House latest", [])

        current_price = _safe_float(quote_data, "price", 0.0)
        company_profile = shares_float

        # Extract monthly price data from historical prices
        monthly_prices = self._extract_monthly_prices(historical_prices)

        # Filter congressional trades for this ticker
        senate_for_ticker = [s for s in senate_latest if s.get("symbol", "").upper() == ticker]
        house_for_ticker = [h for h in house_latest if h.get("symbol", "").upper() == ticker]

        # Whale data from Supabase (for hedge funds tab)
        whale_trades = await asyncio.to_thread(self._get_whale_trades, ticker)

        # Build each section
        breakdown = self._build_shareholder_breakdown(
            company_profile, institutional_holders, insider_roster, current_price
        )
        recent = self._build_recent_activities(
            institutional_holders, insider_trading, insider_roster, current_price
        )

        # Build live smart money data
        insider_sm = self._build_insider_smart_money(insider_trading, monthly_prices)
        hedge_sm = self._build_hedge_fund_smart_money(whale_trades, monthly_prices)
        congress_sm = self._build_congress_smart_money(
            senate_for_ticker, house_for_ticker, monthly_prices
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
    ) -> ShareholderBreakdownSchema:
        # ── Derive ownership percentages ──────────────────────────
        # Primary source: shares-float endpoint (freeFloat %)
        free_float = _safe_float(profile, "freeFloat", 0.0)

        # If freeFloat is available, insiders = 100 - freeFloat
        if free_float > 0:
            insiders_pct = max(0.0, 100.0 - free_float)
        else:
            # Fallback to insidersPercentage if profile has it
            insiders_pct = _safe_float(profile, "insidersPercentage", 0.0)
            if 0 < insiders_pct < 1.0:
                insiders_pct *= 100.0

        # Institutional % — from holder data if premium endpoints available
        if inst_holders:
            inst_pct_from_holders = sum(
                _safe_float(h, "percentOfSharesHeld", 0.0)
                for h in inst_holders
            )
            # FMP may return as decimal (0.08) or whole number (8.0)
            if inst_pct_from_holders < 1.0 and inst_pct_from_holders > 0:
                inst_pct_from_holders *= 100.0
            institutions_pct = inst_pct_from_holders
        else:
            # Fallback: use profile data or estimate from float
            profile_inst_pct = _safe_float(profile, "institutionsPercentage", 0.0)
            if 0 < profile_inst_pct < 1.0:
                profile_inst_pct *= 100.0

            if profile_inst_pct > 0:
                institutions_pct = profile_inst_pct
            elif free_float > 50:
                # For large-cap stocks, ~60-80% of float is typically institutional
                # Conservative estimate: use 60% of float as institutional
                institutions_pct = round(free_float * 0.6, 1)
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
        top_10_insiders = self._build_top_insiders(
            insider_roster, current_price
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
        result = []
        for h in holders:
            pct = _safe_float(h, "percentOfSharesHeld", 0.0)
            if 0 < pct < 1.0:
                pct *= 100.0
            change = _safe_float(h, "changeInSharesPercentage", 0.0)

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
        # Sort by value descending
        sorted_holders = sorted(
            holders,
            key=lambda h: _safe_float(h, "value", 0.0),
            reverse=True,
        )[:10]

        result = []
        for rank, h in enumerate(sorted_holders, 1):
            value = _safe_float(h, "value", 0.0)
            pct = _safe_float(h, "percentOfSharesHeld", 0.0)
            if 0 < pct < 1.0:
                pct *= 100.0

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
    ) -> List[TopInsiderSchema]:
        if not roster:
            return []

        # Build insiders with their share value
        insiders = []
        for r in roster:
            shares = _safe_float(r, "numberOfShares", 0.0)
            value_millions = (shares * current_price) / 1_000_000 if current_price > 0 else 0.0
            ownership_pct = _safe_float(r, "ownershipPercent", 0.0)
            if 0 < ownership_pct < 1.0:
                ownership_pct *= 100.0

            insiders.append({
                "name": r.get("owner", "Unknown"),
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
                percent_ownership=round(ins["percentOwnership"], 2),
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
    ) -> RecentActivitiesSchema:
        # Build institutional activities
        inst_activities = self._build_institutional_activities(inst_holders)
        flow_summary = self._build_institutional_flow_summary(inst_activities)

        # Build insider activities
        insider_acts = self._build_insider_activities(
            insider_trades, insider_roster
        )
        insider_summary = self._build_insider_activity_summary(insider_acts)

        return RecentActivitiesSchema(
            institutional_flow_summary=flow_summary,
            institutional_activities=inst_activities,
            insider_activities=InsiderActivitiesDataSchema(
                summary=insider_summary,
                activities=insider_acts,
            ),
        )

    def _build_institutional_activities(
        self, holders: List[Dict[str, Any]]
    ) -> List[InstitutionalActivitySchema]:
        """Convert institutional holder data into recent activity entries."""
        result = []
        for h in holders[:15]:  # Top 15 for activities
            change_shares = _safe_float(h, "changeInShares", 0.0)
            change_pct = _safe_float(h, "changeInSharesPercentage", 0.0)
            total_value = _safe_float(h, "value", 0.0)
            shares = _safe_float(h, "sharesNumber", 0.0)

            if change_shares == 0.0:
                continue

            # Calculate change in millions (approximate using value/shares ratio)
            price_per_share = total_value / shares if shares > 0 else 0.0
            change_value_millions = (change_shares * price_per_share) / 1_000_000

            # Filing date
            date_str = h.get("filingDate", h.get("dateReported", ""))
            if not date_str:
                date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

            result.append(InstitutionalActivitySchema(
                institution_name=h.get("investorName", h.get("holder", "Unknown")),
                category=self._categorize_institution(
                    h.get("investorName", "")
                ),
                date=date_str[:10],
                change_in_millions=round(change_value_millions, 2),
                change_percent=round(change_pct, 2),
                total_held_in_billions=round(total_value / 1_000_000_000, 1) if total_value > 0 else 0.0,
            ))

        # Sort by absolute change value descending
        result.sort(key=lambda a: abs(a.change_in_millions), reverse=True)
        return result[:10]

    def _build_institutional_flow_summary(
        self, activities: List[InstitutionalActivitySchema]
    ) -> RecentActivitiesFlowSummarySchema:
        """Aggregate institutional activities into a flow summary."""
        inflow = 0.0
        outflow = 0.0

        for act in activities:
            val = act.change_in_millions
            if val >= 0:
                inflow += val
            else:
                outflow += abs(val)

        # Determine period description from activity dates
        now = datetime.now(timezone.utc)
        quarter = (now.month - 1) // 3 + 1
        quarter_start_month = (quarter - 1) * 3 + 1
        month_names = [
            "", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
            "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"
        ]
        period_start = month_names[quarter_start_month]
        period_end = month_names[min(quarter_start_month + 2, 12)]

        return RecentActivitiesFlowSummarySchema(
            period_description=f"{period_start} - {period_end} {now.year}",
            quarter_description=f"Q{quarter}",
            in_flow_in_billions=round(inflow / 1000, 1),
            out_flow_in_billions=round(outflow / 1000, 1),
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
        for tx in trades[:30]:  # Process up to 30 trades
            reporting_name = tx.get("reportingName", tx.get("reportingCik", "Unknown"))
            tx_type_raw = tx.get("transactionType", "")
            classified = _classify_insider_transaction(tx_type_raw)

            # Get shares and price
            shares = abs(_safe_float(tx, "securitiesTransacted", 0.0))
            price = _safe_float(tx, "price", 0.0)
            value_millions = (shares * price) / 1_000_000 if price > 0 else 0.0

            # Sign convention: negative for sells
            if "Sell" in classified:
                value_millions = -value_millions

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
                change_in_millions=round(value_millions, 2),
                transaction_type=classified,
                price_at_transaction=round(price, 2),
            ))

        # Sort by date descending
        result.sort(key=lambda a: a.date, reverse=True)
        return result[:10]

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
            informative_buys_in_millions=round(informative_buys, 2),
            informative_sells_in_millions=round(informative_sells, 2),
            num_buyers=len(buyer_names),
            num_sellers=len(seller_names),
        )

    # ── Smart Money Helpers ──────────────────────────────────────────

    @staticmethod
    def _generate_month_keys(count: int = 12) -> List[str]:
        """Generate last N month keys like '03/2026'."""
        now = datetime.now(timezone.utc)
        months = []
        for i in range(count - 1, -1, -1):
            dt = now - timedelta(days=30 * i)
            months.append(dt.strftime("%m/%Y"))
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

    def _build_price_data(
        self, monthly_prices: Dict[str, float], month_keys: List[str]
    ) -> List[StockPriceDataPointSchema]:
        """Build StockPriceDataPoint list from monthly price map."""
        return [
            StockPriceDataPointSchema(
                month=m, price=round(monthly_prices.get(m, 0.0), 2)
            )
            for m in month_keys
        ]

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
            is_positive=net >= 0,
            period_description="12-Month",
        )

    # ── Insider Smart Money Tab ───────────────────────────────────

    def _build_insider_smart_money(
        self,
        insider_trades: List[Dict[str, Any]],
        monthly_prices: Dict[str, float],
    ) -> SmartMoneyDataSchema:
        """Build insider smart money from individual trade data.

        Uses insider-trading/search results which have per-transaction
        shares and price. Falls back to quarterly stats if needed.
        """
        month_keys = self._generate_month_keys(12)
        monthly_flows: Dict[str, Dict[str, float]] = {
            m: {"buy": 0.0, "sell": 0.0} for m in month_keys
        }

        for tx in insider_trades:
            # Use transactionDate or filingDate
            date_str = (tx.get("transactionDate") or tx.get("filingDate") or "")[:10]
            if not date_str:
                continue

            try:
                m_key = f"{date_str[5:7]}/{date_str[:4]}"
            except (IndexError, ValueError):
                continue

            if m_key not in monthly_flows:
                continue

            shares = abs(_safe_float(tx, "securitiesTransacted", 0.0))
            price = _safe_float(tx, "price", 0.0)

            # If price is 0, use the monthly stock price as estimate
            if price <= 0:
                price = monthly_prices.get(m_key, 0.0)

            value_millions = (shares * price) / 1_000_000 if price > 0 else 0.0

            # Determine buy vs sell from acquisitionOrDisposition field
            acq_disp = (tx.get("acquisitionOrDisposition") or "").upper()
            if acq_disp == "A":
                monthly_flows[m_key]["buy"] += value_millions
            elif acq_disp == "D":
                monthly_flows[m_key]["sell"] += value_millions

        flow_data = [
            SmartMoneyFlowDataPointSchema(
                month=m,
                buy_volume=round(monthly_flows[m]["buy"], 2),
                sell_volume=round(monthly_flows[m]["sell"], 2),
            )
            for m in month_keys
        ]

        return SmartMoneyDataSchema(
            tab="Insider",
            price_data=self._build_price_data(monthly_prices, month_keys),
            flow_data=flow_data,
            summary=self._build_summary(flow_data),
        )

    # ── Hedge Fund Smart Money Tab ────────────────────────────────

    def _get_whale_trades(self, ticker: str) -> List[Dict[str, Any]]:
        """Fetch whale trades for a ticker from Supabase."""
        try:
            result = (
                self.supabase.table("whale_trades")
                .select("action, amount, date, whale_id")
                .eq("ticker", ticker)
                .order("date", desc=True)
                .limit(50)
                .execute()
            )
            return result.data or []
        except Exception as e:
            logger.warning(f"Whale trades fetch failed for {ticker}: {e}")
            return []

    def _build_hedge_fund_smart_money(
        self,
        whale_trades: List[Dict[str, Any]],
        monthly_prices: Dict[str, float],
    ) -> SmartMoneyDataSchema:
        """Build hedge fund smart money from whale trades (Supabase)."""
        month_keys = self._generate_month_keys(12)
        monthly_flows: Dict[str, Dict[str, float]] = {
            m: {"buy": 0.0, "sell": 0.0} for m in month_keys
        }

        for trade in whale_trades:
            date_str = (trade.get("date") or "")[:10]
            amount = abs(_safe_float(trade, "amount", 0.0)) / 1_000_000
            action = (trade.get("action") or "").upper()

            if not date_str or amount <= 0:
                continue

            try:
                m_key = f"{date_str[5:7]}/{date_str[:4]}"
            except (IndexError, ValueError):
                continue

            if m_key not in monthly_flows:
                continue

            if "BOUGHT" in action or "ADDED" in action or "INCREASED" in action:
                monthly_flows[m_key]["buy"] += amount
            elif "SOLD" in action or "REDUCED" in action or "DECREASED" in action:
                monthly_flows[m_key]["sell"] += amount

        flow_data = [
            SmartMoneyFlowDataPointSchema(
                month=m,
                buy_volume=round(monthly_flows[m]["buy"], 2),
                sell_volume=round(monthly_flows[m]["sell"], 2),
            )
            for m in month_keys
        ]

        return SmartMoneyDataSchema(
            tab="Hedge Funds",
            price_data=self._build_price_data(monthly_prices, month_keys),
            flow_data=flow_data,
            summary=self._build_summary(flow_data),
        )

    # ── Congress Smart Money Tab ──────────────────────────────────

    @staticmethod
    def _parse_congress_amount(amount_str: str) -> float:
        """Parse FMP congressional amount range to midpoint in millions.

        FMP uses ranges like '$1,001 - $15,000', '$15,001 - $50,000', etc.
        """
        if not amount_str:
            return 0.0

        # Remove $ signs and commas
        clean = amount_str.replace("$", "").replace(",", "").strip()

        # Try to parse as range (e.g., "1001 - 15000")
        if " - " in clean:
            parts = clean.split(" - ")
            try:
                low = float(parts[0].strip())
                high = float(parts[1].strip())
                return (low + high) / 2 / 1_000_000  # Convert to millions
            except (ValueError, IndexError):
                pass

        # Try as single number
        try:
            return float(clean) / 1_000_000
        except ValueError:
            return 0.0

    def _build_congress_smart_money(
        self,
        senate_trades: List[Dict[str, Any]],
        house_trades: List[Dict[str, Any]],
        monthly_prices: Dict[str, float],
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

            if "purchase" in tx_type or "buy" in tx_type:
                monthly_flows[m_key]["buy"] += amount_millions
            elif "sale" in tx_type or "sell" in tx_type:
                monthly_flows[m_key]["sell"] += amount_millions

        flow_data = [
            SmartMoneyFlowDataPointSchema(
                month=m,
                buy_volume=round(monthly_flows[m]["buy"], 2),
                sell_volume=round(monthly_flows[m]["sell"], 2),
            )
            for m in month_keys
        ]

        return SmartMoneyDataSchema(
            tab="Congress",
            price_data=self._build_price_data(monthly_prices, month_keys),
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
