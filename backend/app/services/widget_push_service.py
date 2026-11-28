"""
WidgetKit push wakes for the Movers widget (iOS 26+): WHO gets a wake, WHEN, and what is written
back. Delivery and APNs classification are `push_service.PushService.send_widget_pushes`.

Owner, 2026-11-27: "Yes, add push too". The sweeper calls `wake(stored_scopes, phase)` once per
pass, after it stored its cards (`updates_insight_sweeper._wake_widgets`, bounded by
`WIDGET_PUSH_PASS_BUDGET_SECONDS`). A wake carries no data: WidgetKit reruns the extension's
timeline and the extension fetches with its own holdings key, exactly as on its schedule. Nothing
depends on delivery — the timeline stays the source of truth.

WHO
  * A ticker card wakes the live push targets whose grant's head ticker (`last_head_ticker`, the
    head the holdings route last served that device) is that ticker.
  * A market card (`MARKET_SCOPE`) wakes every live target (`p_all`).
  * Targets come only from `widget_push_targets`, which applies the holdings-grant migration's ONE
    liveness predicate (`widget_holdings_grant_is_live`) at every read — so a grant revoked,
    expired or evicted since its wake was queued is never pushed (no liveness in Python).

WHEN
  * Never while `WIDGET_HOLDINGS_GRANTS_ENABLED` is off, whatever `WIDGET_PUSH_ENABLED` says: the
    holdings route answers every woken tile 503 then (`require_holdings_grants_enabled`), so a
    wake would only spend APNs sends and the device's WidgetKit push budget. It also CLEARS the
    pending wakes, and logs one WARNING per off-period.
  * Never in the regular session (09:30-16:00 ET): the timeline reloads every 20 min then. A
    regular-session pass also CLEARS the pending wakes.
  * At most one wake per device per `WIDGET_PUSH_MIN_INTERVAL_SECONDS` and
    `WIDGET_PUSH_DAILY_CAP` per ET day. Both are gated on max(local, DB): the DB columns
    (`push_last_sent_at`, `push_sent_day`, `push_sent_count`) survive a restart, so a deploy
    never resets the throttle.
  * A device inside its interval is COALESCED into `_pending` (a trailing-edge flush on a later
    pass, re-read through the targets RPC so a dead grant is dropped). Pending entries die after
    6 h.
  * At most `WIDGET_PUSH_MAX_SENDS_PER_PASS` sends per pass; the rest stay pending.

WRITE-BACK
  * `widget_push_settle` in batches of ≤ 100, flushed at least once a second while the pass
    sends, so a pass cut short by its budget still records what it sent: on cancellation — during
    the sends OR while draining the writes — the buffered batch is scheduled and every write keeps
    running unawaited (`_Settler.drain` waits with `asyncio.wait`, which never cancels them). Every
    element is keyed (grant_id, the exact token sent): a token re-registered since is never
    touched.
  * `PUSH_DRY_RUN` (the global notification kill switch): no POST. The due targets are settled
    as `accepted` with `count: false` — stamped (locally and in the DB) without counting towards
    the daily cap — so a dry-run pass does not re-read the same owed wakes every five minutes.

STATE AND FAILURE
  * Process-local `_last_sent`, `_count` and `_pending` — one uvicorn process (§7.4). The DB half
    of the throttle covers a restart; the pending set does not (a lost wake falls back to the
    timeline).
  * `wake` catches ONLY `Exception`: cancellation (the sweeper's budget, a shutdown) propagates.
  * A failed or missing RPC never means "nobody to wake" silently: a WARNING (store) or a
    throttled ERROR naming the migration (schema missing), then the pass ends with nothing sent.
  * Tokens are never logged in full (`push_service.widget_token_label`); every local holding one
    is named `push_token`, which the Sentry frame scrub already redacts.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

from app.config import settings
from app.database import get_supabase
from app.services.auth_session_service import is_schema_missing
from app.services.news_cache_service import MARKET_SCOPE
from app.services.push_service import (
    WIDGET_ACCEPTED,
    WIDGET_BAD,
    WIDGET_GONE,
    WIDGET_NOT_SENT,
    WIDGET_PROVIDER_FAULT,
    WIDGET_RETRY,
    PushService,
    WidgetPushResult,
)
from app.utils.market_hours import ET, SESSION_REGULAR, session_phase

logger = logging.getLogger(__name__)

#: Rows per `widget_push_targets` page (the RPC clamps to the same bound).
TARGET_PAGE_SIZE = 1000
#: Pending grant ids per targets call (each call ships them as one uuid[]).
PENDING_CHUNK = 1000
#: A hard stop on pages per pass, per query family (50,000 rows): a keyset that stops advancing,
#: or a table far beyond today's scale, must never turn one pass into an unbounded read.
MAX_TARGET_PAGES = 50
#: Elements per `widget_push_settle` call (the RPC refuses more).
SETTLE_BATCH = 100
#: A partial batch is flushed once this long has passed since the last flush.
SETTLE_EVERY_SECONDS = 1.0
#: A coalesced wake older than this is dropped: the timeline has long since covered it.
PENDING_TTL_SECONDS = 6 * 3600
#: Memory bound on the pending set; the OLDEST entries go first.
PENDING_MAX = 50_000
#: Local send stamps older than this are forgotten (the DB stamp still gates a restart).
LAST_SENT_KEEP_SECONDS = 2 * 86400
#: Distinct ticker scopes per wake (one sweep admits far fewer).
MAX_SCOPES = 500
_MAX_SCOPE_LEN = 32

#: The ERROR for a missing migration fires at most this often per process; WARNING otherwise.
_SCHEMA_MISSING_LOG_EVERY_SECONDS = 600.0
_last_schema_missing_error = 0.0

#: Settle tasks scheduled while a pass was being cancelled: referenced here so they are never
#: garbage-collected mid-write.
_BACKGROUND: Set["asyncio.Task[Any]"] = set()


class WidgetPushStoreError(Exception):
    """A widget push RPC failed. Never read as "nobody to wake" without a log line."""


class WidgetPushSchemaMissing(WidgetPushStoreError):
    """The holdings-grant migration (`widget_push_targets` / `widget_push_settle`) is not applied,
    or its signature drifted from these callers."""


def _log_schema_missing(operation: str, exc: BaseException) -> None:
    global _last_schema_missing_error
    now = time.monotonic()
    if now - _last_schema_missing_error >= _SCHEMA_MISSING_LOG_EVERY_SECONDS:
        _last_schema_missing_error = now
        logger.error(
            "widget push: %s is not in the database (%s: %s) — no widget wake can be sent or "
            "settled until the holdings-grant migration "
            "(backend/database/migrations/216_widget_holdings_grants.sql) is applied",
            operation, type(exc).__name__, exc,
        )
    else:
        logger.warning("widget push: %s still missing (holdings-grant migration)", operation)


def _valid_scope(scope: str) -> bool:
    """The widget route's ticker rule (`widget._valid_ticker`), restated here so a service never
    imports an endpoint module."""
    if not scope or len(scope) > _MAX_SCOPE_LEN:
        return False
    return all(c.isalnum() or c in ".-^=" for c in scope)


def normalise_scopes(scopes: Any) -> Tuple[List[str], bool, int]:
    """Pure: (ticker scopes, wake-everyone, dropped). Upper-cased, de-duplicated in first-seen
    order, at most `MAX_SCOPES`. `MARKET_SCOPE` becomes the wake-everyone flag; anything that is
    not a string or fails the ticker rule is dropped (counted, never sent to SQL)."""
    tickers: List[str] = []
    seen: Set[str] = set()
    market = False
    dropped = 0
    if isinstance(scopes, (str, bytes)) or not hasattr(scopes, "__iter__"):
        # A bare string would otherwise iterate character by character.
        return [], False, 1 if scopes else 0
    for raw in scopes:
        if not isinstance(raw, str):
            dropped += 1
            continue
        scope = raw.strip()
        if scope == MARKET_SCOPE:
            market = True
            continue
        scope = scope.upper()
        if not _valid_scope(scope):
            dropped += 1
            continue
        if scope in seen:
            continue
        if len(tickers) >= MAX_SCOPES:
            dropped += 1
            continue
        seen.add(scope)
        tickers.append(scope)
    return tickers, market, dropped


def _et_day(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, ET).date().isoformat()


def _epoch(value: Any) -> float:
    """An ISO-8601 timestamp from PostgREST → epoch seconds; anything unreadable → 0.0 (never
    sent), which can only make a device MORE eligible, never stamp a fake recent send."""
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _count(value: Any) -> int:
    """A non-negative integer count, or 0 (a bool, a fraction, NaN or a string is 0)."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return 0
    return value


