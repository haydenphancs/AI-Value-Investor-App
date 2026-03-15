"""
Sector Benchmark Service — Pre-computes median financial metrics per GICS sector
from S&P 500 constituents and stores them in Supabase.

Runs as a daily background job. Any service (Growth, Profit Power, Health Check, etc.)
can then look up sector benchmarks via a fast DB query instead of fetching peer data
on every request.
"""

import asyncio
import logging
import statistics
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client, FMPClient

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────

BATCH_SIZE = 10           # concurrent FMP calls per batch of companies
BATCH_DELAY_SECONDS = 1.0 # delay between batches to avoid rate limits
FMP_ANNUAL_LIMIT = 6      # 6 annual records → 5 YoY data points
FMP_QUARTERLY_LIMIT = 24  # ~6 years of quarterly data
MIN_SAMPLE_SIZE = 3       # minimum companies needed to compute a median
UPSERT_BATCH_SIZE = 100   # rows per Supabase upsert call

# FMP sector names → canonical app sector names
_FMP_SECTOR_MAP: Dict[str, str] = {
    "Technology": "Technology",
    "Information Technology": "Technology",
    "Healthcare": "Healthcare",
    "Health Care": "Healthcare",
    "Financial Services": "Financial Services",
    "Financials": "Financial Services",
    "Consumer Cyclical": "Consumer Cyclical",
    "Consumer Discretionary": "Consumer Cyclical",
    "Communication Services": "Communication Services",
    "Telecommunication Services": "Communication Services",
    "Industrials": "Industrials",
    "Consumer Defensive": "Consumer Defensive",
    "Consumer Staples": "Consumer Defensive",
    "Energy": "Energy",
    "Real Estate": "Real Estate",
    "Utilities": "Utilities",
    "Basic Materials": "Basic Materials",
    "Materials": "Basic Materials",
}

# Fallback tickers if FMP sp500-constituent endpoint is unavailable
_FALLBACK_SECTOR_TICKERS: Dict[str, List[str]] = {
    "Technology": ["AAPL", "MSFT", "NVDA", "AVGO", "CRM"],
    "Healthcare": ["UNH", "JNJ", "LLY", "PFE", "ABBV"],
    "Financial Services": ["JPM", "BAC", "WFC", "GS", "MS"],
    "Consumer Cyclical": ["AMZN", "TSLA", "HD", "MCD", "NKE"],
    "Communication Services": ["META", "GOOGL", "NFLX", "DIS", "CMCSA"],
    "Industrials": ["CAT", "UNP", "HON", "GE", "RTX"],
    "Consumer Defensive": ["PG", "KO", "PEP", "WMT", "COST"],
    "Energy": ["XOM", "CVX", "COP", "SLB", "EOG"],
    "Real Estate": ["AMT", "PLD", "CCI", "EQIX", "SPG"],
    "Utilities": ["NEE", "DUK", "SO", "D", "AEP"],
    "Basic Materials": ["LIN", "APD", "SHW", "ECL", "NEM"],
}

