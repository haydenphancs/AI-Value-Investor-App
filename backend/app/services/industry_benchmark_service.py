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
after a dyno restart and it resumes from the first un-fresh sector. The freshness
marker (the sector aggregate's annual rows) is the LAST write of a sector and a single
statement, so a sector that failed part-way never looks fresh; and a run in which any
sector failed — raised, computed ZERO rows while another sector wrote, or lost too many of
its companies to TRANSIENT FMP fetch failures (`_FetchTally.sector_loss`: its aggregate is
then not written at all, nor is any industry of it that is itself over the line) — raises
`IndustryBenchmarkRecomputeIncomplete`, so the scheduler retries it the same day and the
retry skips the sectors that did finish.

Off the event loop (2026-10-07): the web process runs ONE uvicorn worker, and the quarterly
and weekly runs are spawned in its lifespan. Every Supabase call here is the SYNC SDK, so
each write (`_write_fiscal_group` / `_write_ttm_group`: the medians and their upserts), each
freshness probe and the universe read run through `asyncio.to_thread`; so does turning one
industry's statements into value lists (`_value_lists_from`, pure CPU over ~300 companies).

The SECTOR aggregate pools only the companies for which a metric means something:
current ratio, quick ratio and interest coverage leave out the banks, insurers, capital-
markets firms and lenders (`financials_metric_gate.peer_metric_applicable`), so a Financial
Services exchange or data vendor compared with its sector is not compared with banks. Their
own industry rows are still written (never served for a gated company).

A MIXED industry's median is computed from its lenders only (owner decision 2026-10-09,
NET-4): `_load_universe` drops the curated non-lender members of "Financial - Credit
Services" (`financials_metric_gate.excluded_from_industry_median`: V, MA, PYPL, WU, GPN, TREE,
PMTS) before the top-N cut, with one INFO line naming them. They are never compared with
that median, and it is a lenders' yardstick for the lenders who are. They also leave the
Financial Services sector pool (5 of ~620 companies, immaterial).
"""

import asyncio
import functools
import json
import logging
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.services.financials_metric_gate import (
    excluded_from_industry_median,
    normalize_ticker,
    peer_metric_applicable,
)
from app.services.sector_benchmark_lookup import CALENDAR_QUARTER_PERIOD_TYPE
from app.services.sector_benchmark_service import (
    SectorBenchmarkService,
    METRIC_CONFIGS,
    MIN_SAMPLE_SIZE,
    UPSERT_BATCH_SIZE,
    BATCH_SIZE,
    BATCH_DELAY_SECONDS,
    FETCH_ERRORS_KEY,
    FMP_ANNUAL_LIMIT_BACKFILL,
    FMP_QUARTERLY_LIMIT_BACKFILL,
    COMPUTED_RATIO_FLOOR,
    COMPUTED_RATIO_CEIL,
    STORED_PERIOD_TYPE,
    FETCH_FAILED_CALLS_KEY,
    FETCH_FAILURE_KINDS,
    TRANSIENT_FETCH_FAILURES,
    _winsorize,
    call_with_rate_limit_retry,
    classify_fetch_failure,
    current_rate_limit_run,
    rate_limit_run,
)
from app.utils.period_labels import (
    BENCHMARK_REPORTING_LAG_DAYS,
    benchmark_period_complete,
    benchmark_period_end,
)
from app.utils.supabase_errors import is_check_violation

logger = logging.getLogger(__name__)


class IndustryBenchmarkRecomputeSkipped(RuntimeError):
    """A full `recompute_all` / `recompute_all_ttm` sweep wrote NOTHING. RAISED, never
    returned as a summary. Four reasons:

      * "empty universe" — the benchmark universe loaded as `[]` (checked before any write);
      * "universe file unreadable" — the operator named a universe file (`universe_file=`,
        the script's `--universe`) that could not be read (checked before any write);
      * "every sector failed" — every attempted sector raised (an upsert failure);
      * "nothing written" — sectors were attempted but none wrote a row: with FMP down,
        `_fetch_company_data` / `_fetch_ttm` turn every failed call into an empty result,
        so each sector "completes" with 0 rows.

    The first, third and fourth used to return a zero summary, and `_run_claimed_phase`
    (main.py) marks any phase that RETURNS as settled: the quarterly (fiscal) or weekly (TTM)
    claim recorded success and the run was consumed, so every "Industry/Sector Avg" stayed a
    quarter (or a week) stale. Raising releases the claim unsettled, so the loop retries the
    phase inside its catch-up window and the ledger row carries this message as its `error`.
    Same contract as `IndustryDossierRecomputeSkipped`.

    Still returns: a run where every sector was skipped as fresh (nothing attempted), a run
    where every attempted sector completed with rows, and the operator-only industries-only
    validation path and `dry_run` (neither is ever run by the scheduler, and a dry run writes
    nothing either way). A run where SOME sector raised or computed zero rows while others
    wrote raises `IndustryBenchmarkRecomputeIncomplete`.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(f"industry_benchmark recompute SKIPPED ({reason}) — {detail}")


class IndustryBenchmarkRecomputeIncomplete(RuntimeError):
    """A full `recompute_all` / `recompute_all_ttm` sweep WROTE rows, but at least one sector
    was left un-refreshed. RAISED, never returned. Three ways a sector is left behind:

      * it RAISED (an upsert failure, or a bug) — `failed_sectors`;
      * it computed ZERO rows (2026-10-07) — `empty_sectors`. A real sector always has
        rows, so zero means its whole fetch failed: an FMP 5xx outage or a 429 window with
        the breaker open that covered that sector's few minutes of the run, while the
        sectors before (or after) it wrote normally;
      * it LOST too many companies to TRANSIENT FMP fetch failures (review round 3, P3-1;
        only 429s, 5xx and network failures count since round 5, P4-2) — `lossy_sectors`:
        an outage that began or ended part-way through the sector, so its median would rest
        on the industries (and the largest companies) fetched before or after it. Its
        aggregate — the freshness marker — is not written (`_FetchTally.sector_loss` has the
        line), nor is an industry that is itself over the line (round 5, P4-1: its previous
        rows stay); its other industries' rows are.

    The message ends with a brief summary that carries `lossy_sectors`, `fetch_failures`,
    `fetch_failures_by_kind` and `industries_with_failures` (review round 5, DOC4-1), because a
    run that raises this never logs its `... complete:` summary line.

    A partial run used to RETURN its summary, so `_run_claimed_phase` (main.py) settled the
    quarterly (or weekly) claim and the left-behind sector kept last quarter's medians — or
    none — until the next scheduled run (~91 days for the fiscal rows). Raising leaves the
    claim unsettled: `_retry_unsettled_run` re-enters the run the same day, and because such
    a sector never wrote its freshness marker (`_upsert(marker_last=True)`) while the
    finished ones did, the retry skips the finished sectors cheaply and recomputes only
    the ones left behind.

    `reason`: "sectors " + the categories present, joined — "sectors failed" (only raised),
    "sectors empty" (only zero-row), "sectors lossy" (only lost to fetch failures), "sectors
    failed and empty", "sectors failed, empty and lossy", and so on. A sweep that wrote
    NOTHING at all is `IndustryBenchmarkRecomputeSkipped` instead (checked first).
    """

    def __init__(
        self, mode: str, failed_sectors: List[str], summary: Dict[str, Any],
        empty_sectors: Optional[List[str]] = None,
        *, lossy_sectors: Optional[List[str]] = None,
    ) -> None:
        self.failed_sectors = list(failed_sectors)
        self.empty_sectors = list(empty_sectors or [])
        self.lossy_sectors = list(lossy_sectors or [])
        kinds = [
            kind for kind, names in (
                ("failed", self.failed_sectors), ("empty", self.empty_sectors),
                ("lossy", self.lossy_sectors),
            ) if names
        ] or ["failed"]
        self.reason = "sectors " + (
            kinds[0] if len(kinds) == 1 else f"{', '.join(kinds[:-1])} and {kinds[-1]}"
        )
        self.summary = dict(summary)
        brief = {
            k: summary.get(k)
            for k in (
                "sectors_done", "sectors_failed", "sectors_empty", "sectors_lossy",
                "lossy_sectors", "sectors_skipped_fresh", "rows_upserted",
                "fetch_failures", "fetch_failures_by_kind", "industries_with_failures",
            )
        }
        parts = []
        if self.failed_sectors:
            parts.append(
                f"{len(self.failed_sectors)} sector(s) failed: {', '.join(self.failed_sectors)}"
            )
        if self.empty_sectors:
            parts.append(
                f"{len(self.empty_sectors)} sector(s) computed zero rows: "
                f"{', '.join(self.empty_sectors)}"
            )
        if self.lossy_sectors:
            parts.append(
                f"{len(self.lossy_sectors)} sector(s) lost too many companies to FMP fetch "
                f"failures: {', '.join(self.lossy_sectors)}"
            )
        super().__init__(
            f"industry_benchmark recompute INCOMPLETE ({'; '.join(parts)}) — {mode}: the "
            "other sectors' rows are written and marked fresh; these are not, so a same-day "
            f"retry recomputes only them. {brief}"
        )


def _empty_universe_skip(rows: str) -> IndustryBenchmarkRecomputeSkipped:
    """Log the empty-universe refusal at ERROR and return it for the caller to raise."""
    # `load_universe` already logged the Storage/parse reason at ERROR when the file itself
    # failed; this line names the consequence (and covers a file that loaded but holds no
    # usable sector/industry with market caps).
    exc = IndustryBenchmarkRecomputeSkipped(
        "empty universe",
        f"{BENCHMARK_UNIVERSE} yielded no sector with a usable industry (it is not in git: "
        "the Supabase Storage download failed, or the file holds no market_caps). Nothing "
        f"was written; the previous {rows} rows in sector_benchmarks are left untouched.",
    )
    logger.error("%s", exc)
    return exc


def _nothing_written_skip(
    rows: str, *, attempted: int, failed: int,
) -> IndustryBenchmarkRecomputeSkipped:
    """Log the nothing-written refusal at ERROR and return it for the caller to raise.

    Only for a sweep that ATTEMPTED sectors (fresh-skips are not attempts). A failed sector
    never adds to `rows_upserted`, so "every sector failed" is the all-raised case and
    "nothing written" is the mix (or all) of sectors that completed with zero rows."""
    reason = "every sector failed" if failed >= attempted else "nothing written"
    exc = IndustryBenchmarkRecomputeSkipped(
        reason,
        f"{attempted} sector(s) attempted ({failed} raised, {attempted - failed} computed "
        f"zero rows); no completed sector wrote a {rows} row. FMP was unreachable or "
        "refusing for the whole run, or every write failed — each sector's own ERROR/WARNING "
        "above names its cause. No sector's aggregate row was refreshed, so none counts as "
        "fresh and a retry recomputes them all.",
    )
    logger.error("%s", exc)
    return exc


def _incomplete_run(
    mode: str, failed_sectors: List[str], summary: Dict[str, Any],
    empty_sectors: Optional[List[str]] = None,
    lossy_sectors: Optional[List[str]] = None,
) -> IndustryBenchmarkRecomputeIncomplete:
    """Log the partial-run refusal at ERROR and return it for the caller to raise. Each
    failed sector already logged its own cause (with the stack) above this line, each
    empty one its zero-row WARNING (the fetch tally's WARNING names the failure kinds), and
    each lossy one its unwritten-aggregate WARNING with the counts."""
    exc = IndustryBenchmarkRecomputeIncomplete(
        mode, failed_sectors, summary, empty_sectors=empty_sectors,
        lossy_sectors=lossy_sectors,
    )
    logger.error("%s", exc)
    return exc


# Single resolver — see `app/services/universe_data.py`. There were FOUR different
# path idioms for this one directory, and the file is FMP-derived so it has to be
# able to move out of the repo (ToS §2.6.1) without a hunt.
from app.services.universe_data import (
    BENCHMARK_UNIVERSE,
    UNIVERSE_BUCKET,
    load_universe,
    universe_path,
)

# Cap per industry by market cap — medians stabilize well below this, and it bounds
# FMP cost + memory. Most industries above the $500M floor have fewer than this.
TOP_TICKERS_PER_INDUSTRY = 300
DEFAULT_SKIP_IF_FRESH_HOURS = 24


# ── The universe, read PER RUN ───────────────────────────────────────────────────────────
#
# `universe_data.load_universe` memoises a file for the life of the process (no TTL) and
# prefers its disk copy, which is right for the request-path readers of the other universe
# file but meant that a regenerated `benchmark_universe.json` uploaded to the `universe-data`
# bucket was not seen by the quarterly or weekly recompute until a redeploy. Only the
# recompute reads this file, so each run fetches it from Storage itself (one ~0.4 MB read).
# A failed fetch falls back to the copy the previous run fetched, then to the process memo,
# with a WARNING saying which copy the run used.
#
# An EXPLICITLY named file wins over Storage (2026-10-07): `recompute_all(universe_file=…)` /
# `recompute_all_ttm(universe_file=…)`, which only the operator script passes (`--universe
# PATH`, or the file in `UNIVERSE_DATA_DIR` when that is set). Without it, an operator who
# rebuilt the universe locally and validated it with `--dry-run` before uploading was shown
# the OLD bucket copy's medians, with one INFO line as the only clue. The scheduled runs
# never name a file — they must keep reading the bucket, or an upload would not take effect
# — and when a local `benchmark_universe.json` exists and differs from the bucket copy a run
# uses, `_note_local_copy` says so (WARNING unless the local copy is the older one, which is
# the normal state of a deployed instance's boot-time cache after an upload).
_last_fetched_universe: Optional[List[Dict[str, Any]]] = None


def _parse_generated_at(raw: Any) -> Optional[datetime]:
    """A universe payload's `generated_at` as an aware datetime, or None."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _note_local_copy(fetched: List[Dict[str, Any]], fetched_generated_at: Any) -> None:
    """Say so when a local `benchmark_universe.json` (`universe_path`, i.e. backend/data/ or
    `UNIVERSE_DATA_DIR`) exists and differs from the bucket copy this run uses. Never
    changes which copy is used. WARNING when the local copy is NEWER than the bucket's (or
    their ages cannot be compared): that is a fresh local build being ignored. INFO when it
    is older: a deployed instance's boot-time cache after an upload, which is expected."""
    path = universe_path(BENCHMARK_UNIVERSE)
    try:
        if not path.exists():
            return
        local = json.loads(path.read_text(encoding="utf-8"))
        local_industries = local.get("industries") if isinstance(local, dict) else None
        if not isinstance(local_industries, list):
            raise ValueError(f"'industries' is {type(local_industries).__name__}, expected list")
    except Exception as exc:
        logger.warning(
            "industry_benchmark: a local %s exists at %s but is unreadable (%s: %s) — "
            "ignored; this run uses the %s bucket copy",
            BENCHMARK_UNIVERSE, path, type(exc).__name__, exc, UNIVERSE_BUCKET,
        )
        return
    if local_industries == fetched:
        return
    local_generated_at = local.get("generated_at")
    local_dt = _parse_generated_at(local_generated_at)
    bucket_dt = _parse_generated_at(fetched_generated_at)
    older = local_dt is not None and bucket_dt is not None and local_dt <= bucket_dt
    logger.log(
        logging.INFO if older else logging.WARNING,
        "industry_benchmark: this run uses the %s bucket copy of %s (%d industries, generated "
        "%s), NOT the different local copy at %s (%d industries, generated %s)%s",
        UNIVERSE_BUCKET, BENCHMARK_UNIVERSE, len(fetched), fetched_generated_at or "?",
        path, len(local_industries), local_generated_at or "?",
        "" if older else (
            " — to compute from the local file, pass it explicitly "
            "(scripts.recompute_industry_benchmarks --universe PATH); to make it the live "
            f"universe, upload it to the {UNIVERSE_BUCKET} bucket"
        ),
    )


