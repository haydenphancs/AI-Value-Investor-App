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
