"""
ETF Detail Service — aggregates FMP data, computes derived stats,
and generates AI-powered snapshot analysis via Gemini.

Serves the ETFDetailView screen on iOS.
"""

import asyncio
import json
import logging
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client, FMPClient
from app.integrations.gemini import get_gemini_client
from app.schemas.etf import (
    BenchmarkSummaryResponse,
    ETFAssetAllocationResponse,
    ETFConcentrationResponse,
    ETFDetailResponse,
    ETFDividendHistoryResponse,
    ETFDividendPaymentResponse,
    ETFHoldingsRiskResponse,
    ETFIdentityRatingResponse,
    ETFNetYieldResponse,
    ETFNewsArticleResponse,
    ETFProfileResponse,
    ETFSectorWeightResponse,
    ETFStrategyResponse,
    ETFTopHoldingResponse,
    KeyStatisticItem,
    KeyStatisticsGroupResponse,
    MarketStatusResponse,
    PerformancePeriodResponse,
    RelatedTickerResponse,
)

logger = logging.getLogger(__name__)

# ── Simple in-memory cache ───────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 300  # 5 minutes for market data
_AI_CACHE_TTL_SECONDS = 3600  # 1 hour for AI-generated snapshots
_SP_HIST_CACHE_TTL = 3600  # 1 hour for S&P 500 historical (shared across ETFs)
# Hard cap on live entries — see stock_overview_service for rationale. Eviction
# is least-recently-written; a miss just re-fetches (no correctness impact).
_CACHE_MAX_ENTRIES = 1024


def _cache_get(key: str, ttl: float = _CACHE_TTL_SECONDS) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any):
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


# ── Related ETF mappings ─────────────────────────────────────────

_RELATED_ETFS: Dict[str, List[str]] = {
    "SPY": ["VOO", "IVV", "QQQ", "DIA", "IWM", "VTI"],
    "VOO": ["SPY", "IVV", "VTI", "QQQ", "SCHX", "SPLG"],
    "IVV": ["SPY", "VOO", "VTI", "QQQ", "SCHX", "SPLG"],
    "QQQ": ["QQQM", "SPY", "VGT", "XLK", "IWM", "ARKK"],
    "DIA": ["SPY", "VOO", "IWM", "VTI", "SCHD", "VYM"],
    "IWM": ["IJR", "VB", "SCHA", "SPY", "QQQ", "DIA"],
    "VTI": ["ITOT", "SPTM", "SPY", "VOO", "SCHB", "IWV"],
    "ARKK": ["QQQ", "ARKW", "ARKG", "VGT", "XLK", "QQQM"],
    "SCHD": ["VYM", "DVY", "HDV", "DGRO", "VIG", "SDY"],
    "VYM": ["SCHD", "DVY", "HDV", "DGRO", "VIG", "SDY"],
    "XLK": ["VGT", "QQQ", "IGV", "FTEC", "IYW", "SMH"],
    "XLF": ["VFH", "IYF", "KBE", "KRE", "FNCL", "IYG"],
    "XLE": ["VDE", "IYE", "FENY", "OIH", "XOP", "AMLP"],
    "GLD": ["IAU", "SLV", "GLDM", "SGOL", "AAAU", "BAR"],
    "TLT": ["IEF", "SHY", "BND", "AGG", "VGLT", "EDV"],
    "BND": ["AGG", "BNDX", "TLT", "IEF", "SCHZ", "FBND"],
}

_DEFAULT_RELATED = ["SPY", "QQQ", "DIA", "IWM", "VTI", "SCHD"]


# ── Static ETF reference data (fallback when FMP premium endpoints are unavailable) ──

_ETF_REFERENCE: Dict[str, Dict[str, Any]] = {
    "SPY":  {"expense_ratio": 0.0945, "holdings": 503, "turnover": 2.0, "index": "S&P 500"},
    "VOO":  {"expense_ratio": 0.03,   "holdings": 504, "turnover": 2.4, "index": "S&P 500"},
    "IVV":  {"expense_ratio": 0.03,   "holdings": 503, "turnover": 5.0, "index": "S&P 500"},
    "QQQ":  {"expense_ratio": 0.20,   "holdings": 101, "turnover": 8.4, "index": "Nasdaq-100"},
    "QQQM": {"expense_ratio": 0.15,   "holdings": 101, "turnover": 8.4, "index": "Nasdaq-100"},
    "DIA":  {"expense_ratio": 0.16,   "holdings": 30,  "turnover": 14.0, "index": "Dow Jones Industrial"},
    "IWM":  {"expense_ratio": 0.19,   "holdings": 1974, "turnover": 18.0, "index": "Russell 2000"},
    "VTI":  {"expense_ratio": 0.03,   "holdings": 3636, "turnover": 2.2, "index": "CRSP US Total Market"},
    "ARKK": {"expense_ratio": 0.75,   "holdings": 35,  "turnover": 60.0, "index": "Active (No Index)"},
    "SCHD": {"expense_ratio": 0.06,   "holdings": 104, "turnover": 14.0, "index": "Dow Jones US Dividend 100"},
    "VYM":  {"expense_ratio": 0.06,   "holdings": 462, "turnover": 8.0, "index": "FTSE High Dividend Yield"},
    "XLK":  {"expense_ratio": 0.09,   "holdings": 69,  "turnover": 5.0, "index": "Technology Select Sector"},
    "XLF":  {"expense_ratio": 0.09,   "holdings": 72,  "turnover": 5.0, "index": "Financial Select Sector"},
    "XLE":  {"expense_ratio": 0.09,   "holdings": 23,  "turnover": 5.0, "index": "Energy Select Sector"},
    "GLD":  {"expense_ratio": 0.40,   "holdings": 1,   "turnover": 0.0, "index": "Gold Spot Price"},
    "TLT":  {"expense_ratio": 0.15,   "holdings": 36,  "turnover": 15.0, "index": "ICE US Treasury 20+ Year"},
    "BND":  {"expense_ratio": 0.03,   "holdings": 17400, "turnover": 40.0, "index": "Bloomberg US Aggregate"},
    "AGG":  {"expense_ratio": 0.03,   "holdings": 12200, "turnover": 40.0, "index": "Bloomberg US Aggregate"},
    "VGT":  {"expense_ratio": 0.10,   "holdings": 316, "turnover": 3.0, "index": "MSCI US IMI Info Tech"},
    "SPLG": {"expense_ratio": 0.02,   "holdings": 503, "turnover": 2.0, "index": "S&P 500"},
    "ITOT": {"expense_ratio": 0.03,   "holdings": 3496, "turnover": 4.0, "index": "S&P Total Market"},
    "IJR":  {"expense_ratio": 0.06,   "holdings": 602, "turnover": 16.0, "index": "S&P SmallCap 600"},
    "VB":   {"expense_ratio": 0.05,   "holdings": 1381, "turnover": 11.0, "index": "CRSP US Small Cap"},
    "SMH":  {"expense_ratio": 0.35,   "holdings": 26,  "turnover": 17.0, "index": "MVIS US Listed Semiconductor"},
    "DGRO": {"expense_ratio": 0.08,   "holdings": 407, "turnover": 14.0, "index": "Morningstar US Dividend Growth"},
    "VIG":  {"expense_ratio": 0.06,   "holdings": 315, "turnover": 10.0, "index": "S&P US Dividend Growers"},
}


# ── Helpers ──────────────────────────────────────────────────────


def _finite_num(v: Any, default: float = 0.0) -> float:
    """Coerce to a finite float, or ``default``.

    FMP weight / price / change fields are forwarded straight into REQUIRED
    response floats (``weight``/``price``/``change_percent``) that have no Pydantic
    finiteness guard. ``float("NaN")`` / ``float("inf")`` SUCCEED (the string
    ``try`` only catches ValueError/TypeError), so a malformed holdings row could
    put a non-finite into the response — Starlette renders with ``allow_nan=False``
    and would raise, 500-ing the ENTIRE ETF detail (blanking valid price/chart/
    profile). Reject non-finite here, mirroring ``chart_helper._finite_or_none``.
    """
    try:
        f = float(v)
    except (ValueError, TypeError):
        return default
    return f if math.isfinite(f) else default


