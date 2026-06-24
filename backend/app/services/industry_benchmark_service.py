"""
Industry Benchmark Service — pre-computes median financial metrics per INDUSTRY
AND rebuilds the per-SECTOR medians over a broad, small-cap-inclusive universe,
storing both in the shared `sector_benchmarks` table:
    industry = ''      → the SECTOR aggregate row (the fallback)
    industry = <name>  → an INDUSTRY aggregate row, with `sector` = parent sector

Reuses SectorBenchmarkService's FMP fetch + per-group metric aggregation and the
module-level winsorization / METRIC_CONFIGS so the medians use IDENTICAL math.

Memory-safe: streams one INDUSTRY at a time, accumulating only the per-(metric,
period) VALUE lists into the parent sector — never holds a whole sector's raw
financials. Each company is fetched exactly once (for its industry).

Resumability: skip-FRESH per sector (skip a sector whose '' aggregate row is newer
than N hours) — we OVERWRITE on a new universe, so skip-exists is wrong. Re-trigger
after a dyno restart and it resumes from the first un-fresh sector.
"""

import asyncio
import json
import logging
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.services.sector_benchmark_service import (
    SectorBenchmarkService,
    METRIC_CONFIGS,
    MIN_SAMPLE_SIZE,
    UPSERT_BATCH_SIZE,
    BATCH_SIZE,
    BATCH_DELAY_SECONDS,
    FMP_ANNUAL_LIMIT_BACKFILL,
    FMP_QUARTERLY_LIMIT_BACKFILL,
    COMPUTED_RATIO_FLOOR,
    COMPUTED_RATIO_CEIL,
    _winsorize,
)

logger = logging.getLogger(__name__)

# backend/app/services/industry_benchmark_service.py → parents[2] == backend/
_UNIVERSE_PATH = Path(__file__).resolve().parents[2] / "data" / "benchmark_universe.json"

# Cap per industry by market cap — medians stabilize well below this, and it bounds
# FMP cost + memory. Most industries above the $500M floor have fewer than this.
TOP_TICKERS_PER_INDUSTRY = 300
DEFAULT_SKIP_IF_FRESH_HOURS = 24

# metric_name → type, for the winsorization dispatch on the sector accumulator.
_METRIC_TYPE: Dict[str, str] = {mc["name"]: mc["type"] for mc in METRIC_CONFIGS}


def _winsorize_for(metric_name: str, metric_type: str, values: List[float]) -> List[float]:
    """Identical dispatch to SectorBenchmarkService._compute_sector: wide bounds for
    yoy/qoq, tight 0-200 for computed multiples (EXCEPT fcf_margin — a signed decimal
    margin), no clamp for direct ratios + fcf_margin."""
    if metric_type in ("yoy", "qoq"):
        return _winsorize(values)
    if metric_type == "computed" and metric_name != "fcf_margin":
        return _winsorize(values, floor=COMPUTED_RATIO_FLOOR, ceil=COMPUTED_RATIO_CEIL)
    return values


