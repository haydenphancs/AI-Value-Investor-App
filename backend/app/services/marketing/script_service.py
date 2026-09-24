"""
The day's script: select → generate → accept, driven by the worker's kick-and-poll
(SYSTEM_DESIGN_GUIDELINES §12.5).

The media worker cannot write copy (it holds no Gemini key and imports nothing from `app.*`), so
it asks the web side for the day's script with ONE idempotent call, repeated until it gets a
final answer: `POST /api/v1/internal/marketing/runs/{run_id}/script` → `kick(run_id)`.

    first kick ──▶ the run must be HELD (in_progress, today/yesterday ET, a live claim) —
                   nothing starts writer spend for an arbitrary run id
                   selection (pure: `selection.choose`, recent picks skipped)
                   one INSERT into marketing_scripts   ← first write wins (PK run_id)
                   rest day? ──▶ "rest_day" (final)
    any kick   ──▶ status "selected" (or "generating" whose lease expired)
                   → a cap reached? close it `rejected` with a CAS, whatever the status
                   → else spawn ONE background generation, answer "generating" at once
    generation ──▶ acquire the lease: conditional UPDATE on the observed (status, generations,
                   generation_id, lease_until); refresh it before every Gemini call; write the
                   outcome FENCED on generation_id. Only one generation can ever own a run.
                   accepted → immutable · content failure → next generation, or `rejected`
                   (reason `content`) at MAX_GENERATIONS · a generation that ends WITHOUT a
                   content verdict (Gemini failure, ledger blip, crash, cancellation, an owner
                   that died) → back to "selected", or `rejected` (reason
                   `writer_unavailable`) at MAX_WRITER_FAILURES

Why not a long request: a 20–90 s Gemini call inside the worker's 30 s HTTP timeout was retried
up to three times CONCURRENTLY, and uvicorn's 30 s graceful shutdown cut it on every deploy. A
short poll touches neither. Why a lease and not just the in-process task set: Railway runs the
old and new containers side by side during a deploy, so two processes can see the same row;
only the database can arbitrate that.

Why two caps: a Gemini outage is not a content verdict. Counting it against the content cap
reported a quota exhaustion as `content_rejected` and sent operators hunting a validator hole,
and burned the day's content attempts on calls that never produced a draft. Failures are
counted as `generations - content_rejections`, so an owner that died without writing anything
is counted too (there is no row to write when a container is SIGKILLed); bounded on its own
because a timeout still bills and a crash-looping container must not buy a generation per tick.

Everything here is logged with run_id / generation_id / source_ref, and every failure ends in a
row state the next kick understands — including a generation that dies at a cap, which the next
kick closes (the lease expiry is a real backstop at every count) — nothing is swallowed.
"""

from __future__ import annotations

import asyncio
import logging
import math
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from app.config import settings
from app.schemas.marketing import SCRIPT_REJECT_REASONS
from app.services.marketing import content_pool, selection
from app.services.marketing.run_service import (
    MarketingRunError,
    MarketingRunNotFound,
    MarketingRunNotHeld,
    MarketingRunService,
    get_marketing_run_service,
    held_problem,
    run_date_et,
)

logger = logging.getLogger(__name__)

#: CONTENT generations (draft + one repair each) the validators may reject before the run is
#: `rejected` for the day with reason `content`.
MAX_GENERATIONS = 4
#: Generations that may end WITHOUT a content verdict (Gemini failure, ledger blip, crash,
#: cancellation, an owner that died) before the run is `rejected` with reason
#: `writer_unavailable`. Counted as `generations - content_rejections`. Its own bound, NOT a
#: free pass: a timeout still bills, and a retired model or a crash-looping container would
#: otherwise buy a generation every tick. Together the caps bound a run at
#: MAX_GENERATIONS + MAX_WRITER_FAILURES - 1 generations of at most two model calls each.
MAX_WRITER_FAILURES = 4

#: `GeminiClient.generate_json`'s generic-error budget — its decorator is
#: `@async_retry(max_attempts=2, delay=2.0)`, which is code, not settings, so it is mirrored
#: here; `tests/test_marketing_server_lease.py` reads the decorator from gemini.py and drives the
#: real `async_retry` to prove `worst_case_model_call_seconds` still bounds it.
_GENERATE_JSON_MAX_ATTEMPTS = 2
_GENERATE_JSON_DELAY_SECONDS = 2.0
#: Slack after the slowest possible model call: validation, the terminal write and its retries.
LEASE_MARGIN_SECONDS = 60


def worst_case_model_call_seconds() -> float:
    """The longest ONE `generate_json` call can take under `async_retry`: every budget
    exhausted (overload and quota: GEMINI_QUOTA_MAX_RETRIES retries each; timeout:
    GEMINI_TIMEOUT_MAX_RETRIES; generic: max_attempts-1), every attempt running to the full
    GEMINI_REQUEST_TIMEOUT_SECONDS, plus every linear backoff. The old comment said "~200 s"
    and counted only the timeout and generic budgets; the real figure is 572 s at defaults."""
    per_attempt = float(settings.GEMINI_REQUEST_TIMEOUT_SECONDS)
    quota = max(int(settings.GEMINI_QUOTA_MAX_RETRIES), 0)
    timeouts = max(int(settings.GEMINI_TIMEOUT_MAX_RETRIES), 0)
    step = float(settings.GEMINI_QUOTA_RETRY_DELAY_SECONDS)
    generic = max(_GENERATE_JSON_MAX_ATTEMPTS - 1, 0)
    attempts = 1 + quota + quota + timeouts + generic
    backoff = (
        2 * step * quota * (quota + 1) / 2          # overload + quota: step*1 + … + step*q, each
        + step * timeouts * (timeouts + 1) / 2
        + _GENERATE_JSON_DELAY_SECONDS * generic * (generic + 1) / 2
    )
    return attempts * per_attempt + backoff


#: How long a generation owns the run between refreshes. The lease is refreshed before each
#: model call, so it must outlive the slowest single call: if it lapsed mid-call, a second
#: container (a deploy overlap) could take the run over and pay for a parallel generation. A
#: crashed owner blocks the run for at most this long — well inside the worker's 15-min poll
#: budget (tests/test_marketing_worker.py pins that).
LEASE_SECONDS = int(math.ceil(worst_case_model_call_seconds() + LEASE_MARGIN_SECONDS))
#: How long a generation task in THIS process counts as its run's live owner even after the
#: lease lapsed (`_owner_state`): two model calls, each covered by one lease, and one more
#: lease of slack for the ledger round trips around them. Every await in a generation is bounded
#: (Gemini per attempt, PostgREST per statement, the hand-back), so a task older than this is
#: presumed wedged. What that CHANGES is only at a cap: the day is closed under it (its late
#: write is fenced out) instead of waiting on it. Below the caps there is nothing to take over —
#: the task is still this process's owner and `_spawn` will not start a second one (that would be
#: a parallel paid generation) — so the kick waits for it to end, or for the process to restart.
OWNER_ALIVE_SECONDS = 3 * LEASE_SECONDS
#: Back-off after a generation that ended without a verdict. Longer than one worker poll
#: session, shorter than the hourly cron period, so the next tick retries.
RETRY_AFTER_GEMINI_FAILURE = timedelta(minutes=30)
#: The cancellation hand-back must finish inside the lifespan's 5 s shutdown budget.
HAND_BACK_TIMEOUT_SECONDS = 3.0
#: Linear back-off between attempts of a terminal write (1 s, 2 s) and of a lease refresh.
_FINISH_BACKOFF_SECONDS = 1.0
_REFRESH_BACKOFF_SECONDS = 0.5
#: How many recent picks selection must not repeat.
RECENT_LIMIT = selection.RECENT_WINDOW

