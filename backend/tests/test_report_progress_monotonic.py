"""A report's progress bar never steps backwards while the report runs.

TestFlight 1.0 (9): the Reports card draws `research_reports.progress`, which two files
write. `ResearchAgent.run` reports its own ticks through `progress_cb` (5 / 20 / 55 / 75 /
85 / 95, the last one "Validating and finalizing..."). Then `ResearchService.generate_report`
stamped "Saving report..." at **92**. So on every deep report the bar went 95 -> 92 -> 100
just before it finished. "Saving report..." is now 96, and every path only goes up:

* deep (leader):  2 -> 5 -> 5 -> 5 ... 95 -> 96 -> 100
* shared-cache hit: 2 -> 5 -> 90 -> 96 -> 100
* follower:       2 -> 5 -> 5 -> 96 -> 100 (the agent's ticks reach only the leader's row)

How it is tested: the REAL `generate_report` drives the REAL `ResearchAgent.run`, so a change
to either file's numbers is caught. Only the network collaborators are stubbed: Supabase,
FMP/Gemini, Stage A/B, the shared-cache read and the push. Every `research_reports` write is
recorded in the order the database sees it, together with the row it targets (its
`.eq("id", ...)`), through the real `_update_status`, the real `_mark_processing_started` and
the real conditional completion write. Each write gets its own recorded chain, so
`.eq(...).in_(...).execute()` still resolves.

The checks model the ROW the card draws, not the write's payload:
  * a write with no `status` key (`_mark_processing_started`) leaves the row's status as it
    was, so a progress value it carried WOULD be drawn;
  * the row is born 'pending' (research.py inserts it at progress 0), and iOS draws a bar for
    'pending' exactly as for 'processing' (frontend/ios/ios/Models/ResearchModels.swift,
    `default: return .processing // "pending" or "processing"`);
  * the status never goes back from 'completed' (the card would show "ready", then "running").

  A. deep path: non-decreasing, ends at 100 'completed', every write targets this report's
     row, and the agent's own ticks really reached it (the anti-vacuity check).
  B. shared-cache hit: the same, and the agent is never built.
  C. in-memory regression table over the recorded sequence (rewrite one value or status, or
     drop rows). It includes the shipped 95 -> 92 bug and the anti-vacuity case; a second
     table holds rewrites the card cannot tell apart, which must still pass.
  D. follower: one `ResearchService` and one Supabase mock per report id, so each row's
     writes, including its completion write, are attributed and checked.
  E. the same A/B checks against MUTATED copies of `research_service.py` /
     `research_agent.py`. Each copy is exec'd into a throwaway module object and never
     written to disk (other sessions read the tree concurrently).
  F. static guards (AST, so comments never count) for what A cannot execute: the agentic
     loop, Stage A and Stage B are STUBBED here, and a tick that one of them emitted, or a
     tick in a branch this run does not take, would never be seen. So: in `ResearchAgent.run`
     `progress_cb` is used only as `if progress_cb:` / `await progress_cb(<int literal>, ...)`
     at run's own scope, those literals rise in source order, nothing else in
     research_agent.py names a progress anything, and `generate_report` hands its
     `on_progress` to the agent only as `agent.run(progress_cb=on_progress)`.

In C, E and F every anchor must occur exactly once, and each mutation must fail with the
assertion message that names it.

Hermetic (backend/conftest.py blocks sockets). Every patch is `monkeypatch.setattr` on the
binding the caller actually reads: `rs.ResearchAgent`, `rs._AGENT_*`, `ra.build_*`. There are
no bare module assignments and no `raising=False` (tests/test_patch_targets_exist.py).
"""

from __future__ import annotations

import ast
import asyncio
import copy
import pathlib
import re
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.services.research_service as rs
from app.services.agents import research_agent as ra
from app.services.agents.persona_config import get_persona_config

_TICKER = "AAPL"
_PERSONA = "warren_buffett"
_DEDUP_KEY = f"{_TICKER}::{_PERSONA}"

# Step strings, as written today. The checks below name them only to locate rows; the
# values themselves are never pinned (only their ORDER is).
_INITIALIZING = "Initializing research agent..."
_CHECKING = "Checking shared cache..."
_LOADING_CACHED = "Loading cached analysis..."
_WAITING = "Waiting for an analysis slot..."
_STARTED = "Analysis in progress..."      # _mark_processing_started's step-only write
_GATHERING = "Gathering market data..."
_BUILDING = "Building report..."
_WRITING = "Writing narrative insights..."
_VALIDATING = "Validating and finalizing..."
_SAVING = "Saving report..."
_COMPLETE = "Complete"

# The statuses the Reports card draws a bar for. iOS maps every status other than
# 'completed' / 'failed' to `.processing` (ResearchModels.swift: `default: return
# .processing // "pending" or "processing"`), so a 'pending' write is drawn like a
# 'processing' one. 'failed' draws no bar.
_BAR_STATUSES = ("pending", "processing", "completed")
_INSERTED_STATUS = "pending"       # research.py inserts every row 'pending' at progress 0
_STATUS_RANK = {"pending": 0, "processing": 0, "completed": 1}
_AGENT_TICKS_TODAY = 6  # ResearchAgent.run: 5, 20, 55, 75, 85, 95

_MSG_CACHE_HIT_BUILT_AGENT = (
    "the shared-cache-hit path constructed a ResearchAgent — a cache hit must reuse the "
    "cached analysis, not run (and bill) the agent"
)

# A report that passes `report_degraded_reason` / `report_degraded_sections` and every
# legacy-field extractor (same shape as `_CACHED` in test_report_completion_guard.py).
_REPORT = {
    "company_name": "Apple Inc.",
    "executive_summary_text": "ok",
    "executive_summary_bullets": [],
    "core_thesis": {"bull_case": [], "bear_case": []},
    "macro_data": {},
    "critical_factors": [],
    "quality_score": 70,
}


