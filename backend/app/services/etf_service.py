"""
ETF Detail Service — aggregates FMP data, computes derived stats,
and generates AI-powered snapshot analysis via Gemini.

Serves the ETFDetailView screen on iOS.
"""

import asyncio
import json
import logging
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
    _cache[key] = (time.time(), value)


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


# ── Helpers ──────────────────────────────────────────────────────


def _fmt(value: Optional[float], decimals: int = 2) -> str:
    """Format a number with commas and N decimal places."""
    if value is None:
        return "—"
    if abs(value) >= 1_000_000_000_000:
        return f"${value / 1_000_000_000_000:.1f}T"
    if abs(value) >= 1_000_000_000:
        return f"${value / 1_000_000_000:.1f}B"
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:.1f}M"
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


def _format_date_readable(date_str: str) -> str:
    """Convert YYYY-MM-DD to human-readable format like 'Dec 20, 2025'."""
    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return dt.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return date_str or "—"


# ── Main service ─────────────────────────────────────────────────


class ETFService:
    """Aggregates FMP data + Gemini AI for the ETF Detail screen."""

    def __init__(self):
        self.fmp: FMPClient = get_fmp_client()

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

        # ── Step 1: Parallel FMP fetches ──────────────────────────
        today = datetime.now(tz=timezone.utc).date()
        from_date = (today - timedelta(days=365 * 5)).isoformat()
        to_date = today.isoformat()

        (
            quote, profile, etf_info, holders, sector_weights,
            dividends, hist_raw, news_raw,
        ) = await asyncio.gather(
            self.fmp.get_stock_price_quote(symbol),
            self.fmp.get_company_profile(symbol),
            self.fmp.get_etf_info(symbol),
            self.fmp.get_etf_holders(symbol, limit=20),
            self.fmp.get_etf_sector_weightings(symbol),
            self.fmp.get_dividend_history(symbol, limit=20),
            self.fmp.get_historical_prices(symbol, from_date, to_date),
            self.fmp.get_stock_news(symbol, limit=10),
            return_exceptions=True,
        )

        # Handle exceptions gracefully — each source fails independently
        if isinstance(quote, Exception):
            logger.error(f"Quote fetch failed for {symbol}: {quote}")
            quote = {}
        if isinstance(profile, Exception):
            logger.error(f"Profile fetch failed for {symbol}: {profile}")
            profile = {}
        if isinstance(etf_info, Exception):
            logger.error(f"ETF info fetch failed for {symbol}: {etf_info}")
            etf_info = {}
        if isinstance(holders, Exception):
            logger.error(f"Holders fetch failed for {symbol}: {holders}")
            holders = []
        if isinstance(sector_weights, Exception):
            logger.error(f"Sector weights fetch failed for {symbol}: {sector_weights}")
            sector_weights = []
        if isinstance(dividends, Exception):
            logger.error(f"Dividends fetch failed for {symbol}: {dividends}")
            dividends = []
        if isinstance(hist_raw, Exception):
            logger.error(f"Historical prices fetch failed for {symbol}: {hist_raw}")
            hist_raw = {}
        if isinstance(news_raw, Exception):
            logger.error(f"News fetch failed for {symbol}: {news_raw}")
            news_raw = []

        # Parse historical prices (sorted oldest-first)
        historical: List[Dict] = []
        if isinstance(hist_raw, dict):
            historical = hist_raw.get("historical", [])
        elif isinstance(hist_raw, list):
            historical = hist_raw
        historical.sort(key=lambda p: p.get("date", ""))

        # ── Step 2: Extract quote data ────────────────────────────
        price = float(quote.get("price") or 0)
        change = float(quote.get("change") or 0)
        change_pct = float(quote.get("changesPercentage") or 0)
        prev_close = float(quote.get("previousClose") or 0)
        volume = quote.get("volume") or 0
        avg_volume = quote.get("avgVolume") or 0
        year_high = float(quote.get("yearHigh") or 0)
        year_low = float(quote.get("yearLow") or 0)
        pe = float(quote.get("pe") or 0)
        market_cap = quote.get("marketCap") or 0
        beta = float(profile.get("beta") or quote.get("beta") or 0)

        # ETF-specific data
        expense_ratio = float(etf_info.get("expenseRatio") or 0)
        nav = float(etf_info.get("navPrice") or etf_info.get("nav") or price)
        total_assets = float(
            etf_info.get("totalAssets") or etf_info.get("aum")
            or etf_info.get("netAssets") or market_cap or 0
        )
        holdings_count = int(etf_info.get("holdingsCount") or etf_info.get("numberOfHoldings") or 0)
        etf_company = (
            etf_info.get("etfCompany") or etf_info.get("companyName")
            or profile.get("companyName") or "—"
        )
        asset_class = etf_info.get("assetClass") or "Equity"
        inception_date_raw = (
            etf_info.get("inceptionDate") or profile.get("ipoDate") or ""
        )
        domicile = etf_info.get("domicile") or "United States"
        index_tracked = etf_info.get("indexTracked") or etf_info.get("index") or "—"
        website = profile.get("website") or ""
        if website.startswith("https://"):
            website = website[8:]
        elif website.startswith("http://"):
            website = website[7:]
        description = profile.get("description") or etf_info.get("description") or ""
        dividend_yield = float(
            etf_info.get("dividendYield")
            or quote.get("dividendYield")
            or profile.get("lastDiv")
            or 0
        )
        turnover = float(etf_info.get("turnover") or 0)

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
            pe=pe,
            holdings_count=holdings_count,
            turnover=turnover,
            inception_date=inception_date_raw,
            asset_class=asset_class,
            domicile=domicile,
            index_tracked=index_tracked,
        )

        # ── Step 5: Build performance periods ─────────────────────
        one_month = _compute_return(historical, 21)
        ytd = _compute_ytd_return(historical)
        one_year = _compute_return(historical, 252)
        three_year = _compute_return(historical, 252 * 3) if len(historical) > 252 * 2 else None
        five_year = _compute_return(historical, 252 * 5) if len(historical) > 252 * 3 else None
        ten_year = _compute_return(historical, 252 * 10) if len(historical) > 252 * 5 else None

        perf_periods = self._build_performance_periods(
            one_month=one_month,
            ytd=ytd,
            one_year=one_year,
            three_year=three_year,
            five_year=five_year,
            ten_year=ten_year,
        )

        # ── Step 6: Build holdings & sector data ──────────────────
        top_holdings = self._build_top_holdings(holders)
        top_sectors = self._build_sector_weights(sector_weights)
        concentration = self._build_concentration(top_holdings)

        # ── Step 7: Build dividend data ───────────────────────────
        dividend_payments = self._build_dividend_history(dividends)

        # ── Step 8: Generate AI snapshots ─────────────────────────
        ai_snapshot = await self._generate_ai_snapshot(
            symbol=symbol,
            name=etf_company,
            description=description,
            expense_ratio=expense_ratio,
            dividend_yield=dividend_yield,
            total_assets=total_assets,
            holdings_count=holdings_count,
            top_holdings=top_holdings,
            top_sectors=top_sectors,
            concentration_weight=concentration.weight,
            beta=beta,
            pe=pe,
            asset_class=asset_class,
            index_tracked=index_tracked,
        )

        # ── Step 9: Build net yield ───────────────────────────────
        fee_per_10k = expense_ratio * 100  # expense_ratio is in %, so 0.0945% → $9.45
        yield_per_10k = dividend_yield * 100  # 1.22% → $122
        multiplier = int(round(dividend_yield / expense_ratio)) if expense_ratio > 0 else 0

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
            fee_context=f"You pay ${fee_per_10k:.2f} per year on a $10,000 investment.",
            dividend_yield=dividend_yield,
            pay_frequency=pay_frequency,
            yield_context=f"You earn ~${yield_per_10k:.0f} per year on a $10,000 investment.",
            verdict=(
                f"This fund pays you {multiplier}x more in dividends than it charges in fees."
                if multiplier > 0
                else "This fund charges nothing in fees."
                if expense_ratio == 0
                else "Dividend yield does not meaningfully exceed the expense ratio."
            ),
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
            expense_ratio=f"{expense_ratio}%" if expense_ratio else "—",
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

        # ── Step 14: Benchmark summary ────────────────────────────
        avg_annual = None
        if one_year is not None:
            avg_annual = round(one_year, 2)
        benchmark = BenchmarkSummaryResponse(
            avg_annual_return=avg_annual or 0,
            sp_benchmark=10.5,  # S&P 500 long-term average
        )

        # ── Assemble response ─────────────────────────────────────
        return ETFDetailResponse(
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
            identity_rating=ai_snapshot["identity_rating"],
            strategy=ai_snapshot["strategy"],
            net_yield=net_yield,
            holdings_risk=holdings_risk,
            etf_profile=etf_profile,
            related_etfs=related_etfs,
            benchmark_summary=benchmark,
            news_articles=news_articles,
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
            if p.get("date", "") >= cutoff:
                close = p.get("close") or p.get("adjClose")
                if close and close > 0:
                    result.append({
                        "date": p.get("date"),
                        "open": p.get("open"),
                        "high": p.get("high"),
                        "low": p.get("low"),
                        "close": round(float(close), 2),
                        "volume": p.get("volume"),
                    })
        return result

    # ── Key statistics builder ────────────────────────────────────

    def _build_key_statistics(
        self, *, nav, total_assets, expense_ratio, avg_volume,
        dividend_yield, year_high, year_low, beta, pe,
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
            KeyStatisticItem(label="Avg. Volume", value=_fmt(avg_volume, 0)),
            KeyStatisticItem(label="Dividend Yield", value=_pct(dividend_yield) if dividend_yield else "—"),
            KeyStatisticItem(label="52W High", value=_fmt_dollar(year_high)),
            KeyStatisticItem(label="52W Low", value=_fmt_dollar(year_low)),
            KeyStatisticItem(label="Beta", value=f"{beta:.2f}" if beta else "—"),
            KeyStatisticItem(label="P/E Ratio", value=f"{pe:.2f}" if pe else "—"),
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
                KeyStatisticItem(label="Avg. Volume", value=_fmt(avg_volume, 0)),
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
                KeyStatisticItem(label="P/E Ratio", value=f"{pe:.2f}" if pe else "—"),
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

    # ── Performance periods builder ───────────────────────────────

    def _build_performance_periods(
        self, *, one_month, ytd, one_year, three_year, five_year, ten_year,
    ) -> List[PerformancePeriodResponse]:
        periods = []
        for label, val in [
            ("1 Month", one_month),
            ("YTD", ytd),
            ("1 Year", one_year),
            ("3 Years", three_year),
            ("5 Years", five_year),
            ("10 Years", ten_year),
        ]:
            if val is not None:
                periods.append(PerformancePeriodResponse(
                    label=label,
                    change_percent=round(val, 2),
                    vs_market_percent=round(val, 2),  # vs self (ETF is its own benchmark ref)
                ))
        return periods

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
                weight=round(float(weight), 2),
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
                weight=round(float(weight), 2),
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

        if total_weight > 35:
            insight = (
                f"Over a third of your money is in just {n} companies. "
                "If these big names stumble, this fund feels it."
            )
        elif total_weight > 20:
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
        if "bond" in ac or "fixed" in ac:
            equities, bonds, crypto, cash = 0, 95, 0, 5
        elif "crypto" in ac or "bitcoin" in ac:
            equities, bonds, crypto, cash = 0, 0, 95, 5
        elif "commodity" in ac or "gold" in ac:
            equities, bonds, crypto, cash = 0, 0, 0, 100  # commodities mapped as cash/other
        elif "real estate" in ac or "reit" in ac:
            equities, bonds, crypto, cash = 95, 0, 0, 5
        else:
            equities, bonds, crypto, cash = 99.5, 0, 0, 0.5

        return ETFAssetAllocationResponse(
            equities=equities,
            bonds=bonds,
            crypto=crypto,
            cash=cash,
            total_assets=_fmt(total_assets),
        )

    # ── Related ETFs builder ──────────────────────────────────────

    async def _build_related_etfs(
        self, symbol: str
    ) -> List[RelatedTickerResponse]:
        """Fetch quotes for related ETFs."""
        related_symbols = _RELATED_ETFS.get(
            symbol, _DEFAULT_RELATED
        )
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
                price=float(res.get("price") or 0),
                change_percent=round(float(res.get("changesPercentage") or 0), 2),
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

    # ── AI Snapshot Generation ────────────────────────────────────

    async def _generate_ai_snapshot(
        self, *, symbol, name, description, expense_ratio, dividend_yield,
        total_assets, holdings_count, top_holdings, top_sectors,
        concentration_weight, beta, pe, asset_class, index_tracked,
    ) -> Dict[str, Any]:
        """
        Generate AI-powered ETF snapshot using Gemini.
        Cached for 1 hour. Falls back to computed defaults on failure.
        """
        cache_key = f"etf_snapshot_{symbol}"
        cached = _cache_get(cache_key, _AI_CACHE_TTL_SECONDS)
        if cached:
            return cached

        # Build default (always works)
        default = self._build_default_snapshot(
            symbol=symbol,
            name=name,
            expense_ratio=expense_ratio,
            dividend_yield=dividend_yield,
            total_assets=total_assets,
            holdings_count=holdings_count,
            top_holdings=top_holdings,
            concentration_weight=concentration_weight,
            beta=beta,
            asset_class=asset_class,
            index_tracked=index_tracked,
        )

        # Try Gemini for richer analysis
        try:
            gemini = get_gemini_client()

            holdings_text = ", ".join(
                f"{h.symbol} ({h.weight}%)" for h in top_holdings[:10]
            )
            sectors_text = ", ".join(
                f"{s.name} ({s.weight}%)" for s in top_sectors[:5]
            )

            prompt = f"""Analyze this ETF and generate a JSON snapshot.

ETF: {symbol}
Name: {name}
Description: {description[:300] if description else 'N/A'}
Asset Class: {asset_class}
Index Tracked: {index_tracked}
Expense Ratio: {expense_ratio}%
Dividend Yield: {dividend_yield}%
Total Assets: ${total_assets:,.0f}
Holdings Count: {holdings_count}
Beta: {beta:.2f}
P/E Ratio: {pe:.2f}
Top Holdings: {holdings_text}
Top Sectors: {sectors_text}
Top 10 Concentration: {concentration_weight:.1f}%

Generate this JSON (output ONLY valid JSON, no markdown):
{{
  "identity_rating": {{
    "score": <int 1-5 based on AUM, tracking error, liquidity, longevity>,
    "esg_rating": "<A-F letter grade>",
    "volatility_label": "<Low Volatility|Moderate Volatility|High Volatility>"
  }},
  "strategy": {{
    "hook": "<max 120 chars, one punchy sentence in plain English about what this fund does>",
    "tags": ["<2-4 tags from: Passive, Active, Index, Large Cap, Mid Cap, Small Cap, Blend, Growth, Value, Sector, Thematic, Bond, International, Dividend, ESG>"]
  }}
}}

RULES:
- score: 5=institutional-grade blue chip ETF, 4=strong, 3=average, 2=niche/risky, 1=speculative
- hook must not exceed 120 characters
- Be honest and direct. No marketing fluff."""

            ai_response = await gemini.generate_text(
                prompt=prompt,
                system_instruction=(
                    "You are an ETF analyst writing for novice investors. "
                    "Output ONLY valid JSON. No markdown, no commentary."
                ),
                model_name="gemini-2.0-flash",
            )

            text = ai_response.get("text", "").strip()
            # Strip markdown code fences if present
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text.rsplit("```", 1)[0]
            text = text.strip()

            parsed = json.loads(text)

            ir = parsed.get("identity_rating", {})
            st = parsed.get("strategy", {})

            score = int(ir.get("score", 3))
            score = max(1, min(5, score))

            esg = ir.get("esg_rating", "B")
            if esg not in ("A", "B", "C", "D", "F"):
                esg = "B"

            vol_label = ir.get("volatility_label", "Moderate Volatility")
            if vol_label not in ("Low Volatility", "Moderate Volatility", "High Volatility"):
                vol_label = "Moderate Volatility"

            hook = st.get("hook", "")[:120]
            tags = st.get("tags", [])
            if not tags:
                tags = ["Index", "Blend"]

            result = {
                "identity_rating": ETFIdentityRatingResponse(
                    score=score,
                    max_score=5,
                    esg_rating=esg,
                    volatility_label=vol_label,
                ),
                "strategy": ETFStrategyResponse(
                    hook=hook or default["strategy"].hook,
                    tags=tags,
                ),
            }
            _cache_set(cache_key, result)
            logger.info(f"Generated AI snapshot for ETF {symbol}")
            return result

        except Exception as e:
            logger.warning(
                f"Gemini ETF snapshot failed for {symbol}, using defaults: {e}"
            )
            return default

    def _build_default_snapshot(
        self, *, symbol, name, expense_ratio, dividend_yield,
        total_assets, holdings_count, top_holdings, concentration_weight,
        beta, asset_class, index_tracked,
    ) -> Dict[str, Any]:
        """Build reasonable defaults without AI."""
        # Score based on AUM
        if total_assets > 50_000_000_000:
            score = 5
        elif total_assets > 10_000_000_000:
            score = 4
        elif total_assets > 1_000_000_000:
            score = 3
        elif total_assets > 100_000_000:
            score = 2
        else:
            score = 1

        # Volatility based on beta
        if beta < 0.8:
            vol_label = "Low Volatility"
        elif beta < 1.2:
            vol_label = "Moderate Volatility"
        else:
            vol_label = "High Volatility"

        # Strategy tags
        tags = []
        ac = asset_class.lower()
        if "equity" in ac:
            tags.append("Index")
            tags.append("Blend")
        elif "bond" in ac or "fixed" in ac:
            tags.append("Bond")
        if index_tracked and index_tracked != "—":
            tags.append("Passive")
        if not tags:
            tags = ["Index", "Blend"]

        # Hook
        if index_tracked and index_tracked != "—":
            hook = f"Tracks the {index_tracked}. {holdings_count} holdings for broad market exposure."
        else:
            hook = f"A {asset_class.lower()} fund with {holdings_count} holdings."

        return {
            "identity_rating": ETFIdentityRatingResponse(
                score=score,
                max_score=5,
                esg_rating="B",
                volatility_label=vol_label,
            ),
            "strategy": ETFStrategyResponse(
                hook=hook[:120],
                tags=tags[:4],
            ),
        }


# ── Singleton ────────────────────────────────────────────────────

_etf_service: Optional[ETFService] = None


def get_etf_service() -> ETFService:
    global _etf_service
    if _etf_service is None:
        _etf_service = ETFService()
    return _etf_service
