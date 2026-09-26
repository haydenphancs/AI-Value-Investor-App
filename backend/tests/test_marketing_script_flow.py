"""
Phase 2b: the day's script — kick-and-poll, lease-fenced generation, and the server-authored
captions that `create_posts` now insists on (SYSTEM_DESIGN_GUIDELINES §12.5).

Hermetic: a small in-memory PostgREST fake (richer than the one in
`test_marketing_run_service.py` because the script store needs `is_` / `not_` / `lt` /
conditional UPDATEs), a fake writer, and no network.

The fake models the PostgREST semantics the service relies on, because a fake more forgiving
than the server is how a guard goes vacuous: an UPDATE whose filter matches nothing returns
`[]`; a duplicate key raises postgrest's `APIError` with code 23505; ORDER BY applies before
LIMIT; a timestamptz filter compares INSTANTS, not strings; and every statement is atomic (the
service runs them in worker threads through `sb_exec`).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import threading
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest
from postgrest.exceptions import APIError

from app.services.marketing import content_pool
from app.services.marketing import run_service as mrs
from app.services.marketing import script_service as ss
from app.services.marketing import selection
from app.services.marketing.writer_service import WriterResult

# ── fake PostgREST ───────────────────────────────────────────────────────────


class _Res:
    def __init__(self, data):
        self.data = data


def _same(stored: Any, wanted: Any) -> bool:
    """PostgREST `eq`: NULL never matches; a timestamptz column compares by instant."""
    if stored is None:
        return False
    a, b = str(stored), str(wanted)
    if a == b:
        return True
    if "T" in a and "T" in b:
        da, db = ss._parse(a), ss._parse(b)
        return da is not None and da == db
    return False


class _Q:
    def __init__(self, table: "_T", op: str, payload=None):
        self.t, self.op, self.payload = table, op, payload
        self.filters: List = []
        self._limit: Optional[int] = None
        self._order: List[tuple] = []
        self._negate = False

    def select(self, *_a, **_k):
        return self

    @property
    def not_(self):
        self._negate = True
        return self

    def _check_plain(self):
        assert not self._negate, "the fake models not_ only in front of is_"

    def eq(self, c, v):
        self._check_plain()
        self.filters.append(lambda r, c=c, v=v: _same(r.get(c), v))
        return self

    def is_(self, c, v):
        assert v == "null"
        neg, self._negate = self._negate, False
        self.filters.append(lambda r, c=c: (r.get(c) is not None) if neg else (r.get(c) is None))
        return self

    def lt(self, c, v):
        self._check_plain()
        self.filters.append(lambda r, c=c, v=v: r.get(c) is not None and str(r.get(c)) < str(v))
        return self

    def order(self, col, *, desc=False, **_k):
        self._order.append((col, desc))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def in_(self, c, values):
        self._check_plain()
        wanted = [str(v) for v in values]
        self.filters.append(lambda r, c=c: r.get(c) is not None and str(r.get(c)) in wanted)
        return self

    def execute(self):
        # Runs in a worker thread (`sb_exec`). A hook may hold the statement IN FLIGHT before it
        # reaches the table, the way a slow round trip does: cancelling the awaiting coroutine
        # does not stop it, and it can still commit afterwards.
        hook = self.t.before_update if self.op == "update" else self.t.before_select if self.op == "select" else None
        if hook is not None:
            hook(self.payload)
        with self.t.lock:  # one statement is atomic, like Postgres
            return self._execute()

    def _execute(self):
        rows = self.t.rows
        if self.op == "insert":
            row = dict(self.payload)
            for key in self.t.unique:
                if any(all(r.get(k) == row.get(k) for k in key) for r in rows):
                    raise APIError({"code": "23505", "message": "duplicate key value violates unique "
                                    "constraint", "details": None, "hint": None})
            row.setdefault("id", str(uuid.uuid4()))
            for k, v in self.t.defaults.items():
                row.setdefault(k, copy.deepcopy(v))
            rows.append(row)
            return _Res([dict(row)])
        hits = [r for r in rows if all(f(r) for f in self.filters)]
        if self.op == "update":
            for r in hits:
                r.update(copy.deepcopy(self.payload))
            return _Res([dict(r) for r in hits])
        for col, desc in reversed(self._order):  # ORDER BY before LIMIT
            hits = sorted(hits, key=lambda r: str(r.get(col) or ""), reverse=desc)
        if self._limit is not None:
            hits = hits[: self._limit]
        return _Res([dict(r) for r in hits])


class _T:
    def __init__(self, unique=(), defaults=None):
        self.rows: List[Dict[str, Any]] = []
        self.unique = unique
        self.defaults = defaults or {}
        self.lock = threading.Lock()
        self.before_update = None  # callable(payload), run in the statement's thread
        self.before_select = None

    def select(self, *a, **k):
        return _Q(self, "select")

    def insert(self, payload):
        return _Q(self, "insert", payload)

    def update(self, payload):
        return _Q(self, "update", payload)


class FakeSB:
    def __init__(self):
        self.tables = {
            mrs.RUNS: _T([("run_date",)], {"status": "planned", "stage": "planned", "metadata": {},
                                           "timings": {}, "attempts": 0, "dry_run": True}),
            mrs.ASSETS: _T([("storage_path",)], {"status": "pending_upload", "metadata": {}}),
            mrs.POSTS: _T([("idempotency_key",)], {"metadata": {}, "attempts": 0}),
            mrs.SCRIPTS: _T([("run_id",)], {"status": "selected", "generations": 0, "violations": [],
                                            "content_rejections": 0, "fact_sheet": {}, "tokens_used": 0}),
        }

    def table(self, name):
        return self.tables[name]


# A posting day (Thursday) and a rest day (Wednesday), relative to selection.POST_WEEKDAYS.
POSTING_DAY = date(2026, 9, 24)
REST_DAY = date(2026, 9, 23)
assert POSTING_DAY.weekday() in selection.POST_WEEKDAYS
assert REST_DAY.weekday() not in selection.POST_WEEKDAYS
KEY = content_pool.eligible_keys()[0]


def _package(item_key: str) -> Dict[str, Any]:
    return {
        "hook": "A calm idea.", "video_script": ["Line one."], "cards": [], "carousel_slides": [],
        "captions": {"x": "body"}, "posts": {
            "x": {"platform": "x", "title": None, "caption": "server-authored X copy"},
            "youtube": {"platform": "youtube", "title": "Server title", "caption": "server YT copy"},
        },
        "dropped_outlets": {}, "disclaimer_card": "Educational. Caydex", "source_ref": item_key,
        "template_id": "checklist", "model": "gemini-2.5-flash", "prompt_version": "t",
    }


class FakeWriter:
    """Stands in for writer_service.generate_package; records calls, answers from a script.
    `calls` is recorded on entry; `model_calls` only once `before_call` let the model call
    through — the difference is what proves a lost lease stopped the SPEND, not just the write."""

    def __init__(self, outcomes: List[Any], *, rounds: int = 1, honor_skip: bool = False):
        self.outcomes = list(outcomes)
        self.calls: List[Dict[str, Any]] = []
        self.model_calls = 0
        self.rounds = rounds
        self.gate: Optional[asyncio.Event] = None
        # What each `before_call` answered, and whether to honour a False the way the writer's
        # contract asks (skip the call, keep the round-1 draft).
        self.refreshes: List[Any] = []
        self.honor_skip = honor_skip
        self.between_rounds = None  # optional callable(generation_id), run after a round's call

    async def __call__(self, item, template, run_date, *, generation_id, allow_x_url, judge_mode,
                       before_call=None):
        self.calls.append({"item": item.key, "template": template.id, "generation_id": generation_id,
                           "run_date": run_date, "judge_mode": judge_mode})
        for i in range(self.rounds):
            if before_call is not None:
                ok = await before_call()
                self.refreshes.append(ok)
                if ok is False and self.honor_skip and i > 0:
                    break
            self.model_calls += 1
            if self.between_rounds is not None:
                self.between_rounds(generation_id)
        if self.gate is not None:
            await self.gate.wait()
        outcome = self.outcomes.pop(0) if len(self.outcomes) > 1 else self.outcomes[0]
        if isinstance(outcome, BaseException):
            raise outcome
        if outcome == "accepted":
            return WriterResult("accepted", _package(item.key), [], [], 1234)
        return WriterResult("rejected", None, [{"field": "hook", "code": "person_named", "detail": "x"}], [], 99)


@pytest.fixture
def world(monkeypatch):
    sb = FakeSB()
    runs = mrs.MarketingRunService(supabase=sb)
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", False)
    monkeypatch.setattr(mrs.settings, "MARKETING_RUN_STALE_SECONDS", 2700)
    # The claim window is computed from the ET date; pin it so the fixed POSTING_DAY is "today".
    monkeypatch.setattr(ss, "_today_et", lambda: POSTING_DAY)
    # No real back-off sleeps in retried ledger writes.
    monkeypatch.setattr(ss, "_FINISH_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(ss, "_REFRESH_BACKOFF_SECONDS", 0.0)
    return sb, runs


def _iso_ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat()


def _iso_ahead(**kw) -> str:
    return (datetime.now(timezone.utc) + timedelta(**kw)).isoformat()


#: The claim nonce every seeded run carries (what claim_run writes into metadata).
NONCE = "0123456789abcdef0123456789abcdef"


def _run(sb: FakeSB, day: date, **extra) -> str:
    """A run the worker HOLDS: in_progress and claimed just now (what claim_run writes)."""
    rid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    sb.tables[mrs.RUNS].rows.append({"id": rid, "run_date": day.isoformat(), "status": "in_progress",
                                     "stage": "planned", "metadata": {"claim_nonce": NONCE},
                                     "timings": {}, "attempts": 1,
                                     "dry_run": True, "started_at": now, "updated_at": now, **extra})
    return rid


def _holder_of(runs: Any, run_id: str) -> mrs.CallerClaim:
    return _holder(type("S", (), {"runs": runs})(), run_id)


def _holder(svc: Any, run_id: str) -> mrs.CallerClaim:
    """The claim of whoever holds `run_id` NOW (its row's attempts + nonce) — the caller every
    kick in this file speaks as, so these tests keep exercising the held/state logic behind the
    caller-claim fence. The fence itself is tested below with a zombie's claim."""
    for r in svc.runs.sb.tables[mrs.RUNS].rows:
        if r.get("id") == run_id:
            meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
            return mrs.CallerClaim(int(r.get("attempts") or 1), meta.get("claim_nonce") or NONCE)
    return mrs.CallerClaim(1, NONCE)


def _seed(sb: FakeSB, rid: str, **fields) -> Dict[str, Any]:
    row = {"run_id": rid, "run_date": POSTING_DAY.isoformat(), "status": "selected", "source_ref": KEY,
           "template_id": "three_takeaways", "generations": 0, "content_rejections": 0,
           "violations": [], "fact_sheet": {}, "tokens_used": 0, **fields}
    sb.tables[mrs.SCRIPTS].rows.append(row)
    return row


async def _drain(svc: ss.MarketingScriptService) -> None:
    for _ in range(50):
        if not svc._tasks:
            return
        await asyncio.sleep(0)
        await asyncio.gather(*list(svc._tasks), return_exceptions=True)


def _script_row(sb, rid):
    return next(r for r in sb.tables[mrs.SCRIPTS].rows if r["run_id"] == rid)


def _run_row(sb, rid):
    return next(r for r in sb.tables[mrs.RUNS].rows if r["id"] == rid)


async def _kick_until_final(svc, sb, rid, *, limit=20) -> List[Dict[str, Any]]:
    """Kick, let the generation finish, and clear the back-off (= the next hourly tick)."""
    seen = []
    for _ in range(limit):
        state = await svc.kick(rid, claim=_holder(svc, rid))
        seen.append(state)
        await _drain(svc)
        _script_row(sb, rid)["retry_not_before"] = None
        if state["status"] in ("rejected", "accepted", "rest_day"):
            break
    return seen


# ── kick: selection ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rest_day_selects_nothing_and_never_calls_the_writer(world):
    sb, runs = world
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, REST_DAY)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert state["status"] == "rest_day" and writer.calls == []
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["status"] == "rest_day"  # final, idempotent
    assert _script_row(sb, rid)["run_date"] == REST_DAY.isoformat()


