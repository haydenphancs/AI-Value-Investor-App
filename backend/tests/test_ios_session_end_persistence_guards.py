"""A request in flight when the session ends must never re-persist the ex-user's data.

auth.md §7: any store keyed WITHOUT a user id (UserDefaults, a singleton's in-memory set)
is reset from `AppState.discardDataForEndedSession()`. `WhaleService.followedWhaleIds` is
one of those, and `reset()` was written specifically to stop account A's followed
investors appearing for account B on the same phone.

THE HOLE: `reset()` clears the set and removes the defaults key, but the in-flight follow
`Task` had no identity check. Cancellation alone does not close it — a task already past
its `await` still runs its completion branch, and BOTH that branch and the error branch
call `saveFollowedWhales()`. So a Follow tapped by A that landed after A signed out
re-created the device-global key `reset()` had just removed, and B inherited it on the
next sign-in: exactly the bleed `reset()` exists to prevent, through the back door.

The fix is an epoch captured at request start and re-checked after every await, before any
persist. Source-scan (there is no XCTest target), comment-stripped per
.claude/rules/testing.md §3 and mutation-tested by hand.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_IOS = pathlib.Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_WHALE = _IOS / "Views" / "Screens" / "WhaleService.swift"
_APPSTATE = _IOS / "Core" / "State" / "AppState.swift"


def _stripped(path: pathlib.Path) -> str:
    assert path.exists(), f"guard is stale — {path} moved"
    return "\n".join(
        "" if l.strip().startswith("//") else re.sub(r"\s//.*$", "", l)
        for l in path.read_text(encoding="utf-8").splitlines()
    )


def _block(src: str, header: str) -> str:
    i = src.find(header)
    assert i != -1, f"guard is stale — {header!r} not found"
    start = src.find("{", i)
    depth, j = 0, start
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
        j += 1
    raise AssertionError("unbalanced braces")


def test_reset_is_still_wired_to_the_session_end_funnel():
    """If this stops being called, none of the guards below matter."""
    assert "WhaleService.shared.reset()" in _stripped(_APPSTATE), (
        "WhaleService.reset() must run from AppState's end-of-session funnel (auth.md §7)"
    )


def test_reset_bumps_an_epoch_and_cancels_in_flight_work():
    body = _block(_stripped(_WHALE), "func reset()")
    assert "identityEpoch" in body, "reset() must invalidate in-flight requests"
    assert "cancel()" in body, "reset() must cancel the in-flight follow tasks"
    assert 'removeObject(forKey: "followedWhaleIds")' in body, (
        "reset() must still clear the device-global key"
    )


def test_every_persist_inside_the_follow_task_is_epoch_guarded():
    """`saveFollowedWhales()` is the write funnel — each call after an await needs a guard.

    Both branches matter: the success branch AND the error branch's revert persist.
    """
    src = _stripped(_WHALE)
    task = _block(src, "followTasks[whaleId] = Task")
    saves = task.count("self.saveFollowedWhales()")
    guards = task.count("self.identityEpoch == epoch")
    assert saves >= 2, f"expected the success and revert persists, found {saves}"
    assert guards >= saves, (
        f"{saves} persist(s) inside the follow task but only {guards} epoch guard(s) — "
        "an unguarded one re-creates the ended session's key"
    )


def test_the_epoch_is_captured_before_the_request_not_read_live():
    """Reading `identityEpoch` live inside the task would always compare equal."""
    src = _stripped(_WHALE)
    assert "let epoch = identityEpoch" in src, (
        "the epoch must be captured at request start; a live read can never detect a change"
    )


def test_a_guard_sits_between_the_await_and_every_persist():
    """The guard must be AFTER the suspension point, not merely somewhere earlier.

    ⚠️ Verified by hand: an entry-only guard (checked before the request is even sent)
    passes a naive "is there a guard in this task" scan while leaving the real hole wide
    open — the sign-out lands DURING the await, so only a re-check afterwards can see it.
    Removing the success branch's guard left this file green until this test existed.

    So: locate the await, then require an epoch check between it and each
    `saveFollowedWhales()` that follows.
    """
    task = _block(_stripped(_WHALE), "followTasks[whaleId] = Task")
    await_at = task.find("try await self.apiClient.request")
    assert await_at != -1, "guard is stale — the request await moved"

    after = task[await_at:]
    persists = [m.start() for m in re.finditer(r"self\.saveFollowedWhales\(\)", after)]
    assert len(persists) >= 2, (
        f"expected the success and revert persists after the await, found {len(persists)}"
    )
    cursor = 0
    for n, at in enumerate(persists, 1):
        segment = after[cursor:at]
        assert "self.identityEpoch == epoch" in segment, (
            f"persist #{n} after the await has no epoch re-check before it — an ended "
            "session's follow would be re-written into the device-global key"
        )
        cursor = at
