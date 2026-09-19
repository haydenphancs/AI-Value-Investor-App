"""F17-4: the `sent` / `failed` stamp after an APNs answer survives a task cancel.

`_deliver` stamped the outcome with a plain `await asyncio.to_thread(self.mark_state, …)`.
`asyncio.to_thread` is an executor future, and cancelling a task whose work item the
executor has not STARTED yet discards it (`concurrent.futures.Future.cancel()` succeeds on
a queued item). With the default executor saturated by concurrent `sb_exec` calls at the
instant of a redeploy, the `sent` stamp simply never ran: the row still read `pending`,
the flush's cancel arm returned it to `deferred`, and the next process re-claimed and
re-sent it — the same "AAPL moved 8%" buzz twice, and a second `sent_at` against the daily
cap. The fix wraps both stamps in `asyncio.shield`, which keeps the inner task — and the
queued item — alive through the cancel; `asyncio.run`'s `shutdown_default_executor()` then
drains it before exit.

Proven here with a ONE-worker default executor occupied by a blocker, so the stamp's work
item is genuinely queued when the cancel lands. Without the shield it is discarded and the
stamp never happens; with it, the stamp lands the moment the worker frees.
"""

from __future__ import annotations

import asyncio
import inspect
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.config import settings
from app.services.notification_kinds import KIND_PRICE_ALERT, get_kind
from app.services.push_dispatch_service import (
    STATE_FAILED,
    STATE_SENT,
    PushDispatchService,
    _Recipient,
)
from app.services.push_service import PushOutcome


def _deliver_source() -> str:
    src = inspect.getsource(PushDispatchService._deliver)
    return "\n".join(re.sub(r"#.*$", "", ln) for ln in src.splitlines())


def test_both_outcome_stamps_are_shielded():
    """Source pin, comment-stripped and bound to `_deliver`: each `mark_state` that records
    an APNs answer sits inside `asyncio.shield(asyncio.to_thread(`."""
    code = _deliver_source()
    for state in ("STATE_SENT", "STATE_FAILED"):
        at = code.index(f"self.mark_state, uid, dedup_key, {state}")
        opener = code.rfind("await ", 0, at)
        call = code[opener:at]
        assert "asyncio.shield(asyncio.to_thread(" in call, (
            f"the {state} stamp is not shielded: {call.strip()!r}"
        )


def test_the_dry_run_and_no_device_stamps_are_not_shielded_by_accident():
    """Anti-over-correction: nothing was sent on those paths, so a dropped stamp costs a
    re-run of a dry-run, not a duplicate buzz. Keep the shield where APNs has answered."""
    code = _deliver_source()
    for state in ("STATE_DRY_RUN", "STATE_NO_DEVICE"):
        at = code.index(f"self.mark_state, uid, dedup_key, {state}")
        call = code[code.rfind("await ", 0, at):at]
        assert "asyncio.shield" not in call, f"{state} needs no shield"


class _Push:
    def __init__(self, outcome, sent_evt):
        self.enabled = True
        self._outcome, self._sent_evt = outcome, sent_evt

    async def send_to_user(self, user_id, **kw):
        self._sent_evt.set()                    # APNs has answered; the stamp is next
        return self._outcome


async def _run_cancel_with_the_stamp_queued(monkeypatch, outcome):
    """Drive `_deliver` to the point where the outcome stamp is QUEUED behind a busy
    single worker, cancel the task there, then free the worker. Returns the stamps."""
    monkeypatch.setattr(settings, "PUSH_DRY_RUN", False)
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(executor)
    blocker = threading.Event()
    hog = loop.run_in_executor(None, blocker.wait)     # occupies the only worker

    stamped: list = []
    svc = object.__new__(PushDispatchService)
    sent_evt = asyncio.Event()
    svc._push = _Push(outcome, sent_evt)
    svc.supabase = None
    svc.mark_state = (
        lambda uid, key, state, *, error=None, sent=False, only_if_state=None:
            stamped.append((uid, key, state, sent))
    )

    task = asyncio.create_task(svc._deliver(
        _Recipient(user_id="u1", devices=[{"token": "x", "environment": "production"}]),
        get_kind(KIND_PRICE_ALERT),
        title="ORCL is above $147.00", body="ORCL is trading at $148.21.",
        dedup_key="alert:1", route={"route": "ticker", "ticker": "ORCL"},
    ))
    await sent_evt.wait()
    for _ in range(3):                    # let `_deliver` submit the stamp to the executor
        await asyncio.sleep(0)
    assert not stamped, "the stamp ran before the worker was freed — the harness is wrong"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    blocker.set()                         # the redeploy's other writes finish
    await hog
    deadline = time.monotonic() + 3.0
    while not stamped and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    executor.shutdown(wait=True)
    return stamped


