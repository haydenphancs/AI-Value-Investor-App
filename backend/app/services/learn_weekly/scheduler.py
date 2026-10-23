"""The Wiser weekly engine's one background loop (spawned from `main.lifespan`, Railway only).

Every five minutes, while `LEARN_WEEKLY_ENABLED`, one tick runs these steps — each isolated, so
one failing step never stops the others (a dead loop would look exactly like "the owner simply
didn't get a Deep Dive this week" and have no other symptom):

  1. cache sync     — `publisher.cache_sync`: a publish/unpublish on ANOTHER instance (Railway
                      overlaps the old and new container on a deploy) invalidates this one's
                      Money Moves cache too.
  2. topic          — Thursday `LEARN_TOPIC_HOUR_ET` → the Deep Dive's top-3 topic card
                      (`topic.run_topic_step`). Day-claimed (`learn_weekly_topic`): the claim
                      keeps two instances from scoring at once; the ledger's UNIQUE
                      `period_key` makes it once per WEEK (a Friday catch-up adopts Thursday's
                      row and does nothing when its topic is already chosen).
  3. auto-pick      — Friday 07:00 ET: no owner tap → candidate #1 (`review.auto_pick`).
  4. publish        — `publisher.publish_due` (approved and due → the live row) and
                      `publisher.expire_due` (the hold ended → expired; never `publishing`). Runs
                      before the build so a 23:00 publish never waits behind a long Regenerate.
  5. build          — every tick, for a `planned` piece whose topic is chosen and whose build
                      time has come (`build.build_piece`), a `building` piece whose lease ran out
                      (a build killed by a deploy) and a `drafted` piece whose narration could not
                      be queued. NOT day-claimed: exactly-once is the piece lease (planned →
                      building CAS), so Regenerate / Next topic can rebuild the same day. A writer
                      failure backs off (15 min, 45 min, …) instead of burning the cap in an
                      outage. Kill switch: `notification_job_state.enabled` on `learn_weekly_build`.
                      A piece still `planned` past its engine's catch-up cut-off
                      (`schedule.catch_up_cutoff`) is skipped with an alert — building it would
                      spend the writer and the narrator on a piece that cannot be reviewed before
                      its hold ends.
  6. narration      — a `media_pending` piece whose current job FAILED or was CANCELLED gets the
                      next round (`narration.after_job_failed`); one whose job is READY but never
                      merged gets the merge (`narration.finish_ready_job`).
  7. review         — `review.review_cycle`: topic cards and review bundles to the owner's
                      Telegram (shared flood gate with the marketing bot).
  8. deadman        — Friday 15:00 ET (`learn_weekly_deadman`, day-claimed): no Deep Dive in
                      review yet → a loud alert (`alerts.deadman_check`).

Exactly-once, three layers deep: the ledger row per period (first write wins), the piece lease
for the build, and the day claims for the two once-a-day steps. Switches: `LEARN_WEEKLY_ENABLED`
gates everything, `LEARN_DEEP_DIVE_ENABLED` the Deep Dive; `LEARN_WEEKLY_DRY_RUN` is frozen
into each piece at creation (a dry-run piece ends `rehearsed` and never touches
`money_move_articles`). The category slot (`LEARN_SLOT_ENABLED`) is phase 1c: its topic and
category-bag pipeline is not built yet, so the switch only logs.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, Mapping, Optional

from app.config import settings
from app.services.learn_weekly import contracts as c
from app.services.learn_weekly import schedule

logger = logging.getLogger(__name__)

JOB_TOPIC = "learn_weekly_topic"
JOB_BUILD = "learn_weekly_build"
JOB_DEADMAN = "learn_weekly_deadman"
TIMEZONE_NAME = "America/New_York"

TICK_SECONDS = 300
BOOT_DELAY_SECONDS = 420          # after the theme (180/240 s) and trillion (300/360 s) loops
# A topic step is ~150-300 FMP news reads + SEC submissions + a few LLM calls (minutes); the
# window only has to outlast it so a deploy overlap cannot start a second scorer.
_TOPIC_STALE_SECONDS = 3600
_DEADMAN_STALE_SECONDS = 900

# Logged once per process, not every five minutes.
_logged_once: set = set()


def _log_once(key: str, level: int, msg: str, *args: Any) -> None:
    if key in _logged_once:
        return
    _logged_once.add(key)
    logger.log(level, msg, *args)


async def run_learn_weekly_loop() -> None:
    await asyncio.sleep(BOOT_DELAY_SECONDS)
    while True:
        try:
            await tick(datetime.now(timezone.utc))
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001 — the loop must outlive any one tick
            logger.error("learn weekly: tick failed (%s: %s) — next tick in %d s",
                         type(e).__name__, e, TICK_SECONDS, exc_info=True)
        await asyncio.sleep(TICK_SECONDS)


async def _step(name: str, fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run one step; a failure is logged with its name and never stops the next step."""
    try:
        return await fn()
    except asyncio.CancelledError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("learn weekly: step %s failed (%s: %s)", name, type(e).__name__, e,
                     exc_info=True)
        return None


