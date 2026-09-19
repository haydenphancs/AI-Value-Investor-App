"""Quiet-hours flush: the badge counts the row it announces, and a shutdown anywhere after
the claim returns the batch.

Three findings from the 2026-09-16 deep-check, all in `flush_deferred` / `_deliver`:

* **F17-1 — badge one too LOW.** The flush built the recipient with `badge=unread` on the
  premise that the deferred row was "already in `unread`". It never was:
  `unread_counts_bulk` counts `push_state = 'sent'` rows only, and the claim RPC flips the
  row to `pending`, so every flushed push carried the user's unread count EXCLUDING itself.
  A new user's first deferred alert went out with `aps.badge: 0` — iOS treats that as
  CLEAR — and k rows for one user in one batch all carried the same stale number.

* **F17-3 / F22-2 — cancel window after the claim.** The shutdown arm wrapped only the
  per-row loop. Three `to_thread` awaits ran between the claim (which has COMMITTED the
  `deferred → pending` flip) and the `try:` — `_devices_bulk`, `unread_counts_bulk`,
  `_preferences_bulk` — and a redeploy landing in any of them left the whole batch at
  `pending` forever. The claim await itself had the same shape: the RPC finishes in its
  thread, the result is discarded, and the rows it claimed are unknown to the arm.

* **F17-4 — the `sent` stamp was cancellable while QUEUED.** `asyncio.to_thread` is a plain
  executor future; a task cancel on a not-yet-started work item discards it. With the
  default executor saturated at the instant of a redeploy, APNs had accepted the push, the
  stamp never ran, the cancel arm returned the row to `deferred`, and the next process
  re-sent it. The fix shields the stamp; this file is the test it landed without.

No Supabase, no APNs. The existing `test_quiet_hours_flush_shutdown.py` cancels at row
index 2 (inside the loop) and drives `mark_state` sequentially, so it is green while every
one of these is live.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

import app.services.push_dispatch_service as pds
from app.services.notification_kinds import KIND_EARNINGS_UPCOMING, KIND_TICKER_MOVE
from app.services.push_dispatch_service import (
    STATE_DEFERRED,
    STATE_FAILED,
    STATE_PENDING,
    STATE_SENT,
    PushDispatchService,
    _Recipient,
)
from app.services.push_service import PushOutcome


# ── fixtures (plain assignments on a throwaway instance; nothing to restore) ─────────


class _FakePush:
    """`accepted_for(user_id)` decides per user so one batch can mix outcomes."""

    def __init__(self, accepted_for=None):
        self.enabled = True
        self.calls = []
        self._accepted_for = accepted_for or (lambda uid: 1)

    async def send_to_user(self, user_id, **kw):
        self.calls.append({"user_id": user_id, **kw})
        n = self._accepted_for(user_id)
        return PushOutcome(attempted=1, accepted=n)


def _svc(push=None) -> PushDispatchService:
    svc = object.__new__(PushDispatchService)
    svc._push = push or _FakePush()
    svc.supabase = None
    return svc


def _row(uid: str, key: str, kind: str = KIND_TICKER_MOVE) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    return {
        "user_id": uid, "dedup_key": key, "kind": kind, "attempts": 1,
        "deliver_after": now_iso, "claimed_at": now_iso,
        "title": "T", "body": "B", "route": {"ticker": "NVDA"},
    }


def _wire(svc, rows, *, unread=None, devices=None, marks=None):
    """The three bulk reads + a recording `mark_state`, all as instance attributes."""
    svc._claim_due = lambda limit: rows
    svc._devices_bulk = lambda ids: (
        devices if devices is not None
        else {u: [{"token": "t", "environment": "sandbox"}] for u in ids}
    )
    svc.unread_counts_bulk = lambda ids: dict(unread or {})
    svc._preferences_bulk = lambda ids: {u: {} for u in ids}
    svc._category_counts_bulk = lambda ids, category, cutoffs: {}
    if marks is not None:
        svc.mark_state = (
            lambda uid, key, state, error=None, sent=False, only_if_state=None:
                marks.append((uid, key, state, only_if_state))
        )
    else:
        svc.mark_state = lambda *a, **k: None
    return svc


# ═══ F17-1 — the badge INCLUDES the row it announces ════════════════════════════════


@pytest.mark.asyncio
async def test_flush_badge_is_never_zero_for_a_first_ever_deferred_push():
    """New user, no unread sent rows, one deferred alert: `aps.badge` must be 1, not 0.
    Zero is not "no badge" to iOS — it CLEARS the icon while the inbox shows 1 unread."""
    push = _FakePush()
    svc = _wire(_svc(push), [_row("u1", "k1")], unread={})
    stats = await svc.flush_deferred()
    assert stats["sent"] == 1
    assert push.calls[0]["badge"] == 1, push.calls[0]["badge"]


@pytest.mark.asyncio
async def test_flush_badge_counts_up_across_a_users_rows_in_one_batch():
    """Three deferred rows for one user with nothing unread: the icon must end at 3, so the
    pushes carry 1, 2, 3 — not 0, 0, 0 (the up-front count cannot see this batch)."""
    push = _FakePush()
    rows = [_row("u1", f"k{i}") for i in range(3)]
    svc = _wire(_svc(push), rows, unread={"u1": 0})
    await svc.flush_deferred()
    assert [c["badge"] for c in push.calls] == [1, 2, 3]


@pytest.mark.asyncio
async def test_flush_badge_starts_from_the_delivered_unread_count():
    """Two already-delivered unread rows + two flushed: 3 then 4."""
    push = _FakePush()
    svc = _wire(_svc(push), [_row("u1", "a"), _row("u1", "b")], unread={"u1": 2})
    await svc.flush_deferred()
    assert [c["badge"] for c in push.calls] == [3, 4]


@pytest.mark.asyncio
async def test_flush_badge_counters_are_per_user():
    """Interleaved users must not share the increment."""
    push = _FakePush()
    rows = [_row("u1", "a"), _row("u2", "x"), _row("u1", "b"), _row("u2", "y")]
    svc = _wire(_svc(push), rows, unread={"u1": 5, "u2": 0})
    await svc.flush_deferred()
    got = [(c["user_id"], c["badge"]) for c in push.calls]
    assert got == [("u1", 6), ("u2", 1), ("u1", 7), ("u2", 2)], got


@pytest.mark.asyncio
async def test_a_no_device_row_between_two_deliveries_does_not_bump_the_badge():
    """A row that was never shown must not inflate the badge of the rows after it."""
    push = _FakePush()
    rows = [_row("u1", "a"), _row("u2", "nodev"), _row("u1", "b")]
    devices = {"u1": [{"token": "t", "environment": "sandbox"}], "u2": []}
    svc = _wire(_svc(push), rows, unread={"u1": 0, "u2": 0}, devices=devices)
    stats = await svc.flush_deferred()
    assert stats["no_device"] == 1
    assert [(c["user_id"], c["badge"]) for c in push.calls] == [("u1", 1), ("u1", 2)]


@pytest.mark.asyncio
async def test_a_rejected_delivery_does_not_bump_the_badge_of_the_next_row():
    """APNs accepted no device for row 1: it is `failed`, not on the phone, so row 2 for the
    same user still carries `unread + 1`."""
    outcomes = iter([0, 1])
    push = _FakePush(accepted_for=lambda uid: next(outcomes))
    svc = _wire(_svc(push), [_row("u1", "a"), _row("u1", "b")], unread={"u1": 0})
    stats = await svc.flush_deferred()
    assert stats["failed"] == 1 and stats["sent"] == 1
    assert [c["badge"] for c in push.calls] == [1, 1]


@pytest.mark.asyncio
async def test_a_suppressed_row_does_not_bump_the_badge():
    """Category turned off overnight for row 1 (same user): no push, no increment."""
    push = _FakePush()
    rows = [_row("u1", "a", KIND_EARNINGS_UPCOMING), _row("u1", "b", KIND_TICKER_MOVE)]
    svc = _wire(_svc(push), rows, unread={"u1": 0})
    svc._preferences_bulk = lambda ids: {"u1": {"notify_earnings_upcoming": False}}
    stats = await svc.flush_deferred()
    assert stats["suppressed"] == 1 and stats["sent"] == 1, stats
    assert [c["badge"] for c in push.calls] == [1]


@pytest.mark.asyncio
async def test_a_missing_unread_entry_defaults_to_zero_not_a_crash():
    """`unread_counts_bulk` omits a user when its read fails for that chunk."""
    push = _FakePush()
    svc = _wire(_svc(push), [_row("u9", "k")], unread={"someone_else": 4})
    await svc.flush_deferred()
    assert push.calls[0]["badge"] == 1


def test_the_flush_recipient_badge_is_unread_plus_one_plus_delivered_now():
    """Source guard, comment-stripped: the `+ 1` and the per-batch counter are both in the
    recipient construction. `badge=unread.get(uid, 0)` alone is the bug."""
    src = inspect.getsource(PushDispatchService.flush_deferred)
    code = "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())
    lines = [l for l in code.splitlines() if "badge=" in l]
    assert lines, "the flush no longer sets an explicit badge"
    expr = lines[0].split("badge=", 1)[1].replace(" ", "")
    assert expr.startswith("unread.get(uid,0)+1+delivered_now"), expr


# ═══ F17-3 / F22-2 — a cancel anywhere after the claim returns the batch ════════════


def _cancel_in(name):
    def _raise(*a, **k):
        raise asyncio.CancelledError()
    return _raise


@pytest.mark.asyncio
@pytest.mark.parametrize("injection_point", ["_devices_bulk", "unread_counts_bulk", "_preferences_bulk"])
async def test_a_cancel_during_a_bulk_read_returns_every_claimed_row(injection_point):
    """The claim has already committed `pending`; the three reads run after it and BEFORE
    the loop, so the arm must cover them. Today's loop-only injection cannot see this."""
    marks = []
    rows = [_row(f"u{i}", f"k{i}") for i in range(5)]
    svc = _wire(_svc(), rows, unread={}, marks=marks)
    setattr(svc, injection_point, _cancel_in(injection_point))

    with pytest.raises(asyncio.CancelledError):
        await svc.flush_deferred()

    returned = {(u, k, only) for u, k, st, only in marks if st == STATE_DEFERRED}
    expected = {(r["user_id"], r["dedup_key"], STATE_PENDING) for r in rows}
    assert returned == expected, f"{injection_point}: {sorted(returned)}"
    # And nothing was stamped terminally: a redeploy is not the row's fault.
    assert not [m for m in marks if m[2] in (STATE_FAILED, STATE_SENT)]