def _fmt(value: Optional[float], decimals: int = 2, prefix: str = "$") -> str:
    """Format a number with commas and N decimal places."""
    if value is None:
        return "—"
    if abs(value) >= 1_000_000_000_000:
        return f"{prefix}{value / 1_000_000_000_000:.1f}T"
    if abs(value) >= 1_000_000_000:
        return f"{prefix}{value / 1_000_000_000:.1f}B"
    if abs(value) >= 1_000_000:
        return f"{prefix}{value / 1_000_000:.1f}M"
    return f"{value:,.{decimals}f}"


def _fmt_dollar(value: Optional[float], decimals: int = 2) -> str:
    """Format as dollar amount."""
    if value is None:
        return "—"
    return f"${value:,.{decimals}f}"


def _pct(value: Optional[float]) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}%"


def _compute_return(prices: List[Dict], days_back: int) -> Optional[float]:
    """Compute % return over the last N trading days."""
    if not prices or len(prices) < 2:
        return None
    # Not enough history to cover the requested window: return None so the caller
    # OMITS this period rather than mislabeling a shorter (e.g. since-inception)
    # return under a "3Y"/"5Y"/"10Y" label (a young ETF would otherwise show its
    # full-history return identically for 3Y/5Y/10Y). Genuine since-inception CAGR
    # uses _build_benchmark_summary, not this fallback.
    if len(prices) <= days_back:
        return None
    start = prices[-(days_back + 1)].get("close") or prices[-(days_back + 1)].get("adjClose")
    end = prices[-1].get("close") or prices[-1].get("adjClose")

    if not start or not end or start == 0:
        return None
    return ((end - start) / start) * 100


def _compute_ytd_return(prices: List[Dict]) -> Optional[float]:
    if not prices or len(prices) < 2:
        return None
    current_year = datetime.now(tz=timezone.utc).year
    for p in prices:
        date_str = p.get("date") or ""
        if date_str.startswith(str(current_year)):
            start_price = p.get("close") or p.get("adjClose")
            end_price = prices[-1].get("close") or prices[-1].get("adjClose")
            if start_price and end_price and start_price > 0:
                return ((end_price - start_price) / start_price) * 100
            break
    return None


def _get_market_status() -> MarketStatusResponse:
    now = datetime.now(tz=timezone(timedelta(hours=-5)))  # EST
    hour = now.hour
    minute = now.minute
    weekday = now.weekday()

    if weekday >= 5:
        status = "closed"
    elif hour < 4:
        status = "closed"
    elif hour < 9 or (hour == 9 and minute < 30):
        status = "pre_market"
    elif hour < 16:
        status = "open"
    elif hour < 20:
        status = "after_hours"
    else:
        status = "closed"

    if status == "closed":
        return MarketStatusResponse(
            status="closed",
            date=now.strftime("%Y-%m-%dT16:00:00-05:00"),
            time="4:00 PM",
            timezone="EST",
        )
    return MarketStatusResponse(status=status)