async def tick(now: datetime) -> Dict[str, Any]:
    """One wake. Returns a per-step summary (for tests and the log line)."""
    if not settings.LEARN_WEEKLY_ENABLED:
        _log_once("disabled", logging.INFO,
                  "learn weekly: LEARN_WEEKLY_ENABLED is off — the engine idles (migration 204 "
                  "must be applied before it is switched on)")
        return {"enabled": False}
    if settings.LEARN_SLOT_ENABLED:
        _log_once("slot", logging.WARNING,
                  "learn weekly: LEARN_SLOT_ENABLED is set, but the category-slot pipeline "
                  "(topic + category bag) is phase 1c and not built — no slot piece is created")

    from app.services.learn_weekly import alerts, publisher, review

    out: Dict[str, Any] = {"enabled": True}
    out["cache_sync"] = await _step("cache_sync", publisher.cache_sync)
    dd = schedule.current_deep_dive(now)
    if settings.LEARN_DEEP_DIVE_ENABLED and dd is not None:
        out["topic"] = await _step("topic", lambda: _topic_step(now, dd))
        out["auto_pick"] = await _step("auto_pick", lambda: review.auto_pick(now))
    # Publish and expire BEFORE the build: a build can take minutes, and a piece due at 23:00 must
    # not wait behind a Regenerate; it also keeps the publish stamp close to the tick's `now`.
    out["publish"] = await _step("publish", lambda: publisher.publish_due(now))
    out["expire"] = await _step("expire", lambda: publisher.expire_due(now))
    out["build"] = await _step("build", lambda: _build_step(now))
    out["narration"] = await _step("narration", lambda: _narration_step(now))
    out["review"] = await _step("review", lambda: review.review_cycle(now))
    if settings.LEARN_DEEP_DIVE_ENABLED and dd is not None:
        out["deadman"] = await _step("deadman", lambda: _deadman_step(now, dd, alerts))
    logger.debug("learn weekly: tick %s → %s", now.isoformat(), out)
    return out


# ── Topic (Thursday) ──────────────────────────────────────────────────────────────────────────


async def _job_enabled(job: str) -> Optional[bool]:
    """The `notification_job_state.enabled` kill switch; None = unreadable (fail closed)."""
    from app.services.notification_jobs import scheduled_job_state

    state = await asyncio.to_thread(scheduled_job_state, job)
    if state is None:
        logger.warning("learn weekly: job state %s unreadable — skipping this wake (fail closed)",
                       job)
        return None
    return bool(state.get("enabled", True))