def _read_universe_file(path: str) -> List[Dict[str, Any]]:
    """The `industries` of an EXPLICITLY named universe file. RAISES
    `IndustryBenchmarkRecomputeSkipped` (after an ERROR log) when it cannot be read: the
    operator asked for this file, so falling back to another copy would compute from a
    universe they did not choose. Sync file I/O — called inside `asyncio.to_thread`."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        industries = payload.get("industries") if isinstance(payload, dict) else None
        if not isinstance(industries, list):
            raise ValueError(f"'industries' is {type(industries).__name__}, expected list")
        if not industries:
            raise ValueError("it lists no industries")
    except Exception as exc:
        skip = IndustryBenchmarkRecomputeSkipped(
            "universe file unreadable",
            f"the universe file named for this run, {path}, could not be read "
            f"({type(exc).__name__}: {exc}). Nothing was written.",
        )
        logger.error("%s", skip)
        raise skip from exc
    logger.warning(
        "industry_benchmark: this run uses the EXPLICITLY named universe file %s (%d "
        "industries, %s tickers, generated %s) — not the %s bucket copy the scheduled runs use",
        path, len(industries), payload.get("ticker_count", "?"),
        payload.get("generated_at", "?"), UNIVERSE_BUCKET,
    )
    return industries


def _fetch_benchmark_universe() -> Optional[List[Dict[str, Any]]]:
    """A FRESH copy of the benchmark universe's `industries` from Supabase Storage, or None
    when it could not be fetched or holds nothing usable (logged with the reason). Sync —
    the Storage SDK is sync, so async callers reach it through `asyncio.to_thread`."""
    try:
        blob = get_supabase().storage.from_(UNIVERSE_BUCKET).download(BENCHMARK_UNIVERSE)
    except Exception as exc:
        logger.warning(
            "industry_benchmark: could not fetch %s from the %s bucket for this run (%s: %s)",
            BENCHMARK_UNIVERSE, UNIVERSE_BUCKET, type(exc).__name__, exc,
        )
        return None
    try:
        payload = json.loads(bytes(blob).decode("utf-8"))
        industries = payload.get("industries") if isinstance(payload, dict) else None
        if not isinstance(industries, list):
            raise ValueError(f"'industries' is {type(industries).__name__}, expected list")
    except Exception as exc:
        logger.error(
            "industry_benchmark: the %s just fetched from the %s bucket is unreadable "
            "(%s: %s) — the upload is broken; this run keeps the previous copy",
            BENCHMARK_UNIVERSE, UNIVERSE_BUCKET, type(exc).__name__, exc,
        )
        return None
    if not industries:
        logger.error(
            "industry_benchmark: the %s just fetched from the %s bucket lists no industries "
            "— the upload is empty; this run keeps the previous copy",
            BENCHMARK_UNIVERSE, UNIVERSE_BUCKET,
        )
        return None
    logger.info(
        "industry_benchmark: fetched %s from the %s bucket for this run (%d industries, "
        "%s tickers, generated %s)",
        BENCHMARK_UNIVERSE, UNIVERSE_BUCKET, len(industries),
        payload.get("ticker_count", "?"), payload.get("generated_at", "?"),
    )
    _note_local_copy(industries, payload.get("generated_at"))
    return industries


def _benchmark_universe_entries(universe_file: Optional[str] = None) -> List[Dict[str, Any]]:
    """The universe entries for ONE recompute run: an explicitly named file when the caller
    passes one (the operator script); else Storage first, so an upload takes effect on the
    next run without a restart; else the copy the last run fetched; else the copy this
    process loaded (`load_universe`, which logs at ERROR and returns [] when it has none)."""
    global _last_fetched_universe
    if universe_file:
        return _read_universe_file(universe_file)
    fresh = _fetch_benchmark_universe()
    if fresh is not None:
        _last_fetched_universe = fresh
        return fresh
    if _last_fetched_universe is not None:
        logger.warning(
            "industry_benchmark: using the %s an earlier run fetched (%d industries) — an "
            "upload since then is not seen by this run",
            BENCHMARK_UNIVERSE, len(_last_fetched_universe),
        )
        return _last_fetched_universe
    logger.warning(
        "industry_benchmark: using this process's cached %s (loaded at boot) — an upload "
        "since then is not seen by this run",
        BENCHMARK_UNIVERSE,
    )
    return load_universe(BENCHMARK_UNIVERSE)

# The CHECK constraint that lists the allowed `sector_benchmarks.period_type` values.
# Migration 184 adds CALENDAR_QUARTER_PERIOD_TYPE to it; until it is applied every
# calendar-quarter row is refused with 23514 (see `_upsert`).
_PERIOD_TYPE_CHECK = "sector_benchmarks_period_type_check"

# A calendar quarter is not WRITTEN until it ended at least this many days before the
# run. Rows are keyed by the calendar quarter a period ENDS in, so a few days after a
# quarter closes its cells hold only the off-calendar filers whose quarter ended inside
# it and who have already reported (Jan-FY retailers, Nvidia/Cisco/Salesforce, the
# Aug-quarter Oracle/Nike/FedEx/Micron) — no calendar filer has reported yet. At sector
# level that cohort easily reaches the n>=20 maturity floor, so the readers' hold-back
# passed it as mature and it stood as the quarter's "Industry/Sector Avg" until the next
# quarterly run. 75 days clears the 40/45-day 10-Q deadlines; on the scheduled cadence
# (first Sunday of Jan/Apr/Jul/Oct) the just-closed quarter is skipped and the one
# before (~95 days old) is written complete. The newest quarter's peer value stays
# blank until then — never drawn from the early-reporter cohort.
#
# ANNUAL rows get the same gate (2026-10-07): year Y is written only from Dec 31 Y + 75
# days, i.e. by the April run. The 2026-10-04 run wrote "2026" cells from 6-27% of each
# group (Software-Infrastructure EPS growth 85% from 21 of 77 companies); the owner's rule
# is that an incomplete period shows no peer value at all. The readers apply the same rule
# to stored rows (`sector_benchmark_lookup.servable_benchmark_rows`), so a row written early
# by an older build never reaches a screen either. One constant for both: the shared
# `period_labels.BENCHMARK_REPORTING_LAG_DAYS`.
CALENDAR_QUARTER_MIN_AGE_DAYS = BENCHMARK_REPORTING_LAG_DAYS


def _calendar_quarter_end(label: Any) -> Optional[date]:
    """Last day of the calendar quarter a stored label names ("Q3'26" → 2026-09-30),
    or None for a label that is not a calendar-quarter key. Delegates to
    `period_labels.benchmark_period_end`, the readers' parser, so producer and readers
    cannot disagree. Its two-digit year reads as 20yy, which is why the producer never
    keys a quarter that ended before 2000 (`sector_benchmark_service.MIN_CALENDAR_QUARTER_YEAR`)."""
    return benchmark_period_end(CALENDAR_QUARTER_PERIOD_TYPE, label)


def _run_day(now: Any) -> date:
    """UTC calendar date of a run's `computed_at` ISO timestamp."""
    try:
        dt = datetime.fromisoformat(str(now).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        # Every caller passes datetime.now(timezone.utc).isoformat(); if that ever
        # changes, gate on today's date rather than skip the gate.
        logger.warning(
            "industry_benchmark: unparseable run timestamp %r — using today's UTC date "
            "for the annual / calendar-quarter age gate", now,
        )
        return datetime.now(timezone.utc).date()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).date()

