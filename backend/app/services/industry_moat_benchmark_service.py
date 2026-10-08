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
  back to the existing 5.0 baseline. A FAILED read returns an empty
  `BenchmarkLookupFailed` instead (same baseline, but `lookup_failed()`
  is True), so the report collector can keep that report out of the
  shared caches rather than freezing a flat 5.0 polygon into them.

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
import math
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
from app.services.sector_benchmark_lookup import BenchmarkLookupFailed, lookup_failed
from app.utils.supabase_errors import is_transient_supabase_error, retry_idempotent_sync

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
# Stamped on every row (migration 057: "bump on scorer logic changes so consumers can
# detect drift"). 2026-10: the scorer compares a company with its sector's median of the
# SAME year (`annual_benchmark_key`, complete years only, n >= 20) instead of the newest
# year with n >= 20 / n >= 10. Rows written under "moat_v1.2026-05" were scored on the old
# rule; `IndustryMoatBenchmarkLookup` serves each pillar's own row and names every pillar
# still on an older vintage as pending (`_current_vintage_rows`), and the next recompute
# (the January chain, or the owner's `/admin/refresh-industry-moat-benchmarks?
# skip_recent_hours=0`) replaces them.
MODEL_VERSION = "moat_v1.2026-10"
# Every vintage the lookup knows, oldest first; a row stamped with anything else (or
# nothing) ranks below all of them.
_MODEL_VERSION_ORDER = ("moat_v1.2026-05", MODEL_VERSION)
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


#: `_zero_write_failure` reasons. Each is also a key of the run's failure breakdown.
_FAIL_UPSERTS = "every upsert failed"
_FAIL_BENCHMARK_READ = "sector-median read failed"
_FAIL_NO_PEER = "no peer could be scored"
# What each reason points the operator at, for the run-level ERROR.
_FAILURE_CAUSE = {
    _FAIL_UPSERTS: "every industry_moat_benchmarks write failed (Supabase)",
    _FAIL_BENCHMARK_READ: (
        "the sector-median read failed (Supabase sector_benchmarks unreadable), so peers "
        "were left out of the averages"
    ),
    _FAIL_NO_PEER: "FMP was unreachable or refusing (every profile fetch failed or was empty)",
    "raised": "an industry's computation raised",
}