# ── harness ──────────────────────────────────────────────────────────────────


def _recording_supabase(seq: list, ids: list, report_id: str) -> MagicMock:
    """Supabase stand-in. Each `table("research_reports").update(payload)` appends
    `(status, progress, current_step)` to `seq`, in write order, and returns a chain of
    ITS OWN whose `.eq("id", x)` calls land in the matching slot of `ids`, so the row each
    write targets is known even if two writes ever overlap. Writes to other tables are not
    bar values and are not recorded. Every `.execute()` answers one matched row, so the
    conditional completion write delivers.

    Nothing is asserted in here: `_update_status` catches every exception, so an assertion
    raised inside the recorder would be swallowed. The checks run on the record afterwards."""
    lock = threading.Lock()                  # the writes run in asyncio.to_thread

    def _chain() -> MagicMock:
        c = MagicMock()
        for m in ("eq", "in_", "is_", "select"):
            getattr(c, m).return_value = c
        c.execute.return_value = MagicMock(data=[{"id": report_id}])
        return c

    def _table(name):
        t = _chain()
        if name != "research_reports":
            return t

        def _update(payload):
            write = _chain()
            with lock:
                slot = len(ids)
                seq.append(
                    (payload.get("status"), payload.get("progress"), payload.get("current_step"))
                )
                ids.append([])

            def _eq(col, val):
                if col == "id":
                    ids[slot].append(val)
                return write

            write.eq.side_effect = _eq
            return write

        t.update.side_effect = _update
        return t

    client = MagicMock()
    client.table.side_effect = _table
    return client


def _patch_agent_collaborators(monkeypatch, ra_mod) -> None:
    """Stub everything `ResearchAgent.run` calls at module level that would reach
    FMP/Gemini (copied from test_sector_benchmark_off_the_loop.py). The stubs swallow any
    argument, which is why F pins that no progress callback is ever handed to them."""

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(ra_mod, "build_financial_context", lambda out: "evidence")
    monkeypatch.setattr(ra_mod, "build_narrative_jobs", lambda persona, evidence, report: [])
    monkeypatch.setattr(ra_mod, "run_narrative_jobs", _noop)
    monkeypatch.setattr(ra_mod, "synthesize_core_thesis", _noop)
    monkeypatch.setattr(ra_mod, "synthesize_critical_factors", _noop)


class _Collector:
    async def collect(self, ticker, persona_key):
        return SimpleNamespace(ticker=ticker, profile={})

    def assemble_report(self, out, shell):
        return copy.deepcopy(_REPORT)


def _make_agent(monkeypatch, ra_mod, *, gate: asyncio.Event | None = None,
                runs: list | None = None):
    """A REAL `ResearchAgent` (its real `run`) with no FMP/Gemini client: the collector,
    the agentic loop and Stage A are stubbed on the instance. `monkeypatch.setattr` on the
    instance raises if either method is renamed, so a rename cannot leave the real
    Gemini-calling method in place."""
    agent = object.__new__(ra_mod.ResearchAgent)
    agent.persona = get_persona_config(_PERSONA)
    agent.fmp = None
    agent.gemini = None
    agent.collector = _Collector()
    agent.research_findings = ""

    async def _research(out, evidence):
        if runs is not None:
            runs.append(out.ticker)
        if gate is not None:
            await gate.wait()
        return "findings"

    async def _stage_a(out, evidence, research_text):
        return {}

    monkeypatch.setattr(agent, "_agentic_research", _research)
    monkeypatch.setattr(agent, "_generate_stage_a", _stage_a)
    return agent


def _patch_service_module(monkeypatch, rs_mod, agent_factory) -> None:
    monkeypatch.setattr(rs_mod, "ResearchAgent", agent_factory)
    monkeypatch.setattr(rs_mod, "compute_quality_score", lambda persona_key, data: 70)
    monkeypatch.setattr(rs_mod, "upsert_cached_report", AsyncMock())
    # Fresh dedup state per test, restored afterwards: never a bare assignment, which would
    # leak a semaphore bound to this test's event loop into later tests.
    monkeypatch.setattr(rs_mod, "_AGENT_SEMAPHORE", asyncio.Semaphore(1))
    monkeypatch.setattr(rs_mod, "_AGENT_INFLIGHT", {})
    monkeypatch.setattr(rs_mod, "_AGENT_RUNS", {})


def _make_service(monkeypatch, rs_mod, report_id: str, *, cached):
    seq: list = []
    ids: list = []
    service = object.__new__(rs_mod.ResearchService)   # skip __init__ (no real clients)
    service.supabase = _recording_supabase(seq, ids, report_id)
    service.fmp = service.gemini = None                 # read before ResearchAgent(...)
    monkeypatch.setattr(service, "_lookup_shared_cache", AsyncMock(return_value=cached))
    # The real one reads `status` off the row; the chain mock's row has none → "abandoned".
    monkeypatch.setattr(service, "_any_still_active", lambda report_ids: True)
    notify = AsyncMock()
    monkeypatch.setattr(service, "_notify_report_ready", notify)
    return service, seq, ids, notify


async def _run_deep(monkeypatch, rs_mod=rs, ra_mod=ra) -> list:
    """Leader on a shared-cache MISS: the real agent runs and ticks this row."""
    _patch_agent_collaborators(monkeypatch, ra_mod)
    built: list = []

    def _factory(**kwargs):
        built.append(kwargs)
        return _make_agent(monkeypatch, ra_mod)

    _patch_service_module(monkeypatch, rs_mod, _factory)
    service, seq, ids, notify = _make_service(monkeypatch, rs_mod, "rid-deep", cached=None)

    delivered = await asyncio.wait_for(
        service.generate_report("rid-deep", _TICKER, _PERSONA, "u1"), timeout=5,
    )
    assert delivered is True, f"the deep run was not delivered (returned {delivered!r}): {seq}"
    assert [k.get("persona_key") for k in built] == [_PERSONA], (
        f"the deep path should build exactly one {_PERSONA} agent, built: {built}"
    )
    notify.assert_awaited_once()
    _assert_writes_target_row(seq, ids, "rid-deep")
    return seq


