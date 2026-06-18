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
    `count`. Every chained builder method returns the same query mock.

    Both the per-user cap query and the global-admission query run through this
    same builder, so `count` stands in for BOTH in-flight counts."""
    query = MagicMock()
    for method in ("select", "eq", "in_", "gte", "insert"):
        getattr(query, method).return_value = query
    query.execute.return_value = MagicMock(count=count, data=[])
    sb = MagicMock()
    sb.table.return_value = query
    return sb


def _supabase_with_counts(per_user: int, global_inflight: int) -> MagicMock:
    """A Supabase mock that returns `per_user` for the FIRST count query (the
    per-user cap) and `global_inflight` for the SECOND (global admission), so a
    test can pass the per-user gate but trip the global one."""
    query = MagicMock()
    for method in ("select", "eq", "in_", "gte", "insert"):
        getattr(query, method).return_value = query
    query.execute.side_effect = [
        MagicMock(count=per_user, data=[]),
        MagicMock(count=global_inflight, data=[]),
    ]
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


@pytest.mark.asyncio
async def test_inflight_queries_scoped_to_current_close_cycle(monkeypatch):
    """Both the per-user cap AND the global-admission count must filter
    `created_at >= current_close_cycle_start()`. Without that `.gte`, the counts
    would include orphaned pending/processing rows from PRIOR cycles → the global
    count could sit permanently >= cap and wedge the WHOLE app at 409 SYSTEM_BUSY.
    Guards against a refactor silently dropping the cycle scope."""
    from app.services.ticker_report_cache import current_close_cycle_start

    gte_calls = []
    q = MagicMock()
    q.select.return_value = q
    q.eq.return_value = q
    q.in_.return_value = q

    def _capture_gte(*args, **kwargs):
        gte_calls.append(args)
        return q

    q.gte.side_effect = _capture_gte
    q.execute.return_value = MagicMock(count=0, data=[])
    sb = MagicMock()
    sb.table.return_value = q
    _patch_credit_service(monkeypatch, try_charge_return=None)  # short-circuit after charge

    await research.generate_research_report(
        request=_req(), user={"id": "user-1"}, supabase=sb, _rate_limit=None,
    )

    cycle = current_close_cycle_start().isoformat()
    # Both the per-user and global queries must have applied the cycle filter.
    assert ("created_at", cycle) in gte_calls
    assert sum(1 for c in gte_calls if c == ("created_at", cycle)) >= 2


@pytest.mark.asyncio
async def test_global_admission_rejects_before_charging(monkeypatch):
    """User is UNDER their per-user cap, but the GLOBAL in-flight count is at the
    backstop → 409 SYSTEM_BUSY, and `try_charge` is NEVER called. This is the
    fast-fail-under-overload path: shed load instead of accepting unbounded
    concurrent agent runs onto the single event loop."""
    monkeypatch.setattr(settings, "MAX_GLOBAL_INFLIGHT_REPORTS", 150)
    supabase = _supabase_with_counts(per_user=0, global_inflight=150)  # at global cap
    credit = _patch_credit_service(monkeypatch, try_charge_return=99)

    resp = await research.generate_research_report(
        request=_req(), user={"id": "user-1"}, supabase=supabase, _rate_limit=None,
    )

    assert resp.status_code == 409
    assert json.loads(resp.body)["error_code"] == ErrorCode.SYSTEM_BUSY.value
    credit.try_charge.assert_not_called()


@pytest.mark.asyncio
async def test_global_cap_exactly_at_rejects(monkeypatch):
    """Boundary: global in-flight count EXACTLY AT the cap → 409 SYSTEM_BUSY,
    no charge. The gate is `>=`, so being at the cap must reject (the slot is
    already taken)."""
    monkeypatch.setattr(settings, "MAX_GLOBAL_INFLIGHT_REPORTS", 7)
    supabase = _supabase_with_counts(per_user=0, global_inflight=7)  # exactly at cap
    credit = _patch_credit_service(monkeypatch, try_charge_return=99)

    resp = await research.generate_research_report(
        request=_req(), user={"id": "user-1"}, supabase=supabase, _rate_limit=None,
    )

    assert resp.status_code == 409
    assert json.loads(resp.body)["error_code"] == ErrorCode.SYSTEM_BUSY.value
    credit.try_charge.assert_not_called()


@pytest.mark.asyncio
async def test_global_cap_under_by_one_passes_gate(monkeypatch):
    """Boundary: global in-flight count one UNDER the cap → the global gate
    lets the request through to the atomic charge. `try_charge` returns None
    (insufficient) so the handler short-circuits right after the charge,
    proving the global gate was passed (not the rejection path)."""
    monkeypatch.setattr(settings, "MAX_GLOBAL_INFLIGHT_REPORTS", 7)
    supabase = _supabase_with_counts(per_user=0, global_inflight=6)  # cap - 1
    credit = _patch_credit_service(monkeypatch, try_charge_return=None)

    resp = await research.generate_research_report(
        request=_req(), user={"id": "user-1"}, supabase=supabase, _rate_limit=None,
    )

    credit.try_charge.assert_called_once()                     # global gate passed
    assert resp.status_code == 403                             # INSUFFICIENT_CREDITS
    assert json.loads(resp.body)["error_code"] == ErrorCode.INSUFFICIENT_CREDITS.value


@pytest.mark.asyncio
async def test_global_gate_disabled_skips_query(monkeypatch):
    """`MAX_GLOBAL_INFLIGHT_REPORTS = 0` disables the global backstop entirely
    (`if global_cap > 0`). The global in-flight query must be SKIPPED, so even a
    huge backlog can't reject — the request reaches `try_charge`. We make the
    SECOND count enormous; if the gate ran it would 409, but with the gate off
    the handler never consumes it and proceeds straight to the charge."""
    monkeypatch.setattr(settings, "MAX_GLOBAL_INFLIGHT_REPORTS", 0)
    # per_user=0 passes the per-user cap; the huge second count would trip a
    # live global gate — but the gate is disabled so it's never queried.
    supabase = _supabase_with_counts(per_user=0, global_inflight=10_000)
    credit = _patch_credit_service(monkeypatch, try_charge_return=None)

    resp = await research.generate_research_report(
        request=_req(), user={"id": "user-1"}, supabase=supabase, _rate_limit=None,
    )

    credit.try_charge.assert_called_once()                     # reached the charge
    assert resp.status_code == 403                             # INSUFFICIENT_CREDITS
    assert json.loads(resp.body)["error_code"] == ErrorCode.INSUFFICIENT_CREDITS.value


@pytest.mark.asyncio
async def test_global_query_has_no_user_filter(monkeypatch):
    """The GLOBAL admission query must count across ALL users — it filters on
    status in (pending, processing) + created_at >= cycle_start but must NOT add
    an eq('user_id', ...). Only the per-user cap query is user-scoped; if the
    global query also filtered by user it would never shed cross-user load.

    We capture every `.eq(...)` and `.in_(...)` call on the shared builder. The
    per-user query (runs first) contributes the lone eq('user_id', ...). After
    it executes we snapshot the eq-call count, then assert the global query (runs
    second) adds NO further eq() — i.e. no user_id filter."""
    eq_calls: list = []
    in_calls: list = []
    eq_count_after_first_execute = {"n": None}

    q = MagicMock()
    q.select.return_value = q
    q.gte.return_value = q

    def _capture_eq(*args, **kwargs):
        eq_calls.append(args)
        return q

    def _capture_in(*args, **kwargs):
        in_calls.append(args)
        return q

    def _execute():
        # Snapshot the number of eq() calls seen at the moment the FIRST
        # (per-user) query executes — everything after belongs to the global
        # query. Per-user count starts at 0, so this fires on the first execute.
        if eq_count_after_first_execute["n"] is None:
            eq_count_after_first_execute["n"] = len(eq_calls)
        return MagicMock(count=0, data=[])

    q.eq.side_effect = _capture_eq
    q.in_.side_effect = _capture_in
    q.execute.side_effect = _execute

    sb = MagicMock()
    sb.table.return_value = q
    _patch_credit_service(monkeypatch, try_charge_return=None)

    await research.generate_research_report(
        request=_req(), user={"id": "user-1"}, supabase=sb, _rate_limit=None,
    )

    # The per-user query added exactly one eq filter: user_id.
    assert eq_count_after_first_execute["n"] == 1
    assert eq_calls[0] == ("user_id", "user-1")
    # The global query added NO further eq() → no user_id filter; it counts
    # across all users. (Total eq() calls is still just the per-user one.)
    assert len(eq_calls) == 1
    # Both queries gate on the in-flight statuses (per-user + global).
    assert all(c == ("status", ["pending", "processing"]) for c in in_calls)
    assert len(in_calls) == 2


@pytest.mark.asyncio
async def test_same_ticker_persona_runs_agent_once(monkeypatch):
    """N concurrent same-(ticker, persona) callers share ONE agent run.

    The whole point of _run_agent_deduped: a hot-ticker herd (e.g. 50 users
    open the same stock after earnings) must collapse to a single Gemini/FMP
    pipeline in the window before the shared cross-user cache is populated."""
    import asyncio

    from app.services import research_service as svc

    # Isolate from any cross-test state in the module-level dedup map.
    svc._AGENT_INFLIGHT.clear()

    calls = {"n": 0}
    started = asyncio.Event()
    release = asyncio.Event()

    async def _run_callable():
        calls["n"] += 1
        started.set()
        await release.wait()        # hold the leader so followers pile on
        return {"quality_score": 42, "ticker": "AAPL"}

    async def _caller():
        return await svc._run_agent_deduped("AAPL", "warren_buffett", _run_callable)

    tasks = [asyncio.create_task(_caller()) for _ in range(5)]
    await started.wait()            # leader is mid-flight; followers now attached
    release.set()
    results = await asyncio.gather(*tasks)

    # run_callable executed exactly once for the 5 concurrent identical callers.
    assert calls["n"] == 1
    # Every caller still gets the result…
    assert all(r["quality_score"] == 42 for r in results)
    # …and followers get an INDEPENDENT deep copy (mutating one can't bleed).
    results[0]["quality_score"] = 0
    assert results[1]["quality_score"] == 42
    assert not svc._AGENT_INFLIGHT  # leader cleaned up the in-flight entry