def _zero_write_failure(pillars_written: int, stats: Dict[str, int]) -> Optional[str]:
    """Why an attempted industry that wrote no pillar counts as FAILED, or None.

    None also when it legitimately had nothing to write: some peers were scored, but no
    pillar reached MIN_SAMPLE_SIZE (a small industry — it never writes, so it is never
    fresh, and a same-day re-run attempts it again). Failed when its pillars met the floor
    but every upsert failed, or when no peer could be scored at all: `_score_one_ticker`
    turns an FMP failure on the profile into None, so an outage looks like an empty result.

    "sector-median read failed" when `stats["benchmark_failed"]` peers were left out
    because their sector-median read failed (`_score_one_ticker`) and that is why nothing
    was written: no peer was scored at all, or the scored peers plus the dropped ones would
    have reached MIN_SAMPLE_SIZE. Told apart from "no peer could be scored" so the
    operator debugs Supabase, not FMP.
    """
    if pillars_written:
        return None
    if stats.get("eligible", 0):
        return _FAIL_UPSERTS
    scored = stats.get("scored", 0)
    benchmark_failed = stats.get("benchmark_failed", 0)
    if benchmark_failed and (not scored or scored + benchmark_failed >= MIN_SAMPLE_SIZE):
        return _FAIL_BENCHMARK_READ
    if not scored:
        return _FAIL_NO_PEER
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
        drops: Optional[Dict[str, int]] = None,
    ) -> Optional[Dict[str, Optional[float]]]:
        """Fetch the focal data for one peer + run the deterministic
        scorer. Returns {pillar_name: score | None}, or None if the
        FMP profile lookup failed (ticker doesn't exist / FMP error) or
        the scorer's sector-median read failed.

        Fetches in parallel:
          profile, income(annual,2), balance(annual,2), ratios(annual,1),
          transcript (list+content under the hood).
        `industry_tam` is shared across all peers in the same industry
        — caller pre-fetches it once and threads it through.
        `drops`, when given, counts a company left out for a failed
        sector-median read under "benchmark_failed" — the one None the
        caller must not read as an FMP failure (`_zero_write_failure`).
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

        # A failed sector-median read leaves the benchmark-driven pillars unscored; folding
        # this company into the industry's peer averages would quietly drag them toward the
        # pillars that did not need a benchmark. Skip it for this run, loudly.
        if lookup_failed(pillars):
            logger.warning(
                "industry_moat_benchmark: sector-median read FAILED while scoring %s "
                "(sector=%r) — company left out of this run's peer averages",
                ticker, profile.get("sector"),
            )
            if drops is not None:
                drops["benchmark_failed"] = drops.get("benchmark_failed", 0) + 1
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
        industry when nothing was written (`_zero_write_failure`). `benchmark_failed`
        (peers left out because their sector-median read failed) is set only when it is
        non-zero, so a clean run's stats keep their three keys.
        """
        st: Dict[str, int] = stats if stats is not None else {}
        st.update(peers=0, scored=0, eligible=0)
        st.pop("benchmark_failed", None)
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
        # Shared by the gathered coroutines; each increments it on the event loop thread
        # (after its `to_thread` scorer returns), so no lock is needed.
        drops: Dict[str, int] = {}
        per_ticker_scores = await asyncio.gather(
            *[self._score_one_ticker(t, sem, industry_tam=industry_tam, drops=drops)
              for t in tickers],
            return_exceptions=True,
        )
        st["peers"] = len(tickers)
        st["scored"] = sum(1 for row in per_ticker_scores if isinstance(row, dict))
        if drops.get("benchmark_failed"):
            st["benchmark_failed"] = drops["benchmark_failed"]
            logger.warning(
                "industry_moat_benchmark: %r — %d of %d peer(s) left out because their "
                "sector-median read failed (Supabase sector_benchmarks unreadable); peer "
                "averages from the %d scored", industry, drops["benchmark_failed"],
                len(tickers), st["scored"],
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

        # (industry, pillars written, pillars short of the floor, fresh-skipped, failure,
        #  peers left out for a failed sector-median read)
        async def _one(ind: str) -> Tuple[str, int, int, bool, Optional[str], int]:
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
                    return (ind, 0, len(PILLAR_ORDER), False, "raised",
                            stats.get("benchmark_failed", 0))
                if written.get("_skipped") == "fresh":
                    return ind, 0, 0, True, None, 0
                wp = len(written)
                benchmark_failed = stats.get("benchmark_failed", 0)
                failure = _zero_write_failure(wp, stats)
                if failure == _FAIL_NO_PEER:
                    # The per-ticker failures are DEBUG-only (one line per peer would flood
                    # an outage); this is the line that names it. Upsert failures were
                    # already logged at ERROR per pillar.
                    logger.warning(
                        "industry_moat_benchmark: %r — none of its %d peers could be "
                        "scored (every profile fetch failed or came back empty; FMP "
                        "unreachable or refusing?); no pillar written", ind,
                        stats.get("peers", 0),
                    )
                elif failure == _FAIL_BENCHMARK_READ:
                    logger.warning(
                        "industry_moat_benchmark: %r — no pillar written: the "
                        "sector-median read failed for %d of its %d peers (Supabase "
                        "sector_benchmarks unreadable?), %d scored; not an FMP outage",
                        ind, benchmark_failed, stats.get("peers", 0),
                        stats.get("scored", 0),
                    )
                return ind, wp, len(PILLAR_ORDER) - wp, False, failure, benchmark_failed

        results = await asyncio.gather(
            *[_one(ind) for ind in industries], return_exceptions=True,
        )
        failures: Dict[str, int] = {}
        companies_benchmark_failed = 0
        industries_benchmark_failed = 0
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
            if r[5]:
                companies_benchmark_failed += r[5]
                industries_benchmark_failed += 1
        failed = sum(failures.values())

        summary = {
            "run_id": run_id,
            "industries": len(industries),
            "pillars_written": pillars_written,
            "industries_failed": failed,
            "skipped_low_sample": skipped_low_sample,
            "skipped_fresh": skipped_fresh,
            # Peers left out because their sector-median read failed (Supabase), and the
            # industries that lost at least one — averages from fewer peers, or none.
            "companies_benchmark_failed": companies_benchmark_failed,
            "industries_benchmark_failed": industries_benchmark_failed,
            "elapsed_seconds": round(time.time() - started, 1),
        }
        if pillars_written == 0 and failed:
            attempted = len(industries) - skipped_fresh
            reason = "every industry failed" if failed >= attempted else "nothing written"
            breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(failures.items()))
            causes = "; ".join(
                _FAILURE_CAUSE.get(k, k) for k in sorted(failures)
            )
            exc = IndustryMoatBenchmarkRecomputeSkipped(
                reason,
                f"{attempted} industr{'y' if attempted == 1 else 'ies'} attempted, 0 pillar "
                f"rows written; {failed} failed ({breakdown}), {attempted - failed} short of "
                f"{MIN_SAMPLE_SIZE} scorable peers. Cause: {causes} — the per-industry "
                "lines above name it. No industry got a fresh row, so a retry recomputes "
                "them all.",
            )
            logger.error("%s", exc)
            raise exc
        if companies_benchmark_failed:
            logger.warning(
                "industry_moat_benchmark recompute_all: %d peer(s) in %d industr%s left "
                "out because their sector-median read failed (Supabase) — those "
                "industries' averages come from fewer peers until the next recompute",
                companies_benchmark_failed, industries_benchmark_failed,
                "y" if industries_benchmark_failed == 1 else "ies",
            )
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


def _vintage_rank(model_version: Any) -> int:
    """Position of a row's `model_version` in `_MODEL_VERSION_ORDER`; -1 for an unknown or
    missing stamp (ranks below every known vintage)."""
    try:
        return _MODEL_VERSION_ORDER.index(model_version)
    except ValueError:
        return -1


