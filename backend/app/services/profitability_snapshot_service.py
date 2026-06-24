"""
Profitability Snapshot service — computes sector-relative profitability
ratings using Operating Margin, Net Margin, ROE, ROA compared against
pre-computed sector medians from the sector_benchmarks table.

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

def _first_valid(*vals: Optional[float]) -> Optional[float]:
    """Return the first non-None value, or None if all are None."""
    for v in vals:
        if v is not None:
            return v
    return None


def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    val = record.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _to_pct(val: Optional[float]) -> Optional[float]:
    """Convert decimal to percentage if needed. FMP ratios are decimals (0.25 = 25%)."""
    if val is None:
        return None
    if abs(val) < 1:
        return round(val * 100, 2)
    return round(val, 2)


def _fmt_pct(val: Optional[float]) -> str:
    """Format as percentage string for display."""
    if val is None:
        return "—"
    return f"{val:.2f}%"


def _profitability_score(value: Optional[float], sector_median_decimal: Optional[float]) -> int:
    """
    Score 1-5 based on how a company's metric compares to sector median.

    Args:
        value: Company metric as percentage (e.g., 25.0 for 25%)
        sector_median_decimal: Sector median as decimal from benchmarks (e.g., 0.15 for 15%)
    """
    if value is None:
        return 3  # neutral if no data

    if sector_median_decimal is None or sector_median_decimal == 0:
        # No sector benchmark — use absolute thresholds as fallback
        if value >= 20:
            return 5
        if value >= 12:
            return 4
        if value >= 5:
            return 3
        if value >= 0:
            return 2
        return 1

    sector_pct = sector_median_decimal * 100  # convert to percentage
    if sector_pct == 0:
        return 3

    ratio = value / sector_pct
    if ratio >= 1.5:
        return 5  # 50%+ above sector
    if ratio >= 1.1:
        return 4  # 10-50% above
    if ratio >= 0.8:
        return 3  # within 20% of sector
    if ratio >= 0.5:
        return 2  # 20-50% below
    return 1      # 50%+ below sector


# Single-value sector comparisons use the mature-period picker
# (`mature_benchmark_value`) so a thin just-closed year never decides the
# comparison; the old `_get_latest_benchmark` max-year helper had no floor.


def _label_with_sector(
    label: str, val: Optional[float], sector_decimal: Optional[float],
) -> str:
    """Append sector context to a profitability metric label so the iOS
    `displayLabel` regex picks it up and renders the " *" footnote.

    `val` is the company's value in percentage form (e.g. 30.0 for 30%).
    `sector_decimal` is the sector median in decimal form (e.g. 0.15 for 15%).
    Returns the bare label when sector data is missing — iOS then renders
    no asterisk for that row, matching today's Valuation/Health behaviour.
    """
    if val is None or sector_decimal is None or sector_decimal <= 0:
        return label
    sector_pct = sector_decimal * 100
    if sector_pct == 0:
        return label
    ratio = val / sector_pct
    return f"{label} ({ratio:.2f}x sector avg {sector_pct:.1f}%)"


# ── Service ───────────────────────────────────────────────────────

class ProfitabilitySnapshotService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_profitability_snapshot(self, ticker: str) -> SnapshotItemResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"prof_snapshot:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Profitability snapshot in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Profitability snapshot Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Profitability snapshot in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Profitability snapshot cache MISS for {ticker} — computing")
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
        """Return cached response if fresh (< 24h). Synchronous — call via to_thread."""
        try:
            row = (
                self.supabase.table("snapshot_cache")
                .select("response_json, cached_at")
                .eq("ticker", ticker)
                .eq("category", "Profitability")
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
                logger.info(f"Profitability snapshot Supabase STALE (age={age}) for {ticker}")
                return None

            json_data = entry["response_json"]
            return SnapshotItemResponse(**json_data)

        except Exception as e:
            logger.warning(f"Profitability snapshot cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache(self, ticker: str, result: SnapshotItemResponse) -> None:
        """Upsert to Supabase. Synchronous — call via run_in_executor."""
        try:
            self.supabase.table("snapshot_cache").upsert(
                {
                    "ticker": ticker,
                    "category": "Profitability",
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker,category",
            ).execute()
        except Exception as e:
            logger.warning(f"Profitability snapshot upsert failed for {ticker}: {e}")

    # ── Core computation ──────────────────────────────────────────

    async def _compute(self, ticker: str) -> SnapshotItemResponse:
        """Reuse ProfitPowerService (Financials tab) for margins, FMP for ROE/ROA.

        Ratios endpoint is fetched in parallel as a fallback: ProfitPowerService
        depends on revenue being non-zero on the income statement, which silently
        skips tickers whose latest filing lacks a clean `revenue` field. The
        FMP `/ratios` direct fields (`operatingProfitMargin` etc.) avoid that
        silent-skip and keep the card populated.
        """
        from app.services.profit_power_service import get_profit_power_service

        # TTM endpoints for ratios + key_metrics so ROE/ROA/margins reflect
        # the last 4 quarters instead of an up-to-12-months-stale fiscal-year
        # snapshot. profit_power still drives margins when its annual data
        # is fresh; TTM ratios are the fallback that matches what Webull
        # and other consumer apps display.
        pp_task = get_profit_power_service().get_profit_power(ticker)
        km_task = self.fmp.get_key_metrics_ttm(ticker)
        profile_task = self.fmp.get_company_profile(ticker)
        ratios_task = self.fmp.get_ratios_ttm(ticker)

        results = await asyncio.gather(
            pp_task, km_task, profile_task, ratios_task, return_exceptions=True
        )

        # Margins from profit_power (exact same as Financials tab)
        pp = results[0] if not isinstance(results[0], Exception) else None
        gross_margin = None
        op_margin = None
        net_margin = None
        if pp and pp.annual:
            latest = pp.annual[-1]  # sorted oldest→newest
            gross_margin = latest.gross_margin
            op_margin = latest.operating_margin
            net_margin = latest.net_margin

        # ROE/ROA from FMP key-metrics (ratios endpoint returns None for these)
        km_raw = results[1]
        km = {}
        if isinstance(km_raw, list) and km_raw:
            km = km_raw[0]
        elif isinstance(km_raw, dict):
            km = km_raw

        # Field names: /key-metrics-ttm uses TTM-suffixed names; legacy
        # bare names are kept as fallbacks in case FMP rolls the schema.
        roe = _to_pct(_first_valid(
            _safe_float(km, "returnOnEquityTTM"),
            _safe_float(km, "returnOnEquity"),
        ))
        roa_raw = _first_valid(
            _safe_float(km, "returnOnAssetsTTM"),
            _safe_float(km, "returnOnAssets"),
            _safe_float(km, "returnOnTangibleAssetsTTM"),
            _safe_float(km, "returnOnTangibleAssets"),
        )
        roa = _to_pct(roa_raw)

        # Sector for benchmark comparison
        profile_raw = results[2]
        profile = {}
        if isinstance(profile_raw, dict):
            profile = profile_raw
        elif isinstance(profile_raw, list) and profile_raw:
            profile = profile_raw[0]

        # Ratios fallback for margins (when ProfitPowerService skips a record
        # because of missing/zero revenue). `_to_pct` normalizes decimal vs %.
        ratios_raw = results[3] if not isinstance(results[3], Exception) else []
        ratios0: Dict[str, Any] = {}
        if isinstance(ratios_raw, list) and ratios_raw:
            ratios0 = ratios_raw[0]
        elif isinstance(ratios_raw, dict):
            ratios0 = ratios_raw

        if gross_margin is None:
            gross_margin = _to_pct(_first_valid(
                _safe_float(ratios0, "grossProfitMarginTTM"),
                _safe_float(ratios0, "grossProfitMargin"),
            ))
        if op_margin is None:
            op_margin = _to_pct(_first_valid(
                _safe_float(ratios0, "operatingProfitMarginTTM"),
                _safe_float(ratios0, "operatingProfitMargin"),
            ))
        if net_margin is None:
            net_margin = _to_pct(_first_valid(
                _safe_float(ratios0, "netProfitMarginTTM"),
                _safe_float(ratios0, "netProfitMargin"),
            ))

        raw_sector = profile.get("sector", "")
        sector = _normalize_sector(raw_sector) if raw_sector else ""
        # Industry-relative: prefer INDUSTRY peers, fall back to sector per cell.
        industry = profile.get("industry", "") if isinstance(profile, dict) else ""

        # CURRENT benchmark per metric: TTM row if present, else latest mature
        # annual value (fallback). {metric: value | None}.
        cur_bench: Dict[str, Optional[float]] = {}
        if sector:
            try:
                lookup = get_sector_benchmark_lookup()
                cur_bench = lookup.get_current_benchmark_values(
                    industry,
                    sector,
                    ["gross_margin", "operating_margin", "net_margin", "roe", "roa"],
                )
            except Exception as e:
                logger.warning(f"Sector benchmark lookup failed for {ticker}: {e}")

        # Score each metric against the CURRENT (TTM-first) sector/industry median
        sector_gross = cur_bench.get("gross_margin")
        sector_op = cur_bench.get("operating_margin")
        sector_net = cur_bench.get("net_margin")
        sector_roe = cur_bench.get("roe")
        sector_roa = cur_bench.get("roa")

        score_gross = _profitability_score(gross_margin, sector_gross)
        score_op = _profitability_score(op_margin, sector_op)
        score_net = _profitability_score(net_margin, sector_net)
        score_roe = _profitability_score(roe, sector_roe)
        score_roa = _profitability_score(roa, sector_roa)

        # Weighted: Gross 15% + Op 20% + Net 25% + ROE 25% + ROA 15% = 100%.
        # Net and ROE keep the largest share because they reflect bottom-line
        # efficiency and capital return — the two metrics value investors weight most.
        weighted = (
            score_gross * 0.15
            + score_op * 0.20
            + score_net * 0.25
            + score_roe * 0.25
            + score_roa * 0.15
        )
        rating = max(1, min(5, round(weighted)))

        metrics = [
            SnapshotMetricResponse(
                name=_label_with_sector("Gross Margin", gross_margin, sector_gross),
                value=_fmt_pct(gross_margin),
            ),
            SnapshotMetricResponse(
                name=_label_with_sector("Operating Margin", op_margin, sector_op),
                value=_fmt_pct(op_margin),
            ),
            SnapshotMetricResponse(
                name=_label_with_sector("Net Margin", net_margin, sector_net),
                value=_fmt_pct(net_margin),
            ),
            SnapshotMetricResponse(
                name=_label_with_sector("Return on Equity (ROE)", roe, sector_roe),
                value=_fmt_pct(roe),
            ),
            SnapshotMetricResponse(
                name=_label_with_sector("Return on Assets (ROA)", roa, sector_roa),
                value=_fmt_pct(roa),
            ),
        ]

        return SnapshotItemResponse(
            category="Profitability",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
        )


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[ProfitabilitySnapshotService] = None


def get_profitability_snapshot_service() -> ProfitabilitySnapshotService:
    global _service
    if _service is None:
        _service = ProfitabilitySnapshotService()
    return _service