# All metrics to compute
METRIC_CONFIGS: List[Dict[str, str]] = [
    # YoY growth metrics (from income statement)
    {"name": "eps_yoy",              "source": "income",   "field": "epsDiluted",             "type": "yoy"},
    {"name": "revenue_yoy",          "source": "income",   "field": "revenue",                "type": "yoy"},
    {"name": "net_income_yoy",       "source": "income",   "field": "netIncome",              "type": "yoy"},
    {"name": "operating_income_yoy", "source": "income",   "field": "operatingIncome",        "type": "yoy"},
    {"name": "gross_profit_yoy",     "source": "income",   "field": "grossProfit",            "type": "yoy"},
    # YoY growth from cash flow
    {"name": "fcf_yoy",             "source": "cashflow",  "field": "freeCashFlow",           "type": "yoy"},
    # Profit Power (direct ratio values)
    {"name": "gross_margin",        "source": "ratios",    "field": "grossProfitMargin",      "type": "direct"},
    {"name": "operating_margin",    "source": "ratios",    "field": "operatingProfitMargin",  "type": "direct"},
    {"name": "net_margin",          "source": "ratios",    "field": "netProfitMargin",        "type": "direct"},
    {"name": "roa",                 "source": "ratios",    "field": "returnOnAssets",         "type": "direct"},
    {"name": "roe",                 "source": "ratios",    "field": "returnOnEquity",         "type": "direct"},
    {"name": "roic",                "source": "ratios",    "field": "returnOnCapitalEmployed","type": "direct"},
    # Health Check (direct ratio values)
    {"name": "current_ratio",       "source": "ratios",    "field": "currentRatio",           "type": "direct"},
    {"name": "debt_to_equity",      "source": "ratios",    "field": "debtEquityRatio",        "type": "direct"},
    {"name": "interest_coverage",   "source": "ratios",    "field": "interestCoverage",       "type": "direct"},
    {"name": "debt_to_assets",      "source": "ratios",    "field": "debtRatio",              "type": "direct"},
    # Valuation
    {"name": "pe_ratio",            "source": "ratios",    "field": "priceEarningsRatio",     "type": "direct"},
    {"name": "pb_ratio",            "source": "ratios",    "field": "priceToBookRatio",       "type": "direct"},
    {"name": "dividend_yield",      "source": "ratios",    "field": "dividendYield",          "type": "direct"},
    # Efficiency
    {"name": "asset_turnover",      "source": "ratios",    "field": "assetTurnover",          "type": "direct"},
]


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    """Safely extract a float value from a dict."""
    val = record.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _extract_year(record: Dict[str, Any]) -> str:
    """Extract calendar year from the date field (e.g., '2024' from '2024-09-28')."""
    date_str = record.get("date", "")
    if len(date_str) >= 4:
        return date_str[:4]
    return ""


def _annual_period_label(record: Dict[str, Any]) -> str:
    """Annual period label like '2024'."""
    return _extract_year(record)


def _quarterly_period_label(record: Dict[str, Any]) -> str:
    """Quarterly period label like \"Q1'24\"."""
    period = record.get("period", "")  # "Q1", "Q2", etc.
    year = _extract_year(record)
    if len(year) >= 4:
        return f"{period}'{year[-2:]}"
    return f"{period}'{year}"


def _compute_yoy_for_records(
    records: List[Dict[str, Any]],
    field: str,
    is_quarterly: bool,
) -> Dict[str, float]:
    """
    Compute YoY growth % for each period in the records.
    Returns {period_label: yoy_percent}.
    """
    if not records:
        return {}

    sorted_recs = sorted(records, key=lambda r: r.get("date", ""))
    result: Dict[str, float] = {}

    if is_quarterly:
        # Build lookup: (period, year) -> record
        lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for rec in sorted_recs:
            p = rec.get("period", "")
            cy = _extract_year(rec)
            lookup[(p, cy)] = rec

        for rec in sorted_recs:
            period = rec.get("period", "")
            cal_year = _extract_year(rec)
            try:
                prev_year = str(int(cal_year) - 1)
            except ValueError:
                continue

            prev_rec = lookup.get((period, prev_year))
            if prev_rec is None:
                continue

            current_val = _safe_float(rec, field)
            prev_val = _safe_float(prev_rec, field)
            if current_val is not None and prev_val is not None and prev_val != 0:
                yoy = round((current_val - prev_val) / abs(prev_val) * 100, 2)
                label = _quarterly_period_label(rec)
                if label:
                    result[label] = yoy
    else:
        # Annual: compare consecutive sorted records
        for i in range(1, len(sorted_recs)):
            rec = sorted_recs[i]
            prev_rec = sorted_recs[i - 1]
            current_val = _safe_float(rec, field)
            prev_val = _safe_float(prev_rec, field)
            if current_val is not None and prev_val is not None and prev_val != 0:
                yoy = round((current_val - prev_val) / abs(prev_val) * 100, 2)
                label = _annual_period_label(rec)
                if label:
                    result[label] = yoy

    return result


def _normalize_sector(raw_sector: str) -> str:
    """Map FMP sector name to canonical app sector name."""
    return _FMP_SECTOR_MAP.get(raw_sector, raw_sector)


# ── Service ───────────────────────────────────────────────────────

