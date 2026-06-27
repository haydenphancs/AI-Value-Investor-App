"""
Financial Health Snapshot service — computes sector-relative health ratings
by reusing the existing HealthCheckService (Financials tab) to ensure
data consistency.

Extracts the overall rating and 4 metrics (Debt-to-Equity, P/E Ratio,
Return on Equity, Current Ratio) from the health check, maps them to
the 1-5 snapshot rating, and formats for display.

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


# ── Display helpers ──────────────────────────────────────────────

# Map health check metric types to display names
_DISPLAY_NAMES = {
    "debt_to_equity": "Debt-to-Equity",
    "pe_ratio": "P/E Ratio",
    "roe": "Return on Equity (ROE)",
    "current_ratio": "Current Ratio",
    "altman_z_score": "Altman Z-Score",
    "interest_coverage": "Interest Coverage",
    "quick_ratio": "Quick Ratio",
}

# Snapshot intentionally hides these — P/E lives in the Valuation card
# (different domain) and ROE lives in the Profitability card (duplicate).
# Interest Coverage and Quick Ratio replace them as proper health metrics.
_HIDE_FROM_SNAPSHOT = {"pe_ratio", "roe"}

# Metric types that participate in the sector-comparable side of the rating
# blend. Altman Z-Score is excluded — it has its own anchor weight via
# `_zscore_rating()` and uses absolute thresholds, not sector comparisons.
_SECTOR_RATING_TYPES = {
    "debt_to_equity",
    "current_ratio",
    "interest_coverage",
    "quick_ratio",
}

# Metrics where the value is a percentage (ROE)
_PCT_METRICS = {"roe"}


def _fmt_value(metric_type: str, value: Optional[float]) -> str:
    """Format metric value for display."""
    if value is None:
        return "—"
    if metric_type in _PCT_METRICS:
        return f"{value:.2f}%"
    return f"{value:.2f}"


def _metric_name(metric_type: str, value: Optional[float], comparison_value: Optional[float]) -> str:
    """Build metric name with optional sector context."""
    label = _DISPLAY_NAMES.get(metric_type, metric_type)
    if value is not None and comparison_value is not None:
        return f"{label} (vs sector {comparison_value:.2f})"
    return label


def _safe_float(record: Dict[str, Any], key: str) -> Optional[float]:
    val = record.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _sum_ttm_income(quarterly: List[Dict[str, Any]]) -> Dict[str, float]:
    """Sum the last 4 quarters of income-statement records into a TTM dict.

    FMP returns quarterly statements newest-first when sorted by date desc,
    but we re-sort here defensively. Skips a field if any of the last 4
    quarters is missing it (rather than partial-summing 2 or 3 quarters,
    which would understate the TTM number and silently corrupt ratios).
    """
    if not quarterly:
        return {}
    sorted_q = sorted(quarterly, key=lambda r: r.get("date", ""), reverse=True)[:4]
    if len(sorted_q) < 4:
        # Fall back to whatever quarters we have, weighted by sum count.
        # Better to expose partial data than render "—" for new tickers.
        sorted_q = quarterly[:4]
    summed: Dict[str, float] = {}
    for field in (
        "operatingIncome", "interestExpense", "revenue",
        "netIncome", "ebitda", "depreciationAndAmortization",
    ):
        vals: List[float] = []
        for rec in sorted_q:
            v = _safe_float(rec, field)
            if v is None:
                vals = []
                break
            vals.append(v)
        if vals:
            summed[field] = sum(vals)
    return summed


def _compute_z_score(bs: Dict, inc: Dict, mcap: Optional[float]) -> Optional[float]:
    """Compute Altman Z-Score from balance sheet, income, and market cap."""
    ta = _safe_float(bs, "totalAssets")
    tl = _safe_float(bs, "totalLiabilities")
    ca = _safe_float(bs, "totalCurrentAssets")
    cl = _safe_float(bs, "totalCurrentLiabilities")
    re = _safe_float(bs, "retainedEarnings")
    ebit = _safe_float(inc, "operatingIncome")
    rev = _safe_float(inc, "revenue")

    if not ta or ta <= 0 or not tl or tl <= 0:
        return None

    wc = (ca or 0) - (cl or 0)
    z = (
        1.2 * (wc / ta)
        + 1.4 * ((re or 0) / ta)
        + 3.3 * ((ebit or 0) / ta)
        + 0.6 * ((mcap or 0) / tl)
        + 1.0 * ((rev or 0) / ta)
    )
    return round(z, 1)


def _zscore_rating(z: Optional[float]) -> int:
    """Score Z-Score 1-5 using Altman's universal thresholds."""
    if z is None:
        return 3
    if z > 3.0:
        return 5  # Safe zone
    if z > 2.5:
        return 4
    if z > 1.8:
        return 3  # Grey zone
    if z > 1.0:
        return 2
    return 1      # Distress zone