async def _topic_step(now: datetime, dd: schedule.DeepDiveTimes) -> str:
    if now < dd.topic_at or now >= dd.publish_at:
        return "not_due"
    from app.services.learn_weekly import ledger

    piece = await ledger.get_piece(dd.period_key)
    if piece is not None and (piece.get("topic") or piece.get("status") != c.PLANNED):
        return "done"           # the week's topic exists (or the piece moved on) — nothing to do
    enabled = await _job_enabled(JOB_TOPIC)
    if not enabled:
        return "disabled" if enabled is False else "unreadable"

    from app.services.learn_weekly import topic
    from app.services.notification_jobs import claimed_scheduled_job

    async with claimed_scheduled_job(JOB_TOPIC, timezone_name=TIMEZONE_NAME,
                                     stale_seconds=_TOPIC_STALE_SECONDS) as run:
        if run is None:
            return "held"       # another instance is scoring, or today's run already succeeded
        result = await topic.run_topic_step(dd, now=now)
        # A run that produced no row (scoring aborted before create, LLM down) leaves the day
        # unmarked so the next wake retries; a terminal `skipped` row is a decided outcome.
        run.success = bool(result)
        run.items = 1 if result else 0
        if not result:
            run.error = "topic step produced no piece"
    return "ran"


# ── Build (every tick, lease-fenced per piece) ────────────────────────────────────────────────


def _chosen(piece: Mapping[str, Any]) -> bool:
    topic = piece.get("topic")
    return isinstance(topic, Mapping) and isinstance(topic.get("chosen_index"), int) \
        and not isinstance(topic.get("chosen_index"), bool)


def _build_due(piece: Mapping[str, Any], now: datetime) -> Optional[datetime]:
    """The piece's build time, or None when its key is unreadable (logged)."""
    try:
        times = schedule.times_for_period_key(str(piece.get("period_key")))
    except ValueError as e:
        logger.error("learn weekly: piece %s has an unreadable period_key %r (%s) — not built",
                     piece.get("id"), piece.get("period_key"), e)
        return None
    return times.build_at


WRITER_BACKOFF_SECONDS = 900      # 15 min after the 1st writer failure, ×3 for each later one


def _writer_retry_at(piece: Mapping[str, Any]) -> Optional[datetime]:
    """When a piece the writer just failed on may be built again, or None (no back-off). Reads the
    build's own record: `writer_failures` and a `last_error` the build writes as
    "writer_failures n/cap: …", from the row's `updated_at`."""
    failures = piece.get("writer_failures")
    if not isinstance(failures, int) or isinstance(failures, bool) or failures < 1:
        return None
    if not str(piece.get("last_error") or "").startswith("writer_failures"):
        return None
    updated = _parse_instant(piece.get("updated_at"))
    if updated is None:
        return None
    return updated + timedelta(seconds=WRITER_BACKOFF_SECONDS * 3 ** (min(failures, 6) - 1))