class SectorBenchmarkService:
    def __init__(self) -> None:
        self.fmp: FMPClient = get_fmp_client()
        self.supabase = get_supabase()

    def _benchmarks_are_fresh(self, max_age_hours: float = 23.0) -> bool:
        """Check if benchmarks were computed recently enough to skip recomputation."""
        try:
            response = (
                self.supabase.table("sector_benchmarks")
                .select("computed_at")
                .order("computed_at", desc=True)
                .limit(1)
                .execute()
            )
            if not response.data:
                return False
            last_computed = response.data[0]["computed_at"]
            # Parse ISO timestamp from Supabase
            from datetime import datetime, timezone
            if last_computed.endswith("Z"):
                last_computed = last_computed.replace("Z", "+00:00")
            last_dt = datetime.fromisoformat(last_computed)
            age_hours = (datetime.now(timezone.utc) - last_dt).total_seconds() / 3600
            if age_hours < max_age_hours:
                logger.info(
                    f"Sector benchmarks are fresh ({age_hours:.1f}h old), skipping recomputation"
                )
                return True
            return False
        except Exception as e:
            logger.warning(f"Could not check benchmark freshness: {e}")
            return False

    async def compute_all_benchmarks(self, force: bool = False) -> Dict[str, Any]:
        """Main entry: fetch constituents, group by sector, compute medians, upsert."""
        if not force and self._benchmarks_are_fresh():
            return {"rows_upserted": 0, "skipped": True, "reason": "benchmarks are fresh"}

        start = time.time()
        logger.info("Starting sector benchmark computation...")

        # Step 1: get S&P 500 constituents grouped by sector
        sector_tickers = await self._get_sector_tickers()
        logger.info(
            f"Sectors to process: {list(sector_tickers.keys())} "
            f"({sum(len(v) for v in sector_tickers.values())} total companies)"
        )

        # Step 2: compute benchmarks for each sector
        total_upserted = 0
        for sector, tickers in sector_tickers.items():
            try:
                logger.info(f"Computing benchmarks for {sector} sector ({len(tickers)} companies)...")
                count = await self._compute_sector(sector, tickers)
                total_upserted += count
                logger.info(f"  {sector}: {count} benchmark rows upserted")
            except Exception as e:
                logger.error(f"  {sector} sector failed: {e}", exc_info=True)

        elapsed = time.time() - start
        logger.info(f"Sector benchmarks complete: {total_upserted} rows in {elapsed:.1f}s")
        return {"rows_upserted": total_upserted, "elapsed_seconds": round(elapsed, 1)}

    async def _get_sector_tickers(self) -> Dict[str, List[str]]:
        """Fetch S&P 500 constituents and group by canonical sector name."""
        constituents = await self.fmp.get_sp500_constituents()

        if not constituents:
            logger.warning("FMP sp500-constituent returned empty, using fallback tickers")
            return dict(_FALLBACK_SECTOR_TICKERS)

        sector_map: Dict[str, List[str]] = {}
        for c in constituents:
            raw_sector = c.get("sector", "")
            symbol = c.get("symbol", "")
            if not raw_sector or not symbol:
                continue
            sector = _normalize_sector(raw_sector)
            sector_map.setdefault(sector, []).append(symbol)

        if not sector_map:
            logger.warning("No valid sectors from constituents, using fallback")
            return dict(_FALLBACK_SECTOR_TICKERS)

        return sector_map

    async def _compute_sector(self, sector: str, tickers: List[str]) -> int:
        """Fetch financial data for all tickers in a sector, compute medians, upsert."""
        all_company_data: List[Dict[str, List]] = []

        for batch_start in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[batch_start:batch_start + BATCH_SIZE]
            tasks = [self._fetch_company_data(ticker) for ticker in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"  Skipping {batch[i]}: {result}")
                    continue
                if result:
                    all_company_data.append(result)

            # Delay between batches (but not after the last one)
            if batch_start + BATCH_SIZE < len(tickers):
                await asyncio.sleep(BATCH_DELAY_SECONDS)

        if not all_company_data:
            logger.warning(f"  No company data collected for {sector}")
            return 0

        # Compute medians for each metric × period_type × period_label
        now = datetime.now(timezone.utc).isoformat()
        rows_to_upsert: List[Dict[str, Any]] = []

        for metric_config in METRIC_CONFIGS:
            for period_type in ("annual", "quarterly"):
                period_values = self._collect_metric_values(
                    all_company_data, metric_config, period_type
                )
                for period_label, values in period_values.items():
                    if len(values) < MIN_SAMPLE_SIZE:
                        continue
                    rows_to_upsert.append({
                        "sector": sector,
                        "metric_name": metric_config["name"],
                        "period_type": period_type,
                        "period_label": period_label,
                        "median_value": round(statistics.median(values), 4),
                        "sample_size": len(values),
                        "computed_at": now,
                    })

        # Upsert in batches
        upserted = 0
        for i in range(0, len(rows_to_upsert), UPSERT_BATCH_SIZE):
            batch = rows_to_upsert[i:i + UPSERT_BATCH_SIZE]
            try:
                self.supabase.table("sector_benchmarks").upsert(
                    batch,
                    on_conflict="sector,metric_name,period_type,period_label",
                ).execute()
                upserted += len(batch)
            except Exception as e:
                logger.error(f"  Upsert batch failed for {sector}: {e}")

        return upserted

    async def _fetch_company_data(self, ticker: str) -> Dict[str, List]:
        """Fetch income, cash flow, and ratios for one company (annual + quarterly)."""
        results = await asyncio.gather(
            self.fmp.get_income_statement(ticker, period="annual", limit=FMP_ANNUAL_LIMIT),
            self.fmp.get_income_statement(ticker, period="quarter", limit=FMP_QUARTERLY_LIMIT),
            self.fmp.get_cash_flow_statement(ticker, period="annual", limit=FMP_ANNUAL_LIMIT),
            self.fmp.get_cash_flow_statement(ticker, period="quarter", limit=FMP_QUARTERLY_LIMIT),
            self.fmp.get_financial_ratios(ticker, period="annual", limit=FMP_ANNUAL_LIMIT),
            self.fmp.get_financial_ratios(ticker, period="quarter", limit=FMP_QUARTERLY_LIMIT),
            return_exceptions=True,
        )

        def _safe_list(r: Any) -> List:
            return r if isinstance(r, list) else []

        return {
            "income_annual": _safe_list(results[0]),
            "income_quarterly": _safe_list(results[1]),
            "cashflow_annual": _safe_list(results[2]),
            "cashflow_quarterly": _safe_list(results[3]),
            "ratios_annual": _safe_list(results[4]),
            "ratios_quarterly": _safe_list(results[5]),
        }

    def _collect_metric_values(
        self,
        all_company_data: List[Dict[str, List]],
        metric_config: Dict[str, str],
        period_type: str,
    ) -> Dict[str, List[float]]:
        """
        For a given metric, collect values per period_label across all companies.
        Returns {"2024": [12.5, 8.3, ...], "2023": [...], ...}
        """
        source = metric_config["source"]   # "income", "cashflow", "ratios"
        field = metric_config["field"]
        metric_type = metric_config["type"]  # "yoy" or "direct"
        is_quarterly = period_type == "quarterly"
        data_key = f"{source}_{period_type}"

        period_values: Dict[str, List[float]] = {}

        for company_data in all_company_data:
            records = company_data.get(data_key, [])
            if not records:
                continue

            if metric_type == "yoy":
                # Compute per-company YoY, then collect
                yoy_points = _compute_yoy_for_records(records, field, is_quarterly)
                for label, yoy_val in yoy_points.items():
                    period_values.setdefault(label, []).append(yoy_val)
            else:
                # Direct value extraction
                for rec in records:
                    val = _safe_float(rec, field)
                    if val is None:
                        continue
                    label = (
                        _quarterly_period_label(rec) if is_quarterly
                        else _annual_period_label(rec)
                    )
                    if label:
                        period_values.setdefault(label, []).append(val)

        return period_values


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[SectorBenchmarkService] = None


def get_sector_benchmark_service() -> SectorBenchmarkService:
    global _service
    if _service is None:
        _service = SectorBenchmarkService()
    return _service