@pytest.mark.asyncio
async def test_a_cancel_during_a_bulk_read_skips_unaddressable_rows_without_crashing():
    """A row with no user_id / dedup_key cannot be re-marked; it must not take the arm down
    with it (a NameError or KeyError there re-strands every other row)."""
    marks = []
    rows = [_row("u1", "k1"), {"id": "orphan", "kind": KIND_TICKER_MOVE}, _row("u2", "k2")]
    svc = _wire(_svc(), rows, unread={}, marks=marks)
    svc._devices_bulk = _cancel_in("_devices_bulk")
    with pytest.raises(asyncio.CancelledError):
        await svc.flush_deferred()
    assert {(u, k) for u, k, st, _ in marks if st == STATE_DEFERRED} == {("u1", "k1"), ("u2", "k2")}


@pytest.mark.asyncio
async def test_an_empty_claim_short_circuits_before_any_bulk_read():
    """Zero rows is the overwhelmingly common cycle; it must not touch the bulk reads."""
    svc = _wire(_svc(), [], unread={})
    svc._devices_bulk = _cancel_in("_devices_bulk")   # would raise if reached
    stats = await svc.flush_deferred()
    assert stats["claimed"] == 0


@pytest.mark.asyncio
async def test_a_real_cancel_while_the_claim_rpc_is_in_flight_returns_what_it_claimed():
    """The RPC commits inside the thread. `Task.cancel()` while it runs used to raise before
    `rows` was bound, so the claimed rows were unknown and stranded at `pending`."""
    marks = []
    rows = [_row("u1", "k1"), _row("u2", "k2")]
    entered = threading.Event()
    release = threading.Event()

    def _claim(limit):
        entered.set()
        assert release.wait(5), "test harness: claim thread was never released"
        return rows

    svc = _wire(_svc(), rows, unread={}, marks=marks)
    svc._claim_due = _claim

    task = asyncio.ensure_future(svc.flush_deferred())
    await asyncio.to_thread(entered.wait, 5)       # the RPC thread is now in flight
    task.cancel()
    await asyncio.sleep(0)                          # let the cancel land in the coroutine
    release.set()                                  # ...and THEN the RPC "commits"
    with pytest.raises(asyncio.CancelledError):
        await task

    returned = {(u, k, only) for u, k, st, only in marks if st == STATE_DEFERRED}
    assert returned == {("u1", "k1", STATE_PENDING), ("u2", "k2", STATE_PENDING)}, marks


