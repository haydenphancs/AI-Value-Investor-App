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

        (
            shares_float,
            quote_data,
            institutional_holders,
            inst_ownership_summary,
            insider_trading,
            insider_roster,
            historical_prices,
            senate_latest,
            house_latest,
        ) = await asyncio.gather(
            self.fmp.get_shares_float(ticker),
            self.fmp.get_stock_price_quote(ticker),
            self.fmp.get_institutional_holder(ticker, limit=20),
            self.fmp.get_institutional_ownership_summary(ticker),
            self.fmp.get_insider_trading(ticker, limit=100),
            self.fmp.get_insider_roster(ticker),
            self.fmp.get_historical_prices(ticker, from_date=from_date),
            self.fmp.get_senate_latest(limit=500),
            self.fmp.get_house_latest(limit=500),
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
        insider_trading = _unwrap(insider_trading, "Insider trading", [])
        insider_roster = _unwrap(insider_roster, "Insider roster", [])
        historical_prices = _unwrap(historical_prices, "Historical prices", [])
        senate_latest = _unwrap(senate_latest, "Senate latest", [])
        house_latest = _unwrap(house_latest, "House latest", [])

        current_price = _safe_float(quote_data, "price", 0.0)
        company_profile = shares_float

        # Extract monthly price data from historical prices
        monthly_prices = self._extract_monthly_prices(historical_prices)
        daily_prices = self._extract_daily_prices(historical_prices)

        # Filter congressional trades for this ticker
        senate_for_ticker = [s for s in senate_latest if s.get("symbol", "").upper() == ticker]
        house_for_ticker = [h for h in house_latest if h.get("symbol", "").upper() == ticker]

        # Build each section
        breakdown = self._build_shareholder_breakdown(
            company_profile, institutional_holders, insider_roster, current_price,
            inst_ownership_summary,
        )
        recent = self._build_recent_activities(
            institutional_holders, insider_trading, insider_roster, current_price
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
        """Convert institutional holder analytics into recent activity entries."""
        result = []
        for h in holders[:15]:
            # Analytics endpoint fields
            change_in_value = _safe_float(h, "changeInMarketValue", 0.0)
            change_pct = _safe_float(h, "changeInSharesNumberPercentage",
                            _safe_float(h, "changeInSharesPercentage", 0.0))
            total_value = _safe_float(h, "marketValue",
                            _safe_float(h, "value", 0.0))

            if change_in_value == 0.0:
                continue

            change_value_millions = change_in_value / 1_000_000

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
        month_keys = self._generate_month_keys(12)
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
                informative_count += 1
            elif acq_disp == "D":
                monthly_flows[m_key]["sell"] += value_millions
                informative_count += 1

        logger.info(
            f"Insider smart money: {informative_count} informative trades, "
            f"{skipped_count} uninformative skipped"
        )

        flow_data = [
            SmartMoneyFlowDataPointSchema(
                month=m,
                buy_volume=round(monthly_flows[m]["buy"], 2),
                sell_volume=round(monthly_flows[m]["sell"], 2),
                has_activity=(monthly_flows[m]["buy"] > 0 or monthly_flows[m]["sell"] > 0),
            )
            for m in month_keys
        ]

        return SmartMoneyDataSchema(
            tab="Insider",
            price_data=self._build_price_data(monthly_prices, month_keys),
            daily_prices=daily_prices or [],
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
                # 1) All values zeroed out
                # 2) Old logic: one side is zero while both buyers & sellers exist
                bv = r.get("buy_volume", 0) or 0
                sv = r.get("sell_volume", 0) or 0
                nf = r.get("net_flow", 0) or 0
                bc = r.get("buyers_count", 0) or 0
                sc = r.get("sellers_count", 0) or 0
                if bv == 0 and sv == 0 and nf == 0:
                    continue
                if nf != 0 and bc > 0 and sc > 0 and (bv == 0 or sv == 0):
                    continue
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

        Uses ``numberOf13FsharesChange * quarter-end price`` to compute
        the actual dollar value of net institutional buying/selling,
        stripping out the stock-price-appreciation component that
        contaminates ``totalInvestedChange``.

        1. Determine the 8 target quarters (last 2 years).
        2. Load already-computed rows from ``hedge_fund_quarters``.
        3. Fetch only missing quarters from FMP (parallel).
        4. Compute buy/sell volumes and persist new rows.
        5. Return 8 quarterly flow-data points.
        """
        target_pairs = self._generate_quarter_keys(8)
        qtr_prices = self._quarter_end_prices(daily_prices)

        # 1. Load existing rows from DB
        existing = await asyncio.to_thread(
            self._load_existing_quarters, ticker, target_pairs
        )
        logger.info(
            f"Hedge fund quarters for {ticker}: "
            f"{len(existing)}/{len(target_pairs)} in DB"
        )

        # 2. Identify missing quarters
        missing = [p for p in target_pairs if p not in existing]

        # 3. Fetch missing from FMP in parallel
        new_rows: List[Dict[str, Any]] = []
        if missing:
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

                buyers = (
                    int(data.get("newPositions") or 0)
                    + int(data.get("increasedPositions") or 0)
                )
                sellers = (
                    int(data.get("closedPositions") or 0)
                    + int(data.get("reducedPositions") or 0)
                )

                # ── Compute REAL net buy/sell ────────────────────────
                # numberOf13FsharesChange = net shares added/removed
                # Multiply by quarter-end price → dollar value of
                # actual institutional trading, free of price drift.
                shares_change = float(data.get("numberOf13FsharesChange") or 0)
                price = qtr_prices.get((y, q), 0.0)
                if price <= 0:
                    # Fallback: use totalInvested / shares for avg price
                    total_inv = float(data.get("totalInvested") or 0)
                    total_shares = float(data.get("numberOf13Fshares") or 1)
                    price = total_inv / total_shares if total_shares > 0 else 0

                net_millions = (shares_change * price) / 1_000_000
                buy_vol, sell_vol = self._estimate_buy_sell(
                    net_millions, buyers, sellers
                )
                row = {
                    "ticker": ticker,
                    "year": y,
                    "quarter": q,
                    "quarter_date": (data.get("date") or "")[:10],
                    "buy_volume": buy_vol,
                    "sell_volume": sell_vol,
                    "net_flow": round(net_millions, 2),
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
            else:
                bv, sv = 0.0, 0.0
            total_buy += bv
            total_sell += sv
            flow_data.append(SmartMoneyFlowDataPointSchema(
                month=label,
                buy_volume=round(bv, 2),
                sell_volume=round(sv, 2),
                has_activity=(bv > 0 or sv > 0),
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
            tab="Hedge Funds",
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

            if "purchase" in tx_type or "buy" in tx_type:
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

        return SmartMoneyDataSchema(
            tab="Congress",
            price_data=self._build_price_data(monthly_prices, month_keys),
            daily_prices=daily_prices or [],
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
