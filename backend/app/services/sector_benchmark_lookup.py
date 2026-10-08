"""
Sector Benchmark Lookup — fast read-only access to pre-computed sector benchmarks
stored in Supabase, with 1-hour in-memory cache.
"""

import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from app.database import get_supabase
from app.utils.period_labels import benchmark_period_complete
from app.utils.supabase_errors import (
    is_transient_supabase_error,
    retry_idempotent_sync,
)

logger = logging.getLogger(__name__)

# ── In-memory cache ───────────────────────────────────────────────

_cache: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL = 3600  # 1 hour

# period_type of the TTM current-snapshot rows (written by industry_benchmark_service).
TTM_PERIOD_TYPE = "ttm"

# period_type of the QUARTERLY rows, keyed by the CALENDAR quarter the period ends in
# (`period_labels.calendar_quarter_label`, e.g. "Q3'25" = Jul-Sep 2025). Every quarterly
# reader asks for this, and joins a company quarter with the same helper.
#
# 🔴 Never read period_type 'quarterly'. Those legacy rows were keyed "<FISCAL quarter
# number>'<calendar year of the period end>", so an off-calendar company (Microsoft, Apple,
# Nvidia, every Jan-year-end retailer) was pooled with — and joined to — peers' quarters
# 3-10 months away. They are kept only until the owner deletes them after the first
# calendar-quarter recompute (migration 185).
CALENDAR_QUARTER_PERIOD_TYPE = "calendar_quarter"


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


# Hard cap on the in-memory tier. Without it this dict grew with the number of DISTINCT
# keys ever requested and was never pruned: `_cache_get` only deletes an entry when that
# SAME key is read again after expiry, so a ticker fetched once and never revisited stayed
# resident for the life of the process. Across ~17 services on a long-lived Railway
# container that is a slow leak whose only resolution is an OOM restart — which drops every
# in-flight report with it. Bounded LRU-ish: evict from the head (least recently WRITTEN).
_CACHE_MAX_ENTRIES = 1024


def _cache_set(key: str, value: Any) -> None:
    _cache.pop(key, None)
    _cache[key] = (time.time(), value)
    if len(_cache) > _CACHE_MAX_ENTRIES:
        for _old in list(_cache.keys())[: len(_cache) - _CACHE_MAX_ENTRIES]:
            _cache.pop(_old, None)


# ── Transient-error retry (Supabase/httpx blips) ─────────────────────────
# A "Server disconnected" mid-query is an infra hiccup, not a bug — retry a few
# times, then log at WARNING (not ERROR) so it doesn't page as a Sentry issue.
_MAX_FETCH_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 0.25


def _is_transient(exc: BaseException) -> bool:
    """True for a transient connection blip worth retrying + logging quietly.

    Back-compat alias — the rule itself now lives in `app/utils/supabase_errors.py`
    so all five Supabase call sites share ONE classification. It moved because the
    version that lived here could not recognise a Cloudflare 520: Supabase's edge
    answers with an HTML error page, postgrest raises
    `APIError('JSON could not be generated')` with an INT `.code`, and none of the
    httpx/message/traceback predicates below match it (its frames are `postgrest`,
    not `httpcore`). That is what produced the 7-event "Industry benchmark lookup
    failed … Error 520" Sentry issue.

    Kept as a module-level name on purpose: the three degrade-to-empty handlers in
    this file and `tests/test_sector_benchmark_transient.py` both reach for it here.
    """
    return is_transient_supabase_error(exc)


# ── Which stored rows may be SERVED ──────────────────────────────────────
#
# Two gates, both on the row's own `computed_at`, applied to every read in
# `_fetch_rows` so no caller can skip them:
#
# 1. An ANNUAL or CALENDAR-QUARTER row is served only when it was computed at least
#    BENCHMARK_REPORTING_LAG_DAYS (75) after its period ended. Before that its cell holds
#    only the early, off-calendar filers (on 2026-10-04 the "2026" annual cells held 6-27%
#    of each group). The producer does not write such a period any more, but rows it
#    wrote earlier stay in the table until a later run rewrites them; this gate hides
#    them meanwhile. Owner decision 2026-10-07: an incomplete period shows NO peer value —
#    never an earlier period's median standing in for it.
# 2. A TTM row is the CURRENT snapshot, rewritten every Sunday for every peer group with
#    at least MIN_SAMPLE_SIZE companies. A row older than TTM_MAX_AGE_DAYS belongs to a
#    group that later fell below that size (or the weekly job has stopped) and is no
#    longer anyone's current median. Historical annual / quarterly rows have no age limit:
#    the producer fetches only 16 annual records, so the oldest years legitimately stop
#    being rewritten and remain correct history.
TTM_MAX_AGE_DAYS = 21
_SERVE_LOG_EVERY_SECONDS = 6 * 3600
_serve_log_last: Dict[Tuple[str, str, str], float] = {}


