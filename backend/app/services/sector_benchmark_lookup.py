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

    def _query(
        self,
        sector: str,
        metrics: List[str],
        period_type: str,
    ) -> Dict[str, Dict[str, float]]:
        """Query Supabase for benchmark values."""
        try:
            response = (
                self.supabase.table("sector_benchmarks")
                .select("metric_name,period_label,median_value")
                .eq("sector", sector)
                .eq("period_type", period_type)
                .in_("metric_name", metrics)
                .execute()
            )

            result: Dict[str, Dict[str, float]] = {m: {} for m in metrics}
            for row in response.data or []:
                metric = row["metric_name"]
                label = row["period_label"]
                result.setdefault(metric, {})[label] = row["median_value"]

            return result
        except Exception as e:
            logger.error(f"Sector benchmark lookup failed for {sector}/{period_type}: {e}")
            return {m: {} for m in metrics}


# ── Singleton ─────────────────────────────────────────────────────

_lookup: Optional[SectorBenchmarkLookup] = None


def get_sector_benchmark_lookup() -> SectorBenchmarkLookup:
    global _lookup
    if _lookup is None:
        _lookup = SectorBenchmarkLookup()
    return _lookup