async def _run_cache_hit(monkeypatch, rs_mod=rs) -> list:
    """Shared-cache HIT: no agent may be built (the factory raises if it is)."""

    def _factory(**kwargs):
        raise AssertionError(_MSG_CACHE_HIT_BUILT_AGENT)

    _patch_service_module(monkeypatch, rs_mod, _factory)
    service, seq, ids, notify = _make_service(
        monkeypatch, rs_mod, "rid-cache", cached=copy.deepcopy(_REPORT),
    )

    delivered = await asyncio.wait_for(
        service.generate_report("rid-cache", _TICKER, _PERSONA, "u1"), timeout=5,
    )
    assert delivered is True, f"the cache-hit run was not delivered (returned {delivered!r}): {seq}"
    notify.assert_awaited_once()
    _assert_writes_target_row(seq, ids, "rid-cache")
    return seq


# ── checks ───────────────────────────────────────────────────────────────────


def _assert_writes_target_row(seq: list, ids: list, report_id: str) -> None:
    """Every `research_reports` write carries exactly one `.eq("id", report_id)`. A write
    aimed at another row, or at no row by id, never reaches this report's card, so the
    checks below would be judging numbers the card never draws."""
    assert len(ids) == len(seq), f"recorder out of step: {len(ids)} target(s), {len(seq)} write(s)"
    stray = [(s, got) for (_st, _p, s), got in zip(seq, ids) if got != [report_id]]
    assert not stray, (
        f"write(s) did not target this report's row {report_id!r} with exactly one "
        f".eq('id', {report_id!r}); the card never draws them: {stray}"
    )


def _bar_writes(seq: list) -> list:
    """Replay the writes onto the ROW and return `(index, row_status, progress, step)` for
    every write the Reports card draws a bar for. A write with no `status` key keeps the
    row's status; the row starts as inserted ('pending')."""
    bar, row_status = [], _INSERTED_STATUS
    for i, (status, progress, step) in enumerate(seq):
        if status is not None:
            row_status = status
        if progress is not None and row_status in _BAR_STATUSES:
            bar.append((i, row_status, progress, step))
    return bar


def _assert_monotonic(seq: list) -> None:
    """Over the writes the card draws: each progress is an int in 0..100, never lower than
    the one before; the status never goes back from 'completed'; and the last one is the
    completion at 100. The 'failed' stamp draws no bar and is not a bar value."""
    bar = [(st, p, s) for _i, st, p, s in _bar_writes(seq)]
    assert bar, f"no write the Reports card draws a bar for was recorded at all: {seq}"
    for _st, p, s in bar:
        assert isinstance(p, int) and not isinstance(p, bool) and 0 <= p <= 100, (
            f"progress {p!r} {s!r} is not an int in 0..100 "
            f"(research_reports.progress CHECK): {bar}"
        )
    for (st0, p0, s0), (st1, p1, s1) in zip(bar, bar[1:]):
        assert p1 >= p0, f"progress stepped backwards: {p0} {s0!r} -> {p1} {s1!r} (sequence: {bar})"
        assert _STATUS_RANK[st1] >= _STATUS_RANK[st0], (
            f"status stepped backwards: {st0} at {p0} {s0!r} -> {st1} at {p1} {s1!r}. The "
            f"card showed the report ready, then running again, and the guarded completion "
            f"write cannot land on a row that is already 'completed' (sequence: {bar})"
        )
    assert bar[-1][0] == "completed" and bar[-1][1] == 100, (
        f"the run did not end at 100 'completed': last bar write {bar[-1]} (sequence: {bar})"
    )


def _agent_window(seq: list) -> tuple:
    steps = [s for _, _, s in seq]
    return steps.index(_WAITING), steps.index(_SAVING)


def _check_deep(seq: list) -> None:
    steps = [s for _, _, s in seq]
    assert _WAITING in steps, (
        f"the deep path never wrote {_WAITING!r}, so it did not take the agent branch: {steps}"
    )
    assert _SAVING in steps, f"the deep path never wrote {_SAVING!r}: {steps}"
    i_wait, i_save = _agent_window(seq)
    # Counted on the ROW: a tick written with no status key, or as 'pending', is drawn all
    # the same, so it counts; one written 'failed' is not drawn, so it does not.
    ticks = [(p, s) for i, _st, p, s in _bar_writes(seq) if i_wait < i < i_save]
    assert len(ticks) >= _AGENT_TICKS_TODAY, (
        f"only {len(ticks)} agent progress tick(s) reached this report's row between "
        f"{_WAITING!r} and {_SAVING!r}; the real ResearchAgent.run writes "
        f"{_AGENT_TICKS_TODAY}. Without them the monotonic check never sees the agent's own "
        f"numbers: {ticks}"
    )
    _assert_monotonic(seq)


def _check_cache_hit(seq: list) -> None:
    steps = [s for _, _, s in seq]
    assert _LOADING_CACHED in steps, (
        f"the shared-cache-hit path never wrote {_LOADING_CACHED!r}: {steps}"
    )
    assert _SAVING in steps, f"the shared-cache-hit path never wrote {_SAVING!r}: {steps}"
    assert steps.index(_LOADING_CACHED) < steps.index(_SAVING), (
        f"{_LOADING_CACHED!r} must come before {_SAVING!r}: {steps}"
    )
    _assert_monotonic(seq)