@pytest.mark.asyncio
async def test_a_cancel_with_the_sent_stamp_queued_still_records_sent(monkeypatch):
    stamped = await _run_cancel_with_the_stamp_queued(
        monkeypatch, PushOutcome(attempted=1, accepted=1))
    assert stamped == [("u1", "alert:1", STATE_SENT, True)], (
        f"APNs accepted the push but the `sent` stamp was discarded with the cancel — the "
        f"row stays `pending` and the next process re-sends it: {stamped}"
    )


@pytest.mark.asyncio
async def test_a_cancel_with_the_failed_stamp_queued_still_records_failed(monkeypatch):
    stamped = await _run_cancel_with_the_stamp_queued(
        monkeypatch, PushOutcome(attempted=1, accepted=0, failures=("400 BadDeviceToken",)))
    assert stamped == [("u1", "alert:1", STATE_FAILED, False)], (
        f"APNs rejected every device but the `failed` stamp was discarded — a re-delivery "
        f"attempt on the next process is not what that should turn into: {stamped}"
    )


@pytest.mark.asyncio
async def test_a_partial_delivery_stamp_survives_the_cancel_with_its_reason(monkeypatch):
    """Outlier: 2 devices, 1 accepted. `sent` (one took it) AND the rejection reaches the
    stamp — the shield must not lose the error argument."""
    monkeypatch.setattr(settings, "PUSH_DRY_RUN", False)
    loop = asyncio.get_running_loop()
    executor = ThreadPoolExecutor(max_workers=1)
    loop.set_default_executor(executor)
    blocker = threading.Event()
    hog = loop.run_in_executor(None, blocker.wait)
    stamped: list = []
    svc = object.__new__(PushDispatchService)
    sent_evt = asyncio.Event()
    svc._push = _Push(PushOutcome(attempted=2, accepted=1, failures=("prod …121: 400 BadDeviceToken",)),
                      sent_evt)
    svc.supabase = None
    svc.mark_state = (
        lambda uid, key, state, *, error=None, sent=False, only_if_state=None:
            stamped.append((state, sent, error))
    )
    task = asyncio.create_task(svc._deliver(
        _Recipient(user_id="u1", devices=[{"token": "x", "environment": "production"},
                                          {"token": "y", "environment": "production"}]),
        get_kind(KIND_PRICE_ALERT), title="t", body="b", dedup_key="alert:2",
        route={"route": "ticker", "ticker": "ORCL"},
    ))
    await sent_evt.wait()
    for _ in range(3):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    blocker.set()
    await hog
    deadline = time.monotonic() + 3.0
    while not stamped and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    executor.shutdown(wait=True)
    assert len(stamped) == 1 and stamped[0][0] == STATE_SENT and stamped[0][1] is True
    assert stamped[0][2] and "BadDeviceToken" in stamped[0][2]


@pytest.mark.asyncio
async def test_an_uncancelled_delivery_still_stamps_exactly_once(monkeypatch):
    """The shield must not double-stamp on the normal path."""
    monkeypatch.setattr(settings, "PUSH_DRY_RUN", False)
    stamped: list = []
    svc = object.__new__(PushDispatchService)
    svc._push = _Push(PushOutcome(attempted=1, accepted=1), asyncio.Event())
    svc.supabase = None
    svc.mark_state = (
        lambda uid, key, state, *, error=None, sent=False, only_if_state=None:
            stamped.append(state)
    )
    ok = await svc._deliver(
        _Recipient(user_id="u1", devices=[{"token": "x", "environment": "production"}]),
        get_kind(KIND_PRICE_ALERT), title="t", body="b", dedup_key="alert:3",
        route={"route": "ticker", "ticker": "ORCL"},
    )
    assert ok is True and stamped == [STATE_SENT]