def _parse_instant(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


async def _build_step(now: datetime) -> Dict[str, int]:
    from app.services.learn_weekly import alerts, build, ledger

    out = {"built": 0, "skipped_late": 0, "not_due": 0, "waiting_topic": 0, "held": 0,
           "narration_retried": 0}
    # `planned` = the build queue. `building` too, but only once its lease has run out: a build
    # killed by a deploy (lifespan tasks are cancelled within ~30 s) would otherwise sit there
    # until its hold expired. `drafted` = a draft whose narration job could not be queued; the
    # build only retries the enqueue for it.
    pieces = await ledger.list_pieces(statuses=[c.PLANNED, c.BUILDING, c.DRAFTED], limit=20)
    if not pieces:
        return out
    enabled = await _job_enabled(JOB_BUILD)
    if not enabled:
        return {**out, "disabled": 1}
    for piece in pieces:
        status = piece.get("status")
        if status == c.BUILDING:
            lease = _parse_instant(piece.get("lease_until"))
            if lease is not None and lease > now:
                out["held"] += 1          # a live generation (this or another instance) owns it
                continue
        if status == c.DRAFTED:
            result = await _step(f"enqueue:{piece.get('period_key')}",
                                 lambda p=piece: build.build_piece(p, now=now))
            if result is not None:
                out["narration_retried"] += 1
            continue
        if not _chosen(piece):
            out["waiting_topic"] += 1
            continue
        due = _build_due(piece, now)
        if due is None or now < due:
            out["not_due"] += 1
            continue
        retry_at = _writer_retry_at(piece)
        if retry_at is not None and now < retry_at:
            # A writer failure is usually the model provider (quota, 5xx, a timeout): three
            # back-to-back 5-minute retries would burn LEARN_MAX_WRITER_FAILURES in ten minutes
            # of an outage and fail the week. Back off 15 min, 45 min, … (the cut-off still ends it).
            out["backing_off"] = out.get("backing_off", 0) + 1
            continue
        # The build's own cut-off (its frozen hold, the engine the enqueue will really use), so the
        # scheduler and the build never disagree about "too late".
        cutoff = build.catch_up_cutoff_for(piece)
        if cutoff is not None and now >= cutoff:
            # Too late to build, narrate AND leave the owner a review window before the hold
            # ends: skip loudly instead of spending the writer and the narrator for nothing.
            moved = await ledger.transition(
                piece["id"], from_statuses=[c.PLANNED, c.BUILDING], to_status=c.SKIPPED,
                fields={"last_error": "catch_up_cutoff_passed"})
            if moved is not None:
                out["skipped_late"] += 1
                await alerts.notify_terminal(moved, status=c.SKIPPED,
                                             reason="catch_up_cutoff_passed", stage="build")
            continue
        result = await _step(f"build:{piece.get('period_key')}",
                             lambda p=piece: build.build_piece(p, now=now))
        if result is not None:
            out["built"] += 1
    return out


# ── Narration sweep (every tick) ──────────────────────────────────────────────────────────────


async def _narration_step(now: datetime) -> Dict[str, int]:
    """Keep a `media_pending` piece from waiting forever on its CURRENT job:
      * `failed` / `cancelled` — the ledger closes a job at its lease re-claim cap (`claim_job`)
        without touching the piece, a `/fail` answer can be lost, and a Regenerate can cancel the
        job a concurrent Retry narration just pointed the piece at → `narration.after_job_failed`:
        the next round while inside the engine's cut-off, else `failed` with the owner's alert;
      * `ready` — `complete` commits the job before the piece, so a failed second write leaves
        finished audio unmerged → `narration.finish_ready_job`.
    Both are idempotent (fenced on the job id and the draft)."""
    from app.services.learn_weekly import ledger, narration

    out = {"checked": 0, "failed_jobs": 0}
    pieces = await ledger.list_pieces(statuses=[c.MEDIA_PENDING], limit=20)
    for piece in pieces:
        job_id = piece.get("narration_job_id")
        if not job_id:
            logger.warning("learn weekly: media_pending piece %s (%s) has no narration job",
                           piece.get("id"), piece.get("period_key"))
            continue
        out["checked"] += 1
        job = await ledger.get_job(job_id)
        status = job.get("status") if job is not None else None
        if status in (c.JOB_FAILED, c.JOB_CANCELLED):
            # FAILED: the next round (or the piece fails). CANCELLED: the piece points at a job no
            # worker will ever claim (a Regenerate racing a Retry narration) — same next-round logic.
            out["failed_jobs"] += 1
            await _step(f"narration:{piece.get('period_key')}",
                        lambda j=job: narration.after_job_failed(j, now=now))
        elif status == c.JOB_READY:
            # The audio is done but the piece never moved (its write failed after the job's).
            out["ready_unmerged"] = out.get("ready_unmerged", 0) + 1
            await _step(f"narration-merge:{piece.get('period_key')}",
                        lambda j=job: narration.finish_ready_job(j))
    return out


# ── Deadman (Friday 15:00 ET) ─────────────────────────────────────────────────────────────────


async def _deadman_step(now: datetime, dd: schedule.DeepDiveTimes, alerts: Any) -> str:
    if now < dd.deadman_at or now >= dd.publish_at:
        return "not_due"
    enabled = await _job_enabled(JOB_DEADMAN)
    if not enabled:
        return "disabled" if enabled is False else "unreadable"
    from app.services.notification_jobs import claimed_scheduled_job

    async with claimed_scheduled_job(JOB_DEADMAN, timezone_name=TIMEZONE_NAME,
                                     stale_seconds=_DEADMAN_STALE_SECONDS) as run:
        if run is None:
            return "held"
        verdict = await alerts.deadman_check(now, dd)
        # "unreadable" leaves the day open so the next wake checks again — never a guess.
        run.success = verdict in ("ok", "alerted")
        if not run.success:
            run.error = f"deadman verdict {verdict!r}"
        return str(verdict)