def _check_follower(seq: list) -> None:
    steps = [s for _, _, s in seq]
    assert _WAITING in steps, f"the follower never wrote {_WAITING!r}: {steps}"
    assert _SAVING in steps, f"the follower never wrote {_SAVING!r}: {steps}"
    _assert_monotonic(seq)


# ── A / B ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_deep_path_progress_never_steps_backwards(monkeypatch):
    _check_deep(await _run_deep(monkeypatch))


@pytest.mark.asyncio
async def test_cache_hit_progress_never_steps_backwards(monkeypatch):
    _check_cache_hit(await _run_cache_hit(monkeypatch))


# ── C: in-memory regression table over the recorded sequence ─────────────────


def _rewrite(seq: list, overrides: dict) -> list:
    return [(st, overrides.get(s, p), s) for st, p, s in seq]


def _restatus(seq: list, rows: dict) -> list:
    """Replace `(status, progress)` of the rows named by step."""
    return [(*rows[s], s) if s in rows else (st, p, s) for st, p, s in seq]


def _agent_tick_rows(seq: list) -> set:
    i_wait, i_save = _agent_window(seq)
    return {s for i, _st, _p, s in _bar_writes(seq) if i_wait < i < i_save}


_RECORD_CASES = [
    # (path, kind, payload, the assertion message that must fire)
    ("deep", "override", {_SAVING: 92},
     f"progress stepped backwards: 95 {_VALIDATING!r} -> 92 {_SAVING!r}"),   # the shipped bug
    ("deep", "override", {_VALIDATING: 97},
     f"progress stepped backwards: 97 {_VALIDATING!r} -> 96 {_SAVING!r}"),
    ("deep", "override", {_WAITING: 6},
     f"progress stepped backwards: 6 {_WAITING!r} -> 5 {_GATHERING!r}"),
    ("deep", "override", {_INITIALIZING: 6},
     f"progress stepped backwards: 6 {_INITIALIZING!r} -> 5 {_CHECKING!r}"),
    ("deep", "override", {_COMPLETE: 90},
     f"progress stepped backwards: 96 {_SAVING!r} -> 90 {_COMPLETE!r}"),
    ("deep", "override", {_COMPLETE: 99},
     "the run did not end at 100 'completed'"),
    ("deep", "override", {_SAVING: 101},
     f"progress 101 {_SAVING!r} is not an int in 0..100"),
    ("deep", "drop_agent_ticks", None,
     "only 0 agent progress tick(s) reached this report's row"),               # anti-vacuity
    # A write with no status key keeps the row's status ('processing'), so its progress is
    # drawn: the row model, not the payload.
    ("deep", "override", {_STARTED: 0},
     f"progress stepped backwards: 5 {_WAITING!r} -> 0 {_STARTED!r}"),
    # 'pending' draws a bar like 'processing'.
    ("deep", "restatus", {_WAITING: ("pending", 0)},
     f"progress stepped backwards: 5 {_CHECKING!r} -> 0 {_WAITING!r}"),
    # 'completed' mid-run: progress still rises, but the card goes ready -> running.
    ("deep", "restatus", {_BUILDING: ("completed", 75)},
     f"status stepped backwards: completed at 75 {_BUILDING!r} -> processing at 85 {_WRITING!r}"),
    ("cache", "override", {_LOADING_CACHED: 97},
     f"progress stepped backwards: 97 {_LOADING_CACHED!r} -> 96 {_SAVING!r}"),
    ("cache", "override", {_SAVING: 89},
     f"progress stepped backwards: 90 {_LOADING_CACHED!r} -> 89 {_SAVING!r}"),
    ("cache", "restatus", {_LOADING_CACHED: ("pending", 3)},
     f"progress stepped backwards: 5 {_CHECKING!r} -> 3 {_LOADING_CACHED!r}"),
    ("cache", "drop", {_LOADING_CACHED},
     f"the shared-cache-hit path never wrote {_LOADING_CACHED!r}"),            # anti-vacuity
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,kind,payload,message", _RECORD_CASES,
    ids=[f"{p}:{k}:{i}" for i, (p, k, *_rest) in enumerate(_RECORD_CASES)],
)
async def test_the_check_catches_each_regression(monkeypatch, path, kind, payload, message):
    """Each regression, applied to the REAL recorded sequence, must fail with ITS OWN
    message — so a case cannot pass by tripping an unrelated earlier check."""
    if path == "deep":
        seq, check = await _run_deep(monkeypatch), _check_deep
    else:
        seq, check = await _run_cache_hit(monkeypatch), _check_cache_hit
    check(seq)  # the unmutated record passes

    steps = [s for _, _, s in seq]
    if kind in ("override", "restatus", "drop"):
        for step in payload:
            assert steps.count(step) == 1, (
                f"{kind} anchor {step!r} was recorded {steps.count(step)} time(s), expected "
                f"exactly once — re-derive this case against the new sequence: {steps}"
            )
    if kind == "override":
        mutated = _rewrite(seq, payload)
    elif kind == "restatus":
        mutated = _restatus(seq, payload)
    elif kind == "drop":
        mutated = [row for row in seq if row[2] not in payload]
    else:  # drop_agent_ticks
        ticks = _agent_tick_rows(seq)
        assert len(ticks) >= _AGENT_TICKS_TODAY, f"no agent ticks to drop: {steps}"
        mutated = [row for row in seq if row[2] not in ticks]
    assert mutated != seq, f"case {kind} {payload} changed nothing in {seq}"

    with pytest.raises(AssertionError, match=re.escape(message)):
        check(mutated)