@pytest.mark.asyncio
async def test_first_kick_selects_once_and_mirrors_onto_the_run(world):
    sb, runs = world
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "generating" and state["source_ref"] in content_pool.eligible_keys()
    row = _script_row(sb, rid)
    assert row["fact_sheet"]["key"] == state["source_ref"] and row["fact_sheet"]["sentences"]
    assert row["run_date"] == POSTING_DAY.isoformat()
    run = _run_row(sb, rid)
    assert run["source_ref"] == state["source_ref"] and run["content_class"] == "A"
    await _drain(svc)
    again = await svc.kick(rid, claim=_holder(svc, rid))
    assert again["status"] == "accepted" and again["source_ref"] == state["source_ref"]
    assert len(sb.tables[mrs.SCRIPTS].rows) == 1  # selection happened exactly once


@pytest.mark.asyncio
async def test_selection_skips_recent_picks_read_from_the_script_rows(world):
    """`recent` reads marketing_scripts ITSELF. Yesterday's run carries NO mirror here — the
    old read of marketing_runs.source_ref would see nothing and repeat the pick."""
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    first = selection.choose(content_pool.eligible_keys(), POSTING_DAY).source_ref
    yesterday = POSTING_DAY - timedelta(days=1)
    yrid = _run(sb, yesterday)
    _seed(sb, yrid, run_date=yesterday.isoformat(), status="rejected", source_ref=first,
          reject_reason="content")
    assert _run_row(sb, yrid).get("source_ref") is None
    rid = _run(sb, POSTING_DAY)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert state["source_ref"] != first


@pytest.mark.asyncio
async def test_a_lost_mirror_write_is_healed_by_the_next_kick_and_recent_never_needed_it(world):
    sb, runs = world

    class Flaky(mrs.MarketingRunService):
        fail = 1

        async def update_run(self, run_id, **kw):
            if kw.get("source_ref") and Flaky.fail:
                Flaky.fail -= 1
                raise mrs.MarketingRunError("update_run failed (run_id=x): APIError: 520")
            return await super().update_run(run_id, **kw)

    svc = ss.MarketingScriptService(Flaky(supabase=sb), writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert _run_row(sb, rid).get("source_ref") is None          # the mirror write was lost
    # Tomorrow's selection already sees today's pick (script row), mirror or not.
    assert await runs.recent_source_refs(POSTING_DAY + timedelta(days=1), 5) == [state["source_ref"]]
    await _drain(svc)
    await svc.kick(rid, claim=_holder(svc, rid))
    assert _run_row(sb, rid)["source_ref"] == state["source_ref"]  # healed by the next poll


@pytest.mark.asyncio
async def test_a_lost_insert_response_is_adopted_and_still_mirrored(world):
    sb, runs = world

    class LostResponse(mrs.MarketingRunService):
        fired = False

        async def insert_script(self, row):
            out = await super().insert_script(row)
            if not LostResponse.fired:
                LostResponse.fired = True
                raise mrs.MarketingRunError("insert_script failed (run_id=x): APIError: 520")
            return out

    svc = ss.MarketingScriptService(LostResponse(supabase=sb), writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    with pytest.raises(mrs.MarketingRunError):
        await svc.kick(rid, claim=_holder(svc, rid))
    state = await svc.kick(rid, claim=_holder(svc, rid))  # the worker's 5xx retry adopts the committed row
    assert state["status"] == "generating"
    assert _run_row(sb, rid)["source_ref"] == state["source_ref"] == _script_row(sb, rid)["source_ref"]
    await _drain(svc)


@pytest.mark.asyncio
async def test_empty_pool_is_a_loud_final_rejection(world, monkeypatch, caplog):
    sb, runs = world
    monkeypatch.setattr(ss.content_pool, "eligible_keys", lambda: [])
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level("ERROR"):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "empty_pool" and state["violations"] == []
    assert any("EMPTY content pool" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_unknown_run_raises_the_not_found_class(world):
    _sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    with pytest.raises(mrs.MarketingRunNotFound):
        await svc.kick(str(uuid.uuid4()), claim=mrs.CallerClaim(1, NONCE))


# ── kick: only a HELD run may start writer spend ─────────────────────────────


@pytest.mark.parametrize("label, day_offset, extra", [
    ("closed skipped run, 111 days old", -111, {"status": "skipped"}),
    ("closed skipped run, today", 0, {"status": "skipped"}),
    ("failed run", 0, {"status": "failed"}),
    ("in_progress two days ago", -2, {}),
    ("in_progress tomorrow", 1, {}),
    ("stale claim", 0, {"started_at": _iso_ago(hours=2), "updated_at": _iso_ago(hours=2)}),
    ("no claim time", 0, {"started_at": None, "updated_at": None}),
])
@pytest.mark.asyncio
async def test_a_kick_never_starts_spend_for_a_run_nobody_holds(world, label, day_offset, extra):
    sb, runs = world
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY + timedelta(days=day_offset), **extra)
    with pytest.raises(mrs.MarketingRunNotHeld):
        await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert writer.calls == [], label
    assert sb.tables[mrs.SCRIPTS].rows == [], label        # no selection either
    # No script row exists, so this cannot reach `_heal_mirror`; the mirror's own held gate is
    # pinned by `test_a_kick_on_an_unheld_run_neither_mirrors_nor_revives_it` below.
    assert _run_row(sb, rid).get("source_ref") is None, label


@pytest.mark.parametrize("zombie", [
    mrs.CallerClaim(2, NONCE),                             # a later attempt number
    mrs.CallerClaim(1, "f" * 32),                          # the right attempts, another nonce
], ids=["wrong-attempts", "wrong-nonce"])
@pytest.mark.parametrize("with_row", [False, True], ids=["fresh", "selected-row"])
@pytest.mark.asyncio
async def test_a_zombie_kick_is_refused_before_any_selection_mirror_or_spend(world, zombie, with_row):
    """The caller-claim check runs FIRST in `kick` (review 2026-09-26: moving it after
    `_select` or `_heal_mirror` passed every test, and on a rest day even after `_advance`).
    Pinned on the fixed POSTING_DAY, so the result does not depend on today's weekday: no
    script row is selected, the run's mirror and liveness are untouched, the writer never runs."""
    sb, runs = world
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY, updated_at=_iso_ago(minutes=5))
    if with_row:
        _seed(sb, rid)
    before_run = copy.deepcopy(_run_row(sb, rid))
    before_rows = copy.deepcopy(sb.tables[mrs.SCRIPTS].rows)
    with pytest.raises(mrs.MarketingRunNotHeld):
        await svc.kick(rid, claim=zombie)
    await _drain(svc)
    assert writer.calls == []
    assert sb.tables[mrs.SCRIPTS].rows == before_rows
    assert _run_row(sb, rid) == before_run


@pytest.mark.asyncio
async def test_yesterdays_held_run_still_resumes(world):
    """The out-of-window resume works on YESTERDAY's run — the window must not cut it off."""
    sb, runs = world
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY - timedelta(days=1))
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["status"] in ("generating", "rest_day")


@pytest.mark.asyncio
async def test_a_selected_row_of_an_unheld_run_does_not_spawn(world):
    sb, runs = world
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY, status="failed")
    _seed(sb, rid)
    touched = _run_row(sb, rid)["updated_at"]
    with pytest.raises(mrs.MarketingRunNotHeld):
        await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert writer.calls == [] and _script_row(sb, rid)["status"] == "selected"
    assert _run_row(sb, rid).get("source_ref") is None and _run_row(sb, rid)["updated_at"] == touched


@pytest.mark.asyncio
async def test_an_expired_lease_of_an_unheld_run_is_not_taken_over(world):
    sb, runs = world
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY, status="skipped")
    _seed(sb, rid, status="generating", generation_id="dead", generations=1, lease_until=_iso_ago(hours=1))
    touched = _run_row(sb, rid)["updated_at"]
    with pytest.raises(mrs.MarketingRunNotHeld):
        await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert writer.calls == [] and _script_row(sb, rid)["generation_id"] == "dead"
    assert _run_row(sb, rid).get("source_ref") is None and _run_row(sb, rid)["updated_at"] == touched


@pytest.mark.asyncio
async def test_final_rows_answer_idempotently_whatever_the_run_state(world):
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY - timedelta(days=90), status="skipped")
    _seed(sb, rid, status="accepted", output=_package(KEY), generation_id=str(uuid.uuid4()))
    touched = _run_row(sb, rid)["updated_at"]
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["status"] == "accepted"
    assert _run_row(sb, rid).get("source_ref") is None and _run_row(sb, rid)["updated_at"] == touched


# ── generation: single flight, fencing, outcomes ────────────────────────────


@pytest.mark.asyncio
async def test_accepted_script_returns_only_the_worker_subset(world):
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    script = state["script"]
    assert set(script) == {"hook", "video_script", "cards", "carousel_slides", "disclaimer_card", "outlets"}
    assert script["outlets"] == ["x", "youtube"]
    row = _script_row(sb, rid)
    assert row["status"] == "accepted" and row["lease_until"] is None and row["tokens_used"] == 1234
    assert row["fact_sheet"]["sentences"]  # the sheet the package was grounded on


