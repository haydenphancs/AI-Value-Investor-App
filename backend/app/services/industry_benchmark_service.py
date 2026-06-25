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
import math
import statistics
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
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


class IndustryBenchmarkService:
    def __init__(self) -> None:
        self.supabase = get_supabase()
        # Reuse the sector service's FMP fetch + per-group aggregation + throttle.
        self._sb = SectorBenchmarkService()
        self._fmp = get_fmp_client()  # for the TTM /ratios-ttm + /key-metrics-ttm path

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
            # TOTAL coercion: a single non-numeric market cap ('N/A', '', a
            # CSV-formatted '3,000,000', None) used to raise ValueError INSIDE the
            # sorted() generator — and since _load_universe is called OUTSIDE the
            # per-sector try/except, one bad value aborted the ENTIRE recompute (every
            # sector, zero rows). Drop + warn the bad ticker instead so the job degrades.
            pairs: List[Tuple[str, float]] = []
            for t, c in mcaps.items():
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
                # FISCAL freshness must inspect ONLY the fiscal aggregate (the
                # 'annual' '' row that _compute_sector always writes). Without this
                # filter the probe saw the newest computed_at across ALL period_types
                # — including the weekly 'ttm' rows — so a fresh Sunday TTM write made
                # this skip the quarterly fiscal recompute for EVERY sector, freezing
                # the chart history + growth series. Mirrors _ttm_sector_is_fresh.
                .eq("period_type", "annual")
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
        """Upsert in batches. RAISES on any batch failure (after logging) so the
        caller's per-sector try/except aborts BEFORE writing that sector's '' aggregate
        row. Otherwise a sector whose industry rows failed mid-write would still get a
        fresh '' timestamp and be wrongly SKIPPED on the next resume (the freshness
        probe only checks the '' row) — leaving its industry rows missing for a whole
        cycle. On abort the per-sector guard logs + continues to the next sector, and
        the failed sector (never marked fresh) is retried in full next run."""
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
                logger.error(
                    "industry_benchmark upsert batch failed (%d rows written before "
                    "failure; aborting sector for retry): %s: %s",
                    n, type(e).__name__, e,
                )
                raise
        return n

    def _emit(self, rows: List[Dict[str, Any]], label: str, dry_run: bool) -> int:
        """Upsert, or (dry_run) log a sample of the computed medians and write nothing."""
        if dry_run:
            self._log_sample(label, rows)
            return 0
        return self._upsert(rows)

    @staticmethod
    def _log_sample(label: str, rows: List[Dict[str, Any]]) -> None:
        """Log a few headline medians at each metric's MOST-SAMPLED annual year (so a
        thin partial current year doesn't mislead) — a sanity check before a full run."""
        best: Dict[str, Any] = {}
        for r in rows:
            if r["period_type"] != "annual":
                continue
            cur = best.get(r["metric_name"])
            if cur is None or r["sample_size"] > cur["sample_size"] or (
                r["sample_size"] == cur["sample_size"]
                and r["period_label"] > cur["period_label"]
            ):
                best[r["metric_name"]] = r
        logger.info(
            "DRY-RUN %s — %d rows. Median at each metric's most-sampled annual year:",
            label, len(rows),
        )
        for m in ("gross_margin", "operating_margin", "net_margin", "fcf_margin",
                  "roe", "roa", "pe_ratio", "pb_ratio", "ps_ratio", "interest_coverage"):
            r = best.get(m)
            if r:
                logger.info(
                    "    %-18s %s: median=%s (n=%d)",
                    m, r["period_label"], r["median_value"], r["sample_size"],
                )

    # ── Per-sector compute (stream industries, accumulate into the sector) ──
    async def _industry_value_lists(
        self, ticker_caps: List[Tuple[str, float]], al: int, ql: int,
    ) -> Dict[Tuple[str, str, str], List[float]]:
        """Fetch one industry's companies and return {(metric,period_type,period):[raw values]}."""
        company_data = await self._fetch_batched([t for t, _ in ticker_caps], al, ql)
        ind_values: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
        if not company_data:
            return ind_values
        for mc in METRIC_CONFIGS:
            for period_type in ("annual", "quarterly"):
                vals = self._sb._collect_metric_values(company_data, mc, period_type)
                for period_label, values in vals.items():
                    ind_values[(mc["name"], period_type, period_label)].extend(values)
        return ind_values

    async def _compute_sector(
        self, sector: str,
        industries: List[Tuple[str, List[Tuple[str, float]]]],
        al: int, ql: int, dry_run: bool = False,
    ) -> int:
        now = datetime.now(timezone.utc).isoformat()
        sector_acc: Dict[Tuple[str, str, str], List[float]] = defaultdict(list)
        written = 0
        for industry, ticker_caps in industries:
            ind_values = await self._industry_value_lists(ticker_caps, al, ql)
            if not ind_values:
                continue
            for key, values in ind_values.items():
                sector_acc[key].extend(values)
            written += self._emit(
                self._rows_from_values(sector, industry, ind_values, now),
                f"{sector} / {industry}", dry_run,
            )
            del ind_values  # free this industry's value lists before the next
        # Sector aggregate (industry='') from the accumulated raw value lists.
        written += self._emit(
            self._rows_from_values(sector, "", sector_acc, now),
            f"{sector} (sector aggregate)", dry_run,
        )
        return written

    async def _compute_industries_only(
        self, targets: List[str], al: int, ql: int, dry_run: bool,
    ) -> Dict[str, Any]:
        """Validation path: compute ONLY the named industries' rows (industry=<name>),
        NOT the sector aggregate (one industry isn't the whole sector). Lets you
        cheaply sanity-check a single industry before the full run."""
        lookup: Dict[str, Tuple[str, List[Tuple[str, float]]]] = {}
        for sector, inds in self._load_universe():
            for industry, tc in inds:
                lookup[industry] = (sector, tc)
        now = datetime.now(timezone.utc).isoformat()
        total = 0
        seen = 0
        for industry in targets:
            if industry not in lookup:
                logger.warning("industry_benchmark: %r not in universe — skipped", industry)
                continue
            seen += 1
            sector, ticker_caps = lookup[industry]
            ind_values = await self._industry_value_lists(ticker_caps, al, ql)
            total += self._emit(
                self._rows_from_values(sector, industry, ind_values, now),
                f"{sector} / {industry}", dry_run,
            )
        return {"industries": seen, "rows_upserted": total, "dry_run": dry_run}

    # ── Orchestration ────────────────────────────────────────────────
    # ── TTM current-snapshot compute (period_type="ttm") ─────────────────
    async def _fetch_ttm(self, ticker: str, sem: asyncio.Semaphore) -> Dict[str, Optional[float]]:
        async with sem:
            try:
                r, k = await asyncio.gather(
                    self._fmp.get_ratios_ttm(ticker),
                    self._fmp.get_key_metrics_ttm(ticker),
                    return_exceptions=True,
                )
            except Exception:
                return {}
        r0 = (r[0] if isinstance(r, list) and r else {}) or {}
        k0 = (k[0] if isinstance(k, list) and k else {}) or {}
        return _extract_ttm(r0, k0)

    async def _industry_ttm_values(
        self, ticker_caps: List[Tuple[str, float]], sem: asyncio.Semaphore,
    ) -> Dict[str, List[float]]:
        rows = await asyncio.gather(*[self._fetch_ttm(t, sem) for t, _ in ticker_caps])
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
        period_type='ttm' so it never confuses TTM freshness with the fiscal rows)."""
        if not hours:
            return False
        try:
            resp = (
                self.supabase.table("sector_benchmarks")
                .select("computed_at")
                .eq("sector", sector).eq("industry", "")
                .eq("period_type", TTM_PERIOD_TYPE)
                .order("computed_at", desc=True).limit(1).execute()
            )
        except Exception:
            return False
        if not resp.data:
            return False
        try:
            last = datetime.fromisoformat(resp.data[0]["computed_at"].replace("Z", "+00:00"))
        except Exception:
            return False
        return (datetime.now(timezone.utc) - last) < timedelta(hours=hours)

    async def recompute_all_ttm(
        self, *, skip_if_fresh_hours: Optional[int] = None,
        sectors: Optional[List[str]] = None,
        industries: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """Compute the TTM current-snapshot medians (period_type='ttm') for every
        industry + sector aggregate. Additive — leaves the fiscal rows intact."""
        start = datetime.now(timezone.utc)
        now = start.isoformat()
        sem = asyncio.Semaphore(_TTM_CONCURRENCY)
        universe = self._load_universe()

        # Validation path: a few named industries only (industry rows, no sector agg).
        if industries:
            by_ind = {ind: (sector, tc) for sector, inds in universe for ind, tc in inds}
            total = done = 0
            for ind in industries:
                if ind not in by_ind:
                    logger.warning("ttm: industry not in universe: %r", ind)
                    continue
                sector, tc = by_ind[ind]
                vals = await self._industry_ttm_values(tc, sem)
                total += self._emit(self._ttm_rows(sector, ind, vals, now), f"TTM {sector}/{ind}", dry_run)
                done += 1
            summary = {
                "mode": "ttm-industries", "industries_done": done,
                "rows_upserted": total, "dry_run": dry_run,
                "elapsed_seconds": round((datetime.now(timezone.utc) - start).total_seconds(), 1),
            }
            logger.info("ttm benchmark (industries-only) complete: %s", summary)
            return summary

        if sectors:
            universe = [(s, inds) for s, inds in universe if s in sectors]
        total_rows = done = skipped_fresh = 0
        for sector, inds in universe:
            if not dry_run and self._ttm_sector_is_fresh(sector, skip_if_fresh_hours):
                skipped_fresh += 1
                logger.info("ttm: %s fresh — skipped", sector)
                continue
            try:
                logger.info("ttm: computing %s (%d industries)...", sector, len(inds))
                sector_acc: Dict[str, List[float]] = defaultdict(list)
                n = 0
                for ind, tc in inds:
                    vals = await self._industry_ttm_values(tc, sem)
                    if not vals:
                        continue
                    for metric, vlist in vals.items():
                        sector_acc[metric].extend(vlist)
                    n += self._emit(self._ttm_rows(sector, ind, vals, now), f"TTM {sector}/{ind}", dry_run)
                n += self._emit(self._ttm_rows(sector, "", sector_acc, now), f"TTM {sector} (aggregate)", dry_run)
                total_rows += n
                done += 1
                logger.info("ttm: %s done — %d rows", sector, n)
            except Exception as e:
                logger.error("ttm: %s failed: %s", sector, e, exc_info=True)
        summary = {
            "mode": "ttm", "sectors_done": done, "sectors_skipped_fresh": skipped_fresh,
            "rows_upserted": total_rows, "dry_run": dry_run,
            "elapsed_seconds": round((datetime.now(timezone.utc) - start).total_seconds(), 1),
        }
        logger.info("ttm benchmark complete: %s", summary)
        return summary

    async def recompute_all(
        self, *, skip_if_fresh_hours: Optional[int] = None,
        sectors: Optional[List[str]] = None,
        industries: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        start = datetime.now(timezone.utc)
        al, ql = FMP_ANNUAL_LIMIT_BACKFILL, FMP_QUARTERLY_LIMIT_BACKFILL

        # Validation path: a few named industries only (industry rows, no sector
        # aggregate). Pair with dry_run to write nothing and just eyeball the medians.
        if industries:
            summary = await self._compute_industries_only(industries, al, ql, dry_run)
            summary["elapsed_seconds"] = round((datetime.now(timezone.utc) - start).total_seconds(), 1)
            logger.info("industry_benchmark (industries-only) complete: %s", summary)
            return summary

        universe = self._load_universe()
        if sectors:
            universe = [(s, inds) for s, inds in universe if s in sectors]
        total_rows = done = skipped_fresh = 0
        for sector, inds in universe:
            if not dry_run and self._sector_is_fresh(sector, skip_if_fresh_hours):
                skipped_fresh += 1
                logger.info("industry_benchmark: %s fresh — skipped", sector)
                continue
            try:
                logger.info(
                    "industry_benchmark: computing %s (%d industries)...",
                    sector, len(inds),
                )
                n = await self._compute_sector(sector, inds, al, ql, dry_run)
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
            "dry_run": dry_run,
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