@pytest.mark.asyncio
@pytest.mark.parametrize("tick_status", [None, "pending"], ids=["no-status-key", "pending"])
async def test_the_check_accepts_rows_the_card_draws_the_same(monkeypatch, tick_status):
    """The checks read the ROW, not the payload: agent ticks written with no `status` key
    (the row stays 'processing') or as 'pending' (drawn like 'processing') are the same bar,
    so they must still pass and still count as ticks."""
    seq = await _run_deep(monkeypatch)
    ticks = _agent_tick_rows(seq)
    assert len(ticks) >= _AGENT_TICKS_TODAY, f"no agent ticks to restatus: {seq}"
    variant = [(tick_status, p, s) if s in ticks else (st, p, s) for st, p, s in seq]
    assert variant != seq, f"restatus to {tick_status!r} changed nothing in {seq}"
    _check_deep(variant)


# ── D: follower ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_follower_progress_never_steps_backwards(monkeypatch):
    """Two reports for the same (ticker, persona): the second attaches to the first's run.
    Each has its own service and Supabase mock, so each row is checked in full, including
    its own completion write."""
    _patch_agent_collaborators(monkeypatch, ra)
    gate = asyncio.Event()
    runs: list = []

    def _factory(**kwargs):
        return _make_agent(monkeypatch, ra, gate=gate, runs=runs)

    _patch_service_module(monkeypatch, rs, _factory)
    leader, leader_seq, leader_ids, leader_notify = _make_service(
        monkeypatch, rs, "rid-lead", cached=None,
    )
    follower, follower_seq, follower_ids, follower_notify = _make_service(
        monkeypatch, rs, "rid-follow", cached=None,
    )

    tasks: list = []

    async def _scenario():
        tasks.append(asyncio.create_task(
            leader.generate_report("rid-lead", _TICKER, _PERSONA, "u1")))
        while not runs:                           # leader parked inside its agentic loop
            await asyncio.sleep(0.01)
        tasks.append(asyncio.create_task(
            follower.generate_report("rid-follow", _TICKER, _PERSONA, "u2")))
        while rs._AGENT_RUNS.get(_DEDUP_KEY) is None or rs._AGENT_RUNS[_DEDUP_KEY].followers < 1:
            await asyncio.sleep(0.01)
        gate.set()
        return await asyncio.gather(*tasks)

    try:
        results = await asyncio.wait_for(_scenario(), timeout=5)
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()

    assert results == [True, True], f"both reports should be delivered: {results}"
    assert runs == [_TICKER], (
        f"the follower ran its own agent instead of sharing the leader's run: {runs}"
    )
    leader_notify.assert_awaited_once()
    follower_notify.assert_awaited_once()
    _assert_writes_target_row(leader_seq, leader_ids, "rid-lead")
    _assert_writes_target_row(follower_seq, follower_ids, "rid-follow")
    _check_deep(leader_seq)
    _check_follower(follower_seq)


# ── E: real-source mutations, in memory ──────────────────────────────────────


def _clone(module, source: str):
    """Exec `source` into a fresh module object named like `module`. Never registered in
    sys.modules and never written to disk; compiled without this file's future flags."""
    clone = types.ModuleType(module.__name__)
    clone.__file__ = module.__file__
    clone.__package__ = module.__package__
    code = compile(
        source, f"<in-memory copy of {pathlib.Path(module.__file__).name}>", "exec",
        dont_inherit=True,
    )
    exec(code, clone.__dict__)
    return clone


def _apply_edits(module, edits: list) -> str:
    """The module's source with each `(old, new)` applied in memory. Every anchor must
    occur exactly once, in the file and in the text it is applied to."""
    path = pathlib.Path(module.__file__)
    original = path.read_text(encoding="utf-8")
    text = original
    for old, new in edits:
        assert original.count(old) == 1 and text.count(old) == 1, (
            f"mutation anchor `{old}` occurs {original.count(old)} time(s) in {path.name} "
            f"({text.count(old)} after the earlier edits), expected exactly once — re-derive "
            "this mutation against the new source rather than deleting it"
        )
        text = text.replace(old, new, 1)
    assert text != original
    return text


def _mutated_clone(module, old: str, new: str):
    return _clone(module, _apply_edits(module, [(old, new)]))


_ON_PROGRESS_WRITE = 'await self._update_status_async(report_id, "processing", progress, step)'
_STARTED_WRITE = '"current_step": "Analysis in progress...",'