@pytest.mark.asyncio
async def test_the_accepted_row_records_the_fact_sheet_it_was_grounded_on(world, monkeypatch, caplog):
    """Each generation grounds against the LIVE bundle. A deploy that edits the item between
    selection and acceptance must not leave an audit copy the output was never checked against."""
    import dataclasses

    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    original = content_pool.get_item(KEY)
    _seed(sb, rid, fact_sheet=ss._fact_sheet_snapshot(original))
    edited = dataclasses.replace(original, fact_sentences=("EDITED sentence after selection.",))
    real_get = content_pool.get_item
    monkeypatch.setattr(ss.content_pool, "get_item", lambda k: edited if k == KEY else real_get(k))
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    row = _script_row(sb, rid)
    assert row["status"] == "accepted"
    assert row["fact_sheet"]["sentences"] == ["EDITED sentence after selection."]
    assert any("changed since selection" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_concurrent_kicks_start_exactly_one_generation(world):
    sb, runs = world
    writer = FakeWriter(["accepted"])
    writer.gate = asyncio.Event()
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    states = await asyncio.gather(*(svc.kick(rid, claim=_holder(svc, rid)) for _ in range(5)))
    for _ in range(5):
        await asyncio.sleep(0)
    assert {s["status"] for s in states} == {"generating"}
    writer.gate.set()
    await _drain(svc)
    assert len(writer.calls) == 1


@pytest.mark.asyncio
async def test_a_second_process_cannot_steal_a_live_lease(world):
    """Two service instances = a redeploy's overlapping containers. B's kick sees the live lease
    and never spawns (`_advance`'s pre-check); the DB-level refusal is pinned separately below."""
    sb, runs = world
    w1, w2 = FakeWriter(["accepted"]), FakeWriter(["accepted"])
    w1.gate = asyncio.Event()
    a, b = ss.MarketingScriptService(runs, writer=w1), ss.MarketingScriptService(runs, writer=w2)
    rid = _run(sb, POSTING_DAY)
    await a.kick(rid, claim=_holder(a, rid))
    for _ in range(200):
        if w1.calls:
            break
        await asyncio.sleep(0.01)
    assert _script_row(sb, rid)["status"] == "generating"
    assert (await b.kick(rid, claim=_holder(b, rid)))["status"] == "generating"
    await _drain(b)
    assert w2.calls == []  # live lease: B must not generate
    w1.gate.set()
    await _drain(a)
    assert len(w1.calls) == 1 and _script_row(sb, rid)["status"] == "accepted"


@pytest.mark.asyncio
async def test_an_expired_lease_is_taken_over_and_the_old_result_is_fenced_out(world):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    stale_gen = str(uuid.uuid4())
    _seed(sb, rid, status="generating", generation_id=stale_gen, generations=1,
          lease_until=_iso_ago(minutes=1))
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["status"] == "generating"
    await _drain(svc)
    row = _script_row(sb, rid)
    assert len(writer.calls) == 1 and row["status"] == "accepted"
    assert row["generation_id"] != stale_gen and row["generations"] == 2
    assert await svc._finish(rid, stale_gen, {"status": "rejected"}) == ss.SUPERSEDED
    assert _script_row(sb, rid)["status"] == "accepted"


# The fences are CONJUNCTIONS; each test below seeds a row that exactly ONE conjunct rejects.


@pytest.mark.asyncio
async def test_a_superseded_generation_cannot_write_over_the_live_owner(world):
    """generation_id half: the row is `generating` (status matches) but held by B."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, status="generating", generation_id="gen-B", generations=2, lease_until=_iso_ahead(minutes=5))
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    out = await svc._finish(rid, "gen-A", {"status": "accepted", "output": _package(KEY)})
    row = _script_row(sb, rid)
    assert out == ss.SUPERSEDED and row.get("output") is None and row["generation_id"] == "gen-B"
    assert row["status"] == "generating"


@pytest.mark.asyncio
async def test_an_accepted_row_is_immutable_against_its_own_late_hand_back(world):
    """status half: the id matches (a cancel landing after our accepted write committed), but
    `accepted` must never be reopened — posts are built from it."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, status="accepted", generation_id="gen-A", output=_package(KEY), generations=1)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    assert await svc._finish(rid, "gen-A", {"status": "selected"}) == ss.SUPERSEDED
    assert _script_row(sb, rid)["status"] == "accepted"


@pytest.mark.asyncio
async def test_losing_the_lease_mid_generation_stops_the_spend_not_just_the_write(world):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)

    class Thief(FakeWriter):
        async def __call__(self, item, template, run_date, *, generation_id, allow_x_url, judge_mode,
                           before_call=None):
            _script_row(sb, rid)["generation_id"] = "someone-else"  # a takeover happened
            return await super().__call__(item, template, run_date, generation_id=generation_id,
                                          allow_x_url=allow_x_url, judge_mode=judge_mode,
                                          before_call=before_call)

    thief = Thief(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=thief)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert thief.model_calls == 0  # the refresh raised LeaseLost BEFORE the model call
    assert _script_row(sb, rid)["status"] == "generating"  # untouched by the loser
    assert _script_row(sb, rid)["generation_id"] == "someone-else"


@pytest.mark.asyncio
async def test_a_ledger_blip_on_the_lease_refresh_keeps_the_generation(world, caplog):
    """A refresh that ERRORS is not a lost lease: the generation continues (every terminal
    write is fenced) instead of throwing away a paid draft as a writer failure."""
    sb, runs = world

    class LedgerDownFromSecondRefresh(mrs.MarketingRunService):
        """A permanent outage from the repair round's refresh on (every attempt fails)."""
        refreshes = 0

        async def update_script_where(self, run_id, patch, *, expect):
            if set(patch) == {"lease_until"}:
                LedgerDownFromSecondRefresh.refreshes += 1
                if LedgerDownFromSecondRefresh.refreshes >= 2:
                    raise mrs.MarketingRunError("update_script failed (run_id=x): APIError: 520")
            return await super().update_script_where(run_id, patch, expect=expect)

    writer = FakeWriter(["accepted"], rounds=2)  # draft + repair, a refresh before each
    svc = ss.MarketingScriptService(LedgerDownFromSecondRefresh(supabase=sb), writer=writer)
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    assert writer.model_calls == 2 and _script_row(sb, rid)["status"] == "accepted"
    assert any("lease NOT refreshed" in r.getMessage() for r in caplog.records)
    assert not any("writer FAILED" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_content_rejection_retries_then_rejects_for_the_day(world):
    sb, runs = world
    writer = FakeWriter(["rejected"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    seen = await _kick_until_final(svc, sb, rid)
    assert seen[-1]["status"] == "rejected" and len(writer.calls) == ss.MAX_GENERATIONS
    assert seen[-1]["reason"] == "content" and seen[-1]["violations"] == ["person_named"]
    row = _script_row(sb, rid)
    assert row["status"] == "rejected" and row["violations"][0]["code"] == "person_named"
    assert row["reject_reason"] == "content" and row["content_rejections"] == ss.MAX_GENERATIONS
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["violations"] == ["person_named"]


@pytest.mark.asyncio
async def test_a_gemini_failure_defers_with_a_retry_time(world):
    sb, runs = world
    from app.integrations.gemini import GeminiTimeoutError

    writer = FakeWriter([GeminiTimeoutError("slow"), "accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "deferred" and state["retry_after_seconds"] > 60
    row = _script_row(sb, rid)
    assert row["status"] == "selected" and "GeminiTimeoutError" in row["last_error"]
    assert len(writer.calls) == 1  # no retry until retry_not_before passes


# ── the two caps: an outage is not a content verdict ────────────────────────


@pytest.mark.asyncio
async def test_a_writer_outage_ends_writer_unavailable_never_content_rejected(world):
    sb, runs = world
    from app.integrations.gemini import GeminiQuotaError

    writer = FakeWriter([GeminiQuotaError("Gemini quota circuit open (resource_exhausted) — failing fast")])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    seen = await _kick_until_final(svc, sb, rid)
    final = seen[-1]
    assert final["status"] == "rejected" and final["reason"] == "writer_unavailable"
    assert final["violations"] == []
    assert len(writer.calls) == ss.MAX_WRITER_FAILURES
    row = _script_row(sb, rid)
    assert row["content_rejections"] == 0 and row["reject_reason"] == "writer_unavailable"


@pytest.mark.asyncio
async def test_outages_do_not_burn_the_days_content_attempts(world):
    sb, runs = world
    from app.integrations.gemini import GeminiTimeoutError

    writer = FakeWriter([GeminiTimeoutError("slow")] * (ss.MAX_WRITER_FAILURES - 1) + ["rejected"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    seen = await _kick_until_final(svc, sb, rid)
    assert seen[-1]["reason"] == "content"
    assert len(writer.calls) == ss.MAX_WRITER_FAILURES - 1 + ss.MAX_GENERATIONS


@pytest.mark.asyncio
async def test_an_outage_after_a_content_round_never_cites_the_stale_code(world):
    sb, runs = world
    from app.integrations.gemini import GeminiTimeoutError

    writer = FakeWriter(["rejected"] + [GeminiTimeoutError("slow")] * ss.MAX_WRITER_FAILURES)
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    final = (await _kick_until_final(svc, sb, rid))[-1]
    assert final["reason"] == "writer_unavailable" and final["violations"] == []
    # The content round is not a failure: all MAX_WRITER_FAILURES outages were allowed.
    assert len(writer.calls) == 1 + ss.MAX_WRITER_FAILURES
    assert _script_row(sb, rid)["content_rejections"] == 1


@pytest.mark.asyncio
async def test_a_failed_generation_records_the_tokens_it_already_spent(world):
    sb, runs = world
    from app.integrations.gemini import GeminiTimeoutError

    err = GeminiTimeoutError("repair timed out")
    setattr(err, "marketing_tokens_used", 777)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter([err, "accepted"]))
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert _script_row(sb, rid)["tokens_used"] == 777


def test_the_tokens_attribute_is_the_writers():
    from app.services.marketing import writer_service

    assert ss._TOKENS_ATTR == writer_service.TOKENS_ATTR


# ── a generation that dies at a cap is closed, never wedged ─────────────────


@pytest.mark.asyncio
async def test_a_dead_owner_at_the_failure_cap_is_closed_rejected_on_the_first_kick(world):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, status="generating", generation_id=str(uuid.uuid4()),
          generations=3 + ss.MAX_WRITER_FAILURES, content_rejections=3,
          lease_until=_iso_ago(hours=5), violations=[{"code": "person_named"}])
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    states = []
    for _ in range(3):
        states.append(await svc.kick(rid, claim=_holder(svc, rid)))
        await _drain(svc)
    assert [s["status"] for s in states] == ["rejected"] * 3
    assert states[0]["reason"] == "writer_unavailable" and states[0]["violations"] == []
    assert writer.calls == []
    row = _script_row(sb, rid)
    assert row["status"] == "rejected" and row["reject_reason"] == "writer_unavailable"
    assert row["lease_until"] is None and "lost its lease" in row["last_error"]


@pytest.mark.asyncio
async def test_a_dead_owner_below_the_caps_is_taken_over_without_burning_a_content_attempt(world):
    """The finding's own scenario: three content rejections, then generation 4 dies. That death
    is a failure, not a verdict — the day still gets its fourth content attempt."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, status="generating", generation_id=str(uuid.uuid4()), generations=4,
          content_rejections=3, lease_until=_iso_ago(minutes=10))
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["status"] == "generating"
    await _drain(svc)
    row = _script_row(sb, rid)
    assert len(writer.calls) == 1 and row["status"] == "accepted" and row["generations"] == 5


@pytest.mark.asyncio
async def test_a_live_owner_at_the_cap_is_left_alone(world):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    gen = str(uuid.uuid4())
    _seed(sb, rid, status="generating", generation_id=gen, generations=3 + ss.MAX_WRITER_FAILURES,
          content_rejections=3, lease_until=_iso_ahead(minutes=5))
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["status"] == "generating"
    await _drain(svc)
    row = _script_row(sb, rid)
    assert writer.calls == [] and row["status"] == "generating" and row["generation_id"] == gen


@pytest.mark.asyncio
async def test_a_lost_terminal_write_on_the_last_failure_is_closed_by_the_next_kick(world, monkeypatch):
    sb, runs = world
    from app.integrations.gemini import GeminiTimeoutError

    class LoseRejected(mrs.MarketingRunService):
        async def update_script_where(self, run_id, patch, *, expect):
            if patch.get("status") == "rejected" and "generation_id" in expect and "generations" not in expect:
                raise mrs.MarketingRunError("update_script failed: APIError: 520")
            return await super().update_script_where(run_id, patch, expect=expect)

    writer = FakeWriter([GeminiTimeoutError("slow")])
    svc = ss.MarketingScriptService(LoseRejected(supabase=sb), writer=writer)
    rid = _run(sb, POSTING_DAY)
    for _ in range(ss.MAX_WRITER_FAILURES):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
        _script_row(sb, rid)["retry_not_before"] = None
    row = _script_row(sb, rid)
    assert row["status"] == "generating" and row["generations"] == ss.MAX_WRITER_FAILURES  # lost
    later = datetime.now(timezone.utc) + timedelta(seconds=ss.LEASE_SECONDS + 5)
    monkeypatch.setattr(ss, "_now", lambda: later)
    run = _run_row(sb, rid)
    run["started_at"] = run["updated_at"] = later.isoformat()
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "writer_unavailable"
    assert len(writer.calls) == ss.MAX_WRITER_FAILURES


@pytest.mark.asyncio
async def test_the_acquire_closes_a_row_that_reached_the_cap_after_the_kick_read_it(world):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, status="selected", generation_id=str(uuid.uuid4()), generations=ss.MAX_WRITER_FAILURES)
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    await svc._generate(rid)  # a task spawned from a kick that still saw the row below the cap
    row = _script_row(sb, rid)
    assert writer.calls == [] and row["status"] == "rejected" and row["reject_reason"] == "writer_unavailable"


@pytest.mark.asyncio
async def test_a_finalize_that_loses_to_a_live_owners_accept_answers_accepted(world):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    gen = str(uuid.uuid4())
    _seed(sb, rid, status="generating", generation_id=gen, generations=3 + ss.MAX_WRITER_FAILURES,
          content_rejections=3, lease_until=_iso_ago(seconds=1))

    class AliveOwner(mrs.MarketingRunService):
        fired = False

        async def update_script_where(self, run_id, patch, *, expect):
            if patch.get("status") == "rejected" and not AliveOwner.fired:
                AliveOwner.fired = True
                # the "dead" owner was only slow: its fenced accepted write lands first
                _script_row(sb, rid).update({"status": "accepted", "output": _package(KEY), "lease_until": None})
            return await super().update_script_where(run_id, patch, expect=expect)

    svc = ss.MarketingScriptService(AliveOwner(supabase=sb), writer=FakeWriter(["accepted"]))
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "accepted" and _script_row(sb, rid)["status"] == "accepted"


@pytest.mark.asyncio
async def test_a_finalize_is_fenced_on_the_observed_lease(world):
    """The owner refreshed between the kick's read and the finalize CAS: it is alive. Without
    `lease_until` in the CAS the day would be closed under a live generation."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    gen = str(uuid.uuid4())
    _seed(sb, rid, status="generating", generation_id=gen, generations=3 + ss.MAX_WRITER_FAILURES,
          content_rejections=3, lease_until=_iso_ago(seconds=1))

    class RefreshFirst(mrs.MarketingRunService):
        fired = False

        async def update_script_where(self, run_id, patch, *, expect):
            if patch.get("status") == "rejected" and not RefreshFirst.fired:
                RefreshFirst.fired = True
                _script_row(sb, rid)["lease_until"] = ss._iso(datetime.now(timezone.utc) + timedelta(minutes=5))
            return await super().update_script_where(run_id, patch, expect=expect)

    svc = ss.MarketingScriptService(RefreshFirst(supabase=sb), writer=FakeWriter(["accepted"]))
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "generating" and _script_row(sb, rid)["status"] == "generating"


@pytest.mark.asyncio
async def test_a_shutdown_hand_back_at_the_cap_is_finalized_by_the_next_kick(world):
    sb, runs = world
    writer = FakeWriter(["accepted"])
    writer.gate = asyncio.Event()  # never set: the generation hangs until cancelled
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, generations=ss.MAX_WRITER_FAILURES - 1)
    await svc.kick(rid, claim=_holder(svc, rid))
    for _ in range(200):
        if writer.calls:
            break
        await asyncio.sleep(0.01)
    await svc.shutdown(timeout=2)
    assert _script_row(sb, rid)["status"] == "selected"
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "writer_unavailable"


# ── the DB-level arbiters: fed stale snapshots, not masked by _running / _advance ──


@pytest.mark.asyncio
async def test_two_acquirers_on_one_snapshot_yield_exactly_one_owner(world):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid)
    barrier = asyncio.Barrier(2)

    class ReadTogether(mrs.MarketingRunService):
        async def get_script(self, run_id):
            row = await super().get_script(run_id)
            await barrier.wait()  # both have read before either writes
            return row

    a = ss.MarketingScriptService(ReadTogether(supabase=sb), writer=FakeWriter(["accepted"]))
    b = ss.MarketingScriptService(ReadTogether(supabase=sb), writer=FakeWriter(["accepted"]))
    got = await asyncio.gather(a._acquire(rid, "gen-a"), b._acquire(rid, "gen-b"))
    assert sum(r is not None for r in got) == 1
    assert _script_row(sb, rid)["generations"] == 1


@pytest.mark.asyncio
async def test_a_stale_task_cannot_take_a_live_lease(world):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, status="generating", generation_id="live", generations=1, lease_until=_iso_ahead(minutes=5))
    writer = FakeWriter(["accepted"])
    await ss.MarketingScriptService(runs, writer=writer)._generate(rid)
    assert writer.calls == [] and _script_row(sb, rid)["generation_id"] == "live"


@pytest.mark.asyncio
async def test_a_stale_task_cannot_regenerate_a_final_row(world):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, status="accepted", generation_id="done", generations=1, output=_package(KEY))
    writer = FakeWriter(["accepted"])
    await ss.MarketingScriptService(runs, writer=writer)._generate(rid)
    row = _script_row(sb, rid)
    assert writer.calls == [] and row["generations"] == 1 and row["output"] == _package(KEY)


@pytest.mark.asyncio
async def test_a_stale_task_honours_the_back_off(world):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, generations=1, retry_not_before=_iso_ahead(minutes=20))
    writer = FakeWriter(["accepted"])
    await ss.MarketingScriptService(runs, writer=writer)._generate(rid)
    assert writer.calls == []


