"""
Tests for the processing_started_at decoupling (reconciliation clock fix):
  - _run_agent_deduped fires on_started AFTER acquiring the slot, before the run.
  - ResearchService._mark_processing_started stamps with an is-null guard and is
    best-effort (swallows errors, incl. the column missing pre-migration).
  - research_reconciliation._is_orphaned applies the two-threshold rule.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

import app.services.research_service as rs
import app.services.research_reconciliation_service as recon
from app.services.research_service import ResearchService


# ── on_started wiring ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_agent_deduped_calls_on_started_after_slot_before_run():
    """on_started must fire once, AFTER the semaphore slot is acquired and
    BEFORE the agent run — so processing_started_at marks real work-start, not
    enqueue time."""
    rs._AGENT_INFLIGHT.clear()
    rs._AGENT_SEMAPHORE = asyncio.Semaphore(1)
    order = []

    async def _on_started():
        order.append("started")

    async def _run():
        order.append("ran")
        return {"x": 1}

    out = await rs._run_agent_deduped("X", "p", _run, on_started=_on_started)

    assert out == {"x": 1}
    assert order == ["started", "ran"]


@pytest.mark.asyncio
async def test_followers_stamp_only_once_the_leader_holds_its_slot():
    """A follower's result arrives when the leader's does, ≤ the pipeline ceiling after
    the leader's slot — so its started clock is exactly as true as the leader's. It used
    to stay NULL, which put a healthy follower on the client's 1,800 s QUEUED clock while
    the server kept it for 11,400 s: shown "failed" under queue pressure, then Retry
    deleted a report that completed minutes later, unrefunded.

    The stamp must wait for the SLOT, though: while the leader is still queued a
    follower must not read "started" ahead of the work."""
    rs._AGENT_INFLIGHT.clear()
    rs._AGENT_RUNS.clear()
    rs._AGENT_SEMAPHORE = asyncio.Semaphore(1)
    started = {"n": 0}
    hold = asyncio.Event()      # occupies the only slot
    gate = asyncio.Event()      # releases the leader's run

    async def _blocker():
        await hold.wait()
        return {"other": 1}

    async def _on_started():
        started["n"] += 1

    async def _leader_run():
        await gate.wait()
        return {"x": 1}

    async def _follower_run():  # must never run
        raise AssertionError("follower ran its own callable")

    blocker = asyncio.create_task(rs._run_agent_deduped("OTHER", "p", _blocker))
    await asyncio.sleep(0.02)
    leader = asyncio.create_task(
        rs._run_agent_deduped("X", "p", _leader_run, on_started=_on_started)
    )
    await asyncio.sleep(0.02)
    follower = asyncio.create_task(
        rs._run_agent_deduped("X", "p", _follower_run, on_started=_on_started)
    )
    await asyncio.sleep(0.05)
    assert started["n"] == 0, "nobody may stamp while the leader is still queued"

    hold.set()                  # the slot frees; the leader acquires it
    await asyncio.sleep(0.05)
    assert started["n"] == 2, "leader AND follower stamp once the leader holds its slot"

    gate.set()
    out = await asyncio.gather(blocker, leader, follower)
    assert out[1] == out[2] == {"x": 1}
    assert started["n"] == 2
    assert rs._AGENT_INFLIGHT == {} and rs._AGENT_RUNS == {}


@pytest.mark.asyncio
async def test_a_follower_attaching_to_a_running_leader_stamps_at_attach_time():
    rs._AGENT_INFLIGHT.clear()
    rs._AGENT_RUNS.clear()
    rs._AGENT_SEMAPHORE = asyncio.Semaphore(2)
    started = {"n": 0}
    gate = asyncio.Event()

    async def _on_started():
        started["n"] += 1

    async def _leader_run():
        await gate.wait()
        return {"x": 1}

    leader = asyncio.create_task(rs._run_agent_deduped("X", "p", _leader_run, on_started=_on_started))
    await asyncio.sleep(0.02)
    assert started["n"] == 1
    follower = asyncio.create_task(rs._run_agent_deduped(
        "X", "p", _leader_run, on_started=_on_started))
    await asyncio.sleep(0.02)
    assert started["n"] == 2, "the leader already runs, so the follower stamps now"
    gate.set()
    await asyncio.gather(leader, follower)


@pytest.mark.asyncio
async def test_a_leader_cancelled_while_queued_leaves_its_follower_unstamped():
    """The shared future settles before the slot is ever held, so the follower fails
    through the normal refund path with a NULL stamp — never a started row for work that
    never started."""
    rs._AGENT_INFLIGHT.clear()
    rs._AGENT_RUNS.clear()
    rs._AGENT_SEMAPHORE = asyncio.Semaphore(1)
    started = {"n": 0}
    hold = asyncio.Event()

    async def _blocker():
        await hold.wait()
        return 1

    async def _on_started():
        started["n"] += 1

    async def _run():
        return {"x": 1}

    blocker = asyncio.create_task(rs._run_agent_deduped("OTHER", "p", _blocker))
    await asyncio.sleep(0.02)
    leader = asyncio.create_task(rs._run_agent_deduped("X", "p", _run, on_started=_on_started))
    await asyncio.sleep(0.02)
    follower = asyncio.create_task(rs._run_agent_deduped("X", "p", _run, on_started=_on_started))
    await asyncio.sleep(0.02)
    leader.cancel()
    with pytest.raises(RuntimeError, match="cancelled"):
        await follower
    assert started["n"] == 0
    hold.set()
    await blocker
    assert rs._AGENT_INFLIGHT == {} and rs._AGENT_RUNS == {}


# ── before_run: deleted-while-queued ────────────────────────────────────────


@pytest.mark.asyncio
async def test_an_abandoned_leader_with_no_followers_gives_the_slot_back_without_running():
    rs._AGENT_INFLIGHT.clear()
    rs._AGENT_RUNS.clear()
    rs._AGENT_SEMAPHORE = asyncio.Semaphore(1)
    ran = []

    async def _before_run(members=frozenset()):
        raise rs.ReportAbandonedError("deleted while queued")

    async def _run():
        ran.append(1)
        return {"x": 1}

    with pytest.raises(rs.ReportAbandonedError):
        await rs._run_agent_deduped("X", "p", _run, before_run=_before_run)
    assert ran == []
    # The slot is free again: a second leader runs immediately.
    assert await rs._run_agent_deduped("Y", "p", _run) == {"x": 1}
    assert rs._AGENT_INFLIGHT == {} and rs._AGENT_RUNS == {}


@pytest.mark.asyncio
async def test_an_abandoned_leader_still_runs_for_an_attached_follower():
    rs._AGENT_INFLIGHT.clear()
    rs._AGENT_RUNS.clear()
    rs._AGENT_SEMAPHORE = asyncio.Semaphore(1)
    hold = asyncio.Event()
    ran = []

    async def _blocker():
        await hold.wait()
        return 1

    async def _before_run(members=frozenset()):
        raise rs.ReportAbandonedError("deleted while queued")

    async def _run():
        ran.append(1)
        return {"x": 1}

    async def _never():
        raise AssertionError("follower ran its own callable")

    blocker = asyncio.create_task(rs._run_agent_deduped("OTHER", "p", _blocker))
    await asyncio.sleep(0.02)
    leader = asyncio.create_task(rs._run_agent_deduped("X", "p", _run, before_run=_before_run))
    await asyncio.sleep(0.02)
    follower = asyncio.create_task(rs._run_agent_deduped("X", "p", _never))
    await asyncio.sleep(0.02)
    hold.set()
    out = await asyncio.gather(blocker, leader, follower)
    assert out[1] == out[2] == {"x": 1} and ran == [1]


def test_is_still_active_fails_open_and_reads_status_and_refund(monkeypatch):
    svc = rs.ResearchService.__new__(rs.ResearchService)

    class _DB:
        def __init__(self, rows=None, raises=None):
            self.rows, self.raises = rows, raises

        def table(self, *_a): return self
        def select(self, *_a, **_k): return self
        def eq(self, *_a): return self
        def limit(self, *_a): return self

        def execute(self):
            if self.raises:
                raise self.raises
            return type("R", (), {"data": self.rows})()

    svc.supabase = _DB([{"status": "processing", "is_refunded": False}])
    assert svc._is_still_active("r") is True
    svc.supabase = _DB([{"status": "deleted", "is_refunded": True}])
    assert svc._is_still_active("r") is False
    svc.supabase = _DB([{"status": "processing", "is_refunded": True}])
    assert svc._is_still_active("r") is False, "the sweep refunded it — nothing to deliver"
    svc.supabase = _DB([])
    assert svc._is_still_active("r") is True, "no row = fail open"
    svc.supabase = _DB(raises=RuntimeError("520"))
    assert svc._is_still_active("r") is True, "read error = fail open"


# ── F08-6: abandonment is judged over EVERY row riding on the run ────────────
#
# "A follower is attached" used to be enough for an abandoned leader to run the whole
# pipeline — but a follower deleted while queued is still attached. The hook now sees
# the member set and asks whether ANY of those rows is still live, in one read.


def test_any_still_active_reads_the_whole_member_set_in_one_query():
    svc = rs.ResearchService.__new__(rs.ResearchService)
    seen = {}

    class _DB:
        def __init__(self, rows=None, raises=None):
            self.rows, self.raises = rows, raises

        def table(self, *_a): return self
        def select(self, *_a, **_k): return self

        def in_(self, col, vals):
            seen["in_"] = (col, list(vals))
            return self

        def execute(self):
            if self.raises:
                raise self.raises
            return type("R", (), {"data": self.rows})()

    svc.supabase = _DB([{"id": "a", "status": "deleted", "is_refunded": True},
                        {"id": "b", "status": "processing", "is_refunded": False}])
    assert svc._any_still_active({"a", "b"}) is True
    assert seen["in_"] == ("id", ["a", "b"]), "ONE read over the whole set"
    svc.supabase = _DB([{"id": "a", "status": "deleted", "is_refunded": True},
                        {"id": "b", "status": "failed", "is_refunded": True}])
    assert svc._any_still_active({"a", "b"}) is False
    svc.supabase = _DB([{"id": "a", "status": "processing", "is_refunded": True}])
    assert svc._any_still_active({"a"}) is False, "refunded = not live"
    svc.supabase = _DB([])
    assert svc._any_still_active({"a"}) is True, "no rows = fail open"
    svc.supabase = _DB(raises=RuntimeError("520"))
    assert svc._any_still_active({"a"}) is True, "read error = fail open"
    assert svc._any_still_active(set()) is True


@pytest.mark.asyncio
async def test_the_hook_sees_the_leader_and_every_attached_follower():
    rs._AGENT_INFLIGHT.clear()
    rs._AGENT_RUNS.clear()
    rs._AGENT_SEMAPHORE = asyncio.Semaphore(1)
    hold = asyncio.Event()
    seen = {}

    async def _blocker():
        await hold.wait()
        return 1

    async def _before_run(members):
        seen["members"] = set(members)

    async def _run():
        return {"x": 1}

    blocker = asyncio.create_task(rs._run_agent_deduped("OTHER", "p", _blocker))
    await asyncio.sleep(0.02)
    leader = asyncio.create_task(rs._run_agent_deduped(
        "X", "p", _run, before_run=_before_run, member_id="r-leader"))
    await asyncio.sleep(0.02)
    f1 = asyncio.create_task(rs._run_agent_deduped("X", "p", _run, member_id="r-f1"))
    f2 = asyncio.create_task(rs._run_agent_deduped("X", "p", _run))   # no id → invisible
    await asyncio.sleep(0.02)
    hold.set()
    await asyncio.gather(blocker, leader, f1, f2)
    assert seen["members"] == {"r-leader", "r-f1"}
    assert rs._AGENT_RUNS == {}


@pytest.mark.asyncio
async def test_a_leader_and_follower_both_deleted_while_queued_do_not_run(monkeypatch):
    """generate_report's hook, driven end to end through the dedup runner: every member
    row is dead → the slot goes back and nothing runs."""
    rs._AGENT_INFLIGHT.clear()
    rs._AGENT_RUNS.clear()
    rs._AGENT_SEMAPHORE = asyncio.Semaphore(1)
    hold = asyncio.Event()
    ran = []
    asked = []

    async def _blocker():
        await hold.wait()
        return 1

    svc = rs.ResearchService.__new__(rs.ResearchService)

    def _any(ids):
        asked.append(set(ids))
        return False                      # every row is deleted/refunded
    monkeypatch.setattr(svc, "_any_still_active", _any)

    def _hook_for(report_id):
        async def _before_run(members):
            ids = set(members) | {report_id}
            if not await asyncio.to_thread(svc._any_still_active, ids):
                raise rs.ReportAbandonedError("all dead")
        return _before_run

    async def _run():
        ran.append(1)
        return {"x": 1}

    blocker = asyncio.create_task(rs._run_agent_deduped("OTHER", "p", _blocker))
    await asyncio.sleep(0.02)
    leader = asyncio.create_task(rs._run_agent_deduped(
        "X", "p", _run, before_run=_hook_for("r1"), member_id="r1"))
    await asyncio.sleep(0.02)
    follower = asyncio.create_task(rs._run_agent_deduped("X", "p", _run, member_id="r2"))
    await asyncio.sleep(0.02)
    hold.set()
    with pytest.raises(rs.ReportAbandonedError):
        await leader
    with pytest.raises(rs.ReportAbandonedError):
        await follower
    assert ran == [], "nothing ran for two dead rows"
    assert asked == [{"r1", "r2"}], "judged over BOTH rows, in one read"
    assert rs._AGENT_INFLIGHT == {} and rs._AGENT_RUNS == {}


@pytest.mark.asyncio
async def test_a_dead_leader_with_a_live_follower_still_runs(monkeypatch):
    rs._AGENT_INFLIGHT.clear()
    rs._AGENT_RUNS.clear()
    rs._AGENT_SEMAPHORE = asyncio.Semaphore(1)
    hold = asyncio.Event()
    ran = []

    async def _blocker():
        await hold.wait()
        return 1

    live = {"r2"}

    def _any(ids):
        return bool(set(ids) & live)

    def _hook_for(report_id):
        async def _before_run(members):
            if not await asyncio.to_thread(_any, set(members) | {report_id}):
                raise rs.ReportAbandonedError("all dead")
        return _before_run

    async def _run():
        ran.append(1)
        return {"x": 1}

    blocker = asyncio.create_task(rs._run_agent_deduped("OTHER", "p", _blocker))
    await asyncio.sleep(0.02)
    leader = asyncio.create_task(rs._run_agent_deduped(
        "X", "p", _run, before_run=_hook_for("r1"), member_id="r1"))
    await asyncio.sleep(0.02)
    follower = asyncio.create_task(rs._run_agent_deduped("X", "p", _run, member_id="r2"))
    await asyncio.sleep(0.02)
    hold.set()
    out = await asyncio.gather(blocker, leader, follower)
    assert out[1] == out[2] == {"x": 1} and ran == [1]


def test_generate_report_wires_the_member_set_into_its_hook():
    """Source-scan, brace-bound to `generate_report`: the hook must take the member set,
    OR the leader's own id with it, and ask `_any_still_active` — a hook that still
    checks only `report_id` reintroduces the bug with every test above green."""
    import inspect
    src = inspect.getsource(rs.ResearchService.generate_report)
    src = "\n".join(ln.split("#", 1)[0] for ln in src.splitlines())
    assert "async def _before_run(members)" in src
    assert "set(members) | {report_id}" in src
    assert "self._any_still_active" in src
    assert "member_id=report_id" in src


@pytest.mark.asyncio
async def test_the_pipeline_ceiling_raises_a_typed_worded_timeout(monkeypatch):
    """The bare `TimeoutError` from `wait_for` has an empty str(); the failed card said
    "market data provider unavailable" and the Sentry line read "TimeoutError: "."""
    from app.api.error_response import ErrorCode, error_body_from_exception

    err = rs.ReportPipelineTimeoutError("AAPL", "warren_buffett", 600)
    assert isinstance(err, TimeoutError) and isinstance(err, asyncio.TimeoutError)
    assert "AAPL/warren_buffett" in str(err) and "600s" in str(err)
    body = error_body_from_exception(err, ticker="AAPL", persona="warren_buffett", step="x")
    assert body["error_code"] == ErrorCode.REPORT_TIMED_OUT.value
    assert body["error_code"] != ErrorCode.FMP_UNAVAILABLE.value


# ── _mark_processing_started ─────────────────────────────────────────────────


def test_mark_processing_started_stamps_with_isnull_guard():
    svc = object.__new__(ResearchService)
    seen = {}
    q = MagicMock()
    q.table.return_value = q

    def _update(payload):
        seen["payload"] = payload
        return q

    q.update.side_effect = _update
    q.eq.return_value = q

    def _is(col, val):
        seen["isnull"] = (col, val)
        return q

    def _in(col, vals):
        seen["in_"] = (col, list(vals))
        return q

    q.is_.side_effect = _is
    q.in_.side_effect = _in
    q.execute.return_value = MagicMock(data=[])
    svc.supabase = q

    svc._mark_processing_started("rid")

    assert "processing_started_at" in seen["payload"]
    assert seen["isnull"] == ("processing_started_at", "null")  # only stamps once
    # F08-7: the stamp carries a truthful step for the FOLLOWER's row, which no agent
    # tick ever touches — it used to read "Checking shared cache..." for the whole run.
    assert seen["payload"]["current_step"] == "Analysis in progress..."
    # ...and only on a row still IN the pipeline. Without this the stamp lands on a
    # row the user already deleted (terminal, already refunded) or one the
    # reconciliation sweep already claimed, re-arming the sweep's own age check
    # against a report nobody owes anything for.
    assert seen["in_"] == ("status", ["pending", "processing"])


def test_mark_processing_started_swallows_errors():
    """Best-effort — must NOT raise even if the column doesn't exist yet
    (before migration 070 is applied) or Supabase is down."""
    svc = object.__new__(ResearchService)
    q = MagicMock()
    q.table.side_effect = RuntimeError("column processing_started_at does not exist")
    svc.supabase = q

    svc._mark_processing_started("rid")  # must not raise


# ── _is_orphaned two-threshold rule ──────────────────────────────────────────


def test_is_orphaned_two_threshold_rule():
    now = datetime(2026, 6, 16, 12, 0, tzinfo=timezone.utc)
    started_cutoff = now - timedelta(seconds=recon.RECON_STUCK_THRESHOLD_SECONDS)
    abandoned_cutoff = now - timedelta(
        seconds=recon.RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS
    )

    def iso(secs):
        return (now - timedelta(seconds=secs)).isoformat()

    # started long ago, never finished → orphaned
    assert recon._is_orphaned(
        {"processing_started_at": iso(1000), "created_at": iso(5000)},
        started_cutoff, abandoned_cutoff,
    ) is True
    # started recently (still running) → NOT orphaned, even with old created_at
    assert recon._is_orphaned(
        {"processing_started_at": iso(100), "created_at": iso(5000)},
        started_cutoff, abandoned_cutoff,
    ) is False
    # never started, within abandon window (legitimately queued) → NOT orphaned
    assert recon._is_orphaned(
        {"processing_started_at": None, "created_at": iso(1200)},
        started_cutoff, abandoned_cutoff,
    ) is False
    # never started, past abandon window → orphaned (relative to the derived
    # threshold so this holds regardless of the configured caps)
    assert recon._is_orphaned(
        {
            "processing_started_at": None,
            "created_at": iso(recon.RECON_QUEUE_ABANDONED_THRESHOLD_SECONDS + 100),
        },
        started_cutoff, abandoned_cutoff,
    ) is True
    # malformed created_at on a claimable+old row → reconcile
    assert recon._is_orphaned(
        {"processing_started_at": None, "created_at": "garbage"},
        started_cutoff, abandoned_cutoff,
    ) is True