#: `widget_push_settle`'s `error` is at most this long (the column's CHECK).
_SETTLE_ERROR_MAX = 200


def settle_outcome(result: WidgetPushResult, *, count: bool = True) -> Optional[Dict[str, Any]]:
    """Pure: one `widget_push_settle` element (the migration's header documents the shape), or
    None when there is nothing to write.

    * The token is the EXACT value sent, never re-derived: every settle UPDATE is keyed
      (grant_id, push_token), so a token re-registered since is never touched.
    * `not_sent` and the sender's own `provider_fault` write nothing (the device did nothing
      wrong, and the RPC's `not_sent` arm is a no-op) — skipping them spares a stopped
      environment's hundreds of no-op elements.
    * `apns_ts` is APNs' 410 `timestamp` as an integer of milliseconds (a JSON number), only on
      `gone`; the RPC then clears the token only if it was not re-registered after that moment.
    * `count=False` (dry run) stamps an `accepted` without counting it towards the daily cap.
    """
    if not result.grant_id or not result.push_token:
        return None
    outcome = result.status
    if outcome not in (WIDGET_ACCEPTED, WIDGET_GONE, WIDGET_BAD, WIDGET_RETRY):
        return None
    element: Dict[str, Any] = {
        "grant_id": result.grant_id,
        "token": result.push_token,
        "outcome": outcome,
        "apns_ts": result.apns_timestamp_ms if outcome == WIDGET_GONE else None,
    }
    if outcome == WIDGET_ACCEPTED:
        element["count"] = bool(count)
    elif result.reason:
        element["error"] = result.reason[:_SETTLE_ERROR_MAX]
    return element