@pytest.mark.asyncio
async def test_the_takeover_cas_is_fenced_on_the_observed_lease(world):
    """We read an EXPIRED lease; the owner refreshed before our write. Status, generations and
    generation_id all still match — only `lease_until` tells the owner is alive."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    live = _seed(sb, rid, status="generating", generation_id="owner", generations=1,
                 lease_until=ss._iso(datetime.now(timezone.utc) + timedelta(minutes=5)))
    stale = dict(live, lease_until=_iso_ago(seconds=5))

    class StaleRead(mrs.MarketingRunService):
        async def get_script(self, run_id):
            return dict(stale)

    got = await ss.MarketingScriptService(StaleRead(supabase=sb), writer=FakeWriter(["accepted"]))._acquire(rid, "taker")
    assert got is None and _script_row(sb, rid)["generation_id"] == "owner"


# ── outcome logs follow what the ledger says ─────────────────────────────────


@pytest.mark.asyncio
async def test_a_landed_write_whose_response_was_lost_is_logged_as_written(world, caplog):
    sb, runs = world

    class LandThenRaise(mrs.MarketingRunService):
        fired = False

        async def update_script_where(self, run_id, patch, *, expect):
            out = await super().update_script_where(run_id, patch, expect=expect)
            if patch.get("status") == "accepted" and not LandThenRaise.fired:
                LandThenRaise.fired = True
                raise mrs.MarketingRunError("update_script failed: 520 after commit")
            return out

    svc = ss.MarketingScriptService(LandThenRaise(supabase=sb), writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    msgs = [r.getMessage() for r in caplog.records]
    assert _script_row(sb, rid)["status"] == "accepted"
    assert any("landed" in m for m in msgs)
    assert not any("matched nothing" in m for m in msgs)
    assert any(m.startswith("marketing script ACCEPTED") for m in msgs)


@pytest.mark.asyncio
async def test_a_lost_accepted_write_is_never_logged_as_accepted(world, caplog):
    sb, runs = world

    class AlwaysRaise(mrs.MarketingRunService):
        async def update_script_where(self, run_id, patch, *, expect):
            if patch.get("status") == "accepted":
                raise mrs.MarketingRunError("update_script failed: 520")
            return await super().update_script_where(run_id, patch, expect=expect)

    svc = ss.MarketingScriptService(AlwaysRaise(supabase=sb), writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    msgs = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert not any(m.startswith("marketing script ACCEPTED") for _, m in msgs)
    assert any(lvl == logging.ERROR and "NOT recorded" in m for lvl, m in msgs)


@pytest.mark.asyncio
async def test_a_failed_hand_back_during_shutdown_is_logged(world, monkeypatch, caplog):
    sb, runs = world
    monkeypatch.setattr(ss, "HAND_BACK_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(ss, "_FINISH_BACKOFF_SECONDS", 5.0)  # the hand-back runs out of time

    class HandBackFails(mrs.MarketingRunService):
        async def update_script_where(self, run_id, patch, *, expect):
            if patch.get("status") == "selected":
                raise mrs.MarketingRunError("update_script failed: 520")
            return await super().update_script_where(run_id, patch, expect=expect)

    writer = FakeWriter(["accepted"])
    writer.gate = asyncio.Event()
    svc = ss.MarketingScriptService(HandBackFails(supabase=sb), writer=writer)
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    for _ in range(200):
        if writer.calls:
            break
        await asyncio.sleep(0.01)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.shutdown(timeout=2)
    assert any("hand-back" in r.getMessage() and "backstop" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_bug_after_the_lease_is_taken_hands_the_run_back(world, monkeypatch, caplog):
    sb, runs = world

    def boom(_key):
        raise RuntimeError("bundle unreadable")

    monkeypatch.setattr(ss.content_pool, "get_item", boom)
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    row = _script_row(sb, rid)
    assert row["status"] == "selected" and row["lease_until"] is None and row["retry_not_before"]
    assert any("CRASHED" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_ledger_blip_before_the_model_call_hands_the_run_back(world, caplog):
    sb, runs = world

    class RunReadBlip(mrs.MarketingRunService):
        calls = 0

        async def get_run(self, run_id):
            RunReadBlip.calls += 1
            if RunReadBlip.calls == 2:  # the generation's own read (a legacy row without run_date)
                raise mrs.MarketingRunError("get_run failed: 520")
            return await super().get_run(run_id)

    rid = _run(sb, POSTING_DAY, source_ref=KEY)  # mirrored already: no update_run read
    row = _seed(sb, rid)
    row.pop("run_date")
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(RunReadBlip(supabase=sb), writer=writer)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    assert writer.calls == [] and _script_row(sb, rid)["status"] == "selected"
    assert any("ledger FAILED before the model call" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_an_item_that_became_ineligible_is_rejected_not_generated(world, monkeypatch):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, source_ref="money_moves:warren-buffetts-early-days", template_id="case_story")
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert writer.calls == [] and _script_row(sb, rid)["status"] == "rejected"
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["reason"] == "source_ineligible" and state["violations"] == []


@pytest.mark.asyncio
async def test_shutdown_hands_the_run_back(world):
    sb, runs = world
    writer = FakeWriter(["accepted"])
    writer.gate = asyncio.Event()  # never set: the generation hangs until cancelled
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    for _ in range(200):  # until the generation is inside the writer (thread hops take time)
        if writer.calls:
            break
        await asyncio.sleep(0.01)
    assert writer.calls and _script_row(sb, rid)["status"] == "generating"
    await svc.shutdown(timeout=2)
    row = _script_row(sb, rid)
    assert row["status"] == "selected" and row["lease_until"] is None


# ── create_posts: server-authored copy ───────────────────────────────────────


async def _accepted_run(sb, runs) -> str:
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    return rid


def _asset(sb, rid, kind="video", status="ready") -> str:
    aid = str(uuid.uuid4())
    sb.tables[mrs.ASSETS].rows.append({"id": aid, "run_id": rid, "status": status, "kind": kind,
                                       "storage_path": f"p-{aid}"})
    return aid


@pytest.mark.asyncio
async def test_create_posts_refuses_before_the_script_is_accepted(world):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    with pytest.raises(mrs.MarketingScriptNotReady):
        await runs.create_posts(rid, [{"platform": "x", "format": "text"}], claim=_holder_of(runs, rid))


@pytest.mark.asyncio
async def test_create_posts_uses_the_accepted_copy_not_the_workers(world):
    sb, runs = world
    rid = await _accepted_run(sb, runs)
    (post,) = await runs.create_posts(rid, [{
        "platform": "youtube", "format": "video", "title": "WORKER TITLE",
        "caption": "buy $AAPL now", "metadata": {"made_with_ai": False},
        "asset_ids": [_asset(sb, rid)],
    }], claim=_holder_of(runs, rid))
    assert post["caption"] == "server YT copy" and post["title"] == "Server title"
    assert post["metadata"]["source_ref"] and "made_with_ai" not in post["metadata"]
    assert post["metadata"]["dry_run"] is True


@pytest.mark.asyncio
async def test_create_posts_refuses_an_outlet_the_script_dropped(world):
    sb, runs = world
    rid = await _accepted_run(sb, runs)
    with pytest.raises(mrs.MarketingScriptNotReady):
        await runs.create_posts(rid, [{"platform": "threads", "format": "text"}], claim=_holder_of(runs, rid))


@pytest.mark.asyncio
async def test_create_posts_requires_ready_assets_of_the_same_run(world):
    sb, runs = world
    rid = await _accepted_run(sb, runs)
    other = _asset(sb, str(uuid.uuid4()))
    with pytest.raises(mrs.MarketingAssetMissingInStorage):
        await runs.create_posts(rid, [{"platform": "youtube", "format": "video", "asset_ids": [other]}], claim=_holder_of(runs, rid))
    mine = _asset(sb, rid, status="pending_upload")
    with pytest.raises(mrs.MarketingAssetMissingInStorage):
        await runs.create_posts(rid, [{"platform": "youtube", "format": "video", "asset_ids": [mine]}], claim=_holder_of(runs, rid))
    assert sb.tables[mrs.POSTS].rows == []


@pytest.mark.asyncio
async def test_media_posts_are_never_auto_approved(world, monkeypatch):
    sb, runs = world
    rid = await _accepted_run(sb, runs)
    _run_row(sb, rid)["dry_run"] = False
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", True)
    video, text = await runs.create_posts(rid, [
        {"platform": "youtube", "format": "video", "asset_ids": [_asset(sb, rid)]},
        {"platform": "x", "format": "text"},
    ], claim=_holder_of(runs, rid))
    assert video["status"] == "pending_review"
    assert text["status"] == "approved" and text["approved_by"] == "auto"


# ── the internal API surface ─────────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr("app.api.v1.endpoints.marketing_internal.settings.MARKETING_WORKER_TOKEN", "tok")
    return TestClient(app)


_H = {"X-Marketing-Worker-Token": "tok", "X-Marketing-Claim": f"1.{NONCE}"}
_BASE = "/api/v1/internal/marketing"


def test_claim_window_refuses_future_and_old_dates(client, monkeypatch):
    import app.api.v1.endpoints.marketing_internal as mi

    pinned = date(2026, 9, 17)
    monkeypatch.setattr(mi, "run_date_et", lambda now=None: pinned)
    for bad in (pinned + timedelta(days=1), pinned - timedelta(days=2)):
        r = client.post(f"{_BASE}/runs/claim", json={"run_date": bad.isoformat(), "worker_version": "t",
                                                     "claim_nonce": NONCE}, headers=_H)
        assert r.status_code == 422 and r.json()["error_code"] == "INVALID_INPUT"
    assert mi.claim_window_ok(pinned, pinned) and mi.claim_window_ok(pinned - timedelta(days=1), pinned)
    assert mi.claim_window_ok is mrs.claim_window_ok  # the kick's held check uses the same window


def test_kick_endpoint_answers_body_states(client, monkeypatch):
    import app.api.v1.endpoints.marketing_internal as mi

    class Svc:
        async def kick(self, run_id, *, claim):
            return {"status": "generating", "source_ref": "journey:x", "template_id": "checklist"}

    monkeypatch.setattr(mi, "get_marketing_script_service", lambda: Svc())
    r = client.post(f"{_BASE}/runs/r1/script", headers=_H)
    assert r.status_code == 200 and r.json()["status"] == "generating"


def test_kick_endpoint_carries_the_rejection_reason(client, monkeypatch):
    import app.api.v1.endpoints.marketing_internal as mi

    class Svc:
        async def kick(self, run_id, *, claim):
            return {"status": "rejected", "source_ref": "journey:x", "reason": "writer_unavailable",
                    "violations": []}

    monkeypatch.setattr(mi, "get_marketing_script_service", lambda: Svc())
    r = client.post(f"{_BASE}/runs/r1/script", headers=_H)
    assert r.status_code == 200 and r.json()["reason"] == "writer_unavailable"


@pytest.mark.parametrize("exc, status, code", [
    (mrs.MarketingRunNotFound("run r1 not found"), 404, "MARKETING_NOT_FOUND"),
    (mrs.MarketingRunNotHeld("run r1 is not held"), 409, "MARKETING_RUN_NOT_HELD"),
])
def test_kick_endpoint_maps_ledger_failures_to_marketing_codes(client, monkeypatch, exc, status, code):
    import app.api.v1.endpoints.marketing_internal as mi

    class Svc:
        async def kick(self, run_id, *, claim):
            raise exc

    monkeypatch.setattr(mi, "get_marketing_script_service", lambda: Svc())
    r = client.post(f"{_BASE}/runs/r1/script", headers=_H)
    assert r.status_code == status and r.json()["error_code"] == code


def test_patch_ignores_server_owned_selection_fields(client, monkeypatch):
    import app.api.v1.endpoints.marketing_internal as mi

    seen = {}

    class Svc:
        async def update_run(self, run_id, **kw):
            seen.update(kw)
            return {"id": run_id, "run_date": "2026-09-24", "status": "in_progress", "stage": "planned",
                    "content_class": "A"}

    monkeypatch.setattr(mi, "get_marketing_run_service", lambda: Svc())
    r = client.patch(f"{_BASE}/runs/r1", headers=_H, json={
        "source_ref": "money_moves:warren-buffetts-early-days", "template_id": "x", "content_class": "C",
        "stage": "selected"})
    assert r.status_code == 200
    assert seen["stage"] == "selected" and seen["worker"] is True
    assert not {"source_ref", "template_id", "content_class"} & set(seen)


@pytest.mark.parametrize("kind,ext", [("script", "json"), ("caption", "json"), ("blog", "json"),
                                      ("video", "html"), ("card", "md"), ("manifest", "txt"),
                                      ("video", "json"), ("manifest", "mp4"), ("card", "mp3")])
def test_worker_cannot_register_copy_text_or_mispaired_assets(client, kind, ext):
    r = client.post(f"{_BASE}/runs/r1/assets", headers=_H, json={
        "kind": kind, "ext": ext, "sha256": "a" * 64, "bytes": 10})
    assert r.status_code == 422


def test_script_not_ready_is_a_non_retryable_409():
    from app.api.error_response import ErrorCode, classify_exception

    code, status = classify_exception(mrs.MarketingScriptNotReady("no"))
    assert code == ErrorCode.MARKETING_SCRIPT_NOT_READY and status == 409


def test_every_marketing_exception_class_classifies_to_a_marketing_code():
    """classify_exception matches marketing classes by NAME SUBSTRING, not inheritance. A new
    class that misses its branch falls to REPORT_GENERATION_FAILED (502), which the worker
    RETRIES — three times per call, re-billing whatever the call did."""
    import inspect

    from app.api.error_response import classify_exception
    import importlib
    import pkgutil

    import app.services.marketing as pkg
    from app.services.marketing import script_service

    # EVERY module of the package (pkgutil), not a hand list: a new exception class in a new
    # module (judge.py's MarketingJudgeUnavailable, 2026-09-26) is walked the day it lands.
    mods = [importlib.import_module(f"{pkg.__name__}.{m.name}") for m in pkgutil.iter_modules(pkg.__path__)]
    classes = [
        obj for mod in mods for _, obj in inspect.getmembers(mod, inspect.isclass)
        if issubclass(obj, Exception) and obj.__module__ == mod.__name__
    ]
    assert any(c.__name__ == "MarketingJudgeUnavailable" for c in classes)
    assert len(classes) >= 7
    for cls in classes:
        if cls is script_service.LeaseLost:
            continue  # internal control flow, never leaves the generation task
        code, status = classify_exception(cls("x"))
        assert code.value.startswith("MARKETING_"), cls.__name__
        assert status < 500 or code.value == "MARKETING_LEDGER_ERROR", cls.__name__


# ── the mirror's held gate: a kick on an unheld run revives nothing ──────────


@pytest.mark.parametrize("label, extra", [
    ("stale claim", {"started_at": _iso_ago(hours=2), "updated_at": _iso_ago(hours=2)}),
    ("failed run", {"status": "failed"}),
    ("skipped run", {"status": "skipped"}),
])
@pytest.mark.asyncio
async def test_a_kick_on_an_unheld_run_neither_mirrors_nor_revives_it(world, label, extra):
    """`update_run` bumps `updated_at`, which IS the claim's liveness. A mirror written onto a
    stale claim revived it: `decide_claim` then held off the re-claim that would recover the day,
    and the SECOND kick passed the held check and started writer spend on a dead claim."""
    sb, runs = world
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY, **extra)
    _seed(sb, rid)  # the selection exists; the run carries no mirror of it
    before = dict(_run_row(sb, rid))
    for _ in range(2):
        with pytest.raises(mrs.MarketingRunNotHeld):
            await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    after = _run_row(sb, rid)
    assert after.get("source_ref") is None and after["updated_at"] == before["updated_at"], label
    assert writer.calls == [] and _script_row(sb, rid)["status"] == "selected", label
    if label == "stale claim":
        assert mrs.decide_claim(after, now=datetime.now(timezone.utc), stale_seconds=2700) == mrs.CLAIMED


# ── tokens_used accumulates across a day's generations ──────────────────────


def _timeout_spending(tokens: int):
    from app.integrations.gemini import GeminiTimeoutError

    err = GeminiTimeoutError("repair timed out")
    setattr(err, "marketing_tokens_used", tokens)
    return err


@pytest.mark.parametrize("second, total, status", [
    ("accepted", 99 + 1234, "accepted"),       # the ACCEPTED write
    ("rejected", 99 + 99, "selected"),         # the content-rejection write
    ("timeout", 99 + 777, "selected"),         # the failure write
])
@pytest.mark.asyncio
async def test_tokens_used_accumulates_across_generations(world, second, total, status):
    sb, runs = world
    outcome = _timeout_spending(777) if second == "timeout" else second
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["rejected", outcome]))
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert _script_row(sb, rid)["tokens_used"] == 99  # read the live row after each drain
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    row = _script_row(sb, rid)
    assert row["generations"] == 2 and row["status"] == status
    assert row["tokens_used"] == total


# ── `_landed` calls a write landed only if the row is exactly OUR write ──────


class _LoseFirstAttempt(mrs.MarketingRunService):
    """The first attempt of the patch `match` selects fails BEFORE commit; `meanwhile` writes
    what a concurrent writer landed between our attempts."""

    def __init__(self, sb, *, match, meanwhile):
        super().__init__(supabase=sb)
        self.match, self.meanwhile, self.fired = match, meanwhile, False

    async def update_script_where(self, run_id, patch, *, expect):
        if self.match(patch) and not self.fired:
            self.fired = True
            self.meanwhile(run_id, expect)
            raise mrs.MarketingRunError("update_script failed: APIError: 520 (before commit)")
        return await super().update_script_where(run_id, patch, expect=expect)


def _msgs(caplog):
    return [(r.levelno, r.getMessage()) for r in caplog.records]


@pytest.mark.asyncio
async def test_a_retry_that_finds_another_generations_accept_is_not_written(world, caplog):
    sb, runs = world

    def g2_accepts(run_id, _expect):
        _script_row(sb, run_id).update({"status": "accepted", "generation_id": "g2", "lease_until": None,
                                        "output": _package(KEY), "last_error": None, "tokens_used": 1234})

    ledger = _LoseFirstAttempt(sb, match=lambda p: p.get("status") == "accepted", meanwhile=g2_accepts)
    svc = ss.MarketingScriptService(ledger, writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    msgs = _msgs(caplog)
    assert _script_row(sb, rid)["generation_id"] == "g2"
    assert not any(m.startswith("marketing script ACCEPTED") for _, m in msgs)
    assert not any("landed" in m for _, m in msgs)
    assert any(lvl == logging.ERROR and "ACCEPTED package was NOT recorded" in m for lvl, m in msgs)


@pytest.mark.asyncio
async def test_a_retry_that_finds_another_generations_same_rejection_is_not_written(world, caplog):
    sb, runs = world

    def g2_rejects_alike(run_id, _expect):
        row = _script_row(sb, run_id)
        row.update({"status": "selected", "generation_id": "g2", "lease_until": None,
                    "content_rejections": 1, "tokens_used": 99, "last_error": "content rejected: person_named",
                    "violations": [{"field": "hook", "code": "person_named", "detail": "x"}]})

    ledger = _LoseFirstAttempt(sb, match=lambda p: "content_rejections" in p, meanwhile=g2_rejects_alike)
    svc = ss.MarketingScriptService(ledger, writer=FakeWriter(["rejected"]))
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    msgs = _msgs(caplog)
    assert not any(m.startswith("marketing script generation REJECTED") for _, m in msgs)
    assert not any("landed" in m for _, m in msgs)
    assert any(lvl == logging.ERROR and "content rejection was NOT recorded" in m for lvl, m in msgs)


@pytest.mark.asyncio
async def test_a_retry_that_finds_the_row_finalized_under_our_id_is_not_written(world, caplog):
    """A concurrent kick closed the day at the cap under OUR generation id (it judged the owner
    dead). Our generation id matches — the verdict does not: our package was not recorded."""
    sb, runs = world

    def finalized_under_our_id(run_id, expect):
        row = _script_row(sb, run_id)
        row.update({"status": "rejected", "reject_reason": "writer_unavailable", "lease_until": None,
                    "last_error": f"generation {expect['generation_id']} (#{row['generations']}) lost its "
                                  "lease without a terminal write"})

    ledger = _LoseFirstAttempt(sb, match=lambda p: p.get("status") == "accepted", meanwhile=finalized_under_our_id)
    svc = ss.MarketingScriptService(ledger, writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    msgs = _msgs(caplog)
    assert _script_row(sb, rid)["status"] == "rejected"
    assert not any(m.startswith("marketing script ACCEPTED") for _, m in msgs)
    assert any(lvl == logging.ERROR and "ACCEPTED package was NOT recorded" in m for lvl, m in msgs)


@pytest.mark.asyncio
async def test_landed_compares_every_scalar_the_write_sets(world):
    """Unit contract of `_landed`: our id AND each `_LANDED_FIELDS` value the patch carries."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    patch = {"status": "selected", "last_error": "content rejected: x", "reject_reason": None,
             "content_rejections": 2, "tokens_used": 198, "violations": [{"code": "x"}]}
    row = _seed(sb, rid, generation_id="ours", **patch)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    assert await svc._landed(rid, "ours", patch) is True
    assert set(ss.MarketingScriptService._LANDED_FIELDS) <= set(patch)
    for field, other in (("generation_id", "theirs"), ("status", "rejected"), ("last_error", "other"),
                         ("reject_reason", "content"), ("content_rejections", 3), ("tokens_used", 99)):
        saved = row[field]
        row[field] = other
        assert await svc._landed(rid, "ours", patch) is False, field
        row[field] = saved


