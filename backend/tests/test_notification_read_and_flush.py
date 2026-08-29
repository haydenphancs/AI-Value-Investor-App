"""Marking a notification read from the lock screen, and the flush that used to lose rows.

WHY THIS FILE EXISTS — two findings from the round-2 notification audit, both of them
things a user can hit and neither of them visible from a log.

1. **"Mark as Read" was a registered button that did nothing.** It is attached to all six
   `UNNotificationCategory` identifiers, so every push the app sends offers it, and the
   handler fired one analytics event and returned. It could not have done anything else:
   the APNs payload identified no row — `route` carries a ticker and a tab, never an id.
   The dispatcher now sends `dedup_key`, the other half of `notification_events`'
   `(user_id, dedup_key)` UNIQUE index, and `mark_read` accepts it.

2. **A `flush_deferred` exception stranded a row at `pending` forever.** The claim RPC
   moves a row `deferred → pending`; an exception in the delivery was counted and logged
   and `mark_state` was never reached. Nothing re-reads `pending` — the RPC only ever
   selects `deferred` — so that notification could never be sent, never reached a terminal
   state, and sat in the user's inbox reading "pending" for its whole 30-day retention.
   Worse, two of the per-row calls sat OUTSIDE the guard, so one raising row stranded every
   remaining row in the batch the same way.
"""

import asyncio

import pytest

from app.services.notification_inbox_service import (
    NotificationInboxService,
    NotificationInboxUnavailable,
)
from app.services.push_dispatch_service import (
    STATE_DEFERRED,
    STATE_FAILED,
    PushDispatchService,
)


# ── mark read by dedup key ───────────────────────────────────────────────────


class _Query:
    """Records every filter applied, so a MISSING one is assertable."""

    def __init__(self, recorder):
        self.rec = recorder

    def update(self, patch):
        self.rec["patch"] = patch
        return self

    def eq(self, col, val):
        self.rec.setdefault("eq", []).append((col, val))
        return self

    def is_(self, col, val):
        self.rec.setdefault("is", []).append((col, val))
        return self

    def in_(self, col, vals):
        self.rec.setdefault("in", []).append((col, list(vals)))
        return self

    def execute(self):
        # EXECUTION is the thing worth recording. The builder is assembled before the
        # "nothing was named" branch returns, so asserting on the filters alone cannot
        # tell an issued UPDATE from an abandoned one.
        self.rec["executed"] = self.rec.get("executed", 0) + 1

        class _R:
            data = [{"id": "1"}]
        return _R()


def _inbox():
    rec = {}

    class _Supa:
        def table(self, *_a, **_k):
            return _Query(rec)

    svc = object.__new__(NotificationInboxService)
    svc.supabase = _Supa()
    return svc, rec


def test_a_dedup_key_addresses_the_row_the_push_named():
    """The payload's only identifier. Without this the button cannot work at all."""
    svc, rec = _inbox()
    assert svc.mark_read("u1", dedup_keys=["ticker_move:NVDA:2026-08-29"]) == 1
    assert ("dedup_key", ["ticker_move:NVDA:2026-08-29"]) in rec["in"]


def test_marking_by_dedup_key_is_still_scoped_to_the_caller():
    """The IDOR wall.

    A dedup key is derived from a ticker and a date, so it is guessable by design — it is
    NOT a capability. `user_id` is what makes this safe, exactly as it is for `ids`, and
    the backend holds the service-role key so RLS is not the thing stopping it.
    """
    svc, rec = _inbox()
    svc.mark_read("u1", dedup_keys=["ticker_move:NVDA:2026-08-29"])
    assert ("user_id", "u1") in rec["eq"], (
        "mark_read by dedup_key dropped the user scope — one user could mark another's "
        "notifications read by guessing a ticker and a date"
    )


def test_ids_win_when_both_selectors_are_sent():
    """One filter per query. The caller holding real ids is the in-app list, which is the
    more specific request; silently ORing the two would widen what was asked for."""
    svc, rec = _inbox()
    svc.mark_read("u1", ids=["abc"], dedup_keys=["k"])
    assert rec["in"] == [("id", ["abc"])]


def test_neither_selector_marks_nothing_rather_than_everything():
    """The dangerous default.

    Falling through with no id filter leaves a query scoped to the USER alone — which
    would mark their entire inbox read. A "Mark as Read" tap on a payload that somehow
    carried no dedup key would silently clear the badge on everything.
    """
    svc, rec = _inbox()
    assert svc.mark_read("u1") == 0
    assert "in" not in rec, "no id filter was applied — this UPDATE is user-scoped only"
    assert rec.get("executed", 0) == 0, (
        "a request naming nothing still issued an UPDATE — scoped to the user alone, that "
        "marks the whole inbox read"
    )


def test_mark_all_still_needs_no_selector():
    """The explicit "mark everything" path must keep working."""
    svc, rec = _inbox()
    assert svc.mark_read("u1", mark_all=True) == 1
    assert ("user_id", "u1") in rec["eq"]
    assert "in" not in rec


def test_a_failed_mark_read_raises_rather_than_reporting_zero():
    """A silent 0 reads to the client as "nothing was unread", so the badge would look
    correct while the write never happened."""
    class _Boom:
        def table(self, *_a, **_k):
            raise RuntimeError("postgrest down")

    svc = object.__new__(NotificationInboxService)
    svc.supabase = _Boom()
    with pytest.raises(NotificationInboxUnavailable):
        svc.mark_read("u1", dedup_keys=["k"])