class _Settler:
    """Collects settle elements and writes them in batches of ≤ `SETTLE_BATCH`, flushing a
    partial batch once `SETTLE_EVERY_SECONDS` have passed. Writes run as tasks so the sends never
    wait on the database; `drain` awaits them, `abandon` (on cancellation) leaves them running."""

    def __init__(self, write: Callable[[List[Dict[str, Any]]], Any],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._write = write
        self._clock = clock
        self._buf: List[Dict[str, Any]] = []
        self._tasks: List["asyncio.Task[Any]"] = []
        self._last_flush = clock()
        self.batches = 0
        self.elements = 0

    def add(self, element: Dict[str, Any]) -> None:
        self._buf.append(element)
        if len(self._buf) >= SETTLE_BATCH or self._clock() - self._last_flush >= SETTLE_EVERY_SECONDS:
            self.flush()

    def flush(self) -> None:
        while self._buf:
            batch, self._buf = self._buf[:SETTLE_BATCH], self._buf[SETTLE_BATCH:]
            self.batches += 1
            self.elements += len(batch)
            self._tasks.append(asyncio.ensure_future(self._write(batch)))
        self._last_flush = self._clock()

    async def drain(self) -> None:
        """Flush, then wait for every write. A cancellation here (the sweeper's pass budget ran
        out while the writes were in flight) hands them to `abandon` and propagates.

        `asyncio.wait`, never `gather`: a cancelled `gather` cancels its children, so a pass cut
        while draining dropped the very writes its budget exists to protect — a settle task that
        had not started never ran its RPC, and an executor job still queued was cancelled — losing
        the DB interval stamps, the ET-day counts and the 410 token clears (review 2026-11-27)."""
        self.flush()
        waiting = [task for task in self._tasks if not task.done()]
        if waiting:
            try:
                await asyncio.wait(waiting)
            except asyncio.CancelledError:
                self.abandon()
                raise
        for task in self._tasks:
            # `_write_settle` never raises; another writer that did is logged, never dropped.
            if task.done() and not task.cancelled() and task.exception() is not None:
                exc = task.exception()
                logger.warning("widget push: a settle write raised (%s: %s)",
                               type(exc).__name__, exc)

    def abandon(self) -> None:
        """The pass is being cancelled: schedule what is buffered and keep every write alive."""
        self.flush()
        for task in self._tasks:
            if not task.done():
                _BACKGROUND.add(task)
                task.add_done_callback(_BACKGROUND.discard)


class WidgetPushService:
    """Process-local throttle + target resolution + settle for widget wakes. One per process."""

    def __init__(
        self,
        *,
        push_factory: Optional[Callable[[], Any]] = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._push_factory = push_factory
        self._clock = clock
        self._last_sent: Dict[str, float] = {}   # grant_id → epoch seconds of our last send
        self._count: Dict[str, int] = {}         # grant_id → sends counted on `_count_day`
        self._count_day: Optional[str] = None    # ET date
        self._pending: Dict[str, float] = {}     # grant_id → epoch seconds first marked
        self._running = False
        self._warned_unconfigured = False
        self._warned_grants_off = False   # one WARNING per grants-off period
        # Send order within a pass is shuffled AFTER the per-pass cut (owed wakes still win the
        # cut): the sender's topic probe reads an environment's first three devices, and a
        # predictable order would let someone who owns those three grants stop the environment.
        self._rng = random.Random()

    # ── I/O seams ──────────────────────────────────────────────────────────────────

    def _push(self) -> Any:
        return (self._push_factory or PushService)()

    @staticmethod
    def _rpc_sync(name: str, params: Dict[str, Any]) -> Any:
        return get_supabase().rpc(name, params).execute().data

    async def _rpc(self, name: str, params: Dict[str, Any]) -> Any:
        try:
            return await asyncio.to_thread(self._rpc_sync, name, params)
        except Exception as e:
            if is_schema_missing(e):
                _log_schema_missing(name, e)
                raise WidgetPushSchemaMissing(f"{name}: {type(e).__name__}: {e}") from e
            raise WidgetPushStoreError(f"{name}: {type(e).__name__}: {e}") from e

    async def _write_settle(self, batch: List[Dict[str, Any]]) -> None:
        """One settle call; never raises (a failed write costs DB-side counters, never a send).
        A batch the RPC could not match or read is counted in its answer and logged here."""
        try:
            answer = await self._rpc("widget_push_settle", {"p_outcomes": batch})
        except WidgetPushSchemaMissing:
            return  # already logged, throttled
        except Exception as e:
            logger.warning(
                "widget push: settle of %d outcome(s) failed (%s: %s) — the DB throttle and "
                "token clears for them are lost; the local throttle still holds",
                len(batch), type(e).__name__, e,
            )
            return
        if not isinstance(answer, dict) or answer.get("status") != "settled":
            logger.warning("widget push: settle answered %r for %d outcome(s)",
                           str(answer)[:200], len(batch))
            return
        skipped = _count(answer.get("skipped"))
        if skipped:
            # A shape the RPC refused: a bug on this side, never a client's.
            logger.error("widget push: settle skipped %d of %d malformed outcome(s)",
                         skipped, len(batch))
        cleared = _count(answer.get("cleared_bad"))
        if cleared:
            logger.info("widget push: %d token(s) cleared after 24 h of BadDeviceToken / "
                        "DeviceTokenNotForTopic", cleared)

    async def _read_targets(
        self, *, scopes: List[str], wake_all: bool, grant_ids: Optional[List[str]]
    ) -> Tuple[List[Dict[str, Any]], int, bool]:
        """Every page of `widget_push_targets` for one query: (valid rows, malformed rows,
        complete). `complete` is False when the read stopped early (page bound, stuck keyset), so
        the caller never reads "not returned" as "dead". Raises `WidgetPushStoreError` on a failed
        read (the caller sends nothing)."""
        rows: List[Dict[str, Any]] = []
        malformed = 0
        after: Optional[str] = None
        for page in range(MAX_TARGET_PAGES):
            data = await self._rpc("widget_push_targets", {
                "p_scopes": scopes,
                "p_all": wake_all,
                "p_grant_ids": grant_ids,
                "p_after": after,
                "p_limit": TARGET_PAGE_SIZE,
            })
            if data is None:
                data = []
            if not isinstance(data, list):
                raise WidgetPushStoreError(
                    f"widget_push_targets answered {type(data).__name__}, not rows"
                )
            last_id: Optional[str] = None
            for row in data:
                grant_id = row.get("grant_id") if isinstance(row, dict) else None
                if isinstance(grant_id, str) and grant_id:
                    last_id = grant_id
                if (not isinstance(row, dict) or not isinstance(grant_id, str) or not grant_id
                        or not isinstance(row.get("push_token"), str)
                        or not isinstance(row.get("push_environment"), str)):
                    malformed += 1
                    continue
                rows.append(row)
            if len(data) < TARGET_PAGE_SIZE:
                return rows, malformed, True
            if last_id is None or (after is not None and last_id <= after):
                logger.warning(
                    "widget push: targets keyset did not advance after page %d (after=%s) — "
                    "stopping the read here", page + 1, after,
                )
                return rows, malformed, False
            after = last_id
        logger.warning(
            "widget push: targets read stopped at %d pages (%d rows) — the rest are not woken "
            "this pass", MAX_TARGET_PAGES, len(rows),
        )
        return rows, malformed, False

    # ── the wake ───────────────────────────────────────────────────────────────────

    async def wake(self, scopes: Sequence[str], phase: Optional[str] = None) -> Dict[str, Any]:
        """Wake the widgets a pass's stored cards concern. Never raises an `Exception`;
        cancellation propagates (the caller's time budget, a shutdown)."""
        try:
            return await self._wake(scopes, phase)
        except Exception as e:
            logger.warning(
                "widget push: wake failed (%s: %s) — cards are stored; tiles fall back to "
                "their timeline", type(e).__name__, e, exc_info=True,
            )
            return {"error": type(e).__name__}

    async def _wake(self, scopes: Sequence[str], phase: Optional[str]) -> Dict[str, Any]:
        if not settings.WIDGET_PUSH_ENABLED:
            return {"skipped": "off"}
        if not settings.WIDGET_HOLDINGS_GRANTS_ENABLED:
            # Grants off (an incident, or step 1 of migration 216's teardown) with push still on:
            # every woken tile would fetch and get 503. No read, no send; owed wakes dropped (the
            # timeline covers them once grants are back on).
            cleared = len(self._pending)
            self._pending.clear()
            if not self._warned_grants_off:
                self._warned_grants_off = True
                logger.warning(
                    "widget push: WIDGET_PUSH_ENABLED is on but WIDGET_HOLDINGS_GRANTS_ENABLED is "
                    "off — no widget wake is sent while the holdings route answers 503 (%d "
                    "pending wake(s) dropped)", cleared,
                )
            return {"skipped": "grants_off", "pending_cleared": cleared}
        self._warned_grants_off = False
        if self._running:
            # A previous wake outlived its budget (a cancellation it did not honour, a stuck
            # read). Never two at once: the throttle state is not built for it.
            logger.warning("widget push: the previous wake is still running — skipping this "
                           "pass (pending wakes kept)")
            return {"skipped": "busy"}
        self._running = True
        try:
            return await self._wake_locked(scopes, phase)
        finally:
            self._running = False

    async def _wake_locked(self, scopes: Sequence[str], phase: Optional[str]) -> Dict[str, Any]:
        started = time.monotonic()
        push = self._push()
        if not push.enabled:
            if not self._warned_unconfigured:
                self._warned_unconfigured = True
                logger.warning("widget push: WIDGET_PUSH_ENABLED is on but APNs is not "
                               "configured (APNS_*) — no widget wake will be sent")
            return {"skipped": "apns_unconfigured"}

        # The pass's phase is read at its START; the live one guards a pass that straddled 09:30.
        if phase == SESSION_REGULAR or session_phase() == SESSION_REGULAR:
            cleared = len(self._pending)
            self._pending.clear()
            if cleared:
                logger.info("widget push: regular session — %d pending wake(s) dropped (the "
                            "timeline reloads every 20 min now)", cleared)
            return {"skipped": "regular_session", "pending_cleared": cleared}

        now = self._clock()
        tickers, wake_all, dropped_scopes = normalise_scopes(scopes)
        if dropped_scopes:
            logger.warning("widget push: %d scope(s) dropped (not a ticker, or past %d)",
                           dropped_scopes, MAX_SCOPES)
        self._prune(now)
        if not tickers and not wake_all and not self._pending:
            return {"skipped": "nothing"}

        # 1. Targets. Scope wakes and pending flushes are separate reads; with `wake_all` the
        #    first read already returns every live target, pending included.
        targets: Dict[str, Dict[str, Any]] = {}
        malformed = 0
        pending_ids = [] if wake_all else sorted(self._pending)
        checked: Set[str] = set()   # pending ids a COMPLETE read answered for
        try:
            if tickers or wake_all:
                rows, bad_rows, complete = await self._read_targets(
                    scopes=tickers, wake_all=wake_all, grant_ids=None)
                malformed += bad_rows
                for row in rows:
                    targets.setdefault(row["grant_id"], row)
                if wake_all and complete:
                    checked.update(self._pending)
            for start in range(0, len(pending_ids), PENDING_CHUNK):
                chunk = pending_ids[start:start + PENDING_CHUNK]
                rows, bad_rows, complete = await self._read_targets(
                    scopes=[], wake_all=False, grant_ids=chunk)
                malformed += bad_rows
                for row in rows:
                    targets.setdefault(row["grant_id"], row)
                if complete:
                    checked.update(chunk)
        except WidgetPushSchemaMissing:
            return {"error": "schema_missing"}
        except WidgetPushStoreError as e:
            logger.warning("widget push: targets read failed (%s) — nothing sent this pass; "
                           "%d pending wake(s) kept", e, len(self._pending))
            return {"error": "targets_read_failed"}

        # A pending grant the liveness predicate no longer returns (revoked, expired, evicted,
        # token cleared) is dropped — never pushed on the strength of an old mark.
        for grant_id in checked - set(targets):
            self._pending.pop(grant_id, None)

        # 2. Gate: the ET-day cap, then the interval, each on max(local, DB).
        today = _et_day(now)
        if self._count_day != today:
            self._count_day = today
            self._count.clear()
        interval = float(settings.WIDGET_PUSH_MIN_INTERVAL_SECONDS)
        cap = int(settings.WIDGET_PUSH_DAILY_CAP)
        due: List[Dict[str, Any]] = []
        coalesced = capped = 0
        for grant_id, row in targets.items():
            db_count = (_count(row.get("push_sent_count"))
                        if str(row.get("push_sent_day") or "")[:10] == today else 0)
            if max(self._count.get(grant_id, 0), db_count) >= cap:
                capped += 1
                self._pending.pop(grant_id, None)
                continue
            # A stamp in the future (clock skew) counts as "just sent": one interval, never more.
            last = min(max(self._last_sent.get(grant_id, 0.0),
                           _epoch(row.get("push_last_sent_at"))), now)
            if now - last < interval:
                coalesced += 1
                self._pending.setdefault(grant_id, now)
                continue
            due.append(row)

        # Owed wakes first (oldest mark first), then this pass's, in the read's grant order.
        due.sort(key=lambda r: (r["grant_id"] not in self._pending,
                                self._pending.get(r["grant_id"], 0.0)))
        limit = int(settings.WIDGET_PUSH_MAX_SENDS_PER_PASS)
        deferred_rows = due[limit:]
        due = due[:limit]
        for row in deferred_rows:
            self._pending.setdefault(row["grant_id"], now)
        self._rng.shuffle(due)

        counts: Dict[str, int] = {WIDGET_ACCEPTED: 0, WIDGET_GONE: 0, WIDGET_BAD: 0,
                                  WIDGET_RETRY: 0, WIDGET_NOT_SENT: 0}
        dry_run = bool(settings.PUSH_DRY_RUN)
        settle_batches = 0
        if due and dry_run:
            # No POST. Stamped as accepted WITHOUT counting, locally and in the DB, so a dry-run
            # pass does not re-read the same owed wakes every five minutes.
            settler = _Settler(self._write_settle)
            for row in due:
                self._last_sent[row["grant_id"]] = now
                self._pending.pop(row["grant_id"], None)
                element = settle_outcome(WidgetPushResult(
                    row["grant_id"], row["push_token"], row["push_environment"], WIDGET_ACCEPTED,
                ), count=False)
                if element is not None:
                    settler.add(element)
            await settler.drain()   # a cancellation here leaves every write running (`drain`)
            settle_batches = settler.batches
            logger.info("widget push DRY RUN: would wake %d device(s)", len(due))
        elif due:
            settle_batches = await self._send_and_settle(push, due, now, counts)

        self._bound_pending()
        summary = {
            "phase": phase or "-",
            "scopes": len(tickers),
            "market": wake_all,
            "targets": len(targets),
            "due": len(due),
            "sent": counts[WIDGET_ACCEPTED] + counts[WIDGET_GONE] + counts[WIDGET_BAD]
            + counts[WIDGET_RETRY],
            "accepted": counts[WIDGET_ACCEPTED],
            "gone": counts[WIDGET_GONE],
            "bad": counts[WIDGET_BAD],
            "retry": counts[WIDGET_RETRY],
            "not_sent": counts[WIDGET_NOT_SENT],
            "coalesced": coalesced,
            "capped": capped,
            "deferred": len(deferred_rows),
            "pending": len(self._pending),
            "malformed": malformed,
            "settle_batches": settle_batches,
            "dry_run": dry_run,
        }
        logger.info(
            "widget push: phase=%s scopes=%d market=%s targets=%d due=%d sent=%d accepted=%d "
            "gone=%d bad=%d retry=%d not_sent=%d coalesced=%d capped=%d deferred=%d pending=%d "
            "malformed=%d settle_batches=%d dry_run=%s in %.2fs",
            summary["phase"], summary["scopes"], wake_all, summary["targets"], summary["due"],
            summary["sent"], summary["accepted"], summary["gone"], summary["bad"],
            summary["retry"], summary["not_sent"], coalesced, capped, summary["deferred"],
            summary["pending"], malformed, settle_batches, dry_run, time.monotonic() - started,
        )
        return summary

    async def _send_and_settle(self, push: Any, due: List[Dict[str, Any]], now: float,
                               counts: Dict[str, int]) -> int:
        """Send `due`, settling each outcome locally at once and in the DB in batches. A device
        with no outcome when the send ends (cancelled by the pass budget) stays pending."""
        settler = _Settler(self._write_settle)
        settled: Set[str] = set()

        def on_result(result: WidgetPushResult) -> None:
            settled.add(result.grant_id)
            status = WIDGET_NOT_SENT if result.status == WIDGET_PROVIDER_FAULT else result.status
            counts[status] = counts.get(status, 0) + 1
            self._settle_local(result, now)
            element = settle_outcome(result)
            if element is not None:
                settler.add(element)

        devices = [{"grant_id": r["grant_id"], "push_token": r["push_token"],
                    "push_environment": r["push_environment"]} for r in due]
        try:
            await push.send_widget_pushes(devices, on_result=on_result)
        except asyncio.CancelledError:
            settler.abandon()
            self._keep_unsettled(due, settled, now)
            raise
        except Exception as e:  # the sender never raises; a bug there still settles what it sent
            logger.warning("widget push: sender raised (%s: %s) — outcomes so far are settled",
                           type(e).__name__, e, exc_info=True)
        self._keep_unsettled(due, settled, now)
        await settler.drain()   # a cancellation here leaves every write running (`drain`)
        return settler.batches

    def _keep_unsettled(self, due: List[Dict[str, Any]], settled: Set[str], now: float) -> None:
        for row in due:
            if row["grant_id"] not in settled:
                self._pending.setdefault(row["grant_id"], now)

    def _settle_local(self, result: WidgetPushResult, now: float) -> None:
        grant_id = result.grant_id
        if not grant_id:
            return
        if result.status in (WIDGET_ACCEPTED, WIDGET_BAD):
            # A bad token counts too: an account cannot keep a garbage token outside the cap.
            self._last_sent[grant_id] = now
            self._count[grant_id] = self._count.get(grant_id, 0) + 1
            self._pending.pop(grant_id, None)
        elif result.status == WIDGET_GONE:
            self._pending.pop(grant_id, None)
        elif result.status == WIDGET_RETRY:
            if result.reason.startswith("429"):
                # APNs: too many requests for THIS device — wait an interval, still owed.
                self._last_sent[grant_id] = now
            self._pending.setdefault(grant_id, now)
        elif result.reason.startswith("invalid_device"):
            self._pending.pop(grant_id, None)
        else:  # not_sent / provider_fault: our side stopped; the wake is still owed
            self._pending.setdefault(grant_id, now)

    def _prune(self, now: float) -> None:
        for grant_id, marked in list(self._pending.items()):
            if now - marked > PENDING_TTL_SECONDS:
                del self._pending[grant_id]
        keep = max(LAST_SENT_KEEP_SECONDS, float(settings.WIDGET_PUSH_MIN_INTERVAL_SECONDS))
        for grant_id, sent in list(self._last_sent.items()):
            if now - sent > keep:
                del self._last_sent[grant_id]

    def _bound_pending(self) -> None:
        excess = len(self._pending) - PENDING_MAX
        if excess <= 0:
            return
        for grant_id, _ in sorted(self._pending.items(), key=lambda kv: kv[1])[:excess]:
            del self._pending[grant_id]
        logger.warning("widget push: pending set over %d — dropped the %d oldest wake(s)",
                       PENDING_MAX, excess)


_service: Optional[WidgetPushService] = None


def get_widget_push_service() -> WidgetPushService:
    global _service
    if _service is None:
        _service = WidgetPushService()
    return _service
