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
async def test_run_agent_deduped_followers_do_not_call_on_started():
    """Followers attach to the leader's future and must NOT fire on_started
    (they never acquire a slot / start their own run)."""
    rs._AGENT_INFLIGHT.clear()
    rs._AGENT_SEMAPHORE = asyncio.Semaphore(5)
    started = {"n": 0}
    gate = asyncio.Event()

    async def _on_started():
        started["n"] += 1

    async def _leader_run():
        await gate.wait()
        return {"x": 1}

    leader = asyncio.create_task(
        rs._run_agent_deduped("X", "p", _leader_run, on_started=_on_started)
    )
    await asyncio.sleep(0.05)

    async def _follower_run():  # must never run
        raise AssertionError("follower ran its own callable")

    follower = asyncio.create_task(
        rs._run_agent_deduped("X", "p", _follower_run, on_started=_on_started)
    )
    await asyncio.sleep(0.05)
    gate.set()
    await asyncio.gather(leader, follower)

    assert started["n"] == 1  # only the leader stamped


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

    q.is_.side_effect = _is
    q.execute.return_value = MagicMock(data=[])
    svc.supabase = q

    svc._mark_processing_started("rid")

    assert "processing_started_at" in seen["payload"]
    assert seen["isnull"] == ("processing_started_at", "null")  # only stamps once


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