@pytest.mark.asyncio
async def test_a_cancel_during_a_claim_that_returns_nothing_marks_nothing():
    marks = []
    entered = threading.Event()
    release = threading.Event()

    def _claim(limit):
        entered.set()
        release.wait(5)
        return []

    svc = _wire(_svc(), [], unread={}, marks=marks)
    svc._claim_due = _claim
    task = asyncio.ensure_future(svc.flush_deferred())
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert marks == []


@pytest.mark.asyncio
async def test_a_claim_that_never_settles_does_not_hold_the_shutdown_open(caplog):
    """Bounded: a wedged connection must not pin the lifespan teardown. Logged as an ERROR
    naming the possible stranding, because nothing else will."""
    marks = []
    entered = threading.Event()
    release = threading.Event()

    def _claim(limit):
        entered.set()
        release.wait(10)
        return [_row("u1", "k1")]

    svc = _wire(_svc(), [], unread={}, marks=marks)
    svc._claim_due = _claim
    svc._CLAIM_SETTLE_SECONDS = 0.05
    task = asyncio.ensure_future(svc.flush_deferred())
    await asyncio.to_thread(entered.wait, 5)
    task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
    finally:
        release.set()
    assert marks == []
    assert any("STRANDED" in r.getMessage() and r.levelname == "ERROR" for r in caplog.records)


