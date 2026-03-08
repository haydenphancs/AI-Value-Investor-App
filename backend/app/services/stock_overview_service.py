"""
Stock Overview Service — aggregates FMP data, computes derived stats
(performance, snapshots, sector info) for the TickerDetailView Overview tab.

Pattern follows etf_service.py: parallel FMP calls, in-memory caching,
helper functions for return calculations and snapshot ratings.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.integrations.fmp import get_fmp_client, FMPClient
from app.schemas.etf import (
    BenchmarkSummaryResponse,
    KeyStatisticItem,
    KeyStatisticsGroupResponse,
    MarketStatusResponse,
    PerformancePeriodResponse,
    RelatedTickerResponse,
)
from app.schemas.stock_overview import (
    CompanyProfileResponse,
    SectorIndustryResponse,
    SnapshotItemResponse,
    SnapshotMetricResponse,
    StockOverviewResponse,
)

logger = logging.getLogger(__name__)

# ── In-memory cache ──────────────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 300          # 5 min for market data
_SP_HIST_CACHE_TTL = 3600  # 1 hour for S&P historical


def _cache_get(key: str, ttl: float = _CACHE_TTL) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, value = entry
    if time.time() - ts > ttl:
        del _cache[key]
        return None
    return value


def _cache_set(key: str, value: Any):
    _cache[key] = (time.time(), value)


# ── Sector P/E averages (approximate, for valuation context) ─────

_SECTOR_PE_AVG: Dict[str, float] = {
    "Technology": 30.0,
    "Healthcare": 22.0,
    "Financial Services": 15.0,
    "Consumer Cyclical": 22.0,
    "Consumer Defensive": 23.0,
    "Industrials": 21.0,
    "Energy": 12.0,
    "Utilities": 18.0,
    "Real Estate": 35.0,
    "Basic Materials": 16.0,
    "Communication Services": 20.0,
}

_SECTOR_PS_AVG: Dict[str, float] = {
    "Technology": 7.0,
    "Healthcare": 4.5,
    "Financial Services": 3.0,
    "Consumer Cyclical": 2.5,
    "Consumer Defensive": 2.0,
    "Industrials": 2.5,
    "Energy": 1.5,
    "Utilities": 2.5,
    "Real Estate": 6.0,
    "Basic Materials": 2.0,
    "Communication Services": 4.0,
}


# ── Number formatting helpers ────────────────────────────────────


def _fmt(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "—"
    if abs(value) >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.2f}T"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.2f}M"
    return f"{value:,.{decimals}f}"


def _fmt_large(value: Optional[float]) -> str:
    """Format large numbers without dollar sign."""
    if value is None:
        return "—"
    if abs(value) >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:,.0f}"


def _pct(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{decimals}f}%"


def _safe_float(d: Dict, key: str, default: float = 0.0) -> float:
    """Safely extract a float from a dict."""
    v = d.get(key)
    if v is None:
        return default
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


# ── Return computation helpers (same as etf_service) ─────────────


def _compute_return(prices: List[Dict], days_back: int) -> Optional[float]:
    """Compute % return over the last N trading days."""
    if not prices or len(prices) < 2:
        return None
    if len(prices) <= days_back:
        start = prices[0].get("close") or prices[0].get("adjClose")
        end = prices[-1].get("close") or prices[-1].get("adjClose")
    else:
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
        date_str = p.get("date", "")
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


def _parse_historical(hist_raw) -> List[Dict]:
    """Parse FMP historical prices into sorted list (oldest-first)."""
    historical: List[Dict] = []
    if isinstance(hist_raw, dict):
        historical = hist_raw.get("historical", [])
    elif isinstance(hist_raw, list):
        historical = hist_raw
    historical.sort(key=lambda p: p.get("date", ""))
    return historical


def _extract_chart_data(prices: List[Dict], chart_range: str) -> List[Dict]:
    """Extract OHLCV data for the requested chart range."""
    range_days = {
        "1D": 2, "1W": 5, "3M": 63, "6M": 126,
        "1Y": 252, "5Y": 1260, "ALL": 999999,
    }
    days = range_days.get(chart_range, 63)
    relevant = prices[-days:] if len(prices) > days else prices
    result = []
    for p in relevant:
        close = p.get("close") or p.get("adjClose")
        if close:
            result.append({
                "date": p.get("date"),
                "open": p.get("open"),
                "high": p.get("high"),
                "low": p.get("low"),
                "close": float(close),
                "volume": p.get("volume"),
            })
    return result


# ── Main service ─────────────────────────────────────────────────


class StockOverviewService:
    """Aggregates FMP data for the Stock Detail Overview tab."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()

    async def get_overview(
        self, ticker: str, chart_range: str = "3M"
    ) -> StockOverviewResponse:
        ticker = ticker.upper()

        # Check full overview cache
        cache_key = f"stock_overview:{ticker}:{chart_range}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        # ── Step 1: Parallel FMP fetches ─────────────────────────
        today = datetime.now(tz=timezone.utc).date()
        from_date_10y = (today - timedelta(days=365 * 10 + 30)).isoformat()
        to_date = today.isoformat()

        # Check if we have cached SPY historical
        sp_cache_key = f"spy_hist:{from_date_10y}:{to_date}"
        cached_spy = _cache_get(sp_cache_key, _SP_HIST_CACHE_TTL)

        tasks = [
            self.fmp.get_company_profile(ticker),          # 0
            self.fmp.get_stock_price_quote(ticker),        # 1
            self.fmp.get_key_metrics(ticker, period="annual", limit=5),   # 2
            self.fmp.get_financial_ratios(ticker, period="annual", limit=5),  # 3
            self.fmp.get_income_statement(ticker, period="annual", limit=3),  # 4
            self.fmp.get_balance_sheet(ticker, period="annual", limit=2),     # 5
            self.fmp.get_cash_flow_statement(ticker, period="annual", limit=2),  # 6
            self.fmp.get_analyst_estimates(ticker, period="annual", limit=1),    # 7
            self.fmp.get_sector_performance(),              # 8
            self.fmp.get_historical_prices(ticker, from_date_10y, to_date),  # 9
        ]

        # Only fetch SPY if not cached
        if cached_spy is None:
            tasks.append(
                self.fmp.get_historical_prices("SPY", from_date_10y, to_date)  # 10
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        profile = results[0] if not isinstance(results[0], Exception) else {}
        quote = results[1] if not isinstance(results[1], Exception) else {}
        key_metrics_raw = results[2] if not isinstance(results[2], Exception) else []
        fin_ratios_raw = results[3] if not isinstance(results[3], Exception) else []
        income_annual = results[4] if not isinstance(results[4], Exception) else []
        balance_annual = results[5] if not isinstance(results[5], Exception) else []
        cashflow_annual = results[6] if not isinstance(results[6], Exception) else []
        analyst_est = results[7] if not isinstance(results[7], Exception) else []
        sector_perf = results[8] if not isinstance(results[8], Exception) else []
        stock_hist_raw = results[9] if not isinstance(results[9], Exception) else {}

        if cached_spy is not None:
            spy_historical = cached_spy
        else:
            spy_hist_raw = results[10] if not isinstance(results[10], Exception) else {}
            spy_historical = _parse_historical(spy_hist_raw)
            if spy_historical:
                _cache_set(sp_cache_key, spy_historical)

        # Log any failures
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                logger.warning(f"FMP call {i} failed for {ticker}: {r}")

        # Parse lists safely
        key_metrics = key_metrics_raw if isinstance(key_metrics_raw, list) else []
        fin_ratios = fin_ratios_raw if isinstance(fin_ratios_raw, list) else []
        income_annual = income_annual if isinstance(income_annual, list) else []
        balance_annual = balance_annual if isinstance(balance_annual, list) else []
        cashflow_annual = cashflow_annual if isinstance(cashflow_annual, list) else []
        analyst_est = analyst_est if isinstance(analyst_est, list) else []
        sector_perf = sector_perf if isinstance(sector_perf, list) else []

        # Parse historical prices
        stock_historical = _parse_historical(stock_hist_raw)

        # ── Step 2: Extract quote/profile data ────────────────────
        price = _safe_float(quote, "price") or _safe_float(profile, "price")
        change = _safe_float(quote, "change") or _safe_float(profile, "changes")
        change_pct = _safe_float(quote, "changesPercentage") or _safe_float(profile, "changesPercentage")
        company_name = profile.get("companyName") or quote.get("name") or ticker

        # ── Step 3: Chart data ────────────────────────────────────
        chart_data = _extract_chart_data(stock_historical, chart_range)

        # ── Step 4: Key statistics ────────────────────────────────
        key_statistics, key_statistics_groups = self._build_key_statistics(
            quote, profile, key_metrics, analyst_est, price
        )

        # ── Step 5: Performance periods ───────────────────────────
        performance_periods = self._build_performance_periods(
            stock_historical, spy_historical
        )

        # ── Step 6: Snapshots ────────────────────────────────────
        sector_name = profile.get("sector") or "N/A"
        snapshots = self._build_snapshots(
            key_metrics, fin_ratios, income_annual, balance_annual,
            cashflow_annual, price, _safe_float(profile, "mktCap") or _safe_float(quote, "marketCap"),
            sector_name
        )

        # ── Step 7: Sector & Industry ─────────────────────────────
        sector_industry = self._build_sector_industry(profile, sector_perf)

        # ── Step 8: Company profile ───────────────────────────────
        company_profile = self._build_company_profile(profile)

        # ── Step 9: Related tickers ───────────────────────────────
        related_tickers = await self._build_related_tickers(ticker)

        # ── Step 10: Benchmark summary ────────────────────────────
        benchmark_summary = self._build_benchmark_summary(
            stock_historical, spy_historical
        )

        # ── Assemble response ────────────────────────────────────
        response = StockOverviewResponse(
            symbol=ticker,
            company_name=company_name,
            current_price=price,
            price_change=change,
            price_change_percent=change_pct,
            market_status=_get_market_status(),
            chart_data=chart_data,
            key_statistics=key_statistics,
            key_statistics_groups=key_statistics_groups,
            performance_periods=performance_periods,
            snapshots=snapshots,
            sector_industry=sector_industry,
            company_profile=company_profile,
            related_tickers=related_tickers,
            benchmark_summary=benchmark_summary,
        )

        _cache_set(cache_key, response)
        return response

    # ── Key Statistics ────────────────────────────────────────────

    def _build_key_statistics(
        self, quote: Dict, profile: Dict, key_metrics: List[Dict],
        analyst_est: List[Dict], price: float,
    ) -> Tuple[List[KeyStatisticItem], List[KeyStatisticsGroupResponse]]:
        open_val = _safe_float(quote, "open")
        prev_close = _safe_float(quote, "previousClose")
        day_high = _safe_float(quote, "dayHigh")
        day_low = _safe_float(quote, "dayLow")
        volume = _safe_float(quote, "volume")
        avg_volume = _safe_float(quote, "avgVolume") or _safe_float(profile, "volAvg")
        market_cap = _safe_float(quote, "marketCap") or _safe_float(profile, "mktCap")
        year_high = _safe_float(quote, "yearHigh")
        year_low = _safe_float(quote, "yearLow")
        # Fallback: parse profile.range string "124.17-199.62"
        if not year_high or not year_low:
            range_str = profile.get("range", "")
            if isinstance(range_str, str) and "-" in range_str:
                parts = range_str.split("-")
                if len(parts) == 2:
                    try:
                        low_val, high_val = float(parts[0]), float(parts[1])
                        if not year_low:
                            year_low = low_val
                        if not year_high:
                            year_high = high_val
                    except ValueError:
                        pass
        pe = _safe_float(quote, "pe")
        eps = _safe_float(quote, "eps")
        beta = _safe_float(profile, "beta") or _safe_float(quote, "beta")
        last_div = _safe_float(profile, "lastDiv")
        shares_out = _safe_float(quote, "sharesOutstanding")

        # Forward P/E from analyst estimates
        pe_fwd = None
        if analyst_est and isinstance(analyst_est[0], dict):
            fwd_eps = _safe_float(analyst_est[0], "estimatedEpsAvg")
            if fwd_eps > 0 and price > 0:
                pe_fwd = round(price / fwd_eps, 2)

        # Ownership from key_metrics (most recent)
        km = key_metrics[0] if key_metrics else {}
        insider_pct = km.get("insidersPercentage")
        inst_pct = km.get("institutionPercentage") or km.get("institutionalOwnership")

        # Dividend yield
        div_str = "—"
        if last_div > 0 and price > 0:
            annual_div = last_div  # FMP lastDiv is already annualized
            div_yield = (annual_div / price) * 100
            div_str = f"{annual_div:.2f} ({div_yield:.2f}%)"

        # Flat list
        flat_stats = [
            KeyStatisticItem(label="Open", value=f"{open_val:.2f}" if open_val else "—"),
            KeyStatisticItem(label="Previous Close", value=f"{prev_close:.2f}" if prev_close else "—"),
            KeyStatisticItem(label="Day High", value=f"{day_high:.2f}" if day_high else "—"),
            KeyStatisticItem(label="Day Low", value=f"{day_low:.2f}" if day_low else "—"),
            KeyStatisticItem(label="Volume", value=_fmt_large(volume) if volume else "—"),
            KeyStatisticItem(label="Avg. Volume (3M)", value=_fmt_large(avg_volume) if avg_volume else "—"),
            KeyStatisticItem(label="Market Cap", value=_fmt(market_cap) if market_cap else "—"),
            KeyStatisticItem(label="52-Week High", value=f"{year_high:.2f}" if year_high else "—"),
            KeyStatisticItem(label="52-Week Low", value=f"{year_low:.2f}" if year_low else "—"),
            KeyStatisticItem(label="P/E (TTM)", value=f"{pe:.2f}" if pe and pe > 0 else "—"),
            KeyStatisticItem(label="P/E (FWD)", value=f"{pe_fwd:.2f}" if pe_fwd else "—"),
            KeyStatisticItem(label="EPS (TTM)", value=f"{eps:.2f}" if eps else "—"),
            KeyStatisticItem(label="Dividend & Yield", value=div_str),
            KeyStatisticItem(label="Beta", value=f"{beta:.2f}" if beta else "—"),
        ]

        # Groups (4 columns)
        group1 = KeyStatisticsGroupResponse(statistics=[
            KeyStatisticItem(label="Open", value=f"{open_val:.2f}" if open_val else "—"),
            KeyStatisticItem(label="Previous Close", value=f"{prev_close:.2f}" if prev_close else "—"),
            KeyStatisticItem(label="Volume", value=_fmt_large(volume) if volume else "—"),
            KeyStatisticItem(label="Avg. Volume (3M)", value=_fmt_large(avg_volume) if avg_volume else "—"),
            KeyStatisticItem(label="Market Cap", value=_fmt(market_cap) if market_cap else "—"),
        ])

        group2 = KeyStatisticsGroupResponse(statistics=[
            KeyStatisticItem(label="Day High", value=f"{day_high:.2f}" if day_high else "—"),
            KeyStatisticItem(label="Day Low", value=f"{day_low:.2f}" if day_low else "—"),
            KeyStatisticItem(label="52-Week High", value=f"{year_high:.2f}" if year_high else "—"),
            KeyStatisticItem(label="52-Week Low", value=f"{year_low:.2f}" if year_low else "—"),
        ])

        group3 = KeyStatisticsGroupResponse(statistics=[
            KeyStatisticItem(label="P/E (TTM)", value=f"{pe:.2f}" if pe and pe > 0 else "—"),
            KeyStatisticItem(label="P/E (FWD)", value=f"{pe_fwd:.2f}" if pe_fwd else "—"),
            KeyStatisticItem(label="EPS (TTM)", value=f"{eps:.2f}" if eps else "—"),
            KeyStatisticItem(label="Dividend & Yield", value=div_str),
            KeyStatisticItem(label="Beta", value=f"{beta:.2f}" if beta else "—"),
        ])

        # Ownership group
        # Short % of Float: try key_metrics fields (availability varies by FMP plan)
        short_pct = km.get("shortPercentOutstanding") or km.get("shortPercentFloat")
        if short_pct is not None:
            try:
                sp = float(short_pct)
                if sp < 1:
                    sp *= 100
                short_pct_str = f"{sp:.2f}%"
            except (ValueError, TypeError):
                short_pct_str = "N/A"
        else:
            short_pct_str = "N/A"

        # Float = shares outstanding * (1 - insider %)
        float_shares = None
        if shares_out and insider_pct is not None:
            try:
                ins = float(insider_pct)
                if ins < 1:
                    ins *= 100  # normalize to percentage
                float_shares = shares_out * (1 - ins / 100)
            except (ValueError, TypeError):
                pass
        float_str = _fmt_large(float_shares) if float_shares else "—"

        insider_str = _pct(insider_pct * 100 if insider_pct and insider_pct < 1 else insider_pct) if insider_pct else "—"
        inst_str = _pct(inst_pct * 100 if inst_pct and inst_pct < 1 else inst_pct) if inst_pct else "—"

        group4 = KeyStatisticsGroupResponse(statistics=[
            KeyStatisticItem(label="Short % of Float", value=short_pct_str, is_highlighted=True),
            KeyStatisticItem(label="Shares Outstanding", value=_fmt_large(shares_out) if shares_out else "—"),
            KeyStatisticItem(label="Float", value=float_str),
            KeyStatisticItem(label="% Held by Insiders", value=insider_str),
            KeyStatisticItem(label="% Held Inst.", value=inst_str),
        ])

        return flat_stats, [group1, group2, group3, group4]

    # ── Performance Periods ───────────────────────────────────────

    def _build_performance_periods(
        self, stock_hist: List[Dict], spy_hist: List[Dict]
    ) -> List[PerformancePeriodResponse]:
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
                stock_ret = _compute_ytd_return(stock_hist)
                sp_ret = _compute_ytd_return(spy_hist)
            else:
                stock_ret = _compute_return(stock_hist, days)
                sp_ret = _compute_return(spy_hist, days)

            if stock_ret is not None:
                vs_market = round(stock_ret - (sp_ret or 0), 2) if sp_ret is not None else None
                periods.append(PerformancePeriodResponse(
                    label=label,
                    change_percent=round(stock_ret, 2),
                    vs_market_percent=vs_market,
                ))
        return periods

    # ── Snapshots ─────────────────────────────────────────────────

    def _build_snapshots(
        self, key_metrics: List[Dict], fin_ratios: List[Dict],
        income_annual: List[Dict], balance_annual: List[Dict],
        cashflow_annual: List[Dict], price: float, market_cap: float,
        sector: str,
    ) -> List[SnapshotItemResponse]:
        snapshots = []

        # Get most recent data
        km = key_metrics[0] if key_metrics else {}
        fr = fin_ratios[0] if fin_ratios else {}
        inc0 = income_annual[0] if income_annual else {}
        inc1 = income_annual[1] if len(income_annual) > 1 else {}
        bs = balance_annual[0] if balance_annual else {}
        cf0 = cashflow_annual[0] if cashflow_annual else {}
        cf1 = cashflow_annual[1] if len(cashflow_annual) > 1 else {}

        # 1. Profitability
        snapshots.append(self._build_profitability_snapshot(km, fr, inc0))

        # 2. Growth
        snapshots.append(self._build_growth_snapshot(inc0, inc1, cf0, cf1, km, key_metrics))

        # 3. Price / Valuation
        snapshots.append(self._build_valuation_snapshot(fr, km, sector))

        # 4. Financial Health
        snapshots.append(self._build_health_snapshot(bs, inc0, cf0, fr, km, market_cap))

        # 5. Insiders & Ownership
        snapshots.append(self._build_ownership_snapshot(km))

        return snapshots

    def _build_profitability_snapshot(
        self, km: Dict, fr: Dict, inc: Dict
    ) -> SnapshotItemResponse:
        op_margin = _safe_float(fr, "operatingProfitMargin") or _safe_float(km, "operatingProfitMargin")
        net_margin = _safe_float(fr, "netProfitMargin") or _safe_float(km, "netIncomePerShare")
        roe = _safe_float(fr, "returnOnEquity") or _safe_float(km, "roe")
        roa = _safe_float(fr, "returnOnAssets") or _safe_float(km, "returnOnTangibleAssets")

        # If margins are in decimal form (0.25 = 25%), convert
        if op_margin and abs(op_margin) < 1:
            op_margin *= 100
        if net_margin and abs(net_margin) < 1:
            net_margin *= 100
        if roe and abs(roe) < 5:  # likely decimal
            roe *= 100
        if roa and abs(roa) < 5:
            roa *= 100

        # Rating
        if roe and roe > 20 and net_margin and net_margin > 15:
            rating = 5
        elif roe and roe > 10 and net_margin and net_margin > 8:
            rating = 4
        elif roe and roe > 5:
            rating = 3
        elif roe and roe > 0:
            rating = 2
        else:
            rating = 1

        metrics = [
            SnapshotMetricResponse(name="Operating Margin", value=_pct(op_margin)),
            SnapshotMetricResponse(name="Net Margin", value=_pct(net_margin)),
            SnapshotMetricResponse(name="Return on Equity (ROE)", value=_pct(roe)),
            SnapshotMetricResponse(name="Return on Assets (ROA)", value=_pct(roa)),
        ]
        return SnapshotItemResponse(
            category="Profitability", rating=rating, metrics=metrics
        )

    def _build_growth_snapshot(
        self, inc0: Dict, inc1: Dict, cf0: Dict, cf1: Dict,
        km: Dict, key_metrics: List[Dict],
    ) -> SnapshotItemResponse:
        def _yoy_growth(curr: Dict, prev: Dict, key: str) -> Optional[float]:
            c = _safe_float(curr, key)
            p = _safe_float(prev, key)
            if p and p != 0:
                return ((c - p) / abs(p)) * 100
            return None

        rev_growth = _yoy_growth(inc0, inc1, "revenue")
        eps_curr = _safe_float(km, "netIncomePerShare") if km else None
        km1 = key_metrics[1] if len(key_metrics) > 1 else {}
        eps_prev = _safe_float(km1, "netIncomePerShare") if km1 else None
        eps_growth = None
        if eps_curr and eps_prev and eps_prev != 0:
            eps_growth = ((eps_curr - eps_prev) / abs(eps_prev)) * 100

        fcf_growth = _yoy_growth(cf0, cf1, "freeCashFlow")
        op_growth = _yoy_growth(inc0, inc1, "operatingIncome")

        # Rating based on average of available growths
        growths = [g for g in [rev_growth, eps_growth, fcf_growth, op_growth] if g is not None]
        avg_growth = sum(growths) / len(growths) if growths else 0

        if avg_growth > 20:
            rating = 5
        elif avg_growth > 10:
            rating = 4
        elif avg_growth > 0:
            rating = 3
        elif avg_growth > -10:
            rating = 2
        else:
            rating = 1

        def _fmt_growth(v: Optional[float]) -> str:
            if v is None:
                return "—"
            sign = "+" if v >= 0 else ""
            return f"{sign}{v:.1f}%"

        metrics = [
            SnapshotMetricResponse(name="Revenue Growth (YoY)", value=_fmt_growth(rev_growth)),
            SnapshotMetricResponse(name="EPS Growth", value=_fmt_growth(eps_growth)),
            SnapshotMetricResponse(name="Free Cash Flow Growth (YoY)", value=_fmt_growth(fcf_growth)),
            SnapshotMetricResponse(name="Operating Income Growth", value=_fmt_growth(op_growth)),
        ]
        return SnapshotItemResponse(
            category="Growth", rating=rating, metrics=metrics
        )

    def _build_valuation_snapshot(
        self, fr: Dict, km: Dict, sector: str
    ) -> SnapshotItemResponse:
        pe = _safe_float(fr, "priceEarningsRatio") or _safe_float(km, "peRatio")
        ps = _safe_float(fr, "priceToSalesRatio") or _safe_float(km, "priceToSalesRatio")
        pfcf = _safe_float(fr, "priceToFreeCashFlowsRatio") or _safe_float(km, "pfcfRatio")
        ev_ebitda = _safe_float(fr, "enterpriseValueOverEBITDA") or _safe_float(km, "enterpriseValueOverEBITDA")

        sector_pe = _SECTOR_PE_AVG.get(sector, 20.0)
        sector_ps = _SECTOR_PS_AVG.get(sector, 3.0)

        # Rating: lower multiples = better (inverted)
        def _val_score(val: float, avg: float) -> int:
            if val <= 0:
                return 3  # can't rate negative
            ratio = val / avg
            if ratio <= 0.8:
                return 5
            if ratio <= 1.2:
                return 4
            if ratio <= 1.5:
                return 3
            if ratio <= 2.0:
                return 2
            return 1

        scores = []
        if pe > 0:
            scores.append(_val_score(pe, sector_pe))
        if ps > 0:
            scores.append(_val_score(ps, sector_ps))
        if pfcf > 0:
            scores.append(_val_score(pfcf, 25.0))
        if ev_ebitda > 0:
            scores.append(_val_score(ev_ebitda, 18.0))

        rating = round(sum(scores) / len(scores)) if scores else 3

        # Format with sector context
        def _val_ctx(val: float, avg: float, label: str) -> str:
            if val <= 0:
                return "—"
            ratio = val / avg if avg > 0 else 0
            return f"{ratio:.2f}x sector avg {avg:.0f}"

        metrics = [
            SnapshotMetricResponse(
                name=f"P/E ({_val_ctx(pe, sector_pe, 'P/E')})",
                value=f"{pe:.2f}" if pe > 0 else "—"
            ),
            SnapshotMetricResponse(
                name=f"P/S ({_val_ctx(ps, sector_ps, 'P/S')})",
                value=f"{ps:.2f}" if ps > 0 else "—"
            ),
            SnapshotMetricResponse(
                name=f"P/FCF ({_val_ctx(pfcf, 25.0, 'P/FCF')})",
                value=f"{pfcf:.2f}" if pfcf > 0 else "—"
            ),
            SnapshotMetricResponse(
                name=f"EV/EBITDA ({_val_ctx(ev_ebitda, 18.0, 'EV/EBITDA')})",
                value=f"{ev_ebitda:.2f}" if ev_ebitda > 0 else "—"
            ),
        ]
        return SnapshotItemResponse(
            category="Price", rating=rating, metrics=metrics
        )

    def _build_health_snapshot(
        self, bs: Dict, inc: Dict, cf: Dict, fr: Dict, km: Dict,
        market_cap: float,
    ) -> SnapshotItemResponse:
        # Altman Z-Score components
        total_assets = _safe_float(bs, "totalAssets")
        total_liab = _safe_float(bs, "totalLiabilities")
        current_assets = _safe_float(bs, "totalCurrentAssets")
        current_liab = _safe_float(bs, "totalCurrentLiabilities")
        retained_earnings = _safe_float(bs, "retainedEarnings")
        ebit = _safe_float(inc, "operatingIncome") or _safe_float(inc, "ebitda")
        revenue = _safe_float(inc, "revenue")

        z_score = None
        if total_assets and total_assets > 0 and total_liab > 0:
            wc = current_assets - current_liab
            z_score = (
                1.2 * (wc / total_assets)
                + 1.4 * (retained_earnings / total_assets if retained_earnings else 0)
                + 3.3 * (ebit / total_assets if ebit else 0)
                + 0.6 * (market_cap / total_liab if market_cap else 0)
                + 1.0 * (revenue / total_assets if revenue else 0)
            )
            z_score = round(z_score, 1)

        # Interest coverage
        interest_coverage = _safe_float(fr, "interestCoverage") or _safe_float(km, "interestCoverage")

        # Cash to Debt
        cash = _safe_float(bs, "cashAndCashEquivalents") or _safe_float(bs, "cashAndShortTermInvestments")
        total_debt = _safe_float(bs, "totalDebt") or _safe_float(bs, "longTermDebt")
        cash_to_debt = round(cash / total_debt, 2) if cash and total_debt and total_debt > 0 else None

        # FCF Margin
        fcf = _safe_float(cf, "freeCashFlow")
        fcf_margin = round((fcf / revenue) * 100, 1) if fcf and revenue and revenue > 0 else None

        # Asset Turnover
        asset_turnover = _safe_float(fr, "assetTurnover")

        # Rating based on Z-Score
        if z_score is not None:
            if z_score > 3.0:
                rating = 5
            elif z_score > 2.5:
                rating = 4
            elif z_score > 1.8:
                rating = 3
            elif z_score > 1.0:
                rating = 2
            else:
                rating = 1
        else:
            rating = 3  # neutral if can't compute

        metrics = [
            SnapshotMetricResponse(name="Altman Z-Score", value=f"{z_score}" if z_score else "—"),
            SnapshotMetricResponse(
                name="Interest Coverage",
                value=f"{interest_coverage:.1f}x" if interest_coverage else "—"
            ),
            SnapshotMetricResponse(name="Cash to Debt", value=f"{cash_to_debt}" if cash_to_debt else "—"),
            SnapshotMetricResponse(name="Free Cash Flow Margin", value=_pct(fcf_margin, 1)),
            SnapshotMetricResponse(
                name="Asset Turnover",
                value=f"{asset_turnover:.2f}" if asset_turnover else "—"
            ),
        ]
        return SnapshotItemResponse(
            category="Financial Health", rating=rating, metrics=metrics
        )

    def _build_ownership_snapshot(self, km: Dict) -> SnapshotItemResponse:
        inst_pct = km.get("institutionalOwnership") or km.get("institutionPercentage")
        insider_pct = km.get("insidersPercentage")

        # Format: if decimal (0.61) multiply by 100
        def _fmt_own(val) -> str:
            if val is None:
                return "—"
            v = float(val)
            if v < 1:
                v *= 100
            return f"{v:.1f}%"

        # Default neutral rating
        rating = 3

        metrics = [
            SnapshotMetricResponse(name="Institutional Ownership", value=_fmt_own(inst_pct)),
            SnapshotMetricResponse(name="Insider Ownership", value=_fmt_own(insider_pct)),
            SnapshotMetricResponse(name="Hedge Fund Holdings", value="—"),
            SnapshotMetricResponse(name="Top 10 Holders", value="—"),
            SnapshotMetricResponse(name="Institutional Activity", value="—"),
        ]
        return SnapshotItemResponse(
            category="Insiders & Ownership", rating=rating, metrics=metrics
        )

    # ── Sector & Industry ─────────────────────────────────────────

    def _build_sector_industry(
        self, profile: Dict, sector_perf: List[Dict]
    ) -> SectorIndustryResponse:
        sector_name = profile.get("sector") or "N/A"
        industry = profile.get("industry") or "N/A"

        sector_perf_value = 0.0
        if isinstance(sector_perf, list):
            for sp in sector_perf:
                sp_sector = sp.get("sector", "")
                if sp_sector.lower() == sector_name.lower():
                    sector_perf_value = _safe_float(sp, "changesPercentage")
                    break

        return SectorIndustryResponse(
            sector=sector_name,
            industry=industry,
            sector_performance=round(sector_perf_value, 2),
            industry_rank="--",
        )

    # ── Company Profile ───────────────────────────────────────────

    def _build_company_profile(self, profile: Dict) -> CompanyProfileResponse:
        city = profile.get("city") or ""
        state = profile.get("state") or ""
        country = profile.get("country") or ""
        if city and state:
            hq = f"{city}, {state}"
        elif city and country:
            hq = f"{city}, {country}"
        else:
            hq = country or "N/A"

        website = profile.get("website") or "N/A"
        if website.startswith("https://"):
            website = website[8:]
        elif website.startswith("http://"):
            website = website[7:]

        return CompanyProfileResponse(
            description=profile.get("description") or "No description available.",
            ceo=profile.get("ceo") or "N/A",
            founded=profile.get("ipoDate") or "N/A",
            employees=int(profile.get("fullTimeEmployees") or 0),
            headquarters=hq,
            website=website,
        )

    # ── Related Tickers ───────────────────────────────────────────

    async def _build_related_tickers(
        self, ticker: str
    ) -> List[RelatedTickerResponse]:
        try:
            peers = await self.fmp.get_stock_peers(ticker)
            peers = peers[:6]  # limit to 6
            if not peers:
                return []

            peer_quotes = await self.fmp.get_batch_quotes(peers)
            related = []
            for q in peer_quotes:
                if not isinstance(q, dict):
                    continue
                symbol = q.get("symbol", "")
                if not symbol:
                    continue
                related.append(RelatedTickerResponse(
                    symbol=symbol,
                    name=q.get("name") or symbol,
                    price=round(_safe_float(q, "price"), 2),
                    change_percent=round(_safe_float(q, "changesPercentage"), 2),
                ))
            return related
        except Exception as e:
            logger.warning(f"Related tickers failed for {ticker}: {e}")
            return []

    # ── Benchmark Summary ─────────────────────────────────────────

    def _build_benchmark_summary(
        self, stock_hist: List[Dict], spy_hist: List[Dict]
    ) -> Optional[BenchmarkSummaryResponse]:
        if not stock_hist or len(stock_hist) < 252:
            return None

        # Use 5-year data for annualized return if available
        days = min(len(stock_hist) - 1, 1260)  # up to 5 years
        years = days / 252

        stock_start = stock_hist[-(days + 1)].get("close") or stock_hist[-(days + 1)].get("adjClose")
        stock_end = stock_hist[-1].get("close") or stock_hist[-1].get("adjClose")

        if not stock_start or not stock_end or stock_start <= 0:
            return None

        stock_annual = ((stock_end / stock_start) ** (1 / years) - 1) * 100

        sp_annual = 0.0
        if spy_hist and len(spy_hist) > days:
            sp_start = spy_hist[-(days + 1)].get("close") or spy_hist[-(days + 1)].get("adjClose")
            sp_end = spy_hist[-1].get("close") or spy_hist[-1].get("adjClose")
            if sp_start and sp_end and sp_start > 0:
                sp_annual = ((sp_end / sp_start) ** (1 / years) - 1) * 100

        return BenchmarkSummaryResponse(
            avg_annual_return=round(stock_annual, 1),
            sp_benchmark=round(sp_annual, 1),
        )


# ── Singleton ────────────────────────────────────────────────────

_service: Optional[StockOverviewService] = None


def get_stock_overview_service() -> StockOverviewService:
    global _service
    if _service is None:
        _service = StockOverviewService()
    return _service
