"""
Valuation Snapshot service — computes sector-relative valuation ratings
for P/E, P/S, P/FCF, and EV/EBITDA using pre-computed sector medians
from the sector_benchmarks table.

Uses the same data as the Financials tab (FMP financial_ratios + key_metrics)
so the user sees consistent numbers.

Uses a two-tier cache-aside pattern:
  Tier 1 — in-memory dict (5-minute TTL)
  Tier 2 — Supabase ``snapshot_cache`` table (24-hour TTL)

Matches the iOS SnapshotItemDTO struct.
"""

import asyncio
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.schemas.stock_overview import SnapshotItemResponse, SnapshotMetricResponse
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
_inflight: Dict[str, asyncio.Future] = {}

# ── Ticker validation ────────────────────────────────────────────
_TICKER_RE = re.compile(r"^[A-Z]{1,5}(-[A-Z]{1,2})?$")


def _validate_ticker(ticker: str) -> str:
    ticker = ticker.upper().strip()
    if not _TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker symbol: {ticker!r}")
    return ticker


# ── Helpers ───────────────────────────────────────────────────────

def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    val = record.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _fmt_ratio(val: Optional[float]) -> str:
    """Format a valuation ratio for display."""
    if val is None or val <= 0:
        return "—"
    return f"{val:.2f}"


def _fmt_pfcf(pfcf: Optional[float], km: Dict[str, Any]) -> str:
    """P/FCF is undefined when free cash flow is negative. Surface that
    explicitly as "Neg." so the user knows the company is burning cash —
    different signal from "data missing" ("—"). Detected via the FMP
    `freeCashFlowYield` field which carries the sign of FCF.
    """
    if pfcf is not None and pfcf > 0:
        return f"{pfcf:.2f}"
    fcf_yield = _safe_float(km, "freeCashFlowYield")
    if fcf_yield is not None and fcf_yield < 0:
        return "Neg."
    return "—"


def _valuation_score(value: Optional[float], sector_median: Optional[float]) -> int:
    """
    Score 1-5 based on how a company's valuation compares to sector median.
    Lower multiples = better value (inverted scoring).
    """
    if value is None or value <= 0:
        return 3  # neutral if no data

    if sector_median is None or sector_median <= 0:
        # Absolute fallback thresholds (no sector data available)
        # These are general "reasonable" ranges for any sector
        if value < 10:
            return 5
        if value < 18:
            return 4
        if value < 28:
            return 3
        if value < 40:
            return 2
        return 1

    ratio = value / sector_median
    if ratio <= 0.7:
        return 5   # 30%+ cheaper than sector
    if ratio <= 0.9:
        return 4   # 10-30% cheaper
    if ratio <= 1.2:
        return 3   # within 20% of sector
    if ratio <= 1.5:
        return 2   # 20-50% more expensive
    return 1        # 50%+ more expensive


def _get_latest_benchmark(benchmarks: Dict[str, Dict[str, float]], metric: str) -> Optional[float]:
    """Get the most recent year's benchmark value for a metric."""
    metric_data = benchmarks.get(metric, {})
    if not metric_data:
        return None
    latest_year = max(metric_data.keys())
    return metric_data[latest_year]


def _sector_ctx(val: Optional[float], sector_median: Optional[float]) -> str:
    """Build sector context string like '1.2x sector avg 25'. Returns '' if unavailable."""
    if val is None or val <= 0:
        return ""
    if sector_median is None or sector_median <= 0:
        return ""
    ratio = val / sector_median
    return f"{ratio:.2f}x sector avg {sector_median:.0f}"


def _metric_name(label: str, val: Optional[float], sector_median: Optional[float]) -> str:
    """Build metric name with optional sector context. No '(—)' when data is missing."""
    ctx = _sector_ctx(val, sector_median)
    if ctx:
        return f"{label} ({ctx})"
    return label


def _fmt_pct(val: Optional[float]) -> str:
    """Format a decimal-form percentage for display (e.g. 0.0425 → '4.25%')."""
    if val is None or val <= 0:
        return "N/A"
    return f"{val * 100:.2f}%"


def _sector_ctx_pct(val: Optional[float], sector_median: Optional[float]) -> str:
    """Sector context for percentage metrics. Both inputs are decimals
    (e.g. 0.0425 for 4.25%). Output displays the median as a percent."""
    if val is None or val <= 0:
        return ""
    if sector_median is None or sector_median <= 0:
        return ""
    ratio = val / sector_median
    return f"{ratio:.2f}x sector avg {sector_median * 100:.2f}%"


def _metric_name_pct(label: str, val: Optional[float], sector_median: Optional[float]) -> str:
    """Same as `_metric_name` but for decimal percentage metrics."""
    ctx = _sector_ctx_pct(val, sector_median)
    if ctx:
        return f"{label} ({ctx})"
    return label