# ── the flush must never strand a row ────────────────────────────────────────


def _flusher():
    """A dispatcher with only `_requeue_or_fail` live and `mark_state` captured."""
    svc = object.__new__(PushDispatchService)
    svc.supabase = None
    stamped = []

    def _mark(user_id, dedup_key, state, *, error=None, sent=False):
        stamped.append({"key": dedup_key, "state": state, "error": error})

    svc.mark_state = _mark
    return svc, stamped


def test_a_failed_flush_hands_the_row_back_as_deferred():
    """`deliver_after` is untouched by the claim RPC and is already in the past, so
    writing `deferred` back is the whole mechanism that makes the next cycle re-claim it.

    Before this, the row stayed at `pending` — a state nothing in the system ever reads
    again."""
    svc, stamped = _flusher()
    assert svc._requeue_or_fail("u1", "k", 1, "TimeoutError: apns") == STATE_DEFERRED
    assert stamped[-1]["state"] == STATE_DEFERRED
    assert "apns" in stamped[-1]["error"], "the cause must survive into last_error"


def test_the_retry_is_BOUNDED():
    """`attempts` is incremented by `claim_due_notifications` on every hand-out, so the
    ceiling is counted across cycles and instances — not per call. Without a bound, a row
    that fails deterministically would be re-delivered every 60 seconds forever."""
    svc, stamped = _flusher()
    at_ceiling = PushDispatchService.MAX_FLUSH_ATTEMPTS
    assert svc._requeue_or_fail("u1", "k", at_ceiling, "boom") == STATE_FAILED
    assert stamped[-1]["state"] == STATE_FAILED
    assert "gave up" in stamped[-1]["error"]


def test_the_last_attempt_before_the_ceiling_still_retries():
    """Anti-off-by-one: `MAX_FLUSH_ATTEMPTS - 1` must NOT be terminal, or the effective
    ceiling is one lower than the constant says."""
    svc, _ = _flusher()
    assert svc._requeue_or_fail(
        "u1", "k", PushDispatchService.MAX_FLUSH_ATTEMPTS - 1, "boom"
    ) == STATE_DEFERRED


def _flush_batch(rows, deliver):
    """Run `flush_deferred` over `rows` with everything but the loop stubbed out."""
    svc = object.__new__(PushDispatchService)
    svc.supabase = None
    stamped = []

    def _mark(user_id, dedup_key, state, *, error=None, sent=False):
        stamped.append({"key": dedup_key, "state": state, "error": error})

    svc.mark_state = _mark
    svc._claim_due = lambda limit: rows
    svc._devices_bulk = lambda uids: {u: [{"token": "t"}] for u in uids}
    svc.unread_counts_bulk = lambda uids: {}
    svc._preferences_bulk = lambda uids: {u: {} for u in uids}
    svc._category_counts_bulk = lambda uids, cat, cutoffs: {}
    svc._deliver = deliver
    return asyncio.run(svc.flush_deferred()), stamped


def test_one_raising_row_does_not_abandon_the_rest_of_the_batch():
    """The worse half of the same bug.

    `_category_counts_bulk` and `decide` used to sit outside the per-row guard, so a raise
    escaped the loop entirely — leaving every REMAINING row in the batch flipped to
    `pending` with nothing to put any of them back. One bad row stranded a hundred.
    """
    rows = [
        {"user_id": "u1", "dedup_key": "bad", "kind": "ticker_move", "attempts": 1},
        {"user_id": "u2", "dedup_key": "good", "kind": "ticker_move", "attempts": 1},
    ]

    async def _deliver(recipient, kind, **kw):
        if kw["dedup_key"] == "bad":
            raise RuntimeError("supabase blip")
        return True

    stats, stamped = _flush_batch(rows, _deliver)
    assert stats["sent"] == 1, "the healthy row after the failing one was never delivered"
    assert stats["requeued"] == 1
    # ONE thing, not two. `failed: 5, requeued: 5` in the same log line reads as ten
    # problems rather than five rows that retry in sixty seconds. A row is `requeued`
    # until the attempt ceiling turns it into a real, terminal `failed`.
    assert stats["failed"] == 0, (
        "a requeued row was ALSO counted as failed — the two counters now describe the "
        "same row and the flush log overstates the damage"
    )
    states = {s["key"]: s["state"] for s in stamped}
    assert states["bad"] == STATE_DEFERRED, (
        "the failing row was left at `pending`, which nothing re-reads — it can never be "
        "sent and never reaches a terminal state"
    )


def test_a_row_with_no_identity_is_reported_rather_than_skipped_silently():
    """It is already `pending` by the time we see it and both halves of its key are needed
    to address it, so it cannot be marked OR re-claimed. That is worth an ERROR, not a
    bare `continue` — the claim path cannot produce such a row."""
    rows = [{"user_id": None, "dedup_key": None, "id": 77, "kind": "ticker_move"}]

    async def _deliver(*a, **kw):  # pragma: no cover - must never be reached
        raise AssertionError("delivery attempted for an unidentifiable row")

    stats, stamped = _flush_batch(rows, _deliver)
    assert stats["failed"] == 1
    assert stamped == [], "there is no row to stamp — both halves of its key are missing"