# Body states the worker branches on — part of the wire contract with backend/marketing/main.py.
REST_DAY = "rest_day"
GENERATING = "generating"
DEFERRED = "deferred"
ACCEPTED = "accepted"
REJECTED = "rejected"
SELECTED = "selected"  # row state only; never a body state

# `reason` of a rejected body (SCRIPT_REJECT_REASONS; the worker maps it to skip_reason).
REASON_CONTENT = "content"
REASON_WRITER_UNAVAILABLE = "writer_unavailable"
REASON_EMPTY_POOL = "empty_pool"
REASON_SOURCE_INELIGIBLE = "source_ineligible"

# `_finish` outcomes. Only WRITTEN may be logged as the outcome of a generation.
WRITTEN = "written"
SUPERSEDED = "superseded"
LOST = "lost"

# `_owner_state` verdicts.
_OWNER_NONE = "none"
_OWNER_ALIVE = "alive"
_OWNER_WEDGED = "wedged"

#: `writer_service.TOKENS_ATTR` (the pre-agreed contract: a re-raised model exception carries
#: the tokens its generation already spent). Not imported: writer_service loads the agents
#: package, and through it the FMP client, at import time.
_TOKENS_ATTR = "marketing_tokens_used"


class LeaseLost(Exception):
    """Another generation took the run over (our lease expired). Stop without writing."""


class _TerminalWrite:
    """ONE generation's terminal write while `_finish` runs it (never the hand-back): the patch,
    the current attempt's statement future — in flight, or answered — and whether an attempt
    raised. Read by `_generate_as`'s cancel handler, so a shutdown settles THIS write instead of
    racing it with a blind hand-back."""

    __slots__ = ("patch", "what", "fut", "raised")

    def __init__(self, patch: Dict[str, Any], what: str) -> None:
        self.patch = patch
        self.what = what
        self.fut: Optional["asyncio.Future"] = None
        self.raised = False


#: `_await_terminal`: the statement did not answer inside the shutdown budget.
_STILL_IN_FLIGHT = object()


def _unrecorded_level(slot: _TerminalWrite) -> int:
    """A terminal write a shutdown may have left unrecorded: ERROR when it carried an ACCEPTED
    package (paid, compliant — the normal path's "NOT recorded" line is ERROR too), else WARNING."""
    return logging.ERROR if slot.patch.get("status") == ACCEPTED else logging.WARNING


def _unrecorded_note(slot: _TerminalWrite) -> str:
    return (" — an ACCEPTED package: if it was not recorded, a later generation re-bills it"
            if slot.patch.get("status") == ACCEPTED else "")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today_et() -> date:
    return run_date_et(_now())