# ── the lease refresh: retried, and judged against the lease on record ──────


@pytest.mark.asyncio
async def test_a_single_blip_on_the_lease_refresh_is_retried_and_extends_the_lease(world, caplog):
    sb, runs = world

    class BlipOnceBeforeRepair(mrs.MarketingRunService):
        attempts: List[str] = []
        applied: List[str] = []

        async def update_script_where(self, run_id, patch, *, expect):
            if set(patch) == {"lease_until"}:
                self.attempts.append(patch["lease_until"])
                if len(self.attempts) == 2:  # the repair round's FIRST attempt only
                    raise mrs.MarketingRunError("update_script failed (run_id=x): APIError: 520")
                out = await super().update_script_where(run_id, patch, expect=expect)
                if out is not None:
                    self.applied.append(out["lease_until"])
                return out
            return await super().update_script_where(run_id, patch, expect=expect)

    ledger = BlipOnceBeforeRepair(supabase=sb)
    writer = FakeWriter(["accepted"], rounds=2)
    svc = ss.MarketingScriptService(ledger, writer=writer)
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    assert writer.model_calls == 2 and _script_row(sb, rid)["status"] == "accepted"
    assert not any("lease NOT refreshed" in r.getMessage() for r in caplog.records)
    assert len(ledger.attempts) == 3 and len(ledger.applied) == 2  # round 1, then fail + success
    assert ledger.applied[1] == ledger.attempts[2] and writer.refreshes == [True, True]