def _parse_computed_at(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    # In UTC, like the producer's run day (`industry_benchmark_service._run_day`): a stamp
    # serialised with another offset ("2027-03-15T20:00:00-05:00") must judge the same
    # calendar day the producer did, or the 75-day gate splits between writer and reader.
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def servable_benchmark_rows(
    rows: Iterable[Dict[str, Any]],
    period_type: str,
    now: Optional[datetime] = None,
) -> Tuple[List[Dict[str, Any]], Counter]:
    """Split stored benchmark rows into the ones a screen may show and a count of the
    ones held back, by reason (``incomplete_period`` / ``stale_ttm``).

    A row with no readable ``computed_at`` is judged as if computed NOW: an incomplete
    period stays hidden, and a TTM row is kept (its age is unknown, not known to be old).
    A label that names no period of its type is kept — the producer already refuses
    unkeyable labels, and guessing would hide real rows."""
    now = now or datetime.now(timezone.utc)
    kept: List[Dict[str, Any]] = []
    dropped: Counter = Counter()
    for row in rows:
        computed = _parse_computed_at(row.get("computed_at"))
        if period_type == TTM_PERIOD_TYPE:
            if computed is not None and now - computed > timedelta(days=TTM_MAX_AGE_DAYS):
                dropped["stale_ttm"] += 1
                continue
        elif period_type in ("annual", CALENDAR_QUARTER_PERIOD_TYPE):
            as_of = (computed or now).date()
            if benchmark_period_complete(period_type, row.get("period_label"), as_of) is False:
                dropped["incomplete_period"] += 1
                continue
        kept.append(row)
    return kept, dropped


def _log_held_back_rows(group: str, period_type: str, dropped: Counter) -> None:
    """At most one line per (peer group, period type, reason) per 6 h. An incomplete
    period is expected for months every year (INFO); a stale TTM row means a peer group
    shrank below MIN_SAMPLE_SIZE or the weekly TTM job stopped (WARNING)."""
    now = time.monotonic()
    for reason, count in dropped.items():
        key = (group, period_type, reason)
        last = _serve_log_last.get(key)
        if last is not None and now - last < _SERVE_LOG_EVERY_SECONDS:
            continue
        _serve_log_last[key] = now
        log = logger.warning if reason == "stale_ttm" else logger.info
        log("sector_benchmarks: held back %d %s row(s) for %s (%s)",
            count, period_type, group, reason)


# ── Peer-cell maturity: one period's industry vs sector choice ───────────

#: A median from fewer companies than this does not decide a comparison when a larger
#: group's median for the SAME period exists. Industry rows are written from
#: MIN_SAMPLE_SIZE (5) companies, so 80 of the 153 industries (5-19 members) never reach
#: it; their companies are compared with their sector's median for that period instead.
MATURE_SAMPLE_FLOOR = 20


def _is_mature(cell: Optional[Dict[str, Any]]) -> bool:
    return (
        cell is not None
        and cell.get("value") is not None
        and (cell.get("n") or 0) >= MATURE_SAMPLE_FLOOR
    )


def merge_peer_cells(
    industry_cell: Optional[Dict[str, Any]],
    sector_cell: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """The peer cell ONE period shows: the industry median when it is mature, else the
    SAME period's sector median when that is mature, else whichever exists (industry
    first). Never another period's value.

    Replaces the old cross-period hold-back, which painted a thin cell with the latest
    mature cell at or before it — usually an older period, and often the SECTOR's when an
    early period had no industry row — so a small industry's peer line froze for years at
    one old sector median (verified 2026-10-07). The partial-period problem that hold-back
    was built for is now handled by never serving an incomplete period at all
    (`servable_benchmark_rows`)."""
    if _is_mature(industry_cell):
        return industry_cell
    if _is_mature(sector_cell):
        return sector_cell
    return industry_cell if industry_cell is not None else sector_cell


def _period_sort_key(label: str) -> Tuple[int, int]:
    """Chronological sort key for a benchmark period label, so "latest" is the
    real most-recent period regardless of label format:

      annual    "2025"     → (2025, 0)
      quarterly "Q4'25"    → (2025, 4)   ("Q1'26" → (2026, 1) sorts AFTER "Q4'25")

    A plain lexical sort is WRONG for quarterly labels ("Q4'25" > "Q1'26"
    lexically, but Q1'26 is later). Unrecognized labels collapse to (0, 0) so
    they sort oldest and never crash the picker. Two-digit quarterly years are
    2000s: the producer never stores a quarter that ended before 2000, whose
    two-digit label ("Q4'95") would read as 2095.
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


def flatten_benchmark_values(
    rich: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, float]]:
    """``{metric: {period: value}}`` from ``get_benchmarks``' rich cells. Each period
    keeps its OWN cell's value: ``get_benchmarks`` already chose that period's peer
    level (`merge_peer_cells`) and hid incomplete periods (`servable_benchmark_rows`),
    so nothing is substituted from another period. A cell without a value is left out."""
    return {
        metric: {
            period: cell["value"]
            for period, cell in cells.items()
            if cell.get("value") is not None
        }
        for metric, cells in rich.items()
    }


def benchmark_levels(
    rich: Dict[str, Dict[str, Dict[str, Any]]],
) -> Dict[str, Dict[str, Optional[str]]]:
    """``{metric: {period: "industry" | "sector" | None}}`` — the peer level of the cell
    each period shows, for legend wording. Same cells as `flatten_benchmark_values`."""
    return {
        metric: {
            period: cell.get("level")
            for period, cell in cells.items()
            if cell.get("value") is not None
        }
        for metric, cells in rich.items()
    }


def peer_level_votes(
    points: List[Dict[str, Any]],
    metric_benchmarks: Dict[str, Any],
    metric_levels: Dict[str, Optional[str]],
) -> List[str]:
    """The peer level of every point in a chart series that actually DRAWS a benchmark
    value (joined on ``_match_period``, falling back to ``period``; "" = no peer value)
    and whose cell declares one."""
    votes: List[str] = []
    for p in points:
        key = p.get("_match_period", p.get("period"))
        if not key or metric_benchmarks.get(key) is None:
            continue
        level = metric_levels.get(key)
        if level in ("industry", "sector"):
            votes.append(level)
    return votes


def majority_peer_level(votes: List[str]) -> Optional[str]:
    """"industry" or "sector" by majority (a tie goes to "industry"); None with no
    votes, so the client keeps its neutral wording instead of naming a line that is
    not drawn."""
    if not votes:
        return None
    return "industry" if votes.count("industry") >= votes.count("sector") else "sector"


def series_peer_level(
    points: List[Dict[str, Any]],
    metric_benchmarks: Dict[str, Any],
    metric_levels: Dict[str, Optional[str]],
) -> Optional[str]:
    """Peer group one series' dashed line comes from (Growth and Profit Power legends)."""
    return majority_peer_level(peer_level_votes(points, metric_benchmarks, metric_levels))


class BenchmarkLookupFailed(dict):
    """The empty ``{metric: {}}`` shape a lookup returns when the DB call FAILED.

    Callers keep the same contract (no benchmark → absolute heuristics), but a
    failure is not the same answer as "this peer group has no rows": a service that
    persists its build (health check, 24h) must not pin a heuristic-only verdict
    produced by a transient Supabase error. ``lookup_failed`` lets it tell the two
    apart WITHOUT a signature change — test stubs and other callers that return a
    plain dict read as "not failed".
    """

    lookup_failed = True


def lookup_failed(result: Any) -> bool:
    """True when a benchmark lookup result came from a failed DB call."""
    return bool(getattr(result, "lookup_failed", False))


def pick_mature_benchmark(
    cells: Dict[str, Dict[str, Any]], floor: int = MATURE_SAMPLE_FLOOR,
) -> Tuple[Optional[Dict[str, Any]], bool]:
    """From a metric's {period_label: cell} map (cell carries value/level/
    peer_group_name/n, as returned by ``get_benchmarks``), return
    ``(cell, held_back)`` for the LATEST period whose sample_size >= `floor` —
    the last *mature* period — or ``(None, False)`` when no period meets the floor.

    It never falls back to a thin cell: that fallback returned a 5-company median
    (or a partial year) as "the" peer value for a single-value comparison, which is
    worse than showing none (audit 2026-10-07). ``held_back`` is True when a newer
    but thinner period was skipped.

    Periods are ordered chronologically via ``_period_sort_key`` (correct for
    BOTH annual "YYYY" and quarterly "Q#'YY" — a lexical sort silently mis-orders
    quarters).
    """
    if not cells:
        return None, False
    labels_desc = sorted(cells.keys(), key=_period_sort_key, reverse=True)
    latest = labels_desc[0]
    for label in labels_desc:
        cell = cells[label]
        if cell.get("value") is not None and (cell.get("n") or 0) >= floor:
            return cell, (label != latest)
    return None, False


def mature_benchmark_value(
    cells: Dict[str, Dict[str, Any]], floor: int = MATURE_SAMPLE_FLOOR,
) -> Optional[float]:
    """Convenience: the median value from ``pick_mature_benchmark`` (or None)."""
    cell, _ = pick_mature_benchmark(cells, floor)
    return cell["value"] if cell else None


# ── Lookup service ────────────────────────────────────────────────

class SectorBenchmarkLookup:
    """SYNCHRONOUS: every method may run paginated supabase-py reads with a `time.sleep`
    retry. From async code, call it through `await asyncio.to_thread(...)`; a direct call
    blocks the single uvicorn worker's event loop on every cold key.
    `tests/test_sector_benchmark_off_the_loop.py` pins the async callers."""

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

        try:
            result = self._query(sector, metrics, period_type)
        except Exception as e:
            # Do NOT cache a failure. Mirrors get_sector_benchmarks_with_n and
            # get_benchmarks, which both scope their `_cache_set` to the success path.
            # Returning the empty shape keeps every caller's contract (comparisons just
            # degrade to "no benchmark"), but the next request retries instead of being
            # served a cached blank for the rest of the hour.
            _log = logger.warning if _is_transient(e) else logger.error
            _log("Sector benchmark lookup failed for %s/%s: %s: %s",
                 sector, period_type, type(e).__name__, e)
            return BenchmarkLookupFailed({m: {} for m in metrics})

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

        Every read goes through `servable_benchmark_rows` (an incomplete period, or a
        TTM row older than TTM_MAX_AGE_DAYS, is never returned), so `computed_at` is
        always selected.
        """
        if "computed_at" not in [c.strip() for c in columns.split(",")]:
            columns = f"{columns},computed_at"

        def _page_all() -> List[Dict[str, Any]]:
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
                    # SECTOR-aggregate rows only — exclude industry=<name> rows so
                    # the sector lookup never mixes in industry rows.
                    query = query.eq("sector", sector).eq("industry", "")
                # A stable ORDER BY: without one, a concurrent upsert (the quarterly
                # recompute) can shift rows between pages and silently skip or repeat one.
                resp = query.order("id").range(start, start + self._PAGE - 1).execute()
                batch = resp.data or []
                rows.extend(batch)
                if len(batch) < self._PAGE:
                    break
                start += self._PAGE
            return rows

        # Idempotent: a pure paginated READ, and the retry restarts from start=0 so
        # a mid-pagination blip cannot yield a half-built list.
        rows = retry_idempotent_sync(
            _page_all,
            what=f"sector_benchmarks fetch sector={sector!r} industry={industry!r}",
            attempts=_MAX_FETCH_ATTEMPTS,
            backoff_seconds=_RETRY_BACKOFF_SECONDS,
            logger=logger,
        )
        kept, dropped = servable_benchmark_rows(rows, period_type)
        if dropped:
            _log_held_back_rows(industry or f"{sector} (sector)", period_type, dropped)
        return kept

    def _query(
        self,
        sector: str,
        metrics: List[str],
        period_type: str,
    ) -> Dict[str, Dict[str, float]]:
        """Query Supabase for benchmark values (paginated → never truncated).

        RAISES on failure — deliberately. This used to swallow every exception and
        return the same empty dict a successful zero-row query returns, which made the
        two indistinguishable to the caller. `get_sector_benchmarks` then cached that
        empty sentinel for the full hour, so ONE transient Supabase blip (a Cloudflare
        520 is the common one) erased every "vs sector avg" comparison on the screen
        until the TTL expired. The two sibling lookups already scope their caching to
        the success path; this one could not, because it never learned that it failed.

        A genuinely-empty result still returns `{m: {} ...}` and is still worth caching
        — a peer group with no rows is a real answer, not an error.
        """
        rows = self._fetch_rows(
            "metric_name,period_label,median_value", sector, metrics, period_type,
        )
        result: Dict[str, Dict[str, float]] = {m: {} for m in metrics}
        for row in rows:
            metric = row["metric_name"]
            label = row["period_label"]
            result.setdefault(metric, {})[label] = row["median_value"]

        return result

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
            _log = logger.warning if _is_transient(e) else logger.error
            _log("Sector benchmark with_n lookup failed for %s/%s: %s: %s",
                 sector, period_type, type(e).__name__, e)
            return BenchmarkLookupFailed({m: {} for m in metrics})

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

        For each (metric, period_label) both layers are read and `merge_peer_cells`
        picks one: the INDUSTRY-aggregate row (industry=<name>) when it carries at
        least MATURE_SAMPLE_FLOOR companies, else the SAME period's SECTOR-aggregate
        row (industry='') when that one does, else whichever exists. Until 2026-10-07
        any industry row overwrote the sector row, so a 6-company industry median
        displaced a 400-company sector median for the same period.

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

        try:
            sector_cells, industry_cells = self._read_layers(industry, sector, metrics, period_type)
        except Exception as e:
            _log = logger.warning if _is_transient(e) else logger.error
            _log("Industry benchmark lookup failed for %r/%r/%s: %s: %s",
                 industry, sector, period_type, type(e).__name__, e)
            return BenchmarkLookupFailed({m: {} for m in metrics})

        result: Dict[str, Dict[str, Dict[str, Any]]] = {m: {} for m in metrics}
        for metric in set(sector_cells) | set(industry_cells):
            s_map = sector_cells.get(metric, {})
            i_map = industry_cells.get(metric, {})
            for label in set(s_map) | set(i_map):
                cell = merge_peer_cells(i_map.get(label), s_map.get(label))
                if cell is not None:
                    result.setdefault(metric, {})[label] = cell

        _cache_set(cache_key, result)
        return result

    def _read_layers(
        self, industry: str, sector: str, metrics: List[str], period_type: str,
    ) -> Tuple[Dict[str, Dict[str, Dict[str, Any]]], Dict[str, Dict[str, Dict[str, Any]]]]:
        """(sector cells, industry cells), each {metric: {period_label: cell}}, from the
        servable rows of each layer. RAISES on a DB error (callers flag the failure)."""
        sector_cells: Dict[str, Dict[str, Dict[str, Any]]] = {}
        industry_cells: Dict[str, Dict[str, Dict[str, Any]]] = {}
        if sector:
            for row in self._fetch_rows(
                self._RICH_COLS, sector, metrics, period_type, industry="",
            ):
                sector_cells.setdefault(row["metric_name"], {})[row["period_label"]] = {
                    "value": row["median_value"],
                    "level": "sector",
                    "peer_group_name": sector,
                    "n": row.get("sample_size") or 0,
                }
        if industry:
            for row in self._fetch_rows(
                self._RICH_COLS, sector, metrics, period_type, industry=industry,
            ):
                industry_cells.setdefault(row["metric_name"], {})[row["period_label"]] = {
                    "value": row["median_value"],
                    "level": "industry",
                    "peer_group_name": industry,
                    "n": row.get("sample_size") or 0,
                }
        return sector_cells, industry_cells

    def get_benchmark_series(
        self,
        industry: str,
        sector: str,
        metrics: List[str],
        period_type: str,
    ) -> Dict[str, Dict[str, Dict[str, Any]]]:
        """Peer cells for a CHART LINE: same shape as `get_benchmarks`, but every period
        of a metric comes from ONE peer group, so the dashed line, its legend, its tooltip
        and Cay AI's peer sentence all name the population every point belongs to.

        Per metric: the INDUSTRY's cells when the industry has a mature cell
        (n >= MATURE_SAMPLE_FLOOR) at the NEWEST period either layer holds — its older,
        thinner periods included, each still that industry's own median — else the
        SECTOR's cells (or the industry's, when the sector has none). A period with no
        cell in the chosen group draws nothing; no period borrows another's value.

        The per-period merge (`get_benchmarks`) is right for picking ONE value, but on a
        line it switched population wherever n crossed 20 (review 2026-10-07): a step that
        reads as the peers' margins jumping, under a single "Industry/Sector Avg" label.
        Degrades to `BenchmarkLookupFailed` on a DB error, like `get_benchmarks`."""
        cache_key = (
            f"gs:{industry}:{sector}:{period_type}:{','.join(sorted(metrics))}"
        )
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached
        try:
            sector_cells, industry_cells = self._read_layers(industry, sector, metrics, period_type)
        except Exception as e:
            _log = logger.warning if _is_transient(e) else logger.error
            _log("Industry benchmark series lookup failed for %r/%r/%s: %s: %s",
                 industry, sector, period_type, type(e).__name__, e)
            return BenchmarkLookupFailed({m: {} for m in metrics})

        result: Dict[str, Dict[str, Dict[str, Any]]] = {m: {} for m in metrics}
        for metric in set(sector_cells) | set(industry_cells):
            s_map = sector_cells.get(metric, {})
            i_map = industry_cells.get(metric, {})
            labels = set(s_map) | set(i_map)
            newest = max(labels, key=_period_sort_key) if labels else None
            use_industry = bool(i_map) and (_is_mature(i_map.get(newest)) or not s_map)
            chosen = i_map if use_industry else s_map
            result[metric] = {
                label: cell for label, cell in chosen.items() if cell.get("value") is not None
            }

        _cache_set(cache_key, result)
        return result

    def get_benchmark_values(
        self,
        industry: str,
        sector: str,
        metrics: List[str],
        period_type: str,
    ) -> Dict[str, Dict[str, float]]:
        """Flat {metric: {period_label: median_value}} view of get_benchmarks (each
        period's merged industry/sector cell). For callers that only need the VALUE."""
        rich = self.get_benchmarks(industry, sector, metrics, period_type)
        flat = flatten_benchmark_values(rich)
        # Keep a failed DB call distinguishable from "no rows" through the flatten.
        return BenchmarkLookupFailed(flat) if lookup_failed(rich) else flat

    # ── Current-snapshot benchmark: TTM-first, mature-annual fallback ────

    def get_current_benchmarks(
        self,
        industry: str,
        sector: str,
        metrics: List[str],
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """The CURRENT single-value benchmark per metric for the "vs industry/sector
        avg" comparisons. Returns {metric: cell | None} where cell carries value /
        level / peer_group_name / n. Per metric, the first that exists:

        1. the industry TTM median, when at least MATURE_SAMPLE_FLOOR companies;
        2. the sector TTM median, when at least MATURE_SAMPLE_FLOOR companies
           (both via `merge_peer_cells` on the one "TTM" cell);
        3. the newest COMPLETE annual year with a mature median, industry before
           sector for that same year (only before the weekly TTM job has covered the
           group, or for a metric it does not compute);
        4. None — the card shows no peer comparison.

        Until 2026-10-07 a thin industry TTM (KO: Beverages, n=13) discarded the
        complete sector TTM and fell to the annual map, where it picked a partial
        2026 cohort (Consumer Defensive, n=39 of ~140) or a years-old sector median,
        and with no mature year it returned a 5-company cell."""
        ttm = self.get_benchmarks(industry, sector, metrics, TTM_PERIOD_TYPE)
        annual: Optional[Dict[str, Dict[str, Dict[str, Any]]]] = None
        result: Dict[str, Optional[Dict[str, Any]]] = {}
        for metric in metrics:
            cells = ttm.get(metric) or {}
            # exactly one TTM cell (period_label == "TTM"), already the mature level
            # when either layer is mature
            ttm_cell = cells.get("TTM") or (next(iter(cells.values())) if cells else None)
            if _is_mature(ttm_cell):
                result[metric] = ttm_cell
                continue
            if annual is None:  # lazy — only fetch the fallback layer if needed
                annual = self.get_benchmarks(industry, sector, metrics, "annual")
            cell, _held_back = pick_mature_benchmark(annual.get(metric) or {})
            result[metric] = cell
        # Carry a failed DB call through (the TTM layer, or the annual fallback layer).
        if lookup_failed(ttm) or lookup_failed(annual):
            return BenchmarkLookupFailed(result)
        return result

    def get_current_benchmark_values(
        self,
        industry: str,
        sector: str,
        metrics: List[str],
    ) -> Dict[str, Optional[float]]:
        """Flat {metric: value} of get_current_benchmarks (TTM-first, mature-annual
        fallback). Drop-in for the snapshot services' single-value comparisons."""
        rich = self.get_current_benchmarks(industry, sector, metrics)
        flat = {m: (cell["value"] if cell else None) for m, cell in rich.items()}
        return BenchmarkLookupFailed(flat) if lookup_failed(rich) else flat


# ── Singleton ─────────────────────────────────────────────────────

_lookup: Optional[SectorBenchmarkLookup] = None


def get_sector_benchmark_lookup() -> SectorBenchmarkLookup:
    global _lookup
    if _lookup is None:
        _lookup = SectorBenchmarkLookup()
    return _lookup