# metric_name → type / cap, for the winsorization dispatch on the sector accumulator.
# (The `positive_only` filter is inherited automatically — we reuse the sector
# service's `_collect_metric_values`, which already drops non-positive values.)
_METRIC_TYPE: Dict[str, str] = {mc["name"]: mc["type"] for mc in METRIC_CONFIGS}
_METRIC_CAP: Dict[str, float] = {mc["name"]: mc["cap"] for mc in METRIC_CONFIGS if "cap" in mc}


def _winsorize_for(metric_name: str, metric_type: str, values: List[float]) -> List[float]:
    """Identical dispatch to SectorBenchmarkService._compute_sector: capped positive-
    only multiples (P/E·P/B·P/S, interest coverage) first, then wide bounds for
    yoy/qoq, tight 0-200 for computed multiples (EXCEPT fcf_margin — a signed decimal
    margin), no clamp for direct ratios + fcf_margin."""
    cap = _METRIC_CAP.get(metric_name)
    if cap is not None:
        return _winsorize(values, floor=0.0, ceil=cap)
    if metric_type in ("yoy", "qoq"):
        return _winsorize(values)
    if metric_type == "computed" and metric_name != "fcf_margin":
        return _winsorize(values, floor=COMPUTED_RATIO_FLOOR, ceil=COMPUTED_RATIO_CEIL)
    return values


# ── TTM (trailing-twelve-month) current-snapshot benchmarks ──────────────────
# Stored ADDITIVELY as period_type="ttm", period_label="TTM" — one current value
# per metric per industry/sector. The fiscal annual/quarterly rows are untouched
# (they remain the chart's historical line + the growth series). Every company
# contributes a complete rolling 12 months from FMP /ratios-ttm + /key-metrics-ttm
# (the SAME TTM the company card shows → apples-to-apples), so there is no
# partial-fiscal-year spike. Rule = positive-only + cap@200 + median (validated
# vs CSIMarket / stockanalysis across 4 industries; NO trim).
TTM_PERIOD_TYPE = "ttm"
TTM_PERIOD_LABEL = "TTM"
_TTM_CONCURRENCY = 12

# metric -> (source, field, positive_only, cap)   source: "r" ratios-ttm / "k" key-metrics-ttm
# fcf_margin is computed (freeCashFlowPerShareTTM ÷ revenuePerShareTTM). Only the
# USER-FACING comparison metrics are TTM'd; Moat-internal metrics (rd/sga/intangibles/
# deferred_revenue/roic/asset_turnover) stay on the annual series the Moat scorer reads.
_TTM_METRICS: Dict[str, Tuple[str, Optional[str], bool, Optional[float]]] = {
    "pe_ratio":          ("r", "priceToEarningsRatioTTM",      True,  200.0),
    "pb_ratio":          ("r", "priceToBookRatioTTM",          True,  200.0),
    "ps_ratio":          ("r", "priceToSalesRatioTTM",         True,  200.0),
    "pfcf_ratio":        ("r", "priceToFreeCashFlowRatioTTM",  True,  200.0),
    "ev_ebitda":         ("k", "evToEBITDATTM",                True,  200.0),
    "earnings_yield":    ("k", "earningsYieldTTM",             True,  None),
    "dividend_yield":    ("r", "dividendYieldTTM",             False, None),
    "gross_margin":      ("r", "grossProfitMarginTTM",         False, None),
    "operating_margin":  ("r", "operatingProfitMarginTTM",     False, None),
    "net_margin":        ("r", "netProfitMarginTTM",           False, None),
    "fcf_margin":        ("compute", None,                     False, None),
    "roe":               ("k", "returnOnEquityTTM",            False, None),
    "roa":               ("k", "returnOnAssetsTTM",            False, None),
    "current_ratio":     ("r", "currentRatioTTM",              False, None),
    "quick_ratio":       ("r", "quickRatioTTM",                False, None),
    "debt_to_equity":    ("r", "debtToEquityRatioTTM",         False, None),
    "interest_coverage": ("r", "interestCoverageRatioTTM",     True,  100.0),
}


def _num(d: Dict[str, Any], field: str) -> Optional[float]:
    v = d.get(field)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # Drop NaN AND +/-inf. `f == f` only filtered NaN; float('inf') == float('inf')
    # is True, so an inf field (FMP string 'Infinity', or a computed overflow) would
    # otherwise ride into statistics.median and corrupt an uncapped metric (roe/roa/
    # margins/debt_to_equity have cap=None + positive_only=False → no later guard).
    return f if math.isfinite(f) else None