@pytest.mark.asyncio
async def test_a_failed_refresh_asks_the_writer_to_skip_a_call_the_lease_cannot_cover(world, caplog):
    """The lease on record (the round-1 refresh's) has run down while the draft call ran; the
    repair-round refresh then fails. Starting a worst-case repair call could outlive the lease,
    so `before_call` answers False — and a writer that honours it keeps the draft."""
    sb, runs = world

    class DownForTheRepair(mrs.MarketingRunService):
        leases = 0

        async def update_script_where(self, run_id, patch, *, expect):
            if set(patch) == {"lease_until"}:
                DownForTheRepair.leases += 1
                if DownForTheRepair.leases >= 2:
                    raise mrs.MarketingRunError("update_script failed (run_id=x): APIError: 520")
            return await super().update_script_where(run_id, patch, expect=expect)

    writer = FakeWriter(["accepted"], rounds=2, honor_skip=True)
    svc = ss.MarketingScriptService(DownForTheRepair(supabase=sb), writer=writer)

    def the_draft_took_a_while(gen_id):  # ~532 s of the 632-s lease spent on round 1
        if writer.model_calls == 1:
            svc._leases[gen_id] = datetime.now(timezone.utc) + timedelta(seconds=100)

    writer.between_rounds = the_draft_took_a_while
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    assert writer.refreshes == [True, False] and writer.model_calls == 1
    assert _script_row(sb, rid)["status"] == "accepted"
    assert any("asking the writer to skip this call" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_the_acquires_lease_is_on_record_when_every_refresh_fails(world, caplog):
    """No refresh ever lands: the only lease this generation wrote is the ACQUIRE's, and the
    repair call is judged against it (it used to be judged against nothing and always ran)."""
    sb, runs = world

    class NoRefreshEverLands(mrs.MarketingRunService):
        async def update_script_where(self, run_id, patch, *, expect):
            if set(patch) == {"lease_until"}:
                raise mrs.MarketingRunError("update_script failed (run_id=x): APIError: 520")
            return await super().update_script_where(run_id, patch, expect=expect)

    writer = FakeWriter(["accepted"], rounds=2, honor_skip=True)
    svc = ss.MarketingScriptService(NoRefreshEverLands(supabase=sb), writer=writer)

    def the_draft_took_a_while(gen_id):
        if writer.model_calls == 1:
            svc._leases[gen_id] -= timedelta(seconds=ss.LEASE_SECONDS - 100)  # KeyError if unrecorded

    writer.between_rounds = the_draft_took_a_while
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert writer.refreshes == [True, False] and writer.model_calls == 1
    assert _script_row(sb, rid)["status"] == "accepted"


@pytest.mark.asyncio
async def test_a_failed_refresh_continues_while_the_lease_on_record_covers_a_call(world, monkeypatch):
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, status="generating", generation_id="g", generations=1, lease_until=_iso_ahead(minutes=10))

    class Down(mrs.MarketingRunService):
        async def update_script_where(self, run_id, patch, *, expect):
            raise mrs.MarketingRunError("update_script failed: APIError: 520")

    svc = ss.MarketingScriptService(Down(supabase=sb), writer=FakeWriter(["accepted"]))
    now = datetime.now(timezone.utc)
    svc._leases["g"] = now + timedelta(seconds=ss.LEASE_SECONDS - 5)
    assert await svc._refresh_lease(rid, "g") is True
    svc._leases["g"] = now + timedelta(seconds=ss.worst_case_model_call_seconds() - 30)
    assert await svc._refresh_lease(rid, "g") is False


