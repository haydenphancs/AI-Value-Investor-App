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

All five Pat Dorsey pillars are populated:
  * Brand Power, Cost Advantage, Intangible Assets — from FMP
    profile + income + balance + ratios (always fetched).
  * Switching Costs — from the earnings-call transcript via
    `transcript_signals_service.extract_signals` (regex extraction
    of NRR + user counts, no LLM cost). Transcript fetch adds two
    FMP calls per peer; tickers without an available transcript
    simply contribute no signal to this pillar.
  * Network Effects — from the per-industry IndustryDossier
    (industry_hhi + lifecycle_phase). The dossier is fetched ONCE
    per industry (not per peer) and shared across the batch.
One USPTO data tier is deliberately skipped: per-peer patent
counts would balloon ip_intel cache misses and the IP component
contributes only a fractional sub-score on Intangible Assets.
"""

from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone
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


class IndustryMoatBenchmarkRecomputeSkipped(RuntimeError):
    """`recompute_all` wrote NOTHING. RAISED, never returned as a summary. Three reasons:

      * "empty universe" — the industry universe loaded as `[]` (checked before any write);
      * "every industry failed" — every attempted industry FAILED (see `_zero_write_failure`);
      * "nothing written" — no industry wrote a pillar and at least one failed; the rest
        legitimately had too few scorable peers.

    All three used to return a zero summary, and the quarterly chain's `_run_claimed_phase`
    (main.py) marks any phase that RETURNS as settled: the day-keyed claim recorded success
    and the quarter was consumed, so the Moat radar's "Peer Avg" pentagon kept last
    quarter's rows (or the flat 5.0 baseline) until the next one. Raising releases the
    claim unsettled, so the chain retries the phase inside its catch-up window and the
    ledger row carries this message as its `error`. Same contract as
    `IndustryDossierRecomputeSkipped`.

    Still returns: every industry skipped as fresh, every attempted industry short of
    peers (an industry with fewer than MIN_SAMPLE_SIZE scorable peers never writes and so
    is never fresh — a same-day re-run attempts exactly those), and a PARTIAL run.
    """

    def __init__(self, reason: str, detail: str) -> None:
        self.reason = reason
        super().__init__(f"industry_moat_benchmark recompute SKIPPED ({reason}) — {detail}")


# ── Constants ───────────────────────────────────────────────────────

MIN_SAMPLE_SIZE = 5
TOP_TICKERS_PER_INDUSTRY = 200
# Concurrency calibrated for FMP Premium (3000/min cap). Each ticker
# fans out 5 parallel FMP calls + 1 sequential transcript-content call
# (~6 total). Running 1 industry × 3 tickers in flight stays under
# ~50 calls/sec sustained = 3000/min, with comfortable headroom for
# the transcript-content burst tail. Bumping these higher risks 429s
# (the FMP integration raises FMPRateLimitException on 429 with no
# retry, so dropped peers shrink the per-pillar sample size).
PER_TICKER_FMP_CONCURRENCY = 3
PER_INDUSTRY_CONCURRENCY = 1
MODEL_VERSION = "moat_v1.2026-05"
TABLE_NAME = "industry_moat_benchmarks"
# Re-running the bootstrap with `skip_if_fresh_hours` set lets the
# operator resume after a Ctrl-C / rate-limit-induced abort — any
# industry with a benchmark row newer than this is skipped.
DEFAULT_SKIP_IF_FRESH_HOURS = 24
# Single resolver — see `app/services/universe_data.py`. There were FOUR different
# path idioms for this one directory, and the file is FMP-derived so it has to be
# able to move out of the repo (ToS §2.6.1) without a hunt.
from app.services.universe_data import INDUSTRY_UNIVERSE, load_universe, universe_path


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


def _zero_write_failure(pillars_written: int, stats: Dict[str, int]) -> Optional[str]:
    """Why an attempted industry that wrote no pillar counts as FAILED, or None.

    None also when it legitimately had nothing to write: some peers were scored, but no
    pillar reached MIN_SAMPLE_SIZE (a small industry — it never writes, so it is never
    fresh, and a same-day re-run attempts it again). Failed when its pillars met the floor
    but every upsert failed, or when no peer could be scored at all: `_score_one_ticker`
    turns an FMP failure on the profile into None, so an outage looks like an empty result.
    """
    if pillars_written:
        return None
    if stats.get("eligible", 0):
        return "every upsert failed"
    if not stats.get("scored", 0):
        return "no peer could be scored"
    return None


def _load_universe_industries() -> List[Tuple[str, List[Tuple[str, float]]]]:
    """Return [(industry, [(ticker, mkt_cap), ...]), ...]. Tickers are
    sorted by market-cap descending. Empty industries are skipped.
    """
    industries = load_universe(INDUSTRY_UNIVERSE)
    if not industries:
        return []          # already logged at ERROR by `load_universe`
    out: List[Tuple[str, List[Tuple[str, float]]]] = []
    for entry in industries:
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
        *,
        industry_tam: Optional[Any] = None,
    ) -> Optional[Dict[str, Optional[float]]]:
        """Fetch the focal data for one peer + run the deterministic
        scorer. Returns {pillar_name: score | None}, or None if the
        FMP profile lookup failed (ticker doesn't exist / FMP error).

        Fetches in parallel:
          profile, income(annual,2), balance(annual,2), ratios(annual,1),
          transcript (list+content under the hood).
        `industry_tam` is shared across all peers in the same industry
        — caller pre-fetches it once and threads it through.
        """
        async with sem:
            try:
                profile_task = self.fmp.get_company_profile(ticker)
                income_task = self.fmp.get_income_statement(ticker, "annual", 2)
                balance_task = self.fmp.get_balance_sheet(ticker, "annual", 2)
                ratios_task = self.fmp.get_financial_ratios(ticker, "annual", 1)
                transcript_task = self.fmp.get_earning_call_transcript(ticker)
                profile, income, balance, ratios, transcript = await asyncio.gather(
                    profile_task, income_task, balance_task,
                    ratios_task, transcript_task,
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

        transcript_str = transcript if isinstance(transcript, str) else None

        try:
            pillars: Dict[str, PillarResult] = await asyncio.to_thread(
                score_moat_dimensions,
                sector=profile.get("sector"),
                industry=profile.get("industry"),
                profile=profile,
                income=_safe(income),
                balance=_safe(balance),
                ratios=_safe(ratios),
                industry_tam=industry_tam,
                transcript=transcript_str,
            )
        except Exception as exc:
            logger.debug(
                "industry_moat_benchmark: scorer failed for %s: %s",
                ticker, exc,
            )
            return None

        return {p: pillars.get(p).score if pillars.get(p) else None for p in PILLAR_ORDER}

    async def _fetch_industry_tam(
        self, industry: str, sample_ticker: str,
    ) -> Optional[Any]:
        """Fetch the shared IndustryDossier for `industry`. Needs a
        sector to look it up; pulls one from the first peer's profile
        so we don't hardcode an industry→sector map. The dossier
        service itself memoizes for 5 min, so re-running the batch
        within that window is free.
        """
        try:
            profile = await self.fmp.get_company_profile(sample_ticker)
            sector = (profile or {}).get("sector")
            if not sector:
                return None
            from app.services.industry_dossier_service import (
                get_industry_dossier_service,
            )
            return await get_industry_dossier_service().get_or_compute_dossier(
                industry=industry, sector=sector,
            )
        except Exception as exc:
            logger.warning(
                "industry_moat_benchmark: industry_tam fetch failed for "
                "%s (sample=%s): %s — Network Effects pillar will fall to baseline",
                industry, sample_ticker, exc,
            )
            return None

    # ── Industry-level aggregate ─────────────────────────────────────

    async def compute_for_industry(
        self, industry: str, *, run_id: Optional[str] = None,
        skip_if_fresh_hours: Optional[int] = None,
        stats: Optional[Dict[str, int]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Compute peer averages for one industry. Upserts one row per
        pillar that meets the sample-size threshold. Returns a summary
        dict {pillar_name: {avg, sample_size, p25, p75}} for the rows
        that were actually written.

        When `skip_if_fresh_hours` is set and the industry already has
        at least one benchmark row computed within that window, the
        function returns `{"_skipped": "fresh"}` without re-computing.
        Used by `recompute_all` to resume a partially-finished backfill
        after a Ctrl-C / rate-limit abort.

        `stats`, when given, is filled with `peers` (tickers tried), `scored` (peers the
        scorer ran on) and `eligible` (pillars that met the sample floor, i.e. were sent
        to the upsert) — what `recompute_all` needs to tell an outage from a small
        industry when nothing was written (`_zero_write_failure`).
        """
        st: Dict[str, int] = stats if stats is not None else {}
        st.update(peers=0, scored=0, eligible=0)
        run_id = run_id or str(uuid.uuid4())

        if skip_if_fresh_hours:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(hours=skip_if_fresh_hours)
            ).isoformat()
            try:
                existing = await asyncio.to_thread(
                    lambda: self.supabase.table(TABLE_NAME)
                    .select("industry")
                    .eq("industry", industry)
                    .gte("computed_at", cutoff)
                    .limit(1)
                    .execute()
                )
                if existing.data:
                    logger.info(
                        "industry_moat_benchmark: skip %r (fresh row exists "
                        "within %dh)", industry, skip_if_fresh_hours,
                    )
                    return {"_skipped": "fresh"}
            except Exception as exc:
                logger.warning(
                    "industry_moat_benchmark: freshness check failed for "
                    "%r: %s — proceeding to recompute", industry, exc,
                )

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

        # Shared per-industry context for the Network Effects pillar.
        # One fetch per industry; the dossier service caches for 5 min
        # so a re-run within that window pays nothing extra.
        industry_tam = await self._fetch_industry_tam(industry, tickers[0])

        sem = asyncio.Semaphore(PER_TICKER_FMP_CONCURRENCY)
        per_ticker_scores = await asyncio.gather(
            *[self._score_one_ticker(t, sem, industry_tam=industry_tam)
              for t in tickers],
            return_exceptions=True,
        )
        st["peers"] = len(tickers)
        st["scored"] = sum(1 for row in per_ticker_scores if isinstance(row, dict))

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
            st["eligible"] += 1
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
        skip_if_fresh_hours: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Quarterly batch entry. Iterates every industry from
        `industry_universe.json` and upserts per-pillar peer averages.
        Concurrency-bounded so we don't burst FMP quota.

        `skip_if_fresh_hours` lets the operator resume a partial backfill
        — any industry with a benchmark row newer than this window is
        skipped. Pass 0 / None to force a full recompute. The CLI
        default (24h) means a Ctrl-C abort can be resumed by simply
        re-running the same command without re-doing finished work.

        Raises `IndustryMoatBenchmarkRecomputeSkipped` (after an ERROR log) when the
        universe yields no industry (before any write), or when no attempted industry wrote
        a pillar and at least one of them FAILED (`_zero_write_failure`) — the quarterly
        chain's claim stays UNSETTLED and the phase is retried. Industries skipped as fresh,
        industries legitimately short of peers, and a partial run are settled returns.
        """
        run_id = run_id or str(uuid.uuid4())
        started = time.time()
        universe = _load_universe_industries()
        if not universe:
            # `load_universe` already logged the Storage/parse reason at ERROR when the file
            # itself failed; this line names the consequence (and covers a file that loaded
            # but holds no industry with market caps).
            exc = IndustryMoatBenchmarkRecomputeSkipped(
                "empty universe",
                f"{INDUSTRY_UNIVERSE} yielded no industry with market caps (it is not in "
                "git: the Supabase Storage download failed, or the file holds no "
                "market_caps). Nothing was written; the previous industry_moat_benchmarks "
                "rows are left untouched.",
            )
            logger.error("%s", exc)
            raise exc
        industries = [ind for ind, _ in universe]
        logger.info(
            "industry_moat_benchmark: recompute_all starting — "
            "run_id=%s, industries=%d, skip_if_fresh_hours=%s",
            run_id, len(industries), skip_if_fresh_hours,
        )

        sem = asyncio.Semaphore(PER_INDUSTRY_CONCURRENCY)
        pillars_written = 0
        skipped_low_sample = 0
        skipped_fresh = 0

        # (industry, pillars written, pillars short of the floor, fresh-skipped, failure)
        async def _one(ind: str) -> Tuple[str, int, int, bool, Optional[str]]:
            stats: Dict[str, int] = {}
            async with sem:
                try:
                    written = await self.compute_for_industry(
                        ind, run_id=run_id,
                        skip_if_fresh_hours=skip_if_fresh_hours,
                        stats=stats,
                    )
                except Exception as exc:
                    logger.error(
                        "industry_moat_benchmark: compute_for_industry "
                        "failed for %r: %s: %s", ind, type(exc).__name__, exc,
                    )
                    return ind, 0, len(PILLAR_ORDER), False, "raised"
                if written.get("_skipped") == "fresh":
                    return ind, 0, 0, True, None
                wp = len(written)
                failure = _zero_write_failure(wp, stats)
                if failure == "no peer could be scored":
                    # The per-ticker failures are DEBUG-only (one line per peer would flood
                    # an outage); this is the line that names it. Upsert failures were
                    # already logged at ERROR per pillar.
                    logger.warning(
                        "industry_moat_benchmark: %r — none of its %d peers could be "
                        "scored (every profile fetch failed or came back empty; FMP "
                        "unreachable or refusing?); no pillar written", ind,
                        stats.get("peers", 0),
                    )
                return ind, wp, len(PILLAR_ORDER) - wp, False, failure

        results = await asyncio.gather(
            *[_one(ind) for ind in industries], return_exceptions=True,
        )
        failures: Dict[str, int] = {}
        for ind, r in zip(industries, results):
            if not isinstance(r, tuple):
                # `_one` catches Exception, so this is a BaseException escaping it.
                logger.error(
                    "industry_moat_benchmark: %r ended without a result: %s: %s",
                    ind, type(r).__name__, r,
                )
                failures["raised"] = failures.get("raised", 0) + 1
                continue
            pillars_written += r[1]
            skipped_low_sample += r[2]
            if r[3]:
                skipped_fresh += 1
            if r[4]:
                failures[r[4]] = failures.get(r[4], 0) + 1
        failed = sum(failures.values())

        summary = {
            "run_id": run_id,
            "industries": len(industries),
            "pillars_written": pillars_written,
            "industries_failed": failed,
            "skipped_low_sample": skipped_low_sample,
            "skipped_fresh": skipped_fresh,
            "elapsed_seconds": round(time.time() - started, 1),
        }
        if pillars_written == 0 and failed:
            attempted = len(industries) - skipped_fresh
            reason = "every industry failed" if failed >= attempted else "nothing written"
            breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(failures.items()))
            exc = IndustryMoatBenchmarkRecomputeSkipped(
                reason,
                f"{attempted} industr{'y' if attempted == 1 else 'ies'} attempted, 0 pillar "
                f"rows written; {failed} failed ({breakdown}), {attempted - failed} short of "
                f"{MIN_SAMPLE_SIZE} scorable peers. FMP was unreachable or refusing for the "
                "whole run, or every write failed — the per-industry lines above name the "
                "cause. No industry got a fresh row, so a retry recomputes them all.",
            )
            logger.error("%s", exc)
            raise exc
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