def _extract_ttm(r0: Dict[str, Any], k0: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """One company's TTM value per metric (raw, pre-filter)."""
    out: Dict[str, Optional[float]] = {}
    for name, (src, field, _pos, _cap) in _TTM_METRICS.items():
        if src == "compute":  # fcf_margin = FCF/share ÷ revenue/share
            fcf = _num(r0, "freeCashFlowPerShareTTM")
            rev = _num(r0, "revenuePerShareTTM")
            # Require rev > 0 — MATCHES the fiscal path (sector_benchmark_service
            # ._compute_ratio_values: `fcf is not None and rev and rev > 0`). The old
            # `rev not in (None, 0)` admitted NEGATIVE revenue/share (sign-flips the
            # margin: a cash-generating firm looks deeply negative) — fcf_margin has
            # cap=None + positive_only=False, so nothing downstream catches it.
            out[name] = (fcf / rev) if (fcf is not None and rev is not None and rev > 0) else None
        else:
            out[name] = _num(r0 if src == "r" else k0, field)
    return out


# ── FMP fetch-failure accounting (per industry, per run) ─────────────────────────────────
#
# Every fetch layer degrades a failed FMP call to "no data for this company", which is right
# for one call and invisible for ten thousand: the 2026 runs reported `sectors_failed 0`
# whatever FMP had refused. Each ticker's fetch now ends in one outcome — ok, rate_limited
# (a 429 that outlived `call_with_rate_limit_retry`), unavailable (5xx / network after the
# client's retries), error (a malformed body, a bug), refused (a 4xx, 401, 402, or a 200 whose
# body is not a list: `sector_benchmark_service.classify_fetch_failure`) or empty (every call
# answered, none with data) — counted per industry and reported in the run summary.
#
# WARNING line (either rule): more than FETCH_FAILURE_WARN_SHARE of the run's tickers FAILED
# (every failure kind — upstream health; "empty" is excluded because it is a universe-quality
# signal, e.g. a fund in the universe), OR an industry with at least MIN_SAMPLE_SIZE tickers
# LOST more than INDUSTRY_LOSS_WARN_SHARE of them (failed + empty — its median then rests on
# noticeably fewer companies than the universe lists; a smaller industry never writes an
# industry row, so its loss cannot move one). The line names the worst industries.
#
# INCOMPLETE line (review round 3, P3-1). An outage that starts or ends part-way through a
# sector used to leave it pooled from whatever was fetched — industries are fetched in name
# order and companies largest first, so e.g. Technology without any "Software - *" industry —
# and then marked FRESH, so the same-day retry skipped it and the skewed sector median stood
# for the quarter (the TTM one for the week). A sector that LOST too many companies (below)
# now does NOT write its aggregate (the freshness marker) and the run raises
# `IndustryBenchmarkRecomputeIncomplete` ("sectors lossy"), so the retry recomputes it:
#   * more than SECTOR_FAILURE_INCOMPLETE_SHARE of the sector's tickers were lost, or
#   * an industry of at least MIN_SAMPLE_SIZE tickers lost more than
#     INDUSTRY_FAILURE_INCOMPLETE_SHARE of them (`_industry_over_line`).
# Such an industry is itself neither written nor pooled into the sector (review round 5,
# P4-1): its rows from the last full fetch stay. Writing it replaced e.g. Semiconductors'
# n~120 medians with its 40 largest companies' (still "mature" at n >= 20, and the readers
# look the industry row up first) until a retry got through — and for the quarter when the
# outage outlasted the retries. The sector's other industries are written.
#
# What counts as LOST (review round 5, P4-2) — a TRANSIENT failure only
# (`sector_benchmark_service.TRANSIENT_FETCH_FAILURES`: rate_limited, unavailable, which
# includes a raw 5xx), because the remedy, a same-day retry, can change only those. A refused
# or an 'error' answer comes back the same on every attempt, so counting it kept the run and
# the sector's aggregate unsettled for ever (and a TTM aggregate never rewritten is dropped
# as stale after 21 days); those are counted by kind in the summary and the WARNING line,
# never toward the INCOMPLETE line.
#   * TTM: a ticker is lost when either of its two calls failed transiently (each feeds
#     about half of the TTM metrics).
#   * Fiscal: a ticker is lost only when one of its CORE calls failed transiently —
#     `_FISCAL_LINE_CALLS`, the income statement and the ratios, annual and quarterly, which
#     feed the growth, margin, valuation-multiple and liquidity medians. A transient failure
#     of one of its six side calls (cash flow, key metrics, balance sheet) no longer marks the
#     whole ticker lost: its other statements are in the medians. That failure is still
#     counted, by kind. An endpoint-wide outage of a side call leaves its few metrics below
#     MIN_SAMPLE_SIZE, so no row is written and the previous one stays; a PARTIAL one can
#     skew those few metrics — the accepted cost of not holding every sector open over one
#     side endpoint, named by the WARNING line.
# "empty" answers never count: an industry that is structurally empty (funds in the universe,
# a delisted name) must not keep a sector — and the run — unsettled for ever. A failure that
# LOOKS transient but repeats (FMP answering one symbol 500 every time) still can; it is
# bounded by the scheduler's three same-day retries per run, each saying so at WARNING/ERROR.
SECTOR_FAILURE_INCOMPLETE_SHARE = 0.10
INDUSTRY_FAILURE_INCOMPLETE_SHARE = 0.25
FETCH_FAILURE_WARN_SHARE = 0.02
INDUSTRY_LOSS_WARN_SHARE = 0.25
_WORST_INDUSTRIES_LOGGED = 5
# Every failure kind, most telling first (a ticker with several is counted under the first).
_FAILURE_KINDS: Tuple[str, ...] = FETCH_FAILURE_KINDS
# The fiscal calls whose TRANSIENT failure makes a ticker lost to the INCOMPLETE line — keys of
# `SectorBenchmarkService._fetch_company_data` (a test pins that each one is fetched).
_FISCAL_LINE_CALLS: Tuple[str, ...] = (
    "income_annual", "income_quarterly", "ratios_annual", "ratios_quarterly",
)
# A lost ticker is also counted by its most telling transient kind, so the INCOMPLETE line's
# account says what it was lost to: {"rate_limited": "lost_rate_limited", "unavailable":
# "lost_unavailable"}, derived from the one list of transient kinds.
_LOST_KEYS: Dict[str, str] = {
    kind: f"lost_{kind}" for kind in _FAILURE_KINDS if kind in TRANSIENT_FETCH_FAILURES
}


def _new_counts() -> Dict[str, int]:
    return {
        "tickers": 0, **{k: 0 for k in _FAILURE_KINDS}, "empty": 0,
        **{k: 0 for k in _LOST_KEYS.values()},
    }


def _record_fetch_outcome(
    counts: Optional[Dict[str, int]], failures: List[str], has_data: bool,
    line_failures: Optional[List[str]] = None,
) -> None:
    """Count one ticker into an industry's `counts`.

    By kind (the summary and the WARNING line): a ticker with ANY failed call counts as
    failed (some of its metrics are missing), under its most telling kind — a 429 first (the
    back-off was not enough), then 5xx / network, then anything else ('error', also an
    unknown kind), then a refusal.

    Toward the INCOMPLETE line (`lost_*`): the ticker is LOST when one of `line_failures` is
    TRANSIENT — by default all of its `failures` (the TTM sweep); the fiscal sweep passes only
    its core calls' kinds (`_fiscal_line_failures`)."""
    if counts is None:
        return
    counts["tickers"] += 1
    if not failures:
        if not has_data:
            counts["empty"] += 1
        return
    for kind in _FAILURE_KINDS:
        if kind in failures:
            counts[kind] += 1
            break
    else:
        counts["error"] += 1
    line = failures if line_failures is None else line_failures
    for kind, key in _LOST_KEYS.items():   # rate_limited first: the most telling
        if kind in line:
            counts[key] += 1
            break


def _fiscal_line_failures(failed_calls: Any) -> Optional[List[str]]:
    """The failure kinds of a fiscal ticker's CORE calls (`_FISCAL_LINE_CALLS`), from its
    FETCH_FAILED_CALLS_KEY map; None — every failure counts, the conservative pre-round-5
    rule — when the fetch did not say which calls failed."""
    if not isinstance(failed_calls, dict):
        return None
    return [failed_calls[k] for k in _FISCAL_LINE_CALLS if k in failed_calls]


def _lost(c: Dict[str, int]) -> int:
    """Tickers of `c` lost to the INCOMPLETE line (transient failures only)."""
    return sum(c[key] for key in _LOST_KEYS.values())


def _industry_over_line(c: Dict[str, int]) -> bool:
    """An industry of at least MIN_SAMPLE_SIZE tickers that lost more than
    INDUSTRY_FAILURE_INCOMPLETE_SHARE of them: it is neither written nor pooled (P4-1), and
    its sector does not write its aggregate."""
    return (
        c["tickers"] >= MIN_SAMPLE_SIZE
        and _lost(c) / c["tickers"] > INDUSTRY_FAILURE_INCOMPLETE_SHARE
    )


def _lost_account(c: Dict[str, int]) -> str:
    """'<lost> of <tickers> tickers lost to transient fetch failures (<share>: <n>
    rate-limited, <n> unavailable)' for a sector's summed counts."""
    lost, tickers = _lost(c), c["tickers"]
    return (
        f"{lost} of {tickers} tickers lost to transient fetch failures "
        f"({lost / tickers if tickers else 0:.0%}: {c['lost_rate_limited']} rate-limited, "
        f"{c['lost_unavailable']} unavailable)"
    )


def _log_unwritten_aggregate(prefix: str, sector: str, loss: str) -> None:
    """The WARNING for a sector over the INCOMPLETE line (`_FetchTally.sector_loss`)."""
    logger.warning(
        "%s: %s — the sector aggregate is NOT written: %s (the line: more than %.0f%% of the "
        "sector's tickers, or more than %.0f%% of an industry of >= %d, lost to transient "
        "fetch failures). Its industries under the line are written (one over it keeps its "
        "previous rows); without a fresh aggregate the run will not settle, so a same-day "
        "retry recomputes the sector",
        prefix, sector, loss, SECTOR_FAILURE_INCOMPLETE_SHARE * 100,
        INDUSTRY_FAILURE_INCOMPLETE_SHARE * 100, MIN_SAMPLE_SIZE,
    )


def _log_withheld_industries(prefix: str, sector: str, notes: List[str]) -> None:
    """ONE WARNING per sector naming the industries over the INCOMPLETE line whose PARTIAL
    fetch the line kept out (P4-1): none of them was written or pooled, so each keeps the rows
    of its last full fetch. A full sweep leaves out an industry that fetched nothing usable at
    all — it could not have been written either, and the tally WARNING (the worst industries)
    and the sector's zero-rows or unwritten-aggregate WARNING already account for it (one more
    line per sector in an outage would be noise); the summary's `industries_withheld` lists
    both."""
    if not notes:
        return
    one = len(notes) == 1
    logger.warning(
        "%s: %s — %d industr%s NOT written and left out of the sector pool: over the loss line "
        "(an industry of >= %d tickers that lost more than %.0f%% of them to transient fetch "
        "failures), so %s previous rows stay rather than a median over only the companies "
        "fetched around the outage: %s",
        prefix, sector, len(notes), "y" if one else "ies", MIN_SAMPLE_SIZE,
        INDUSTRY_FAILURE_INCOMPLETE_SHARE * 100, "its" if one else "their", "; ".join(notes),
    )


def _rate_limit_summary() -> Dict[str, Any]:
    """The current run's shared-429-window totals for its summary (`rate_limit_run`)."""
    run = current_rate_limit_run()
    return run.summary() if run is not None else {}


def _rate_limited_run(label: str):
    """Run the decorated recompute inside one `rate_limit_run(label)`: its FMP 429 window
    waits share (and are bounded by) one per-run budget, logged when it ends."""
    def decorate(fn: Callable[..., Awaitable[Dict[str, Any]]]):
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
            with rate_limit_run(label):
                return await fn(*args, **kwargs)
        return wrapper
    return decorate


class _FetchTally:
    """Per-run fetch outcome counts, keyed (sector, industry), and the industries the run
    withheld for being over the INCOMPLETE line (`withhold`)."""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self._groups: Dict[Tuple[str, str], Dict[str, int]] = {}
        self.withheld: List[str] = []   # "Sector / Industry", in the order they were met

    def counts_for(self, sector: str, industry: str) -> Dict[str, int]:
        return self._groups.setdefault((sector, industry), _new_counts())

    def withhold(self, sector: str, industry: str) -> Optional[str]:
        """Call once per industry, after its fetch. None when the industry is within the
        INCOMPLETE line (the caller writes and pools it); else its one-line account, and the
        industry is recorded as withheld — the caller must neither write nor pool it (P4-1)."""
        c = self._groups.get((sector, industry))
        if c is None or not _industry_over_line(c):
            return None
        name = f"{sector} / {industry}"
        if name not in self.withheld:
            self.withheld.append(name)
        return (
            f"{industry} {_lost(c)}/{c['tickers']} ({_lost(c) / c['tickers']:.0%}: "
            f"{c['lost_rate_limited']} rate-limited, {c['lost_unavailable']} unavailable)"
        )

    @staticmethod
    def _failed(c: Dict[str, int]) -> int:
        return sum(c[k] for k in _FAILURE_KINDS)

    def summary(self) -> Dict[str, Any]:
        totals = _new_counts()
        for c in self._groups.values():
            for k in totals:
                totals[k] += c[k]
        return {
            "tickers_fetched": totals["tickers"],
            "fetch_failures": self._failed(totals),
            "fetch_failures_by_kind": {k: totals[k] for k in _FAILURE_KINDS},
            "fetch_empty": totals["empty"],
            "industries_with_failures": sum(1 for c in self._groups.values() if self._failed(c)),
            # The subset of `fetch_failures` the INCOMPLETE line counts (transient only).
            "fetch_lost_transient": _lost(totals),
            "industries_withheld": list(self.withheld),
        }

    def sector_loss(self, sector: str) -> Optional[str]:
        """None when `sector`'s LOST tickers (transient failures only — see the INCOMPLETE line
        above) are within the line, else a one-line account of them (the sector then does not
        write its aggregate and does not settle). Empty, refused and 'error' answers never
        count; the account names the last two when there are any. A sector this tally never
        saw is None."""
        groups = [(ind, c) for (s, ind), c in self._groups.items() if s == sector]
        totals = _new_counts()
        for _, c in groups:
            for k in totals:
                totals[k] += c[k]
        tickers, lost = totals["tickers"], _lost(totals)
        if not tickers or not lost:
            return None
        heavy = sorted(
            ((_lost(c) / c["tickers"], ind, c) for ind, c in groups if _industry_over_line(c)),
            key=lambda r: (-r[0], r[1]),
        )
        if lost / tickers <= SECTOR_FAILURE_INCOMPLETE_SHARE and not heavy:
            return None
        account = _lost_account(totals)
        if totals["refused"] or totals["error"]:
            account += (
                f"; not counted toward the line: {totals['refused']} refused, "
                f"{totals['error']} other"
            )
        if heavy:
            account += "; industries over the line: " + "; ".join(
                f"{ind} {_lost(c)}/{c['tickers']} ({share:.0%})"
                for share, ind, c in heavy[:_WORST_INDUSTRIES_LOGGED]
            )
        return account

    def _ranked_losses(self) -> List[Tuple[float, int, str, str, Dict[str, int]]]:
        ranked = []
        for (sector, industry), c in self._groups.items():
            lost = self._failed(c) + c["empty"]
            if c["tickers"] and lost:
                ranked.append((lost / c["tickers"], lost, sector, industry, c))
        ranked.sort(key=lambda r: (-r[0], -r[1], r[2], r[3]))
        return ranked

    def log(self) -> None:
        """One WARNING when either threshold above is crossed, naming the worst industries."""
        s = self.summary()
        tickers, failed = s["tickers_fetched"], s["fetch_failures"]
        if not tickers:
            return
        ranked = self._ranked_losses()
        heavy = [
            r for r in ranked
            if r[4]["tickers"] >= MIN_SAMPLE_SIZE and r[0] > INDUSTRY_LOSS_WARN_SHARE
        ]
        if failed / tickers <= FETCH_FAILURE_WARN_SHARE and not heavy:
            return
        worst = "; ".join(
            f"{sector} / {industry} {lost}/{c['tickers']} ({share:.0%}: "
            f"{c['rate_limited']} rate-limited, {c['unavailable']} unavailable, "
            f"{c['refused']} refused, {c['error']} other, {c['empty']} empty)"
            for share, lost, sector, industry, c in (heavy or ranked)[:_WORST_INDUSTRIES_LOGGED]
        )
        by_kind = s["fetch_failures_by_kind"]
        permanent = by_kind["refused"] + by_kind["error"]
        logger.warning(
            "industry_benchmark [%s run]: FMP fetch failures above the warning line (> %.0f%% of "
            "tickers failed run-wide, or an industry of >= %d tickers lost > %.0f%%): %d of %d "
            "tickers failed (%d rate-limited after retries, %d unavailable, %d refused, %d "
            "other) and %d answered empty; %d industries had failures. Worst: %s — their "
            "medians rest on fewer companies than the universe lists%s",
            self.mode, FETCH_FAILURE_WARN_SHARE * 100, MIN_SAMPLE_SIZE,
            INDUSTRY_LOSS_WARN_SHARE * 100, failed, tickers,
            by_kind["rate_limited"], by_kind["unavailable"], by_kind["refused"],
            by_kind["error"], s["fetch_empty"], s["industries_with_failures"], worst,
            (
                f". The {permanent} refused / other never hold a run open (a retry gets the "
                "same answer): a 4xx, 401 or 402, or a body that is not a statement list, "
                "points at the FMP plan, the key or the universe"
            ) if permanent else "",
        )


class IndustryBenchmarkService:
    def __init__(self) -> None:
        self.supabase = get_supabase()
        # Reuse the sector service's FMP fetch + per-group aggregation + throttle.
        self._sb = SectorBenchmarkService()
        self._fmp = get_fmp_client()  # for the TTM /ratios-ttm + /key-metrics-ttm path
        # Set when the database refused a calendar-quarter row because migration 184
        # is not applied; reset at the start of every run (see `_upsert`).
        self._calendar_quarter_blocked = False

    # ── Universe ─────────────────────────────────────────────────────
    def _load_universe(
        self, universe_file: Optional[str] = None,
    ) -> List[Tuple[str, List[Tuple[str, List[Tuple[str, float]]]]]]:
        """[(sector, [(industry, [(ticker, cap)...] top-N by cap), ...]), ...].

        Read PER RUN (`_benchmark_universe_entries`): Storage (or the explicitly named
        `universe_file`) is asked each time, so it is SYNC I/O — the async run paths call it
        through `asyncio.to_thread` (`_universe_for_run`)."""
        industries = _benchmark_universe_entries(universe_file)
        if not industries:
            # `load_universe` already logged at ERROR with the reason.
            return []
        by_sector: Dict[str, List[Tuple[str, List[Tuple[str, float]]]]] = defaultdict(list)
        # Curated non-lender members left out of a MIXED industry's median, per industry.
        excluded: Dict[str, List[str]] = defaultdict(list)
        for entry in industries:
            ind = entry.get("industry")
            sector = entry.get("sector")
            mcaps = entry.get("market_caps") or {}
            if not ind or not sector or sector == "Unknown" or not mcaps:
                continue
            # TOTAL coercion: a single non-numeric market cap ('N/A', '', a
            # CSV-formatted '3,000,000', None) used to raise ValueError INSIDE the
            # sorted() generator — and since _load_universe is called OUTSIDE the
            # per-sector try/except, one bad value aborted the ENTIRE recompute (every
            # sector, zero rows). Drop + warn the bad ticker instead so the job degrades.
            pairs: List[Tuple[str, float]] = []
            for t, c in mcaps.items():
                # BEFORE the top-N cut, so a dropped member frees its slot for a lender.
                if excluded_from_industry_median(t, ind):
                    excluded[ind].append(normalize_ticker(t))
                    continue
                try:
                    cap = float(c)
                except (TypeError, ValueError):
                    logger.warning(
                        "industry_benchmark: %s — dropping %r, non-numeric market cap %r",
                        ind, t, c,
                    )
                    continue
                if not math.isfinite(cap):
                    continue
                pairs.append((t, cap))
            sorted_tkrs = sorted(
                pairs, key=lambda x: x[1], reverse=True,
            )[:TOP_TICKERS_PER_INDUSTRY]
            if sorted_tkrs:
                by_sector[sector].append((ind, sorted_tkrs))
        if excluded:
            logger.info(
                "industry_benchmark: left out of the mixed industry's median (curated "
                "non-lender members — never compared with it; the median is the lenders'): "
                "%s",
                "; ".join(
                    f"{ind}: {', '.join(sorted(tickers))}"
                    for ind, tickers in sorted(excluded.items())
                ),
            )
        return sorted(by_sector.items())

    async def _universe_for_run(
        self, universe_file: Optional[str],
    ) -> List[Tuple[str, List[Tuple[str, List[Tuple[str, float]]]]]]:
        """`_load_universe` off the event loop. The no-file call keeps its zero-argument
        shape (tests and the scheduled runs never name a file)."""
        if universe_file:
            return await asyncio.to_thread(self._load_universe, universe_file)
        return await asyncio.to_thread(self._load_universe)

    # ── Resumability ─────────────────────────────────────────────────
    def _sector_is_fresh(self, sector: str, hours: Optional[int]) -> bool:
        """True when the sector aggregate's ANNUAL rows were written within `hours`.

        Those rows are the freshness MARKER, and `_compute_sector` writes them LAST, in ONE
        upsert statement (`_upsert(marker_last=True)`): after every industry row and after
        the aggregate's calendar-quarter rows. So a sector that failed anywhere before the
        end — an industry batch, an aggregate calendar-quarter batch, or the marker itself —
        has no fresh marker and is recomputed by the next run. (When the marker was written
        in 100-row batches with the calendar-quarter rows after it, a failure in a later
        batch left an early batch fresh and the sector was skipped on the retry.)"""
        return self._aggregate_is_fresh(sector, "annual", hours)

    def _aggregate_is_fresh(self, sector: str, period_type: str, hours: Optional[int]) -> bool:
        if not hours:
            return False
        try:
            resp = (
                self.supabase.table("sector_benchmarks")
                .select("computed_at")
                .eq("sector", sector).eq("industry", "")
                # One period_type only. Without this filter the FISCAL probe saw the
                # newest computed_at across ALL period_types — including the weekly 'ttm'
                # rows — so a fresh Sunday TTM write made the quarterly fiscal recompute
                # skip EVERY sector, freezing the chart history + growth series.
                .eq("period_type", period_type)
                .order("computed_at", desc=True).limit(1).execute()
            )
        except Exception as e:
            logger.warning(
                "industry_benchmark: freshness probe for %s (%s) failed (%s: %s) — "
                "recomputing it", sector, period_type, type(e).__name__, e,
            )
            return False
        if not resp.data:
            return False
        row = resp.data[0]
        raw = row.get("computed_at") if isinstance(row, dict) else None
        try:
            last = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
        except ValueError as e:
            logger.warning(
                "industry_benchmark: unreadable computed_at %r on the %s (%s) aggregate "
                "(%s) — recomputing it", raw, sector, period_type, e,
            )
            return False
        return (datetime.now(timezone.utc) - last) < timedelta(hours=hours)

    # ── Fetch (batched + throttled, reusing the sector service's semaphore) ──
    async def _fetch_batched(
        self, tickers: List[str], al: int, ql: int,
        counts: Optional[Dict[str, int]] = None,
    ) -> List[Dict[str, List]]:
        """Each ticker's statements (`_fetch_company_data`), with its fetch outcome counted
        into `counts` (one industry's `_FetchTally.counts_for`): every failed call by kind,
        and the ticker as LOST to the INCOMPLETE line only when one of its core calls
        (`_FISCAL_LINE_CALLS`) failed transiently."""
        out: List[Dict[str, List]] = []
        for i in range(0, len(tickers), BATCH_SIZE):
            batch = tickers[i:i + BATCH_SIZE]
            results = await asyncio.gather(
                *[self._sb._fetch_company_data(t, al, ql) for t in batch],
                return_exceptions=True,
            )
            for ticker, r in zip(batch, results):
                if not isinstance(r, dict):
                    # `_fetch_company_data` turns every failed CALL into an empty list, so
                    # reaching here is a bug in it, not an FMP answer — say so.
                    logger.warning(
                        "industry_benchmark: fetching %s raised %s: %s — counted as a "
                        "failed fetch", ticker, type(r).__name__, r,
                    )
                    _record_fetch_outcome(
                        counts,
                        [classify_fetch_failure(r) if isinstance(r, BaseException) else "error"],
                        False,
                    )
                    continue
                _record_fetch_outcome(
                    counts,
                    list(r.get(FETCH_ERRORS_KEY) or []),
                    any(
                        isinstance(v, list) and v for k, v in r.items()
                        if k not in (FETCH_ERRORS_KEY, FETCH_FAILED_CALLS_KEY)
                    ),
                    line_failures=_fiscal_line_failures(r.get(FETCH_FAILED_CALLS_KEY)),
                )
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
        run_day = _run_day(now)
        too_recent: set = set()   # calendar quarters ended < CALENDAR_QUARTER_MIN_AGE_DAYS ago
        unkeyed: set = set()      # calendar-quarter rows whose label names no quarter
        recent_years: set = set()     # annual years ended < BENCHMARK_REPORTING_LAG_DAYS ago
        unkeyed_years: set = set()    # annual rows whose label names no year
        for (metric_name, period_type, period_label), values in values_by_key.items():
            if len(values) < MIN_SAMPLE_SIZE:
                continue
            if period_type == CALENDAR_QUARTER_PERIOD_TYPE:
                quarter_end = _calendar_quarter_end(period_label)
                if quarter_end is None:
                    unkeyed.add(period_label)
                    continue
                if (run_day - quarter_end).days < CALENDAR_QUARTER_MIN_AGE_DAYS:
                    too_recent.add(period_label)
                    continue
            elif period_type == "annual":
                # Same rule as the calendar quarters above: "2026" is written only from
                # 2027-03-16 — before that its cells hold only the early filers.
                complete = benchmark_period_complete("annual", period_label, run_day)
                if complete is None:
                    unkeyed_years.add(period_label)
                    continue
                if not complete:
                    recent_years.add(period_label)
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
        group = f"{sector} / {industry}" if industry else f"{sector} (sector aggregate)"
        if too_recent:
            logger.info(
                "industry_benchmark: %s — not writing calendar quarter(s) %s: ended less "
                "than %d days before this run (%s), so only early off-calendar filers "
                "have reported; written by a later run",
                group, ", ".join(sorted(too_recent, key=_calendar_quarter_end)),
                CALENDAR_QUARTER_MIN_AGE_DAYS, run_day.isoformat(),
            )
        if unkeyed:
            logger.warning(
                "industry_benchmark: %s — dropped calendar-quarter row(s) with no "
                "calendar-quarter label: %s",
                group, ", ".join(sorted(repr(x) for x in unkeyed)),
            )
        if recent_years:
            logger.info(
                "industry_benchmark: %s — not writing annual year(s) %s: the year ended less "
                "than %d days before this run (%s), so most of the group has not filed it "
                "yet; written by a later run",
                group, ", ".join(sorted(recent_years)),
                BENCHMARK_REPORTING_LAG_DAYS, run_day.isoformat(),
            )
        if unkeyed_years:
            logger.warning(
                "industry_benchmark: %s — dropped annual row(s) with no four-digit year "
                "label: %s",
                group, ", ".join(sorted(repr(x) for x in unkeyed_years)),
            )
        return rows

    def _upsert(self, rows: List[Dict[str, Any]], *, marker_last: bool = False) -> int:
        """Upsert in batches. RAISES on any batch failure (after logging) so the
        caller's per-sector try/except aborts BEFORE writing that sector's '' aggregate
        row. Otherwise a sector whose industry rows failed mid-write would still get a
        fresh '' timestamp and be wrongly SKIPPED on the next resume (the freshness
        probe only checks the '' row) — leaving its industry rows missing for a whole
        cycle. On abort the per-sector guard logs + continues to the next sector, and
        the failed sector (never marked fresh) is retried in full next run.

        ONE exception, for the code-before-migration window: a 23514 on the period_type
        CHECK (migration 184 not applied yet) skips calendar-quarter rows for the rest of
        the run with an ERROR instead of aborting the sector — so a forgotten migration
        costs the quarterly peer lines only, not every sector's annual refresh. The run's
        summary reports it (`calendar_quarter_blocked`); re-run with skip_recent_hours=0
        once 184 is applied.

        Order: industry rows (the default) write the annual rows first, then the
        calendar-quarter rows in their own batches. A SECTOR AGGREGATE passes
        `marker_last=True`: its calendar-quarter rows go FIRST and its other rows — the
        annual rows `_sector_is_fresh` reads (or the TTM rows `_ttm_sector_is_fresh`
        reads) — LAST, in ONE upsert statement, which Postgres applies all-or-nothing. A
        failure anywhere in the sector therefore leaves its marker stale, never half-
        fresh. (One sector aggregate holds ~27 annual metrics × ≤ 16 years ≈ 430 rows, or
        ≤ 17 TTM rows — one small request.)"""
        others = [r for r in rows if r.get("period_type") != CALENDAR_QUARTER_PERIOD_TYPE]
        calendar_quarter = [
            r for r in rows if r.get("period_type") == CALENDAR_QUARTER_PERIOD_TYPE
        ]
        if marker_last:
            n = self._upsert_calendar_quarter(calendar_quarter)
            return n + self._upsert_batches(others, batch_size=max(len(others), 1))
        n = self._upsert_batches(others)
        return n + self._upsert_calendar_quarter(calendar_quarter)

    def _upsert_calendar_quarter(self, rows: List[Dict[str, Any]]) -> int:
        """Calendar-quarter rows in their own batches; the migration-184 degrade above."""
        if not rows or self._calendar_quarter_blocked:
            return 0
        try:
            return self._upsert_batches(rows)
        except Exception as e:
            if not is_check_violation(e, _PERIOD_TYPE_CHECK):
                raise
            self._calendar_quarter_blocked = True
            logger.error(
                "industry_benchmark: the database refused period_type=%r (%s: %s) — "
                "migration 184 (184_calendar_quarter_benchmarks.sql) is not applied. "
                "Skipping every calendar-quarter row for the rest of this run; annual "
                "rows are still written. Apply 184, then re-run with skip_recent_hours=0.",
                CALENDAR_QUARTER_PERIOD_TYPE, type(e).__name__, e,
            )
            return 0

    def _upsert_batches(
        self, rows: List[Dict[str, Any]], batch_size: int = UPSERT_BATCH_SIZE,
    ) -> int:
        n = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            try:
                self.supabase.table("sector_benchmarks").upsert(
                    batch,
                    on_conflict="sector,industry,metric_name,period_type,period_label",
                ).execute()
                n += len(batch)
            except Exception as e:
                # A refused calendar-quarter period_type is logged once, by `_upsert`,
                # which degrades instead of aborting — don't also report it as an abort.
                if not is_check_violation(e, _PERIOD_TYPE_CHECK):
                    logger.error(
                        "industry_benchmark upsert batch failed (%d rows written before "
                        "failure; aborting sector for retry): %s: %s",
                        n, type(e).__name__, e,
                    )
                raise
        return n

    def _emit(
        self, rows: List[Dict[str, Any]], label: str, dry_run: bool,
        *, marker_last: bool = False,
    ) -> int:
        """Upsert, or (dry_run) log a sample of the computed medians and write nothing.
        `marker_last` is for a SECTOR AGGREGATE (see `_upsert`)."""
        if dry_run:
            self._log_sample(label, rows)
            return 0
        return self._upsert(rows, marker_last=marker_last)

    # SYNC: the medians (`statistics.median` over a sector's pooled lists) and the PostgREST
    # upserts block, and the web process has ONE event loop — every async caller runs these
    # two through `asyncio.to_thread`. A 2026 quarterly run was ~3,800 upsert round-trips.
    def _write_fiscal_group(
        self, sector: str, industry: str,
        values_by_key: Dict[Tuple[str, str, str], List[float]], now: str, dry_run: bool,
        *, marker_last: bool = False,
    ) -> int:
        """Build one group's fiscal rows (`_rows_from_values`) and write them (`_emit`).
        `industry=''` is the sector aggregate (pass `marker_last=True`)."""
        label = f"{sector} / {industry}" if industry else f"{sector} (sector aggregate)"
        return self._emit(
            self._rows_from_values(sector, industry, values_by_key, now), label, dry_run,
            marker_last=marker_last,
        )

    def _write_ttm_group(
        self, sector: str, industry: str, values_by_metric: Dict[str, List[float]],
        now: str, dry_run: bool, *, marker_last: bool = False,
    ) -> int:
        """`_write_fiscal_group` for the TTM rows (`_ttm_rows`)."""
        label = f"TTM {sector}/{industry}" if industry else f"TTM {sector} (aggregate)"
        return self._emit(
            self._ttm_rows(sector, industry, values_by_metric, now), label, dry_run,
            marker_last=marker_last,
        )

    @staticmethod
    def _pool_into_sector(
        sector_acc: Dict[Any, List[float]], values: Dict[Any, List[float]], industry: str,
        left_out: Dict[str, set],
    ) -> None:
        """Add one industry's raw values to its sector's pool — except a metric that means
        nothing for that industry (`financials_metric_gate.peer_metric_applicable`: current
        and quick ratio, interest coverage for banks, insurers, capital markets, lenders),
        recorded in `left_out[metric]` for the sector's log line. Keys are
        (metric, period_type, label) for the fiscal rows and the metric name for TTM.

        Without this the Financial Services aggregate for those metrics was ~95% banks and
        insurers, and every thin kept industry (exchanges and data vendors: SPGI, MCO, CME,
        ICE, NDAQ…) whose own cell is below n=20 was judged against it."""
        for key, vals in values.items():
            metric = key[0] if isinstance(key, tuple) else key
            if not peer_metric_applicable(metric, industry):
                left_out[metric].add(industry)
                continue
            sector_acc[key].extend(vals)

    @staticmethod
    def _log_left_out(group: str, left_out: Dict[str, set]) -> None:
        """One INFO line per sector naming the industries a gated metric's pool left out."""
        if not left_out:
            return
        industries = sorted(set().union(*left_out.values()))
        logger.info(
            "industry_benchmark: %s — %s pool only the industries where they mean something; "
            "left out %d industr%s: %s",
            group, ", ".join(sorted(left_out)), len(industries),
            "y" if len(industries) == 1 else "ies", ", ".join(industries),
        )

    @staticmethod
    def _log_sample(label: str, rows: List[Dict[str, Any]]) -> None:
        """Log a few headline medians at each metric's MOST-SAMPLED annual year (so a
        thin partial current year doesn't mislead) — a sanity check before a full run. A
        `--ttm` run writes only `ttm` rows (one per metric): those are logged, where an
        annual-only filter used to print the header over nothing (2026-10-08)."""
        best: Dict[str, Any] = {}
        for r in rows:
            if r["period_type"] not in ("annual", "ttm"):
                continue
            cur = best.get(r["metric_name"])
            if cur is None or r["sample_size"] > cur["sample_size"] or (
                r["sample_size"] == cur["sample_size"]
                and r["period_label"] > cur["period_label"]
            ):
                best[r["metric_name"]] = r
        logger.info(
            "DRY-RUN %s — %d rows. Median at each metric's most-sampled annual year (or TTM):",
            label, len(rows),
        )
        for m in ("gross_margin", "operating_margin", "net_margin", "fcf_margin",
                  "roe", "roa", "pe_ratio", "pb_ratio", "ps_ratio", "pfcf_ratio", "ev_ebitda",
                  "earnings_yield", "debt_to_equity", "interest_coverage"):
            r = best.get(m)
            if r:
                logger.info(
                    "    %-18s %s: median=%s (n=%d)",
                    m, r["period_label"], r["median_value"], r["sample_size"],
                )

    # ── Per-sector compute (stream industries, accumulate into the sector) ──
    async def _industry_value_lists(
        self, ticker_caps: List[Tuple[str, float]], al: int, ql: int,
        counts: Optional[Dict[str, int]] = None,
    ) -> Dict[Tuple[str, str, str], List[float]]:
        """Fetch one industry's companies and return {(metric,period_type,period):[raw values]}.
        Each ticker's fetch outcome is counted into `counts` (see `_FetchTally`)."""
        company_data = await self._fetch_batched(
            [t for t, _ in ticker_caps], al, ql, counts=counts,
        )
        if not company_data:
            return defaultdict(list)
        # Pure CPU (per company and metric: dedupe, sort, YoY/QoQ over ~16 annual + ~80
        # quarterly rows), off the event loop like the writes.
        return await asyncio.to_thread(self._value_lists_from, company_data)

    def _value_lists_from(
        self, company_data: List[Dict[str, List]],
    ) -> Dict[Tuple[str, str, str], List[float]]:
        """{(metric, stored period_type, label): [raw values]} for one industry's companies."""
        ind_values: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
        for mc in METRIC_CONFIGS:
            for period_type in ("annual", "quarterly"):
                vals = self._sb._collect_metric_values(company_data, mc, period_type)
                # Quarterly values are keyed by CALENDAR quarter and stored under
                # their own period_type (STORED_PERIOD_TYPE), never 'quarterly'.
                stored_type = STORED_PERIOD_TYPE[period_type]
                for period_label, values in vals.items():
                    ind_values[(mc["name"], stored_type, period_label)].extend(values)
        return ind_values

    async def _compute_sector(
        self, sector: str,
        industries: List[Tuple[str, List[Tuple[str, float]]]],
        al: int, ql: int, dry_run: bool = False,
        tally: Optional[_FetchTally] = None,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        sector_acc: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
        left_out: Dict[str, set] = defaultdict(set)
        withheld: List[str] = []
        written = 0
        for industry, ticker_caps in industries:
            ind_values = await self._industry_value_lists(
                ticker_caps, al, ql,
                counts=tally.counts_for(sector, industry) if tally is not None else None,
            )
            # Over the INCOMPLETE line itself (P4-1): neither written — its rows from the last
            # full fetch stay — nor pooled; its sector is then lossy, so no aggregate either.
            note = tally.withhold(sector, industry) if tally is not None else None
            if note is not None:
                if ind_values:   # the line kept a partial median out: say so (below)
                    withheld.append(note)
                continue
            if not ind_values:
                continue
            self._pool_into_sector(sector_acc, ind_values, industry, left_out)
            written += await asyncio.to_thread(
                self._write_fiscal_group, sector, industry, ind_values, now, dry_run,
            )
            del ind_values  # free this industry's value lists before the next
        _log_withheld_industries("industry_benchmark", sector, withheld)
        self._log_left_out(f"{sector} (sector aggregate)", left_out)
        loss = tally.sector_loss(sector) if tally is not None else None
        if loss is not None:
            if written or dry_run:   # zero rows: the caller's ZERO-rows WARNING says it
                _log_unwritten_aggregate("industry_benchmark", sector, loss)
            return written
        # Sector aggregate (industry='') from the accumulated raw value lists — written
        # LAST, its annual rows (the freshness marker) last of all (`_upsert`).
        written += await asyncio.to_thread(
            self._write_fiscal_group, sector, "", sector_acc, now, dry_run, marker_last=True,
        )
        return written

    async def _compute_industries_only(
        self, targets: List[str], al: int, ql: int, dry_run: bool,
        universe_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Validation path: compute ONLY the named industries' rows (industry=<name>),
        NOT the sector aggregate (one industry isn't the whole sector). Lets you
        cheaply sanity-check a single industry before the full run."""
        lookup: Dict[str, Tuple[str, List[Tuple[str, float]]]] = {}
        for sector, inds in await self._universe_for_run(universe_file):
            for industry, tc in inds:
                lookup[industry] = (sector, tc)
        now = datetime.now(timezone.utc).isoformat()
        tally = _FetchTally("fiscal-industries")
        total = 0
        seen = 0
        for industry in targets:
            if industry not in lookup:
                logger.warning("industry_benchmark: %r not in universe — skipped", industry)
                continue
            seen += 1
            sector, ticker_caps = lookup[industry]
            ind_values = await self._industry_value_lists(
                ticker_caps, al, ql, counts=tally.counts_for(sector, industry),
            )
            note = tally.withhold(sector, industry)   # over the line: keep its rows (P4-1)
            if note is not None:
                _log_withheld_industries("industry_benchmark", sector, [note])
                continue
            total += await asyncio.to_thread(
                self._write_fiscal_group, sector, industry, ind_values, now, dry_run,
            )
        tally.log()
        return {"industries": seen, "rows_upserted": total, "dry_run": dry_run, **tally.summary()}

    # ── Orchestration ────────────────────────────────────────────────
    # ── TTM current-snapshot compute (period_type="ttm") ─────────────────
    async def _fetch_ttm(
        self, ticker: str, sem: asyncio.Semaphore,
        counts: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Optional[float]]:
        """One company's TTM values. A 429 is retried (`call_with_rate_limit_retry`, inside
        the semaphore so a rate-limited burst slows down); a call that still fails leaves
        its metrics None, and the ticker's outcome is counted into `counts` (LOST to the
        INCOMPLETE line when either call failed transiently). A 200 whose body is not a list
        is 'refused', like `_fetch_company_data`'s."""
        async with sem:
            r, k = await asyncio.gather(
                call_with_rate_limit_retry(self._fmp.get_ratios_ttm, ticker),
                call_with_rate_limit_retry(self._fmp.get_key_metrics_ttm, ticker),
                return_exceptions=True,
            )
        failures = [
            classify_fetch_failure(x) if isinstance(x, BaseException) else "refused"
            for x in (r, k)
            if isinstance(x, BaseException) or (x is not None and not isinstance(x, list))
        ]
        r0 = r[0] if isinstance(r, list) and r and isinstance(r[0], dict) else {}
        k0 = k[0] if isinstance(k, list) and k and isinstance(k[0], dict) else {}
        values = _extract_ttm(r0, k0)
        _record_fetch_outcome(counts, failures, any(v is not None for v in values.values()))
        return values

    async def _industry_ttm_values(
        self, ticker_caps: List[Tuple[str, float]], sem: asyncio.Semaphore,
        counts: Optional[Dict[str, int]] = None,
    ) -> Dict[str, List[float]]:
        rows = await asyncio.gather(
            *[self._fetch_ttm(t, sem, counts=counts) for t, _ in ticker_caps]
        )
        vals: Dict[str, List[float]] = defaultdict(list)
        for row in rows:
            for metric, v in row.items():
                if v is not None:
                    vals[metric].append(v)
        return vals

    @staticmethod
    def _ttm_median(metric: str, values: List[float]) -> Tuple[Optional[float], int]:
        """positive-only + cap@ceiling (winsorize, NOT trim) + median — the
        validated rule. cap clamps (min(x, cap)); we never drop the artifacts."""
        _src, _field, positive_only, cap = _TTM_METRICS[metric]
        v = [x for x in values if x is not None]
        if positive_only:
            v = [x for x in v if x > 0]
        if cap is not None:
            v = [min(x, cap) for x in v]
        if len(v) < MIN_SAMPLE_SIZE:
            return None, len(v)
        return round(statistics.median(v), 4), len(v)

    def _ttm_rows(
        self, sector: str, industry: str,
        values_by_metric: Dict[str, List[float]], now: str,
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        for metric, vals in values_by_metric.items():
            med, n = self._ttm_median(metric, vals)
            if med is None:
                continue
            rows.append({
                "sector": sector,
                "industry": industry,
                "metric_name": metric,
                "period_type": TTM_PERIOD_TYPE,
                "period_label": TTM_PERIOD_LABEL,
                "median_value": med,
                "sample_size": n,
                "computed_at": now,
            })
        return rows

    def _ttm_sector_is_fresh(self, sector: str, hours: Optional[int]) -> bool:
        """Skip a sector whose TTM '' aggregate is newer than N hours (filters
        period_type='ttm' so it never confuses TTM freshness with the fiscal rows). That
        aggregate is the sector's last write, one statement (`_upsert(marker_last=True)`)."""
        return self._aggregate_is_fresh(sector, TTM_PERIOD_TYPE, hours)

    @_rate_limited_run("ttm")
    async def recompute_all_ttm(
        self, *, skip_if_fresh_hours: Optional[int] = None,
        sectors: Optional[List[str]] = None,
        industries: Optional[List[str]] = None,
        dry_run: bool = False,
        universe_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute the TTM current-snapshot medians (period_type='ttm') for every
        industry + sector aggregate. Additive — leaves the fiscal rows intact.

        Raises `IndustryBenchmarkRecomputeSkipped` (after an ERROR log) when the full
        sweep's universe is empty or a named `universe_file` is unreadable (before any
        write), or when it attempted sectors and wrote no row (`_fetch_ttm` turns an FMP
        failure into all-None values, so an outage "completes" every sector with 0 rows),
        and `IndustryBenchmarkRecomputeIncomplete` when some sector raised, computed zero
        rows while others wrote, or lost too many companies to TRANSIENT FMP fetch failures
        (`_FetchTally.sector_loss`; its aggregate is not written, nor is an industry over the
        line, which keeps its previous rows) — the weekly claim stays
        UNSETTLED and the same-day retry recomputes only the sectors without a fresh
        aggregate. A run where every sector was fresh or wrote rows, the industries-only path
        and `dry_run` still return. The summary carries the FMP fetch-failure counts
        (`_FetchTally`: by kind, the transient subset the line counts, the withheld
        industries), the lossy sectors and the run's shared-429-window waits
        (`rate_limit_run`). `universe_file`: an explicitly named universe (the operator
        script only; see `_read_universe_file`)."""
        start = datetime.now(timezone.utc)
        now = start.isoformat()
        sem = asyncio.Semaphore(_TTM_CONCURRENCY)
        universe = await self._universe_for_run(universe_file)

        # Validation path: a few named industries only (industry rows, no sector agg).
        if industries:
            tally = _FetchTally("ttm-industries")
            by_ind = {ind: (sector, tc) for sector, inds in universe for ind, tc in inds}
            total = done = 0
            for ind in industries:
                if ind not in by_ind:
                    logger.warning("ttm: industry not in universe: %r", ind)
                    continue
                sector, tc = by_ind[ind]
                vals = await self._industry_ttm_values(tc, sem, counts=tally.counts_for(sector, ind))
                note = tally.withhold(sector, ind)   # over the line: keep its rows (P4-1)
                if note is not None:
                    _log_withheld_industries("ttm", sector, [note])
                    continue
                total += await asyncio.to_thread(
                    self._write_ttm_group, sector, ind, vals, now, dry_run,
                )
                done += 1
            tally.log()
            summary = {
                "mode": "ttm-industries", "industries_done": done,
                "rows_upserted": total, "dry_run": dry_run, **tally.summary(),
                **_rate_limit_summary(),
                "elapsed_seconds": round((datetime.now(timezone.utc) - start).total_seconds(), 1),
            }
            logger.info("ttm benchmark (industries-only) complete: %s", summary)
            return summary

        # Checked BEFORE the `sectors` filter: an operator's filter matching nothing is
        # their input, not a failed universe.
        if not universe and not dry_run:
            raise _empty_universe_skip("TTM (period_type='ttm')")
        if sectors:
            universe = [(s, inds) for s, inds in universe if s in sectors]
        tally = _FetchTally("ttm")
        failed_sectors: List[str] = []
        empty_sectors: List[str] = []
        lossy_sectors: List[str] = []
        total_rows = done = skipped_fresh = 0
        for sector, inds in universe:
            if not dry_run and (await asyncio.to_thread(self._ttm_sector_is_fresh, sector, skip_if_fresh_hours)):
                skipped_fresh += 1
                logger.info("ttm: %s fresh — skipped", sector)
                continue
            try:
                logger.info("ttm: computing %s (%d industries)...", sector, len(inds))
                sector_acc: Dict[str, List[float]] = defaultdict(list)
                left_out: Dict[str, set] = defaultdict(set)
                withheld: List[str] = []
                n = 0
                for ind, tc in inds:
                    vals = await self._industry_ttm_values(tc, sem, counts=tally.counts_for(sector, ind))
                    # Over the INCOMPLETE line itself (P4-1): neither written nor pooled.
                    note = tally.withhold(sector, ind)
                    if note is not None:
                        if vals:   # the line kept a partial median out: say so (below)
                            withheld.append(note)
                        continue
                    if not vals:
                        continue
                    self._pool_into_sector(sector_acc, vals, ind, left_out)
                    n += await asyncio.to_thread(
                        self._write_ttm_group, sector, ind, vals, now, dry_run,
                    )
                _log_withheld_industries("ttm", sector, withheld)
                self._log_left_out(f"TTM {sector} (aggregate)", left_out)
                loss = tally.sector_loss(sector)
                if loss is not None:
                    if n or dry_run:   # zero rows: the ZERO-rows WARNING below says it
                        _log_unwritten_aggregate("ttm", sector, loss)
                else:
                    n += await asyncio.to_thread(
                        self._write_ttm_group, sector, "", sector_acc, now, dry_run,
                        marker_last=True,
                    )
                total_rows += n
                done += 1
                if n == 0 and not dry_run:
                    empty_sectors.append(sector)
                    logger.warning(
                        "ttm: %s computed ZERO rows — no company returned a usable TTM value "
                        "(FMP unreachable or refusing?); the sector is not marked fresh and "
                        "the run will not settle, so a same-day retry recomputes it", sector,
                    )
                elif loss is not None:
                    lossy_sectors.append(sector)
                else:
                    logger.info("ttm: %s done — %d rows", sector, n)
            except Exception as e:
                failed_sectors.append(sector)
                logger.error("ttm: %s failed: %s: %s", sector, type(e).__name__, e, exc_info=True)
        failed = len(failed_sectors)
        summary = {
            "mode": "ttm", "sectors_done": done, "sectors_failed": failed,
            "sectors_empty": len(empty_sectors),
            "sectors_lossy": len(lossy_sectors), "lossy_sectors": list(lossy_sectors),
            "sectors_skipped_fresh": skipped_fresh,
            "rows_upserted": total_rows, "dry_run": dry_run, **tally.summary(),
            **_rate_limit_summary(),
            "elapsed_seconds": round((datetime.now(timezone.utc) - start).total_seconds(), 1),
        }
        tally.log()
        # Attempted sectors (not fresh-skipped) that wrote nothing must not settle the
        # weekly claim. A dry run writes nothing by design. Checked FIRST: a sweep that
        # wrote nothing at all is a Skipped, not an Incomplete.
        attempted = done + failed
        if attempted and total_rows == 0 and not dry_run:
            raise _nothing_written_skip(
                "TTM (period_type='ttm')", attempted=attempted, failed=failed,
            )
        # A PARTIAL run — a sector raised, computed zero rows while others wrote, or lost
        # too many companies to fetch failures — must not settle it either (see
        # `IndustryBenchmarkRecomputeIncomplete`).
        if (failed_sectors or empty_sectors or lossy_sectors) and not dry_run:
            raise _incomplete_run(
                "TTM (period_type='ttm')", failed_sectors, summary, empty_sectors,
                lossy_sectors,
            )
        logger.info("ttm benchmark complete: %s", summary)
        return summary

    @_rate_limited_run("fiscal")
    async def recompute_all(
        self, *, skip_if_fresh_hours: Optional[int] = None,
        sectors: Optional[List[str]] = None,
        industries: Optional[List[str]] = None,
        dry_run: bool = False,
        universe_file: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute the fiscal medians (annual + calendar-quarter) for every industry and
        sector aggregate. Raises `IndustryBenchmarkRecomputeSkipped` for an empty universe,
        an unreadable named `universe_file` or a sweep that wrote nothing, and
        `IndustryBenchmarkRecomputeIncomplete` when some sector raised, computed zero rows
        while others wrote, or lost too many companies to TRANSIENT FMP fetch failures
        (`_FetchTally.sector_loss`; `_compute_sector` then writes no aggregate, nor any
        industry over the line, which keeps its previous rows) — either way
        the quarterly claim stays UNSETTLED, and the same-day retry skips the sectors whose
        freshness marker this run wrote. The summary carries the FMP fetch-failure counts
        (`_FetchTally`: by kind, the transient subset the line counts, the withheld
        industries), the lossy sectors and the run's shared-429-window waits
        (`rate_limit_run`). `universe_file`: an explicitly named universe (the operator
        script only; see `_read_universe_file`)."""
        start = datetime.now(timezone.utc)
        al, ql = FMP_ANNUAL_LIMIT_BACKFILL, FMP_QUARTERLY_LIMIT_BACKFILL
        # Each run re-probes: migration 184 may have been applied since the last one.
        self._calendar_quarter_blocked = False

        # Validation path: a few named industries only (industry rows, no sector
        # aggregate). Pair with dry_run to write nothing and just eyeball the medians.
        if industries:
            summary = await self._compute_industries_only(
                industries, al, ql, dry_run, universe_file=universe_file,
            )
            summary["calendar_quarter_blocked"] = self._calendar_quarter_blocked
            summary.update(_rate_limit_summary())
            summary["elapsed_seconds"] = round((datetime.now(timezone.utc) - start).total_seconds(), 1)
            logger.info("industry_benchmark (industries-only) complete: %s", summary)
            return summary

        universe = await self._universe_for_run(universe_file)
        # Before any write, and before the `sectors` filter (see recompute_all_ttm). A
        # scheduled run must not settle its quarterly claim having iterated nothing.
        if not universe and not dry_run:
            raise _empty_universe_skip("fiscal (annual + calendar-quarter)")
        if sectors:
            universe = [(s, inds) for s, inds in universe if s in sectors]
        tally = _FetchTally("fiscal")
        failed_sectors: List[str] = []
        empty_sectors: List[str] = []
        lossy_sectors: List[str] = []
        total_rows = done = skipped_fresh = 0
        for sector, inds in universe:
            if not dry_run and (await asyncio.to_thread(self._sector_is_fresh, sector, skip_if_fresh_hours)):
                skipped_fresh += 1
                logger.info("industry_benchmark: %s fresh — skipped", sector)
                continue
            try:
                logger.info(
                    "industry_benchmark: computing %s (%d industries)...",
                    sector, len(inds),
                )
                n = await self._compute_sector(sector, inds, al, ql, dry_run, tally=tally)
                total_rows += n
                done += 1
                if n == 0 and not dry_run:
                    empty_sectors.append(sector)
                    logger.warning(
                        "industry_benchmark: %s computed ZERO rows — no company returned "
                        "usable statements (FMP unreachable or refusing?); the sector is not "
                        "marked fresh and the run will not settle, so a same-day retry "
                        "recomputes it", sector,
                    )
                elif tally.sector_loss(sector) is not None:
                    # `_compute_sector` read the same line, wrote no aggregate and logged
                    # the counts at WARNING.
                    lossy_sectors.append(sector)
                else:
                    logger.info("industry_benchmark: %s done — %d rows", sector, n)
            except Exception as e:
                failed_sectors.append(sector)
                logger.error(
                    "industry_benchmark: %s failed: %s: %s", sector, type(e).__name__, e,
                    exc_info=True,
                )
        failed = len(failed_sectors)
        elapsed = (datetime.now(timezone.utc) - start).total_seconds()
        summary = {
            "sectors_done": done,
            "sectors_failed": failed,
            "sectors_empty": len(empty_sectors),
            "sectors_lossy": len(lossy_sectors),
            "lossy_sectors": list(lossy_sectors),
            "sectors_skipped_fresh": skipped_fresh,
            "rows_upserted": total_rows,
            "dry_run": dry_run,
            # True = migration 184 is missing, so NO calendar-quarter row was written.
            "calendar_quarter_blocked": self._calendar_quarter_blocked,
            **tally.summary(),
            **_rate_limit_summary(),
            "elapsed_seconds": round(elapsed, 1),
        }
        tally.log()
        # Attempted sectors (not fresh-skipped) that wrote nothing must not settle the
        # quarterly claim (`_fetch_company_data` turns every FMP failure into an empty
        # list, so an outage "completes" each sector with 0 rows). A dry run writes
        # nothing by design. Checked FIRST: nothing written at all is a Skipped.
        attempted = done + failed
        if attempted and total_rows == 0 and not dry_run:
            raise _nothing_written_skip(
                "fiscal (annual + calendar-quarter)", attempted=attempted, failed=failed,
            )
        # A PARTIAL run must not settle it either: a sector that raised, that computed zero
        # rows while others wrote (its whole fetch failed), or that lost too many companies
        # to fetch failures would keep last quarter's medians (or a skewed one) until the
        # next quarter.
        if (failed_sectors or empty_sectors or lossy_sectors) and not dry_run:
            raise _incomplete_run(
                "fiscal (annual + calendar-quarter)", failed_sectors, summary, empty_sectors,
                lossy_sectors,
            )
        logger.info("industry_benchmark complete: %s", summary)
        return summary


_industry_benchmark_service: Optional[IndustryBenchmarkService] = None


def get_industry_benchmark_service() -> IndustryBenchmarkService:
    global _industry_benchmark_service
    if _industry_benchmark_service is None:
        _industry_benchmark_service = IndustryBenchmarkService()
    return _industry_benchmark_service
