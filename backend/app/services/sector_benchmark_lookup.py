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
        # pop (not del) — the lookup is called from asyncio.to_thread workers, so
        # two threads can expire the same key at once; del would raise KeyError.
        _cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time(), value)


# ── Phase 3: mature-period picker (sample-size floor + hold-last-mature) ──

#: A just-closed fiscal period is only partially reported (e.g. annual 2026 has
#: ~16 of ~76 names), so its median swings wildly. A benchmark period must carry
#: at least this many companies to be allowed to "decide" a single-value
#: comparison; otherwise we hold the most recent period that does.
MATURE_SAMPLE_FLOOR = 20


def _period_sort_key(label: str) -> Tuple[int, int]:
    """Chronological sort key for a benchmark period label, so "latest" is the
    real most-recent period regardless of label format:

      annual    "2025"     → (2025, 0)
      quarterly "Q4'25"    → (2025, 4)   ("Q1'26" → (2026, 1) sorts AFTER "Q4'25")

    A plain lexical sort is WRONG for quarterly labels ("Q4'25" > "Q1'26"
    lexically, but Q1'26 is later). Unrecognized labels collapse to (0, 0) so
    they sort oldest and never crash the picker. Two-digit quarterly years are
    in the 2000s for this dataset (Q1'06 … Q4'26).
    """
    s = label.strip()
    if s.startswith("Q") and "'" in s:
        try:
            q_part, y_part = s[1:].split("'", 1)
            return (2000 + int(y_part), int(q_part))
        except (ValueError, IndexError):
            return (0, 0)
    try:
        return (int(s), 0)
    except ValueError:
        return (0, 0)


def pick_mature_benchmark(
    cells: Dict[str, Dict[str, Any]], floor: int = MATURE_SAMPLE_FLOOR,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """From a metric's {period_label: cell} map (cell carries value/level/
    peer_group_name/n, as returned by ``get_benchmarks``), return
    ``(cell, held_back)`` for the LATEST period whose sample_size >= `floor` —
    the last *mature* period. Falls back to the latest period overall when none
    meet the floor (a very thin metric still shows something, just not held back).

    ``held_back`` is True when a newer-but-thinner period was skipped, so the
    caller can footnote that the latest period was excluded.

    Periods are ordered chronologically via ``_period_sort_key`` (correct for
    BOTH annual "YYYY" and quarterly "Q#'YY" — a lexical sort silently mis-orders
    quarters). Returns ``(None, False)`` for an empty map.
    """
    if not cells:
        return None, False
    labels_desc = sorted(cells.keys(), key=_period_sort_key, reverse=True)
    latest = labels_desc[0]
    for label in labels_desc:
        if (cells[label].get("n") or 0) >= floor:
            return cells[label], (label != latest)
    return cells[latest], False


def mature_benchmark_value(
    cells: Dict[str, Dict[str, Any]], floor: int = MATURE_SAMPLE_FLOOR,
) -> Optional[float]:
    """Convenience: the median value from ``pick_mature_benchmark`` (or None)."""
    cell, _ = pick_mature_benchmark(cells, floor)
    return cell["value"] if cell else None


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
        industry: str = "",
    ) -> List[Dict[str, Any]]:
        """Paginated fetch of benchmark rows for ONE peer group.

        industry=""    → the SECTOR-aggregate rows (industry='' for `sector`).
        industry=<name> → the INDUSTRY-aggregate rows, matched by the globally
        unique FMP industry name. The stored parent sector is intentionally NOT
        re-checked, so a ticker whose profile.sector drifts from the industry's
        recorded parent (a modal-sector straddler) still gets its industry row
        rather than silently dropping to the sector fallback.
        """
        rows: List[Dict[str, Any]] = []
        start = 0
        while True:
            query = (
                self.supabase.table("sector_benchmarks")
                .select(columns)
                .eq("period_type", period_type)
                .in_("metric_name", metrics)
            )
            if industry:
                query = query.eq("industry", industry)
            else:
                # SECTOR-aggregate rows only — exclude industry=<name> rows so the
                # sector lookup never mixes in industry rows.
                query = query.eq("sector", sector).eq("industry", "")
            resp = query.range(start, start + self._PAGE - 1).execute()
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

    # ── Phase 2: industry-first lookup with per-cell sector fallback ─────

    _RICH_COLS = "metric_name,period_label,median_value,sample_size"

    def get_benchmarks(
        self,
        industry: str,
        sector: str,
        metrics: List[str],
        period_type: str,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Industry-relative benchmark lookup with per-(metric, period) sector
        fallback.

        For each (metric, period_label) returns the INDUSTRY-aggregate row when
        one exists (industry=<name>), else the SECTOR-aggregate row
        (industry=''). The fallback is PER CELL — a thin industry that carries
        only some metrics/periods still gets the industry value where present and
        the sector value everywhere else.

        Returns:
            {metric: {period_label: {"value": float,
                                     "level": "industry" | "sector",
                                     "peer_group_name": str,
                                     "n": int}}}

        Pass industry="" for a pure sector lookup (every cell level="sector").
        Degrades to empty per-metric dicts on any DB error — the caller simply
        gets no benchmark line, never an exception.
        """
        cache_key = (
            f"gb:{industry}:{sector}:{period_type}:{','.join(sorted(metrics))}"
        )
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        result: Dict[str, Dict[str, Dict[str, Any]]] = {m: {} for m in metrics}
        try:
            # 1. Fallback layer first — the sector aggregate.
            if sector:
                for row in self._fetch_rows(
                    self._RICH_COLS, sector, metrics, period_type, industry="",
                ):
                    result.setdefault(row["metric_name"], {})[row["period_label"]] = {
                        "value": row["median_value"],
                        "level": "sector",
                        "peer_group_name": sector,
                        "n": row.get("sample_size") or 0,
                    }
            # 2. Preferred layer — industry rows overwrite the matching cells.
            if industry:
                for row in self._fetch_rows(
                    self._RICH_COLS, sector, metrics, period_type, industry=industry,
                ):
                    result.setdefault(row["metric_name"], {})[row["period_label"]] = {
                        "value": row["median_value"],
                        "level": "industry",
                        "peer_group_name": industry,
                        "n": row.get("sample_size") or 0,
                    }
        except Exception as e:
            logger.error(
                f"Industry benchmark lookup failed for "
                f"{industry!r}/{sector!r}/{period_type}: {type(e).__name__}: {e}"
            )
            return {m: {} for m in metrics}

        _cache_set(cache_key, result)
        return result

    def get_benchmark_values(
        self,
        industry: str,
        sector: str,
        metrics: List[str],
        period_type: str,
    ) -> Dict[str, Dict[str, float]]:
        """Flat {metric: {period_label: median_value}} view of get_benchmarks —
        industry-preferred with sector fallback. Drop-in replacement for
        get_sector_benchmarks for callers that only need the VALUE (the
        peer-group label still reads "sector" until Phase 3 plumbs level/name
        into the DTOs)."""
        rich = self.get_benchmarks(industry, sector, metrics, period_type)
        return {
            metric: {label: cell["value"] for label, cell in periods.items()}
            for metric, periods in rich.items()
        }


# ── Singleton ─────────────────────────────────────────────────────

_lookup: Optional[SectorBenchmarkLookup] = None


def get_sector_benchmark_lookup() -> SectorBenchmarkLookup:
    global _lookup
    if _lookup is None:
        _lookup = SectorBenchmarkLookup()
    return _lookup