# ── Service ───────────────────────────────────────────────────────

class ValuationSnapshotService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_valuation_snapshot(self, ticker: str) -> SnapshotItemResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"val_snapshot:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Valuation snapshot in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Valuation snapshot Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Valuation snapshot in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Valuation snapshot cache MISS for {ticker} — computing")
            result = await self._compute(ticker)

            # Persist to Supabase in background thread
            asyncio.get_running_loop().run_in_executor(
                None, self._upsert_supabase_cache, ticker, result,
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

    def _check_supabase_cache(self, ticker: str) -> Optional[SnapshotItemResponse]:
        try:
            row = (
                self.supabase.table("snapshot_cache")
                .select("response_json, cached_at")
                .eq("ticker", ticker)
                .eq("category", "Price")
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
            if age > timedelta(hours=24):
                logger.info(f"Valuation snapshot Supabase STALE (age={age}) for {ticker}")
                return None

            json_data = entry["response_json"]
            return SnapshotItemResponse(**json_data)

        except Exception as e:
            logger.warning(f"Valuation snapshot cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache(self, ticker: str, result: SnapshotItemResponse) -> None:
        try:
            self.supabase.table("snapshot_cache").upsert(
                {
                    "ticker": ticker,
                    "category": "Price",
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker,category",
            ).execute()
        except Exception as e:
            logger.warning(f"Valuation snapshot upsert failed for {ticker}: {e}")

    # ── Core computation ──────────────────────────────────────────

    async def _compute(self, ticker: str) -> SnapshotItemResponse:
        """Fetch same data as Financials tab and score against sector benchmarks."""

        # Parallel fetch — same FMP endpoints the Financials tab uses
        # Include cash_flow + income for fallback P/FCF and EV/EBITDA computation
        results = await asyncio.gather(
            self.fmp.get_company_profile(ticker),
            self.fmp.get_financial_ratios(ticker, period="annual", limit=1),
            self.fmp.get_key_metrics(ticker, period="annual", limit=1),
            self.fmp.get_cash_flow_statement(ticker, period="annual", limit=1),
            self.fmp.get_income_statement(ticker, period="annual", limit=1),
            return_exceptions=True,
        )

        def _parse_first(raw) -> Dict:
            if isinstance(raw, list) and raw:
                return raw[0]
            if isinstance(raw, dict):
                return raw
            return {}

        profile = _parse_first(results[0]) if not isinstance(results[0], Exception) else {}
        fr = _parse_first(results[1]) if not isinstance(results[1], Exception) else {}
        km = _parse_first(results[2]) if not isinstance(results[2], Exception) else {}
        cf = _parse_first(results[3]) if not isinstance(results[3], Exception) else {}
        inc = _parse_first(results[4]) if not isinstance(results[4], Exception) else {}

        # Extract valuation metrics (fallback chain: ratios → key_metrics → compute from raw)
        def _first_valid(*vals) -> Optional[float]:
            for v in vals:
                if v is not None:
                    return v
            return None

        pe = _first_valid(_safe_float(fr, "priceToEarningsRatio"), _safe_float(km, "peRatio"))
        ps = _first_valid(_safe_float(fr, "priceToSalesRatio"), _safe_float(km, "priceToSalesRatio"))
        pb = _first_valid(_safe_float(fr, "priceToBookRatio"), _safe_float(km, "pbRatio"))
        pfcf = _first_valid(_safe_float(fr, "priceToFreeCashFlowsRatio"), _safe_float(km, "pfcfRatio"))
        ev_ebitda = _first_valid(_safe_float(fr, "enterpriseValueOverEBITDA"), _safe_float(km, "enterpriseValueOverEBITDA"))

        # Market-cap fallback chain — FMP sometimes omits it from key_metrics
        # for less-covered tickers. Profile.mktCap and key_metrics.marketCap
        # are typically identical; profile is the more reliable surface.
        mcap = _safe_float(km, "marketCap") or _safe_float(profile, "mktCap")

        # Fallback: compute P/FCF from marketCap / freeCashFlow. When FCF is
        # negative the ratio is meaningless (negative multiples don't compare),
        # so we leave pfcf as None and the renderer shows "—".
        if pfcf is None:
            fcf = _safe_float(cf, "freeCashFlow")
            if mcap and mcap > 0 and fcf and fcf > 0:
                pfcf = round(mcap / fcf, 2)

        # Fallback: compute EV/EBITDA from enterpriseValue / ebitda. When the
        # `ebitda` field is missing, reconstruct it from operatingIncome plus
        # depreciation & amortization — both already on the existing payloads
        # so no extra FMP call is needed.
        if ev_ebitda is None:
            ev = _safe_float(km, "enterpriseValue")
            ebitda = _safe_float(inc, "ebitda")
            if ebitda is None or ebitda <= 0:
                op_income = _safe_float(inc, "operatingIncome")
                d_and_a = (
                    _safe_float(cf, "depreciationAndAmortization")
                    or _safe_float(inc, "depreciationAndAmortization")
                )
                if op_income is not None and d_and_a is not None:
                    ebitda = op_income + d_and_a
            if ev and ev > 0 and ebitda and ebitda > 0:
                ev_ebitda = round(ev / ebitda, 2)

        # Earnings Yield (decimal form, e.g. 0.0425 for 4.25%). Fallback chain:
        #   1. ratios.earningsYield
        #   2. key_metrics.earningsYield
        #   3. 1/PE  (matches the canonical formula)
        #   4. netIncome / marketCap
        ey = _first_valid(
            _safe_float(fr, "earningsYield"),
            _safe_float(km, "earningsYield"),
        )
        if ey is None and pe is not None and pe > 0:
            ey = round(1.0 / pe, 4)
        if ey is None:
            ni = _safe_float(inc, "netIncome")
            if ni is not None and ni > 0 and mcap and mcap > 0:
                ey = round(ni / mcap, 4)

        # Get sector for benchmark comparison
        raw_sector = profile.get("sector", "")
        sector = _normalize_sector(raw_sector) if raw_sector else ""

        # Fetch pre-computed sector benchmarks (dynamic, not hardcoded)
        benchmarks: Dict[str, Dict[str, float]] = {}
        if sector:
            try:
                lookup = get_sector_benchmark_lookup()
                benchmarks = lookup.get_sector_benchmarks(
                    sector,
                    ["pe_ratio", "ps_ratio", "pb_ratio", "pfcf_ratio", "ev_ebitda", "earnings_yield"],
                    "annual",
                )
            except Exception as e:
                logger.warning(f"Sector benchmark lookup failed for {ticker}: {e}")

        sector_pe = _get_latest_benchmark(benchmarks, "pe_ratio")
        sector_ps = _get_latest_benchmark(benchmarks, "ps_ratio")
        sector_pb = _get_latest_benchmark(benchmarks, "pb_ratio")
        sector_pfcf = _get_latest_benchmark(benchmarks, "pfcf_ratio")
        sector_ev = _get_latest_benchmark(benchmarks, "ev_ebitda")
        sector_ey = _get_latest_benchmark(benchmarks, "earnings_yield")

        # Score each metric against sector median (lower = better)
        score_pe = _valuation_score(pe, sector_pe)
        score_ps = _valuation_score(ps, sector_ps)
        score_pb = _valuation_score(pb, sector_pb)
        score_pfcf = _valuation_score(pfcf, sector_pfcf)
        score_ev = _valuation_score(ev_ebitda, sector_ev)

        # Weighted average: P/E 25%, P/B 15%, P/S 15%, P/FCF 20%, EV/EBITDA 25%
        weighted = (
            score_pe * 0.25
            + score_pb * 0.15
            + score_ps * 0.15
            + score_pfcf * 0.20
            + score_ev * 0.25
        )
        rating = max(1, min(5, round(weighted)))

        metrics = [
            SnapshotMetricResponse(
                name=_metric_name("P/E", pe, sector_pe),
                value=_fmt_ratio(pe),
            ),
            SnapshotMetricResponse(
                name=_metric_name("P/B", pb, sector_pb),
                value=_fmt_ratio(pb),
            ),
            SnapshotMetricResponse(
                name=_metric_name("P/S", ps, sector_ps),
                value=_fmt_ratio(ps),
            ),
            SnapshotMetricResponse(
                name=_metric_name("P/FCF", pfcf, sector_pfcf),
                value=_fmt_pfcf(pfcf, km),
            ),
            SnapshotMetricResponse(
                name=_metric_name("EV/EBITDA", ev_ebitda, sector_ev),
                value=_fmt_ratio(ev_ebitda),
            ),
            # Earnings Yield: informational — not part of the composite
            # star-rating (which weights P/E, P/B, P/S, P/FCF, EV/EBITDA only)
            # to keep historical ratings comparable.
            SnapshotMetricResponse(
                name=_metric_name_pct("Earnings Yield", ey, sector_ey),
                value=_fmt_pct(ey),
            ),
        ]

        return SnapshotItemResponse(
            category="Price",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
        )


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[ValuationSnapshotService] = None


def get_valuation_snapshot_service() -> ValuationSnapshotService:
    global _service
    if _service is None:
        _service = ValuationSnapshotService()
    return _service
