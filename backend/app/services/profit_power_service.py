"""
Profit Power service — fetches income + cash flow statements from FMP,
computes margin percentages (gross, operating, FCF, net), and looks up
pre-computed sector median net margin from the sector_benchmarks table.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``profit_power_cache`` table (24-hour TTL + earnings-aware)

Matches the iOS ProfitPowerSectionData struct.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.utils.period_labels import quarterly_period_label
from app.schemas.profit_power import ProfitPowerDataPointSchema, ProfitPowerResponse
from app.services.sector_benchmark_lookup import get_sector_benchmark_lookup
from app.services.sector_benchmark_service import _normalize_sector

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
# Prevents thundering herd: if two requests arrive for the same ticker
# while the cache is cold, only one FMP fetch runs; the other awaits.
_inflight: Dict[str, asyncio.Future] = {}


# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    """Validate and normalize ticker symbol. Raises ValueError if invalid."""
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


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
    """Extract calendar year from the record.

    Prefers FMP's ``calendarYear`` field which correctly maps fiscal quarters
    to their reporting calendar year.
    """
    cal_year = record.get("calendarYear")
    if cal_year:
        return str(cal_year)
    date_str = record.get("date", "")
    if len(date_str) >= 4:
        return date_str[:4]
    return ""


def _annual_period_label(record: Dict[str, Any]) -> str:
    """Annual period label like '2024'."""
    return _extract_year(record)


def _quarterly_period_label(
    record: Dict[str, Any], use_fiscal_year: bool = False
) -> str:
    """Quarterly period label like \"Q1'24\"."""
    period = record.get("period", "")  # "Q1", "Q2", etc.
    # Off-calendar fiscal years (e.g. Oracle, FY ends May 31) get non-monotonic
    # quarter LABELS when the fiscal quarter is paired with the calendar year
    # (fiscal Q1/Aug shares a calendar year with the prior fiscal Q4/May).
    # use_fiscal_year pairs it with FMP's fiscalYear ("Q1'26") for DISPLAY only;
    # the sector-benchmark join stays on the calendar label (see `_match_period`).
    if use_fiscal_year and record.get("fiscalYear"):
        year = str(record.get("fiscalYear"))
    else:
        year = _extract_year(record)
    if len(year) >= 4:
        return f"{period}'{year[-2:]}"
    return f"{period}'{year}"


def _compute_margin(numerator: Optional[float], revenue: Optional[float]) -> Optional[float]:
    """Compute margin as percentage. Returns None if revenue is zero/missing."""
    if numerator is None or revenue is None or revenue == 0:
        return None
    return round(numerator / revenue * 100, 2)


def _build_margin_points(
    income_records: List[Dict[str, Any]],
    cashflow_records: List[Dict[str, Any]],
    is_quarterly: bool,
) -> List[Dict[str, Any]]:
    """
    Compute margin data points from income + cash flow statements.

    For each income statement period, computes gross/operating/net margins
    from income data and FCF margin from cash flow data (matched by date).
    """
    if not income_records:
        return []

    # Sort by date ascending
    sorted_income = sorted(income_records, key=lambda r: r.get("date", ""))

    # Build cash flow lookup by date for FCF matching
    cf_by_date: Dict[str, Dict[str, Any]] = {}
    for rec in cashflow_records:
        date = rec.get("date", "")
        if date:
            cf_by_date[date] = rec

    results = []
    for rec in sorted_income:
        revenue = _safe_float(rec, "revenue")
        if revenue is None or revenue == 0:
            continue

        if is_quarterly:
            label = quarterly_period_label(rec, use_fiscal_year=True)   # fiscal display
            match_period = _quarterly_period_label(rec)                 # calendar join key
        else:
            label = _annual_period_label(rec)
            match_period = label
        if not label:
            continue

        gross_profit = _safe_float(rec, "grossProfit")
        operating_income = _safe_float(rec, "operatingIncome")
        net_income = _safe_float(rec, "netIncome")

        # Match cash flow by date for FCF
        cf_rec = cf_by_date.get(rec.get("date", ""), {})
        free_cash_flow = _safe_float(cf_rec, "freeCashFlow")

        results.append({
            "period": label,                 # fiscal label (display)
            "_match_period": match_period,    # calendar label (sector-benchmark join)
            "gross_margin": _compute_margin(gross_profit, revenue),
            "operating_margin": _compute_margin(operating_income, revenue),
            "fcf_margin": _compute_margin(free_cash_flow, revenue),
            "net_margin": _compute_margin(net_income, revenue),
        })

    return results


