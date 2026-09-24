"""
Marketing run ledger — persistence for migration 170 (SYSTEM_DESIGN_GUIDELINES §12).

Who calls this
--------------
* `app/api/v1/endpoints/marketing_internal.py` — the token-gated API the MEDIA WORKER
  (`marketing/main.py`, a Railway cron service holding NO Supabase key) uses to
  claim the day, checkpoint stages, register artefacts and hand over the day's posts.
* `app/services/marketing/publisher_service.py` — the PUBLISHER loop in the web lifespan,
  which claims `approved` posts one at a time before any external call.

The two processes never share a clock or a filesystem; every "have I already done this"
question is answered by a row here, never by inference from the boot time (the lesson of
migration 147).

Idempotency shapes, all enforced by the database, not by check-then-act:
* `marketing_runs.run_date` UNIQUE — the worker's daily claim is the INSERT; a second
  worker (or the same one after a Railway-skipped slot) gets 23505 and reads the row.
* `marketing_assets.storage_path` UNIQUE — content-addressed, so re-registering the same
  bytes on a resumed run returns the existing row instead of a second object.
* `marketing_posts (run_id, platform, format)` UNIQUE and `idempotency_key` UNIQUE — one
  ledger row per outlet, and the key the outlets themselves see.
* `claim_post`: `UPDATE … WHERE status = 'approved'` returning the row — atomic, so two
  publisher ticks cannot both take the same post.

Supabase is reached through `sb_exec` (never a bare `.execute()` on the loop —
`app/utils/supabase_async.py` explains why that matters on one uvicorn worker).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.database import get_supabase
from app.schemas.marketing import (
    ASSET_EXTENSIONS,
    ASSET_KIND_EXTENSIONS,
    ASSET_KINDS,
    MEDIA_REQUIRED_FORMATS,
    POST_FORMATS,
    POST_FORMATS_BY_PLATFORM,
    POST_MEDIA_KINDS,
    POST_PLATFORMS,
    POST_STATUSES,
    RUN_STAGES,
    RUN_STATUSES,
    SERVER_OWNED_RUN_METADATA,
    WORKER_RUN_STATUSES,
)
from app.utils.market_hours import ET
from app.utils.supabase_async import sb_exec
from app.utils.supabase_errors import is_unique_violation

logger = logging.getLogger(__name__)

RUNS = "marketing_runs"
ASSETS = "marketing_assets"
POSTS = "marketing_posts"
SCRIPTS = "marketing_scripts"

# Claim reasons — the worker branches on these strings, so they are part of the wire contract.
CLAIMED = "claimed"
ALREADY_DONE = "already_done"
IN_PROGRESS = "in_progress"
MEDIA_READY = "media_ready"
ATTEMPTS_EXHAUSTED = "attempts_exhausted"
NO_RUN = "no_run"

_TERMINAL_RUN_STATUSES = frozenset({"published", "skipped"})

#: Statuses the abandoned-run sweep may close. Never `media_ready` (the publisher owns it; closing
#: it would strand approved posts) and never a terminal or `failed` row.
_SWEEPABLE_RUN_STATUSES = ("in_progress", "planned")
#: Rows one sweep looks at, oldest run_date first. It runs inside the claim request on the single
#: uvicorn worker, so it is bounded; anything left over is closed by the next hourly claim. The
#: liveness check runs AFTER the limit, so an old row something touched recently still takes a
#: slot (and is skipped): the limit bounds rows READ, not rows closed — harmless at about one
#: abandoned run a day. Pinned (count and order) by
#: tests/test_marketing_run_service.py::test_one_sweep_closes_at_most_the_limit_and_the_oldest_first.
_SWEEP_LIMIT = 20

# Columns the publisher may write through `mark_post`. A whitelist, so an adapter result dict
# can never smuggle a column (or a typo that PostgREST would 400 on) into the ledger.
_POST_WRITABLE = frozenset({
    "external_id", "external_url", "attempts", "last_error", "cost_micros", "metrics",
    "metadata", "claimed_at", "published_at", "approved_at", "approved_by",
})


class MarketingRunError(Exception):
    """A ledger operation could not be completed. Carries the operation and ids so the log
    line is diagnosable without a repro."""


class MarketingRunNotFound(MarketingRunError):
    pass


class MarketingAssetNotFound(MarketingRunError):
    pass


class MarketingAssetMissingInStorage(MarketingRunError):
    """The worker said it uploaded, but the object is not there. Refuse to mark it ready:
    a `ready` asset that 404s would publish a broken post."""


class MarketingScriptNotReady(MarketingRunError):
    """`create_posts` before the run's script was accepted, or for an outlet the accepted
    script does not carry. The caption is SERVER-authored from the accepted script, so there is
    nothing to record until the writer is done (409, never retried as a 5xx)."""


class MarketingRunNotHeld(MarketingRunError):
    """A per-run write on a run the caller does not hold — not `in_progress`, outside the
    today/yesterday ET window, or a claim gone stale. 409 MARKETING_RUN_NOT_HELD: never retried;
    the next claim decides what happens to the day. Most importantly this is what stops a kick
    from starting writer spend for an arbitrary (old, closed) run id."""


class MarketingRequestInvalid(MarketingRunError):
    """The worker asked for something the contract forbids — a (platform, format) the server
    does not record, a media post with no media, a stage moving backwards, a status only the
    claim or the publisher may write. 422 MARKETING_REQUEST_INVALID: the same request can never
    succeed, so it must not be retried as a 5xx."""


# ── pure helpers (unit-tested, no I/O) ─────────────────────────────────────────


def run_date_et(now: Optional[datetime] = None) -> date:
    """The ET calendar day a run belongs to. Marketing days are wall-clock days in New York,
    matching every other daily boundary in the app (`market_hours.ET`)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(ET).date()


def claim_window_ok(run_date: date, today: date) -> bool:
    """A worker may claim only TODAY (ET) or YESTERDAY (the out-of-window resume). A future
    date would claim the real day before it arrives (`run_date` is UNIQUE, so that day would
    later be skipped as already done); an older one is unbounded writer spend on demand. The
    same window bounds which runs a kick may start writer spend for (`held_problem`)."""
    return today - timedelta(days=1) <= run_date <= today