def _current_vintage_rows(industry: str, rows: List[Any]) -> List[Dict[str, Any]]:
    """Per pillar, the row of the newest scorer vintage the industry holds FOR THAT PILLAR.

    Rows are upserted per (industry, pillar), so a recompute that brings only some pillars
    over the sample floor (`MIN_SAMPLE_SIZE`; FMP failures and dropped benchmark reads
    shrink each pillar's n) leaves the others on the previous vintage. Until 2026-10-08
    any current-vintage row hid every older one (review F2: one year rule per radar), so
    a partial recompute turned those pillars' real peer averages into the flat 5.0
    placeholder until the next full run — the January chain, or April if that also
    misses (round 3, RPT3-5). A real peer average under the old rule beats 5.0: each
    pillar now keeps its own row, and every pillar still on an older vintage is named at
    INFO as recompute-pending (at most once per cache window). A malformed row (no
    string pillar name) is dropped here; the caller validates the score."""
    newest_by_pillar: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        name = r.get("pillar_name")
        if not isinstance(name, str) or not name:
            continue
        held = newest_by_pillar.get(name)
        # The unique key is (industry, pillar_name), so a pillar has one row; a duplicate
        # (a changed constraint, a test double) resolves to its newest vintage.
        if held is None or _vintage_rank(r.get("model_version")) > _vintage_rank(
            held.get("model_version")
        ):
            newest_by_pillar[name] = r
    kept = list(newest_by_pillar.values())
    current = _vintage_rank(MODEL_VERSION)
    pending = sorted(
        f"{name}={r.get('model_version')!r}"
        for name, r in newest_by_pillar.items()
        if _vintage_rank(r.get("model_version")) < current
    )
    if pending:
        logger.info(
            "industry_moat_benchmark lookup: %r — %d of %d pillar(s) served from a row "
            "scored under an older vintage than %r (%s); recompute pending: January chain "
            "or /admin/refresh-industry-moat-benchmarks", industry, len(pending),
            len(kept), MODEL_VERSION, ", ".join(pending),
        )
    return kept


class IndustryMoatBenchmarkLookup:
    """SYNCHRONOUS (Supabase sync SDK, with a `time.sleep` retry on a transient error):
    from async code call it through `asyncio.to_thread` (the report collector reaches it
    from `assemble_report`, which both report doors run on a worker thread)."""

    def __init__(self) -> None:
        self.supabase = get_supabase()

    def get_pillar_benchmarks(self, industry: str) -> Dict[str, float]:
        """Return {pillar_name: peer_average_score} for `industry`.
        Empty dict means no benchmark rows yet — caller should fall
        back to the existing 5.0 baseline. Cached in-process for 1h.

        A FAILED read (a Supabase error that outlasted the transient retry) returns an
        empty `BenchmarkLookupFailed`: the same "no peer averages" to a caller that only
        reads values, but `lookup_failed(result)` is True so the report collector keeps
        that report out of the shared caches. Never cached. Each pillar is served from
        its own newest row, an older-vintage one logged as pending (`_current_vintage_rows`).
        """
        if not industry:
            return {}
        cached = _lookup_cache.get(industry)
        if cached and time.time() - cached[0] < _LOOKUP_CACHE_TTL_SECONDS:
            return cached[1]

        def _read() -> List[Any]:
            resp = (
                self.supabase.table(TABLE_NAME)
                .select("pillar_name,peer_average_score,model_version")
                .eq("industry", industry)
                .execute()
            )
            return list(resp.data or [])

        try:
            # Idempotent pure read: safe to replay on a transient blip.
            rows = retry_idempotent_sync(
                _read, what=f"{TABLE_NAME} read industry={industry!r}", logger=logger,
            )
        except Exception as exc:
            transient = is_transient_supabase_error(exc)
            (logger.warning if transient else logger.error)(
                "industry_moat_benchmark lookup FAILED for industry=%r: %s: %s — no peer "
                "averages (5.0 baseline); flagged so the report is not shared-cached",
                industry, type(exc).__name__, exc, exc_info=not transient,
            )
            return BenchmarkLookupFailed()
        out: Dict[str, float] = {}
        for r in _current_vintage_rows(industry, rows):
            name = r.get("pillar_name")
            score = r.get("peer_average_score")
            if (
                isinstance(name, str) and name
                and score is not None and not isinstance(score, bool)
            ):
                try:
                    value = float(score)
                except (TypeError, ValueError):
                    continue
                if math.isfinite(value):
                    out[name] = value
        _lookup_cache[industry] = (time.time(), out)
        return out


_lookup_singleton: Optional[IndustryMoatBenchmarkLookup] = None


def get_industry_moat_benchmark_lookup() -> IndustryMoatBenchmarkLookup:
    global _lookup_singleton
    if _lookup_singleton is None:
        _lookup_singleton = IndustryMoatBenchmarkLookup()
    return _lookup_singleton