class IndustryBenchmarkService:
    def __init__(self) -> None:
        self.supabase = get_supabase()
        # Reuse the sector service's FMP fetch + per-group aggregation + throttle.
        self._sb = SectorBenchmarkService()

    # ── Universe ─────────────────────────────────────────────────────
    def _load_universe(self) -> List[Tuple[str, List[Tuple[str, List[Tuple[str, float]]]]]]:
        """[(sector, [(industry, [(ticker, cap)...] top-N by cap), ...]), ...]."""
        try:
            data = json.loads(_UNIVERSE_PATH.read_text())
        except Exception as exc:
            logger.error("industry_benchmark: failed to read %s: %s", _UNIVERSE_PATH, exc)
            return []
        by_sector: Dict[str, List[Tuple[str, List[Tuple[str, float]]]]] = defaultdict(list)
        for entry in data.get("industries", []) or []:
            ind = entry.get("industry")
            sector = entry.get("sector")
            mcaps = entry.get("market_caps") or {}
            if not ind or not sector or sector == "Unknown" or not mcaps:
                continue
            sorted_tkrs = sorted(
                ((t, float(c or 0.0)) for t, c in mcaps.items()),
                key=lambda x: x[1], reverse=True,
            )[:TOP_TICKERS_PER_INDUSTRY]
            by_sector[sector].append((ind, sorted_tkrs))
        return sorted(by_sector.items())

    # ── Resumability ─────────────────────────────────────────────────
    def _sector_is_fresh(self, sector: str, hours: Optional[int]) -> bool:
        if not hours:
            return False
        try:
            resp = (
                self.supabase.table("sector_benchmarks")
                .select("computed_at")
                .eq("sector", sector).eq("industry", "")
                .order("computed_at", desc=True).limit(1).execute()
            )
        except Exception:
            return False
        if not resp.data:
            return False
        try:
            last = datetime.fromisoformat(
                resp.data[0]["computed_at"].replace("Z", "+00:00")
            )
        except Exception:
            return False
        return (datetime.now(timezone.utc) - last) < timedelta(hours=hours)

    # ── Fetch (batched + throttled, reusing the sector service's semaphore) ──
    async def _fetch_batched(self, tickers: List[str], al: int, ql: int) -> List[Dict[str, List]]:
        out: List[Dict[str, List]] = []
        for i in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[i:i + BATCH_SIZE]
            results = await asyncio.gather(
                *[self._sb._fetch_company_data(t, al, ql) for t in batch],
                return_exceptions=True,
            )
            for r in results:
                if isinstance(r, dict):
                    out.append(r)
            if i + BATCH_SIZE < len(tickers):
                await asyncio.sleep(BATCH_DELAY_SECONDS)
        return out

    # ── Row building (mirrors _compute_sector's MIN_SAMPLE + winsorize) ──
    def _rows_from_values(
        self, sector: str, industry: str,
        values_by_key: Dict[Tuple[str, str, str], List[float]], now: str,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for (metric_name, period_type, period_label), values in values_by_key.items():
            if len(values) < MIN_SAMPLE_SIZE:
                continue
            cleaned = _winsorize_for(
                metric_name, _METRIC_TYPE.get(metric_name, "direct"), values,
            )
            rows.append({
                "sector": sector,
                "industry": industry,
                "metric_name": metric_name,
                "period_type": period_type,
                "period_label": period_label,
                "median_value": round(statistics.median(cleaned), 4),
                "sample_size": len(cleaned),
                "computed_at": now,
            })
        return rows

    def _upsert(self, rows: List[Dict[str, Any]]) -> int:
        n = 0
        for i in range(0, len(rows), UPSERT_BATCH_SIZE):
            batch = rows[i:i + UPSERT_BATCH_SIZE]
            try:
                self.supabase.table("sector_benchmarks").upsert(
                    batch,
                    on_conflict="sector,industry,metric_name,period_type,period_label",
                ).execute()
                n += len(batch)
            except Exception as e:
                logger.error("industry_benchmark upsert batch failed: %s", e)
        return n

    # ── Per-sector compute (stream industries, accumulate into the sector) ──
    async def _compute_sector(
        self, sector: str,
        industries: List[Tuple[str, List[Tuple[str, float]]]],
        al: int, ql: int,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        sector_acc: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
        written = 0
        for industry, ticker_caps in industries:
            tickers = [t for t, _ in ticker_caps]
            company_data = await self._fetch_batched(tickers, al, ql)
            if not company_data:
                continue
            ind_values: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
            for mc in METRIC_CONFIGS:
                for period_type in ("annual", "quarterly"):
                    vals = self._sb._collect_metric_values(company_data, mc, period_type)
                    for period_label, values in vals.items():
                        key = (mc["name"], period_type, period_label)
                        ind_values[key].extend(values)
                        sector_acc[key].extend(values)
            written += self._upsert(self._rows_from_values(sector, industry, ind_values, now))
            del company_data  # free this industry's raw financials before the next
        # Sector aggregate (industry='') from the accumulated raw value lists.
        written += self._upsert(self._rows_from_values(sector, "", sector_acc, now))
        return written

    # ── Orchestration ────────────────────────────────────────────────
    async def recompute_all(
        self, *, skip_if_fresh_hours: Optional[int] = None,
        sectors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        start = datetime.now(timezone.utc)
        universe = self._load_universe()
        if sectors:
            universe = [(s, inds) for s, inds in universe if s in sectors]
        total_rows = done = skipped_fresh = 0
        for sector, industries in universe:
            if self._sector_is_fresh(sector, skip_if_fresh_hours):
                skipped_fresh += 1
                logger.info("industry_benchmark: %s fresh — skipped", sector)
                continue
            try:
                logger.info(
                    "industry_benchmark: computing %s (%d industries)...",
                    sector, len(industries),
                )
                n = await self._compute_sector(
                    sector, industries,
                    FMP_ANNUAL_LIMIT_BACKFILL, FMP_QUARTERLY_LIMIT_BACKFILL,
                )
                total_rows += n
                done += 1
                logger.info("industry_benchmark: %s done — %d rows", sector, n)
            except Exception as e:
                logger.error("industry_benchmark: %s failed: %s", sector, e, exc_info=True)
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        summary = {
            "sectors_done": done,
            "sectors_skipped_fresh": skipped_fresh,
            "rows_upserted": total_rows,
            "elapsed_seconds": round(elapsed, 1),
        }
        logger.info("industry_benchmark complete: %s", summary)
        return summary


_industry_benchmark_service: Optional[IndustryBenchmarkService] = None


def get_industry_benchmark_service() -> IndustryBenchmarkService:
    global _industry_benchmark_service
    if _industry_benchmark_service is None:
        _industry_benchmark_service = IndustryBenchmarkService()
    return _industry_benchmark_service