def _iso(dt: datetime) -> str:
    """UTC, microseconds, `Z` — never `+00:00`: these values also travel as PostgREST FILTER
    values (the takeover CAS fences on `lease_until`), where a `+` decodes to a space and the
    request 503s (the notifications-cursor incident)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _ts_key(value: Any) -> Optional[str]:
    """An observed timestamp, re-rendered for use as an equality FILTER: the same instant
    (Postgres compares timestamptz by value, to the microsecond), in the `Z` form."""
    if value is None:
        return None
    dt = _parse(value)
    return _iso(dt) if dt is not None else str(value)


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _failures(row: Dict[str, Any]) -> int:
    """Generations that ended without a content verdict — including one still in flight on a
    live row, and one whose owner died without writing anything."""
    return max(_int(row.get("generations")) - _int(row.get("content_rejections")), 0)


def _cap_verdict(row: Dict[str, Any]) -> Optional[str]:
    """The reject reason a cap forces on this row, or None while it may still generate."""
    if _int(row.get("content_rejections")) >= MAX_GENERATIONS:
        return REASON_CONTENT
    if _failures(row) >= MAX_WRITER_FAILURES:
        return REASON_WRITER_UNAVAILABLE
    return None


def _spent(exc: BaseException) -> int:
    try:
        return max(int(getattr(exc, _TOKENS_ATTR, 0) or 0), 0)
    except (TypeError, ValueError):
        return 0


def worker_script(output: Dict[str, Any]) -> Dict[str, Any]:
    """The subset the WORKER needs to voice and render (Phases 3-4). Captions are not in it:
    they are server-authored and reach the ledger only through `create_posts`."""
    return {
        "hook": output.get("hook") or "",
        "video_script": list(output.get("video_script") or []),
        "cards": list(output.get("cards") or []),
        "carousel_slides": list(output.get("carousel_slides") or []),
        "disclaimer_card": output.get("disclaimer_card") or "",
        "outlets": sorted((output.get("posts") or {}).keys()),
    }


def _fact_sheet_snapshot(item: content_pool.ContentItem) -> Dict[str, Any]:
    """What the writer was shown. Written at selection, and REWRITTEN with the accepted package
    — each generation grounds against the LIVE bundle (a deploy may have cleaned a sentence
    since), so the audit copy must be the one the accepted output was actually checked against."""
    return {
        "key": item.key, "kind": item.kind, "title": item.title, "category": item.category,
        "sentences": list(item.fact_sentences), "word_count": item.word_count,
    }


class MarketingScriptService:
    def __init__(self, runs: Optional[MarketingRunService] = None, *, writer=None) -> None:
        self._runs = runs
        # Injected in tests; the real one is imported lazily because it loads the agents
        # package (and, through it, the FMP client) — keep that out of module import.
        self._writer = writer
        self._tasks: Set[asyncio.Task] = set()
        self._running: Set[str] = set()
        # run_id → when its task was spawned (`_owner_state`), and generation_id → the last
        # lease_until that generation actually WROTE (`_refresh_lease` judges a failed refresh
        # against it). Both live only as long as the task.
        self._spawned_at: Dict[str, datetime] = {}
        self._leases: Dict[str, datetime] = {}
        # generation_id → its terminal write while `_finish` runs it (`_TerminalWrite`); run_ids
        # whose over-age owner was already reported below the caps (`_owner_state`). Both live
        # only as long as the task.
        self._terminal: Dict[str, _TerminalWrite] = {}
        self._wedge_reported: Set[str] = set()

    @property
    def runs(self) -> MarketingRunService:
        return self._runs or get_marketing_run_service()

    def _generate_fn(self):
        if self._writer is None:
            from app.services.marketing.writer_service import generate_package

            self._writer = generate_package
        return self._writer

    # ── kick ─────────────────────────────────────────────────────────────────

    async def kick(self, run_id: str) -> Dict[str, Any]:
        """Idempotent; safe to call as often as the worker likes. Never raises for a CONTENT
        outcome — only for a ledger failure, or MarketingRunNotHeld when the kick would START
        writer spend (select, or spawn a generation) for a run no live claim holds. A final row
        (rest_day / accepted / rejected) answers idempotently whatever the run's state."""
        run = await self.runs.get_run(run_id)
        if run is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        row = await self.runs.get_script(run_id)
        if row is None:
            self._require_held(run)
            row = await self._select(run)
        await self._heal_mirror(run, row)
        return await self._advance(run_id, row, run=run)

    def _held_problem(self, run: Dict[str, Any]) -> Optional[str]:
        return held_problem(run, now=_now(), today=_today_et(),
                            stale_seconds=int(settings.MARKETING_RUN_STALE_SECONDS))

    def _require_held(self, run: Dict[str, Any]) -> None:
        problem = self._held_problem(run)
        if problem is not None:
            raise MarketingRunNotHeld(
                f"run {run.get('id')} ({run.get('run_date')}) is not held by a live worker claim: "
                f"{problem}; no writer spend started"
            )

    async def _heal_mirror(self, run: Dict[str, Any], row: Dict[str, Any]) -> None:
        """Copy the selection onto `marketing_runs` for the ledger. Informational only —
        `recent` reads marketing_scripts — and self-healing: a lost write (or a lost INSERT
        response whose retry adopted the row) is repaired by the next poll. Only on a HELD run:
        `update_run` bumps `updated_at`, which is the claim's liveness."""
        ref = row.get("source_ref")
        if not ref or run.get("source_ref") == ref:
            return
        if self._held_problem(run) is not None:
            logger.info("marketing script: selection not mirrored onto run %s (not held); the "
                        "script row is the record", run.get("id"))
            return
        if run.get("source_ref"):
            logger.warning("marketing script: run %s mirrors source_ref=%r but its script says %r — "
                           "rewriting the mirror from the script", run.get("id"), run.get("source_ref"), ref)
        try:
            await self.runs.update_run(
                run["id"], source_ref=ref, template_id=row.get("template_id"), content_class="A",
            )
            run["source_ref"] = ref
        except Exception as e:
            logger.warning("marketing script: could not mirror the selection onto run %s "
                           "source_ref=%s (%s: %s) — the next kick retries", run.get("id"), ref,
                           type(e).__name__, e)

    async def _select(self, run: Dict[str, Any]) -> Dict[str, Any]:
        run_id = run["id"]
        run_date = date.fromisoformat(str(run["run_date"])[:10])
        pool = content_pool.eligible_keys()
        recent = await self.runs.recent_source_refs(run_date, RECENT_LIMIT)
        sel = selection.choose(pool, run_date, recent)
        # run_date rides in the same first-write-wins INSERT: it is what `recent` reads.
        base = {"run_id": run_id, "run_date": run_date.isoformat()}
        if sel.rest_day:
            new = {**base, "status": REST_DAY}
        elif sel.source_ref is None:
            # Loud: an empty pool means the bundle is unreadable or everything got excluded.
            logger.error("marketing script: EMPTY content pool run_id=%s run_date=%s", run_id, run_date)
            new = {**base, "status": REJECTED, "reject_reason": REASON_EMPTY_POOL,
                   "last_error": "empty content pool"}
        else:
            item = content_pool.get_item(sel.source_ref)
            new = {
                **base, "status": SELECTED, "source_ref": sel.source_ref,
                "template_id": sel.template_id,
                "fact_sheet": _fact_sheet_snapshot(item) if item else {},
            }
        row, ours = await self.runs.insert_script(new)
        logger.info(
            "marketing script %s run_id=%s run_date=%s status=%s source_ref=%s template=%s "
            "pool=%d recent=%d", "SELECTED" if ours else "selection ADOPTED (a concurrent kick won)",
            run_id, run_date, row.get("status"), row.get("source_ref"), row.get("template_id"),
            len(pool), len(recent),
        )
        return row

    def _rejected_body(self, row: Dict[str, Any]) -> Dict[str, Any]:
        reason = row.get("reject_reason")
        if reason not in SCRIPT_REJECT_REASONS:
            logger.warning("marketing script: rejected row run_id=%s carries reject_reason=%r — "
                           "reported as %r", row.get("run_id"), reason, REASON_CONTENT)
            reason = REASON_CONTENT
        return {
            "status": REJECTED, "source_ref": row.get("source_ref"),
            "template_id": row.get("template_id"), "reason": reason,
            # Only a content verdict has violations to show; an outage's body must not cite
            # the codes of an earlier content round as the day's cause.
            "violations": _codes(row.get("violations")) if reason == REASON_CONTENT else [],
        }

    async def _advance(self, run_id: str, row: Dict[str, Any], *, run: Dict[str, Any],
                       _depth: int = 0) -> Dict[str, Any]:
        status = row.get("status")
        base = {"source_ref": row.get("source_ref"), "template_id": row.get("template_id")}
        if status == REST_DAY:
            return {"status": REST_DAY, **base}
        if status == ACCEPTED and isinstance(row.get("output"), dict):
            return {"status": ACCEPTED, **base, "script": worker_script(row["output"])}
        if status == REJECTED:
            return self._rejected_body(row)

        now = _now()
        verdict = _cap_verdict(row)
        if status == SELECTED:
            if verdict is not None:
                return await self._finalize_rejected(run_id, row, verdict, run=run, _depth=_depth)
            not_before = _parse(row.get("retry_not_before"))
            if not_before and not_before > now:
                return {"status": DEFERRED, **base,
                        "retry_after_seconds": int((not_before - now).total_seconds()) + 1}
            self._require_held(run)
            self._spawn(run_id)
            return {"status": GENERATING, **base}
        if status == GENERATING:
            lease = _parse(row.get("lease_until"))
            if lease is not None and lease > now:
                return {"status": GENERATING, **base}
            owner = self._owner_state(run_id, now)
            if owner == _OWNER_ALIVE:
                # The lease lapsed (a refresh that errored, or a slow round trip) but the owner is
                # a live task in THIS process: not dead, whatever the cap says. Closing the day here
                # fenced out the paid package it was about to write; spawning is a no-op anyway.
                logger.info("marketing script: lease of run %s generation %s lapsed at %s but its "
                            "owner is alive in this process — leaving it be", run_id,
                            row.get("generation_id"), row.get("lease_until"))
                return {"status": GENERATING, **base}
            if verdict is not None:
                if owner == _OWNER_WEDGED:
                    # The one place a wedged owner changes anything, so the one place that says so.
                    logger.warning("marketing script: the generation task for run %s has run since "
                                   "%s, past OWNER_ALIVE_SECONDS (%ds), at the cap — treating it as "
                                   "wedged: closing the day under it (its late write is fenced out)",
                                   run_id, self._spawned_at.get(run_id), OWNER_ALIVE_SECONDS)
                # The owner of the last allowed generation died without a terminal write. The
                # takeover below would be refused at the cap, so nothing else would ever close it.
                return await self._finalize_rejected(
                    run_id, row, verdict, run=run, _depth=_depth,
                    last_error=(f"generation {row.get('generation_id')} (#{row.get('generations')}) "
                                "lost its lease without a terminal write"),
                )
            if owner == _OWNER_WEDGED:
                # Below the caps an over-age task in THIS process is still the owner: `_spawn`
                # would be a no-op (it is in `_running`), and cancelling it would need the fenced
                # hand-back ordering. Nothing is taken over; said once per task, not per poll.
                self._report_wedged_below_cap(run_id)
                return {"status": GENERATING, **base}
            self._require_held(run)
            self._spawn(run_id)  # the owner died; the task's CAS takes the expired lease
            return {"status": GENERATING, **base}
        logger.error("marketing script: unknown status %r on run %s", status, run_id)
        return {"status": DEFERRED, **base, "retry_after_seconds": int(RETRY_AFTER_GEMINI_FAILURE.total_seconds())}

    async def _finalize_rejected(
        self, run_id: str, row: Dict[str, Any], reason: str, *, run: Optional[Dict[str, Any]] = None,
        last_error: Optional[str] = None, readvance: bool = True, _depth: int = 0,
    ) -> Dict[str, Any]:
        """Close the day `rejected` — a CAS on exactly the row we judged: (status, generations,
        generation_id, lease_until). A live owner that refreshed its lease, or landed its own
        terminal write, in the meantime makes this miss; then the row is re-read and advanced
        (once) instead of answering `rejected` for a day whose script may be `accepted`."""
        patch: Dict[str, Any] = {"status": REJECTED, "reject_reason": reason, "lease_until": None}
        if last_error:
            patch["last_error"] = last_error[:2000]
        done = await self.runs.update_script_where(
            run_id, patch,
            expect={"status": row.get("status"), "generations": _int(row.get("generations")),
                    "generation_id": row.get("generation_id"),
                    "lease_until": _ts_key(row.get("lease_until"))},
        )
        if done is None:
            fresh = await self.runs.get_script(run_id)
            if fresh is None:
                raise MarketingRunError(f"finalize: the script row of run {run_id} vanished")
            if fresh.get("status") == REJECTED:
                return self._rejected_body(fresh)
            logger.info("marketing script: finalize of run %s lost to a concurrent write (now %s, "
                        "generation %s) — re-reading", run_id, fresh.get("status"), fresh.get("generation_id"))
            if readvance and run is not None and _depth == 0:
                return await self._advance(run_id, fresh, run=run, _depth=1)
            return {"status": GENERATING, "source_ref": fresh.get("source_ref"),
                    "template_id": fresh.get("template_id")}
        logger.warning(
            "marketing script REJECTED for the day run_id=%s source_ref=%s reason=%s generations=%s "
            "content_rejections=%s violations=%s last_error=%s", run_id, done.get("source_ref"), reason,
            done.get("generations"), done.get("content_rejections"),
            _codes(done.get("violations")) if reason == REASON_CONTENT else [],
            (done.get("last_error") or "")[:300],
        )
        return self._rejected_body(done)

    # ── generation ───────────────────────────────────────────────────────────

    def _owner_state(self, run_id: str, now: datetime) -> str:
        """Is a generation task for `run_id` running in THIS process — young enough to be a live
        owner (`_OWNER_ALIVE`), or past OWNER_ALIVE_SECONDS (`_OWNER_WEDGED`)? `_OWNER_NONE` when
        no task here holds it. Silent: the caller logs on the path that acts on the verdict."""
        if run_id not in self._running:
            return _OWNER_NONE
        started = self._spawned_at.get(run_id)
        if started is not None and (now - started).total_seconds() < OWNER_ALIVE_SECONDS:
            return _OWNER_ALIVE
        return _OWNER_WEDGED

    def _report_wedged_below_cap(self, run_id: str) -> None:
        """ONE WARNING per task (cleared when it ends), not one per 15-s worker poll: an old task
        below the caps is presumed wedged, but nothing is taken over and the kick keeps answering
        `generating` until it ends or the process restarts."""
        if run_id in self._wedge_reported:
            return
        self._wedge_reported.add(run_id)
        logger.warning("marketing script: the generation task for run %s has run since %s, past "
                       "OWNER_ALIVE_SECONDS (%ds), below the caps — nothing to take over in this "
                       "process (it still owns the run; a second task would be a parallel paid "
                       "generation), so the day waits for it to end or for a restart",
                       run_id, self._spawned_at.get(run_id), OWNER_ALIVE_SECONDS)

    def _spawn(self, run_id: str) -> None:
        if run_id in self._running:
            return
        self._running.add(run_id)
        self._spawned_at[run_id] = _now()
        task = asyncio.create_task(self._generate(run_id), name=f"marketing_script:{run_id}")
        self._tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            self._running.discard(run_id)
            self._spawned_at.pop(run_id, None)
            self._wedge_reported.discard(run_id)
            if not t.cancelled() and t.exception() is not None:
                exc = t.exception()
                logger.error("marketing script task crashed run_id=%s: %s: %s", run_id,
                             type(exc).__name__, exc, exc_info=exc)

        task.add_done_callback(_done)

    async def _acquire(self, run_id: str, gen_id: str) -> Optional[Dict[str, Any]]:
        """Take the run for ONE generation (as `gen_id`), or return None if it is not ours to
        take. A row that reached a cap since the kick read it is closed here too."""
        row = await self.runs.get_script(run_id)
        if row is None:
            return None
        now = _now()
        status = row.get("status")
        if status == GENERATING:
            lease = _parse(row.get("lease_until"))
            if lease is not None and lease > now:
                return None
        elif status != SELECTED:
            return None
        verdict = _cap_verdict(row)
        if verdict is not None:
            await self._finalize_rejected(
                run_id, row, verdict, readvance=False,
                last_error=(f"generation {row.get('generation_id')} (#{row.get('generations')}) lost "
                            "its lease without a terminal write") if status == GENERATING else None,
            )
            return None
        if status == SELECTED:
            not_before = _parse(row.get("retry_not_before"))
            if not_before and not_before > now:
                return None
        generations = _int(row.get("generations"))
        until = now + timedelta(seconds=LEASE_SECONDS)
        # The statement runs in a worker thread (`sb_exec`), and cancelling this coroutine does
        # NOT stop it: an UPDATE still in flight can commit after a cancel. So the write is a task
        # of its own and a cancel waits for it to answer before handing the run back — a
        # hand-back sent while it is in flight can overtake it, match nothing, and leave a
        # 632-s lease with nobody behind it.
        write = asyncio.ensure_future(self.runs.update_script_where(
            run_id,
            {"status": GENERATING, "generation_id": gen_id, "generations": generations + 1,
             "lease_until": _iso(until), "retry_not_before": None},
            # lease_until too: an owner whose refresh landed between our read and this write
            # is alive, and must not be taken over on the strength of a stale read.
            expect={"status": status, "generations": generations,
                    "generation_id": row.get("generation_id"),
                    "lease_until": _ts_key(row.get("lease_until"))},
        ))
        try:
            taken = await asyncio.shield(write)
        except asyncio.CancelledError:
            await self._settle_cancelled_acquire(run_id, gen_id, write)
            raise
        if taken is not None:
            self._leases[gen_id] = until
        return taken

    async def _settle_cancelled_acquire(self, run_id: str, gen_id: str, write: "asyncio.Future") -> None:
        """A cancel (shutdown) arrived while the acquire UPDATE was in flight. Wait for the
        statement to ANSWER, then hand back only if it took the run — ordered after it, so the
        fenced hand-back can no longer run first and match nothing. The wait and the hand-back
        share HAND_BACK_TIMEOUT_SECONDS (the lifespan gives shutdown 5 s in all). If the statement
        does not answer in time the lease is the backstop: a hand-back now could overtake it."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HAND_BACK_TIMEOUT_SECONDS
        why = "cancelled while acquiring the lease"
        try:
            taken = await asyncio.wait_for(asyncio.shield(write), HAND_BACK_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            logger.warning("marketing script: acquire of run %s by generation %s still in flight %.1fs "
                           "after the cancel — not handing back (a hand-back could overtake it); the "
                           "lease (%ds) is the backstop", run_id, gen_id, HAND_BACK_TIMEOUT_SECONDS,
                           LEASE_SECONDS)

            def _late(f: "asyncio.Future") -> None:
                exc = None if f.cancelled() else f.exception()  # retrieved: never "never retrieved"
                if exc is None and not f.cancelled() and f.result() is not None:
                    logger.warning("marketing script: the orphaned acquire of run %s by generation %s "
                                   "landed after the cancel — nobody owns that lease; it expires in "
                                   "%ds and the next kick takes the run", run_id, gen_id, LEASE_SECONDS)

            write.add_done_callback(_late)
            return
        except asyncio.CancelledError:
            logger.warning("marketing script: waiting for the in-flight acquire of run %s (generation "
                           "%s) was INTERRUPTED — the lease (%ds) is the backstop", run_id, gen_id,
                           LEASE_SECONDS)
            raise
        except Exception as e:
            # The statement answered with an error, so a hand-back is ordered after it; it may
            # still have committed (a response lost after commit), and the hand-back is fenced.
            logger.warning("marketing script: the acquire of run %s by generation %s failed during "
                           "a cancel (%s: %s) — handing back in case it landed", run_id, gen_id,
                           type(e).__name__, e)
            taken = e
        if taken is None:
            logger.info("marketing script: the cancelled acquire of run %s by generation %s took "
                        "nothing; nothing to hand back", run_id, gen_id)
            return
        await self._hand_back(run_id, gen_id, why, timeout=max(deadline - loop.time(), 0.0))

    def _lease_left(self, gen_id: str) -> Optional[float]:
        until = self._leases.get(gen_id)
        return None if until is None else (until - _now()).total_seconds()

    async def _refresh_lease(self, run_id: str, gen_id: str) -> bool:
        """Before every model call. A CAS miss = the run was taken over → LeaseLost (the
        generation stops without spending). A ledger ERROR is not a lost lease: retried a
        little, then judged against the lease this generation last WROTE — every terminal write
        is fenced on generation_id, so a result that did lose the run still writes nothing, and
        aborting would throw away a paid, publishable draft.

        Returns True when the lease on record still covers one worst-case model call, False when
        it does not (every attempt errored, or landed too late): starting that call could outlive
        the lease, and a second container could take the run and pay for a parallel generation.
        `before_call`'s contract for False: a writer that already holds a publishable candidate
        skips the call and keeps it (as it does when a repair call fails); with nothing to keep,
        the call is its only way to a package, and it may still make it — the terminal write is
        fenced either way. False never throws a draft away; a writer that ignores it simply
        makes the call, as before."""
        need = worst_case_model_call_seconds()
        last: Optional[BaseException] = None
        for attempt in range(3):
            until = _now() + timedelta(seconds=LEASE_SECONDS)
            try:
                ok = await self.runs.update_script_where(
                    run_id, {"lease_until": _iso(until)},
                    expect={"status": GENERATING, "generation_id": gen_id},
                )
            except Exception as e:
                last = e
                logger.warning("marketing script: lease refresh failed run_id=%s generation=%s "
                               "attempt=%d (%s: %s)", run_id, gen_id, attempt + 1, type(e).__name__, e)
                if attempt < 2:
                    await asyncio.sleep(_REFRESH_BACKOFF_SECONDS * (attempt + 1))
                continue
            if ok is None:
                raise LeaseLost(f"run {run_id} generation {gen_id} lost its lease")
            self._leases[gen_id] = until
            left = self._lease_left(gen_id)
            if left is not None and left >= need:
                return True
            # Written, but the round trip ate the margin: the lease starts before the call does.
            last = TimeoutError(f"the refresh landed with only {left:.0f}s of lease left")
            logger.warning("marketing script: lease refresh landed late run_id=%s generation=%s "
                           "attempt=%d (%.0fs left < %.0fs for one model call)", run_id, gen_id,
                           attempt + 1, left or 0.0, need)
        left = self._lease_left(gen_id)
        if left is None or left >= need:
            logger.warning("marketing script: lease NOT refreshed run_id=%s generation=%s (%s: %s) — "
                           "continuing: the lease on record (%s) still covers one model call; the "
                           "terminal write is fenced on the generation id", run_id, gen_id,
                           type(last).__name__, last,
                           "unknown" if left is None else f"{left:.0f}s left")
            return True
        logger.warning("marketing script: lease NOT refreshed run_id=%s generation=%s (%s: %s) and "
                       "only %.0fs of it is left, less than one worst-case model call (%.0fs) — "
                       "asking the writer to skip this call", run_id, gen_id, type(last).__name__,
                       last, left, need)
        return False

    async def _finish(self, run_id: str, gen_id: str, patch: Dict[str, Any], *,
                      what: str = "terminal write") -> str:
        """Write fenced on our generation id; WRITTEN / SUPERSEDED / LOST. Retried a little:
        losing this write after a paid Gemini call means paying again once the lease expires.
        A retry that matches nothing after an attempt RAISED may be our own write whose response
        was lost — re-read before calling it superseded.

        A TERMINAL write (anything but the hand-back) is registered in `_terminal` while it runs,
        and each attempt's statement is a future of its own awaited through a shield: `sb_exec`
        runs it in a worker thread a cancel cannot stop, and a shutdown hand-back sent while it
        was in flight could overtake it and discard a paid package or a content verdict. On a
        cancel the record is LEFT for `_generate_as`, which settles that statement first
        (`_settle_cancelled_terminal`). The hand-back is never registered — it would wait on
        itself."""
        if what == "hand-back":
            return await self._finish_attempts(run_id, gen_id, patch, what=what, slot=None)
        slot = self._terminal[gen_id] = _TerminalWrite(patch, what)
        try:
            outcome = await self._finish_attempts(run_id, gen_id, patch, what=what, slot=slot)
        except asyncio.CancelledError:
            raise  # the record stays: `_generate_as`'s cancel handler settles it
        except BaseException:
            self._forget_terminal(gen_id, slot)
            raise
        self._forget_terminal(gen_id, slot)
        return outcome

    def _forget_terminal(self, gen_id: str, slot: _TerminalWrite) -> None:
        if self._terminal.get(gen_id) is slot:
            del self._terminal[gen_id]

    async def _finish_attempts(self, run_id: str, gen_id: str, patch: Dict[str, Any], *,
                               what: str, slot: Optional[_TerminalWrite]) -> str:
        raised = False
        for attempt in range(3):
            try:
                if slot is None:
                    done = await self.runs.update_script_where(
                        run_id, {**patch, "lease_until": None},
                        expect={"status": GENERATING, "generation_id": gen_id},
                    )
                else:
                    slot.fut = asyncio.ensure_future(self.runs.update_script_where(
                        run_id, {**patch, "lease_until": None},
                        expect={"status": GENERATING, "generation_id": gen_id},
                    ))
                    done = await asyncio.shield(slot.fut)
            except Exception as e:
                raised = True
                if slot is not None:
                    slot.raised = True
                logger.warning("marketing script: %s failed run_id=%s generation=%s attempt=%d "
                               "(%s: %s)", what, run_id, gen_id, attempt + 1, type(e).__name__, e)
                if attempt < 2:
                    await asyncio.sleep(_FINISH_BACKOFF_SECONDS * (attempt + 1))
                continue
            if done is not None:
                return WRITTEN
            if raised and await self._landed(run_id, gen_id, patch):
                logger.info("marketing script: %s landed (an earlier response was lost) run_id=%s "
                            "generation=%s status=%s", what, run_id, gen_id, patch.get("status"))
                return WRITTEN
            if what == "hand-back":
                # Fenced on our id, so this is the normal answer for a run we never took (or had
                # already written): not evidence that anyone else holds it.
                logger.info("marketing script: hand-back of generation %s on run %s matched nothing "
                            "(it does not hold the run: never taken, already written, or taken "
                            "over); nothing written", gen_id, run_id)
            else:
                logger.warning(
                    "marketing script: %s of generation %s on run %s matched nothing (another "
                    "generation holds the run, or the row is final); nothing written", what, gen_id,
                    run_id,
                )
            return SUPERSEDED
        logger.error("marketing script: %s LOST run_id=%s generation=%s status=%s — the lease "
                     "(%ds) expires and the next kick decides", what, run_id, gen_id,
                     patch.get("status"), LEASE_SECONDS)
        return LOST

    #: What `_landed` compares between our patch and the re-read row: every scalar a terminal
    #: write carries whose value survives the round trip exactly (text / integer columns). A row
    #: finalized under our id, or rewritten by another generation between our attempts, differs in
    #: at least one of them; the JSONB and timestamp columns are not compared (their
    #: representation changes on the way back, which would call a landed write lost).
    _LANDED_FIELDS = ("status", "last_error", "reject_reason", "content_rejections", "tokens_used")

    async def _landed(self, run_id: str, gen_id: str, patch: Dict[str, Any]) -> bool:
        """After an attempt RAISED and a retry matched nothing: is the row exactly what OUR
        write would have made it — our generation_id and every `_LANDED_FIELDS` value the patch
        sets? Anything else (another generation's write, a finalize under our id) means our
        package was NOT recorded."""
        try:
            cur = await self.runs.get_script(run_id)
        except Exception as e:
            logger.warning("marketing script: re-read after a failed write failed run_id=%s "
                           "generation=%s (%s: %s)", run_id, gen_id, type(e).__name__, e)
            return False
        if not cur or str(cur.get("generation_id")) != str(gen_id):
            return False
        return all(cur.get(f) == patch.get(f) for f in self._LANDED_FIELDS if f in patch)

    async def _hand_back(self, run_id: str, gen_id: str, why: str, *,
                         timeout: Optional[float] = None) -> None:
        """Give the run back (`selected`) so the next container takes it at once instead of
        waiting out the lease. Fenced on our id, so it is a no-op if we never held it. Never
        swallows a cancellation; never silent. `timeout` defaults to HAND_BACK_TIMEOUT_SECONDS,
        read at CALL time (a default argument would freeze it at import)."""
        if timeout is None:
            timeout = HAND_BACK_TIMEOUT_SECONDS
        if timeout <= 0:
            logger.warning("marketing script: no time left to hand back run_id=%s generation=%s (%s) "
                           "— the lease (%ds) is the backstop", run_id, gen_id, why, LEASE_SECONDS)
            return
        try:
            outcome = await asyncio.wait_for(
                self._finish(run_id, gen_id, {"status": SELECTED, "last_error": why[:2000]},
                             what="hand-back"),
                timeout,
            )
        except asyncio.CancelledError:
            logger.warning("marketing script: hand-back INTERRUPTED run_id=%s generation=%s (%s) — "
                           "the lease (%ds) is the backstop", run_id, gen_id, why, LEASE_SECONDS)
            raise
        except Exception as e:  # incl. the wait_for timeout
            logger.warning("marketing script: hand-back FAILED run_id=%s generation=%s (%s) %s: %s — "
                           "the lease (%ds) is the backstop", run_id, gen_id, why, type(e).__name__, e,
                           LEASE_SECONDS)
            return
        if outcome == WRITTEN:
            logger.info("marketing script: run %s handed back by generation %s (%s)", run_id, gen_id, why)

    async def _generate(self, run_id: str) -> None:
        gen_id = str(uuid.uuid4())
        try:
            await self._generate_as(run_id, gen_id)
        finally:
            self._leases.pop(gen_id, None)
            self._terminal.pop(gen_id, None)

    async def _generate_as(self, run_id: str, gen_id: str) -> None:
        try:
            row = await self._acquire(run_id, gen_id)
        except asyncio.CancelledError:
            # `_acquire` already settled its own in-flight UPDATE (and handed back if it took the
            # run); a cancel during its reads or its at-cap finalize needs nothing undone.
            logger.info("marketing script: generation %s of run %s cancelled while acquiring",
                        gen_id, run_id)
            raise
        except Exception as e:
            logger.warning("marketing script: acquire FAILED run_id=%s generation=%s (%s: %s) — "
                           "the next kick retries", run_id, gen_id, type(e).__name__, e)
            # The statement ANSWERED (with an error), so this fenced hand-back is ordered after
            # it — and it may have committed before its response was lost.
            await self._hand_back(run_id, gen_id, f"acquire failed: {type(e).__name__}: {e}")
            return
        if row is None:
            return
        try:
            await self._generate_owned(run_id, row, gen_id)
        except asyncio.CancelledError:
            terminal = self._terminal.pop(gen_id, None)
            if terminal is not None:
                # The cancel caught a terminal write (in flight, or between its attempts): settle
                # THAT write first. A hand-back sent now could overtake it — both are fenced on
                # our id — and throw away a paid package or a content verdict with no log.
                await self._settle_cancelled_terminal(run_id, gen_id, terminal)
            else:
                # Shutdown anywhere else after the lease was taken: hand the run back so the next
                # container can take it at once instead of waiting LEASE_SECONDS for it to expire.
                await self._hand_back(run_id, gen_id, "generation cancelled (shutdown)")
            raise
        except Exception as e:
            logger.error("marketing script: generation CRASHED run_id=%s generation=%s (%s: %s) — "
                         "handing the run back", run_id, gen_id, type(e).__name__, e, exc_info=True)
            await self._finish(run_id, gen_id, {
                "status": SELECTED,
                "retry_not_before": _iso(_now() + RETRY_AFTER_GEMINI_FAILURE),
                "last_error": f"crashed: {type(e).__name__}: {e}"[:2000],
            })

    async def _settle_cancelled_terminal(self, run_id: str, gen_id: str,
                                         slot: _TerminalWrite) -> None:
        """A cancel (shutdown) arrived during a terminal write. Everything here shares ONE
        HAND_BACK_TIMEOUT_SECONDS budget (the lifespan gives shutdown 5 s in all):

        * the statement is waited for, never raced: it LANDED → recorded, nothing to hand back;
          it matched nothing → the row is not ours, nothing to hand back; it did not answer in
          time → nothing is sent (a hand-back could overtake it), the lease is the backstop and
          a late landing is logged;
        * it answered with an ERROR (in flight, or the cancel caught `_finish` between attempts)
          → the SAME fenced patch is re-sent once, ordered after it: it keeps the package / the
          verdict / the back-off that a hand-back would discard. Only if that fails too is the run
          handed back, in whatever budget is left."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + HAND_BACK_TIMEOUT_SECONDS
        if slot.fut is not None:
            answer = await self._await_terminal(run_id, gen_id, slot, slot.fut, deadline)
            if answer is _STILL_IN_FLIGHT:
                return
            if not isinstance(answer, BaseException):
                self._log_settled_terminal(run_id, gen_id, slot, answer, "during the shutdown")
                return
        left = deadline - loop.time()
        if left <= 0:
            logger.log(_unrecorded_level(slot),
                       "marketing script: no time left to re-send the %s (status=%s) of run %s by "
                       "generation %s after the cancel — the lease (%ds) is the backstop%s",
                       slot.what, slot.patch.get("status"), run_id, gen_id, LEASE_SECONDS,
                       _unrecorded_note(slot))
            return
        logger.warning("marketing script: the %s (status=%s) of run %s by generation %s answered "
                       "with an error before the cancel settled it — re-sending it once inside "
                       "the shutdown budget", slot.what, slot.patch.get("status"), run_id, gen_id)
        resend = asyncio.ensure_future(self.runs.update_script_where(
            run_id, {**slot.patch, "lease_until": None},
            expect={"status": GENERATING, "generation_id": gen_id},
        ))
        answer = await self._await_terminal(run_id, gen_id, slot, resend, deadline)
        if answer is _STILL_IN_FLIGHT:
            return
        if not isinstance(answer, BaseException):
            self._log_settled_terminal(run_id, gen_id, slot, answer, "on its shutdown re-send",
                                       maybe_ours=True)
            return
        # Both answered with an error, so a fenced hand-back is ordered after them.
        logger.log(_unrecorded_level(slot),
                   "marketing script: the %s (status=%s) of run %s by generation %s failed again on "
                   "its shutdown re-send — handing the run back instead%s", slot.what,
                   slot.patch.get("status"), run_id, gen_id, _unrecorded_note(slot))
        await self._hand_back(run_id, gen_id, "generation cancelled (shutdown)",
                              timeout=max(deadline - loop.time(), 0.0))

    async def _await_terminal(self, run_id: str, gen_id: str, slot: _TerminalWrite,
                              fut: "asyncio.Future", deadline: float) -> Any:
        """The statement's answer (a row, None, or the exception it raised), or
        `_STILL_IN_FLIGHT` when it did not answer by `deadline` — then a done-callback logs
        where it landed, and the caller must send nothing that could overtake it."""
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(asyncio.shield(fut), max(deadline - loop.time(), 0.0))
        except asyncio.TimeoutError:
            logger.warning("marketing script: the %s (status=%s) of run %s by generation %s is still "
                           "in flight after the %.1fs shutdown budget — not handing back (a "
                           "hand-back could overtake it); the lease (%ds) is the backstop",
                           slot.what, slot.patch.get("status"), run_id, gen_id,
                           HAND_BACK_TIMEOUT_SECONDS, LEASE_SECONDS)
            fut.add_done_callback(lambda f: self._log_late_terminal(run_id, gen_id, slot, f))
            return _STILL_IN_FLIGHT
        except asyncio.CancelledError:
            logger.warning("marketing script: settling the %s (status=%s) of run %s by generation %s "
                           "was INTERRUPTED — the lease (%ds) is the backstop", slot.what,
                           slot.patch.get("status"), run_id, gen_id, LEASE_SECONDS)
            raise
        except Exception as e:
            logger.warning("marketing script: the %s (status=%s) of run %s by generation %s failed "
                           "(%s: %s)", slot.what, slot.patch.get("status"), run_id, gen_id,
                           type(e).__name__, e)
            return e

    def _log_settled_terminal(self, run_id: str, gen_id: str, slot: _TerminalWrite, answer: Any,
                              when: str, *, maybe_ours: bool = False) -> None:
        status = slot.patch.get("status")
        if answer is not None:
            logger.info("marketing script: the %s of run %s by generation %s LANDED %s (status=%s) "
                        "— recorded; nothing to hand back", slot.what, run_id, gen_id, when, status)
            return
        logger.log(_unrecorded_level(slot),
                   "marketing script: the %s (status=%s) of run %s by generation %s matched nothing "
                   "%s — %s; nothing to hand back%s", slot.what, status, run_id, gen_id, when,
                   "an earlier attempt may have landed before its response was lost, or the run "
                   "moved on" if (maybe_ours or slot.raised) else
                   "another generation holds the run, or the row is final",
                   _unrecorded_note(slot))

    def _log_late_terminal(self, run_id: str, gen_id: str, slot: _TerminalWrite,
                           f: "asyncio.Future") -> None:
        """Done-callback for a terminal statement that outlived the shutdown budget."""
        if f.cancelled():
            return
        exc = f.exception()  # retrieved: never "exception was never retrieved"
        status = slot.patch.get("status")
        if exc is not None:
            logger.warning("marketing script: the orphaned %s (status=%s) of run %s by generation %s "
                           "failed after the shutdown wait (%s: %s) — the lease (%ds) is the "
                           "backstop", slot.what, status, run_id, gen_id, type(exc).__name__, exc,
                           LEASE_SECONDS)
        elif f.result() is not None:
            logger.info("marketing script: the orphaned %s of run %s by generation %s landed after "
                        "the shutdown wait (status=%s) — recorded", slot.what, run_id, gen_id, status)
        else:
            logger.log(_unrecorded_level(slot),
                       "marketing script: the orphaned %s (status=%s) of run %s by generation %s "
                       "matched nothing after the shutdown wait%s", slot.what, status, run_id,
                       gen_id, _unrecorded_note(slot))

    async def _run_date_of(self, run_id: str, row: Dict[str, Any]) -> date:
        raw = row.get("run_date")
        if raw:
            return date.fromisoformat(str(raw)[:10])
        run = await self.runs.get_run(run_id)
        if run is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        return date.fromisoformat(str(run["run_date"])[:10])

    async def _generate_owned(self, run_id: str, row: Dict[str, Any], gen_id: str) -> None:
        source_ref = row.get("source_ref")
        try:
            run_date = await self._run_date_of(run_id, row)
        except Exception as e:
            logger.warning("marketing script: ledger FAILED before the model call run_id=%s "
                           "generation=%s source=%s (%s: %s) — handing the run back", run_id, gen_id,
                           source_ref, type(e).__name__, e)
            await self._finish(run_id, gen_id, {
                "status": SELECTED,
                "retry_not_before": _iso(_now() + RETRY_AFTER_GEMINI_FAILURE),
                "last_error": f"ledger: {type(e).__name__}: {e}"[:2000],
            })
            return
        item = content_pool.get_item(source_ref) if source_ref else None
        template = selection.TEMPLATES_BY_ID.get(row.get("template_id") or "")
        if item is None or template is None or not item.eligible:
            # The bundle changed under a selected run (item removed/excluded by a deploy).
            outcome = await self._finish(run_id, gen_id, {
                "status": REJECTED, "reject_reason": REASON_SOURCE_INELIGIBLE,
                "last_error": f"source {source_ref!r} is no longer eligible",
            })
            logger.error("marketing script: selected source no longer eligible run_id=%s "
                         "generation=%s source=%s template=%s — rejected for the day (%s)",
                         run_id, gen_id, source_ref, row.get("template_id"), outcome)
            return
        generate = self._generate_fn()
        started = _now()
        logger.info("marketing script GENERATING run_id=%s generation=%s (#%d; content %d/%d, "
                    "failures %d/%d) source=%s template=%s", run_id, gen_id, _int(row.get("generations")),
                    _int(row.get("content_rejections")), MAX_GENERATIONS, _failures(row) - 1,
                    MAX_WRITER_FAILURES, source_ref, template.id)
        try:
            result = await generate(
                item, template, run_date, generation_id=gen_id,
                allow_x_url=bool(settings.MARKETING_X_ALLOW_URLS),
                before_call=lambda: self._refresh_lease(run_id, gen_id),
            )
        except LeaseLost as e:
            logger.warning("marketing script: %s — stopping without a write (tokens spent and not "
                           "recorded: %d)", e, _spent(e))
            return
        except Exception as e:
            await self._record_failure(run_id, row, gen_id, e)
            return

        elapsed = (_now() - started).total_seconds()
        prior_tokens = _int(row.get("tokens_used"))
        if result.status == ACCEPTED and result.package:
            sheet = _fact_sheet_snapshot(item)
            frozen = row.get("fact_sheet") if isinstance(row.get("fact_sheet"), dict) else {}
            if frozen.get("sentences") is not None and frozen.get("sentences") != sheet["sentences"]:
                logger.warning("marketing script: the fact sheet of %s changed since selection "
                               "run_id=%s generation=%s — recording the one the package was grounded on",
                               source_ref, run_id, gen_id)
            outcome = await self._finish(run_id, gen_id, {
                "status": ACCEPTED, "output": result.package, "violations": result.violations,
                "model": result.model, "prompt_version": result.prompt_version,
                "tokens_used": prior_tokens + _int(result.tokens_used),
                "fact_sheet": sheet, "last_error": None,
            })
            if outcome == WRITTEN:
                logger.info("marketing script ACCEPTED run_id=%s generation=%s source=%s outlets=%s "
                            "dropped=%s tokens=%d in %.1fs", run_id, gen_id, source_ref,
                            sorted((result.package.get("posts") or {}).keys()),
                            sorted((result.package.get("dropped_outlets") or {}).keys()),
                            _int(result.tokens_used), elapsed)
            else:
                logger.error("marketing script: an ACCEPTED package was NOT recorded run_id=%s "
                             "generation=%s source=%s (%s) tokens=%d — a later generation re-bills it",
                             run_id, gen_id, source_ref, outcome, _int(result.tokens_used))
            return
        content_after = _int(row.get("content_rejections")) + 1
        final = content_after >= MAX_GENERATIONS
        patch: Dict[str, Any] = {
            "status": REJECTED if final else SELECTED,
            "violations": result.violations,
            "content_rejections": content_after,
            "tokens_used": prior_tokens + _int(result.tokens_used),
            "last_error": "content rejected: " + ", ".join(_codes(result.violations))[:1900],
        }
        if final:
            patch["reject_reason"] = REASON_CONTENT
        outcome = await self._finish(run_id, gen_id, patch)
        if outcome == WRITTEN:
            logger.warning("marketing script generation REJECTED run_id=%s generation=%s content=%d/%d "
                           "final=%s codes=%s", run_id, gen_id, content_after, MAX_GENERATIONS, final,
                           _codes(result.violations))
        else:
            logger.error("marketing script: a content rejection was NOT recorded run_id=%s "
                         "generation=%s (%s) codes=%s", run_id, gen_id, outcome, _codes(result.violations))

    async def _record_failure(self, run_id: str, row: Dict[str, Any], gen_id: str, e: Exception) -> None:
        """A generation that ended without a content verdict. Counts against MAX_WRITER_FAILURES
        (never the content cap) and records the tokens it already spent (contract (2))."""
        spent = _spent(e)
        if isinstance(e, MarketingRunError):
            logger.warning("marketing script: ledger FAILED during generation run_id=%s generation=%s "
                           "(%s: %s) tokens=%d", run_id, gen_id, type(e).__name__, e, spent)
        else:
            from app.integrations.gemini import is_transient_gemini_error

            transient = is_transient_gemini_error(e)
            (logger.warning if transient else logger.error)(
                "marketing script: writer FAILED run_id=%s generation=%s transient=%s tokens=%d (%s: %s)",
                run_id, gen_id, transient, spent, type(e).__name__, e, exc_info=not transient,
            )
        failures = _failures(row)  # this generation included
        final = failures >= MAX_WRITER_FAILURES
        patch: Dict[str, Any] = {
            "status": REJECTED if final else SELECTED,
            "tokens_used": _int(row.get("tokens_used")) + spent,
            "last_error": f"{type(e).__name__}: {e}"[:2000],
        }
        if final:
            patch["reject_reason"] = REASON_WRITER_UNAVAILABLE
        else:
            patch["retry_not_before"] = _iso(_now() + RETRY_AFTER_GEMINI_FAILURE)
        outcome = await self._finish(run_id, gen_id, patch)
        if outcome != WRITTEN:
            logger.error("marketing script: a writer failure was NOT recorded run_id=%s generation=%s "
                         "(%s) tokens=%d — the lease expires and the next kick counts it",
                         run_id, gen_id, outcome, spent)
        elif final:
            logger.warning("marketing script REJECTED for the day run_id=%s reason=%s failures=%d "
                           "last_error=%s", run_id, REASON_WRITER_UNAVAILABLE, failures,
                           f"{type(e).__name__}: {e}"[:300])

    async def shutdown(self, timeout: float = 5.0) -> None:
        """Lifespan teardown: cancel in-flight generations (each hands its run back)."""
        tasks = list(self._tasks)
        for t in tasks:
            t.cancel()
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=timeout)
            for t in pending:
                logger.warning("marketing script: %s still running after the %.1fs shutdown wait — "
                               "its lease (%ds) is the backstop", t.get_name(), timeout, LEASE_SECONDS)


def _codes(violations: Any) -> List[str]:
    out = []
    for v in violations or []:
        code = v.get("code") if isinstance(v, dict) else None
        if code and code not in out:
            out.append(code)
    return out


_service: Optional[MarketingScriptService] = None


def get_marketing_script_service() -> MarketingScriptService:
    global _service
    if _service is None:
        _service = MarketingScriptService()
    return _service
