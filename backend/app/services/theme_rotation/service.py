"""Orchestrates one monthly theme rotation: claim → load → score → check → plan → record →
publish. See `models` for the rules and `rotation.plan_rotation` for the decision itself.

Fail-closed at every step: any `ThemeSourceError` (or an unexpected error) marks the run
FAILED, records nothing as published, and leaves every theme's list exactly as it was.
Publishing is ONE database transaction for all themes (`publish_theme_rotation`,
migration 174), which also refuses if a list was edited in Studio after this run read it.

Modes: `live` publishes; `dry_run` computes and records but never publishes (and its
decisions never count as history); `preview` is the owner's manual look (the
`scripts/preview_theme_rotation.py` CLI), never unique, never published.
"""
from __future__ import annotations

import asyncio
import logging
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from app.config import settings
from app.utils.market_hours import ET
from app.utils.postgrest_paging import fetch_all_rows
from app.services.theme_rotation.definitions import DEFINITIONS_VERSION, THEME_DEFINITIONS
from app.services.theme_rotation.llm_gate import FitGate
from app.services.theme_rotation.models import (
    Action,
    Candidate,
    Decision,
    MemberHistory,
    Reason,
    RotationConfig,
    ThemeDefinition,
    ThemePlan,
)
from app.services.theme_rotation.reasons import user_reason
from app.services.theme_rotation.rotation import plan_rotation
from app.services.theme_rotation.scoring import (
    build_pool_context,
    count_keyword_hits,
    floor_failure,
    is_us_listed,
    score_candidate,
    segment_theme_share,
)
from app.services.theme_rotation.sources import (
    CallCounter,
    PriceStats,
    ThemeSourceError,
    load_etf_holdings,
    load_price_stats,
    load_profiles,
    load_segments,
    load_universe,
)

logger = logging.getLogger(__name__)

HISTORY_RUNS = 6                 # published live runs that count as history
OUTSIDER_FETCH_LIMIT = 40        # outsiders per theme that get the full (costly) data pull
FIT_CONTENTION_EXTRA = 4         # outsiders beyond the entry zone that still get a fit check
# Spread over the 7-day catch-up window, not burned hourly: attempt 1 at the anchor, one
# retry an hour later (a blip), then at most ONE a day — three hourly failures used to
# skip a month on a ~3-hour upstream outage with six days of the window left.
MAX_ATTEMPTS_PER_MONTH = 5
SHUTDOWN_ERROR = "cancelled (shutdown)"   # a redeploy is not an attempt
STALE_IN_PROGRESS = timedelta(hours=2)
_DECISION_CHUNK = 500


@dataclass
class ThemeRow:
    slug: str
    title: str
    tickers: List[str]
    rotation_enabled: bool = True
    pinned: Set[str] = field(default_factory=set)
    blocked: Set[str] = field(default_factory=set)
    # The `tickers` array exactly as stored — what the publish RPC compares against. The
    # normalised list above is not: one lower-case or duplicated entry typed in Studio made
    # every publish attempt fail as "theme_basket_changed", and one theme fails the month.
    stored: List[Optional[str]] = field(default_factory=list)


@dataclass
class RunResult:
    run_id: Optional[str]
    run_month: date
    mode: str
    status: str                  # published | computed | skipped | failed
    plans: Dict[str, ThemePlan] = field(default_factory=dict)
    stored_baskets: Dict[str, List[Optional[str]]] = field(default_factory=dict)
    skipped_themes: List[str] = field(default_factory=list)
    fmp_calls: int = 0
    llm_calls: int = 0
    llm_failures: int = 0
    llm_tokens: int = 0
    error: Optional[str] = None

    def summary(self) -> Dict[str, object]:
        return {
            slug: {
                "before": len(p.before), "after": len(p.after), "added": p.added,
                "returned": p.returned, "removed": p.removed, "deferred": p.deferred,
                "change_count": p.change_count, "change_cap": p.change_cap,
                "shortfall": p.shortfall,
            }
            for slug, p in self.plans.items()
        } | ({"_skipped": self.skipped_themes} if self.skipped_themes else {})