@pytest.mark.asyncio
async def test_a_refresh_that_lands_late_is_retried_for_a_full_lease(world, monkeypatch):
    """The lease is computed BEFORE the round trip: one that took longer than the margin lands
    with less than a model call left, and is refreshed again rather than trusted."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, status="generating", generation_id="g", generations=1, lease_until=_iso_ahead(minutes=10))
    t0 = datetime.now(timezone.utc)
    clock = {"now": t0}
    monkeypatch.setattr(ss, "_now", lambda: clock["now"])
    written: List[str] = []

    class SlowOnce(mrs.MarketingRunService):
        async def update_script_where(self, run_id, patch, *, expect):
            written.append(patch["lease_until"])
            if len(written) == 1:
                clock["now"] = t0 + timedelta(seconds=ss.LEASE_MARGIN_SECONDS + 40)  # a 100-s round trip
            return await super().update_script_where(run_id, patch, expect=expect)

    svc = ss.MarketingScriptService(SlowOnce(supabase=sb), writer=FakeWriter(["accepted"]))
    assert await svc._refresh_lease(rid, "g") is True
    assert len(written) == 2
    assert (svc._leases["g"] - clock["now"]).total_seconds() >= ss.worst_case_model_call_seconds()


# ── a live owner in this process is never closed out from under ──────────────


async def _generation_in_flight(sb, svc, writer, rid):
    await svc.kick(rid, claim=_holder(svc, rid))
    for _ in range(300):
        if writer.calls:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the generation never reached the writer")


@pytest.mark.asyncio
async def test_a_lapsed_lease_under_a_live_owner_at_the_cap_is_left_alone(world):
    """Three failures already; generation 4 is at the failure cap for its whole life. Its lease
    lapsed mid-call (a refresh that errored, then slow calls). The worker's next poll reaches the
    SAME process: it used to close the day `writer_unavailable` and fence out the paid package."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, generations=ss.MAX_WRITER_FAILURES - 1)
    writer = FakeWriter(["accepted"])
    writer.gate = asyncio.Event()
    svc = ss.MarketingScriptService(runs, writer=writer)
    await _generation_in_flight(sb, svc, writer, rid)
    row = _script_row(sb, rid)
    assert row["status"] == "generating" and ss._cap_verdict(row) == ss.REASON_WRITER_UNAVAILABLE
    row["lease_until"] = ss._iso(datetime.now(timezone.utc) - timedelta(seconds=5))  # lapsed
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["status"] == "generating"
    assert _script_row(sb, rid)["status"] == "generating"
    writer.gate.set()
    await _drain(svc)
    assert _script_row(sb, rid)["status"] == "accepted"


@pytest.mark.asyncio
async def test_a_wedged_task_does_not_wedge_the_day(world, caplog):
    """The in-process owner counts only while it is young enough to be alive
    (OWNER_ALIVE_SECONDS); past that the lease logic decides again and the cap closes the day."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, generations=ss.MAX_WRITER_FAILURES - 1)
    writer = FakeWriter(["accepted"])
    writer.gate = asyncio.Event()
    svc = ss.MarketingScriptService(runs, writer=writer)
    await _generation_in_flight(sb, svc, writer, rid)
    _script_row(sb, rid)["lease_until"] = ss._iso(datetime.now(timezone.utc) - timedelta(seconds=5))
    svc._spawned_at[rid] = datetime.now(timezone.utc) - timedelta(seconds=ss.OWNER_ALIVE_SECONDS + 1)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "writer_unavailable"
    assert any("treating it as wedged" in r.getMessage() for r in caplog.records)
    writer.gate.set()
    await _drain(svc)
    assert _script_row(sb, rid)["status"] == "rejected"  # the late owner is fenced out


# ── a cancel during the acquire never orphans a lease ────────────────────────


def _hold_the_acquire(sb, seconds: float) -> threading.Event:
    """The acquire UPDATE (status → generating) is held IN FLIGHT in its thread for `seconds`."""
    entered = threading.Event()

    def hold(payload):
        if payload.get("status") == "generating":
            entered.set()
            time.sleep(seconds)

    sb.tables[mrs.SCRIPTS].before_update = hold
    return entered


async def _until(flag: threading.Event):
    for _ in range(300):
        if flag.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


@pytest.mark.asyncio
async def test_a_cancel_during_an_in_flight_acquire_hands_back_after_it_lands(world):
    """sb_exec runs the statement in a thread; cancelling the coroutine does not stop it. The
    hand-back used to be sent at once, overtake the still-in-flight acquire, match nothing — and
    the acquire then committed a 632-s lease with nobody behind it."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid)
    entered = _hold_the_acquire(sb, 0.3)
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _until(entered)
    await svc.shutdown(timeout=2)
    await asyncio.sleep(0.5)  # any statement still in flight has landed by now
    row = _script_row(sb, rid)
    assert writer.calls == []
    assert row["status"] == "selected" and row["lease_until"] is None, row
    assert row["generations"] == 1  # the slot stays spent: the cap bounds crash-looping containers


@pytest.mark.asyncio
async def test_an_acquire_still_in_flight_after_the_budget_is_not_raced(world, monkeypatch, caplog):
    """If the statement does not answer inside the hand-back budget, no hand-back is sent at all
    (it could overtake it); the lease is the backstop, and a late landing is logged."""
    sb, runs = world
    monkeypatch.setattr(ss, "HAND_BACK_TIMEOUT_SECONDS", 0.1)
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid)
    entered = _hold_the_acquire(sb, 0.4)
    sent: List[Dict[str, Any]] = []

    class Recording(mrs.MarketingRunService):
        async def update_script_where(self, run_id, patch, *, expect):
            sent.append(dict(patch))
            return await super().update_script_where(run_id, patch, expect=expect)

    svc = ss.MarketingScriptService(Recording(supabase=sb), writer=FakeWriter(["accepted"]))
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _until(entered)
        await svc.shutdown(timeout=2)
        await asyncio.sleep(0.6)
    assert [p.get("status") for p in sent] == ["generating"]  # no hand-back raced it
    msgs = [r.getMessage() for r in caplog.records]
    assert any("still in flight" in m and "backstop" in m for m in msgs)
    assert any("landed after the cancel" in m for m in msgs)
    assert _script_row(sb, rid)["status"] == "generating"  # the orphan the lease will expire


@pytest.mark.asyncio
async def test_a_cancel_before_the_acquire_write_takes_nothing(world):
    """A cancel while the acquire is still READING must not let the write go out afterwards."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    await svc.kick(rid, claim=_holder(svc, rid))
    entered = threading.Event()

    def slow_read(_payload):
        entered.set()
        time.sleep(0.2)

    sb.tables[mrs.SCRIPTS].before_select = slow_read
    await _until(entered)
    await svc.shutdown(timeout=2)
    sb.tables[mrs.SCRIPTS].before_select = None
    await asyncio.sleep(0.4)
    row = _script_row(sb, rid)
    assert row["status"] == "selected" and row["generations"] == 0 and row.get("generation_id") is None


# ── round 3: a shutdown never races a terminal write; budgets and bookkeeping are pinned ──


class _Recording(mrs.MarketingRunService):
    """Records every conditional UPDATE the service SENDS (before it reaches the table)."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.sent: List[Dict[str, Any]] = []

    async def update_script_where(self, run_id, patch, *, expect):
        self.sent.append(dict(patch))
        return await super().update_script_where(run_id, patch, expect=expect)


_SHUTDOWN_WHY = "generation cancelled (shutdown)"


def _hold_first(sb, pred, seconds: float, *, then_raise: Optional[BaseException] = None,
                raise_next: int = 0) -> threading.Event:
    """The FIRST update matching `pred` is held IN FLIGHT in its thread for `seconds` (then,
    with `then_raise`, fails before it commits); the next `raise_next` matching updates fail at
    once. A thread-level hold: an asyncio-level one is cancelled with the coroutine and proves
    nothing."""
    entered = threading.Event()
    state = {"n": 0}

    def hook(payload):
        if not pred(payload):
            return
        state["n"] += 1
        if state["n"] == 1:
            entered.set()
            time.sleep(seconds)
            if then_raise is not None:
                raise then_raise
        elif state["n"] <= 1 + raise_next:
            raise mrs.MarketingRunError("update_script failed: APIError: 520")

    sb.tables[mrs.SCRIPTS].before_update = hook
    return entered


def _is_accepted(p):
    return p.get("status") == "accepted"


_TERMINAL_CASES = {
    "accepted": ("accepted", _is_accepted),
    "content": ("rejected", lambda p: "content_rejections" in p),
    "failure": (RuntimeError("model down"),
                lambda p: p.get("status") == "selected"
                and str(p.get("last_error") or "").startswith("RuntimeError")),
}


@pytest.mark.parametrize("case", sorted(_TERMINAL_CASES))
@pytest.mark.asyncio
async def test_a_cancel_during_an_in_flight_terminal_write_waits_for_it(world, caplog, case):
    """W3-SWW-1: the acquire fix covered only the acquire. A shutdown cancel that landed while
    the ACCEPTED (or content-rejection, or failure) UPDATE ran in its thread sent the hand-back
    at once; fenced on the same generation id, it could reach Postgres first and commit
    `selected`, the terminal write then matched nothing — a paid, compliant package thrown away
    with only an INFO "handed back" line. The terminal statement is now waited for first."""
    sb, runs = world
    outcome, pred = _TERMINAL_CASES[case]
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid)
    entered = _hold_first(sb, pred, 0.3)
    ledger = _Recording(supabase=sb)
    svc = ss.MarketingScriptService(ledger, writer=FakeWriter([outcome]))
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _until(entered)
        await svc.shutdown(timeout=2)
    await asyncio.sleep(0.5)  # anything still in flight has landed by now
    row = _script_row(sb, rid)
    assert not [p for p in ledger.sent if p.get("last_error") == _SHUTDOWN_WHY], ledger.sent
    assert row["lease_until"] is None and row["generations"] == 1
    if case == "accepted":
        assert row["status"] == "accepted" and isinstance(row.get("output"), dict), row
    elif case == "content":
        assert row["status"] == "selected" and row["content_rejections"] == 1, row
        assert row["last_error"].startswith("content rejected"), row
    else:
        assert row["status"] == "selected" and row["retry_not_before"], row
        assert row["last_error"].startswith("RuntimeError"), row
    assert any("LANDED during the shutdown" in r.getMessage() for r in caplog.records)
    assert svc._terminal == {} and svc._leases == {}