_SOURCE_MUTATIONS = [
    # (module, old, new, path, the assertion message that must fire)
    (rs, '"processing", 96, "Saving report..."', '"processing", 92, "Saving report..."', "deep",
     f"progress stepped backwards: 95 {_VALIDATING!r} -> 92 {_SAVING!r}"),     # the shipped bug
    (rs, '"processing", 90, "Loading cached analysis..."',
     '"processing", 97, "Loading cached analysis..."', "cache",
     f"progress stepped backwards: 97 {_LOADING_CACHED!r} -> 96 {_SAVING!r}"),
    (rs, '"processing", 5, "Waiting for an analysis slot...",',
     '"processing", 6, "Waiting for an analysis slot...",', "deep",
     f"progress stepped backwards: 6 {_WAITING!r} -> 5 {_GATHERING!r}"),
    (rs, '"processing", 2, "Initializing research agent..."',
     '"processing", 6, "Initializing research agent..."', "deep",
     f"progress stepped backwards: 6 {_INITIALIZING!r} -> 5 {_CHECKING!r}"),
    (rs, '"progress": 100,', '"progress": 90,', "deep",
     f"progress stepped backwards: 96 {_SAVING!r} -> 90 {_COMPLETE!r}"),
    (rs, '"progress": 100,', '"progress": 90,', "cache",
     f"progress stepped backwards: 96 {_SAVING!r} -> 90 {_COMPLETE!r}"),
    (rs, "progress_cb=on_progress,", "progress_cb=None,", "deep",
     "only 0 agent progress tick(s) reached this report's row"),               # anti-vacuity
    (rs, "if cached is not None:", "if cached is None:", "cache",
     _MSG_CACHE_HIT_BUILT_AGENT),
    # The status-less `_mark_processing_started` write starts carrying progress: the row is
    # still 'processing', so the card draws it (row model).
    (rs, _STARTED_WRITE, _STARTED_WRITE + ' "progress": 0,', "deep",
     f"progress stepped backwards: 5 {_WAITING!r} -> 0 {_STARTED!r}"),
    (rs, _STARTED_WRITE, _STARTED_WRITE + ' "progress": 10,', "deep",
     f"progress stepped backwards: 10 {_STARTED!r} -> 5 {_GATHERING!r}"),
    # Queued / cached stamps written 'pending': iOS draws 'pending' like 'processing'.
    (rs, '"processing", 5, "Waiting for an analysis slot...",',
     '"pending", 0, "Waiting for an analysis slot...",', "deep",
     f"progress stepped backwards: 5 {_CHECKING!r} -> 0 {_WAITING!r}"),
    (rs, '"processing", 90, "Loading cached analysis..."',
     '"pending", 3, "Loading cached analysis..."', "cache",
     f"progress stepped backwards: 5 {_CHECKING!r} -> 3 {_LOADING_CACHED!r}"),
    # The agent's ticks, or the completion write, aimed at another row.
    (rs, _ON_PROGRESS_WRITE, _ON_PROGRESS_WRITE.replace("(report_id,", "(ticker,"), "deep",
     "write(s) did not target this report's row 'rid-deep'"),
    (rs, '.eq("id", report_id)\n                    .eq("is_refunded", False)',
     '.eq("user_id", user_id)\n                    .eq("is_refunded", False)', "cache",
     "write(s) did not target this report's row 'rid-cache'"),
    # Every agent tick marks the row 'completed': progress still rises, the card does not.
    (rs, _ON_PROGRESS_WRITE, _ON_PROGRESS_WRITE.replace('"processing"', '"completed"'), "deep",
     f"status stepped backwards: completed at 95 {_VALIDATING!r} -> processing at 96 {_SAVING!r}"),
    (ra, 'progress_cb(95, "Validating and finalizing...")',
     'progress_cb(97, "Validating and finalizing...")', "deep",
     f"progress stepped backwards: 97 {_VALIDATING!r} -> 96 {_SAVING!r}"),
    (ra, 'progress_cb(5, "Gathering market data...")',
     'progress_cb(4, "Gathering market data...")', "deep",
     f"progress stepped backwards: 5 {_WAITING!r} -> 4 {_GATHERING!r}"),
    (ra, 'progress_cb(85, "Writing narrative insights...")',
     'progress_cb(70, "Writing narrative insights...")', "deep",
     f"progress stepped backwards: 75 {_BUILDING!r} -> 70 {_WRITING!r}"),
]


async def _run_and_check(monkeypatch, path: str, rs_mod, ra_mod) -> None:
    if path == "deep":
        _check_deep(await _run_deep(monkeypatch, rs_mod, ra_mod))
    else:
        _check_cache_hit(await _run_cache_hit(monkeypatch, rs_mod))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["deep", "cache"])
async def test_an_unmutated_copy_passes(monkeypatch, path):
    """The exec'd copy itself introduces no failure, so a killed mutation below is killed by
    the mutation and not by the copying."""
    rs_copy = _clone(rs, pathlib.Path(rs.__file__).read_text(encoding="utf-8"))
    ra_copy = _clone(ra, pathlib.Path(ra.__file__).read_text(encoding="utf-8"))
    await _run_and_check(monkeypatch, path, rs_copy, ra_copy)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "module,old,new,path,message", _SOURCE_MUTATIONS,
    ids=[f"{m.__name__.rsplit('.', 1)[-1]}:{p}:{i}"
         for i, (m, _o, _n, p, _msg) in enumerate(_SOURCE_MUTATIONS)],
)
async def test_each_source_mutation_is_killed(monkeypatch, module, old, new, path, message):
    mutant = _mutated_clone(module, old, new)   # anchor check runs OUTSIDE pytest.raises
    rs_mod = mutant if module is rs else rs
    ra_mod = mutant if module is ra else ra
    with pytest.raises(AssertionError, match=re.escape(message)):
        await _run_and_check(monkeypatch, path, rs_mod, ra_mod)


# ── F: static guards for the ticks A cannot execute ──────────────────────────
#
# A runs the real `ResearchAgent.run`, but the agentic loop, Stage A and Stage B are stubbed
# (the module-level ones swallow any argument). So a tick that one of them emitted, through
# a callback handed down or stashed on the agent, would never execute here, and neither would
# a tick in a branch this run does not take. These guards pin that every tick is a direct
# `await progress_cb(<int literal>, ...)` at run's own scope, in rising source order.

_MSG_NO_PARAM = "ResearchAgent.run has no `progress_cb` parameter"
_MSG_NESTED = "progress_cb is used inside a nested function or lambda in ResearchAgent.run"
_MSG_ESCAPES = (
    "progress_cb is used other than as `if progress_cb:` / `await progress_cb(...)` "
    "in ResearchAgent.run"
)
_MSG_NOT_LITERAL = "a tick in ResearchAgent.run does not pass an int literal as its progress"
_MSG_SOURCE_ORDER = "the ticks in ResearchAgent.run do not rise in source order"
_MSG_OUTSIDE = "something outside ResearchAgent.run names a progress value in research_agent.py"
_MSG_NO_ON_PROGRESS = "ResearchService.generate_report defines no `on_progress` callback"
_MSG_ON_PROGRESS_STRAYS = (
    "on_progress reaches something other than `agent.run(progress_cb=on_progress)` in "
    "ResearchService.generate_report"
)