def held_problem(
    run: Dict[str, Any], *, now: datetime, today: date, stale_seconds: int
) -> Optional[str]:
    """Why `run` is NOT held by a live worker claim, or None when it is. Pure.

    Held = `in_progress`, dated inside the claim window, and touched (the later of
    `started_at` / `updated_at`, exactly `decide_claim`'s liveness) less than `stale_seconds`
    ago. The shipped worker always kicks inside its own tick, which is capped
    (WORKER_DEADLINE_SECONDS) below MARKETING_RUN_STALE_SECONDS, and its out-of-window resume
    runs for YESTERDAY before the run hour — so every legitimate kick passes. A run that
    `decide_claim` would re-claim as stale is exactly one that is not held."""
    status = run.get("status")
    if status != "in_progress":
        return f"status is {status!r}, not 'in_progress'"
    try:
        run_date = date.fromisoformat(str(run.get("run_date"))[:10])
    except ValueError:
        return f"run_date {run.get('run_date')!r} is unreadable"
    if not claim_window_ok(run_date, today):
        return f"run_date {run_date} is outside the claim window ({today - timedelta(days=1)}..{today} ET)"
    touched = max(
        (t for t in (_parse_ts(run.get("started_at")), _parse_ts(run.get("updated_at"))) if t is not None),
        default=None,
    )
    if touched is None:
        return "the run carries no claim time"
    if now - touched >= timedelta(seconds=max(stale_seconds, 0)):
        return f"the claim went stale (last touched {touched.isoformat()})"
    return None


def next_stage(stage: str) -> Optional[str]:
    """The stage after `stage`, or None when the pipeline is complete."""
    if stage not in RUN_STAGES:
        raise ValueError(f"unknown stage {stage!r}")
    i = RUN_STAGES.index(stage)
    return RUN_STAGES[i + 1] if i + 1 < len(RUN_STAGES) else None


def storage_path_for(run_date: date, kind: str, sha256: str, ext: str) -> str:
    """Content-addressed object key: `<run_date>/<kind>-<sha256[:16]>.<ext>`.

    Immutable by construction — the same bytes always land on the same key, different bytes
    never collide with it, and nothing is ever overwritten in place (podcast directories
    and Meta both cache by URL)."""
    if kind not in ASSET_KINDS:
        raise ValueError(f"unknown asset kind {kind!r}")
    ext = ext.lower().lstrip(".")
    if ext not in ASSET_EXTENSIONS:
        raise ValueError(f"unknown extension {ext!r}")
    if ext not in ASSET_KIND_EXTENSIONS.get(kind, ()):
        raise ValueError(f"a {kind!r} asset cannot be a .{ext}")
    if len(sha256) != 64:
        raise ValueError("sha256 must be 64 hex chars")
    return f"{run_date.isoformat()}/{kind}-{sha256[:16].lower()}.{ext}"


def idempotency_key_for(run_date: date, platform: str, fmt: str) -> str:
    if platform not in POST_PLATFORMS:
        raise ValueError(f"unknown platform {platform!r}")
    if fmt not in POST_FORMATS:
        raise ValueError(f"unknown format {fmt!r}")
    return f"{run_date.isoformat()}:{platform}:{fmt}"


def decide_claim(
    existing: Optional[Dict[str, Any]],
    *,
    now: datetime,
    stale_seconds: int,
    max_attempts: int = 0,
    claim_nonce: Optional[str] = None,
) -> str:
    """Given the row already holding today's `run_date` (or None), decide what the worker
    may do. Pure, so the whole matrix is unit-tested.

    * no row → CLAIMED (insert)
    * published / skipped → ALREADY_DONE
    * media_ready → MEDIA_READY (the worker's half is done; the publisher owns the rest)
    * in_progress carrying OUR `claim_nonce` → CLAIMED (our own claim whose response was
      lost; the retry must not conclude "someone else has it")
    * in_progress, last touched less than `stale_seconds` ago → IN_PROGRESS (another
      worker, or a manual run beside the cron — leave it alone). Liveness is the LATER of
      `started_at` and `updated_at`: every stage checkpoint bumps `updated_at`, so a long
      healthy run keeps itself alive, and a killed one goes quiet.
    * `attempts` ≥ `max_attempts` (when > 0) → ATTEMPTS_EXHAUSTED: a deterministically
      failing day must not re-run every hourly tick until midnight.
    * in_progress but stale, planned, or failed → CLAIMED (re-claim and resume)
    """
    if existing is None:
        return CLAIMED
    status = existing.get("status")
    if status in _TERMINAL_RUN_STATUSES:
        return ALREADY_DONE
    if status == "media_ready":
        return MEDIA_READY
    if status == "in_progress":
        meta = existing.get("metadata") or {}
        if claim_nonce and isinstance(meta, dict) and meta.get("claim_nonce") == claim_nonce:
            return CLAIMED
        touched = max(
            (t for t in (_parse_ts(existing.get("started_at")), _parse_ts(existing.get("updated_at")))
             if t is not None),
            default=None,
        )
        if touched is not None and now - touched < timedelta(seconds=max(stale_seconds, 0)):
            return IN_PROGRESS
    if max_attempts > 0 and int(existing.get("attempts") or 0) >= max_attempts:
        return ATTEMPTS_EXHAUSTED
    # stale in_progress, planned, failed, or an unknown value written by a newer migration:
    # let the worker take it.
    return CLAIMED


