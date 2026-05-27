"""Industry Moat Benchmark Service — pre-computed per-pillar peer
averages so the iOS Moat radar's gray "Peer Avg" pentagon shows a
real industry signal instead of a flat 5.0 anchor.

Compute model (offline, quarterly):
  for each industry in data/industry_universe.json:
    for each constituent ticker (capped at top 200 by mkt cap):
      fetch profile + income + balance + ratios + key_metrics in parallel
      run the existing `score_moat_dimensions` deterministic scorer
    for each of the 5 pillars:
      drop None scores, winsorize at p10/p90, mean → peer_average_score
      skip if sample_size < MIN_SAMPLE_SIZE
    upsert one row per pillar to industry_moat_benchmarks

Lookup (online, per request):
  IndustryMoatBenchmarkLookup.get_pillar_benchmarks(industry)
  → {pillar_name: peer_average_score}, with 1h in-memory cache.
  Returns {} when the industry has no rows yet so callers can fall
  back to the existing 5.0 baseline.

Two pillars (Switching Costs, Intangible Assets) depend on transcript
or USPTO data that we deliberately don't fetch in this batch — those
pillars will commonly have small samples and fall back to the 5.0
baseline. That's expected; this service is best-effort for the
financial-statement-driven pillars (Network Effects, Brand Power,
Cost Advantage) where signal density is high.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.database import get_supabase
from app.integrations.fmp import get_fmp_client
from app.services.moat_scoring_service import (
    PILLAR_ORDER,
    PillarResult,
    score_moat_dimensions,
)

logger = logging.getLogger(__name__)


# ── Constants ───────────────────────────────────────────────────────

MIN_SAMPLE_SIZE = 5
TOP_TICKERS_PER_INDUSTRY = 200
PER_TICKER_FMP_CONCURRENCY = 5
PER_INDUSTRY_CONCURRENCY = 3
MODEL_VERSION = "moat_v1.2026-05"
TABLE_NAME = "industry_moat_benchmarks"
_UNIVERSE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "industry_universe.json"
)


# ── Helpers ─────────────────────────────────────────────────────────


def _winsorize_p10_p90(values: List[float]) -> List[float]:
    """Cap values at the 10th and 90th percentiles. Returns the values
    in their original order (no sort), so caller can correlate with
    per-ticker metadata if needed.
    """
    if len(values) < 10:
        # Below 10 samples a percentile-based cap is more noise than
        # signal; return the values untouched and let the small-sample
        # threshold downstream drop them.
        return list(values)
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    p10 = sorted_vals[max(0, int(n * 0.10))]
    p90 = sorted_vals[min(n - 1, int(n * 0.90))]
    return [max(p10, min(p90, v)) for v in values]


def _percentile(sorted_values: List[float], pct: float) -> Optional[float]:
    """Linear-interpolation percentile on a pre-sorted list. Returns
    None when the list is empty.
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    k = (len(sorted_values) - 1) * pct
    lo = int(k)
    hi = min(lo + 1, len(sorted_values) - 1)
    weight = k - lo
    return sorted_values[lo] * (1 - weight) + sorted_values[hi] * weight


def _load_universe_industries() -> List[Tuple[str, List[Tuple[str, float]]]]:
    """Return [(industry, [(ticker, mkt_cap), ...]), ...]. Tickers are
    sorted by market-cap descending. Empty industries are skipped.
    """
    try:
        data = json.loads(_UNIVERSE_PATH.read_text())
    except Exception as exc:
        logger.error(
            "industry_moat_benchmark: failed to read %s: %s",
            _UNIVERSE_PATH, exc,
        )
        return []
    out: List[Tuple[str, List[Tuple[str, float]]]] = []
    for entry in data.get("industries", []) or []:
        ind = entry.get("industry")
        mcaps = entry.get("market_caps") or {}
        if not ind or not mcaps:
            continue
        sorted_tkrs = sorted(
            ((t, float(c or 0.0)) for t, c in mcaps.items()),
            key=lambda x: x[1],
            reverse=True,
        )
        out.append((ind, sorted_tkrs))
    return out


# ── Service ─────────────────────────────────────────────────────────