def _flush_code() -> str:
    src = inspect.getsource(PushDispatchService.flush_deferred)
    return "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())


def test_the_guarded_region_opens_before_the_bulk_reads():
    """Source guard: in the comment-stripped body, the `try:` that owns the CancelledError
    arm precedes every one of the three bulk reads. A later refactor that hoists a read
    above it reopens the exact window this file exists for."""
    lines = _flush_code().splitlines()

    def _indent(line: str) -> int:
        return len(line) - len(line.lstrip())

    arm = next(i for i, l in enumerate(lines) if "except asyncio.CancelledError" in l)
    # The `try:` that owns the arm is the LAST `try:` at the arm's own indentation before it.
    owners = [i for i in range(arm)
              if lines[i].strip() == "try:" and _indent(lines[i]) == _indent(lines[arm])]
    assert owners, "no try: owns the CancelledError arm"
    owner = owners[-1]
    for read in ("self._devices_bulk", "self.unread_counts_bulk", "self._preferences_bulk"):
        at = next(i for i, l in enumerate(lines) if read in l)
        assert owner < at < arm, f"{read} runs OUTSIDE the shutdown-guarded region"


def test_the_flush_does_not_await_the_claim_unguarded():
    """The claim goes through `_claim_due_guarded`, never a bare `to_thread(self._claim_due`."""
    code = _flush_code()
    assert "self._claim_due_guarded(" in code
    assert "to_thread(self._claim_due," not in code and "to_thread(self._claim_due)" not in code


def test_the_claim_guard_keeps_the_future_and_returns_rows_on_cancel():
    src = inspect.getsource(PushDispatchService._claim_due_guarded)
    code = "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())
    assert "asyncio.shield(claim)" in code
    arm = code[code.index("except asyncio.CancelledError"):]
    assert "wait_for(claim" in arm, "a cancel must wait (bounded) for the RPC's result"
    assert "_return_to_deferred" in arm and "asyncio.shield" in arm
    assert arm.rstrip().endswith("raise"), "the cancellation must propagate after the requeue"