def _method(tree: ast.Module, cls_name: str, fn_name: str):
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name]
    assert len(classes) == 1, f"expected one top-level class {cls_name}, found {len(classes)}"
    fns = [n for n in classes[0].body
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == fn_name]
    assert len(fns) == 1, f"expected one {cls_name}.{fn_name}, found {len(fns)}"
    return fns[0]


def _parents(root) -> dict:
    parents = {}
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _in_nested_scope(node, parents: dict, root) -> bool:
    up = parents.get(node)
    while up is not None and up is not root:
        if isinstance(up, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            return True
        up = parents.get(up)
    return False


def _is_truth_test(node, parents: dict) -> bool:
    """`if progress_cb:` (also inside `and`/`or`/`not`, or `progress_cb is not None`)."""
    child, up = node, parents.get(node)
    while True:
        if isinstance(up, (ast.If, ast.IfExp, ast.While)) and up.test is child:
            return True
        if isinstance(up, ast.BoolOp) or (isinstance(up, ast.UnaryOp) and isinstance(up.op, ast.Not)):
            child, up = up, parents.get(up)
            continue
        if (isinstance(up, ast.Compare) and up.left is child
                and all(isinstance(c, ast.Constant) for c in up.comparators)):
            child, up = up, parents.get(up)
            continue
        return False


def _is_awaited_call(node, parents: dict) -> bool:
    """`await progress_cb(...)`: the name is the callee, and the call is awaited right there
    (not stored, passed on, or scheduled with create_task)."""
    call = parents.get(node)
    return (isinstance(call, ast.Call) and call.func is node
            and isinstance(parents.get(call), ast.Await) and parents[call].value is call)


def _progress_name(node) -> str | None:
    if isinstance(node, ast.Name):
        name = node.id
    elif isinstance(node, ast.Attribute):
        name = node.attr
    elif isinstance(node, ast.arg):
        name = node.arg
    elif isinstance(node, ast.keyword):
        name = node.arg
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        name = node.name
    else:
        return None
    return name if name and "progress" in name.lower() else None


def _assert_agent_ticks_stay_in_run(source: str) -> None:
    tree = ast.parse(source)
    run = _method(tree, "ResearchAgent", "run")
    params = [a.arg for a in (*run.args.posonlyargs, *run.args.args, *run.args.kwonlyargs)]
    assert "progress_cb" in params, (
        f"{_MSG_NO_PARAM} (params: {params}); re-derive this guard and research_service's "
        "`agent.run(progress_cb=...)` against the new name"
    )
    parents = _parents(run)
    uses = [n for n in ast.walk(run) if isinstance(n, ast.Name) and n.id == "progress_cb"]

    nested = sorted(n.lineno for n in uses if _in_nested_scope(n, parents, run))
    assert not nested, (
        f"{_MSG_NESTED} (line(s) {nested}): a closure over it can be handed to a callee this "
        "test stubs, and the ticks it makes would never run here"
    )
    escapes = sorted(n.lineno for n in uses
                     if not (_is_truth_test(n, parents) or _is_awaited_call(n, parents)))
    assert not escapes, (
        f"{_MSG_ESCAPES} (line(s) {escapes}): the callback leaves run (stored, passed on or "
        "scheduled), and a tick from a callee this test stubs (the agentic loop, Stage A, "
        "Stage B) never executes here"
    )

    calls = sorted((parents[n] for n in uses if _is_awaited_call(n, parents)),
                   key=lambda c: (c.lineno, c.col_offset))
    assert len(calls) >= _AGENT_TICKS_TODAY, (
        f"ResearchAgent.run makes only {len(calls)} direct `await progress_cb(...)` call(s) at "
        f"its own scope, expected at least {_AGENT_TICKS_TODAY}; re-derive _AGENT_TICKS_TODAY "
        "if a tick was removed on purpose"
    )
    values = []
    for call in calls:
        first = call.args[0] if call.args else None
        ok = isinstance(first, ast.Constant) and type(first.value) is int
        assert ok, (
            f"{_MSG_NOT_LITERAL} (line {call.lineno}: {ast.unparse(call)}): a computed value "
            "can step the bar backwards on a path this run does not take"
        )
        values.append((first.value, call.lineno))
    for (v0, l0), (v1, l1) in zip(values, values[1:]):
        assert v1 >= v0, (
            f"{_MSG_SOURCE_ORDER}: {v0} at line {l0} -> {v1} at line {l1}. A tick on a branch "
            f"this run does not take is invisible to the dynamic check (ticks: {values})"
        )

    inside = {id(n) for n in ast.walk(run)}
    outside = sorted({(n.lineno, name) for n in ast.walk(tree) if id(n) not in inside
                      for name in [_progress_name(n)] if name})
    assert not outside, (
        f"{_MSG_OUTSIDE}: {outside}. A progress callback reached through the agent (an "
        "attribute, a parameter of a callee) would tick from code this test stubs"
    )


def _assert_on_progress_only_feeds_run(source: str) -> None:
    tree = ast.parse(source)
    gen = _method(tree, "ResearchService", "generate_report")
    defs = [n for n in ast.walk(gen)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "on_progress"]
    assert len(defs) == 1, (
        f"{_MSG_NO_ON_PROGRESS} (found {len(defs)}); re-derive this guard against the new name"
    )
    parents = _parents(gen)

    def _feeds_run(name) -> bool:
        kw = parents.get(name)
        call = parents.get(kw)
        return (isinstance(kw, ast.keyword) and kw.arg == "progress_cb" and kw.value is name
                and isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == "run" and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "agent")

    uses = [n for n in ast.walk(gen) if isinstance(n, ast.Name) and n.id == "on_progress"]
    strays = sorted(n.lineno for n in uses if not _feeds_run(n))
    assert not strays, (
        f"{_MSG_ON_PROGRESS_STRAYS} at line(s) {strays}: a callback stashed on the agent or "
        "handed elsewhere can tick from code this test stubs"
    )
    feeds = [n for n in uses if _feeds_run(n)]
    assert len(feeds) == 1, (
        f"generate_report passes on_progress to agent.run(progress_cb=...) {len(feeds)} "
        "time(s), expected exactly once"
    )


_GUARDS = {
    "agent": (ra, _assert_agent_ticks_stay_in_run),
    "service": (rs, _assert_on_progress_only_feeds_run),
}


def test_every_agent_tick_is_a_direct_await_in_run():
    _assert_agent_ticks_stay_in_run(pathlib.Path(ra.__file__).read_text(encoding="utf-8"))


def test_on_progress_reaches_the_agent_only_through_run():
    _assert_on_progress_only_feeds_run(pathlib.Path(rs.__file__).read_text(encoding="utf-8"))


_RESEARCH_CALL = "        research_text = await self._agentic_research(out, evidence)\n"
_LOOP_TOP = (
    "        tools = build_fmp_tool_declarations()\n"
    "        handlers = build_tool_handlers(self.fmp)\n"
)
_STAGE_B_CALL = "            run_narrative_jobs(jobs, self.gemini, self.persona, evidence),\n"
_JOBS = "        jobs = build_narrative_jobs(self.persona, evidence, report)\n"
_DEGRADED_TAG = "            report[REPORT_DEGRADED_KEY] = stage_a_degradation\n"
_ROUND_TICK = '            await self.progress_cb(60, "Research round 1 of 4...")\n'

_GUARD_MUTATIONS = [
    # (guard, [(old, new), ...], the assertion message that must fire)
    # Stashed on the agent and ticked from the stubbed agentic loop: 20 -> 60 -> 55.
    ("agent", [(_RESEARCH_CALL, "        self._progress_cb = progress_cb\n" + _RESEARCH_CALL),
               (_LOOP_TOP, '        if getattr(self, "_progress_cb", None):\n'
                           '            await self._progress_cb(60, "Research round 1 of 4...")\n'
                + _LOOP_TOP)],
     _MSG_ESCAPES),
    # Handed to the stubbed Stage B as an argument.
    ("agent", [(_STAGE_B_CALL, _STAGE_B_CALL.replace("evidence),", "evidence, progress_cb),"))],
     _MSG_ESCAPES),
    # Fire-and-forget: the tick may land after a later one.
    ("agent", [('await progress_cb(85, "Writing narrative insights...")',
                'asyncio.create_task(progress_cb(85, "Writing narrative insights..."))')],
     _MSG_ESCAPES),
    # A closure that looks like a plain tick, handed to the stubbed Stage B.
    ("agent", [(_JOBS, "        async def _tick(p, s):\n"
                       "            if progress_cb:\n"
                       "                await progress_cb(p, s)\n\n" + _JOBS),
               (_STAGE_B_CALL, _STAGE_B_CALL.replace("evidence),", "evidence, on_round=_tick),"))],
     _MSG_NESTED),
    # A tick on a branch this run does not take (Stage A is stubbed and never degrades).
    ("agent", [(_DEGRADED_TAG, _DEGRADED_TAG
                + "            if progress_cb:\n"
                + '                await progress_cb(30, "Retrying analysis...")\n')],
     _MSG_SOURCE_ORDER),
    # A value that differs only on a path this run does not take (20 -> 15 on no findings).
    ("agent", [('progress_cb(55, "Deep research complete, synthesizing...")',
                'progress_cb(55 if research_text else 15, '
                '"Deep research complete, synthesizing...")')],
     _MSG_NOT_LITERAL),
    # The stubbed agentic loop reads a callback the service stashed on the agent.
    ("agent", [(_LOOP_TOP, "        if self.progress_cb:\n" + _ROUND_TICK + _LOOP_TOP)],
     _MSG_OUTSIDE),
    # Guard anti-vacuity: renamed parameter, and a removed tick.
    ("agent", [("        progress_cb: Optional[Callable[..., Any]] = None,\n",
                "        on_tick: Optional[Callable[..., Any]] = None,\n")],
     _MSG_NO_PARAM),
    ("agent", [('        if progress_cb:\n'
                '            await progress_cb(55, "Deep research complete, synthesizing...")\n',
                "")],
     "ResearchAgent.run makes only 5 direct `await progress_cb(...)` call(s)"),
    # Service side: the callback stashed on the agent (the other end of the line above).
    ("service", [("                    " + _ON_PROGRESS_WRITE + "\n",
                  "                    " + _ON_PROGRESS_WRITE + "\n"
                  "                agent.progress_cb = on_progress\n")],
     _MSG_ON_PROGRESS_STRAYS),
    ("service", [("progress_cb=on_progress,", "progress_cb=None,")],
     "generate_report passes on_progress to agent.run(progress_cb=...) 0 time(s)"),
    ("service", [("async def on_progress(progress: int, step: str):",
                  "async def on_tick(progress: int, step: str):")],
     _MSG_NO_ON_PROGRESS),
]


@pytest.mark.parametrize(
    "guard,edits,message", _GUARD_MUTATIONS,
    ids=[f"{g}:{i}" for i, (g, _e, _m) in enumerate(_GUARD_MUTATIONS)],
)
def test_each_static_guard_mutation_is_killed(guard, edits, message):
    module, check = _GUARDS[guard]
    mutated = _apply_edits(module, edits)        # anchor checks run OUTSIDE pytest.raises
    ast.parse(mutated)                           # still Python: the kill is the guard's
    with pytest.raises(AssertionError, match=re.escape(message)):
        check(mutated)