def _format_date_readable(date_str: str) -> str:
    """Convert YYYY-MM-DD to human-readable format like 'Dec 20, 2025'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return date_str or "—"


# ── Main service ─────────────────────────────────────────────────


_ETF_DB_TTL_HOURS = 24  # 24 hours in Supabase for ETF fundamentals


class ETFService:
    """Aggregates FMP data + Gemini AI for the ETF Detail screen."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()
        from app.database import get_supabase
        self.supabase = get_supabase()

    # ── Supabase cache-aside helpers ─────────────────────────────

    def _check_etf_db_cache(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Check Supabase etf_detail_cache (24h TTL)."""
        try:
            row = (
                self.supabase.table("etf_detail_cache")
                .select("response_json, cached_at")
                .eq("symbol", symbol)
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
            if age > timedelta(hours=_ETF_DB_TTL_HOURS):
                logger.info(f"ETF Supabase STALE (age={age}) for {symbol}")
                return None

            data = entry.get("response_json")
            if data and isinstance(data, dict):
                logger.info(f"ETF Supabase HIT for {symbol} (age={age})")
                return data
            return None
        except Exception as e:
            logger.warning(f"ETF Supabase check failed for {symbol}: {e}")
            return None

    def _upsert_etf_db_cache(self, symbol: str, data: Dict[str, Any]) -> None:
        """Upsert ETF detail into Supabase cache."""
        try:
            self.supabase.table("etf_detail_cache").upsert(
                {
                    "symbol": symbol,
                    "response_json": data,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="symbol",
            ).execute()
            logger.info(f"ETF detail cached in Supabase for {symbol}")
        except Exception as e:
            logger.warning(f"ETF Supabase upsert failed for {symbol}: {e}")

    async def get_etf_detail(
        self, symbol: str, chart_range: str = "3M", interval: str = None
    ) -> ETFDetailResponse:
        """
        Fetch and assemble complete ETF detail data.

        Steps:
          1. Parallel FMP fetches (quote, profile, etf-info, holdings, sectors, dividends, history, news)
          2. Compute key statistics and performance periods
          3. Generate AI snapshots via Gemini (identity, strategy, net yield, holdings risk)
          4. Build related ETFs
          5. Assemble and return the response
        """
        symbol = symbol.upper()

        # ── Cache check: in-memory (5 min) then Supabase (24h) ──
        mem_key = f"etf_detail_{symbol}"
        cached = _cache_get(mem_key, _CACHE_TTL_SECONDS)
        if cached is not None:
            logger.info(f"ETF in-memory HIT for {symbol}")
            return cached

        db_data = self._check_etf_db_cache(symbol)
        if db_data is not None:
            try:
                response = ETFDetailResponse(**db_data)
                _cache_set(mem_key, response)
                return response
            except Exception as e:
                logger.warning(f"ETF Supabase data invalid for {symbol}: {e}")

        # ── Step 1: Parallel FMP fetches ──────────────────────────
        today = datetime.now(tz=timezone.utc).date()
        from_date = "1900-01-01"  # Fetch full history — FMP returns from actual inception
        to_date = today.isoformat()

        # Check SPY historical cache (shared across all ETF requests, 1h TTL)
        sp_cache_key = f"spy_hist_full:{to_date}"
        cached_spy = _cache_get(sp_cache_key, _SP_HIST_CACHE_TTL)

        # Build tasks — add SPY fetch only if not cached
        tasks = [
            self.fmp.get_stock_price_quote(symbol),        # 0
            self.fmp.get_company_profile(symbol),           # 1
            self.fmp.get_etf_info(symbol),                  # 2
            self.fmp.get_etf_holders(symbol, limit=20),     # 3
            self.fmp.get_etf_sector_weightings(symbol),     # 4
            self.fmp.get_dividend_history(symbol, limit=20), # 5
            self.fmp.get_historical_prices(symbol, from_date, to_date),  # 6
            self.fmp.get_stock_news(symbol, limit=10),      # 7
        ]
        spy_task_idx = None
        if cached_spy is None:
            spy_task_idx = len(tasks)
            tasks.append(self.fmp.get_historical_prices("SPY", from_date, to_date))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        def _safe(i, default={}):
            r = results[i] if i < len(results) else default
            return default if isinstance(r, Exception) else r

        quote = _safe(0)
        profile = _safe(1)
        etf_info = _safe(2)
        holders = _safe(3, [])
        sector_weights = _safe(4, [])
        dividends = _safe(5, [])
        hist_raw = _safe(6)
        news_raw = _safe(7, [])

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.error(f"ETF FMP call {i} failed for {symbol}: {r}")

        # Parse ETF historical prices (sorted oldest-first)
        historical: List[Dict] = []
        if isinstance(hist_raw, dict):
            historical = hist_raw.get("historical", [])
        elif isinstance(hist_raw, list):
            historical = hist_raw
        historical.sort(key=lambda p: p.get("date") or "")

        # Parse SPY historical prices
        if cached_spy is not None:
            spy_hist = cached_spy
        else:
            spy_raw = _safe(spy_task_idx) if spy_task_idx is not None else {}
            spy_hist_raw = spy_raw.get("historical", []) if isinstance(spy_raw, dict) else (spy_raw if isinstance(spy_raw, list) else [])
            spy_hist = sorted(spy_hist_raw, key=lambda p: p.get("date") or "")
            if spy_hist:
                _cache_set(sp_cache_key, spy_hist)

        # ── Step 2: Extract quote data ────────────────────────────
        price = float(quote.get("price") or 0)
        change = float(quote.get("change") or 0)
        change_pct = float(quote.get("changePercentage") or quote.get("changesPercentage") or 0)
        prev_close = float(quote.get("previousClose") or 0)
        # Safety net: compute from change/previousClose if FMP didn't return percentage
        if not change_pct and change and prev_close > 0:
            change_pct = round((change / prev_close) * 100, 4)
        volume = quote.get("volume") or 0
        avg_volume = (
            quote.get("avgVolume")
            or profile.get("averageVolume")
            or etf_info.get("avgVolume")
            or 0
        )
        year_high = float(quote.get("yearHigh") or 0)
        year_low = float(quote.get("yearLow") or 0)
        price_avg_50 = float(quote.get("priceAvg50") or 0)
        market_cap = quote.get("marketCap") or profile.get("marketCap") or 0
        beta = float(profile.get("beta") or quote.get("beta") or 0)

        # ETF-specific data (etf_info may be empty if FMP plan doesn't include it)
        # Fall back to static reference table for popular ETFs
        ref = _ETF_REFERENCE.get(symbol, {})

        expense_ratio = float(etf_info.get("expenseRatio") or ref.get("expense_ratio") or 0)
        nav = float(etf_info.get("navPrice") or etf_info.get("nav") or price)
        total_assets = float(
            etf_info.get("assetsUnderManagement") or etf_info.get("totalAssets")
            or etf_info.get("aum") or etf_info.get("netAssets") or market_cap or 0
        )
        holdings_count = int(
            etf_info.get("holdingsCount") or etf_info.get("numberOfHoldings")
            or ref.get("holdings") or 0
        )
        etf_company = (
            etf_info.get("etfCompany") or etf_info.get("companyName")
            or profile.get("companyName") or "—"
        )
        asset_class = etf_info.get("assetClass") or "Equity"
        inception_date_raw = (
            etf_info.get("inceptionDate") or profile.get("ipoDate") or ""
        )
        domicile = etf_info.get("domicile") or "United States"
        index_tracked = (
            etf_info.get("indexTracked") or etf_info.get("index")
            or ref.get("index") or "—"
        )
        website = etf_info.get("website") or profile.get("website") or ""
        if website.startswith("https://"):
            website = website[8:]
        elif website.startswith("http://"):
            website = website[7:]
        description = etf_info.get("description") or profile.get("description") or ""
        turnover = float(etf_info.get("turnover") or ref.get("turnover") or 0)

        # Dividend yield: prefer etf_info, then compute from lastDividend / price
        last_div_dollar = float(profile.get("lastDividend") or profile.get("lastDiv") or 0)
        dividend_yield = float(etf_info.get("dividendYield") or quote.get("dividendYield") or 0)
        if not dividend_yield and last_div_dollar > 0 and price > 0:
            dividend_yield = round((last_div_dollar / price) * 100, 2)

        # ── Step 3: Build chart data ──────────────────────────────
        from app.services.chart_helper import fetch_chart_data, resolve_interval
        resolved = resolve_interval(chart_range, interval)
        if resolved != "daily" or chart_range == "ALL":
            chart_data = await fetch_chart_data(self.fmp, symbol, chart_range, interval)
        else:
            chart_data = self._extract_chart_data(historical, chart_range)

        # ── Step 4: Build key statistics ──────────────────────────
        key_statistics, key_statistics_groups = self._build_key_statistics(
            nav=nav,
            total_assets=total_assets,
            expense_ratio=expense_ratio,
            avg_volume=avg_volume,
            dividend_yield=dividend_yield,
            year_high=year_high,
            year_low=year_low,
            beta=beta,
            price_avg_50=price_avg_50,
            holdings_count=holdings_count,
            turnover=turnover,
            inception_date=inception_date_raw,
            asset_class=asset_class,
            domicile=domicile,
            index_tracked=index_tracked,
        )

        # ── Step 5: Build performance periods (vs S&P 500) ─────────
        perf_periods = self._build_performance_periods(historical, spy_hist)

        # ── Step 6: Build holdings & sector data ──────────────────
        top_holdings = self._build_top_holdings(holders)
        top_sectors = self._build_sector_weights(sector_weights)
        concentration = self._build_concentration(top_holdings)

        # ── Step 7: Build dividend data ───────────────────────────
        dividend_payments = self._build_dividend_history(dividends)

        # ── Step 8: Build snapshots (FMP data + Gemini for hook text) ──
        identity_rating = self._build_identity_rating(
            total_assets=total_assets,
            beta=beta,
            expense_ratio=expense_ratio,
            inception_date=inception_date_raw,
            holdings_count=holdings_count,
        )
        strategy = await self._build_strategy(
            symbol=symbol,
            name=etf_company,
            description=description,
            asset_class=asset_class,
            index_tracked=index_tracked,
            holdings_count=holdings_count,
            top_holdings=top_holdings,
            top_sectors=top_sectors,
        )

        # ── Step 9: Build net yield ───────────────────────────────
        fee_per_10k = expense_ratio * 100  # expense_ratio is in %, so 0.0945% → $9.45
        yield_per_10k = dividend_yield * 100  # 1.22% → $122

        # Honest net-yield verdict. Note: expense_ratio == 0 here means the value
        # is UNAVAILABLE (FMP didn't return it and there's no reference entry),
        # NOT a genuinely free fund — never claim "$0 fees" / "charges nothing".
        if expense_ratio <= 0:
            fee_context = "Expense ratio unavailable for this fund."
            net_yield_verdict = "We couldn't confirm this fund's fees."
        elif dividend_yield <= 0:
            fee_context = f"You pay ${fee_per_10k:.2f} per year on a $10,000 investment."
            net_yield_verdict = "This fund doesn't currently pay a dividend — you only pay its fees."
        else:
            fee_context = f"You pay ${fee_per_10k:.2f} per year on a $10,000 investment."
            ratio = dividend_yield / expense_ratio
            if ratio >= 1.05:
                net_yield_verdict = f"This fund pays you {ratio:.1f}x more in dividends than it charges in fees."
            elif ratio >= 0.95:
                net_yield_verdict = "This fund's dividend yield roughly matches its expense ratio."
            else:
                net_yield_verdict = (
                    f"This fund's {expense_ratio:.2f}% fee is higher than its "
                    f"{dividend_yield:.2f}% dividend yield."
                )

        if not dividend_payments:
            last_payment = ETFDividendPaymentResponse(
                dividend_per_share="—",
                ex_dividend_date="—",
                pay_date="—",
            )
        else:
            last_payment = dividend_payments[0]

        pay_frequency = self._infer_pay_frequency(dividends)

        net_yield = ETFNetYieldResponse(
            expense_ratio=expense_ratio,
            fee_context=fee_context,
            dividend_yield=dividend_yield,
            pay_frequency=pay_frequency,
            yield_context=f"You earn ~${yield_per_10k:.0f} per year on a $10,000 investment.",
            verdict=net_yield_verdict,
            last_dividend_payment=last_payment,
            dividend_history=dividend_payments,
        )

        # ── Step 10: Build related ETFs ───────────────────────────
        related_etfs = await self._build_related_etfs(symbol)

        # ── Step 11: Build news ───────────────────────────────────
        news_articles = self._build_news(news_raw if isinstance(news_raw, list) else [])

        # ── Step 12: Build profile ────────────────────────────────
        inception_display = _format_date_readable(inception_date_raw)

        etf_profile = ETFProfileResponse(
            description=description,
            symbol=symbol,
            etf_company=etf_company,
            asset_class=asset_class,
            inception_date=inception_display,
            domicile=domicile,
            index_tracked=index_tracked,
            website=website,
        )

        # ── Step 13: Asset allocation (inferred) ──────────────────
        asset_alloc = self._infer_asset_allocation(
            asset_class=asset_class,
            total_assets=total_assets,
        )

        holdings_risk = ETFHoldingsRiskResponse(
            asset_allocation=asset_alloc,
            top_sectors=top_sectors[:5],
            top_holdings=top_holdings[:10],
            concentration=concentration,
        )

        # ── Step 14: Benchmark summary (proper CAGR) ────────────────
        benchmark = self._build_benchmark_summary(historical, spy_hist, symbol=symbol, index_tracked=index_tracked)

        # ── Assemble response ─────────────────────────────────────
        response = ETFDetailResponse(
            symbol=symbol,
            name=profile.get("companyName") or etf_company,
            current_price=price,
            price_change=change,
            price_change_percent=change_pct,
            market_status=_get_market_status(),
            chart_data=chart_data,
            key_statistics=key_statistics,
            key_statistics_groups=key_statistics_groups,
            performance_periods=perf_periods,
            identity_rating=identity_rating,
            strategy=strategy,
            net_yield=net_yield,
            holdings_risk=holdings_risk,
            etf_profile=etf_profile,
            related_etfs=related_etfs,
            benchmark_summary=benchmark,
            news_articles=news_articles,
        )

        # ── Cache in both tiers ──────────────────────────────────
        _cache_set(mem_key, response)
        try:
            self._upsert_etf_db_cache(symbol, response.model_dump())
        except Exception as e:
            logger.warning(f"ETF Supabase background cache failed for {symbol}: {e}")

        return response

    # ── Unified Snapshot Cache (etf_snapshot_cache) ────────────────
    # Single table with (symbol, category) unique constraint.
    # Categories: "dividend_history", "holdings_risk", etc.

    _SNAPSHOT_DB_TTL_HOURS = 24
    _SNAPSHOT_MEM_TTL = 3600  # 1 hour in-memory

    def _check_snapshot_cache(self, symbol: str, category: str) -> Optional[Dict[str, Any]]:
        """Check Supabase etf_snapshot_cache (24h TTL)."""
        try:
            row = (
                self.supabase.table("etf_snapshot_cache")
                .select("response_json, cached_at")
                .eq("symbol", symbol)
                .eq("category", category)
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
            if age > timedelta(hours=self._SNAPSHOT_DB_TTL_HOURS):
                logger.info(f"ETF snapshot STALE ({category}, age={age}) for {symbol}")
                return None
            data = entry.get("response_json")
            if data and isinstance(data, dict):
                logger.info(f"ETF snapshot HIT ({category}, age={age}) for {symbol}")
                return data
            return None
        except Exception as e:
            logger.warning(f"ETF snapshot check failed ({category}) for {symbol}: {e}")
            return None

    def _upsert_snapshot_cache(self, symbol: str, category: str, data: Dict[str, Any]) -> None:
        """Upsert into etf_snapshot_cache."""
        try:
            self.supabase.table("etf_snapshot_cache").upsert(
                {
                    "symbol": symbol,
                    "category": category,
                    "response_json": data,
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="symbol,category",
            ).execute()
            logger.info(f"ETF snapshot cached ({category}) for {symbol}")
        except Exception as e:
            logger.warning(f"ETF snapshot upsert failed ({category}) for {symbol}: {e}")

    # ── Dividend History (dedicated endpoint) ─────────────────────

    async def get_dividend_history(self, symbol: str) -> ETFDividendHistoryResponse:
        """
        Fetch full dividend history for an ETF.
        Two-tier cache: in-memory (1h) + Supabase etf_snapshot_cache (24h).
        """
        symbol = symbol.upper()
        category = "dividend_history"

        # ── Cache check: in-memory then Supabase ────────────────
        mem_key = f"etf_{category}_{symbol}"
        cached = _cache_get(mem_key, self._SNAPSHOT_MEM_TTL)
        if cached is not None:
            logger.info(f"Dividend in-memory HIT for {symbol}")
            return cached

        db_data = self._check_snapshot_cache(symbol, category)
        if db_data is not None:
            try:
                response = ETFDividendHistoryResponse(**db_data)
                _cache_set(mem_key, response)
                return response
            except Exception as e:
                logger.warning(f"Dividend snapshot data invalid for {symbol}: {e}")

        # ── Fetch from FMP ──────────────────────────────────────
        raw_dividends = await self.fmp.get_dividend_history(symbol, limit=100)
        if not raw_dividends:
            logger.warning(f"No dividend data from FMP for {symbol}")
            return ETFDividendHistoryResponse(
                symbol=symbol,
                pay_frequency="—",
                total_dividends=0,
                dividends=[],
            )

        # Use FMP's frequency field directly (first non-empty value)
        pay_frequency = "—"
        for d in raw_dividends:
            freq = d.get("frequency")
            if freq and freq != "—":
                pay_frequency = freq
                break

        # Format each dividend payment
        dividends = []
        for d in raw_dividends:
            div_amount = d.get("dividend") or d.get("adjDividend") or d.get("amount") or 0
            ex_date = d.get("date") or d.get("recordDate") or ""
            pay_date = d.get("paymentDate") or d.get("payDate") or ""

            dividends.append(ETFDividendPaymentResponse(
                dividend_per_share=f"${float(div_amount):.4f}" if div_amount else "—",
                ex_dividend_date=_format_date_readable(ex_date),
                pay_date=_format_date_readable(pay_date),
            ))

        response = ETFDividendHistoryResponse(
            symbol=symbol,
            pay_frequency=pay_frequency,
            total_dividends=len(dividends),
            dividends=dividends,
        )

        # ── Cache in both tiers ─────────────────────────────────
        _cache_set(mem_key, response)
        try:
            self._upsert_snapshot_cache(symbol, category, response.model_dump())
        except Exception as e:
            logger.warning(f"Dividend snapshot cache failed for {symbol}: {e}")

        return response

    # ── ETF Profile (dedicated endpoint) ───────────────────────────

    async def get_profile(self, symbol: str) -> ETFProfileResponse:
        """
        Fetch ETF profile data via dedicated endpoint.
        Two-tier cache: in-memory (1h) + Supabase etf_snapshot_cache (24h).
        """
        symbol = symbol.upper()
        category = "profile"

        # ── Cache check ─────────────────────────────────────────
        mem_key = f"etf_{category}_{symbol}"
        cached = _cache_get(mem_key, self._SNAPSHOT_MEM_TTL)
        if cached is not None:
            logger.info(f"Profile in-memory HIT for {symbol}")
            return cached

        db_data = self._check_snapshot_cache(symbol, category)
        if db_data is not None:
            try:
                response = ETFProfileResponse(**db_data)
                _cache_set(mem_key, response)
                return response
            except Exception as e:
                logger.warning(f"Profile snapshot data invalid for {symbol}: {e}")

        # ── Fetch from FMP (2 parallel calls) ───────────────────
        results = await asyncio.gather(
            self.fmp.get_etf_info(symbol),
            self.fmp.get_company_profile(symbol),
            return_exceptions=True,
        )

        etf_info = results[0] if not isinstance(results[0], Exception) else {}
        profile = results[1] if not isinstance(results[1], Exception) else {}

        if isinstance(results[0], Exception):
            logger.error(f"Profile etf/info failed for {symbol}: {results[0]}")
        if isinstance(results[1], Exception):
            logger.error(f"Profile company/profile failed for {symbol}: {results[1]}")

        # ── Build profile ───────────────────────────────────────
        description = etf_info.get("description") or profile.get("description") or ""
        etf_company = (
            etf_info.get("etfCompany") or etf_info.get("companyName")
            or profile.get("companyName") or "—"
        )
        asset_class = etf_info.get("assetClass") or "Equity"
        inception_date_raw = etf_info.get("inceptionDate") or profile.get("ipoDate") or ""
        domicile = etf_info.get("domicile") or "United States"
        ref = _ETF_REFERENCE.get(symbol, {})
        index_tracked = (
            etf_info.get("indexTracked") or etf_info.get("index")
            or ref.get("index") or "—"
        )
        website = etf_info.get("website") or profile.get("website") or ""
        if website.startswith("https://"):
            website = website[8:]
        elif website.startswith("http://"):
            website = website[7:]

        response = ETFProfileResponse(
            description=description,
            symbol=symbol,
            etf_company=etf_company,
            asset_class=asset_class,
            inception_date=_format_date_readable(inception_date_raw),
            domicile=domicile,
            index_tracked=index_tracked,
            website=website,
        )

        # ── Cache in both tiers ─────────────────────────────────
        _cache_set(mem_key, response)
        try:
            self._upsert_snapshot_cache(symbol, category, response.model_dump())
        except Exception as e:
            logger.warning(f"Profile snapshot cache failed for {symbol}: {e}")

        return response

    # ── Holdings & Risk (dedicated endpoint) ─────────────────────

    async def get_holdings_risk(self, symbol: str) -> ETFHoldingsRiskResponse:
        """
        Fetch holdings & risk data for an ETF via dedicated endpoint.

        Data sources (2 parallel FMP calls):
          - etf/info → sectorsList (exposure), assetClass, AUM
          - etf/holdings → top holdings with weightPercentage

        Math:
          - Asset allocation: extracts "Cash & Others" from sectorsList for real cash %
          - Sectors: top 5 from sectorsList sorted by exposure desc
          - Holdings: top 10 from etf/holdings
          - Concentration: sum of top-10 weights with insight text
        """
        symbol = symbol.upper()
        category = "holdings_risk"

        # ── Cache check: in-memory then Supabase ────────────────
        mem_key = f"etf_{category}_{symbol}"
        cached = _cache_get(mem_key, self._SNAPSHOT_MEM_TTL)
        if cached is not None:
            logger.info(f"HoldingsRisk in-memory HIT for {symbol}")
            return cached

        db_data = self._check_snapshot_cache(symbol, category)
        if db_data is not None:
            try:
                response = ETFHoldingsRiskResponse(**db_data)
                _cache_set(mem_key, response)
                return response
            except Exception as e:
                logger.warning(f"HoldingsRisk snapshot data invalid for {symbol}: {e}")

        # ── Fetch from FMP (2 parallel calls) ───────────────────
        etf_info_task = self.fmp.get_etf_info(symbol)
        holders_task = self.fmp.get_etf_holders(symbol, limit=20)

        results = await asyncio.gather(
            etf_info_task, holders_task, return_exceptions=True
        )

        etf_info = results[0] if not isinstance(results[0], Exception) else {}
        holders = results[1] if not isinstance(results[1], Exception) else []

        if isinstance(results[0], Exception):
            logger.error(f"HoldingsRisk etf/info failed for {symbol}: {results[0]}")
        if isinstance(results[1], Exception):
            logger.error(f"HoldingsRisk etf/holdings failed for {symbol}: {results[1]}")

        # ── Build sectors from sectorsList ───────────────────────
        sectors_list = etf_info.get("sectorsList") or []
        top_sectors = self._build_sectors_from_info(sectors_list)

        # ── Build top holdings ──────────────────────────────────
        top_holdings = self._build_top_holdings(holders if isinstance(holders, list) else [])

        # ── Build concentration ─────────────────────────────────
        concentration = self._build_concentration(top_holdings)

        # ── Build asset allocation (uses real cash from sectorsList) ──
        asset_class = etf_info.get("assetClass") or "Equity"
        total_assets = float(
            etf_info.get("assetsUnderManagement")
            or etf_info.get("totalAssets")
            or etf_info.get("aum")
            or etf_info.get("netAssets")
            or 0
        )
        asset_alloc = self._build_asset_allocation(
            sectors_list=sectors_list,
            asset_class=asset_class,
            total_assets=total_assets,
        )

        response = ETFHoldingsRiskResponse(
            asset_allocation=asset_alloc,
            top_sectors=top_sectors[:5],
            top_holdings=top_holdings[:10],
            concentration=concentration,
        )

        # ── Cache in both tiers ─────────────────────────────────
        _cache_set(mem_key, response)
        try:
            self._upsert_snapshot_cache(symbol, category, response.model_dump())
        except Exception as e:
            logger.warning(f"HoldingsRisk snapshot cache failed for {symbol}: {e}")

        return response

    def _build_sectors_from_info(
        self, sectors_list: List[Dict]
    ) -> List[ETFSectorWeightResponse]:
        """Build sector weights from etf/info sectorsList field.

        sectorsList uses 'industry' and 'exposure' keys (vs 'sector'/'weightPercentage'
        from the separate etf/sector-weightings endpoint). Both return the same data.
        """
        results = []
        for s in sectors_list:
            name = s.get("industry") or s.get("sector") or s.get("name") or "—"
            weight = s.get("exposure") or s.get("weightPercentage") or s.get("weight") or 0
            if isinstance(weight, str):
                try:
                    weight = float(weight.replace("%", ""))
                except (ValueError, TypeError):
                    weight = 0

            # Skip "Cash & Others" from sector display (used in asset allocation instead)
            if "cash" in name.lower() and "other" in name.lower():
                continue

            results.append(ETFSectorWeightResponse(
                name=name,
                weight=round(_finite_num(weight), 2),
            ))

        results.sort(key=lambda x: x.weight, reverse=True)
        return results

    def _build_asset_allocation(
        self, *, sectors_list: List[Dict], asset_class: str, total_assets: float,
    ) -> ETFAssetAllocationResponse:
        """Build asset allocation using real cash % from FMP sectorsList.

        FMP's sectorsList includes a "Cash & Others" entry with the actual
        cash allocation percentage. For the rest, we infer from asset_class.

        Edge case: For bond ETFs, FMP often reports sectorsList as
        [{"industry": "Cash & Others", "exposure": 100}] because it can't
        break down bond sectors. In that case, 100% is actually bonds, not cash.
        We detect this by checking if "Cash & Others" is the ONLY sector AND
        the asset class indicates bonds.
        """
        ac = asset_class.lower()
        is_bond_etf = "bond" in ac or "fixed" in ac or "income" in ac

        # Extract real cash % from sectorsList
        cash_pct = 0.0
        has_only_cash_sector = False
        for s in sectors_list:
            name = (s.get("industry") or s.get("sector") or "").lower()
            if "cash" in name and "other" in name:
                raw_cash = round(float(s.get("exposure") or s.get("weightPercentage") or 0), 2)
                # If "Cash & Others" is ~100% AND this is a bond ETF,
                # FMP is lumping all bonds into "Cash & Others" — don't treat as cash
                if raw_cash >= 95 and is_bond_etf:
                    has_only_cash_sector = True
                    cash_pct = 5.0  # Typical operational cash for bond ETFs
                else:
                    cash_pct = raw_cash
                break

        # Determine primary allocation from asset class
        remaining = round(100.0 - cash_pct, 2)

        commodities = 0.0
        if is_bond_etf:
            equities, bonds, crypto = 0.0, remaining, 0.0
        elif "crypto" in ac or "bitcoin" in ac or "digital" in ac:
            equities, bonds, crypto = 0.0, 0.0, remaining
        elif "commodity" in ac or "gold" in ac or "alternative" in ac:
            # Commodities are neither equity nor cash; a gold ETF shown as
            # "equities" (or the sibling _infer path's "100% cash") corrupts the
            # allocation donut. Route into the dedicated commodities bucket.
            equities, bonds, crypto, commodities = 0.0, 0.0, 0.0, remaining
        else:
            # Default: equity
            equities, bonds, crypto = remaining, 0.0, 0.0

        return ETFAssetAllocationResponse(
            equities=equities,
            bonds=bonds,
            crypto=crypto,
            commodities=commodities,
            cash=cash_pct,
            total_assets=_fmt(total_assets),
        )

    # ── Chart helpers ─────────────────────────────────────────────

    def _extract_chart_data(
        self, historical: List[Dict], chart_range: str
    ) -> List[Dict]:
        if not historical:
            return []

        today = datetime.now(tz=timezone.utc).date()
        range_days = {
            "1D": 2, "1W": 7, "3M": 90, "6M": 180,
            "1Y": 365, "5Y": 365 * 5, "ALL": 99999,
        }
        days = range_days.get(chart_range, 90)
        cutoff = (today - timedelta(days=days)).isoformat()

        result = []
        for p in historical:
            date = p.get("date")
            # `None >= cutoff` raises TypeError; guard before comparing.
            if not date or date < cutoff:
                continue
            raw_close = p.get("close") or p.get("adjClose")
            # Coerce BEFORE the > 0 comparison — a string close would raise
            # TypeError on `close > 0` in Python 3.
            try:
                close = float(raw_close) if raw_close is not None else None
            except (ValueError, TypeError):
                close = None
            if close is None or close <= 0:
                continue
            result.append({
                "date": date,
                "open": p.get("open"),
                "high": p.get("high"),
                "low": p.get("low"),
                "close": round(close, 2),
                "volume": p.get("volume"),
            })
        return result

    # ── Key statistics builder ────────────────────────────────────

    def _build_key_statistics(
        self, *, nav, total_assets, expense_ratio, avg_volume,
        dividend_yield, year_high, year_low, beta, price_avg_50,
        holdings_count, turnover, inception_date, asset_class,
        domicile, index_tracked,
    ) -> Tuple[List[KeyStatisticItem], List[KeyStatisticsGroupResponse]]:
        """Build both flat and grouped key statistics."""

        flat = [
            KeyStatisticItem(label="NAV", value=_fmt_dollar(nav)),
            KeyStatisticItem(label="Total Assets", value=_fmt(total_assets)),
            KeyStatisticItem(
                label="Expense Ratio",
                value=f"{expense_ratio}%" if expense_ratio else "—",
                is_highlighted=True,
            ),
            KeyStatisticItem(label="Avg. Volume", value=_fmt(avg_volume, 0, prefix="")),
            KeyStatisticItem(label="Dividend Yield", value=_pct(dividend_yield) if dividend_yield else "—"),
            KeyStatisticItem(label="52W High", value=_fmt_dollar(year_high)),
            KeyStatisticItem(label="52W Low", value=_fmt_dollar(year_low)),
            KeyStatisticItem(label="Beta", value=f"{beta:.2f}" if beta else "—"),
            KeyStatisticItem(label="50-Day Avg", value=_fmt_dollar(price_avg_50) if price_avg_50 else "—"),
            KeyStatisticItem(label="Holdings", value=str(holdings_count) if holdings_count else "—"),
            KeyStatisticItem(label="Turnover", value=_pct(turnover) if turnover else "—"),
            KeyStatisticItem(label="Inception", value=_format_date_readable(inception_date)),
        ]

        groups = [
            # Column 1: Price & NAV
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="NAV", value=_fmt_dollar(nav)),
                KeyStatisticItem(label="52W High", value=_fmt_dollar(year_high)),
                KeyStatisticItem(label="52W Low", value=_fmt_dollar(year_low)),
                KeyStatisticItem(label="Avg. Volume", value=_fmt(avg_volume, 0, prefix="")),
                KeyStatisticItem(label="Beta", value=f"{beta:.2f}" if beta else "—"),
            ]),
            # Column 2: Fund Details
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="Total Assets", value=_fmt(total_assets)),
                KeyStatisticItem(
                    label="Expense Ratio",
                    value=f"{expense_ratio}%" if expense_ratio else "—",
                    is_highlighted=True,
                ),
                KeyStatisticItem(label="Dividend Yield", value=_pct(dividend_yield) if dividend_yield else "—"),
                KeyStatisticItem(label="50-Day Avg", value=_fmt_dollar(price_avg_50) if price_avg_50 else "—"),
                KeyStatisticItem(label="Turnover", value=_pct(turnover) if turnover else "—"),
            ]),
            # Column 3: Structure
            KeyStatisticsGroupResponse(statistics=[
                KeyStatisticItem(label="Holdings", value=str(holdings_count) if holdings_count else "—"),
                KeyStatisticItem(label="Inception", value=_format_date_readable(inception_date)),
                KeyStatisticItem(label="Asset Class", value=asset_class),
                KeyStatisticItem(label="Domicile", value=domicile),
                KeyStatisticItem(label="Index", value=index_tracked),
            ]),
        ]

        return flat, groups

    # ── Performance periods builder (with S&P 500 comparison) ─────

    def _build_performance_periods(
        self, etf_hist: List[Dict], spy_hist: List[Dict]
    ) -> List[PerformancePeriodResponse]:
        """Build performance periods with real S&P 500 comparison.
        Follows same pattern as stock_overview_service._build_performance_periods."""
        periods = []
        definitions = [
            ("1 Month", 21),
            ("YTD", None),
            ("1 Year", 252),
            ("3 Years", 756),
            ("5 Years", 1260),
            ("10 Years", 2520),
        ]
        for label, days in definitions:
            if days is None:
                etf_ret = _compute_ytd_return(etf_hist)
                sp_ret = _compute_ytd_return(spy_hist)
            else:
                etf_ret = _compute_return(etf_hist, days)
                sp_ret = _compute_return(spy_hist, days)

            if etf_ret is not None:
                vs_market = round(etf_ret - (sp_ret or 0), 2) if sp_ret is not None else None
                periods.append(PerformancePeriodResponse(
                    label=label,
                    change_percent=round(etf_ret, 2),
                    vs_market_percent=vs_market,
                    sp_return_percent=round(sp_ret, 2) if sp_ret is not None else None,
                ))
        return periods

    # ── Benchmark summary builder (proper CAGR) ──────────────────

    def _build_benchmark_summary(
        self, etf_hist: List[Dict], spy_hist: List[Dict],
        *, symbol: str = "", index_tracked: str = "",
    ) -> Optional[BenchmarkSummaryResponse]:
        """Compute annualized (CAGR) returns since inception for ETF vs S&P 500.

        Each uses its OWN full history independently:
          - ETF CAGR: from ETF's first available date to today
          - S&P CAGR: from S&P's first available date to today
        The "Since" dates will differ (e.g. SPY since 2006, S&P since 1993).
        """
        if not etf_hist or len(etf_hist) < 252:
            return None

        # ── ETF: CAGR from its own first available date ──────────
        etf_days = len(etf_hist) - 1
        etf_years = etf_days / 252

        etf_start = etf_hist[0].get("close") or etf_hist[0].get("adjClose")
        etf_end = etf_hist[-1].get("close") or etf_hist[-1].get("adjClose")
        etf_start_date = etf_hist[0].get("date") or ""

        if not etf_start or not etf_end or etf_start <= 0 or etf_years <= 0:
            return None

        etf_annual = ((etf_end / etf_start) ** (1 / etf_years) - 1) * 100

        # ── S&P 500: CAGR from its own first available date ─────
        sp_annual = 0.0
        sp_start_date = ""
        if spy_hist and len(spy_hist) >= 252:
            sp_start_price = spy_hist[0].get("close") or spy_hist[0].get("adjClose")
            sp_end_price = spy_hist[-1].get("close") or spy_hist[-1].get("adjClose")
            sp_start_date = spy_hist[0].get("date") or ""
            sp_days = len(spy_hist) - 1
            sp_years = sp_days / 252

            if sp_start_price and sp_end_price and sp_start_price > 0 and sp_years > 0:
                sp_annual = ((sp_end_price / sp_start_price) ** (1 / sp_years) - 1) * 100

        return BenchmarkSummaryResponse(
            avg_annual_return=round(etf_annual, 1),
            sp_benchmark=round(sp_annual, 1),
            benchmark_name="S&P 500",
            since_date=_format_date_readable(etf_start_date),
            benchmark_since_date=None,
            badge_threshold=0.0,
        )

    # ── Holdings builder ──────────────────────────────────────────

    def _build_top_holdings(
        self, holders: List[Dict]
    ) -> List[ETFTopHoldingResponse]:
        results = []
        for h in holders[:10]:
            weight = h.get("weightPercentage") or h.get("weight") or 0
            if isinstance(weight, str):
                try:
                    weight = float(weight.replace("%", ""))
                except (ValueError, TypeError):
                    weight = 0
            results.append(ETFTopHoldingResponse(
                symbol=h.get("asset") or h.get("symbol") or "—",
                name=h.get("name") or h.get("companyName") or "—",
                weight=round(_finite_num(weight), 2),
            ))
        return results

    # ── Sector weights builder ────────────────────────────────────

    def _build_sector_weights(
        self, sector_raw: List[Dict]
    ) -> List[ETFSectorWeightResponse]:
        results = []
        for s in sector_raw:
            weight = s.get("weightPercentage") or s.get("weight") or "0"
            if isinstance(weight, str):
                try:
                    weight = float(weight.replace("%", ""))
                except (ValueError, TypeError):
                    weight = 0
            sector_name = s.get("sector") or s.get("name") or "—"
            results.append(ETFSectorWeightResponse(
                name=sector_name,
                weight=round(_finite_num(weight), 2),
            ))
        # Sort largest first
        results.sort(key=lambda x: x.weight, reverse=True)
        return results

    # ── Concentration builder ─────────────────────────────────────

    def _build_concentration(
        self, top_holdings: List[ETFTopHoldingResponse]
    ) -> ETFConcentrationResponse:
        top_10 = top_holdings[:10]
        total_weight = sum(h.weight for h in top_10)
        n = len(top_10)

        # No holdings data → report honestly instead of mislabeling an unknown
        # fund as "well diversified" (green / low-risk) off a 0% total weight.
        if n == 0:
            return ETFConcentrationResponse(
                top_n=0,
                weight=0.0,
                insight="Holdings data isn't available for this fund yet.",
            )

        # Boundaries aligned with Swift ETFConcentrationLevel:
        #   < 20% → low (Well Diversified)
        #   20-35% → moderate (Moderate)
        #   >= 35% → high (Concentrated)
        if total_weight >= 35:
            insight = (
                f"Over a third of your money is in just {n} companies. "
                "If these big names stumble, this fund feels it."
            )
        elif total_weight >= 20:
            insight = (
                f"The top {n} holdings make up {total_weight:.0f}% — "
                "moderate concentration with reasonable diversification."
            )
        else:
            insight = (
                f"Only {total_weight:.0f}% in the top {n} holdings — "
                "this fund is well diversified across many companies."
            )

        return ETFConcentrationResponse(
            top_n=n,
            weight=round(total_weight, 1),
            insight=insight,
        )

    # ── Dividend history builder ──────────────────────────────────

    def _build_dividend_history(
        self, dividends: List[Dict]
    ) -> List[ETFDividendPaymentResponse]:
        results = []
        for d in dividends:
            div_amount = d.get("dividend") or d.get("adjDividend") or d.get("amount") or 0
            ex_date = d.get("date") or d.get("recordDate") or ""
            pay_date = d.get("paymentDate") or d.get("payDate") or ""

            results.append(ETFDividendPaymentResponse(
                dividend_per_share=f"${float(div_amount):.4f}" if div_amount else "—",
                ex_dividend_date=_format_date_readable(ex_date),
                pay_date=_format_date_readable(pay_date),
            ))
        return results

    # ── Pay frequency inference ───────────────────────────────────

    def _infer_pay_frequency(self, dividends: List[Dict]) -> str:
        """Infer dividend pay frequency from payment dates."""
        if not dividends or len(dividends) < 2:
            return "—"

        dates = []
        for d in dividends[:8]:
            date_str = d.get("date") or d.get("recordDate") or ""
            try:
                dates.append(datetime.strptime(date_str, "%Y-%m-%d"))
            except (ValueError, TypeError):
                continue

        if len(dates) < 2:
            return "—"

        # Compute average gap between payments
        gaps = []
        for i in range(1, len(dates)):
            gaps.append(abs((dates[i - 1] - dates[i]).days))

        avg_gap = sum(gaps) / len(gaps) if gaps else 365

        if avg_gap < 45:
            return "Monthly"
        elif avg_gap < 120:
            return "Quarterly"
        elif avg_gap < 240:
            return "Semi-Annually"
        else:
            return "Annually"

    # ── Asset allocation inference ────────────────────────────────

    def _infer_asset_allocation(
        self, *, asset_class: str, total_assets: float,
    ) -> ETFAssetAllocationResponse:
        """Infer asset allocation from asset class (FMP doesn't provide granular breakdown)."""
        ac = asset_class.lower()
        commodities = 0.0
        if "bond" in ac or "fixed" in ac:
            equities, bonds, crypto, cash = 0, 95, 0, 5
        elif "crypto" in ac or "bitcoin" in ac:
            equities, bonds, crypto, cash = 0, 0, 95, 5
        elif "commodity" in ac or "gold" in ac or "alternative" in ac:
            # Was "0,0,0,100" (a gold ETF shown as 100% cash). Route into the
            # dedicated commodities bucket, matching _build_asset_allocation so the
            # detail screen and /holdings-risk endpoint agree.
            equities, bonds, crypto, cash, commodities = 0, 0, 0, 5, 95
        elif "real estate" in ac or "reit" in ac:
            equities, bonds, crypto, cash = 95, 0, 0, 5
        else:
            equities, bonds, crypto, cash = 99.5, 0, 0, 0.5

        return ETFAssetAllocationResponse(
            equities=equities,
            bonds=bonds,
            crypto=crypto,
            commodities=commodities,
            cash=cash,
            total_assets=_fmt(total_assets),
        )

    # ── Related ETFs builder ──────────────────────────────────────

    async def _build_related_etfs(
        self, symbol: str
    ) -> List[RelatedTickerResponse]:
        """Fetch related ETFs: curated table first, then FMP peers as fallback.

        Strategy:
          1. If symbol is in the curated _RELATED_ETFS table, use those (high quality).
          2. Otherwise, try FMP's stock peers endpoint.
          3. If FMP returns nothing, use _DEFAULT_RELATED.
        """
        if symbol in _RELATED_ETFS:
            related_symbols = _RELATED_ETFS[symbol]
        else:
            # Try FMP peers endpoint
            try:
                fmp_peers = await self.fmp.get_stock_peers(symbol)
                # Filter out mutual funds (5-char tickers ending in X) and non-alpha
                related_symbols = [
                    p for p in (fmp_peers or [])
                    if p and 2 <= len(p) <= 5 and p.isalpha() and p.isupper()
                    and not (len(p) == 5 and p.endswith("X"))
                ][:6]
                if related_symbols:
                    logger.info(f"Related ETFs from FMP peers for {symbol}: {related_symbols}")
                else:
                    related_symbols = _DEFAULT_RELATED
            except Exception as e:
                logger.warning(f"FMP peers failed for {symbol}: {e}")
                related_symbols = _DEFAULT_RELATED

        # Exclude self
        related_symbols = [s for s in related_symbols if s != symbol][:6]

        if not related_symbols:
            return []

        tasks = [self.fmp.get_stock_price_quote(s) for s in related_symbols]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        related = []
        for sym, res in zip(related_symbols, results):
            if isinstance(res, Exception) or not res:
                continue
            related.append(RelatedTickerResponse(
                symbol=sym,
                name=res.get("name") or sym,
                price=_finite_num(res.get("price")),
                change_percent=round(_finite_num(
                    res.get("changePercentage") or res.get("changesPercentage")
                ), 2),
            ))
        return related

    # ── News builder ──────────────────────────────────────────────

    def _build_news(
        self, raw_articles: List[Dict]
    ) -> List[ETFNewsArticleResponse]:
        articles = []
        for item in raw_articles[:10]:
            published = item.get("publishedDate") or item.get("published_date") or ""
            articles.append(ETFNewsArticleResponse(
                headline=item.get("title") or item.get("headline") or "",
                source_name=item.get("site") or item.get("source") or "Unknown",
                source_icon=None,
                sentiment="neutral",
                published_at=published,
                thumbnail_url=item.get("image") or item.get("thumbnail_url"),
                related_tickers=[
                    s.strip() for s in (item.get("symbol") or "").split(",") if s.strip()
                ],
                summary_bullets=[],
                article_url=item.get("url") or item.get("article_url"),
            ))
        return articles

    # ── Identity Rating (100% FMP data) ────────────────────────────

    def _build_identity_rating(
        self, *, total_assets: float, beta: float, expense_ratio: float,
        inception_date: str, holdings_count: int,
    ) -> ETFIdentityRatingResponse:
        """
        Build identity rating from FMP data only.

        Score (1-5): Composite of AUM, age, expense ratio, and diversification.
        Volatility: Directly from beta.
        """
        # ── Score: weighted composite ────────────────────────────
        # AUM component (0-2 points)
        if total_assets > 50_000_000_000:
            aum_pts = 2.0
        elif total_assets > 10_000_000_000:
            aum_pts = 1.5
        elif total_assets > 1_000_000_000:
            aum_pts = 1.0
        elif total_assets > 100_000_000:
            aum_pts = 0.5
        else:
            aum_pts = 0.0

        # Age component (0-1 point) — older = more proven
        age_years = 0
        if inception_date:
            try:
                inception = datetime.strptime(inception_date, "%Y-%m-%d")
                age_years = (datetime.now() - inception).days / 365.25
            except (ValueError, TypeError):
                pass
        if age_years > 15:
            age_pts = 1.0
        elif age_years > 7:
            age_pts = 0.7
        elif age_years > 3:
            age_pts = 0.4
        else:
            age_pts = 0.1

        # Expense ratio component (0-1 point) — lower = better
        if expense_ratio <= 0.05:
            fee_pts = 1.0
        elif expense_ratio <= 0.15:
            fee_pts = 0.8
        elif expense_ratio <= 0.40:
            fee_pts = 0.5
        elif expense_ratio <= 0.75:
            fee_pts = 0.2
        else:
            fee_pts = 0.0

        # Diversification component (0-1 point)
        if holdings_count >= 500:
            div_pts = 1.0
        elif holdings_count >= 100:
            div_pts = 0.7
        elif holdings_count >= 30:
            div_pts = 0.4
        else:
            div_pts = 0.1

        raw_score = aum_pts + age_pts + fee_pts + div_pts  # 0-5
        score = max(1, min(5, round(raw_score)))

        # ── Volatility from beta ─────────────────────────────────
        # Negative beta = inverse/leveraged ETF (moves opposite to market)
        # Use abs(beta) for magnitude; negative beta is always high risk
        if beta < 0:
            vol_label = "High Volatility"
        elif beta < 0.8:
            vol_label = "Low Volatility"
        elif beta < 1.2:
            vol_label = "Moderate Volatility"
        else:
            vol_label = "High Volatility"

        return ETFIdentityRatingResponse(
            score=score,
            max_score=5,
            volatility_label=vol_label,
        )

    # ── Strategy (FMP for tags, Gemini for hook text only) ───────

    async def _build_strategy(
        self, *, symbol: str, name: str, description: str,
        asset_class: str, index_tracked: str, holdings_count: int,
        top_holdings: List[ETFTopHoldingResponse],
        top_sectors: List[ETFSectorWeightResponse],
    ) -> ETFStrategyResponse:
        """
        Build strategy snapshot.
        Tags: derived from FMP data (asset class, index, holdings).
        Hook: Gemini generates a punchy one-liner; falls back to template.
        """
        # ── Tags from FMP data ───────────────────────────────────
        tags = []
        ac = asset_class.lower()

        # Passive vs Active
        if index_tracked and index_tracked != "—":
            tags.append("Passive")
            tags.append("Index")
        else:
            tags.append("Active")

        # Asset class tags
        if "bond" in ac or "fixed" in ac:
            tags.append("Bond")
        elif "commodity" in ac or "gold" in ac:
            tags.append("Thematic")
        elif "real estate" in ac or "reit" in ac:
            tags.append("Sector")
        elif "equity" in ac or ac == "":
            # Size classification from holdings count
            if holdings_count >= 500:
                tags.append("Large Cap")
                tags.append("Blend")
            elif holdings_count >= 100:
                tags.append("Blend")
            elif holdings_count < 50:
                tags.append("Thematic")

        # Check for dividend focus from name
        name_lower = name.lower() + " " + (description or "").lower()
        if "dividend" in name_lower or "yield" in name_lower:
            tags.append("Dividend")
        if "growth" in name_lower:
            tags.append("Growth")
        if "value" in name_lower:
            tags.append("Value")
        if "international" in name_lower or "global" in name_lower or "emerging" in name_lower:
            tags.append("International")

        # Deduplicate and limit
        seen = set()
        unique_tags = []
        for t in tags:
            if t not in seen:
                seen.add(t)
                unique_tags.append(t)
        tags = unique_tags[:4]

        if not tags:
            tags = ["Index", "Blend"]

        # ── Hook: Gemini for creative text, fallback to template ──
        fallback_hook = self._build_hook_fallback(
            asset_class=asset_class,
            index_tracked=index_tracked,
            holdings_count=holdings_count,
        )

        hook = await self._generate_hook_text(
            symbol=symbol,
            name=name,
            description=description,
            asset_class=asset_class,
            index_tracked=index_tracked,
            holdings_count=holdings_count,
            top_holdings=top_holdings,
            top_sectors=top_sectors,
            fallback=fallback_hook,
        )

        return ETFStrategyResponse(hook=hook, tags=tags)

    def _build_hook_fallback(
        self, *, asset_class: str, index_tracked: str, holdings_count: int,
    ) -> str:
        """Template-based hook when Gemini is unavailable."""
        if index_tracked and index_tracked != "—":
            return f"Tracks the {index_tracked}. {holdings_count} holdings for broad market exposure."[:120]
        return f"A {asset_class.lower()} fund with {holdings_count} holdings."[:120]

    async def _generate_hook_text(
        self, *, symbol: str, name: str, description: str,
        asset_class: str, index_tracked: str, holdings_count: int,
        top_holdings: List[ETFTopHoldingResponse],
        top_sectors: List[ETFSectorWeightResponse],
        fallback: str,
    ) -> str:
        """
        Use Gemini to generate ONLY the hook text — one punchy sentence.
        All structured data (score, tags) comes from FMP.
        Cached for 1 hour. Returns fallback on any failure.
        """
        cache_key = f"etf_hook_{symbol}"
        cached = _cache_get(cache_key, _AI_CACHE_TTL_SECONDS)
        if cached:
            return cached

        try:
            gemini = get_gemini_client()

            holdings_text = ", ".join(
                f"{h.symbol} ({h.weight}%)" for h in top_holdings[:5]
            )
            sectors_text = ", ".join(
                f"{s.name} ({s.weight}%)" for s in top_sectors[:3]
            )

            prompt = f"""Write ONE sentence (max 120 characters) that explains what this ETF does in plain English for a beginner investor.

ETF: {symbol} — {name}
Asset Class: {asset_class}
Index Tracked: {index_tracked or 'Actively managed'}
Holdings: {holdings_count}
Top Holdings: {holdings_text}
Top Sectors: {sectors_text}
Description: {(description or 'N/A')[:200]}

RULES:
- Max 120 characters total
- Plain English, no jargon
- Be direct and specific about what this fund actually does
- Do NOT start with "This ETF" or "This fund"
- Output ONLY the sentence, nothing else"""

            ai_response = await gemini.generate_text(
                prompt=prompt,
                system_instruction="You are a concise financial writer. Output only the requested sentence.",
                model_name="gemini-2.5-flash",
            )

            text = ai_response.get("text", "").strip().strip('"').strip("'")
            # Remove any markdown or extra content
            text = text.split("\n")[0].strip()

            if text and len(text) <= 140:
                hook = text[:120]
                _cache_set(cache_key, hook)
                logger.info(f"Generated Gemini hook for ETF {symbol}: {hook}")
                return hook
            else:
                logger.warning(f"Gemini hook too long or empty for {symbol}, using fallback")
                return fallback

        except Exception as e:
            logger.warning(f"Gemini hook failed for {symbol}, using fallback: {e}")
            return fallback


# ── Singleton ────────────────────────────────────────────────────

_etf_service: Optional[ETFService] = None


def get_etf_service() -> ETFService:
    global _etf_service
    if _etf_service is None:
        _etf_service = ETFService()
    return _etf_service