def _parse_ts(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_filter(value: Any) -> str:
    """An observed timestamp re-rendered for an equality FILTER: the same instant (Postgres
    compares timestamptz by value, to the microsecond) in the UTC `Z` form — never `+00:00`,
    which PostgREST decodes to a space and answers 503 (the notifications-cursor incident)."""
    parsed = _parse_ts(value)
    if parsed is None:
        return str(value)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _touched(row: Dict[str, Any]) -> Optional[datetime]:
    """A run's liveness: the LATER of `started_at` / `updated_at` (what `decide_claim` and
    `held_problem` read)."""
    return max(
        (t for t in (_parse_ts(row.get("started_at")), _parse_ts(row.get("updated_at"))) if t is not None),
        default=None,
    )


def _is_worker_replay(row: Dict[str, Any], *, status: Optional[str], stage: Optional[str]) -> bool:
    """Does `row` already hold everything a worker PATCH that could not be applied asks for?
    Only a TERMINAL request can be a replay (`status` is always a WORKER_RUN_STATUSES value, never
    `in_progress`): a bare checkpoint on a run that left `in_progress` is a lost claim, and the
    worker must stop, not carry on against a closed day."""
    if status is None or row.get("status") != status:
        return False
    return stage is None or row.get("stage") == stage


def _replayed(run_id: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """Answer a replayed worker PATCH with the row as it is. Nothing is written — which is also
    why this says "already", not "recorded": a stale tick can confirm a status it did not write."""
    logger.info(
        "marketing update_run: run %s is already %r (stage=%s) — the worker's PATCH is a replay (a "
        "retried write whose first response was lost, or a stale tick's); nothing written",
        run_id, row.get("status"), row.get("stage"),
    )
    return row


async def _exec(query: Any, *, op: str, **ids: Any) -> Any:
    """`sb_exec` with every failure re-raised as `MarketingRunError` carrying the operation
    and ids — except a 23505, which is re-raised as-is so callers can adopt the winner.
    Without this a raw postgrest `APIError` (22P02 on a non-UUID id, a 520 from the edge)
    reaches the endpoint classifier's generic tail and is reported as a REPORT failure."""
    try:
        return await sb_exec(query)
    except Exception as e:
        if is_unique_violation(e):
            raise
        raise MarketingRunError(
            f"{op} failed ({', '.join(f'{k}={v}' for k, v in ids.items())}): "
            f"{type(e).__name__}: {e}"
        ) from e


def _one(result: Any) -> Optional[Dict[str, Any]]:
    data = getattr(result, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data or None


# ── the service ────────────────────────────────────────────────────────────────


class MarketingRunService:
    """Stateless; every call is one or two PostgREST round trips off the loop."""

    def __init__(self, supabase=None) -> None:
        self._sb = supabase

    @property
    def sb(self):
        return self._sb or get_supabase()

    # runs ------------------------------------------------------------------

    async def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return _one(
            await _exec(
                self.sb.table(RUNS).select("*").eq("id", run_id).limit(1),
                op="get_run", run_id=run_id,
            )
        )

    async def get_run_by_date(self, run_date: date) -> Optional[Dict[str, Any]]:
        return _one(
            await _exec(
                self.sb.table(RUNS).select("*").eq("run_date", run_date.isoformat()).limit(1),
                op="get_run_by_date", run_date=run_date,
            )
        )

    async def claim_run(
        self,
        run_date: date,
        *,
        worker_version: str,
        dry_run: bool,
        now: Optional[datetime] = None,
        claim_nonce: Optional[str] = None,
        resume_only: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """INSERT the day's row, or decide against the one that exists. Returns (row, reason).

        The INSERT is the claim: a UNIQUE violation means someone got there first and we
        fall through to the decision matrix on THEIR row. A re-claim of a stale/failed row is
        a compare-and-swap conditioned on the `attempts` value we observed — a column the
        re-claim itself increments — so two workers racing on the same abandoned row cannot
        both win (conditioning on `status` alone was a no-op: the observed and the new value
        were both `in_progress`).

        `resume_only` never inserts: it is the out-of-window tick finishing a run that was
        killed after the last in-window tick, and (None, NO_RUN) means there is nothing.

        Every claim — of any date, resume or not — first sweeps runs abandoned OUTSIDE the claim
        window (`_sweep_abandoned`): nothing else could ever close them.
        """
        now = now or datetime.now(timezone.utc)
        stamp = now.isoformat()
        nonce_meta = {"claim_nonce": claim_nonce} if claim_nonce else {}
        await self._sweep_abandoned(now=now)

        if not resume_only:
            fresh = {
                "run_date": run_date.isoformat(),
                "status": "in_progress",
                "stage": "planned",
                "worker_version": worker_version,
                "dry_run": bool(dry_run),
                "attempts": 1,
                "started_at": stamp,
                "updated_at": stamp,
                "metadata": nonce_meta,
            }
            try:
                inserted = _one(await _exec(self.sb.table(RUNS).insert(fresh), op="claim_run.insert", run_date=run_date))
                if inserted:
                    logger.info(
                        "marketing run CLAIMED (new) run_date=%s run_id=%s worker=%s dry_run=%s",
                        run_date, inserted.get("id"), worker_version, dry_run,
                    )
                    return inserted, CLAIMED
            except Exception as e:
                if not is_unique_violation(e):
                    raise

        existing = await self.get_run_by_date(run_date)
        if existing is None:
            if resume_only:
                return None, NO_RUN
            # the row vanished between the 23505 and the read — treat as busy
            raise MarketingRunError(f"claim_run: run for {run_date} raced away")

        reason = decide_claim(
            existing,
            now=now,
            stale_seconds=settings.MARKETING_RUN_STALE_SECONDS,
            max_attempts=settings.MARKETING_MAX_RUN_ATTEMPTS,
            claim_nonce=claim_nonce,
        )
        if reason == ATTEMPTS_EXHAUSTED and existing.get("status") != "failed":
            # A stale in_progress (or planned) row at the cap: its last worker was killed, so no
            # `failed` was ever written and the day would read "in_progress" forever.
            existing = await self._close_exhausted(existing, now=now)
        if reason != CLAIMED:
            logger.info(
                "marketing run NOT claimed run_date=%s run_id=%s status=%s attempts=%s reason=%s",
                run_date, existing.get("id"), existing.get("status"), existing.get("attempts"),
                reason,
            )
            return existing, reason

        meta = existing.get("metadata") or {}
        if claim_nonce and isinstance(meta, dict) and meta.get("claim_nonce") == claim_nonce \
                and existing.get("status") == "in_progress":
            # Our own claim; the response was lost. Nothing to re-claim.
            logger.info(
                "marketing run claim RECOVERED by nonce run_date=%s run_id=%s",
                run_date, existing.get("id"),
            )
            return existing, CLAIMED

        observed_status = existing.get("status")
        observed_attempts = int(existing.get("attempts") or 0)
        reclaim = {
            "status": "in_progress",
            "attempts": observed_attempts + 1,
            "started_at": stamp,
            "finished_at": None,
            "last_error": None,
            "worker_version": worker_version,
            "dry_run": bool(dry_run),
            "updated_at": stamp,
            "metadata": {**(meta if isinstance(meta, dict) else {}), **nonce_meta},
        }
        updated = _one(
            await _exec(
                self.sb.table(RUNS)
                .update(reclaim)
                .eq("id", existing["id"])
                .eq("status", observed_status)
                .eq("attempts", observed_attempts),
                op="claim_run.reclaim", run_id=existing["id"],
            )
        )
        if updated is None:
            # Lost the race: someone else re-claimed between our read and our write.
            current = await self.get_run(existing["id"]) or existing
            logger.info(
                "marketing run re-claim LOST race run_date=%s run_id=%s now status=%s",
                run_date, existing["id"], current.get("status"),
            )
            return current, IN_PROGRESS
        logger.info(
            "marketing run RE-CLAIMED run_date=%s run_id=%s from status=%s attempt=%s stage=%s",
            run_date, updated.get("id"), observed_status, updated.get("attempts"),
            updated.get("stage"),
        )
        return updated, CLAIMED

    async def _close_failed_cas(
        self, existing: Dict[str, Any], *, now: datetime, last_error: str, op: str
    ) -> Optional[Dict[str, Any]]:
        """Close an ABANDONED run `failed`: one compare-and-swap on exactly the row that was
        judged abandoned — the observed (status, attempts), the same guard the re-claim uses and
        never `status` alone, plus the observed `updated_at` (the liveness the judgement read: a
        worker checkpoint landing in between proves it alive, and makes this miss). `attempts`
        is left as it is, so the cap still holds. Returns the closed row, or None when another
        writer moved the row first; a ledger error raises MarketingRunError."""
        run_id = existing.get("id")
        stamp = now.isoformat()
        query = (
            self.sb.table(RUNS)
            .update({"status": "failed", "finished_at": stamp, "updated_at": stamp,
                     "last_error": last_error[:2000]})
            .eq("id", run_id)
            .eq("status", existing.get("status"))
            .eq("attempts", int(existing.get("attempts") or 0))
        )
        observed_touch = existing.get("updated_at")
        query = (query.is_("updated_at", "null") if observed_touch is None
                 else query.eq("updated_at", _ts_filter(observed_touch)))
        return _one(await _exec(query, op=op, run_id=run_id))

    async def _sweep_abandoned(self, *, now: datetime) -> int:
        """Close runs abandoned OUTSIDE the claim window. `_close_exhausted` runs only from a
        claim of the run's own date, and the window stops admitting that date after yesterday
        ET — so a run killed on its last claimable tick (the D+1 resume, an interrupted manual
        FORCE run, one that crossed midnight) used to read `in_progress` forever with nothing
        ever logging it. Every claim (the hourly cron claims one date or another on every tick)
        sweeps them: an `in_progress`/`planned` run dated STRICTLY before yesterday ET — never
        yesterday, which is still resumable until the run hour — whose liveness is older than
        MARKETING_RUN_STALE_SECONDS. Each close is the same fenced CAS as the attempts-cap close;
        only the transition logs WARNING. Bounded (`_SWEEP_LIMIT`, on the (status, run_date)
        index) and best effort: a failure here never fails the claim. Returns how many closed."""
        today = run_date_et(now)
        cutoff = today - timedelta(days=1)
        stale = timedelta(seconds=max(int(settings.MARKETING_RUN_STALE_SECONDS), 0))
        try:
            res = await _exec(
                self.sb.table(RUNS).select("*")
                .in_("status", list(_SWEEPABLE_RUN_STATUSES))
                .lt("run_date", cutoff.isoformat())
                .order("run_date")
                .limit(_SWEEP_LIMIT),
                op="sweep_abandoned.select", before=cutoff,
            )
        except Exception as e:
            (logger.warning if isinstance(e, MarketingRunError) else logger.error)(
                "marketing run: abandoned-run sweep could not read (%s: %s) — the next claim retries",
                type(e).__name__, e, exc_info=not isinstance(e, MarketingRunError),
            )
            return 0
        closed = 0
        # The window and the status are decided by the query above ONLY (one guard, not two
        # that would each hide the other's regression); liveness is decided here.
        for row in list(getattr(res, "data", None) or []):
            run_id, run_date = row.get("id"), row.get("run_date")
            touched = _touched(row)
            if touched is not None and now - touched < stale:
                continue  # something touched it recently: not provably abandoned yet
            try:
                done = await self._close_failed_cas(
                    row, now=now, op="sweep_abandoned.close",
                    last_error=(f"abandoned outside the claim window: run_date {run_date} (today "
                                f"{today} ET) was last touched "
                                f"{touched.isoformat() if touched else 'never'} at "
                                f"stage={row.get('stage')} status={row.get('status')} "
                                f"attempts={row.get('attempts')}"),
                )
            except Exception as e:
                (logger.warning if isinstance(e, MarketingRunError) else logger.error)(
                    "marketing run: sweep could not close run_id=%s run_date=%s (%s: %s) — the next "
                    "claim retries", run_id, run_date, type(e).__name__, e,
                    exc_info=not isinstance(e, MarketingRunError),
                )
                continue
            if done is None:
                logger.info("marketing run: sweep of run_id=%s run_date=%s lost to a concurrent write "
                            "— left as it is now", run_id, run_date)
                continue
            closed += 1
            logger.warning(
                "marketing run ABANDONED outside the claim window run_date=%s run_id=%s attempts=%s "
                "stage=%s was=%s last_touched=%s — closed failed; the day needs a human",
                run_date, run_id, row.get("attempts"), row.get("stage"), row.get("status"),
                touched.isoformat() if touched else None,
            )
        return closed

    async def _close_exhausted(self, existing: Dict[str, Any], *, now: datetime) -> Dict[str, Any]:
        """Move an abandoned run at the attempts cap to `failed`, once (`_close_failed_cas`).
        Only the call that made the transition logs WARNING; every later tick sees `failed` and
        stays at INFO. Best effort: a ledger blip here must not turn the claim answer into a 503."""
        run_id = existing.get("id")
        observed_status = existing.get("status")
        observed_attempts = int(existing.get("attempts") or 0)
        try:
            closed = await self._close_failed_cas(
                existing, now=now, op="claim_run.close_exhausted",
                last_error=(f"attempts exhausted ({observed_attempts}): the last attempt was "
                            f"abandoned at stage={existing.get('stage')} status={observed_status}"),
            )
        except MarketingRunError as e:
            logger.warning(
                "marketing run: could not close an exhausted run run_id=%s run_date=%s (%s: %s) — "
                "the next tick retries", run_id, existing.get("run_date"), type(e).__name__, e,
            )
            return existing
        if closed is None:
            # Someone moved it first (a late worker's own `failed`); report what is there now.
            try:
                return await self.get_run(run_id) or existing
            except MarketingRunError as e:
                logger.warning("marketing run: re-read after a lost close failed run_id=%s (%s: %s)",
                               run_id, type(e).__name__, e)
                return existing
        logger.warning(
            "marketing run ABANDONED at the attempts cap run_date=%s run_id=%s attempts=%s "
            "stage=%s was=%s — closed failed; the day needs a human", existing.get("run_date"),
            run_id, observed_attempts, existing.get("stage"), observed_status,
        )
        return closed

    async def update_run(
        self,
        run_id: str,
        *,
        stage: Optional[str] = None,
        status: Optional[str] = None,
        content_class: Optional[str] = None,
        template_id: Optional[str] = None,
        source_ref: Optional[str] = None,
        last_error: Optional[str] = None,
        timings: Optional[Dict[str, float]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        finished: bool = False,
        worker: bool = False,
    ) -> Dict[str, Any]:
        """Write the fields that were given; merge `timings`/`metadata` into the JSONB
        rather than replacing it, so each stage reports only its own numbers.

        `worker=True` is the internal PATCH route: the least-trusted process in the engine
        may only write its OWN live run — `in_progress`, fenced in the UPDATE itself, not
        read-then-checked — may set only WORKER_RUN_STATUSES, may only move `stage` forward
        (also fenced on the observed stage), and may not write the server-owned metadata keys
        (`claim_nonce` is trusted by `decide_claim` ahead of the attempts cap).

        The worker RETRIES every call after a transport error or a 502/503/504, so a worker
        write must be idempotent: a PATCH whose effect is already there — the run already holds
        the terminal `status` it asks for (and the `stage`, if it names one) — is a REPLAY of a
        write whose response was lost (or a stale tick's), and answers the row unchanged with
        200 instead of 409. It writes NOTHING: merging its last_error/metadata/timings into a
        closed run would reopen the hole the fence closes. A stage checkpoint is fenced on the
        observed stage OR the one it asks for, so a retry whose read ran before its own first
        attempt committed still lands; never "any stage ahead", which would let a stale tick
        write over a newer holder's progress."""
        if stage is not None and stage not in RUN_STAGES:
            raise ValueError(f"unknown stage {stage!r}")
        if status is not None and status not in RUN_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        if worker:
            if status is not None and status not in WORKER_RUN_STATUSES:
                raise MarketingRequestInvalid(
                    f"run {run_id}: the worker may set status to {WORKER_RUN_STATUSES}, not {status!r}"
                )
            if metadata:
                reserved = sorted(k for k in metadata if k in SERVER_OWNED_RUN_METADATA)
                if reserved:
                    logger.warning("marketing update_run: ignoring server-owned metadata key(s) %s "
                                   "from the worker run_id=%s", reserved, run_id)
                    metadata = {k: v for k, v in metadata.items() if k not in SERVER_OWNED_RUN_METADATA}

        current = await self.get_run(run_id)
        if current is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        observed_stage = current.get("stage")
        if worker:
            if current.get("status") != "in_progress":
                if _is_worker_replay(current, status=status, stage=stage):
                    return _replayed(run_id, current)
                raise MarketingRunNotHeld(
                    f"run {run_id} is {current.get('status')!r}; the worker writes only an in_progress run"
                )
            if stage is not None:
                was = RUN_STAGES.index(observed_stage) if observed_stage in RUN_STAGES else -1
                if RUN_STAGES.index(stage) < was:
                    raise MarketingRequestInvalid(
                        f"run {run_id}: stage may only move forward ({observed_stage!r} → {stage!r})"
                    )

        patch: Dict[str, Any] = {"updated_at": _now_iso()}
        if stage is not None:
            patch["stage"] = stage
        if status is not None:
            patch["status"] = status
        if content_class is not None:
            patch["content_class"] = content_class
        if template_id is not None:
            patch["template_id"] = template_id
        if source_ref is not None:
            patch["source_ref"] = source_ref
        if last_error is not None:
            patch["last_error"] = last_error[:2000]
        if timings:
            merged = dict(current.get("timings") or {})
            merged.update({k: float(v) for k, v in timings.items()})
            patch["timings"] = merged
        if metadata:
            merged_md = dict(current.get("metadata") or {})
            merged_md.update(metadata)
            patch["metadata"] = merged_md
        if finished:
            patch["finished_at"] = _now_iso()

        query = self.sb.table(RUNS).update(patch).eq("id", run_id)
        if worker:
            # The fence lives in the UPDATE: a re-check after the read would let a claim or a
            # close that lands in between be overwritten.
            query = query.eq("status", "in_progress")
            # …and on the observed `attempts`: a re-claim between our read and this write (it keeps
            # `in_progress`, and may already stand at the stage we ask for) is a newer holder, not
            # us. It does not prove the read itself was ours — the claim-nonce fence (Phase 3-4)
            # does that — but it keeps the stage `in_` below from widening that window.
            query = query.eq("attempts", int(current.get("attempts") or 0))
            if stage is not None and observed_stage is not None:
                query = (query.eq("stage", stage) if observed_stage == stage
                         else query.in_("stage", [observed_stage, stage]))
        updated = _one(await _exec(query, op="update_run", run_id=run_id))
        if updated is None:
            fresh = await self.get_run(run_id) if worker else None
            if fresh is not None:
                if _is_worker_replay(fresh, status=status, stage=stage):
                    return _replayed(run_id, fresh)
                raise MarketingRunNotHeld(f"run {run_id} changed under the worker's write; nothing written")
            raise MarketingRunNotFound(f"run {run_id} vanished during update")
        logger.info(
            "marketing run updated run_id=%s stage=%s status=%s finished=%s",
            run_id, updated.get("stage"), updated.get("status"), finished,
        )
        return updated

    # assets ----------------------------------------------------------------

    async def register_asset(
        self,
        run_id: str,
        *,
        kind: str,
        ext: str,
        sha256: str,
        size_bytes: int,
        duration_seconds: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        """Insert the asset row and mint a signed upload URL for it.

        Returns (row, upload) where `upload` is None if the row already exists and is
        `ready` — a resumed run re-registering identical bytes must NOT re-upload (the
        object is immutable and Spotify/Meta cache it by URL).
        """
        run = await self.get_run(run_id)
        if run is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        if run.get("status") != "in_progress":
            # Media is rendered inside the worker's live tick; a closed or never-claimed run
            # gets no new object in the PUBLIC bucket.
            raise MarketingRunNotHeld(f"run {run_id} is {run.get('status')!r}; assets register only on an in_progress run")
        run_date = date.fromisoformat(str(run["run_date"]))
        try:
            path = storage_path_for(run_date, kind, sha256, ext)
        except ValueError as e:
            # The request schema checks the same things first; a ValueError here must still be a
            # 422, not the classifier's generic 502 (which the worker would retry).
            raise MarketingRequestInvalid(f"register_asset run {run_id}: {e}") from e
        content_type = ASSET_EXTENSIONS[ext.lower().lstrip(".")]

        row = {
            "run_id": run_id,
            "kind": kind,
            "storage_path": path,
            "content_type": content_type,
            "bytes": int(size_bytes),
            "sha256": sha256.lower(),
            "duration_seconds": duration_seconds,
            "status": "pending_upload",
            "metadata": metadata or {},
            "updated_at": _now_iso(),
        }
        asset: Optional[Dict[str, Any]] = None
        try:
            asset = _one(await _exec(self.sb.table(ASSETS).insert(row), op="register_asset.insert", run_id=run_id, path=path))
        except Exception as e:
            if not is_unique_violation(e):
                raise
            asset = _one(
                await _exec(
                    self.sb.table(ASSETS).select("*").eq("storage_path", path).limit(1),
                    op="register_asset.select", path=path,
                )
            )
        if asset is None:
            raise MarketingRunError(f"register_asset: no row for {path}")
        if asset.get("status") == "ready":
            logger.info(
                "marketing asset already READY, no re-upload run_id=%s kind=%s path=%s",
                run_id, kind, path,
            )
            return asset, None

        # The wedge this closes: the worker's PUT landed but the process died before
        # `complete_asset`. The object exists, the row says pending_upload, and every later
        # `x-upsert: false` PUT to the same immutable key would 409 forever. So check the
        # bucket BEFORE minting a URL and finish the row here if the bytes are already there.
        try:
            already_there = await self._object_exists(path)
        except MarketingRunError as e:
            # Not fatal: fall through to minting a URL; a real outage surfaces on the PUT.
            logger.warning(
                "register_asset: existence pre-check failed for %s (%s) — minting anyway", path, e,
            )
            already_there = False
        if already_there:
            ready = _one(
                await _exec(
                    self.sb.table(ASSETS)
                    .update({"status": "ready", "updated_at": _now_iso()})
                    .eq("id", asset["id"]),
                    op="register_asset.ready", asset_id=asset["id"],
                )
            ) or {**asset, "status": "ready"}
            logger.info(
                "marketing asset object already in bucket, marked READY without re-upload "
                "run_id=%s kind=%s path=%s", run_id, kind, path,
            )
            return ready, None

        try:
            signed = await sb_exec_storage(
                lambda: self.sb.storage.from_(settings.MARKETING_MEDIA_BUCKET)
                .create_signed_upload_url(path)
            )
        except Exception as e:
            raise MarketingRunError(
                f"register_asset: signed upload URL failed for {path}: {type(e).__name__}: {e}"
            ) from e
        upload = {
            "method": "PUT",
            "url": signed["signed_url"],
            "token": signed["token"],
            "bucket": settings.MARKETING_MEDIA_BUCKET,
            "path": path,
            "content_type": content_type,
        }
        logger.info(
            "marketing asset registered run_id=%s kind=%s path=%s bytes=%s",
            run_id, kind, path, size_bytes,
        )
        return asset, upload

    async def _object_exists(self, path: str) -> bool:
        """Is `path` in the media bucket? Distinguishes ABSENT from OUTAGE.

        storage3's `exists()` answers False for ANY non-200 HEAD (a 5xx included), which
        would turn a Storage outage into "the worker never uploaded". So a False is confirmed
        with a prefix LIST, which raises on an outage — and that raise becomes
        `MarketingRunError` (503 → the worker retries) instead of MARKETING_ASSET_MISSING.
        """
        bucket = settings.MARKETING_MEDIA_BUCKET
        try:
            if await sb_exec_storage(lambda: self.sb.storage.from_(bucket).exists(path)):
                return True
        except Exception as e:
            raise MarketingRunError(
                f"storage HEAD failed for {path}: {type(e).__name__}: {e}"
            ) from e
        prefix, _, name = path.rpartition("/")
        try:
            listing = await sb_exec_storage(
                lambda: self.sb.storage.from_(bucket).list(prefix, {"limit": 100, "search": name})
            )
        except Exception as e:
            raise MarketingRunError(
                f"storage LIST failed for {path}: {type(e).__name__}: {e}"
            ) from e
        return any((item or {}).get("name") == name for item in (listing or []))

    async def complete_asset(self, asset_id: str) -> Dict[str, Any]:
        """Flip `pending_upload` → `ready` ONLY after the object is verifiably in the bucket.
        The worker's word is not enough: a `ready` row whose object 404s would publish a
        broken post, and nothing downstream re-checks."""
        asset = _one(
            await _exec(
                self.sb.table(ASSETS).select("*").eq("id", asset_id).limit(1),
                op="complete_asset.select", asset_id=asset_id,
            )
        )
        if asset is None:
            raise MarketingAssetNotFound(f"asset {asset_id} not found")
        if asset.get("status") == "ready":
            return asset
        path = asset["storage_path"]
        if not await self._object_exists(path):
            raise MarketingAssetMissingInStorage(
                f"asset {asset_id} at {path} is not in bucket "
                f"{settings.MARKETING_MEDIA_BUCKET}"
            )
        updated = _one(
            await _exec(
                self.sb.table(ASSETS)
                .update({"status": "ready", "updated_at": _now_iso()})
                .eq("id", asset_id),
                op="complete_asset.ready", asset_id=asset_id,
            )
        )
        if updated is None:
            raise MarketingAssetNotFound(f"asset {asset_id} vanished during complete")
        logger.info("marketing asset READY id=%s path=%s", asset_id, path)
        return updated

    async def list_assets(self, run_id: str) -> List[Dict[str, Any]]:
        res = await _exec(self.sb.table(ASSETS).select("*").eq("run_id", run_id), op="list_assets", run_id=run_id)
        return list(getattr(res, "data", None) or [])

    # posts -----------------------------------------------------------------

    async def create_posts(
        self, run_id: str, specs: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """One ledger row per (platform, format). Born `pending_review` unless
        MARKETING_AUTO_PUBLISH is on. Re-creating an existing pair returns the existing row
        untouched — a resumed run must never reset a post an admin already approved.

        The worker names the OUTLET (platform, format, asset ids) and nothing else. The caption
        and title come from the run's ACCEPTED script (`marketing_scripts.output.posts`), which
        passed every server-side compliance check (§12.5); the worker's `caption`, `title` and
        `metadata` are ignored. That is what makes MARKETING_AUTO_PUBLISH safe to turn on later:
        the least-trusted process in the engine can no longer choose the words that get posted.
        Posts that carry media are always born `pending_review` — the server cannot yet verify
        what a worker-rendered video says (Phase 7 adds that check).

        The worker does not choose how many posts one caption becomes either: the (platform,
        format) pair must be in POST_FORMATS_BY_PLATFORM, a media format must carry a READY asset
        of a matching kind (POST_MEDIA_KINDS), and only a media-less `text` post can be born
        `approved`. EVERY spec is validated before the first INSERT, so a deterministic refusal
        (409/422, never retried) leaves no partial ledger behind — a later spec that is invalid
        used to leave the earlier ones recorded, and publishable, under a run that then failed.
        A transient failure mid-loop can still leave a prefix; the re-send adopts those rows
        through the idempotency key, so that heals itself."""
        run = await self.get_run(run_id)
        if run is None:
            raise MarketingRunNotFound(f"run {run_id} not found")
        if run.get("status") not in ("in_progress", "media_ready"):
            raise MarketingRunNotHeld(
                f"run {run_id} is {run.get('status')!r}; posts are recorded only for an in_progress "
                "or media_ready run"
            )
        run_date = date.fromisoformat(str(run["run_date"]))
        script = await self.get_script(run_id)
        output = (script or {}).get("output")
        if not script or script.get("status") != "accepted" or not isinstance(output, dict):
            raise MarketingScriptNotReady(
                f"run {run_id} has no accepted script (status={(script or {}).get('status')})"
            )
        copy_by_platform = output.get("posts") if isinstance(output.get("posts"), dict) else {}
        wants_assets = any(spec.get("asset_ids") for spec in specs)
        assets = {a.get("id"): a for a in await self.list_assets(run_id)} if wants_assets else {}

        # ── validate EVERYTHING first ──────────────────────────────────────────
        bad_pairs: List[str] = []
        no_copy: List[str] = []
        not_ready: List[str] = []
        bad_media: List[str] = []
        seen: Dict[Tuple[str, str], Tuple[str, ...]] = {}
        planned: List[Tuple[str, str, Dict[str, Any], List[str]]] = []
        for spec in specs:
            platform, fmt = spec["platform"], spec["format"]
            allowed = POST_FORMATS_BY_PLATFORM.get(platform, ())
            if fmt not in allowed:
                bad_pairs.append(f"{platform}/{fmt} (allowed: {', '.join(allowed) or 'none'})")
            copy = copy_by_platform.get(platform)
            if not isinstance(copy, dict) or not copy.get("caption"):
                no_copy.append(platform)
            asset_ids = list(dict.fromkeys(spec.get("asset_ids") or []))
            previous = seen.setdefault((platform, fmt), tuple(asset_ids))
            if previous != tuple(asset_ids):
                bad_media.append(f"{platform}/{fmt} named twice with different assets")
            kinds = POST_MEDIA_KINDS.get(fmt, ())
            matching = 0
            for aid in asset_ids:
                asset = assets.get(aid)
                if asset is None or asset.get("status") != "ready":
                    not_ready.append(aid)
                elif asset.get("kind") not in kinds:
                    bad_media.append(f"{platform}/{fmt} cannot carry a {asset.get('kind')!r} asset ({aid})")
                else:
                    matching += 1
            if fmt in MEDIA_REQUIRED_FORMATS and not matching and not any(a in not_ready for a in asset_ids):
                bad_media.append(f"{platform}/{fmt} carries no ready {'/'.join(kinds)} asset")
            if isinstance(copy, dict):
                planned.append((platform, fmt, copy, asset_ids))
        if bad_pairs:
            raise MarketingRequestInvalid(
                f"run {run_id}: the server records no post for {sorted(set(bad_pairs))}"
            )
        if no_copy:
            raise MarketingScriptNotReady(
                f"run {run_id}: the accepted script carries no copy for {sorted(set(no_copy))}"
            )
        if not_ready:
            raise MarketingAssetMissingInStorage(
                f"run {run_id}: asset(s) {sorted(set(not_ready))} are not ready assets of this run"
            )
        if bad_media:
            raise MarketingRequestInvalid(f"run {run_id}: {sorted(set(bad_media))}")

        # A dry-run RUN never auto-approves, whatever the web process's switch says: the
        # worker's rehearsal must not become a real post because a different service flipped
        # MARKETING_AUTO_PUBLISH. The flag rides on every row so the publisher sees it too.
        run_dry = bool(run.get("dry_run"))
        auto = settings.MARKETING_AUTO_PUBLISH and not run_dry
        out: List[Dict[str, Any]] = []
        for platform, fmt, copy, asset_ids in planned:
            # Only a media-less TEXT post is born approved: the server has verified every word
            # of it, and the format map allows `text` only on text-native outlets.
            initial = "approved" if (auto and fmt == "text" and not asset_ids) else "pending_review"
            key = idempotency_key_for(run_date, platform, fmt)
            row = {
                "run_id": run_id,
                "platform": platform,
                "format": fmt,
                "status": initial,
                "title": copy.get("title"),
                "caption": copy["caption"],
                "asset_ids": asset_ids,
                "idempotency_key": key,
                "metadata": {
                    "dry_run": run_dry,
                    "generation_id": script.get("generation_id"),
                    "source_ref": script.get("source_ref"),
                    "template_id": script.get("template_id"),
                },
                "approved_at": _now_iso() if initial == "approved" else None,
                "approved_by": "auto" if initial == "approved" else None,
                "updated_at": _now_iso(),
            }
            try:
                created = _one(await _exec(self.sb.table(POSTS).insert(row), op="create_posts.insert", key=key))
            except Exception as e:
                if not is_unique_violation(e):
                    raise
                created = _one(
                    await _exec(
                        self.sb.table(POSTS).select("*").eq("idempotency_key", key).limit(1),
                        op="create_posts.select", key=key,
                    )
                )
            if created is None:
                raise MarketingRunError(f"create_posts: no row for {key}")
            out.append(created)
        logger.info(
            "marketing posts recorded run_id=%s n=%d auto_publish=%s",
            run_id, len(out), auto,
        )
        return out

    async def list_posts(self, status: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        res = await _exec(
            self.sb.table(POSTS)
            .select("*")
            .eq("status", status)
            .order("created_at")
            .limit(limit),
            op="list_posts", status=status,
        )
        return list(getattr(res, "data", None) or [])

    async def claim_post(self, post_id: str) -> Optional[Dict[str, Any]]:
        """approved → queued, atomically. None means another tick took it (or an admin
        rejected it between the list and the claim)."""
        now = _now_iso()
        return _one(
            await _exec(
                self.sb.table(POSTS)
                .update({"status": "queued", "claimed_at": now, "updated_at": now})
                .eq("id", post_id)
                .eq("status", "approved"),
                op="claim_post", post_id=post_id,
            )
        )

    async def mark_post(self, post_id: str, status: str, **fields: Any) -> Dict[str, Any]:
        if status not in POST_STATUSES:
            raise ValueError(f"unknown post status {status!r}")
        unknown = set(fields) - _POST_WRITABLE
        if unknown:
            raise ValueError(f"mark_post: not writable: {sorted(unknown)}")
        patch = {"status": status, "updated_at": _now_iso(), **fields}
        updated = _one(
            await _exec(self.sb.table(POSTS).update(patch).eq("id", post_id), op="mark_post", post_id=post_id)
        )
        if updated is None:
            raise MarketingRunError(f"mark_post: post {post_id} not found")
        return updated


    # scripts (migration 173) --------------------------------------------------
    # Written ONLY by the web side (`script_service.py`). Every state change after the first
    # INSERT is a compare-and-swap on the observed columns, so two writers — a redeploy's
    # overlapping containers, a retried kick — can never both win.

    async def get_script(self, run_id: str) -> Optional[Dict[str, Any]]:
        return _one(
            await _exec(
                self.sb.table(SCRIPTS).select("*").eq("run_id", run_id).limit(1),
                op="get_script", run_id=run_id,
            )
        )

    async def insert_script(self, row: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """First write wins: (row, True) if ours, (existing row, False) on a 23505."""
        run_id = row["run_id"]
        try:
            created = _one(await _exec(self.sb.table(SCRIPTS).insert(row), op="insert_script",
                                       run_id=run_id))
            if created:
                return created, True
        except Exception as e:
            if not is_unique_violation(e):
                raise
        existing = await self.get_script(run_id)
        if existing is None:
            raise MarketingRunError(f"insert_script: row for run {run_id} raced away")
        return existing, False

    async def update_script_where(
        self, run_id: str, patch: Dict[str, Any], *, expect: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """UPDATE … WHERE run_id AND every `expect` column equals its value (None = IS NULL).
        Returns the new row, or None when another writer changed the row first."""
        query = self.sb.table(SCRIPTS).update({**patch, "updated_at": _now_iso()}).eq("run_id", run_id)
        for col, val in expect.items():
            query = query.is_(col, "null") if val is None else query.eq(col, val)
        return _one(await _exec(query, op="update_script", run_id=run_id,
                                expect=",".join(sorted(expect))))

    async def recent_source_refs(self, before: date, limit: int) -> List[str]:
        """`source_ref` of the days before `before`, newest first — what selection must not
        repeat. Read from `marketing_scripts` ITSELF (its `run_date` is written in the same
        first-write-wins INSERT as the selection), never from the best-effort mirror on
        `marketing_runs`: one lost mirror write used to drop that day's pick out of the window,
        and with the window at pool-1 the rotation then picked it again on the very next posting
        day. Rejected days still count (the pick was made); rest days (NULL) never do."""
        if limit <= 0:
            return []
        res = await _exec(
            self.sb.table(SCRIPTS).select("run_date,source_ref")
            .lt("run_date", before.isoformat())
            .not_.is_("source_ref", "null")
            .order("run_date", desc=True)
            .limit(limit),
            op="recent_source_refs", before=before,
        )
        rows = [r for r in (getattr(res, "data", None) or []) if r.get("source_ref")]
        rows.sort(key=lambda r: str(r.get("run_date") or ""), reverse=True)
        return [str(r["source_ref"]) for r in rows][:limit]


async def sb_exec_storage(fn):
    """Storage calls are sync too; run them off the loop like `sb_exec` does for PostgREST."""
    import asyncio

    return await asyncio.to_thread(fn)


_service: Optional[MarketingRunService] = None


def get_marketing_run_service() -> MarketingRunService:
    global _service
    if _service is None:
        _service = MarketingRunService()
    return _service
