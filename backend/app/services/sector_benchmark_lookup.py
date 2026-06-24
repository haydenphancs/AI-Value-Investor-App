"""
Sector Benchmark Lookup — fast read-only access to pre-computed sector benchmarks
stored in Supabase, with 1-hour in-memory cache.
"""

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 3600  # 1 hour


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


# ── Lookup service ────────────────────────────────────────────────

class SectorBenchmarkLookup:
    def __init__(self) -> None:
        self.supabase = get_supabase()

    def get_sector_benchmarks(
        self,
        sector: str,
        metrics: List[str],
        period_type: str,
    ) -> Dict[str, Dict[str, float]]:
        """
        Look up pre-computed sector benchmarks.

        Args:
            sector: GICS sector name (e.g., "Technology")
            metrics: List of metric names (e.g., ["eps_yoy", "revenue_yoy"])
            period_type: "annual" or "quarterly"

        Returns:
            {"eps_yoy": {"2024": 12.5, "2023": 8.3, ...}, "revenue_yoy": {...}}
        """
        cache_key = f"{sector}:{period_type}:{','.join(sorted(metrics))}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        result = self._query(sector, metrics, period_type)
        _cache_set(cache_key, result)
        return result

    # PostgREST / Supabase caps a single response at ~1000 rows by default.
    # A multi-metric quarterly lookup (e.g. 14 metrics × ~84 quarters ≈ 1176
    # rows) silently TRUNCATES at 1000, dropping whole metrics — which is why
    # the drill-down's quarterly sector lines went missing. Page through with
    # .range() so the result is always complete, regardless of row count.
    _PAGE = 1000

    def _fetch_rows(
        self, columns: str, sector: str, metrics: List[str], period_type: str,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        start = 0
        while True:
            resp = (
                self.supabase.table("sector_benchmarks")
                .select(columns)
                .eq("sector", sector)
                # SECTOR-aggregate rows only — industry rows (industry=<name>) are
                # served by the Phase-2 industry-first lookup. Without this filter
                # the sector lookup would mix in industry rows once they exist.
                .eq("industry", "")
                .eq("period_type", period_type)
                .in_("metric_name", metrics)
                .range(start, start + self._PAGE - 1)
                .execute()
            )
            batch = resp.data or []
            rows.extend(batch)
            if len(batch) < self._PAGE:
                break
            start += self._PAGE
        return rows

    def _query(
        self,
        sector: str,
        metrics: List[str],
        period_type: str,
    ) -> Dict[str, Dict[str, float]]:
        """Query Supabase for benchmark values (paginated → never truncated)."""
        try:
            rows = self._fetch_rows(
                "metric_name,period_label,median_value", sector, metrics, period_type,
            )
            result: Dict[str, Dict[str, float]] = {m: {} for m in metrics}
            for row in rows:
                metric = row["metric_name"]
                label = row["period_label"]
                result.setdefault(metric, {})[label] = row["median_value"]

            return result
        except Exception as e:
            logger.error(f"Sector benchmark lookup failed for {sector}/{period_type}: {e}")
            return {m: {} for m in metrics}

    # ── Phase 3A: sample-size-aware lookup ──────────────────────────────

    def get_sector_benchmarks_with_n(
        self,
        sector: str,
        metrics: List[str],
        period_type: str,
    ) -> Dict[str, Dict[str, Dict[str, float]]]:
        """Variant of get_sector_benchmarks that also returns sample_size
        per (metric, period). Used by moat scoring to skip partial-year
        rows whose medians are noisy.

        Returns:
            {
              "rd_to_revenue": {
                  "2025": {"median": 6.0, "n": 85},
                  "2026": {"median": 27.3, "n": 12},
              },
              ...
            }
        """
        cache_key = f"with_n:{sector}:{period_type}:{','.join(sorted(metrics))}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        try:
            rows = self._fetch_rows(
                "metric_name,period_label,median_value,sample_size",
                sector, metrics, period_type,
            )
            result: Dict[str, Dict[str, Dict[str, float]]] = {m: {} for m in metrics}
            for row in rows:
                metric = row["metric_name"]
                label = row["period_label"]
                result.setdefault(metric, {})[label] = {
                    "median": row.get("median_value"),
                    "n": row.get("sample_size") or 0,
                }
            _cache_set(cache_key, result)
            return result
        except Exception as e:
            logger.error(
                f"Sector benchmark with_n lookup failed for {sector}/{period_type}: {e}"
            )
            return {m: {} for m in metrics}


# ── Singleton ─────────────────────────────────────────────────────

_lookup: Optional[SectorBenchmarkLookup] = None


def get_sector_benchmark_lookup() -> SectorBenchmarkLookup:
    global _lookup
    if _lookup is None:
        _lookup = SectorBenchmarkLookup()
    return _lookup