def test_return_to_deferred_is_conditional_on_pending_and_spends_no_attempt():
    svc = _svc()
    seen = []
    svc.mark_state = (
        lambda uid, key, state, error=None, sent=False, only_if_state=None:
            seen.append((uid, key, state, sent, only_if_state))
    )
    n = svc._return_to_deferred(
        [_row("u1", "k1"), {"user_id": "", "dedup_key": "k"}, {"user_id": "u3"}, _row("u4", "k4")],
        reason="r",
    )
    assert n == 2
    assert seen == [("u1", "k1", STATE_DEFERRED, False, STATE_PENDING),
                    ("u4", "k4", STATE_DEFERRED, False, STATE_PENDING)]
    # AST, not text: the docstring legitimately NAMES `_requeue_or_fail` to say why it
    # is not used, so a substring scan would fail on prose.
    import ast, textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(PushDispatchService._return_to_deferred)))
    called = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "_requeue_or_fail" not in called and "mark_state" in called, called


# ═══ F17-4 — the outcome stamp survives a cancel while QUEUED ═══════════════════════


async def _cancel_while_stamp_is_queued(accepted: int):
    """Saturate a 1-worker executor, run `_deliver` until the stamp is queued behind the
    blocker, cancel the task, then release the worker. Returns the recorded stamps."""
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
    blocker = threading.Event()
    apns_done = asyncio.Event()
    stamps = []

    class _Push:
        enabled = True

        async def send_to_user(self, user_id, **kw):
            apns_done.set()
            return PushOutcome(attempted=1, accepted=accepted)

    svc = _svc(_Push())
    svc.mark_state = (
        lambda uid, key, state, error=None, sent=False, only_if_state=None:
            stamps.append((state, sent))
    )
    hold = loop.run_in_executor(None, blocker.wait)      # occupies the only worker
    task = asyncio.ensure_future(svc._deliver(
        _Recipient(user_id="u1", devices=[{"token": "t", "environment": "sandbox"}]),
        pds.get_kind(KIND_TICKER_MOVE),
        title="T", body="B", dedup_key="k", route={},
    ))
    await apns_done.wait()
    for _ in range(5):                                   # let the stamp be SUBMITTED
        await asyncio.sleep(0)
    assert stamps == [], "harness: the stamp ran before the cancel — executor not blocked"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    blocker.set()
    await hold
    for _ in range(50):                                  # drain the queued item
        if stamps:
            break
        await asyncio.sleep(0.01)
    return stamps


@pytest.mark.asyncio
async def test_a_cancel_while_the_sent_stamp_is_queued_still_stamps_sent():
    """APNs accepted; the only thing left was the stamp, queued behind a saturated executor.
    Unshielded, `Task.cancel()` discards a queued work item and the row stays `pending` →
    the cancel arm writes `deferred` → the next process re-sends the same buzz."""
    stamps = await _cancel_while_stamp_is_queued(accepted=1)
    assert (STATE_SENT, True) in stamps, stamps


@pytest.mark.asyncio
async def test_a_cancel_while_the_failed_stamp_is_queued_still_stamps_failed():
    """Same shape for "APNs rejected every device": a dropped `failed` stamp turns a
    terminal rejection into a re-delivery attempt on the next process."""
    stamps = await _cancel_while_stamp_is_queued(accepted=0)
    assert (STATE_FAILED, False) in stamps, stamps


def test_both_outcome_stamps_in_deliver_are_shielded():
    """Source guard, comment-stripped: every `mark_state` carrying STATE_SENT or
    STATE_FAILED inside `_deliver` sits inside `asyncio.shield(`."""
    src = inspect.getsource(PushDispatchService._deliver)
    code = "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())
    for state in ("STATE_SENT", "STATE_FAILED"):
        pos = code.index(state)
        opener = code.rfind("await ", 0, pos)
        assert opener != -1
        stmt = code[opener:pos]
        assert "asyncio.shield(" in stmt and "asyncio.to_thread(" in stmt, (
            f"the {state} stamp is a cancellable executor future: {stmt!r}"
        )