def _find_next_earnings_date_simple(
    ec_records: List[Dict[str, Any]],
) -> Optional[str]:
    """Return the first future earnings date as yyyy-MM-dd, or None."""
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    for ec in sorted(ec_records, key=lambda r: r.get("date", "")):
        ec_date = (ec.get("date") or "")[:10]
        if not ec_date or ec_date <= today_str:
            continue
        # Skip if actual EPS is already reported (past quarter)
        if ec.get("eps") is not None:
            continue
        return ec_date
    return None


# ── Service ───────────────────────────────────────────────────────

class ProfitPowerService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_profit_power(self, ticker: str) -> ProfitPowerResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"profit_power:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Profit power in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache (run in thread to avoid blocking event loop) ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Profit power Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        # If another request is already fetching this ticker, wait for it
        if cache_key in _inflight:
            logger.info(f"Profit power in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        # Create a future so other concurrent requests can wait on us
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            # ── Cache miss: build from FMP ──
            logger.info(f"Profit power cache MISS for {ticker} — fetching from FMP")
            result, next_earnings = await self._build_profit_power(ticker)

            # Persist to Supabase in background thread (truly fire-and-forget)
            asyncio.get_running_loop().run_in_executor(
                None,
                self._upsert_supabase_cache_safe,
                ticker,
                result,
                next_earnings,
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

    def _check_supabase_cache(self, ticker: str) -> Optional[ProfitPowerResponse]:
        """Return cached response if fresh (< 24h and before next earnings).
        This is a synchronous method — call via asyncio.to_thread().
        """
        try:
            row = (
                self.supabase.table("profit_power_cache")
                .select("response_json, cached_at, next_earnings_date")
                .eq("ticker", ticker)
                .limit(1)
                .execute()
            )
            if not row.data:
                return None

            entry = row.data[0]
            cached_at_str = entry.get("cached_at")
            if not cached_at_str:
                return None

            # Parse cached_at and check 24-hour freshness
            cached_at = datetime.fromisoformat(cached_at_str.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - cached_at
            if age > timedelta(hours=24):
                logger.info(f"Supabase cache STALE (age={age}) for {ticker}")
                return None

            # Check next earnings date — invalidate if we've passed it
            next_earnings = entry.get("next_earnings_date")
            if next_earnings:
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                if today_str >= next_earnings:
                    logger.info(f"Supabase cache STALE (past earnings {next_earnings}) for {ticker}")
                    return None

            # Deserialize
            json_data = entry["response_json"]
            return ProfitPowerResponse(**json_data)

        except Exception as e:
            logger.warning(f"Supabase cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache_safe(
        self,
        ticker: str,
        result: ProfitPowerResponse,
        next_earnings: Optional[str],
    ) -> None:
        """Upsert to Supabase cache — safe wrapper that logs and swallows errors.
        This is a synchronous method — call via run_in_executor().
        """
        try:
            self.supabase.table("profit_power_cache").upsert(
                {
                    "ticker": ticker,
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                    "next_earnings_date": next_earnings,
                },
                on_conflict="ticker",
            ).execute()
        except Exception as e:
            logger.warning(f"Supabase upsert failed for {ticker}: {e}")

    # ── Builder ───────────────────────────────────────────────────

    async def _build_profit_power(
        self, ticker: str
    ) -> Tuple[ProfitPowerResponse, Optional[str]]:
        """Fetch income + cash flow, compute margins, look up sector benchmarks.
        Returns (response, next_earnings_date).
        """

        # Phase 1: parallel fetch — profile + income + cash flow + earnings calendar (6 FMP calls)
        (
            profile,
            annual_income,
            quarterly_income,
            annual_cashflow,
            quarterly_cashflow,
            ec_raw,
        ) = await asyncio.gather(
            self.fmp.get_company_profile(ticker),
            self.fmp.get_income_statement(ticker, period="annual", limit=16),
            self.fmp.get_income_statement(ticker, period="quarter", limit=80),
            self.fmp.get_cash_flow_statement(ticker, period="annual", limit=16),
            self.fmp.get_cash_flow_statement(ticker, period="quarter", limit=80),
            self.fmp.get_earning_calendar_full(ticker),
            return_exceptions=True,
        )

        # Handle failures gracefully
        if isinstance(profile, Exception):
            logger.warning(f"Profile fetch failed for {ticker}: {profile}")
            profile = {}
        if isinstance(annual_income, Exception):
            logger.error(f"Annual income fetch failed for {ticker}: {annual_income}")
            annual_income = []
        if isinstance(quarterly_income, Exception):
            logger.error(f"Quarterly income fetch failed for {ticker}: {quarterly_income}")
            quarterly_income = []
        if isinstance(annual_cashflow, Exception):
            logger.error(f"Annual cash flow fetch failed for {ticker}: {annual_cashflow}")
            annual_cashflow = []
        if isinstance(quarterly_cashflow, Exception):
            logger.error(f"Quarterly cash flow fetch failed for {ticker}: {quarterly_cashflow}")
            quarterly_cashflow = []
        if isinstance(ec_raw, Exception):
            logger.warning(f"Earnings calendar fetch failed for {ticker}: {ec_raw}")
            ec_raw = []

        # Phase 2: get sector from profile
        raw_sector = profile.get("sector", "") if isinstance(profile, dict) else ""
        sector = _normalize_sector(raw_sector)
        # Industry-relative benchmarks: prefer the company's INDUSTRY peer group,
        # fall back to its sector per (metric, period).
        industry = profile.get("industry", "") if isinstance(profile, dict) else ""

        # Phase 3: compute company margins for each period
        annual_points = _build_margin_points(annual_income, annual_cashflow, is_quarterly=False)
        quarterly_points = _build_margin_points(quarterly_income, quarterly_cashflow, is_quarterly=True)

        # Phase 4: look up pre-computed sector benchmarks for ALL four margins.
        # net is the original (the live detail chart's dashed line); gross/operating/
        # fcf were added so the report's per-metric Profitability drill-down can draw
        # a sector line for each margin. fcf_margin stays sparse until its historical
        # backfill runs — the chart degrades to a gapped line, not a crash.
        _MARGIN_BENCHMARK_METRICS = [
            "net_margin", "gross_margin", "operating_margin", "fcf_margin",
        ]
        benchmarks_annual: Dict[str, Dict[str, float]] = {}
        benchmarks_quarterly: Dict[str, Dict[str, float]] = {}
        # "industry" if the benchmark lines come from the company's industry peers,
        # "sector" on fallback — labels the drill-down legend/footer.
        peer_group_level: Optional[str] = None
        if sector:
            lookup = get_sector_benchmark_lookup()
            benchmarks_annual = lookup.get_benchmark_values(
                industry, sector, _MARGIN_BENCHMARK_METRICS, "annual"
            )
            benchmarks_quarterly = lookup.get_benchmark_values(
                industry, sector, _MARGIN_BENCHMARK_METRICS, "quarterly"
            )
            # One ticker-level peer group for the label, by majority of the margin
            # benchmark cells (rich lookup is a cache hit — same args as above).
            rich = lookup.get_benchmarks(
                industry, sector, _MARGIN_BENCHMARK_METRICS, "annual"
            )
            levels = [
                c.get("level")
                for periods in rich.values()
                for c in periods.values()
            ]
            if levels:
                peer_group_level = (
                    "industry"
                    if levels.count("industry") >= levels.count("sector")
                    else "sector"
                )

        # Phase 5: attach sector averages and build response.
        # sector_benchmarks stores every margin as a raw DECIMAL (0.12 = 12%), so
        # ×100 for the percentage scale the chart uses — uniform across all four.
        def _to_schemas(
            points: List[Dict],
            benchmarks: Dict[str, Dict[str, float]],
        ) -> List[ProfitPowerDataPointSchema]:
            net_b = benchmarks.get("net_margin", {})
            gross_b = benchmarks.get("gross_margin", {})
            op_b = benchmarks.get("operating_margin", {})
            fcf_b = benchmarks.get("fcf_margin", {})

            def _pct(table: Dict[str, float], key: str) -> Optional[float]:
                raw = table.get(key)
                return round(raw * 100, 2) if raw is not None else None

            schemas = []
            for p in points:
                # Match on the calendar key (_match_period); annual points have
                # no _match_period and fall back to period (also calendar).
                k = p.get("_match_period", p["period"])
                schemas.append(ProfitPowerDataPointSchema(
                    period=p["period"],
                    gross_margin=p["gross_margin"],
                    operating_margin=p["operating_margin"],
                    fcf_margin=p["fcf_margin"],
                    net_margin=p["net_margin"],
                    sector_average_net_margin=_pct(net_b, k),
                    sector_average_gross_margin=_pct(gross_b, k),
                    sector_average_operating_margin=_pct(op_b, k),
                    sector_average_fcf_margin=_pct(fcf_b, k),
                ))
            return schemas

        response = ProfitPowerResponse(
            symbol=ticker,
            annual=_to_schemas(annual_points, benchmarks_annual),
            quarterly=_to_schemas(quarterly_points, benchmarks_quarterly),
            peer_group_level=peer_group_level,
        )

        # Phase 6: extract next earnings date for cache invalidation
        next_earnings = _find_next_earnings_date_simple(
            ec_raw if isinstance(ec_raw, list) else []
        )

        return response, next_earnings


# ── Singleton ─────────────────────────────────────────────────────

_profit_power_service: Optional[ProfitPowerService] = None


def get_profit_power_service() -> ProfitPowerService:
    global _profit_power_service
    if _profit_power_service is None:
        _profit_power_service = ProfitPowerService()
    return _profit_power_service