@pytest.mark.asyncio
async def test_a_terminal_write_still_in_flight_after_the_budget_is_not_raced(
        world, monkeypatch, caplog):
    """Past HAND_BACK_TIMEOUT_SECONDS nothing is sent (a hand-back could still overtake it); the
    lease is the backstop and the late landing is logged."""
    sb, runs = world
    monkeypatch.setattr(ss, "HAND_BACK_TIMEOUT_SECONDS", 0.1)
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid)
    entered = _hold_first(sb, _is_accepted, 0.4)
    ledger = _Recording(supabase=sb)
    svc = ss.MarketingScriptService(ledger, writer=FakeWriter(["accepted"]))
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _until(entered)
        await svc.shutdown(timeout=2)
        await asyncio.sleep(0.6)
    assert [p.get("status") for p in ledger.sent] == ["generating", None, "accepted"], ledger.sent
    msgs = [r.getMessage() for r in caplog.records]
    assert any("still in flight after" in m and "backstop" in m for m in msgs), msgs
    assert any("landed after the shutdown wait" in m for m in msgs), msgs
    assert not any("re-send" in m for m in msgs), "an unanswered statement is never re-sent either"
    assert _script_row(sb, rid)["status"] == "accepted"


@pytest.mark.asyncio
async def test_a_terminal_write_that_answered_with_an_error_is_re_sent_not_handed_back(world):
    """The in-flight ACCEPTED statement fails (before committing) while the cancel waits on it:
    the same fenced patch is re-sent inside the budget — the package is kept, where a hand-back
    would discard it and re-bill a generation."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid)
    entered = _hold_first(sb, _is_accepted, 0.3,
                          then_raise=mrs.MarketingRunError("update_script failed: APIError: 520"))
    ledger = _Recording(supabase=sb)
    svc = ss.MarketingScriptService(ledger, writer=FakeWriter(["accepted"]))
    await svc.kick(rid, claim=_holder(svc, rid))
    await _until(entered)
    await svc.shutdown(timeout=2)
    row = _script_row(sb, rid)
    assert row["status"] == "accepted" and isinstance(row.get("output"), dict), row
    assert [p.get("status") for p in ledger.sent] == ["generating", None, "accepted", "accepted"]


@pytest.mark.asyncio
async def test_a_cancel_between_terminal_attempts_re_sends_the_package(world, monkeypatch):
    """No statement in flight, but a package in hand: the cancel caught `_finish` in its
    back-off after a failed attempt. It is re-sent once, not handed back."""
    sb, runs = world
    monkeypatch.setattr(ss, "_FINISH_BACKOFF_SECONDS", 5.0)
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid)
    _hold_first(sb, _is_accepted, 0.0,
                then_raise=mrs.MarketingRunError("update_script failed: APIError: 520"))
    ledger = _Recording(supabase=sb)
    svc = ss.MarketingScriptService(ledger, writer=FakeWriter(["accepted"]))
    await svc.kick(rid, claim=_holder(svc, rid))
    for _ in range(300):  # until the first accepted attempt answered and `_finish` is sleeping
        if [p for p in ledger.sent if p.get("status") == "accepted"] and svc._terminal \
                and next(iter(svc._terminal.values())).fut.done():
            break
        await asyncio.sleep(0.01)
    else:
        raise AssertionError("the first terminal attempt never answered")
    started = time.monotonic()
    await svc.shutdown(timeout=2)
    assert time.monotonic() - started < 1.5, "the back-off sleep is not waited out"
    assert _script_row(sb, rid)["status"] == "accepted"
    assert not [p for p in ledger.sent if p.get("last_error") == _SHUTDOWN_WHY]


@pytest.mark.asyncio
async def test_when_the_re_send_fails_too_the_hand_back_gets_only_what_is_left_of_the_budget(
        world, monkeypatch, caplog):
    """W3-SWW-2 for the terminal path: the wait, the re-send and the fallback hand-back share
    ONE HAND_BACK_TIMEOUT_SECONDS (the lifespan gives shutdown 5 s in all)."""
    sb, runs = world
    budget, hold = 0.8, 0.5
    monkeypatch.setattr(ss, "HAND_BACK_TIMEOUT_SECONDS", budget)
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid)
    entered = _hold_first(sb, _is_accepted, hold, raise_next=1,
                          then_raise=mrs.MarketingRunError("update_script failed: APIError: 520"))
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    timeouts: List[Optional[float]] = []
    real_hand_back = svc._hand_back

    async def recording_hand_back(run_id, gen_id, why, *, timeout=None):
        timeouts.append(timeout)
        return await real_hand_back(run_id, gen_id, why, timeout=timeout)

    svc._hand_back = recording_hand_back  # instance attribute: restored with the instance
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _until(entered)
        await svc.shutdown(timeout=2)
    assert len(timeouts) == 1 and timeouts[0] is not None, timeouts
    # ≈ budget - hold; the slack absorbs a slow runner's delay between the hold and the cancel,
    # and a fresh full budget (the regression) still fails it.
    assert 0 < timeouts[0] <= budget - 0.2, timeouts
    row = _script_row(sb, rid)
    assert row["status"] == "selected" and row["last_error"] == _SHUTDOWN_WHY, row
    # A paid package given up on is never quiet: ERROR, like the normal path's "NOT recorded".
    assert any(r.levelno == logging.ERROR and "handing the run back instead" in r.getMessage()
               and "ACCEPTED package" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_the_acquire_settle_hands_back_inside_the_shared_budget(world, monkeypatch):
    """W3-SWW-2 (a): `_settle_cancelled_acquire` waits for the in-flight acquire and then hands
    back with what is LEFT of HAND_BACK_TIMEOUT_SECONDS — never a fresh full budget, which would
    let one shutdown take ~2 × 3 s, past the lifespan's 5 s. The hold stays below the budget, or
    the test would go down the no-hand-back branch and prove nothing."""
    sb, runs = world
    budget, hold = 0.6, 0.45
    monkeypatch.setattr(ss, "HAND_BACK_TIMEOUT_SECONDS", budget)
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid)
    entered = _hold_the_acquire(sb, hold)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    timeouts: List[Optional[float]] = []
    real_hand_back = svc._hand_back

    async def recording_hand_back(run_id, gen_id, why, *, timeout=None):
        timeouts.append(timeout)
        return await real_hand_back(run_id, gen_id, why, timeout=timeout)

    svc._hand_back = recording_hand_back
    await svc.kick(rid, claim=_holder(svc, rid))
    await _until(entered)
    await svc.shutdown(timeout=2)
    assert len(timeouts) == 1 and timeouts[0] is not None, timeouts
    # ≈ budget - hold; the slack absorbs a slow runner's delay between the hold and the cancel,
    # and a fresh full budget (the regression) still fails it.
    assert 0 < timeouts[0] <= budget - 0.15, timeouts
    assert _script_row(sb, rid)["status"] == "selected"


@pytest.mark.asyncio
async def test_per_generation_bookkeeping_is_dropped_when_the_task_ends(world):
    """W3-SWW-2 (c): `_leases` and `_terminal` are keyed by generation id and must not outlive
    the task. Sentinels prove both were populated while it ran."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    seen: Dict[str, Any] = {}

    class Peek(mrs.MarketingRunService):
        async def update_script_where(self, run_id, patch, *, expect):
            if patch.get("status") == "accepted":
                seen["terminal"] = dict(svc._terminal)
            return await super().update_script_where(run_id, patch, expect=expect)

    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(Peek(supabase=sb), writer=writer)
    writer.between_rounds = lambda gen_id: seen.setdefault("leases", (gen_id, dict(svc._leases)))
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert _script_row(sb, rid)["status"] == "accepted"
    gen_id, leases = seen["leases"]
    assert gen_id in leases and gen_id in seen["terminal"], seen
    assert svc._leases == {} and svc._terminal == {}
    # `_finish` itself forgets its record on a normal return (the task's own cleanup is only
    # the backstop for a cancel): called directly, nothing is left behind.
    rid2 = _run(sb, POSTING_DAY + timedelta(days=7))
    _seed(sb, rid2, status="generating", generation_id="g2", generations=1,
          lease_until=_iso_ahead(minutes=10))
    assert await svc._finish(rid2, "g2", {"status": "selected", "last_error": "x"}) == ss.WRITTEN
    assert svc._terminal == {}


@pytest.mark.asyncio
async def test_a_wedged_task_below_the_caps_is_reported_once_and_left_to_finish(world, caplog):
    """W3-SWW-4: below the caps an over-age task was logged "treating it as wedged; the lease
    decides" on EVERY poll while nothing was decided — `_spawn` is a no-op while the task is in
    `_running`. Now one WARNING per task says what actually happens (nothing taken over), and
    the at-cap wording is kept for the one path that acts."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, generations=1)
    writer = FakeWriter(["accepted"])
    writer.gate = asyncio.Event()
    svc = ss.MarketingScriptService(runs, writer=writer)
    await _generation_in_flight(sb, svc, writer, rid)
    row = _script_row(sb, rid)
    assert ss._cap_verdict(row) is None, "sentinel: below both caps"
    row["lease_until"] = ss._iso(datetime.now(timezone.utc) - timedelta(seconds=5))
    svc._spawned_at[rid] = datetime.now(timezone.utc) - timedelta(seconds=ss.OWNER_ALIVE_SECONDS + 1)
    tasks = set(svc._tasks)
    try:
        with caplog.at_level(logging.INFO, logger=ss.logger.name):
            states = [(await svc.kick(rid, claim=_holder(svc, rid)))["status"] for _ in range(4)]
    finally:
        writer.gate.set()
    assert states == ["generating"] * 4
    assert svc._tasks == tasks and len(writer.calls) == 1, "nothing taken over, nothing spawned"
    wedged = [r for r in caplog.records if "OWNER_ALIVE_SECONDS" in r.getMessage()]
    assert len(wedged) == 1 and wedged[0].levelno == logging.WARNING, [r.getMessage() for r in wedged]
    assert "below the caps" in wedged[0].getMessage()
    assert not any("treating it as wedged" in r.getMessage() for r in caplog.records)
    await _drain(svc)
    assert _script_row(sb, rid)["status"] == "accepted"
    assert svc._wedge_reported == set(), "cleared with the task"


@pytest.mark.asyncio
async def test_a_young_owner_with_a_lapsed_lease_logs_no_wedge(world, caplog):
    """The twin: a live owner inside OWNER_ALIVE_SECONDS gets the INFO line, no wedge WARNING."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, generations=1)
    writer = FakeWriter(["accepted"])
    writer.gate = asyncio.Event()
    svc = ss.MarketingScriptService(runs, writer=writer)
    await _generation_in_flight(sb, svc, writer, rid)
    _script_row(sb, rid)["lease_until"] = ss._iso(datetime.now(timezone.utc) - timedelta(seconds=5))
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        for _ in range(3):
            assert (await svc.kick(rid, claim=_holder(svc, rid)))["status"] == "generating"
    msgs = [r.getMessage() for r in caplog.records]
    assert sum("owner is alive in this process" in m for m in msgs) == 3
    assert not any("OWNER_ALIVE_SECONDS" in m for m in msgs), msgs
    writer.gate.set()
    await _drain(svc)



@pytest.mark.asyncio
async def test_the_service_passes_the_configured_judge_mode_to_the_writer(world, monkeypatch):
    """The writer has no default for `judge_mode` (a default is how a fail-open "off" slips in);
    the service always states it, from MARKETING_JUDGE_MODE."""
    from app.config import settings

    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    monkeypatch.setattr(settings, "MARKETING_JUDGE_MODE", "shadow")
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert [c["judge_mode"] for c in writer.calls] == ["shadow"]