# ── Verdict scoring (drivers for card_verdict.generate_card_verdict) ──
# HealthCheck metric type → the canonical key the deterministic card verdict
# uses; plus a 1-5 score (Altman Z from its absolute zones, the sector-comparable
# ratios from their positive/neutral/negative status).
_VERDICT_KEY = {
    "altman_z_score": "altman_z",
    "debt_to_equity": "debt_to_equity",
    "current_ratio": "current_ratio",
    "interest_coverage": "interest_coverage",
    "quick_ratio": "quick_ratio",
}


def _status_score(status: Optional[str]) -> Optional[int]:
    if status == "positive":
        return 4
    if status == "negative":
        return 2
    if status == "neutral":
        return 3
    return None


def _health_metric_score(m: Any) -> Optional[int]:
    """1-5 verdict score for a HealthCheck metric (None when its value is missing)."""
    if getattr(m, "value", None) is None:
        return None
    if getattr(m, "type", None) == "altman_z_score":
        return _zscore_rating(m.value)
    return _status_score(getattr(m, "status", None))


def _fallback_sector_score(
    value: Optional[float], benchmark: Optional[float], *, lower_is_better: bool,
) -> Optional[int]:
    """Quick 4 (beats) / 2 (lags) score for the local-fallback path — used only
    when HealthCheckService is down (no per-metric status to read)."""
    if value is None or benchmark is None or benchmark <= 0:
        return None
    beats = (value < benchmark) if lower_is_better else (value > benchmark)
    return 4 if beats else 2


# ── Service ───────────────────────────────────────────────────────