class IndustryMoatBenchmarkService:
    def __init__(self) -> None:
        self.supabase = get_supabase()
        self.fmp = get_fmp_client()

    # ── Per-ticker pillar scoring ────────────────────────────────────

    async def _score_one_ticker(
        self,
        ticker: str,
        sem: asyncio.Semaphore,
    ) -> Optional[Dict[str, Optional[float]]]:
        """Fetch the focal data for one peer + run the deterministic
        scorer. Returns {pillar_name: score | None}, or None if the
        FMP profile lookup failed (ticker doesn't exist / FMP error).
        Skips transcript + ip_intel inputs by design — those would
        balloon the per-batch FMP cost without enough quality lift
        across most industries.
        """
        async with sem:
            try:
                profile_task = self.fmp.get_company_profile(ticker)
                income_task = self.fmp.get_income_statement(ticker, "annual", 2)
                balance_task = self.fmp.get_balance_sheet(ticker, "annual", 2)
                ratios_task = self.fmp.get_financial_ratios(ticker, "annual", 1)
                km_task = self.fmp.get_key_metrics(ticker, "annual", 1)
                profile, income, balance, ratios, km = await asyncio.gather(
                    profile_task, income_task, balance_task,
                    ratios_task, km_task,
                    return_exceptions=True,
                )
            except Exception as exc:
                logger.debug(
                    "industry_moat_benchmark: fan-out failed for %s: %s",
                    ticker, exc,
                )
                return None

        if isinstance(profile, Exception) or not profile:
            return None

        def _safe(v: Any) -> List[Dict[str, Any]]:
            return v if isinstance(v, list) else []

        try:
            pillars: Dict[str, PillarResult] = await asyncio.to_thread(
                score_moat_dimensions,
                sector=profile.get("sector"),
                industry=profile.get("industry"),
                profile=profile,
                income=_safe(income),
                balance=_safe(balance),
                ratios=_safe(ratios),
            )
        except Exception as exc:
            logger.debug(
                "industry_moat_benchmark: scorer failed for %s: %s",
                ticker, exc,
            )
            return None

        # The /key-metrics ROE fallback the per-report path applies
        # isn't needed here — peer averaging only requires the pillar
        # scores, and ROE-derived pillars resolve from /ratios already.
        # km is fetched for symmetry with future scorer extensions.
        _ = km

        return {p: pillars.get(p).score if pillars.get(p) else None for p in PILLAR_ORDER}

    # ── Industry-level aggregate ─────────────────────────────────────

    async def compute_for_industry(
        self, industry: str, *, run_id: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Compute peer averages for one industry. Upserts one row per
        pillar that meets the sample-size threshold. Returns a summary
        dict {pillar_name: {avg, sample_size, p25, p75}} for the rows
        that were actually written.
        """
        run_id = run_id or str(uuid.uuid4())
        universe = _load_universe_industries()
        tickers: List[str] = []
        for ind, sorted_tkrs in universe:
            if ind == industry:
                tickers = [t for t, _ in sorted_tkrs[:TOP_TICKERS_PER_INDUSTRY]]
                break
        if not tickers:
            logger.info(
                "industry_moat_benchmark: no tickers for industry %r", industry,
            )
            return {}

        sem = asyncio.Semaphore(PER_TICKER_FMP_CONCURRENCY)
        per_ticker_scores = await asyncio.gather(
            *[self._score_one_ticker(t, sem) for t in tickers],
            return_exceptions=True,
        )

        # Collect per-pillar score lists.
        pillar_scores: Dict[str, List[float]] = {p: [] for p in PILLAR_ORDER}
        for row in per_ticker_scores:
            if not isinstance(row, dict):
                continue
            for p in PILLAR_ORDER:
                v = row.get(p)
                if v is None:
                    continue
                try:
                    pillar_scores[p].append(float(v))
                except (TypeError, ValueError):
                    continue

        # Aggregate + upsert. The scorer emits 0.0-10.0; we still
        # winsorize at p10/p90 so a handful of outlier filings can't
        # drag the mean. Skip pillars below the sample threshold so
        # callers fall back to the 5.0 baseline cleanly.
        written: Dict[str, Dict[str, Any]] = {}
        for pillar, vals in pillar_scores.items():
            n = len(vals)
            if n < MIN_SAMPLE_SIZE:
                logger.info(
                    "industry_moat_benchmark: skip %s / %s (n=%d < %d)",
                    industry, pillar, n, MIN_SAMPLE_SIZE,
                )
                continue
            wins = _winsorize_p10_p90(vals)
            avg = round(statistics.fmean(wins), 1)
            sorted_wins = sorted(wins)
            p25 = round(_percentile(sorted_wins, 0.25) or 0.0, 1)
            p75 = round(_percentile(sorted_wins, 0.75) or 0.0, 1)
            row = {
                "industry": industry,
                "pillar_name": pillar,
                "peer_average_score": avg,
                "sample_size": n,
                "score_p25": p25,
                "score_p75": p75,
                "computed_at": datetime.now(timezone.utc).isoformat(),
                "model_version": MODEL_VERSION,
            }
            try:
                await asyncio.to_thread(
                    lambda r=row: self.supabase.table(TABLE_NAME)
                    .upsert(r, on_conflict="industry,pillar_name")
                    .execute(),
                )
                written[pillar] = {
                    "avg": avg, "sample_size": n, "p25": p25, "p75": p75,
                }
            except Exception as exc:
                logger.error(
                    "industry_moat_benchmark: upsert failed for %s / %s: %s",
                    industry, pillar, exc,
                )
        return written

    async def recompute_all(
        self, *, run_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Quarterly batch entry. Iterates every industry from
        `industry_universe.json` and upserts per-pillar peer averages.
        Concurrency-bounded so we don't burst FMP quota.
        """
        run_id = run_id or str(uuid.uuid4())
        started = time.time()
        universe = _load_universe_industries()
        industries = [ind for ind, _ in universe]
        logger.info(
            "industry_moat_benchmark: recompute_all starting — "
            "run_id=%s, industries=%d", run_id, len(industries),
        )

        sem = asyncio.Semaphore(PER_INDUSTRY_CONCURRENCY)
        pillars_written = 0
        skipped_low_sample = 0

        async def _one(ind: str) -> Tuple[str, int, int]:
            async with sem:
                try:
                    written = await self.compute_for_industry(ind, run_id=run_id)
                except Exception as exc:
                    logger.error(
                        "industry_moat_benchmark: compute_for_industry "
                        "failed for %r: %s", ind, exc,
                    )
                    return ind, 0, len(PILLAR_ORDER)
                wp = len(written)
                return ind, wp, len(PILLAR_ORDER) - wp

        results = await asyncio.gather(
            *[_one(ind) for ind in industries], return_exceptions=True,
        )
        for r in results:
            if isinstance(r, tuple):
                pillars_written += r[1]
                skipped_low_sample += r[2]

        summary = {
            "run_id": run_id,
            "industries": len(industries),
            "pillars_written": pillars_written,
            "skipped_low_sample": skipped_low_sample,
            "elapsed_seconds": round(time.time() - started, 1),
        }
        logger.info("industry_moat_benchmark recompute_all summary: %s", summary)
        return summary


# ── Singleton service ───────────────────────────────────────────────

_service_singleton: Optional[IndustryMoatBenchmarkService] = None


def get_industry_moat_benchmark_service() -> IndustryMoatBenchmarkService:
    global _service_singleton
    if _service_singleton is None:
        _service_singleton = IndustryMoatBenchmarkService()
    return _service_singleton


# ── Lookup with in-memory cache ─────────────────────────────────────

_LOOKUP_CACHE_TTL_SECONDS = 3600  # 1h in-process cache
_lookup_cache: Dict[str, Tuple[float, Dict[str, float]]] = {}


class IndustryMoatBenchmarkLookup:
    def __init__(self) -> None:
        self.supabase = get_supabase()

    def get_pillar_benchmarks(self, industry: str) -> Dict[str, float]:
        """Return {pillar_name: peer_average_score} for `industry`.
        Empty dict means no benchmark rows yet — caller should fall
        back to the existing 5.0 baseline. Cached in-process for 1h.
        """
        if not industry:
            return {}
        cached = _lookup_cache.get(industry)
        if cached and time.time() - cached[0] < _LOOKUP_CACHE_TTL_SECONDS:
            return cached[1]
        try:
            resp = (
                self.supabase.table(TABLE_NAME)
                .select("pillar_name,peer_average_score")
                .eq("industry", industry)
                .execute()
            )
            rows = resp.data or []
        except Exception as exc:
            logger.warning(
                "industry_moat_benchmark lookup failed for %r: %s",
                industry, exc,
            )
            return {}
        out: Dict[str, float] = {}
        for r in rows:
            name = r.get("pillar_name")
            score = r.get("peer_average_score")
            if name and score is not None:
                try:
                    out[name] = float(score)
                except (TypeError, ValueError):
                    continue
        _lookup_cache[industry] = (time.time(), out)
        return out


_lookup_singleton: Optional[IndustryMoatBenchmarkLookup] = None


def get_industry_moat_benchmark_lookup() -> IndustryMoatBenchmarkLookup:
    global _lookup_singleton
    if _lookup_singleton is None:
        _lookup_singleton = IndustryMoatBenchmarkLookup()
    return _lookup_singleton