class ThemeRotationService:
    def __init__(self, *, supabase=None, fmp=None, gemini=None,
                 cfg: Optional[RotationConfig] = None):
        self._supabase = supabase
        self._fmp = fmp
        self._gemini = gemini
        max_frac = float(getattr(settings, "THEME_ROTATION_MAX_CHANGE_FRACTION", 0.30))
        if not math.isfinite(max_frac) or not 0.0 <= max_frac <= 1.0:
            logger.error("THEME_ROTATION_MAX_CHANGE_FRACTION=%r is invalid — using 0.30", max_frac)
            max_frac = 0.30
        self.cfg = cfg or RotationConfig(max_change_fraction=max_frac)

    # ── Public entry points ──────────────────────────────────────────────────────────

    async def run(self, run_month: date, mode: str, *, slugs: Optional[Sequence[str]] = None,
                  record: bool = True, as_of: Optional[date] = None) -> RunResult:
        """One rotation. `record=False` (preview only) writes nothing at all."""
        if mode not in ("live", "dry_run", "preview"):
            raise ValueError(f"unknown rotation mode {mode!r}")
        if run_month.day != 1:
            raise ValueError(f"run_month must be the first of a month, got {run_month}")
        if not record and mode != "preview":
            raise ValueError("only a preview may run without recording")
        # The ET trading date: an evening retry in UTC is already tomorrow, and users
        # would read "Updated <tomorrow>".
        as_of = as_of or datetime.now(ET).date()

        run_id: Optional[str] = None
        if record:
            run_id = await self._claim_run(run_month, mode)
            if run_id is None:
                return RunResult(None, run_month, mode, "skipped")

        result = RunResult(run_id, run_month, mode, "computed")
        try:
            await self._compute(result, slugs=slugs, as_of=as_of)
            if record:
                await self._record(result)
            if mode == "live" and record:
                published = await self._publish(result, as_of)
                result.status = "published" if published else "computed"
                if published:
                    await self._refresh_caches()
                    await self._refresh_insights(result)
            for slug, plan in result.plans.items():
                if plan.shortfall:
                    logger.error("theme rotation %s %s: list is SHORT (%d < %d) — pool ran dry",
                                 run_month.isoformat(), slug, len(plan.after), self.cfg.min_size)
            logger.info("theme rotation %s (%s): %s — fmp=%d llm=%d/%d failed",
                        run_month.isoformat(), mode, result.status, result.fmp_calls,
                        result.llm_calls, result.llm_failures)
            return result
        except asyncio.CancelledError:
            # Shielded: a redeploy cancels us, and the write that says so must not be
            # cancelled with it (the row would look in-progress until it went stale).
            await asyncio.shield(self._mark_failed(run_id, SHUTDOWN_ERROR, result))
            raise
        except Exception as e:
            result.status = "failed"
            result.error = f"{type(e).__name__}: {e}"
            logger.error("theme rotation %s (%s) FAILED — nothing published: %s",
                         run_month.isoformat(), mode, result.error, exc_info=True)
            await self._mark_failed(run_id, result.error, result)
            raise

    async def month_done(self, run_month: date, mode: str) -> Optional[bool]:
        """True when this month's run of `mode` already finished; None when unreadable."""
        try:
            rows = await asyncio.to_thread(
                lambda: self._db().table("theme_rotation_runs").select("status, attempts")
                .eq("run_month", run_month.isoformat()).eq("mode", mode).limit(1).execute()
            )
        except Exception as e:
            logger.warning("theme rotation: run-state read failed (%s: %s)", type(e).__name__, e)
            return None
        data = getattr(rows, "data", None) or []
        if not data:
            return False
        row = data[0]
        if mode == "live":
            return row.get("status") == "published"
        return row.get("status") in ("computed", "published")

    async def month_attempted(self, run_month: date, mode: str) -> Optional[bool]:
        """True when a run row exists for this month (it was tried); None when unreadable."""
        try:
            rows = await asyncio.to_thread(
                lambda: self._db().table("theme_rotation_runs").select("id")
                .eq("run_month", run_month.isoformat()).eq("mode", mode).limit(1).execute()
            )
        except Exception as e:
            logger.warning("theme rotation: run-state read failed (%s: %s)", type(e).__name__, e)
            return None
        return bool(getattr(rows, "data", None) or [])

    async def _run_status(self, run_id: Optional[str]) -> Optional[str]:
        if run_id is None:
            return None
        try:
            rows = await asyncio.to_thread(
                lambda: self._db().table("theme_rotation_runs").select("status")
                .eq("id", run_id).limit(1).execute())
        except Exception as e:
            logger.warning("theme rotation: run status unreadable (%s: %s)", type(e).__name__, e)
            return None
        data = getattr(rows, "data", None) or []
        return data[0].get("status") if data else None

    async def attempts_exhausted(self, run_month: date, mode: str) -> bool:
        try:
            rows = await asyncio.to_thread(
                lambda: self._db().table("theme_rotation_runs").select("status, attempts")
                .eq("run_month", run_month.isoformat()).eq("mode", mode).limit(1).execute()
            )
        except Exception:
            return False
        data = getattr(rows, "data", None) or []
        return bool(data) and data[0].get("status") == "failed" and \
            int(data[0].get("attempts") or 0) >= MAX_ATTEMPTS_PER_MONTH

    # ── Claim ────────────────────────────────────────────────────────────────────────

    async def _claim_run(self, run_month: date, mode: str) -> Optional[str]:
        """Insert this month's run row, or re-open a failed / abandoned one.

        Concurrency across replicas is the caller's day-keyed `notification_job_state`
        claim; this row is the MONTH-level "already done" record. Returns the run id to use,
        or None when the month is done, still running, or out of attempts.
        """
        db = self._db()
        row = {"run_month": run_month.isoformat(), "mode": mode,
               "definitions_version": DEFINITIONS_VERSION,
               "params": _params_snapshot(self.cfg)}
        if mode == "preview":
            inserted = await asyncio.to_thread(
                lambda: db.table("theme_rotation_runs").insert(row).execute())
            return (getattr(inserted, "data", None) or [{}])[0].get("id")
        try:
            inserted = await asyncio.to_thread(
                lambda: db.table("theme_rotation_runs").insert(row).execute())
            return (getattr(inserted, "data", None) or [{}])[0].get("id")
        except Exception as e:
            if "23505" not in str(e) and "duplicate key" not in str(e).lower():
                raise
        existing = await asyncio.to_thread(
            lambda: db.table("theme_rotation_runs")
            .select("id, status, attempts, started_at, finished_at, error")
            .eq("run_month", run_month.isoformat()).eq("mode", mode).limit(1).execute())
        data = getattr(existing, "data", None) or []
        if not data:
            raise RuntimeError("theme_rotation_runs: insert conflicted but no row is readable")
        cur = data[0]
        status, attempts = cur.get("status"), int(cur.get("attempts") or 0)
        if status == "published" or (mode == "dry_run" and status == "computed"):
            return None
        if status == "in_progress":
            started = _parse_ts(cur.get("started_at"))
            if started is not None and datetime.now(timezone.utc) - started < STALE_IN_PROGRESS:
                logger.info("theme rotation %s: a run is already in progress — skipping", run_month)
                return None
        shutdown = status == "failed" and cur.get("error") == SHUTDOWN_ERROR
        if attempts >= MAX_ATTEMPTS_PER_MONTH and not shutdown:
            logger.error("theme rotation MISSED %s (%s): %d attempts failed — lists unchanged "
                         "this month; see theme_rotation_runs.error", run_month, mode, attempts)
            return None
        if status == "failed" and not shutdown and attempts >= 2:
            finished = _parse_ts(cur.get("finished_at"))
            if finished is not None and \
                    finished.astimezone(ET).date() >= datetime.now(ET).date():
                logger.info("theme rotation %s (%s): attempt %d failed today — next attempt "
                            "after midnight ET", run_month, mode, attempts)
                return None
        # Re-open: compare-and-swap on the observed attempts AND status, so two re-openers
        # cannot both win (a shutdown re-open leaves `attempts` unchanged, so the status
        # flip is what makes it exclusive).
        updated = await asyncio.to_thread(
            lambda: db.table("theme_rotation_runs").update({
                "status": "in_progress", "attempts": attempts if shutdown else attempts + 1,
                "error": None,
                "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None,
                "definitions_version": DEFINITIONS_VERSION, "params": row["params"],
            }).eq("id", cur["id"]).eq("attempts", attempts).eq("status", status).execute())
        if not (getattr(updated, "data", None) or []):
            return None
        return cur["id"]

    async def _mark_failed(self, run_id: Optional[str], error: str, result: RunResult) -> None:
        if run_id is None:
            return
        try:
            await asyncio.to_thread(
                lambda: self._db().table("theme_rotation_runs").update({
                    "status": "failed", "error": error[:2000],
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "fmp_calls": result.fmp_calls, "llm_tokens": result.llm_tokens,
                # Never demote a finished run: a publish whose answer was lost (or a cancel
                # during the cache refresh) must stay published, or the month rotates twice
                # and its recorded changes are deleted by the re-run. The status predicate
                # is re-checked after any row lock the publish RPC holds.
                }).eq("id", run_id).in_("status", ["in_progress", "computed"]).execute())
        except Exception as e:
            logger.error("theme rotation: could not mark run %s failed (%s: %s) — it will look "
                         "in-progress until it goes stale", run_id, type(e).__name__, e)

    # ── Compute ──────────────────────────────────────────────────────────────────────

    async def _compute(self, result: RunResult, *, slugs: Optional[Sequence[str]],
                       as_of: date) -> None:
        preview_only = result.run_id is None
        rows = await self._read_themes(slugs, legacy_ok=preview_only)
        try:
            history = await self._read_history(result.run_month)
        except Exception as e:
            if not preview_only:
                raise
            # A preview may run before migration 174 is applied: no history yet.
            logger.warning("theme rotation preview: history unreadable (%s: %s) — assuming none",
                           type(e).__name__, e)
            history = {}
        counter = CallCounter()
        gate: Optional[FitGate] = None
        # A run that fails part-way must still record what it spent — `_mark_failed`
        # writes these, and a failed run used to report 0 FMP calls after 18.
        try:
            fmp = self._fmp_client()
            work = [(r, THEME_DEFINITIONS.get(r.slug)) for r in rows]
            active = [(r, d) for r, d in work if d is not None and r.rotation_enabled and r.tickers]
            result.skipped_themes = [r.slug for r, d in work if (r, d) not in active]
            if not active:
                return

            universe = await load_universe(fmp, counter)
            etf_names = sorted({e for _, d in active for e in d.seed_etfs})
            etf_holdings, etf_failed = await load_etf_holdings(fmp, etf_names, counter)
            for row, defn in active:
                loaded = [e for e in defn.seed_etfs if e in etf_holdings]
                if len(loaded) < math.ceil(len(defn.seed_etfs) * (1 - _max_etf_failure())):
                    raise ThemeSourceError(
                        "etf/holdings",
                        f"{row.slug}: only {loaded} of {list(defn.seed_etfs)} answered")

            # Pass 1: who gets the full data pull, per theme.
            pools: Dict[str, List[str]] = {}
            for row, defn in active:
                pools[row.slug] = _candidate_pool(row, defn, universe, etf_holdings,
                                                  _bench(history.get(row.slug, {})))
            everyone = sorted({t for pool in pools.values() for t in pool})
            profiles = await load_profiles(fmp, everyone, counter)
            segments = await load_segments(fmp, everyone, counter)
            prices = await load_price_stats(fmp, everyone, as_of=as_of, counter=counter)

            gate = FitGate(definitions_version=DEFINITIONS_VERSION, supabase=self._supabase,
                           gemini=self._gemini, persist=result.run_id is not None)

            # Pass 2: score, check, plan.
            for row, defn in active:
                members = {t.upper() for t in row.tickers}
                candidates = {
                    t: _candidate(t, t in members, defn, universe.get(t), profiles.get(t),
                                  segments.get(t), prices.get(t, None), t in prices, etf_holdings)
                    for t in pools[row.slug]
                }
                etfs_loaded = sum(1 for e in defn.seed_etfs if e in etf_holdings)
                candidates = await self._apply_fit(gate, defn, candidates, row, etfs_loaded, segments)
                ctx = build_pool_context(list(candidates.values()), etfs_loaded=etfs_loaded)
                scores = {t: score_candidate(c, defn, ctx) for t, c in candidates.items()}
                floors = {t: floor_failure(c, defn, median_member_cap=ctx.median_member_cap)
                          for t, c in candidates.items()}
                result.plans[row.slug] = plan_rotation(
                    slug=row.slug, current=row.tickers, candidates=candidates, scores=scores,
                    floors=floors, history=history.get(row.slug, {}), pinned=row.pinned,
                    blocked=row.blocked, cfg=self.cfg,
                )
                result.stored_baskets[row.slug] = list(row.stored)
        finally:
            result.fmp_calls = counter.calls
            if gate is not None:
                result.llm_calls, result.llm_failures = gate.calls, gate.failures
                result.llm_tokens = gate.tokens_used

    async def _apply_fit(self, gate: FitGate, defn: ThemeDefinition,
                         candidates: Dict[str, Candidate], row: ThemeRow,
                         etfs_loaded: int,
                         segments: Optional[Mapping[str, Optional[Dict[str, float]]]] = None,
                         ) -> Dict[str, Candidate]:
        """Run the relevance check on every member and on the outsiders in contention.

        Contention = outsiders that pass the newcomer floors and rank (on a provisional score)
        within the entry zone plus a few. Checking everyone would spend on stocks that could
        never enter this month.
        """
        ctx = build_pool_context(list(candidates.values()), etfs_loaded=etfs_loaded)
        provisional = {t: score_candidate(c, defn, ctx).total for t, c in candidates.items()}
        n_target = max(self.cfg.min_size, min(self.cfg.max_size, len(row.tickers)))
        entry_zone = max(1, math.floor(self.cfg.entry_rank_fraction * n_target + 1e-9))
        outsiders = sorted(
            (t for t, c in candidates.items()
             if not c.is_member and t not in row.blocked
             and floor_failure(c, defn, median_member_cap=ctx.median_member_cap) is None),
            key=lambda t: (-provisional[t], t),
        )[: entry_zone + FIT_CONTENTION_EXTRA]
        to_check = [t for t, c in candidates.items() if c.is_member] + outsiders

        async def check(t: str) -> Tuple[str, Optional[Tuple[str, str]]]:
            c = candidates[t]
            verdict = await gate.verdict(defn, t, c.name or t, c.description,
                                         industry=c.industry,
                                         segments=(segments or {}).get(t))
            return t, ((verdict.fit, verdict.pure_play_band) if verdict is not None else None)

        sem = asyncio.Semaphore(6)

        async def bounded(t: str):
            async with sem:
                return await check(t)

        fits = dict(await asyncio.gather(*(bounded(t) for t in to_check)))
        out = dict(candidates)
        for t, verdict in fits.items():
            c = candidates[t]
            fit, band = verdict if verdict is not None else (None, None)
            out[t] = Candidate(**{**c.__dict__, "fit": fit, "fit_band": band})
        return out

    # ── Record / publish ─────────────────────────────────────────────────────────────

    async def _record(self, result: RunResult) -> None:
        db = self._db()
        run_id = result.run_id
        rows = []
        for slug, plan in result.plans.items():
            for d in plan.decisions:
                rows.append({
                    "run_id": run_id, "run_month": result.run_month.isoformat(), "slug": slug,
                    "ticker": d.ticker, "action": d.action.value, "reason_code": d.reason.value,
                    "reason_text": user_reason(d.action, d.reason, d.score_parts),
                    "score": d.score, "score_parts": d.score_parts, "rank": d.rank,
                    "was_member": d.was_member, "strike": d.strike,
                })
        # A re-opened run starts from a clean slate.
        await asyncio.to_thread(
            lambda: db.table("theme_rotation_decisions").delete().eq("run_id", run_id).execute())
        for i in range(0, len(rows), _DECISION_CHUNK):
            chunk = rows[i:i + _DECISION_CHUNK]
            await asyncio.to_thread(
                lambda c=chunk: db.table("theme_rotation_decisions").insert(c).execute())
        await asyncio.to_thread(
            lambda: db.table("theme_rotation_runs").update({
                "status": "computed", "summary": result.summary(),
                "fmp_calls": result.fmp_calls, "llm_tokens": result.llm_tokens,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", run_id).execute())

    async def _publish(self, result: RunResult, as_of: date) -> bool:
        baskets = {slug: {"expected": result.stored_baskets.get(slug, plan.before),
                          "tickers": plan.after}
                   for slug, plan in result.plans.items() if plan.after}
        if not baskets:
            # Nothing rotation-enabled this month: the month is still DONE, or the loop would
            # re-run it every hour for a week.
            await asyncio.to_thread(
                lambda: self._db().table("theme_rotation_runs").update({
                    "status": "published",
                    "published_at": datetime.now(timezone.utc).isoformat(),
                }).eq("id", result.run_id).execute())
            logger.info("theme rotation %s: no rotation-enabled theme — month closed with no "
                        "changes", result.run_month.isoformat())
            return True
        try:
            response = await asyncio.to_thread(
                lambda: self._db().rpc("publish_theme_rotation", {
                    "p_run_id": result.run_id, "p_baskets": baskets,
                    "p_as_of": as_of.isoformat(),
                }).execute())
        except Exception as e:
            # The RPC may have COMMITTED and only its answer was lost (a read timeout, a
            # reset). Ask the row before calling it a failure.
            if await self._run_status(result.run_id) == "published":
                logger.warning("theme rotation %s: publish answer lost (%s: %s) but the run "
                               "IS published", result.run_month.isoformat(), type(e).__name__, e)
                return True
            raise
        outcome = getattr(response, "data", None)
        logger.info("theme rotation %s: publish → %s (%d themes)",
                    result.run_month.isoformat(), outcome, len(baskets))
        return outcome in ("published", "already_published")

    async def _refresh_caches(self) -> None:
        try:
            from app.services.home_dashboard_service import refresh_theme_caches
            await refresh_theme_caches()
        except Exception as e:
            # The new lists are live in the database either way; every cache here expires
            # within minutes (themes/detail 10 min, chat starters 1 h).
            logger.warning("theme rotation: cache refresh after publish failed (%s: %s)",
                           type(e).__name__, e)

    async def _refresh_insights(self, result: RunResult) -> None:
        """Recompute the daily insights of the themes that just changed, so "Performance
        vs S&P 500 · Current stocks" describes the NEW list the same evening instead of
        the pre-rotation one until the next trading day (a whole weekend when the 1st is
        a Friday). Best effort, and only while the insights job itself is switched on —
        the read path already hides numbers built on another list."""
        if not getattr(settings, "THEME_INSIGHTS_ENABLED", False):
            return
        changed = sorted(slug for slug, plan in result.plans.items()
                         if plan.added or plan.returned or plan.removed)
        if not changed:
            return
        try:
            from app.services.theme_insights_service import get_theme_insights_service
            await get_theme_insights_service().run_daily(slugs=changed)
        except Exception as e:
            logger.warning("theme rotation: insights refresh after publish failed for %s "
                           "(%s: %s) — the next daily run catches up", changed,
                           type(e).__name__, e)

    # ── Reads ────────────────────────────────────────────────────────────────────────

    async def _read_themes(self, slugs: Optional[Sequence[str]], *,
                           legacy_ok: bool = False) -> List[ThemeRow]:
        def query(columns: str):
            q = (self._db().table("trending_themes").select(columns)
                 .eq("is_active", True).order("sort_order"))
            if slugs:
                q = q.in_("slug", list(slugs))
            return q.execute()

        full = ("slug, title, tickers, is_active, rotation_enabled, pinned_tickers, "
                "blocked_tickers, sort_order")
        try:
            response = await asyncio.to_thread(query, full)
        except Exception as e:
            if not legacy_ok:
                raise
            # Before migration 174 the override columns do not exist; a preview still runs.
            logger.warning("theme rotation preview: reading themes without the 174 columns "
                           "(%s: %s)", type(e).__name__, e)
            response = await asyncio.to_thread(query, "slug, title, tickers, is_active, sort_order")
        out: List[ThemeRow] = []
        for r in getattr(response, "data", None) or []:
            raw = r.get("tickers") if isinstance(r.get("tickers"), list) else []
            tickers = [t.strip().upper() for t in raw if isinstance(t, str) and t.strip()]
            out.append(ThemeRow(
                slug=str(r.get("slug") or ""), title=str(r.get("title") or ""),
                tickers=list(dict.fromkeys(tickers)),
                rotation_enabled=bool(r.get("rotation_enabled", True)),
                pinned={str(t).upper() for t in (r.get("pinned_tickers") or []) if t},
                blocked={str(t).upper() for t in (r.get("blocked_tickers") or []) if t},
                stored=[t if isinstance(t, str) else None for t in raw],
            ))
        if not out:
            raise ThemeSourceError("trending_themes", "no active themes were readable")
        return out

    async def _read_history(self, run_month: date) -> Dict[str, Dict[str, MemberHistory]]:
        """History from the last published LIVE runs before `run_month` (never dry runs)."""
        db = self._db()
        runs = await asyncio.to_thread(
            lambda: db.table("theme_rotation_runs").select("id, run_month")
            .eq("mode", "live").eq("status", "published")
            .lt("run_month", run_month.isoformat())
            .order("run_month", desc=True).limit(HISTORY_RUNS).execute())
        run_rows = getattr(runs, "data", None) or []
        if not run_rows:
            return {}
        order = {r["id"]: i for i, r in enumerate(run_rows)}   # 0 = most recent
        # Paged: ~450 rows a run, so six runs are well past PostgREST's 1,000-row cap, and
        # an unpaged read silently dropped the NEWEST run — every member then read as
        # tenure 0 and natural rotation froze with no error.
        decisions = await asyncio.to_thread(
            fetch_all_rows,
            lambda: db.table("theme_rotation_decisions")
            .select("id, run_id, slug, ticker, action, strike, was_member")
            .in_("run_id", list(order)),
            order_by="id", what="theme rotation history")
        return build_history(decisions, order)

    # ── Clients ──────────────────────────────────────────────────────────────────────

    def _db(self):
        if self._supabase is None:
            from app.database import get_supabase
            self._supabase = get_supabase()
        return self._supabase

    def _fmp_client(self):
        if self._fmp is None:
            from app.integrations.fmp import get_fmp_client
            self._fmp = get_fmp_client()
        return self._fmp


# ── Pure helpers (tested directly) ────────────────────────────────────────────────────

def build_history(rows: Iterable[Mapping[str, object]],
                  run_order: Mapping[str, int]) -> Dict[str, Dict[str, MemberHistory]]:
    """{slug: {ticker: MemberHistory}} from decision rows of published LIVE runs.

    `run_order[run_id]` is 0 for the most recent run. A ticker is a member AFTER a run when
    it was kept, added or returned, or when its removal was DEFERRED by the cap (it stayed).
    Tenure is the unbroken streak of such runs ending with the latest one — KNOWN when the
    streak began with an addition, UNKNOWN (None → seasoned) when it began with `kept`,
    i.e. the ticker was already a member before the records start.
    """
    per: Dict[Tuple[str, str], Dict[int, Mapping[str, object]]] = {}
    for r in rows:
        idx = run_order.get(str(r.get("run_id")))
        slug, ticker = r.get("slug"), r.get("ticker")
        if idx is None or not isinstance(slug, str) or not isinstance(ticker, str):
            continue
        per.setdefault((slug, ticker.upper()), {})[idx] = r
    # The runs that actually REVIEWED each theme. A theme left out of a run (rotation off
    # for it that month, or an all-off month closed empty) has no rows there, and that
    # absence is "not reviewed", not "not a member": walking the global run order made
    # every member read as tenure 0 the month the theme came back.
    reviewed_runs: Dict[str, Set[int]] = {}
    for (slug, _), by_run in per.items():
        reviewed_runs.setdefault(slug, set()).update(by_run)
    reviewed = {slug: sorted(runs) for slug, runs in reviewed_runs.items()}
    out: Dict[str, Dict[str, MemberHistory]] = {}
    for (slug, ticker), by_run in per.items():
        streak_start: Optional[Mapping[str, object]] = None
        tenure = 0
        for i in reviewed[slug]:                      # most recent first
            r = by_run.get(i)
            if r is None or not _member_after(r):
                break
            tenure += 1
            streak_start = r
        if tenure == 0:
            tenure_months: Optional[int] = 0
        elif streak_start is not None and streak_start.get("action") in (
                Action.ADDED.value, Action.RETURNED.value):
            tenure_months = tenure
        else:
            tenure_months = None
        latest = by_run.get(0)
        out.setdefault(slug, {})[ticker] = MemberHistory(
            tenure_months=tenure_months,
            struck_last_month=bool(latest and latest.get("strike") and _member_after(latest)),
            flips_6m=sum(1 for r in by_run.values() if r.get("action") in (
                Action.ADDED.value, Action.RETURNED.value, Action.REMOVED.value)),
            removed_recently=any(r.get("action") == Action.REMOVED.value for r in by_run.values()),
        )
    return out


def _member_after(r: Mapping[str, object]) -> bool:
    action = r.get("action")
    if action in (Action.KEPT.value, Action.ADDED.value, Action.RETURNED.value):
        return True
    return action == Action.DEFERRED.value and bool(r.get("was_member"))


def _bench(history: Mapping[str, MemberHistory]) -> Set[str]:
    return {t for t, h in history.items() if h.removed_recently}


def _candidate_pool(row: ThemeRow, defn: ThemeDefinition, universe: Mapping[str, dict],
                    etf_holdings: Mapping[str, Mapping[str, float]], bench: Set[str]) -> List[str]:
    """Members + bench + the most promising outsiders (ETF votes first, then size)."""
    members = [t.upper() for t in row.tickers]
    votes: Dict[str, Tuple[int, float]] = {}
    for etf in defn.seed_etfs:
        for t, w in (etf_holdings.get(etf) or {}).items():
            n, wmax = votes.get(t, (0, 0.0))
            votes[t] = (n + 1, max(wmax, w))
    outsiders: Set[str] = set()
    for t in votes:
        outsiders.add(t)
    for t, u in universe.items():
        if (u.get("industry") or "") in defn.industries:
            outsiders.add(t)
    outsiders |= {b.upper() for b in bench}
    outsiders -= set(members)
    outsiders -= {b.upper() for b in row.blocked}

    def screen_ok(t: str) -> bool:
        u = universe.get(t)
        if u is None:
            return False           # not a US-listed, actively trading stock above $300M
        cap, price = _num(u.get("marketCap")), _num(u.get("price"))
        return (cap is not None and cap >= defn.min_market_cap
                and price is not None and price >= 3.0)

    screened = [t for t in outsiders if screen_ok(t)]
    screened.sort(key=lambda t: (-votes.get(t, (0, 0.0))[0], -votes.get(t, (0, 0.0))[1],
                                 -(_num(universe[t].get("marketCap")) or 0.0), t))
    chosen = screened[:OUTSIDER_FETCH_LIMIT]
    # The bench always gets a look: "removed in month 1, back in month 2" must be possible.
    chosen += [b for b in sorted(bench) if b in screened and b not in chosen]
    return list(dict.fromkeys(members + chosen))


def _candidate(t: str, is_member: bool, defn: ThemeDefinition, u: Optional[dict],
               profile: Optional[dict], segments: Optional[Dict[str, float]],
               stats: Optional[PriceStats], history_known: bool,
               etf_holdings: Mapping[str, Mapping[str, float]]) -> Candidate:
    p = profile or {}
    u = u or {}
    holders = [etf_holdings[e][t] for e in defn.seed_etfs if t in (etf_holdings.get(e) or {})]
    active = p.get("isActivelyTrading")
    description = p.get("description") if isinstance(p.get("description"), str) else ""
    return Candidate(
        ticker=t,
        name=str(p.get("companyName") or u.get("companyName") or t),
        is_member=is_member,
        market_cap=_num(p.get("marketCap") or p.get("mktCap") or u.get("marketCap")),
        price=_num(p.get("price") or u.get("price")),
        exchange=(p.get("exchange") or u.get("exchangeShortName") or u.get("exchange") or None),
        actively_trading=(active if isinstance(active, bool) else (True if u else None)),
        industry=(p.get("industry") or u.get("industry") or None),
        description=description or "",
        segment_share=segment_theme_share(segments, defn.segment_keywords),
        keyword_hits=count_keyword_hits(description, defn.description_keywords),
        etf_holders=len(holders),
        etf_max_weight=max(holders) if holders else 0.0,
        ret_3m=stats.ret_3m if stats else None,
        ret_6m=stats.ret_6m if stats else None,
        adtv_6m=stats.adtv_6m if stats else None,
        session_coverage=stats.session_coverage if stats else None,
        sessions_listed=stats.sessions_listed if stats else None,
        history_blocked=history_known and stats is None,
    )


def _num(value: object) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _max_etf_failure() -> float:
    from app.services.theme_rotation.sources import MAX_ETF_FAILURE_FRACTION
    return MAX_ETF_FAILURE_FRACTION


def _params_snapshot(cfg: RotationConfig) -> Dict[str, object]:
    return {**cfg.__dict__, "definitions_version": DEFINITIONS_VERSION,
            "fit_model": getattr(settings, "THEME_ROTATION_FIT_MODEL", None)}


def _parse_ts(value: object) -> Optional[datetime]:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


_service: Optional[ThemeRotationService] = None


def get_theme_rotation_service() -> ThemeRotationService:
    global _service
    if _service is None:
        _service = ThemeRotationService()
    return _service
