"""
Regression for the charge-refund-AND-deliver money bug (capstone review HIGH):

A report can sit queued behind the agent semaphore long enough that its
created_at ages past the 900s reconciliation threshold; the sweep then refunds
it + flips status='failed'. When it finally finishes, ResearchService.generate_report's
completion write MUST NOT revive that already-refunded row to 'completed' (which
would refund the user AND deliver the report). The write is now conditional on
is_refunded=False AND status in (pending,processing); 0 rows matched → drop the
result (no delivery, no cache seed).

We drive generate_report down the shared-cache-HIT path (no agent needed) and
flip the Supabase update's matched-row count.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.research_service as rs
from app.services.research_service import ResearchService

_CACHED = {
    "company_name": "Apple Inc.",
    "executive_summary_text": "ok",
    "executive_summary_bullets": [],
    "core_thesis": {"bull_case": [], "bear_case": []},
    "macro_data": {},
    "critical_factors": [],
    "quality_score": 70,
}


def _supabase(matched: bool) -> MagicMock:
    """Chain mock: the conditional completion update resolves to a result whose
    `.data` is non-empty (row matched) or empty (already reconciled)."""
    q = MagicMock()
    for m in ("table", "update", "eq", "in_"):
        getattr(q, m).return_value = q
    q.execute.return_value = MagicMock(data=([{"id": "rid"}] if matched else []))
    return q


def _service(matched: bool, monkeypatch):
    svc = object.__new__(ResearchService)        # skip __init__ (no real clients)
    svc.supabase = _supabase(matched)
    monkeypatch.setattr(svc, "_update_status", lambda *a, **k: None)
    monkeypatch.setattr(svc, "_lookup_shared_cache", AsyncMock(return_value=_CACHED))
    monkeypatch.setattr(rs, "compute_quality_score", lambda persona, data: 70)
    upsert = AsyncMock()
    monkeypatch.setattr(rs, "upsert_cached_report", upsert)
    return svc, upsert


@pytest.mark.asyncio
async def test_completion_dropped_when_already_reconciled(monkeypatch):
    """Reconciled (update matches 0 rows) → NO delivery: upsert_cached_report
    is never called, so the refunded report is not also delivered."""
    svc, upsert = _service(matched=False, monkeypatch=monkeypatch)

    await svc.generate_report("rid", "AAPL", "warren_buffett", "u1")

    upsert.assert_not_called()


@pytest.mark.asyncio
async def test_completion_delivers_when_row_still_live(monkeypatch):
    """Normal case (row still pending/processing, not refunded) → the write
    matches and the report IS delivered (cache seeded)."""
    svc, upsert = _service(matched=True, monkeypatch=monkeypatch)

    await svc.generate_report("rid", "AAPL", "warren_buffett", "u1")

    upsert.assert_awaited_once()


# ── Terminal statuses are owned by someone else ──────────────────────────────
#
# `_update_status` was an unconditional `UPDATE ... WHERE id = ?`, so it wrote over
# whatever terminal state a row had reached. Two live resurrections came out of that:
#
#   * the user DELETED an in-flight report (status='deleted', already refunded) and a
#     later worker failure rewrote it to 'failed' — the card reappeared in the Reports
#     list, complete with a Retry button, for a report they had already been refunded;
#   * the reconciliation sweep claimed an orphan ('failed' + is_refunded=True) and the
#     still-running worker's next progress tick wrote 'processing' back over it,
#     making a fully-settled row look live again.


def _status_recorder() -> tuple[MagicMock, dict]:
    seen: dict = {}
    q = MagicMock()
    for m in ("table", "update", "eq"):
        getattr(q, m).return_value = q

    def _in(col, vals):
        seen.setdefault("in_", []).append((col, list(vals)))
        return q

    q.in_.side_effect = _in
    q.execute.return_value = MagicMock(data=[])
    return q, seen


@pytest.mark.parametrize(
    "status,progress",
    [("processing", 5), ("failed", 0), ("completed", 100)],
)
def test_every_status_write_is_scoped_to_an_active_row(status, progress):
    svc = ResearchService.__new__(ResearchService)
    q, seen = _status_recorder()
    svc.supabase = q

    svc._update_status("rid", status, progress, current_step="x")

    assert seen.get("in_"), (
        f"_update_status({status!r}) issued an UNSCOPED update — it can overwrite a "
        "'deleted' row the user already had refunded, or revive one the sweep settled"
    )
    assert ("status", ["pending", "processing"]) in seen["in_"]


def test_status_writes_never_admit_a_terminal_state_to_the_filter():
    """The filter is the guard; widening it to include a terminal status would
    reintroduce the resurrection while leaving this file's other tests green."""
    assert rs._ACTIVE_STATUSES == ["pending", "processing"]
    for terminal in ("completed", "failed", "deleted"):
        assert terminal not in rs._ACTIVE_STATUSES
