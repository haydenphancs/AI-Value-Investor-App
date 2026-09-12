"""A redeploy mid-flush must not strand the rest of the batch at `pending` forever.

`claim_due_notifications` atomically flips due rows `deferred → pending` and hands them
back. `flush_deferred` then loops them with a per-row `except Exception` that puts a failed
row back — but `asyncio.CancelledError` is a **BaseException**, so that handler never sees
it. When the lifespan teardown cancelled `notification_dispatch` while row k was awaiting a
delivery, the loop simply exited: rows k..N stayed `pending`, the claim RPC only ever
selects `deferred`, nothing re-reads `pending`, and `mark_state` never ran for them.

No push, no terminal state, and an inbox row reading "pending" for its whole 30-day
retention — the exact stranded state the per-row handler exists to prevent, reached by the
one path that handler cannot catch (found 2026-09-12).
"""

from __future__ import annotations

import asyncio
import ast
import inspect
import re

import pytest

import app.services.push_dispatch_service as pds


def _flush_source() -> str:
    src = inspect.getsource(pds.PushDispatchService.flush_deferred)
    return "\n".join(re.sub(r"#.*$", "", line) for line in src.splitlines())


def test_the_flush_catches_cancellation_at_all():
    code = _flush_source()
    assert "asyncio.CancelledError" in code, (
        "a shutdown mid-batch leaves every un-processed row stranded at `pending` — "
        "`except Exception` cannot see a BaseException"
    )
    assert "raise" in code, "the cancellation must continue after the rows are returned"


def test_the_requeue_is_shielded():
    """It runs DURING a cancellation, so an unshielded await is cancelled too and the rows
    stay stranded anyway — the same reason `claimed_job`'s release is shielded."""
    code = _flush_source()
    arm = code[code.index("except asyncio.CancelledError"):]
    assert "asyncio.shield" in arm, "the shutdown requeue would itself be cancelled"


def test_it_re_defers_by_position_not_by_a_handled_set():
    """Several paths mark a row terminally FAILED and `continue`. Re-defering "everything
    not in the handled set" would resurrect those; everything from the cursor onward is
    untouched by construction."""
    code = _flush_source()
    assert "rows[cursor:]" in code
    assert "for cursor, row in enumerate(rows)" in code


def test_a_shutdown_does_not_spend_a_flush_attempt():
    """A redeploy is not the row's fault. Using `_requeue_or_fail` would burn one of
    `MAX_FLUSH_ATTEMPTS`, so a few unlucky restarts could fail a notification terminally."""
    code = _flush_source()
    arm = code[code.index("except asyncio.CancelledError"):]
    assert "_requeue_or_fail" not in arm, (
        "the shutdown path spends a flush attempt on a row that never got its turn"
    )
    assert "STATE_DEFERRED" in arm


@pytest.mark.asyncio
async def test_the_unprocessed_rows_are_returned_to_deferred(monkeypatch):
    """Behavioural: cancel mid-batch and assert the tail comes back as `deferred`."""
    from datetime import datetime, timezone

    svc = pds.PushDispatchService.__new__(pds.PushDispatchService)
    marks = []
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = [
        {"user_id": f"u{i}", "dedup_key": f"k{i}", "kind": "earnings_upcoming",
         "attempts": 1, "deliver_after": now_iso, "claimed_at": now_iso,
         "title": "t", "body": "b", "data": {}}
        for i in range(5)
    ]

    monkeypatch.setattr(svc, "_claim_due", lambda *a, **k: rows, raising=False)
    monkeypatch.setattr(svc, "_devices_bulk", lambda uids: {u: ["tok"] for u in uids},
                        raising=False)
    monkeypatch.setattr(svc, "unread_counts_bulk", lambda uids: {u: 0 for u in uids},
                        raising=False)
    monkeypatch.setattr(svc, "_preferences_bulk", lambda uids: {u: {} for u in uids},
                        raising=False)
    monkeypatch.setattr(
        svc, "mark_state",
        lambda uid, key, state, error=None, sent=False: marks.append((uid, key, state)),
        raising=False,
    )

    calls = {"n": 0}

    def _counts(*a, **k):
        calls["n"] += 1
        if calls["n"] == 3:                      # cancel while row index 2 is in flight
            raise asyncio.CancelledError()
        return {}

    # `_category_counts_bulk` is the first per-row await in the loop body, so raising the
    # cancellation there lands it INSIDE a row rather than between rows.
    monkeypatch.setattr(svc, "_category_counts_bulk", _counts, raising=False)

    with pytest.raises(asyncio.CancelledError):
        await svc.flush_deferred()

    returned = {(u, k) for u, k, st in marks if st == pds.STATE_DEFERRED}
    assert ("u2", "k2") in returned, "the row in flight was left stranded at pending"
    assert ("u3", "k3") in returned and ("u4", "k4") in returned, (
        f"the un-started tail was not returned to deferred: {sorted(returned)}"
    )
