"""
Tests for the per-user concurrency cap on POST /research/generate.

The cap (`settings.MAX_CONCURRENT_REPORTS_PER_USER`, default 4) is enforced
PRE-CHARGE: a user already at the cap is rejected with
`TOO_MANY_CONCURRENT_REPORTS` at HTTP 409 and is NEVER charged credits. With a
slot free, the request proceeds to the atomic credit charge.

We call the endpoint handler directly with a mocked Supabase client + a patched
`CreditService`, so we can assert (a) the HTTP status / error_code and (b)
whether `try_charge` was invoked — with no network, no DB, no FMP, no Gemini.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

import app.api.v1.endpoints.research as research
from app.api.error_response import ErrorCode
from app.config import settings
from app.schemas.research import GenerateResearchRequest


def _supabase_with_inflight_count(count: int) -> MagicMock:
    """A Supabase mock whose research_reports count('exact') query resolves to
    `count`. Every chained builder method returns the same query mock."""
    query = MagicMock()
    for method in ("select", "eq", "in_", "gte", "insert"):
        getattr(query, method).return_value = query
    query.execute.return_value = MagicMock(count=count, data=[])
    sb = MagicMock()
    sb.table.return_value = query
    return sb


def _patch_credit_service(monkeypatch, try_charge_return):
    """Replace research.CreditService with a mock class; return the singleton
    instance so the test can assert on `try_charge`."""
    instance = MagicMock()
    instance.try_charge.return_value = try_charge_return
    cls = MagicMock(return_value=instance)
    cls.DEEP_RESEARCH_COST = 5
    monkeypatch.setattr(research, "CreditService", cls)
    return instance


def _req() -> GenerateResearchRequest:
    return GenerateResearchRequest(stock_id="AAPL", investor_persona="warren_buffett")


@pytest.mark.asyncio
async def test_at_cap_rejects_before_charging(monkeypatch):
    """User already at the cap → 409 TOO_MANY_CONCURRENT_REPORTS, and
    `try_charge` is NEVER called (no credits burned). This pre-charge
    invariant is the whole point of placing the cap check before the charge."""
    cap = settings.MAX_CONCURRENT_REPORTS_PER_USER
    supabase = _supabase_with_inflight_count(cap)               # exactly at cap
    credit = _patch_credit_service(monkeypatch, try_charge_return=99)

    resp = await research.generate_research_report(
        request=_req(), user={"id": "user-1"}, supabase=supabase, _rate_limit=None,
    )

    assert resp.status_code == 409
    assert json.loads(resp.body)["error_code"] == ErrorCode.TOO_MANY_CONCURRENT_REPORTS.value
    credit.try_charge.assert_not_called()


@pytest.mark.asyncio
async def test_under_cap_proceeds_to_charge(monkeypatch):
    """One slot free → the cap gate lets the request through to the atomic
    charge. We make `try_charge` return None (insufficient) so the handler
    short-circuits right after the charge — proving the charge WAS reached,
    without mocking the full FMP / insert / create_task happy path."""
    supabase = _supabase_with_inflight_count(
        settings.MAX_CONCURRENT_REPORTS_PER_USER - 1           # one under cap
    )
    credit = _patch_credit_service(monkeypatch, try_charge_return=None)

    resp = await research.generate_research_report(
        request=_req(), user={"id": "user-1"}, supabase=supabase, _rate_limit=None,
    )

    credit.try_charge.assert_called_once()                     # cap gate passed through
    assert resp.status_code == 403                             # INSUFFICIENT_CREDITS
    assert json.loads(resp.body)["error_code"] == ErrorCode.INSUFFICIENT_CREDITS.value


@pytest.mark.asyncio
async def test_cap_query_scopes_to_inflight_statuses(monkeypatch):
    """The cap must only count rows that are actually in flight — status in
    (pending, processing). Guards against a future edit widening the count to
    completed/failed rows (which would wrongly block new generations)."""
    query_holder = {}

    def _capture_in(*args, **kwargs):
        query_holder["statuses"] = args[1] if len(args) > 1 else kwargs.get("values")
        return query_holder["q"]

    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.gte.return_value = q
    q.in_.side_effect = _capture_in
    q.execute.return_value = MagicMock(count=0, data=[])
    query_holder["q"] = q
    sb = MagicMock()
    sb.table.return_value = q
    _patch_credit_service(monkeypatch, try_charge_return=None)

    await research.generate_research_report(
        request=_req(), user={"id": "user-1"}, supabase=sb, _rate_limit=None,
    )

    assert query_holder["statuses"] == ["pending", "processing"]