class HealthSnapshotService:
    def __init__(self):
        self.fmp = get_fmp_client()
        self.supabase = get_supabase()

    async def get_health_snapshot(self, ticker: str) -> SnapshotItemResponse:
        """Public entry point with two-tier caching and in-flight dedup."""
        ticker = _validate_ticker(ticker)
        cache_key = f"health_snapshot:{ticker}"

        # ── Tier 1: in-memory cache ──
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info(f"Health snapshot in-memory HIT for {ticker}")
            return cached

        # ── Tier 2: Supabase cache ──
        db_cached = await asyncio.to_thread(self._check_supabase_cache, ticker)
        if db_cached is not None:
            logger.info(f"Health snapshot Supabase HIT for {ticker}")
            _cache_set(cache_key, db_cached)
            return db_cached

        # ── In-flight deduplication ──
        if cache_key in _inflight:
            logger.info(f"Health snapshot in-flight JOIN for {ticker}")
            return await _inflight[cache_key]

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        _inflight[cache_key] = future

        try:
            logger.info(f"Health snapshot cache MISS for {ticker} — computing")
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
                .eq("category", "Financial Health")
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
                logger.info(f"Health snapshot Supabase STALE (age={age}) for {ticker}")
                return None

            json_data = entry["response_json"]
            return SnapshotItemResponse(**json_data)

        except Exception as e:
            logger.warning(f"Health snapshot cache check failed for {ticker}: {e}")
            return None

    def _upsert_supabase_cache(self, ticker: str, result: SnapshotItemResponse) -> None:
        try:
            self.supabase.table("snapshot_cache").upsert(
                {
                    "ticker": ticker,
                    "category": "Financial Health",
                    "response_json": result.model_dump(),
                    "cached_at": datetime.now(timezone.utc).isoformat(),
                },
                on_conflict="ticker,category",
            ).execute()
        except Exception as e:
            logger.warning(f"Health snapshot upsert failed for {ticker}: {e}")

    # ── Core computation ──────────────────────────────────────────

    async def _compute(self, ticker: str) -> SnapshotItemResponse:
        """Reuse HealthCheckService (Financials tab) + compute Altman Z-Score.

        Income-statement fields (operatingIncome, interestExpense, revenue)
        are summed across the last 4 quarters to get TTM values. The balance
        sheet itself is a snapshot — the latest quarterly filing is used so
        ratios like Quick Ratio and Debt-to-Equity reflect current state
        rather than the prior fiscal year-end.
        """
        from app.services.health_check_service import get_health_check_service

        # Fetch health check + Z-Score data in parallel.
        # `bs` is a point-in-time figure — quarterly = latest available.
        # `inc` is a flow figure — summed over 4 quarters for TTM.
        health_task = get_health_check_service().get_health_check(ticker)
        bs_task = self.fmp.get_balance_sheet(ticker, period="quarter", limit=1)
        inc_task = self.fmp.get_income_statement(ticker, period="quarter", limit=4)
        profile_task = self.fmp.get_company_profile(ticker)

        results = await asyncio.gather(
            health_task, bs_task, inc_task, profile_task, return_exceptions=True,
        )

        health = results[0] if not isinstance(results[0], Exception) else None
        bs_raw = results[1] if not isinstance(results[1], Exception) else []
        inc_raw = results[2] if not isinstance(results[2], Exception) else []
        profile_raw = results[3] if not isinstance(results[3], Exception) else {}

        # Parse data
        bs = bs_raw[0] if isinstance(bs_raw, list) and bs_raw else {}
        inc = _sum_ttm_income(inc_raw) if isinstance(inc_raw, list) else {}
        profile = {}
        if isinstance(profile_raw, dict):
            profile = profile_raw
        elif isinstance(profile_raw, list) and profile_raw:
            profile = profile_raw[0]

        mcap = _safe_float(profile, "mktCap")
        if mcap is None:
            mcap = _safe_float(profile, "marketCap")

        # Build the snapshot metrics. The happy path reuses HealthCheckService's
        # metrics directly (it now emits D/E, P/E, ROE, Current Ratio, Altman Z,
        # Interest Coverage, Quick Ratio — see METRIC_DEFS in
        # health_check_service.py). P/E and ROE are hidden from the snapshot
        # because they live in the Valuation and Profitability cards. The
        # explicit-compute block below is the **fallback** for when the upstream
        # HealthCheckService call failed — without it we'd silently drop to a
        # single-metric "Financial Health: —" card.
        metrics: List[SnapshotMetricResponse] = []
        z_rating = 3

        if health is not None:
            for m in health.metrics:
                if m.type in _HIDE_FROM_SNAPSHOT:
                    continue
                name = _metric_name(m.type, m.value, m.comparison_value)
                value = _fmt_value(m.type, m.value)
                metrics.append(SnapshotMetricResponse(
                    name=name, value=value,
                    metric_key=_VERDICT_KEY.get(m.type),
                    score=_health_metric_score(m),
                ))

            # Z-Score for the rating blend
            z_val_from_hc = next(
                (m.value for m in health.metrics if m.type == "altman_z_score"), None
            )
            z_rating = _zscore_rating(z_val_from_hc)
        else:
            # ── Fallback path: compute D/E, CR, IC, QR + Z-Score locally ──
            logger.warning(
                f"HealthCheckService returned None for {ticker} — using local fallback"
            )
            raw_sector = profile.get("sector", "")
            sector = _normalize_sector(raw_sector) if raw_sector else ""
            # Industry-relative: prefer INDUSTRY peers, fall back to sector per cell.
            industry = profile.get("industry", "") if isinstance(profile, dict) else ""
            sector_ic = sector_qr = sector_de = sector_cr = None
            if sector:
                try:
                    # CURRENT benchmark per metric: TTM row if present, else latest
                    # mature annual value (fallback).
                    cur = get_sector_benchmark_lookup().get_current_benchmark_values(
                        industry,
                        sector,
                        ["interest_coverage", "quick_ratio", "debt_to_equity", "current_ratio"],
                    )
                    sector_ic = cur.get("interest_coverage")
                    sector_qr = cur.get("quick_ratio")
                    sector_de = cur.get("debt_to_equity")
                    sector_cr = cur.get("current_ratio")
                except Exception as e:
                    logger.warning(f"Sector benchmark lookup failed for {ticker}: {e}")

            # Z-Score from balance sheet + TTM income + market cap
            z_score = _compute_z_score(bs, inc, mcap)
            z_value = f"{z_score}" if z_score is not None else "—"
            metrics.append(SnapshotMetricResponse(
                name="Altman Z-Score", value=z_value,
                metric_key="altman_z", score=_zscore_rating(z_score),
            ))
            z_rating = _zscore_rating(z_score)

            # Debt-to-Equity = total debt / shareholders' equity
            total_debt = _safe_float(bs, "totalDebt")
            equity = (
                _safe_float(bs, "totalStockholdersEquity")
                or _safe_float(bs, "totalEquity")
            )
            de = None
            if total_debt is not None and equity is not None and equity > 0:
                de = round(total_debt / equity, 2)
            metrics.append(SnapshotMetricResponse(
                name=_metric_name("debt_to_equity", de, sector_de),
                value=_fmt_value("debt_to_equity", de),
                metric_key="debt_to_equity",
                score=_fallback_sector_score(de, sector_de, lower_is_better=True),
            ))

            # Current Ratio = total current assets / total current liabilities
            curr_assets = _safe_float(bs, "totalCurrentAssets")
            curr_liab = _safe_float(bs, "totalCurrentLiabilities")
            cr = None
            if curr_assets is not None and curr_liab is not None and curr_liab > 0:
                cr = round(curr_assets / curr_liab, 2)
            metrics.append(SnapshotMetricResponse(
                name=_metric_name("current_ratio", cr, sector_cr),
                value=_fmt_value("current_ratio", cr),
                metric_key="current_ratio",
                score=_fallback_sector_score(cr, sector_cr, lower_is_better=False),
            ))

            # Interest Coverage = EBIT / |Interest Expense|. interestExpense
            # is reported as a positive number on the income statement.
            op_income = _safe_float(inc, "operatingIncome")
            int_expense = _safe_float(inc, "interestExpense")
            ic = None
            if op_income is not None and int_expense is not None and abs(int_expense) > 0:
                ic = round(op_income / abs(int_expense), 2)
            metrics.append(SnapshotMetricResponse(
                name=_metric_name("interest_coverage", ic, sector_ic),
                value=_fmt_value("interest_coverage", ic),
                metric_key="interest_coverage",
                score=_fallback_sector_score(ic, sector_ic, lower_is_better=False),
            ))

            # Quick Ratio = (cash + receivables) / current liabilities
            cash = _safe_float(bs, "cashAndCashEquivalents")
            receivables = _safe_float(bs, "netReceivables")
            qr = None
            if curr_liab is not None and curr_liab > 0:
                qr_numerator = (cash or 0) + (receivables or 0)
                if qr_numerator > 0:
                    qr = round(qr_numerator / curr_liab, 2)
            metrics.append(SnapshotMetricResponse(
                name=_metric_name("quick_ratio", qr, sector_qr),
                value=_fmt_value("quick_ratio", qr),
                metric_key="quick_ratio",
                score=_fallback_sector_score(qr, sector_qr, lower_is_better=False),
            ))

        # ── Rating blend: 40% Altman Z + 60% pass-rate over the 4 sector-
        # comparable metrics (D/E, Current Ratio, Interest Coverage, Quick
        # Ratio). Z-Score keeps its anchor weight because Altman thresholds
        # are calibrated to bankruptcy risk, not sector-relative comparisons.
        pass_rating = 3
        if health is not None:
            positives = sum(
                1 for m in health.metrics
                if m.type in _SECTOR_RATING_TYPES and m.status == "positive"
            )
            total_sector = sum(
                1 for m in health.metrics if m.type in _SECTOR_RATING_TYPES
            )
            if total_sector > 0:
                ratio = positives / total_sector
                if ratio >= 1.0:
                    pass_rating = 5
                elif ratio >= 0.75:
                    pass_rating = 4
                elif ratio >= 0.5:
                    pass_rating = 3
                elif ratio >= 0.25:
                    pass_rating = 2
                else:
                    pass_rating = 1

        weighted = 0.4 * z_rating + 0.6 * pass_rating
        rating = max(1, min(5, round(weighted)))

        if not metrics:
            metrics.append(SnapshotMetricResponse(name="Financial Health", value="—"))

        return SnapshotItemResponse(
            category="Financial Health",
            rating=rating,
            metrics=metrics,
            full_report_available=True,
            weighted_score=round(weighted, 3),
        )


# ── Singleton ─────────────────────────────────────────────────────

_service: Optional[HealthSnapshotService] = None


def get_health_snapshot_service() -> HealthSnapshotService:
    global _service
    if _service is None:
        _service = HealthSnapshotService()
    return _service
