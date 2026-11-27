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
import dataclasses
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


#: Columns the fake reads as their NOT NULL DEFAULT when a row lacks them (a row a test appends straight to `.rows`,
#: bypassing `insert`'s defaults) — `lane` (migration 210: `TEXT NOT NULL DEFAULT 'main'` on marketing_runs and
#: marketing_scripts), so every row written before the lanes, and every main-lane INSERT, is the main lane's.
#: Migration 215: `replacement` (NOT NULL DEFAULT 0 on both tables) reads as 0 the same way — a day's own run.
_READ_DEFAULTS: Dict[str, Any] = {"lane": "main", "replacement": 0}


def _cell(row: Dict[str, Any], col: str) -> Any:
    """`row[col]`, or the column's default (`_READ_DEFAULTS`) when the row lacks it — as the real column reads."""
    if col in _READ_DEFAULTS and col not in row:
        return _READ_DEFAULTS[col]
    return row.get(col)


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
        self.filters.append(lambda r, c=c, v=v: _same(_cell(r, c), v))
        return self

    def is_(self, c, v):
        assert v == "null"
        neg, self._negate = self._negate, False
        self.filters.append(lambda r, c=c: (r.get(c) is not None) if neg else (r.get(c) is None))
        return self

    def lt(self, c, v):
        self._check_plain()
        self.filters.append(lambda r, c=c, v=v: _cell(r, c) is not None and str(_cell(r, c)) < str(v))
        return self

    # `gte` / `lte` (the spotlight lane's cross-lane read, `lane_scripts_between`): NULL never matches, like `lt`;
    # only ISO dates and single-digit replacement numbers (migration 215) are compared here, whose string order is
    # their order. A `_READ_DEFAULTS` column a row lacks compares as its default.
    def gte(self, c, v):
        self._check_plain()
        self.filters.append(lambda r, c=c, v=v: _cell(r, c) is not None and str(_cell(r, c)) >= str(v))
        return self

    def lte(self, c, v):
        self._check_plain()
        self.filters.append(lambda r, c=c, v=v: _cell(r, c) is not None and str(_cell(r, c)) <= str(v))
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
            row.setdefault("id", str(uuid.uuid4()))
            # Defaults BEFORE the unique check, as in Postgres; a column the row lacks is compared as its read
            # default (`_cell`): a main-lane INSERT never sends `lane`, and its (run_date, 'main') must still meet
            # an existing main row (migration 210).
            for k, v in self.t.defaults.items():
                row.setdefault(k, copy.deepcopy(v))
            for key in self.t.unique:
                if any(all(_cell(r, k) == _cell(row, k) for k in key) for r in rows):
                    raise APIError({"code": "23505", "message": "duplicate key value violates unique "
                                    "constraint", "details": None, "hint": None})
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
            # migration 210: one run per (day, lane). `lane` (NOT NULL DEFAULT 'main') is NOT stored by the fake
            # when an INSERT omits it — it reads as 'main' (`_cell`) — so a test can see what the INSERT sent.
            # Migration 215: the key is (day, lane, replacement); `replacement` reads as 0 when absent, like `lane`.
            mrs.RUNS: _T([("run_date", "lane", "replacement")], {"status": "planned", "stage": "planned", "metadata": {},
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


#: What a FakeWriter records when the service does NOT pass `store_state` — never a real state, so a
#: service that stopped stating it cannot pass for one that passed the default ("prelaunch").
STORE_STATE_NOT_PASSED = "<not passed>"
#: The same for `image_posts` (drop-1 review, server:F4): never a bool, so a service that stopped
#: stating the switch cannot pass for one that passed the writer's default (True).
IMAGE_POSTS_NOT_PASSED = "<not passed>"


class FakeWriter:
    """Stands in for writer_service.generate_package; records calls, answers from a script.
    `calls` is recorded on entry; `model_calls` only once `before_call` let the model call
    through — the difference is what proves a lost lease stopped the SPEND, not just the write.
    `store_state` (the captions' value line, 2026-10-05) defaults to a sentinel the real writer
    never sees, so the state tests below prove the service STATES it on every call."""

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
                       before_call=None, store_state=STORE_STATE_NOT_PASSED,
                       image_posts=IMAGE_POSTS_NOT_PASSED):
        self.calls.append({"item": item.key, "template": template.id, "generation_id": generation_id,
                           "run_date": run_date, "judge_mode": judge_mode,
                           "store_state": store_state, "image_posts": image_posts})
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
            package = _package(item.key)
            if judge_mode != "off":   # the real writer records the mode IN the package
                package["judge"] = {"mode": judge_mode, "verdicts": []}
            return WriterResult("accepted", package, [], [], 1234)
        return WriterResult("rejected", None, [{"field": "hook", "code": "person_named", "detail": "x"}], [], 99)


@pytest.fixture
def world(monkeypatch):
    sb = FakeSB()
    runs = mrs.MarketingRunService(supabase=sb)
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", False)
    monkeypatch.setattr(mrs.settings, "MARKETING_RUN_STALE_SECONDS", 2700)
    # Image posts (drop 1) off, whatever this machine's environment says: the tests that need them
    # turn them on themselves.
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_POSTS", False)
    monkeypatch.setattr(mrs.settings, "MARKETING_X_IMAGES", False)
    # Batch 1: the picture platforms at their declared default (every image platform), and no first
    # comment — again whatever this machine's environment says.
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_PLATFORMS", "bluesky,facebook,linkedin,threads")
    monkeypatch.setattr(mrs.settings, "MARKETING_FIRST_COMMENT_PLATFORMS", "")
    # Batch 2: no video feature (the declared default), whatever this machine's environment says.
    monkeypatch.setattr(mrs.settings, "MARKETING_VIDEO_FEATURES", "")
    # Batch 3: the three new switches at their declared defaults (no extra video post, the four posting days, no
    # trial Reels), whatever this machine's environment says.
    monkeypatch.setattr(mrs.settings, "MARKETING_VIDEO_EXTRA_PLATFORMS", "")
    monkeypatch.setattr(mrs.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat")
    monkeypatch.setattr(mrs.settings, "MARKETING_INSTAGRAM_TRIAL_MODE", "off")
    # The content classes and the news series at their declared defaults (lessons only), whatever this machine's
    # environment says (review of 2026-10-20, HYG-1): with C/F enabled there — the planned production value — a
    # lesson-day test whose holder declares `news_templates` built a template day through the REAL company-news
    # adapter (FMP and Supabase, blocked by backend/conftest.py) and reached its lesson only by that fallback. The
    # tests that need a news day turn them on themselves.
    from app.config import Settings
    for name in ("MARKETING_CONTENT_CLASSES", "MARKETING_NEWS_SERIES"):
        monkeypatch.setattr(mrs.settings, name, Settings.model_fields[name].default)
    # The spotlight lane (migration 210) at its declared defaults — closed — likewise: `news_recent_limit()` and the
    # spotlight selection read it. `spot_world` opens it.
    for name in ("MARKETING_SPOTLIGHT_ENABLED", "MARKETING_SPOTLIGHT_WEEKDAYS", "MARKETING_SPOTLIGHT_HOUR_ET"):
        monkeypatch.setattr(mrs.settings, name, Settings.model_fields[name].default)
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
    """A run the worker HOLDS: in_progress and claimed just now (what claim_run writes for a drop-1
    worker, which declares it can render the post image)."""
    rid = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    sb.tables[mrs.RUNS].rows.append({"id": rid, "run_date": day.isoformat(), "status": "in_progress",
                                     "stage": "planned",
                                     "metadata": {"claim_nonce": NONCE, "worker_capabilities": ["post_image"]},
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

        async def insert_script(self, row, **kw):
            out = await super().insert_script(row, **kw)
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
    assert set(script) == {"hook", "video_script", "cards", "carousel_slides", "disclaimer_card", "outlets",
                           "post_formats", "image_post", "image_footer",
                           # drop 2 (contract D11): a lesson's template fields are their lesson defaults
                           "content_class", "authorship", "series", "video_layout", "opening_card",
                           "image_spec", "logos",
                           # Batch 2: always present — their presence is the worker's "this web knows" signal
                           "video_features", "end_card_cta",
                           # Batch 2 fix (CARDS-B2-1): which card a data card replaces — None without one
                           "data_card_slot",
                           # per-post art (workstream C): None on a lesson, always (and without art anywhere)
                           "art",
                           # news-v23: on every read-back — the look's own strings are allowed by this web
                           "look"}
    assert script["look"] == "news-v23"
    assert (script["content_class"], script["authorship"], script["series"], script["video_layout"],
            script["opening_card"], script["image_spec"], script["logos"]) == ("A", "ai", None, None, None, None, [])
    assert script["art"] is None
    # No feature switched on; the package carries no store state, so the end card keeps the legacy CTA.
    assert script["video_features"] == [] and script["end_card_cta"] == "caydexinvest.com"
    assert script["data_card_slot"] is None
    assert script["outlets"] == ["x", "youtube"]
    # Image posts off: the formats are frozen all the same (text / video), with no image to draw.
    assert script["post_formats"] == {"x": "text", "youtube": "video"}
    assert script["image_post"] is None and script["image_footer"] is None
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
                           before_call=None, store_state=STORE_STATE_NOT_PASSED,
                           image_posts=IMAGE_POSTS_NOT_PASSED):
            _script_row(sb, rid)["generation_id"] = "someone-else"  # a takeover happened
            return await super().__call__(item, template, run_date, generation_id=generation_id,
                                          allow_x_url=allow_x_url, judge_mode=judge_mode,
                                          before_call=before_call, store_state=store_state,
                                          image_posts=image_posts)

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

    # mirrored already (drop 2: the mirror is source_ref + template_id + class): no update_run read
    rid = _run(sb, POSTING_DAY, source_ref=KEY, template_id="three_takeaways", content_class="A")
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


def _run_video(sb, rid) -> str:
    """A ready video of `rid` named by the run's `metadata.video_asset_id` — what the worker's `rendered`
    PATCH leaves, and the one video every video post of a run with frozen formats must carry (F1)."""
    aid = _asset(sb, rid)
    _run_row(sb, rid)["metadata"]["video_asset_id"] = aid
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
        "asset_ids": [_run_video(sb, rid)],
    }], claim=_holder_of(runs, rid))
    assert post["caption"] == "server YT copy" and post["title"] == "Server title"
    # the worker's metadata is ignored: its made_with_ai=False never lands — the server's own flag does
    # (drop 2: an AI-written lesson video is made with AI)
    assert post["metadata"]["source_ref"] and post["metadata"]["made_with_ai"] is True
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
        {"platform": "youtube", "format": "video", "asset_ids": [_run_video(sb, rid)]},
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
    # Drop 2: a class whose constructor validates its first argument (logo_check.LogoRejected takes a
    # LOGO_REJECT_REASONS value) is built with a valid one — never skipped, so the walk stays complete.
    from app.services.marketing import logo_check

    ctor_args = {logo_check.LogoRejected: (sorted(logo_check.LOGO_REJECT_REASONS)[0],)}
    assert {"MarketingTemplateRefused", "MarketingNewsUnavailable", "NewsTemplateRefused",
            "LogoRejected"} <= {c.__name__ for c in classes}
    for cls in classes:
        if cls is script_service.LeaseLost:
            continue  # internal control flow, never leaves the generation task
        code, status = classify_exception(cls(*ctor_args.get(cls, ("x",))))
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


# ── the Claude part of tokens_used (migration 206, 2026-10-31) ───────────────

_U1 = {"claude-sonnet-5-5": {"input": 900, "output": 300, "cache_read": 0, "cache_write": 0}}
_U2 = {"claude-sonnet-5-5": {"input": 100, "output": 50, "cache_read": 0, "cache_write": 0},
       "claude-opus-5-5": {"input": 450, "output": 50, "cache_read": 0, "cache_write": 0}}
_U1_U2 = {"claude-sonnet-5-5": {"input": 1_000, "output": 350, "cache_read": 0, "cache_write": 0},
          "claude-opus-5-5": {"input": 450, "output": 50, "cache_read": 0, "cache_write": 0}}


class _BillingWriter(FakeWriter):
    """FakeWriter whose n-th generation also billed `usages[n]` of Claude tokens — on its WriterResult, or
    on the raised exception as the writer's CLAUDE_USAGE_ATTR (None: a Gemini-only generation)."""

    def __init__(self, outcomes: List[Any], usages: List[Any]):
        super().__init__(outcomes)
        self.usages = list(usages)

    async def __call__(self, *args, **kwargs):
        from app.services.marketing import writer_service

        usage = self.usages.pop(0)
        try:
            result = await super().__call__(*args, **kwargs)
        except Exception as e:
            if usage:
                setattr(e, writer_service.CLAUDE_USAGE_ATTR, usage)
            raise
        if usage:
            result.claude_usage = usage
        return result


class _PatchSpy(mrs.MarketingRunService):
    def __init__(self, sb):
        super().__init__(supabase=sb)
        self.patches: List[Dict[str, Any]] = []

    async def update_script_where(self, run_id, patch, *, expect):
        self.patches.append(dict(patch))
        return await super().update_script_where(run_id, patch, expect=expect)


def test_the_claude_usage_attribute_is_the_writers():
    from app.services.marketing import writer_service

    assert ss._CLAUDE_USAGE_ATTR == writer_service.CLAUDE_USAGE_ATTR


@pytest.mark.asyncio
async def test_a_gemini_only_day_never_writes_the_claude_column(world):
    """The default path writes exactly what it wrote before migration 206 — so it works against a table
    that does not have the column yet."""
    sb, _runs = world
    spy = _PatchSpy(sb)
    # the content-rejection and accepted writes (the failure write: the next test)
    svc = ss.MarketingScriptService(spy, writer=FakeWriter(["rejected", "accepted"]))
    rid = _run(sb, POSTING_DAY)
    for _ in range(2):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    assert _script_row(sb, rid)["status"] == "accepted"
    assert len([p for p in spy.patches if "tokens_used" in p]) == 2
    assert not any("claude_usage" in p for p in spy.patches) and "claude_usage" not in _script_row(sb, rid)


@pytest.mark.asyncio
async def test_a_gemini_only_failure_never_writes_the_claude_column(world):
    sb, _runs = world
    spy = _PatchSpy(sb)
    svc = ss.MarketingScriptService(spy, writer=FakeWriter([_timeout_spending(5)]))
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert _script_row(sb, rid)["tokens_used"] == 5
    assert len([p for p in spy.patches if "tokens_used" in p]) == 1
    assert not any("claude_usage" in p for p in spy.patches) and "claude_usage" not in _script_row(sb, rid)


@pytest.mark.parametrize("second, status", [("accepted", "accepted"), ("rejected", "selected"),
                                            ("timeout", "selected")])
@pytest.mark.asyncio
async def test_claude_usage_accumulates_across_generations_like_tokens_used(world, second, status):
    sb, runs = world
    outcome = _timeout_spending(777) if second == "timeout" else second
    svc = ss.MarketingScriptService(runs, writer=_BillingWriter(["rejected", outcome], [_U1, _U2]))
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert _script_row(sb, rid)["claude_usage"] == _U1
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    row = _script_row(sb, rid)
    assert row["status"] == status and row["claude_usage"] == _U1_U2
    assert row["tokens_used"] == 99 + {"accepted": 1234, "rejected": 99, "timeout": 777}[second]


@pytest.mark.asyncio
async def test_a_later_gemini_only_generation_keeps_the_stored_claude_usage(world):
    sb, runs = world
    spy = _PatchSpy(sb)
    svc = ss.MarketingScriptService(spy, writer=_BillingWriter(["rejected", "accepted"], [_U1, None]))
    rid = _run(sb, POSTING_DAY)
    for _ in range(2):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    row = _script_row(sb, rid)
    assert row["status"] == "accepted" and row["claude_usage"] == _U1
    assert "claude_usage" not in spy.patches[-1], "the accepted write leaves the column alone"


@pytest.mark.parametrize("junk", ["junk", [], {"claude-sonnet-5-5": {"input": -1}}, {"m": {"thinking": 3}}])
@pytest.mark.asyncio
async def test_an_unreadable_stored_claude_usage_is_never_overwritten(world, caplog, junk):
    """Rewriting it would drop whatever tokens it held; left alone, the digest reads that day's model parts
    as unreadable — a floor, never a wrong number. tokens_used still adds up."""
    sb, runs = world
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, claude_usage=junk, tokens_used=50)
    svc = ss.MarketingScriptService(runs, writer=_BillingWriter(["accepted"], [_U1]))
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    row = _script_row(sb, rid)
    assert row["status"] == "accepted" and row["tokens_used"] == 50 + 1234
    assert row["claude_usage"] == junk
    assert any("unreadable claude_usage" in m for _lvl, m in _msgs(caplog))


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


# ── the captions' value line: the store state is read at WRITE time (2026-10-05) ─────────────

_STORE_URL = "https://apps.apple.com/us/app/caydex/id6759525689"


def _store_settings(monkeypatch, url: str, preorder: bool) -> None:
    """The two settings `smart_link.store_state()` decides from, and its one-time misconfig flag
    (restored after the test, so an ERROR logged here never silences another test's)."""
    from app.config import settings
    from app.services.marketing import smart_link

    monkeypatch.setattr(settings, "MARKETING_APP_STORE_URL", url)
    monkeypatch.setattr(settings, "MARKETING_APP_STORE_PREORDER", preorder)
    monkeypatch.setattr(smart_link, "_misconfig_logged", False)


@pytest.mark.parametrize("url, preorder, expected", [
    ("", False, "prelaunch"),                                    # pre-launch: no URL
    ("", True, "prelaunch"),                                     # the flag alone claims nothing
    ("https://evil.com/app/id6759525689", True, "prelaunch"),    # an invalid URL is pre-launch
    (_STORE_URL, False, "live"),
    (_STORE_URL, True, "preorder"),
], ids=["no-url", "flag-without-url", "invalid-url-with-flag", "live", "preorder"])
@pytest.mark.asyncio
async def test_the_service_passes_the_configured_store_state_to_the_writer(world, monkeypatch, url,
                                                                           preorder, expected):
    """The captions' code-owned value line (the claim-free prelaunch line / "pre-order on the App
    Store" / "on the App Store") is decided by `smart_link.store_state()` from the two settings, and the
    service STATES it on every writer call: the FakeWriter's default is a sentinel, so even the
    prelaunch rows prove it was passed, not defaulted. An invalid URL is prelaunch — exactly when
    /go still lands on the landing page with no store link — so a caption can never say "on the App
    Store" while its link lands there."""
    from app.services.marketing import post_copy

    sb, runs = world
    _store_settings(monkeypatch, url, preorder)
    rid = _run(sb, POSTING_DAY)
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert [c["store_state"] for c in writer.calls] == [expected]
    assert expected in post_copy.STORE_STATES
    assert _script_row(sb, rid)["status"] == "accepted"


@pytest.mark.asyncio
async def test_the_store_state_is_read_per_generation_never_cached(world, monkeypatch):
    """Read at WRITE time, once per generation: a setting changed between two generations of one
    day (a redeploy at approval: URL + pre-order flag in one change) reaches the next prompt and
    validator. Caching it on the service (or at import) would keep writing the prelaunch line."""
    sb, runs = world
    _store_settings(monkeypatch, "", False)
    rid = _run(sb, POSTING_DAY)
    writer = FakeWriter(["rejected", "accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert _script_row(sb, rid)["status"] == "selected", "sentinel: one content round, below the cap"
    _script_row(sb, rid)["retry_not_before"] = None   # the next hourly tick
    _store_settings(monkeypatch, _STORE_URL, True)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert [c["store_state"] for c in writer.calls] == ["prelaunch", "preorder"]
    assert _script_row(sb, rid)["status"] == "accepted"


@pytest.mark.asyncio
async def test_an_unreadable_store_state_is_a_recorded_writer_failure_never_a_crash(world, monkeypatch,
                                                                                    caplog):
    """The read sits INSIDE the writer call's try: a raise is a writer failure (counted against
    MAX_WRITER_FAILURES, never the content cap, with a back-off), logged at ERROR — not a
    "generation CRASHED" hand-back, and no model call is made with a state nobody decided."""
    sb, runs = world

    def unreadable() -> str:
        raise RuntimeError("store state unreadable")

    monkeypatch.setattr(ss.smart_link, "store_state", unreadable)
    rid = _run(sb, POSTING_DAY)
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    assert writer.calls == []
    row = _script_row(sb, rid)
    assert row["status"] == "selected" and row["content_rejections"] == 0
    assert row["last_error"] == "RuntimeError: store state unreadable"
    assert row["retry_not_before"] is not None and row["generations"] == 1
    msgs = [(r.levelno, r.getMessage()) for r in caplog.records]
    assert any(lvl == logging.ERROR and "writer FAILED" in m and "store state unreadable" in m
               for lvl, m in msgs), msgs
    assert not any("generation CRASHED" in m for _lvl, m in msgs), msgs


# ── drop 1: the formats frozen into the accepted output (contract C3) ────────
#
# Which of text / image each text platform's post takes is decided ONCE, when the accepted output is
# written, from MARKETING_IMAGE_POSTS / MARKETING_X_IMAGES and the writer's validated `image_post` —
# and frozen into the stored output (`post_formats`, `image_post`, `image_footer`). The worker reads
# them back from the script; `create_posts` records each platform only in its frozen format.

from app.services.marketing import post_copy  # noqa: E402

_IMG = {"title": "Three habits that compound",
        "paragraphs": ["Small, steady saving adds up.", "Costs you avoid keep working for you."]}
_ALL_POSTS = ("x", "bluesky", "facebook", "linkedin", "threads", "youtube", "tiktok", "instagram")


def _image_package(image_post: Any = None, posts=_ALL_POSTS, **extra) -> Dict[str, Any]:
    pkg = _package(KEY)
    pkg["posts"] = {p: {"platform": p, "title": "T" if p == "youtube" else None, "caption": f"copy {p}"}
                    for p in posts}
    pkg["image_post"] = copy.deepcopy(_IMG) if image_post is None else image_post
    pkg.update(extra)
    return pkg


def _image_switches(monkeypatch, images: bool, x_images: bool = False) -> None:
    monkeypatch.setattr(ss.settings, "MARKETING_IMAGE_POSTS", images)
    monkeypatch.setattr(ss.settings, "MARKETING_X_IMAGES", x_images)


class ImageWriter(FakeWriter):
    """FakeWriter whose accepted package carries copy for every outlet and an `image_post` (or the
    one given — None for none, anything else verbatim)."""

    def __init__(self, outcomes, image_post: Any = "default", **kw):
        super().__init__(outcomes, **kw)
        self.image_post = image_post

    async def __call__(self, *a, **k):
        result = await super().__call__(*a, **k)
        if result.package is not None:
            judge = result.package.get("judge")
            result.package.clear()
            result.package.update(_image_package(posts=_ALL_POSTS))
            if self.image_post != "default":
                result.package["image_post"] = self.image_post
            if judge is not None:
                result.package["judge"] = judge
        return result


_VIDEO_FORMATS = {"youtube": "video", "tiktok": "video", "instagram": "video"}


@pytest.mark.parametrize("images, x_images, expected_text_formats", [
    (False, False, {p: "text" for p in ("x", "bluesky", "facebook", "linkedin", "threads")}),
    (False, True, {p: "text" for p in ("x", "bluesky", "facebook", "linkedin", "threads")}),
    (True, False, {"x": "text", **{p: "image" for p in ("bluesky", "facebook", "linkedin", "threads")}}),
    (True, True, {p: "image" for p in ("x", "bluesky", "facebook", "linkedin", "threads")}),
])
def test_freeze_post_formats_decides_each_platform_from_the_switches(images, x_images, expected_text_formats):
    package = _image_package()
    snapshot = copy.deepcopy(package)
    out = ss.freeze_post_formats(package, POSTING_DAY, image_posts=images, x_images=x_images, run_id="r")
    assert out["post_formats"] == {**_VIDEO_FORMATS, **expected_text_formats}
    assert out["image_post"] == _IMG                       # kept either way: it is the writer's text
    if "image" in out["post_formats"].values():
        assert out["image_footer"] == post_copy.image_footer(POSTING_DAY)
    else:
        assert "image_footer" not in out
    assert package == snapshot                              # a copy: the writer's package is untouched
    assert mrs.frozen_post_formats(out) == out["post_formats"]   # what is stored reads back


@pytest.mark.parametrize("bad", [
    {"title": "t", "paragraphs": ["only one"]}, {"title": "", "paragraphs": ["a", "b"]},
    {"paragraphs": ["a", "b"]}, "a string", ["t", "a", "b"], 42,
    {"title": "t", "paragraphs": ["a", "b", "c", "d", "e"]},
])
def test_an_unusable_image_post_makes_every_platform_text_and_is_logged(bad, caplog):
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        out = ss.freeze_post_formats(_image_package(image_post=bad), POSTING_DAY, image_posts=True,
                                     x_images=True, run_id="run-7")
    assert out["image_post"] is None and "image_footer" not in out
    assert set(out["post_formats"].values()) == {"text", "video"}
    assert any("image_post is unusable" in r.getMessage() and "run-7" in r.getMessage()
               for r in caplog.records)


def test_an_absent_image_post_is_text_without_a_warning(caplog):
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        pkg = _image_package()
        del pkg["image_post"]
        out = ss.freeze_post_formats(pkg, POSTING_DAY, image_posts=True, x_images=True)
        out2 = ss.freeze_post_formats(_image_package(image_post=None) | {"image_post": None}, POSTING_DAY,
                                      image_posts=True, x_images=True)
    for o in (out, out2):
        assert o["image_post"] is None and set(o["post_formats"].values()) == {"text", "video"}
    assert not caplog.records


def test_freeze_covers_only_the_outlets_with_copy_and_drops_a_stale_footer(caplog):
    pkg = _image_package(posts=("bluesky", "tiktok"), image_footer="a stale footer from somewhere")
    out = ss.freeze_post_formats(pkg, POSTING_DAY, image_posts=False, x_images=False)
    assert out["post_formats"] == {"bluesky": "text", "tiktok": "video"}   # dropped outlets: no format
    assert "image_footer" not in out
    # a platform outside the format map gets no format, loudly
    pkg = _image_package(posts=("bluesky", "pinterest"))
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        out = ss.freeze_post_formats(pkg, POSTING_DAY, image_posts=True, x_images=False, run_id="r")
    assert out["post_formats"] == {"bluesky": "image"}
    assert any("pinterest" in r.getMessage() for r in caplog.records)
    # no posts at all: an empty map, never an error
    assert ss.freeze_post_formats({"posts": None}, POSTING_DAY, image_posts=True, x_images=True)[
        "post_formats"] == {}


@pytest.mark.asyncio
async def test_an_accepted_package_freezes_its_formats_and_the_worker_reads_them_back(world, monkeypatch):
    sb, runs = world
    _image_switches(monkeypatch, images=True, x_images=False)
    svc = ss.MarketingScriptService(runs, writer=ImageWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    stored = _script_row(sb, rid)["output"]
    expected = {**_VIDEO_FORMATS, "x": "text", "bluesky": "image", "facebook": "image",
                "linkedin": "image", "threads": "image"}
    assert stored["post_formats"] == expected
    assert stored["image_post"] == _IMG and stored["image_footer"] == post_copy.image_footer(POSTING_DAY)
    assert stored["judge"]["mode"] == "enforce" and stored["posts"]["x"]["caption"] == "copy x"
    state = await svc.kick(rid, claim=_holder(svc, rid))
    script = state["script"]
    assert script["post_formats"] == expected and script["image_post"] == _IMG
    assert script["image_footer"] == post_copy.image_footer(POSTING_DAY)
    # The wire model carries them (add-only fields of WorkerScript).
    from app.schemas.marketing import ScriptKickResponse

    wire = ScriptKickResponse.model_validate(state).model_dump()["script"]
    assert wire["post_formats"] == expected and wire["image_post"] == _IMG
    # FROZEN: flipping the switches after acceptance changes nothing the worker reads.
    _image_switches(monkeypatch, images=False, x_images=False)
    again = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert again["post_formats"] == expected and again["image_footer"] == script["image_footer"]


@pytest.mark.asyncio
@pytest.mark.parametrize("at_start, expected_x", [(False, "text"), (True, "image")])
async def test_the_image_switch_is_read_once_when_the_generation_starts_and_freezes_with_it(
        world, monkeypatch, at_start, expected_x):
    """Drop-1 review (server:F4): MARKETING_IMAGE_POSTS is read ONCE, when the generation starts (like
    the store state): the writer is told it — off, an image problem alone buys no repair round — and
    the formats are frozen with the SAME value. A flip during a slow generation lands on the next
    generation, never half on this one (a writer told "off" and formats frozen "image", or the
    reverse). This used to read the switch again at the accepted write."""
    sb, runs = world
    _image_switches(monkeypatch, images=at_start, x_images=True)
    writer = ImageWriter(["accepted"])
    writer.gate = asyncio.Event()
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    for _ in range(500):   # until the generation is INSIDE the writer (past every pre-call read)
        if writer.calls:
            break
        await asyncio.sleep(0.01)
    assert writer.calls and not writer.gate.is_set()
    _image_switches(monkeypatch, images=not at_start, x_images=True)
    writer.gate.set()
    await _drain(svc)
    assert [c["image_posts"] for c in writer.calls] == [at_start]
    assert _script_row(sb, rid)["output"]["post_formats"]["x"] == expected_x


@pytest.mark.asyncio
async def test_an_unusable_image_post_never_rejects_the_package(world, monkeypatch, caplog):
    sb, runs = world
    _image_switches(monkeypatch, images=True, x_images=True)
    svc = ss.MarketingScriptService(runs, writer=ImageWriter(["accepted"], image_post={"title": "only"}))
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    row = _script_row(sb, rid)
    assert row["status"] == "accepted" and row["generations"] == 1
    assert row["output"]["image_post"] is None and "image_footer" not in row["output"]
    assert set(row["output"]["post_formats"].values()) == {"text", "video"}
    assert any("image_post is unusable" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_freeze_failure_keeps_the_paid_package_without_formats(world, monkeypatch, caplog):
    """An unexpected raise while freezing must not throw away a paid, accepted package (that would
    re-bill a generation): it is stored WITHOUT formats — which reads back exactly as a script accepted
    before image posts existed — and the failure is an ERROR."""
    sb, runs = world
    _image_switches(monkeypatch, images=True)

    def broken(*_a, **_k):
        raise RuntimeError("freeze broke")

    monkeypatch.setattr(ss, "freeze_post_formats", broken)
    svc = ss.MarketingScriptService(runs, writer=ImageWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    row = _script_row(sb, rid)
    assert row["status"] == "accepted" and row["generations"] == 1
    assert "post_formats" not in row["output"] and "image_footer" not in row["output"]
    # Batch 1: the day that should have carried the picture says why it did not (X is off by default).
    assert row["output"]["image_fallback"] == {"reason": "freeze_failed",
                                               "platforms": ["bluesky", "facebook", "linkedin", "threads"]}
    assert any(r.levelno == logging.ERROR and "could not freeze the post formats" in r.getMessage()
               for r in caplog.records)
    script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert script["post_formats"] is None and script["image_post"] is None and script["image_footer"] is None


@pytest.mark.asyncio
async def test_a_script_accepted_before_drop_1_reads_back_exactly_as_before(world):
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, status="accepted", output=_package(KEY), generation_id=str(uuid.uuid4()))
    state = await svc.kick(rid, claim=_holder(svc, rid))
    script = state["script"]
    assert script["post_formats"] is None and script["image_post"] is None and script["image_footer"] is None
    assert script["outlets"] == ["x", "youtube"]
    from app.schemas.marketing import ScriptKickResponse

    assert ScriptKickResponse.model_validate(state).script.post_formats is None


@pytest.mark.parametrize("bad", [{"x": "carousel"}, "image", {"x": "image"}])
@pytest.mark.asyncio
async def test_frozen_formats_that_do_not_read_back_reach_the_worker_as_none(world, caplog, bad):
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    _seed(sb, rid, status="accepted", output={**_package(KEY), "post_formats": bad,
                                              "image_post": copy.deepcopy(_IMG)},
          generation_id=str(uuid.uuid4()))
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert script["post_formats"] is None and script["image_post"] is None and script["image_footer"] is None
    assert any(r.levelno == logging.ERROR and "do not read back" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_an_image_day_end_to_end_records_image_posts_with_the_frozen_formats(world, monkeypatch):
    """kick → accepted (image posts on) → the worker's post image (a ready card, role post_image, named
    by the run's pointer) → create_posts: the text platforms as images, X as text, video as video."""
    sb, runs = world
    _image_switches(monkeypatch, images=True, x_images=False)
    svc = ss.MarketingScriptService(runs, writer=ImageWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    image = _asset(sb, rid, kind="card")
    next(a for a in sb.tables[mrs.ASSETS].rows if a["id"] == image)["metadata"] = {"image_role": "post_image"}
    _run_row(sb, rid)["metadata"]["image_asset_id"] = image
    video = _run_video(sb, rid)
    posts = await runs.create_posts(rid, [
        {"platform": "bluesky", "format": "image", "asset_ids": [image]},
        {"platform": "threads", "format": "image", "asset_ids": [image]},
        {"platform": "x", "format": "text"},
        {"platform": "youtube", "format": "video", "asset_ids": [video]},
    ], claim=_holder_of(runs, rid))
    assert [(p["platform"], p["format"], p["status"]) for p in posts] == [
        ("bluesky", "image", "pending_review"), ("threads", "image", "pending_review"),
        ("x", "text", "pending_review"), ("youtube", "video", "pending_review")]
    assert [p["caption"] for p in posts] == ["copy bluesky", "copy threads", "copy x", "copy youtube"]
    # X was frozen as text: an image for it is refused, whatever the worker asks.
    with pytest.raises(mrs.MarketingRequestInvalid):
        await runs.create_posts(rid, [{"platform": "x", "format": "image", "asset_ids": [image]}],
                                claim=_holder_of(runs, rid))


def test_the_kick_route_serializes_the_frozen_formats(client, monkeypatch):
    import app.api.v1.endpoints.marketing_internal as mi

    script = {"hook": "h", "video_script": ["l"], "cards": [], "carousel_slides": [],
              "disclaimer_card": "d", "outlets": ["bluesky", "tiktok"],
              "post_formats": {"bluesky": "image", "tiktok": "video"}, "image_post": _IMG,
              "image_footer": post_copy.image_footer(POSTING_DAY)}

    class Svc:
        def __init__(self, s):
            self.s = s

        async def kick(self, run_id, *, claim):
            return {"status": "accepted", "source_ref": KEY, "template_id": "checklist", "script": self.s}

    monkeypatch.setattr(mi, "get_marketing_script_service", lambda: Svc(script))
    body = client.post(f"{_BASE}/runs/r1/script", headers=_H).json()
    assert body["script"]["post_formats"] == {"bluesky": "image", "tiktok": "video"}
    # news-v23 (restyle 2026-11-19): the lesson picture's footer is the one short line
    assert body["script"]["image_post"] == _IMG and body["script"]["image_footer"] == "Not investment advice"
    # an older service shape (no drop-1 keys) still answers, with nulls the worker treats as absent
    legacy = {k: v for k, v in script.items() if k not in ("post_formats", "image_post", "image_footer")}
    monkeypatch.setattr(mi, "get_marketing_script_service", lambda: Svc(legacy))
    body = client.post(f"{_BASE}/runs/r1/script", headers=_H).json()
    assert body["script"]["post_formats"] is None and body["script"]["image_footer"] is None


def test_the_image_switches_ship_off_and_the_x_image_price_is_bounded(monkeypatch):
    """Declared defaults (not this machine's environment): every drop-1 switch fail-closed, and the X
    image price a bounded integer that fails the deploy when it is not."""
    from pydantic import ValidationError

    from app.config import Settings

    fields = Settings.model_fields
    assert fields["MARKETING_REVIEW_BUNDLES"].default is False
    assert fields["MARKETING_IMAGE_POSTS"].default is False
    assert fields["MARKETING_X_IMAGES"].default is False
    assert fields["MARKETING_X_IMAGE_POST_MICROS"].default == 200_000
    for bad in ("-1", "1000001", "abc", "0.5", ""):
        monkeypatch.setenv("MARKETING_X_IMAGE_POST_MICROS", bad)
        with pytest.raises(ValidationError) as e:
            Settings()
        assert [err["loc"] for err in e.value.errors()] == [("MARKETING_X_IMAGE_POST_MICROS",)], bad
    for ok in ("0", "1000000", "200000"):
        monkeypatch.setenv("MARKETING_X_IMAGE_POST_MICROS", ok)
        assert Settings().MARKETING_X_IMAGE_POST_MICROS == int(ok)


# ── UP-IMG-1: a Threads image post only while its caption fits 500 UTF-8 BYTES ─────


@pytest.mark.parametrize("caption, nbytes, expected", [
    ("a" * 500, 500, "image"),                      # 500 bytes: fits
    ("a" * 498 + "é", 500, "image"),                # 499 characters, 500 bytes: fits
    ("a" * 499 + "é", 501, "text"),                 # 500 characters, 501 bytes: does not
    ("a" * 499 + "—", 502, "text"),                 # 500 characters (the value line's em dash): does not
    ("a" * 501, 501, "text"),
])
def test_a_threads_caption_over_500_utf8_bytes_freezes_threads_as_text(caption, nbytes, expected, caplog):
    """Upload-Post splits Threads text over 500 BYTES into a thread unless `threads_long_text_as_post`
    works — verified only on its text route. A longer caption keeps Threads on the text route (one
    post); the rule is Threads-only (Bluesky's equally long caption still takes the image)."""
    assert len(caption.encode("utf-8")) == nbytes
    pkg = _image_package()
    pkg["posts"]["threads"]["caption"] = caption
    pkg["posts"]["bluesky"]["caption"] = caption
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        out = ss.freeze_post_formats(pkg, POSTING_DAY, image_posts=True, x_images=True, run_id="run-9")
    assert out["post_formats"]["threads"] == expected
    assert out["post_formats"]["bluesky"] == "image" and out["image_footer"]
    logged = [r for r in caplog.records if "Threads caption" in r.getMessage()]
    if expected == "text":
        (rec,) = logged
        assert rec.levelno == logging.INFO and f"{nbytes} UTF-8 bytes" in rec.getMessage()
        assert "run-9" in rec.getMessage()
    else:
        assert logged == []
    assert mrs.frozen_post_formats(out) == out["post_formats"]


def _composed_template(rec, run_date, pictured):
    """A composed template output as the build stores it before the freeze: every logo a wordmark."""
    out = news_templates.compose(rec, run_date=run_date, store_state="live", allow_x_url=False,
                                 picture_outlets=pictured)
    out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None, "width": None,
                     "height": None} for r in out["logo_refs"]]
    return out


def test_a_list_posts_x_and_bluesky_freeze_in_the_format_their_captions_were_written_for(caplog):
    """Review 2026-11-20 (confirmed): an eight-change 13F used to DROP X and Bluesky (their captions could not list the
    picture's "+2 more"), X although it is TEXT under the production switches. Composed for the production picture
    platforms (`template_picture_outlets(True, False, …)`: X not among them), X is a text post (rows from the top, no
    promise owed) and Bluesky — pictured, but too short to keep the promise — is written as a text post too and listed in
    `text_outlets`: the freeze makes it TEXT with `image_fallback` reason `caption_list_long` (owner words), while
    Facebook, LinkedIn and Threads post the picture. Mutation-checked by hand: the `list_text` branch removed from
    `freeze_post_formats` froze Bluesky as an image under a "+2 more" its caption does not keep — red."""
    import test_marketing_news_templates as NT

    platforms = ("bluesky", "facebook", "linkedin", "threads")
    pictured = ss.template_picture_outlets(True, False, platforms)
    assert pictured == ["bluesky", "facebook", "linkedin", "threads"]
    assert ss.template_picture_outlets(False, True, platforms) == []
    assert ss.template_picture_outlets(True, True, ("threads", "tiktok", "nope")) == ["threads", "x"]
    out = _composed_template(NT.f13s_list8(), NT.SPOT_SAT, pictured)
    assert out["dropped_outlets"] == {} and out["text_outlets"] == ["bluesky"] and out["picture_outlets"] == pictured
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        frozen = ss.freeze_post_formats(out, NT.SPOT_SAT, image_posts=True, x_images=False, run_id="r-list",
                                        image_platforms=platforms)
    formats = frozen["post_formats"]
    assert formats["bluesky"] == "text" and formats["x"] == "text"
    assert {formats[p] for p in ("facebook", "linkedin", "threads")} == {"image"}
    assert frozen[ss.IMAGE_FALLBACK_KEY] == {"reason": "caption_list_long", "platforms": ["bluesky"]}
    assert "caption_list_long" in schemas.IMAGE_FALLBACK_REASONS
    assert any("could not list the rows its picture leaves out" in r.getMessage() for r in caplog.records)
    # a platform the COMPOSITION did not picture never posts the picture, whatever the freeze's switches say — its
    # caption is a text post's (no fallback: as if left off)
    only_fb = _composed_template(NT.f13s_list8(), NT.SPOT_SAT, ["facebook"])
    frozen = ss.freeze_post_formats(only_fb, NT.SPOT_SAT, image_posts=True, x_images=True, run_id="r-list2")
    assert frozen["post_formats"]["facebook"] == "image"
    assert {frozen["post_formats"][p] for p in ("x", "bluesky", "linkedin", "threads")} == {"text"}
    assert ss.IMAGE_FALLBACK_KEY not in frozen
    assert news_templates.revalidate(json.loads(json.dumps(frozen)), fact_sheet=json.loads(json.dumps(
        news_rules.fact_sheet(NT.f13s_list8(), rejections={}, selection={}))), run_date=NT.SPOT_SAT) == []


def test_a_long_threads_caption_with_image_posts_off_is_text_as_always(caplog):
    pkg = _image_package()
    pkg["posts"]["threads"]["caption"] = "a" * 499 + "—"
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        out = ss.freeze_post_formats(pkg, POSTING_DAY, image_posts=False, x_images=False)
    assert out["post_formats"]["threads"] == "text"
    assert not [r for r in caplog.records if "Threads caption" in r.getMessage()]


# ── compat F2: image formats only for a run whose holding worker can render them ──


@pytest.mark.asyncio
async def test_an_old_worker_holding_the_run_gets_text_posts_and_a_warning_naming_it(world, monkeypatch, caplog):
    """MARKETING_IMAGE_POSTS on, but the worker that claimed the run declared nothing (an older image):
    every text platform is frozen as TEXT — the posts it can make — instead of `image`, which its
    create_posts call would have refused for the whole day (videos included)."""
    sb, runs = world
    _image_switches(monkeypatch, images=True, x_images=True)
    svc = ss.MarketingScriptService(runs, writer=ImageWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY, worker_version="phase4")
    _run_row(sb, rid)["metadata"].pop("worker_capabilities")
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    out = _script_row(sb, rid)["output"]
    assert out["post_formats"] == {**_VIDEO_FORMATS, **{p: "text" for p in ("x", "bluesky", "facebook",
                                                                            "linkedin", "threads")}}
    assert "image_footer" not in out and out["image_post"] == _IMG
    warned = [r.getMessage() for r in caplog.records if "did not declare" in r.getMessage()]
    assert len(warned) == 1 and "'phase4'" in warned[0] and rid in warned[0] and "TEXT" in warned[0]
    # Batch 1: recorded for the decision message and the digest (X too: MARKETING_X_IMAGES is on here).
    assert out["image_fallback"] == {"reason": "worker_capability",
                                     "platforms": ["bluesky", "facebook", "linkedin", "threads", "x"]}


@pytest.mark.parametrize("declared_at_start, declared_at_write, expected", [
    (True, False, "text"),    # an older worker re-claimed the run during the generation
    (False, True, "image"),   # a drop-1 worker re-claimed it
])
@pytest.mark.asyncio
async def test_the_capability_of_the_worker_holding_the_run_at_write_time_decides(
        world, monkeypatch, declared_at_start, declared_at_write, expected):
    sb, runs = world
    _image_switches(monkeypatch, images=True, x_images=True)
    writer = ImageWriter(["accepted"])
    writer.gate = asyncio.Event()
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    meta = _run_row(sb, rid)["metadata"]
    if not declared_at_start:
        meta.pop("worker_capabilities")
    await svc.kick(rid, claim=_holder(svc, rid))
    for _ in range(500):
        if writer.calls:
            break
        await asyncio.sleep(0.01)
    assert writer.calls and not writer.gate.is_set()
    if declared_at_write:
        meta["worker_capabilities"] = ["post_image"]
    else:
        meta.pop("worker_capabilities", None)
    writer.gate.set()
    await _drain(svc)
    assert _script_row(sb, rid)["output"]["post_formats"]["bluesky"] == expected


@pytest.mark.asyncio
async def test_a_run_that_cannot_be_read_at_write_time_freezes_text(world, monkeypatch, caplog):
    sb, runs = world
    _image_switches(monkeypatch, images=True, x_images=True)
    writer = ImageWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    real_get_run = runs.get_run

    async def broken_after_the_model_call(run_id):
        if writer.calls:      # the write-time read of the holder fails; the kick's own reads did not
            raise mrs.MarketingRunError("get_run failed: 520")
        return await real_get_run(run_id)

    monkeypatch.setattr(runs, "get_run", broken_after_the_model_call)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    row = _script_row(sb, rid)
    assert row["status"] == "accepted" and set(row["output"]["post_formats"].values()) == {"text", "video"}
    assert any("could not read run_id" in r.getMessage() and "frozen as text" in r.getMessage()
               for r in caplog.records)
    # Batch 1: a ledger blip is "the picture could not be prepared" — never the worker's fault.
    assert row["output"]["image_fallback"] == {"reason": "freeze_failed",
                                               "platforms": ["bluesky", "facebook", "linkedin", "threads", "x"]}


@pytest.mark.asyncio
async def test_with_image_posts_off_the_holder_is_never_consulted(world, monkeypatch):
    """Switches off: the freeze is what it was before the capability check — no run read, no warning."""
    sb, runs = world
    _image_switches(monkeypatch, images=False)
    svc = ss.MarketingScriptService(runs, writer=ImageWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    _run_row(sb, rid)["metadata"].pop("worker_capabilities")
    asked: List[str] = []

    async def spy(run_id, gen_id, **_kw):
        asked.append(run_id)
        return None

    monkeypatch.setattr(svc, "_holder_image_block", spy)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert asked == []
    out = _script_row(sb, rid)["output"]
    assert set(out["post_formats"].values()) == {"text", "video"}
    assert "image_fallback" not in out          # switches off: a text day on purpose, never a fallback


# ── Batch 1 (2026-10-15): MARKETING_IMAGE_PLATFORMS and the `image_fallback` reason ─────────────
#
# Which platforms post the day's picture is a switch read at write time (X keeps MARKETING_X_IMAGES),
# and a day that SHOULD have carried the picture but froze some of those platforms as text says why:
# `output.image_fallback = {"reason": <schemas.IMAGE_FALLBACK_REASONS code>, "platforms": [...]}`,
# which create_posts stamps on the posts. A platform left off on purpose (or X with its switch off) is
# text on purpose — never a fallback.

import ast  # noqa: E402
from pathlib import Path  # noqa: E402

from app.schemas import marketing as schemas  # noqa: E402
from app.services.marketing import template_onscreen  # noqa: E402

_PICTURE_DEFAULT = "bluesky,facebook,linkedin,threads"
_FOUR = ["bluesky", "facebook", "linkedin", "threads"]
#: 500 characters, 502 UTF-8 bytes (the value line's em dash): over Threads' picture-post limit.
_LONG_THREADS = "a" * 499 + "—"


def _freeze_before_batch_1(output: Dict[str, Any], run_date: date, *, image_posts: bool,
                           x_images: bool) -> Dict[str, Any]:
    """The freeze's DECISIONS exactly as they were before Batch 1 (drop 1 + drop 2, logging left out) —
    the reference a default-argument freeze must still reproduce, key for key, byte for byte."""
    out = dict(output)
    template = output.get("authorship") == "template"
    template_footer = output.get("image_footer")
    if template and (not isinstance(template_footer, str) or not template_footer.strip()):
        raise ValueError("a template output carries no image_footer")
    image_post = schemas.normalize_image_post(output.get("image_post"))
    out["image_post"] = image_post
    image_ok = image_post is not None
    if template and image_ok and template_onscreen.validate_image_spec(
            output.get("image_spec"), template_onscreen.logo_keys(output.get("logos")),
            footer=template_footer) is not None:
        image_ok = False
    posts = output.get("posts") if isinstance(output.get("posts"), dict) else {}
    formats: Dict[str, str] = {}
    for platform in sorted(posts):
        allowed = schemas.POST_FORMATS_BY_PLATFORM.get(platform, ())
        if "video" in allowed:
            formats[platform] = "video"
        elif "image" in allowed and image_posts and image_ok and (platform != "x" or x_images):
            caption = posts[platform].get("caption") if isinstance(posts[platform], dict) else None
            size = len(caption.encode("utf-8")) if isinstance(caption, str) else 0
            formats[platform] = "text" if platform == "threads" and size > 500 else "image"
        elif "text" in allowed:
            formats[platform] = "text"
    out["post_formats"] = formats
    if template:
        out["image_footer"] = template_footer
    elif "image" in formats.values():
        out["image_footer"] = post_copy.image_footer(run_date)
    else:
        out.pop("image_footer", None)
    return out


def _template_output(**over: Any) -> Dict[str, Any]:
    """A minimal TEMPLATE output whose image_spec can never validate (no such layout, no logos)."""
    out = {"authorship": "template", "image_footer": "Educational only · Source: SEC filings · Caydex",
           "image_spec": {"layout": "table"}, "logos": [], "image_post": copy.deepcopy(_IMG),
           "posts": {p: {"platform": p, "caption": f"copy {p}"} for p in _ALL_POSTS}}
    out.update(over)
    return out


def _freeze_inputs() -> List[Dict[str, Any]]:
    """Packages across every branch of the freeze: a usable / unusable / absent / garbage image post;
    every outlet, a few, only X, none; a Threads caption under and over its byte limit; a template whose
    spec does not validate; a stale `image_fallback` and footer copied into the input."""
    cases: List[Dict[str, Any]] = []
    for image_post in ("usable", {"title": "only"}, "absent", "a string", None):
        for posts in (_ALL_POSTS, ("bluesky", "tiktok"), ("x", "threads"), ("x",), ()):
            for long_threads in (False, True):
                pkg = _image_package(posts=posts)
                if image_post == "absent":
                    del pkg["image_post"]
                elif image_post != "usable":
                    pkg["image_post"] = image_post
                if long_threads and "threads" in pkg["posts"]:
                    pkg["posts"]["threads"]["caption"] = _LONG_THREADS
                cases.append(pkg)
    cases.append(_image_package(image_footer="a stale footer"))
    cases.append(_template_output())
    cases.append(_template_output(image_post=None))
    return cases


def test_a_default_freeze_decides_exactly_as_before_batch_1():
    """`image_platforms` defaults to every image platform: every caller that does not pass it (the
    preview script, the tests, a worker read-back) gets the formats, image post and footer it got before
    Batch 1, byte for byte. The ONE new key, `image_fallback`, appears exactly when image posts are on and
    a platform that should have posted the picture froze as text."""
    n = 0
    for pkg in _freeze_inputs():
        for image_posts in (False, True):
            for x_images in (False, True):
                old = _freeze_before_batch_1(copy.deepcopy(pkg), POSTING_DAY, image_posts=image_posts,
                                             x_images=x_images)
                for kw in ({}, {"image_platforms": None}, {"image_platforms": schemas.IMAGE_PLATFORMS}):
                    new = ss.freeze_post_formats(copy.deepcopy(pkg), POSTING_DAY, image_posts=image_posts,
                                                 x_images=x_images, run_id="r", **kw)
                    fallback = new.pop("image_fallback", None)
                    assert new == old, (pkg.get("posts", {}).keys(), image_posts, x_images, kw)
                    pictured = sorted(p for p in pkg.get("posts") or {}
                                      if p in schemas.IMAGE_PLATFORMS or (p == "x" and x_images))
                    fallen = [p for p in pictured if old["post_formats"].get(p) != "image"] if image_posts else []
                    assert (fallback or {}).get("platforms", []) == fallen
                    if fallback is not None:
                        assert fallback["reason"] in schemas.IMAGE_FALLBACK_REASONS
                        assert set(fallback) == {"reason", "platforms"}
                    n += 1
    assert n >= 600   # anti-vacuity: the matrix is really walked


def test_the_image_fallback_key_is_the_shared_contract():
    assert ss.IMAGE_FALLBACK_KEY == schemas.POST_METADATA_IMAGE_FALLBACK == "image_fallback"
    # review 2026-11-20: `caption_list_long` — a list post's X / Bluesky caption that cannot keep its picture's promise
    assert set(schemas.IMAGE_FALLBACK_REASONS) == {"image_dropped", "worker_capability", "image_spec_invalid",
                                                   "threads_caption_long", "caption_list_long", "freeze_failed"}
    assert all(isinstance(w, str) and w.strip() for w in schemas.IMAGE_FALLBACK_REASONS.values())


def test_the_worker_capability_words_name_this_picture_never_every_picture():
    """Review CTA-3 (2026-10-16): `_holder_image_block` answers "worker_capability" when the holder lacks ANY
    capability the day's picture needs — post_image, or news_templates on a template day, or layouts_2b for a
    `pair` / `grid` layout (test_the_holder_check_names_its_reason walks all three) — and a worker lacking
    only the last two draws a lesson picture every day. So the owner words (the POSTS decision message, the
    weekly digest) say it cannot draw THIS picture, never that it cannot draw pictures."""
    words = schemas.IMAGE_FALLBACK_REASONS["worker_capability"]
    assert words == "the worker that ran the day cannot draw this picture"
    assert not words.endswith("pictures")


@pytest.mark.parametrize("label, pkg, kw, reason, fallen", [
    ("an unusable image post", _image_package(image_post={"title": "only"}), {}, "image_dropped", _FOUR),
    ("no image post", {k: v for k, v in _image_package().items() if k != "image_post"}, {}, "image_dropped",
     _FOUR),
    ("no image post, X images on", _image_package(image_post=["t", "a", "b"]), {"x_images": True},
     "image_dropped", _FOUR + ["x"]),
    ("the holder cannot draw it", _image_package(), {"image_blocked": "worker_capability"},
     "worker_capability", _FOUR),
    ("the run could not be read", _image_package(), {"image_blocked": "freeze_failed"}, "freeze_failed", _FOUR),
    ("a holder block beats a dropped image", _image_package(image_post={"title": "only"}),
     {"image_blocked": "worker_capability", "x_images": True}, "worker_capability", _FOUR + ["x"]),
    ("a template spec that does not validate", _template_output(), {}, "image_spec_invalid", _FOUR),
    ("a template without its alt text", _template_output(image_post=None), {}, "image_dropped", _FOUR),
    ("a long Threads caption", _image_package(), {"long_threads": True}, "threads_caption_long", ["threads"]),
    ("only some platforms listed, one dropped", _image_package(image_post={"title": "only"}),
     {"image_platforms": ("facebook", "threads")}, "image_dropped", ["facebook", "threads"]),
])
def test_each_fallback_reason_is_recorded_with_the_platforms_it_cost(label, pkg, kw, reason, fallen, caplog):
    pkg = copy.deepcopy(pkg)
    kw = dict(kw)
    if kw.pop("long_threads", False):
        pkg["posts"]["threads"]["caption"] = _LONG_THREADS
    x_images = kw.pop("x_images", False)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        out = ss.freeze_post_formats(pkg, POSTING_DAY, image_posts=True, x_images=x_images, run_id="run-5", **kw)
    assert out["image_fallback"] == {"reason": reason, "platforms": fallen}, label
    assert all(out["post_formats"][p] == "text" for p in fallen)
    pictured = [p for p in out["post_formats"] if out["post_formats"][p] == "image"]
    assert not set(pictured) & set(fallen)
    assert mrs.frozen_post_formats(out) == out["post_formats"]           # what is stored reads back
    logged = [r.getMessage() for r in caplog.records if "recorded as image_fallback" in r.getMessage()]
    assert len(logged) == 1 and f"reason={reason}" in logged[0] and "run-5" in logged[0]


def test_a_platform_left_off_the_image_platforms_is_text_on_purpose_never_a_fallback():
    out = ss.freeze_post_formats(_image_package(), POSTING_DAY, image_posts=True, x_images=False,
                                 image_platforms=("bluesky",))
    assert out["post_formats"] == {**_VIDEO_FORMATS, "x": "text", "bluesky": "image", "facebook": "text",
                                   "linkedin": "text", "threads": "text"}
    assert "image_fallback" not in out and out["image_footer"] == post_copy.image_footer(POSTING_DAY)
    # none listed: every post text, no footer — and still no fallback (the owner chose it)
    out = ss.freeze_post_formats(_image_package(), POSTING_DAY, image_posts=True, x_images=False, image_platforms=())
    assert set(out["post_formats"].values()) == {"text", "video"}
    assert "image_fallback" not in out and "image_footer" not in out
    # a listed platform the package carries no copy for costs nothing either
    out = ss.freeze_post_formats(_image_package(posts=("tiktok", "bluesky")), POSTING_DAY, image_posts=True,
                                 x_images=False, image_platforms=("facebook",))
    assert out["post_formats"] == {"tiktok": "video", "bluesky": "text"} and "image_fallback" not in out


@pytest.mark.parametrize("x_images", [False, True])
def test_x_in_the_image_platforms_is_ignored_x_follows_its_own_switch(x_images):
    out = ss.freeze_post_formats(_image_package(), POSTING_DAY, image_posts=True, x_images=x_images,
                                 image_platforms=("x", "bluesky", "pinterest", None, 3, "BLUESKY"))
    assert out["post_formats"]["x"] == ("image" if x_images else "text")
    assert out["post_formats"]["bluesky"] == "image"
    assert {out["post_formats"][p] for p in ("facebook", "linkedin", "threads")} == {"text"}
    assert "image_fallback" not in out


def test_x_while_its_switch_is_off_is_never_a_fallback():
    out = ss.freeze_post_formats(_image_package(image_post=None) | {"image_post": None}, POSTING_DAY,
                                 image_posts=True, x_images=False)
    assert "x" not in out["image_fallback"]["platforms"] and out["image_fallback"]["platforms"] == _FOUR


@pytest.mark.parametrize("blocked", [None, "worker_capability", "freeze_failed"])
def test_with_image_posts_off_there_is_never_a_fallback(blocked):
    """Image posts off is a text day on purpose: no reason recorded, whatever else is true."""
    for pkg in (_image_package(), _image_package(image_post={"title": "only"}), _template_output()):
        out = ss.freeze_post_formats(copy.deepcopy(pkg), POSTING_DAY, image_posts=False, x_images=True,
                                     image_blocked=blocked)
        assert "image_fallback" not in out and "image" not in out["post_formats"].values()


def test_a_stale_image_fallback_in_the_input_is_never_carried_over():
    stale = {"reason": "worker_capability", "platforms": ["bluesky"]}
    out = ss.freeze_post_formats(_image_package(image_fallback=stale), POSTING_DAY, image_posts=True, x_images=False)
    assert "image_fallback" not in out                          # this freeze's own verdict: none
    out = ss.freeze_post_formats(_image_package(image_post={"title": "only"}, image_fallback=stale), POSTING_DAY,
                                 image_posts=True, x_images=False)
    assert out["image_fallback"] == {"reason": "image_dropped", "platforms": _FOUR}


@pytest.mark.parametrize("bad", ["bluesky", b"bluesky", "bluesky,facebook"])
def test_image_platforms_is_a_collection_never_one_string(bad):
    """A bare string would iterate as characters (no platform at all): refused loudly instead —
    `_frozen_output` then stores the package without formats and records `freeze_failed`."""
    with pytest.raises(ValueError, match="not one string"):
        ss.freeze_post_formats(_image_package(), POSTING_DAY, image_posts=True, x_images=False, image_platforms=bad)


@pytest.mark.parametrize("bad", ["worker", "", "WORKER_CAPABILITY", "image_fallback", 1, True])
@pytest.mark.parametrize("image_posts", [False, True])
def test_an_unknown_image_blocked_code_is_a_value_error(bad, image_posts):
    with pytest.raises(ValueError, match="not an image-fallback reason"):
        ss.freeze_post_formats(_image_package(), POSTING_DAY, image_posts=image_posts, x_images=False,
                               image_blocked=bad)


# ── MARKETING_IMAGE_PLATFORMS: the setting and its reader ─────────────────────────────────────


def test_the_image_platforms_setting_is_declared_with_every_image_platform():
    from app.config import Settings, settings

    assert hasattr(settings, "MARKETING_IMAGE_PLATFORMS")
    default = Settings.model_fields["MARKETING_IMAGE_PLATFORMS"].default
    assert default == _PICTURE_DEFAULT == ",".join(schemas.IMAGE_PLATFORMS)
    assert ss.parse_image_platforms(default) == schemas.IMAGE_PLATFORMS
    assert "x" not in schemas.IMAGE_PLATFORMS


def test_the_config_literal_equals_the_image_platforms():
    """config.py imports no app module, so its validator carries an inline copy of IMAGE_PLATFORMS. Read by
    AST (the literal itself) and by behaviour (every platform survives the validator)."""
    from app.config import Settings

    src = (Path(__file__).resolve().parents[1] / "app" / "config.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_image_platforms_fail_closed")
    literals = [ast.literal_eval(a.value) for a in ast.walk(fn)
                if isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "known" for t in a.targets)]
    assert literals == [schemas.IMAGE_PLATFORMS]
    every = ",".join(schemas.IMAGE_PLATFORMS)
    assert Settings(MARKETING_IMAGE_PLATFORMS=every).MARKETING_IMAGE_PLATFORMS == \
        ",".join(sorted(schemas.IMAGE_PLATFORMS))


@pytest.mark.parametrize("raw, expected, error_words", [
    (" Threads , BLUESKY ", "bluesky,threads", None),
    ("bluesky,bluesky", "bluesky", None),
    ("", "", None),
    ("  , ", "", None),
    ("x,bluesky", "bluesky", "MARKETING_X_IMAGES"),             # X keeps its own switch
    ("X", "", "MARKETING_X_IMAGES"),
    ("pinterest,facebook", "facebook", "unknown"),
    ("tiktok", "", "unknown"),                                  # a video platform posts no picture here
    ("bluesky;facebook", "", "unknown"),                       # one unknown token — never half-parsed
])
def test_the_image_platforms_setting_normalises_and_never_fails_the_deploy(monkeypatch, caplog, raw, expected,
                                                                           error_words):
    from app.config import Settings

    monkeypatch.setenv("MARKETING_IMAGE_PLATFORMS", raw)
    with caplog.at_level(logging.ERROR, logger="app.config"):
        got = Settings().MARKETING_IMAGE_PLATFORMS
    assert got == expected
    errors = [r.getMessage() for r in caplog.records if r.name == "app.config" and r.levelno >= logging.ERROR]
    assert bool(errors) == (error_words is not None), errors
    for m in errors:
        assert "MARKETING_IMAGE_PLATFORMS" in m
    if error_words:
        assert any(error_words in m for m in errors), errors
    assert ss.parse_image_platforms(got) == tuple(p for p in schemas.IMAGE_PLATFORMS if p in got.split(","))


@pytest.mark.parametrize("value", [None, 1, ["bluesky"]])
def test_a_non_string_image_platforms_setting_is_no_platform(caplog, value):
    from app.config import Settings

    with caplog.at_level(logging.ERROR, logger="app.config"):
        assert Settings(MARKETING_IMAGE_PLATFORMS=value).MARKETING_IMAGE_PLATFORMS == ""
    assert any("MARKETING_IMAGE_PLATFORMS" in r.getMessage() for r in caplog.records if r.name == "app.config")


@pytest.mark.parametrize("raw, expected, logs", [
    ("threads,bluesky", ("bluesky", "threads"), False),          # always in IMAGE_PLATFORMS order
    (" LinkedIn ,facebook", ("facebook", "linkedin"), False),
    ("", (), False),
    ("x,threads", ("threads",), True),
    ("x;threads", (), True),
    (None, (), True),
    (5, (), True),
    (["bluesky"], (), True),
])
def test_parse_image_platforms_re_validates_a_raw_value(raw, expected, logs, caplog):
    """A value set on the live settings object skips the Settings validator: the reader re-validates it
    (fail closed to text posts), logging at ERROR, and never raises."""
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        assert ss.parse_image_platforms(raw) == expected
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert bool(errors) == logs
    assert all("MARKETING_IMAGE_PLATFORMS" in r.getMessage() for r in errors)


# ── _frozen_output: the switch read at write time, every reason recorded ─────────────────────


@pytest.mark.asyncio
async def test_the_image_platforms_switch_is_read_at_write_time(world, monkeypatch):
    """Read when the accepted output is written (like MARKETING_X_IMAGES), not when the generation
    starts: a flip during a slow generation lands on THIS write."""
    sb, runs = world
    _image_switches(monkeypatch, images=True, x_images=False)
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_PLATFORMS", _PICTURE_DEFAULT)
    writer = ImageWriter(["accepted"])
    writer.gate = asyncio.Event()
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    for _ in range(500):
        if writer.calls:
            break
        await asyncio.sleep(0.01)
    assert writer.calls and not writer.gate.is_set()
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_PLATFORMS", "threads")
    writer.gate.set()
    await _drain(svc)
    out = _script_row(sb, rid)["output"]
    assert out["post_formats"] == {**_VIDEO_FORMATS, "x": "text", "threads": "image", "bluesky": "text",
                                   "facebook": "text", "linkedin": "text"}
    assert "image_fallback" not in out and out["image_footer"] == post_copy.image_footer(POSTING_DAY)


@pytest.mark.asyncio
async def test_a_raw_image_platforms_value_is_re_validated_at_write_time(world, monkeypatch, caplog):
    sb, runs = world
    _image_switches(monkeypatch, images=True, x_images=False)
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_PLATFORMS", "x, BlueSky ,pinterest")
    svc = ss.MarketingScriptService(runs, writer=ImageWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    out = _script_row(sb, rid)["output"]
    assert out["post_formats"]["bluesky"] == "image" and out["post_formats"]["x"] == "text"
    assert {out["post_formats"][p] for p in ("facebook", "linkedin", "threads")} == {"text"}
    assert "image_fallback" not in out
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("MARKETING_X_IMAGES" in m for m in errors) and any("pinterest" in m for m in errors)


@pytest.mark.asyncio
async def test_a_freeze_that_raises_under_a_holder_that_cannot_draw_records_the_holders_reason(world, monkeypatch):
    """Both true: the freeze raised AND the holder could not draw the picture. The owner hears the
    actionable one (deploy the worker), the one the freeze would have recorded itself."""
    sb, runs = world
    _image_switches(monkeypatch, images=True, x_images=False)

    def broken(*_a, **_k):
        raise RuntimeError("freeze broke")

    monkeypatch.setattr(ss, "freeze_post_formats", broken)
    svc = ss.MarketingScriptService(runs, writer=ImageWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    _run_row(sb, rid)["metadata"].pop("worker_capabilities")
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    out = _script_row(sb, rid)["output"]
    assert "post_formats" not in out and out["image_fallback"] == {"reason": "worker_capability", "platforms": _FOUR}


@pytest.mark.asyncio
async def test_a_freeze_failure_with_no_picture_platform_records_nothing(world, monkeypatch):
    sb, runs = world
    _image_switches(monkeypatch, images=True, x_images=False)
    monkeypatch.setattr(mrs.settings, "MARKETING_IMAGE_PLATFORMS", "")

    def broken(*_a, **_k):
        raise RuntimeError("freeze broke")

    monkeypatch.setattr(ss, "freeze_post_formats", broken)
    svc = ss.MarketingScriptService(runs, writer=ImageWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    out = _script_row(sb, rid)["output"]
    assert "post_formats" not in out and "image_fallback" not in out


@pytest.mark.asyncio
async def test_the_holder_check_names_its_reason(world, monkeypatch):
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    assert await svc._holder_image_block(rid, "g") is None
    _run_row(sb, rid)["metadata"]["worker_capabilities"] = []
    assert await svc._holder_image_block(rid, "g") == "worker_capability"
    _run_row(sb, rid)["metadata"]["worker_capabilities"] = ["post_image"]
    assert await svc._holder_image_block(rid, "g", template=True) == "worker_capability"   # no news_templates
    # review CTA-3: a drop-2a holder draws lesson pictures and 2a layouts, but not a 2b layout — the same
    # code, which is why its owner words name THIS picture (test_the_worker_capability_words_name_…)
    _run_row(sb, rid)["metadata"]["worker_capabilities"] = ["post_image", "news_templates"]
    assert await svc._holder_image_block(rid, "g") is None
    for layout in ("rows", "spotlight", "bars"):                     # the drop-2a layouts
        assert await svc._holder_image_block(rid, "g", template=True, image_layout=layout) is None
    for layout in schemas.WORKER_LAYOUTS_2B:
        assert await svc._holder_image_block(rid, "g", template=True, image_layout=layout) == "worker_capability"
    _run_row(sb, rid)["metadata"]["worker_capabilities"] = ["post_image", "news_templates", "layouts_2b"]
    for layout in schemas.WORKER_LAYOUTS_2B:
        assert await svc._holder_image_block(rid, "g", template=True, image_layout=layout) is None
    assert await svc._holder_image_block(str(uuid.uuid4()), "g") == "freeze_failed"         # no such run

    async def broken(_run_id):
        raise mrs.MarketingRunError("get_run failed: 520")

    monkeypatch.setattr(runs, "get_run", broken)
    assert await svc._holder_image_block(rid, "g") == "freeze_failed"


@pytest.mark.asyncio
async def test_an_old_worker_image_day_stamps_its_reason_on_the_text_posts_end_to_end(world, monkeypatch):
    """kick → accepted under a holder that cannot draw pictures → create_posts: each text post of a
    picture platform carries `image_fallback: worker_capability`; X (its switch off) and the videos
    carry none."""
    sb, runs = world
    _image_switches(monkeypatch, images=True, x_images=False)
    svc = ss.MarketingScriptService(runs, writer=ImageWriter(["accepted"]))
    rid = _run(sb, POSTING_DAY)
    _run_row(sb, rid)["metadata"].pop("worker_capabilities")
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert _script_row(sb, rid)["output"]["image_fallback"] == {"reason": "worker_capability", "platforms": _FOUR}
    video = _run_video(sb, rid)
    posts = await runs.create_posts(rid, [
        {"platform": "bluesky", "format": "text"}, {"platform": "threads", "format": "text"},
        {"platform": "x", "format": "text"}, {"platform": "youtube", "format": "video", "asset_ids": [video]},
    ], claim=_holder_of(runs, rid))
    stamped = {p["platform"]: p["metadata"].get(schemas.POST_METADATA_IMAGE_FALLBACK) for p in posts}
    assert stamped == {"bluesky": "worker_capability", "threads": "worker_capability", "x": None, "youtube": None}


@pytest.mark.parametrize("listed, x_images, expected", [
    (frozenset({"x"}), False, []),                        # X never follows the listed set…
    (frozenset({"x"}), True, ["x"]),                      # …only its own switch
    (frozenset(), True, ["x"]),
    (frozenset({"bluesky", "tiktok"}), False, ["bluesky"]),   # TikTok posts no picture, listed or not
    (frozenset(schemas.IMAGE_PLATFORMS), False, ["bluesky"]),
])
def test_the_picture_platforms_are_listed_image_platforms_and_x_by_its_switch(listed, x_images, expected):
    out = _image_package(posts=("x", "bluesky", "tiktok"))
    assert ss._picture_platforms(out, listed, x_images) == expected


@pytest.mark.parametrize("output", [None, "text", {}, {"posts": None}, {"posts": ["bluesky"]},
                                    {"posts": {1: {}, None: {}, "bluesky": {}}}])
def test_the_picture_platforms_of_a_malformed_output_never_raise(output):
    got = ss._picture_platforms(output, frozenset(schemas.IMAGE_PLATFORMS), True)
    assert got == (["bluesky"] if isinstance(output, dict) and isinstance(output.get("posts"), dict) else [])


def test_only_image_platforms_members_count_even_for_a_future_picture_capable_platform(monkeypatch):
    """`image_platforms` counts only members of schemas.IMAGE_PLATFORMS. Today every picture-capable
    platform but X is one, so this is proven with a format map that gives a platform outside the set an
    image format (as a later outlet might): listing it posts no picture and records no fallback."""
    monkeypatch.setattr(ss, "POST_FORMATS_BY_PLATFORM",
                        {**schemas.POST_FORMATS_BY_PLATFORM, "pinterest": ("text", "image")})
    pkg = _image_package(posts=("pinterest", "bluesky"))
    out = ss.freeze_post_formats(pkg, POSTING_DAY, image_posts=True, x_images=True,
                                 image_platforms=("pinterest", "bluesky"))
    assert out["post_formats"] == {"pinterest": "text", "bluesky": "image"} and "image_fallback" not in out


# ══ Batch 2 (2026-10-19, "first seconds"): the run's frozen VIDEO features ═══════════════════════════
#
# MARKETING_VIDEO_FEATURES is read at WRITE time and frozen into the accepted output as `video_features`:
# sorted(switch ∩ what applies to the script's kind) — a lesson {hook_card, push_in}, a template
# {data_card, push_in} — ONLY when the worker holding the run declared `video_v2` (else none: today's
# video). ONE run read at write time serves both capability decisions (image posts and video). The script
# read-back always carries `video_features` and `end_card_cta` (their presence is the worker's signal).

import json  # noqa: E402

from app.services.marketing import company_news_rules as news_rules  # noqa: E402
from app.services.marketing import news_templates  # noqa: E402

_ALL_FEATURES = "hook_card,data_card,push_in"
#: What the Batch 2 worker declares on its claim (marketing/main.py WORKER_CAPABILITIES).
_B2_CAPS = ["layouts_2b", "news_templates", "post_image", "video_v2"]


def _video_switch(monkeypatch, raw: Any) -> None:
    monkeypatch.setattr(ss.settings, "MARKETING_VIDEO_FEATURES", raw)


def _b2_run(sb, day: date = POSTING_DAY, *, caps: Optional[List[str]] = None, **extra) -> str:
    rid = _run(sb, day, **extra)
    _run_row(sb, rid)["metadata"]["worker_capabilities"] = list(_B2_CAPS if caps is None else caps)
    return rid


def _count_holder_reads(monkeypatch, runs, writer) -> List[str]:
    """Every `get_run` the service makes AFTER the writer was called — i.e. the write-time holder reads."""
    seen: List[str] = []
    real = runs.get_run

    async def counting(run_id):
        if writer.calls:
            seen.append(run_id)
        return await real(run_id)

    monkeypatch.setattr(runs, "get_run", counting)
    return seen


# ── the setting ──────────────────────────────────────────────────────────────────────────────


def test_the_video_features_setting_is_declared_off_by_default():
    from app.config import Settings, settings

    assert hasattr(settings, "MARKETING_VIDEO_FEATURES")
    assert Settings.model_fields["MARKETING_VIDEO_FEATURES"].default == ""
    assert ss.parse_video_features("") == ()


def test_the_config_literal_equals_the_video_features():
    """config.py imports no app module, so its validator carries an inline copy of VIDEO_FEATURES. Read by AST
    (the literal itself) and by behaviour (every feature survives the validator)."""
    from app.config import Settings

    src = (Path(__file__).resolve().parents[1] / "app" / "config.py").read_text(encoding="utf-8")
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_video_features_fail_closed")
    literals = [ast.literal_eval(a.value) for a in ast.walk(fn)
                if isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "known" for t in a.targets)]
    assert literals == [schemas.VIDEO_FEATURES]
    assert Settings(MARKETING_VIDEO_FEATURES=",".join(schemas.VIDEO_FEATURES)).MARKETING_VIDEO_FEATURES == \
        ",".join(sorted(schemas.VIDEO_FEATURES))


@pytest.mark.parametrize("raw, expected, logs_error", [
    (" Push_In , hook_card ", "hook_card,push_in", False),
    ("data_card,data_card", "data_card", False),
    ("", "", False),
    (" , ", "", False),
    ("hook_card,count_up", "hook_card", True),        # the rejected count-up is no feature
    ("hook-card", "", True),
    ("hook_card;push_in", "", True),                  # one unknown token — never half-parsed
])
def test_the_video_features_setting_normalises_and_never_fails_the_deploy(monkeypatch, caplog, raw, expected,
                                                                         logs_error):
    from app.config import Settings

    monkeypatch.setenv("MARKETING_VIDEO_FEATURES", raw)
    with caplog.at_level(logging.ERROR, logger="app.config"):
        got = Settings().MARKETING_VIDEO_FEATURES
    assert got == expected
    errors = [r.getMessage() for r in caplog.records if r.name == "app.config" and r.levelno >= logging.ERROR]
    assert bool(errors) == logs_error and all("MARKETING_VIDEO_FEATURES" in m for m in errors)
    assert ss.parse_video_features(got) == tuple(f for f in schemas.VIDEO_FEATURES if f in got.split(","))


@pytest.mark.parametrize("value", [None, 1, ["hook_card"]])
def test_a_non_string_video_features_setting_is_no_feature(caplog, value):
    from app.config import Settings

    with caplog.at_level(logging.ERROR, logger="app.config"):
        assert Settings(MARKETING_VIDEO_FEATURES=value).MARKETING_VIDEO_FEATURES == ""
    assert any("MARKETING_VIDEO_FEATURES" in r.getMessage() for r in caplog.records if r.name == "app.config")


@pytest.mark.parametrize("raw, expected, logs", [
    ("push_in,HOOK_CARD", ("hook_card", "push_in"), False),          # VIDEO_FEATURES order, whatever the input
    (_ALL_FEATURES, schemas.VIDEO_FEATURES, False),
    ("", (), False),
    ("count_up", (), True),
    (None, (), True),
    (3, (), True),
    (["hook_card"], (), True),
])
def test_parse_video_features_re_validates_a_raw_value(raw, expected, logs, caplog):
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        assert ss.parse_video_features(raw) == expected
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert bool(errors) == logs and all("MARKETING_VIDEO_FEATURES" in r.getMessage() for r in errors)


# ── the pure freeze ──────────────────────────────────────────────────────────────────────────


_LESSON_OUT = {"hook": "A calm idea.", "posts": {"tiktok": {"caption": "c"}}}


@pytest.mark.parametrize("switch, holder_v2, expected", [
    ((), True, []),
    (schemas.VIDEO_FEATURES, False, []),                      # an older worker holds the run: today's video
    (schemas.VIDEO_FEATURES, True, ["hook_card", "push_in"]),
    (("push_in",), True, ["push_in"]),
    (("hook_card",), True, ["hook_card"]),
    (("data_card",), True, []),                               # a template feature on a lesson
    (("data_card", "count_up", 7, None), True, []),           # unknown entries are ignored
])
def test_freeze_video_features_on_a_lesson(switch, holder_v2, expected):
    assert ss.freeze_video_features(dict(_LESSON_OUT), switch=switch, holder_v2=holder_v2, run_id="r") == expected


@pytest.mark.parametrize("hook", [None, "", "  ", " padded", "two\nlines", "x" * 601, "see https://x.example"])
def test_a_hook_that_cannot_be_one_drawable_string_freezes_no_hook_card(hook, caplog):
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        got = ss.freeze_video_features({"hook": hook}, switch=schemas.VIDEO_FEATURES, holder_v2=True, run_id="r-1")
    assert got == ["push_in"]
    assert any("hook_card" in r.getMessage() and "r-1" in r.getMessage() for r in caplog.records)


def test_a_string_switch_is_a_value_error():
    for bad in ("hook_card", b"push_in"):
        with pytest.raises(ValueError, match="collection"):
            ss.freeze_video_features(dict(_LESSON_OUT), switch=bad, holder_v2=True)


# ── a lesson day, through the kick ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("caps, expected", [
    (_B2_CAPS, ["hook_card", "push_in"]),
    (["post_image", "news_templates", "layouts_2b"], None),        # a drop-2b worker: no video_v2
    ([], None),
])
@pytest.mark.asyncio
async def test_a_lesson_freezes_its_video_features_only_for_a_video_v2_holder(world, monkeypatch, caplog, caps,
                                                                              expected):
    sb, runs = world
    _video_switch(monkeypatch, _ALL_FEATURES)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _b2_run(sb, caps=caps, worker_version="drop2b-image")
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    out = _script_row(sb, rid)["output"]
    assert out.get("video_features") == expected
    script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert script["video_features"] == (expected or [])
    accepted = [r.getMessage() for r in caplog.records if r.getMessage().startswith("marketing script ACCEPTED")]
    assert len(accepted) == 1 and f"video_features={expected or []}" in accepted[0]
    warned = [r.getMessage() for r in caplog.records if "did not declare 'video_v2'" in r.getMessage()]
    if expected is None:
        assert len(warned) == 1 and rid in warned[0] and "'drop2b-image'" in warned[0] and "hook_card" in warned[0]
    else:
        assert not warned


@pytest.mark.parametrize("raw", ["", "data_card"], ids=["switch_off", "template_only_feature"])
@pytest.mark.asyncio
async def test_no_applicable_feature_means_no_holder_read_and_no_key(world, monkeypatch, raw):
    """Byte-identical to before Batch 2: with image posts off and no feature that applies to the day's kind,
    the accepted write reads no run and stores no `video_features` key."""
    sb, runs = world
    _video_switch(monkeypatch, raw)
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _b2_run(sb)
    reads = _count_holder_reads(monkeypatch, runs, writer)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert reads == []
    assert "video_features" not in _script_row(sb, rid)["output"]


@pytest.mark.parametrize("images, features", [(True, ""), (False, "push_in"), (True, _ALL_FEATURES)])
@pytest.mark.asyncio
async def test_one_holder_read_serves_every_capability_decision(world, monkeypatch, images, features):
    """The generation's worst case counts ONE write-time run read (_HOLDER_STATEMENTS): image posts and video
    features share it — and both decisions still come out of it."""
    sb, runs = world
    _image_switches(monkeypatch, images=images)
    _video_switch(monkeypatch, features)
    writer = ImageWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _b2_run(sb)
    reads = _count_holder_reads(monkeypatch, runs, writer)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert reads == [rid] and ss._HOLDER_STATEMENTS == 1
    out = _script_row(sb, rid)["output"]
    assert (out["post_formats"]["bluesky"] == "image") is images
    want = sorted(set(features.split(",")) & {"hook_card", "push_in"}) if features else None
    assert out.get("video_features") == want


@pytest.mark.asyncio
async def test_a_holder_read_that_fails_freezes_no_feature_and_the_picture_says_why(world, monkeypatch, caplog):
    sb, runs = world
    _image_switches(monkeypatch, images=True)
    _video_switch(monkeypatch, _ALL_FEATURES)
    writer = ImageWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _b2_run(sb)
    real = runs.get_run

    async def broken_after_the_model_call(run_id):
        if writer.calls:
            raise mrs.MarketingRunError("get_run failed: 520")
        return await real(run_id)

    monkeypatch.setattr(runs, "get_run", broken_after_the_model_call)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    row = _script_row(sb, rid)
    assert row["status"] == "accepted" and "video_features" not in row["output"]
    assert row["output"]["image_fallback"]["reason"] == "freeze_failed"           # Batch 1's words, unchanged
    msgs = [r.getMessage() for r in caplog.records]
    assert any("render the video features" in m and "no video feature this run" in m and rid in m for m in msgs)
    assert any("render the post image" in m and "frozen as text" in m for m in msgs)


@pytest.mark.asyncio
async def test_the_video_switch_is_read_at_write_time_and_then_frozen(world, monkeypatch):
    """Read when the accepted output is written (like MARKETING_IMAGE_PLATFORMS): a flip during a slow
    generation lands on THIS write — and once written, a later flip changes nothing the worker reads."""
    sb, runs = world
    _video_switch(monkeypatch, "")
    writer = FakeWriter(["accepted"])
    writer.gate = asyncio.Event()
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _b2_run(sb)
    await svc.kick(rid, claim=_holder(svc, rid))
    for _ in range(500):
        if writer.calls:
            break
        await asyncio.sleep(0.01)
    assert writer.calls and not writer.gate.is_set()
    _video_switch(monkeypatch, "hook_card")
    writer.gate.set()
    await _drain(svc)
    assert _script_row(sb, rid)["output"]["video_features"] == ["hook_card"]
    _video_switch(monkeypatch, "push_in")
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["script"]["video_features"] == ["hook_card"]
    _run_row(sb, rid)["metadata"]["worker_capabilities"] = ["post_image"]      # a re-claim by an older image
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["script"]["video_features"] == ["hook_card"]


@pytest.mark.asyncio
async def test_a_video_features_key_in_the_package_is_never_carried_over(world, monkeypatch):
    sb, runs = world
    _video_switch(monkeypatch, "")

    class Planting(FakeWriter):
        async def __call__(self, *a, **k):
            result = await super().__call__(*a, **k)
            result.package["video_features"] = ["hook_card", "push_in"]
            return result

    svc = ss.MarketingScriptService(runs, writer=Planting(["accepted"]))
    rid = _b2_run(sb)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert "video_features" not in _script_row(sb, rid)["output"]
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["script"]["video_features"] == []


@pytest.mark.asyncio
async def test_a_failed_formats_freeze_still_freezes_the_video_features(world, monkeypatch, caplog):
    sb, runs = world
    _image_switches(monkeypatch, images=True)
    _video_switch(monkeypatch, _ALL_FEATURES)

    def broken(*_a, **_k):
        raise RuntimeError("freeze broke")

    monkeypatch.setattr(ss, "freeze_post_formats", broken)
    svc = ss.MarketingScriptService(runs, writer=ImageWriter(["accepted"]))
    rid = _b2_run(sb)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    out = _script_row(sb, rid)["output"]
    assert "post_formats" not in out and out["video_features"] == ["hook_card", "push_in"]


@pytest.mark.asyncio
async def test_a_video_feature_freeze_that_raises_never_costs_the_paid_package(world, monkeypatch, caplog):
    sb, runs = world
    _video_switch(monkeypatch, _ALL_FEATURES)

    def broken(*_a, **_k):
        raise RuntimeError("feature freeze broke")

    monkeypatch.setattr(ss, "freeze_video_features", broken)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _b2_run(sb)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    row = _script_row(sb, rid)
    assert row["status"] == "accepted" and row["generations"] == 1 and "video_features" not in row["output"]
    assert any(r.levelno == logging.ERROR and "could not freeze the video features" in r.getMessage()
               and rid in r.getMessage() for r in caplog.records)


# ── the read-back ────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("state, cta", [("live", "Search Caydex on the App Store"),
                                        ("preorder", "Search Caydex on the App Store"),
                                        ("prelaunch", "caydexinvest.com"), (None, "caydexinvest.com"),
                                        ("LIVE", "caydexinvest.com"), ("<absent>", "caydexinvest.com")])
@pytest.mark.asyncio
async def test_an_output_accepted_before_batch_2_reads_back_as_no_feature_and_its_stores_end_card(world, state,
                                                                                                 cta):
    """An output without `video_features` = [] (today's video); the end card follows the store state the
    output's captions were worded with (owner decision 6), exactly like the first comment."""
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _b2_run(sb)
    output = _package(KEY)
    if state != "<absent>":
        output["store_state"] = state
    _seed(sb, rid, status="accepted", output=output, generation_id=str(uuid.uuid4()))
    script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert script["video_features"] == [] and script["end_card_cta"] == cta
    assert cta in schemas.VIDEO_BRAND_TEXT
    assert ss.end_card_cta({"store_state": state}) == cta


@pytest.mark.parametrize("bad", [["hook_card", "hook_card"], ["data_card"], "push_in", ["count_up"]])
@pytest.mark.asyncio
async def test_frozen_video_features_that_do_not_read_back_reach_the_worker_as_none(world, caplog, bad):
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _b2_run(sb)
    _seed(sb, rid, status="accepted", output={**_package(KEY), "video_features": bad},
          generation_id=str(uuid.uuid4()))
    with caplog.at_level(logging.ERROR, logger=mrs.logger.name):
        script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert script["video_features"] == []
    assert any("do not read back" in r.getMessage() and rid in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_frozen_hook_card_without_a_drawable_hook_is_not_sent(world, caplog):
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _b2_run(sb)
    _seed(sb, rid, status="accepted", output={**_package(KEY), "hook": "two\nlines",
                                              "video_features": ["hook_card", "push_in"]},
          generation_id=str(uuid.uuid4()))
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert script["video_features"] == ["push_in"]
    assert any("hook_card is frozen but the accepted hook" in r.getMessage() and rid in r.getMessage()
               for r in caplog.records)


def test_the_kick_route_serializes_the_video_fields(client, monkeypatch):
    import app.api.v1.endpoints.marketing_internal as mi

    script = {"hook": "h", "video_script": ["l"], "cards": [], "carousel_slides": [], "disclaimer_card": "d",
              "outlets": ["tiktok"], "video_features": ["hook_card", "push_in"],
              "end_card_cta": "Search Caydex on the App Store", "data_card_slot": 2}

    class Svc:
        def __init__(self, s):
            self.s = s

        async def kick(self, run_id, *, claim):
            return {"status": "accepted", "source_ref": KEY, "template_id": "checklist", "script": self.s}

    monkeypatch.setattr(mi, "get_marketing_script_service", lambda: Svc(script))
    body = client.post(f"{_BASE}/runs/r1/script", headers=_H).json()
    assert body["script"]["video_features"] == ["hook_card", "push_in"]
    assert body["script"]["end_card_cta"] == "Search Caydex on the App Store"
    assert body["script"]["data_card_slot"] == 2
    # the real service's read-back always sets them; even a service shape without them serialises the KEYS
    legacy = {k: v for k, v in script.items() if k not in ("video_features", "end_card_cta", "data_card_slot")}
    monkeypatch.setattr(mi, "get_marketing_script_service", lambda: Svc(legacy))
    body = client.post(f"{_BASE}/runs/r1/script", headers=_H).json()
    assert body["script"]["video_features"] == [] and body["script"]["end_card_cta"] is None
    assert "data_card_slot" in body["script"] and body["script"]["data_card_slot"] is None


# ── a TEMPLATE day end to end: freeze → read-back → the server's on-screen check ─────────────


_B2_DAY = date(2026, 10, 26)      # a Monday outside the members-elect window


def _b2_ceo_week() -> news_rules.InsiderBuysWeek:
    start, end = news_rules.insider_window(_B2_DAY)

    def buy(sym, name, amount, shares, filed, holding="direct"):
        return news_rules.InsiderPurchase(
            company=news_rules.CompanyRef(symbol=sym, name=name), role="ceo", person_name=None, amount_usd=amount,
            shares=shares, purchases=1, earliest_trade_date=filed, latest_trade_date=filed, filing_dates=(filed,),
            holding=holding, amended=False)

    return news_rules.InsiderBuysWeek(series="ceo_buys", window_start=start, window_end=end, rows=(
        buy("LOW", "Lowe's", 2_300_000.0, 9_812.0, end - timedelta(days=3), holding="indirect"),
        buy("SBUX", "Starbucks", 410_000.0, 4_850.0, end - timedelta(days=2)),
        # owner decisions 2026-11-20 ("3 - 5 companies at once"): a Form 4 week holds three companies at least
        buy("NKE", "Nike", 250_000.0, 3_500.0, end - timedelta(days=1))))


def _b2_composed() -> Dict[str, Any]:
    out = news_templates.compose(_b2_ceo_week(), run_date=_B2_DAY, store_state="live", allow_x_url=False)
    out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None,
                     "width": None, "height": None} for r in news_templates.logo_refs(out)]
    return out


def _b2_sheet() -> Dict[str, Any]:
    rec = _b2_ceo_week()
    return json.loads(json.dumps(news_rules.fact_sheet(rec, rejections={}, selection={
        "plan": "monday", "chain": ["ceo_buys", "lesson"], "trail": [{"series": "ceo_buys", "outcome": "chosen"}]})))


@pytest.mark.parametrize("caps, images, expected", [
    (_B2_CAPS, True, ["data_card", "push_in"]),
    (_B2_CAPS, False, ["data_card", "push_in"]),            # a data card does not need image POSTS on
    (["post_image", "video_v2"], True, ["push_in"]),         # video_v2, but it cannot draw a template picture
    (["layouts_2b", "news_templates", "post_image"], True, None),
], ids=["b2", "b2_image_posts_off", "no_news_templates", "drop2b"])
@pytest.mark.asyncio
async def test_a_template_freezes_its_data_card_and_the_whole_chain_agrees(world, monkeypatch, caps, images,
                                                                           expected):
    sb, runs = world
    monkeypatch.setattr(ss, "_today_et", lambda: _B2_DAY)
    _image_switches(monkeypatch, images=images)
    _video_switch(monkeypatch, _ALL_FEATURES)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _b2_run(sb, _B2_DAY, caps=caps)
    stored = json.loads(json.dumps(await svc._frozen_output(_b2_composed(), _B2_DAY, run_id=rid, gen_id="template:ceo_buys",
                                                            image_posts=images)))
    assert stored.get("video_features") == expected
    # the new key never breaks the template's own re-check (create_posts runs it again)
    assert news_templates.revalidate(stored, fact_sheet=_b2_sheet(), run_date=_B2_DAY) == []
    sb.tables[mrs.SCRIPTS].rows.append({"run_id": rid, "run_date": _B2_DAY.isoformat(), "status": "accepted",
                                        "source_ref": news_rules.ledger_key(_b2_ceo_week()), "template_id": "ceo_buys",
                                        "output": stored, "fact_sheet": _b2_sheet(), "generation_id": "g",
                                        "generations": 0, "tokens_used": 0, "violations": []})
    script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert script["video_features"] == (expected or [])
    # news-v23: a holder without look_v23 (these claims predate it) is sent the picture without the ticker chips its
    # walker would refuse — the rest of the spec exactly as stored
    sent_spec = (stored["image_spec"] if "look_v23" in caps
                 else ss._without_look_chips(stored["image_spec"], rid))
    assert sent_spec != stored["image_spec"]                              # the roundup's rows carry chips
    assert script["image_spec"] == sent_spec and script["image_footer"] == stored["image_footer"]
    assert script["end_card_cta"] == "Search Caydex on the App Store"
    # CARDS-B2-1: the data card replaces "The purchase" (card 2 of a Form 4 roundup) — never the last card,
    # "Same week", whose "At least one more …" is the roundup's own hedge
    with_data = bool(expected and "data_card" in expected)
    assert script["data_card_slot"] == (1 if with_data else None)
    if with_data:
        assert [c["title"] for c in script["cards"]] == ["Who filed", "The purchase", "Held indirectly",
                                                         "Same week"]
    # what a Batch 2 worker draws: the opening card, every text card but the one the slot names, the data card
    # (the post image), the disclaimer, the new end card
    voice = str(uuid.uuid4())
    sb.tables[mrs.ASSETS].rows.append({"id": voice, "run_id": rid, "kind": "audio", "status": "ready",
                                       "storage_path": f"v-{voice}", "metadata": {"words": [{"w": "x", "s": 0, "e": 1}]}})
    texts = [c[k] for i, c in enumerate(stored["cards"]) if i != script["data_card_slot"] for k in ("title", "body")]
    data = template_onscreen.image_strings(stored["image_spec"], stored["logos"])
    drawn = [*template_onscreen.opening_strings(stored["opening_card"], stored["logos"]), *texts, *data,
             stored["disclaimer_card"], "Search Caydex on the App Store"]
    meta = {"onscreen_text": list(dict.fromkeys(drawn)), "voice_asset_id": voice}
    run = _run_row(sb, rid)
    if expected and "data_card" in expected:
        await runs._check_onscreen_text(run, meta)
    else:
        with pytest.raises(mrs.MarketingRequestInvalid, match="not the accepted script"):
            await runs._check_onscreen_text(run, meta)
    # the alt text is never a data card's (its title may BE the picture's title; a describing paragraph is not)
    alt_only = next(p for p in stored["image_post"]["paragraphs"] if p not in meta["onscreen_text"])
    with pytest.raises(mrs.MarketingRequestInvalid, match="not the accepted script"):
        await runs._check_onscreen_text(run, {**meta, "onscreen_text": [*meta["onscreen_text"], alt_only]})


def test_freeze_video_features_keeps_each_kind_to_its_own_features():
    """The KIND decides, not what happens to be drawable: a template's hook is a drawable string, yet a
    template never gets a hook card (it opens on its code-owned opening card); a lesson that carries a
    drawable template-shaped picture never gets a data card."""
    template = _b2_composed()
    template["post_formats"] = {"tiktok": "video", "x": "text"}
    assert mrs.hook_card_text(template) is not None and mrs.data_card_strings(template) is not None
    assert ss.freeze_video_features(template, switch=schemas.VIDEO_FEATURES, holder_v2=True) == ["data_card", "push_in"]
    assert ss.freeze_video_features(template, switch=("hook_card",), holder_v2=True) == []
    lesson = {k: v for k, v in template.items() if k not in ("authorship",)}
    assert mrs.data_card_strings(lesson) is not None
    assert ss.freeze_video_features(lesson, switch=schemas.VIDEO_FEATURES, holder_v2=True) == ["hook_card", "push_in"]
    assert ss.freeze_video_features(lesson, switch=("data_card",), holder_v2=True) == []


@pytest.mark.parametrize("tamper", [
    lambda o: o["image_spec"].__setitem__("layout", "table"),
    lambda o: o["image_spec"].__setitem__("footer", "Another footer · Caydex"),
    lambda o: o.__setitem__("logos", o["logos"][1:]),               # the spec names a logo the run lacks
    lambda o: o.pop("post_formats"),                                 # the read-back would send no footer
], ids=["bad_layout", "footer_mismatch", "missing_logo", "no_formats"])
def test_a_template_whose_picture_cannot_be_drawn_freezes_no_data_card(tamper, caplog):
    out = _b2_composed()
    out["post_formats"] = {"tiktok": "video", "x": "text"}
    assert ss.freeze_video_features(copy.deepcopy(out), switch=schemas.VIDEO_FEATURES, holder_v2=True) == [
        "data_card", "push_in"]
    tamper(out)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        got = ss.freeze_video_features(out, switch=schemas.VIDEO_FEATURES, holder_v2=True, run_id="r-9")
    assert got == ["push_in"]
    assert any("data_card" in r.getMessage() and "r-9" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("caps, layout, expected", [
    (["video_v2"], "rows", ("push_in",)),                                   # no news_templates: no picture
    (["video_v2", "news_templates"], "rows", ("data_card", "push_in")),
    (["video_v2", "news_templates"], "spotlight", ("data_card", "push_in")),
    (["video_v2", "news_templates"], "pair", ("push_in",)),                 # a 2b layout needs layouts_2b
    (["video_v2", "news_templates", "layouts_2b"], "grid", ("data_card", "push_in")),
    (["news_templates", "layouts_2b", "post_image"], "rows", ()),           # no video_v2: nothing
])
def test_the_holder_must_be_able_to_draw_each_feature(world, caplog, caps, layout, expected):
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    run = {"id": "r-7", "worker_version": "w-1", "attempts": 2, "metadata": {"worker_capabilities": caps}}
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        got = svc._holder_video_features("r-7", "g", (run, None), ("data_card", "push_in"), image_layout=layout)
    assert got == expected
    warned = [r.getMessage() for r in caplog.records]
    assert bool(warned) == (got != ("data_card", "push_in"))
    assert all("r-7" in m and "'w-1'" in m for m in warned)


def test_an_unreadable_or_missing_holder_draws_no_feature(world, caplog):
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        assert svc._holder_video_features("r-8", "g", (None, mrs.MarketingRunError("get_run failed: 520")),
                                          ("push_in",)) == ()
        assert svc._holder_video_features("r-8", "g", (None, None), ("push_in",)) == ()
        assert svc._holder_video_features("r-8", "g", None, ("push_in",)) == ()
    msgs = [r.getMessage() for r in caplog.records]
    assert any("could not read run_id=r-8" in m and "520" in m for m in msgs)
    assert any("r-8" in m and "not found" in m for m in msgs)


@pytest.mark.asyncio
async def test_a_data_card_the_read_back_cannot_carry_is_not_sent(world, caplog):
    """The worker's copy of the logos keeps only entries it can use (an https url…): when that leaves the
    spec unsent, the frozen data card is not sent either (ERROR) — the worker keeps its last text card."""
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _b2_run(sb, _B2_DAY)
    out = _b2_composed()
    out["post_formats"] = {"tiktok": "video", "x": "text"}
    out["video_features"] = ["data_card", "push_in"]
    out["logos"][0]["url"] = "http://not-https.example/logo.png"        # the server's check reads key + name only
    assert mrs.data_card_strings(out) is not None
    sb.tables[mrs.SCRIPTS].rows.append({"run_id": rid, "run_date": _B2_DAY.isoformat(), "status": "accepted",
                                        "template_id": "ceo_buys", "output": out, "generation_id": "g"})
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        script = ss.worker_script(out, run_id=rid)
    assert script["image_spec"] is None and script["video_features"] == ["push_in"]
    assert any("data_card is frozen but this read-back cannot carry" in r.getMessage() and rid in r.getMessage()
               for r in caplog.records)


# ══ Batch 2 fix round (2026-10-20) ══════════════════════════════════════════════════════════════════
#
# B2-COMPL-1: a hook whose compliance leans on its first narration line (a debunk the writer read as the
# next sentence) is never frozen as a hook card — the card shows it alone — while the push-in stays.
# CARDS-B2-1: the read-back names the ONE card the data card replaces (`data_card_slot`), exactly when it
# sends data_card; a template with no card a data card may replace freezes (and sends) no data card.

#: (hook, the first narration line that debunks it): each passes the writer's chained scan, none alone.
_LEANING = [("Index funds are risk-free.", "Not always. Every fund can lose value."),
            ("Diversified portfolios can't lose.", "That's a myth, and every crash has shown why."),
            ("The market always recovers.", "Not always. Some markets took decades to come back.")]


@pytest.mark.parametrize("hook, line0", _LEANING, ids=[h for h, _l in _LEANING])
def test_a_hook_that_leans_on_its_first_line_freezes_no_hook_card_and_the_push_in_stays(hook, line0, caplog):
    output = {"hook": hook, "video_script": [line0, "A second line of the script."]}
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        got = ss.freeze_video_features(output, switch=schemas.VIDEO_FEATURES, holder_v2=True, run_id="r-lean")
    assert got == ["push_in"]
    (msg,) = [r.getMessage() for r in caplog.records if "hook_card" in r.getMessage()]
    assert "r-lean" in msg and "passes the compliance checks only beside its first narration line" in msg
    assert "shows the hook alone" in msg and hook not in msg
    # a hook that stands alone keeps its card beside the very same first line
    assert ss.freeze_video_features({"hook": "Why do small habits beat big plans?", "video_script": [line0]},
                                    switch=schemas.VIDEO_FEATURES, holder_v2=True) == ["hook_card", "push_in"]


@pytest.mark.asyncio
async def test_a_leaning_hook_from_the_writer_is_accepted_without_a_hook_card(world, monkeypatch, caplog):
    """End to end through the kick: the writer's accepted package keeps its hook (the chained scan passed it),
    but the frozen features hold no hook card — and the read-back sends none."""
    sb, runs = world
    _video_switch(monkeypatch, _ALL_FEATURES)
    hook, line0 = _LEANING[0]

    class Leaning(FakeWriter):
        async def __call__(self, *a, **k):
            result = await super().__call__(*a, **k)
            result.package["hook"] = hook
            result.package["video_script"] = [line0, "Line two."]
            return result

    svc = ss.MarketingScriptService(runs, writer=Leaning(["accepted"]))
    rid = _b2_run(sb)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    out = _script_row(sb, rid)["output"]
    assert out["hook"] == hook and out["video_features"] == ["push_in"]
    script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert script["hook"] == hook and script["video_features"] == ["push_in"]
    assert any("passes the compliance checks only beside its first narration line" in r.getMessage()
               and rid in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("hook, line0", _LEANING[:2], ids=[h for h, _l in _LEANING[:2]])
@pytest.mark.asyncio
async def test_a_frozen_hook_card_whose_hook_leans_on_its_first_line_is_not_sent(world, caplog, hook, line0):
    """A row frozen before the check (or hand-edited): the read-back drops the hook card (ERROR, naming why) and
    keeps the push-in."""
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _b2_run(sb)
    _seed(sb, rid, status="accepted", output={**_package(KEY), "hook": hook, "video_script": [line0, "Two."],
                                              "video_features": ["hook_card", "push_in"]},
          generation_id=str(uuid.uuid4()))
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert script["video_features"] == ["push_in"] and script["data_card_slot"] is None
    assert any("hook_card is frozen but the accepted hook passes the compliance checks only beside its first "
               "narration line" in r.getMessage() and rid in r.getMessage() for r in caplog.records)


# ── the data card's slot in the read-back ────────────────────────────────────────────────────


def _seed_template(sb, rid, out) -> None:
    sb.tables[mrs.SCRIPTS].rows.append({"run_id": rid, "run_date": _B2_DAY.isoformat(), "status": "accepted",
                                        "source_ref": news_rules.ledger_key(_b2_ceo_week()), "template_id": "ceo_buys",
                                        "output": out, "fact_sheet": _b2_sheet(), "generation_id": "g",
                                        "generations": 0, "tokens_used": 0, "violations": []})


@pytest.mark.parametrize("features, sent, slot", [
    (["data_card", "push_in"], ["data_card", "push_in"], 1),
    (["data_card"], ["data_card"], 1),
    (["push_in"], ["push_in"], None),
    (None, [], None),                       # an output accepted before Batch 2: today's video, no slot
], ids=["data_and_push_in", "data_only", "push_in_only", "pre_batch_2"])
@pytest.mark.asyncio
async def test_the_read_back_names_the_data_cards_slot_exactly_when_it_sends_data_card(world, features, sent, slot):
    """Computed from the stored cards at every read-back, never stored: an output frozen before this change
    (data_card in its features, no slot anywhere) reads back the same slot as a new one."""
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _b2_run(sb, _B2_DAY)
    out = _b2_composed()
    out["post_formats"] = {"tiktok": "video", "x": "text"}
    if features is not None:
        out["video_features"] = features
    assert "data_card_slot" not in out
    _seed_template(sb, rid, json.loads(json.dumps(out)))
    script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert script["video_features"] == sent and script["data_card_slot"] == slot
    assert ("data_card" in script["video_features"]) == (script["data_card_slot"] is not None)
    if slot is not None:
        assert script["cards"][slot]["title"] == "The purchase"
        assert type(script["data_card_slot"]) is int
    wire = json.loads(schemas.WorkerScript(**script).model_dump_json())
    assert wire["data_card_slot"] == slot


@pytest.mark.parametrize("tamper", [
    lambda o: o["cards"][1].__setitem__("title", "How much"),                  # no card of the series' titles
    lambda o: o.__setitem__("series", "lesson"),                               # a series the table does not name
    lambda o: o["cards"][1].pop("body"),                                       # not a text card
], ids=["no_slot_title", "unknown_series", "malformed_card"])
@pytest.mark.asyncio
async def test_a_frozen_data_card_with_no_card_to_replace_is_not_sent(world, caplog, tamper):
    """A hand-edited row (the freeze never stores one): the worker is not sent data_card (ERROR) — it keeps
    today's text cards — and the slot stays None; the push-in still goes."""
    sb, runs = world
    rid = _b2_run(sb, _B2_DAY)
    out = _b2_composed()
    out["post_formats"] = {"tiktok": "video", "x": "text"}
    out["video_features"] = ["data_card", "push_in"]
    tamper(out)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        script = ss.worker_script(out, run_id=rid)
    assert script["video_features"] == ["push_in"] and script["data_card_slot"] is None
    assert any("none of the read-back's cards is one a data card may replace" in r.getMessage()
               and rid in r.getMessage() for r in caplog.records)
    assert not any("cannot carry the post image" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("tamper", [
    lambda o: o["cards"][1].__setitem__("title", "How much"),
    lambda o: o.__setitem__("cards", [dict(c) for c in o["cards"] if c["title"] != "The purchase"]),
], ids=["no_slot_title", "slot_card_removed"])
def test_a_template_with_no_card_a_data_card_may_replace_freezes_no_data_card(tamper, caplog):
    out = _b2_composed()
    out["post_formats"] = {"tiktok": "video", "x": "text"}
    assert ss.freeze_video_features(copy.deepcopy(out), switch=schemas.VIDEO_FEATURES, holder_v2=True) == [
        "data_card", "push_in"]
    tamper(out)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        got = ss.freeze_video_features(out, switch=schemas.VIDEO_FEATURES, holder_v2=True, run_id="r-slot")
    assert got == ["push_in"]
    assert any("none of the template's cards is one a data card may replace" in r.getMessage()
               and "r-slot" in r.getMessage() and "ceo_buys" in r.getMessage() for r in caplog.records)


def test_a_lesson_read_back_never_names_a_slot():
    lesson = {**_package(KEY), "video_features": ["hook_card", "push_in"], "series": "ceo_buys",
              "cards": [{"title": "The purchase", "body": "1 purchase · 1 share · $1"}]}
    script = ss.worker_script(lesson, run_id="r-lesson")
    assert script["video_features"] == ["hook_card", "push_in"] and script["data_card_slot"] is None


# ══ Batch 3 (2026-10-20): the EXTRA video posts, frozen at write time ═══════════════════════════════════════
#
# Owner decision 2 (2026-10-15): Facebook / LinkedIn (and Threads behind its token) also post the day's video. The
# web switch MARKETING_VIDEO_EXTRA_PLATFORMS is read ONCE, when the accepted output is written (`_frozen_output`),
# and the extras are frozen into it as `extra_post_formats` + `video_posts` — only on a day whose frozen PRIMARY
# formats already hold a video, each caption the platform's own accepted caption under the narrated-video
# disclaimer. With the switch empty (the default) the stored output is byte for byte what it was; the worker's
# read-back never carries either key.

_B3_BODIES = {
    "tiktok": "Three habits that quietly compound over a decade.",
    "youtube_title": "Three habits that compound",
    "youtube_description": "A short lesson on habits that compound over time.",
    "instagram": "Three habits that quietly compound.",
    "facebook": "Three habits that quietly compound over a decade.",
    "x": "Three habits that quietly compound.",
    "threads": "Three habits that quietly compound.",
    "bluesky": "Three habits that quietly compound.",
    "linkedin": "Three habits that quietly compound over a decade.",
}
_EXTRA_ALL = "facebook,linkedin,threads"


def _b3_package(run_date: date = POSTING_DAY, *, posts=_ALL_POSTS, bodies: Optional[Dict[str, str]] = None,
                store_state: str = "live") -> Dict[str, Any]:
    """A lesson package whose captions are REAL compositions (`post_copy.compose`) — what the writer stores, and
    the only captions an extra video caption re-derives from — with copy for `posts` and an image post."""
    pkg = _image_package(posts=posts)
    texts = dict(_B3_BODIES, **(bodies or {}))
    pkg["posts"] = {p: post_copy.compose(p, texts, category="blueprints", run_date=run_date,
                                         store_state=store_state).as_dict() for p in posts}
    pkg["store_state"] = store_state
    return pkg


def _b3_frozen(pkg: Dict[str, Any], *, images: bool = True) -> Dict[str, Any]:
    return ss.freeze_post_formats(pkg, POSTING_DAY, image_posts=images, x_images=False, run_id="r")


def _extra_switch(monkeypatch, raw: Any) -> None:
    monkeypatch.setattr(ss.settings, "MARKETING_VIDEO_EXTRA_PLATFORMS", raw)


class ExtraWriter(FakeWriter):
    """FakeWriter whose accepted package is `_b3_package` for the generation's run date (real captions)."""

    async def __call__(self, item, template, run_date, **k):
        result = await super().__call__(item, template, run_date, **k)
        if result.package is not None:
            judge = result.package.get("judge")
            result.package.clear()
            result.package.update(_b3_package(run_date))
            if judge is not None:
                result.package["judge"] = judge
        return result


# ── the setting, re-validated at call time ───────────────────────────────────────────────────


@pytest.mark.parametrize("raw, expected, error_words", [
    (" LinkedIn , facebook ", ("facebook", "linkedin"), None),
    ("threads,linkedin,facebook", ("facebook", "linkedin", "threads"), None),     # EXTRA_VIDEO_PLATFORMS order
    ("threads,threads", ("threads",), None),
    ("", (), None),
    (" , ", (), None),
    ("x,facebook", ("facebook",), "two formats"),
    ("X", (), "two formats"),
    ("bluesky", (), "deferred to the week-4 review"),
    ("BlueSky,linkedin", ("linkedin",), "deferred to the week-4 review"),
    ("tiktok", (), "unknown"),
    ("instagram,youtube", (), "unknown"),          # their PRIMARY post already is the video
    ("facebook;linkedin", (), "unknown"),          # one unknown token — never half-parsed
    (None, (), "not a string"),
    (7, (), "not a string"),
    (["facebook"], (), "not a string"),
])
def test_batch3_parse_video_extra_platforms_re_validates_a_raw_value(raw, expected, error_words, caplog):
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        assert ss.parse_video_extra_platforms(raw) == expected
    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert bool(errors) == (error_words is not None), errors
    assert all("MARKETING_VIDEO_EXTRA_PLATFORMS" in m for m in errors)
    if error_words:
        assert any(error_words in m for m in errors), errors


def test_batch3_the_extra_setting_is_read_when_called_and_never_echoes_a_blob(monkeypatch, caplog):
    _extra_switch(monkeypatch, "linkedin")
    assert ss.video_extra_platforms_setting() == ("linkedin",)
    _extra_switch(monkeypatch, "facebook,x")
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        assert ss.video_extra_platforms_setting() == ("facebook",)
    _extra_switch(monkeypatch, ",".join(f"z{i}{'q' * 500}" for i in range(50)))
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        assert ss.video_extra_platforms_setting() == ()
    assert caplog.records and all(len(r.getMessage()) < 400 for r in caplog.records)
    from app.config import Settings

    assert Settings.model_fields["MARKETING_VIDEO_EXTRA_PLATFORMS"].default == ""
    assert ss.parse_video_extra_platforms(Settings.model_fields["MARKETING_VIDEO_EXTRA_PLATFORMS"].default) == ()


# ── the pure freeze ──────────────────────────────────────────────────────────────────────────


def _video_caption(stored: Dict[str, Any], platform: str, authorship: str = "ai") -> str:
    return post_copy.extra_video_caption(platform, stored["posts"][platform]["caption"], POSTING_DAY, authorship)


def test_batch3_freeze_extra_formats_matrix(caplog):
    stored = _b3_frozen(_b3_package())
    assert stored["post_formats"]["facebook"] == "image" and "video" in stored["post_formats"].values()
    snapshot = copy.deepcopy(stored)
    freeze = ss.freeze_extra_formats
    # the switch empty: nothing, whatever the output could carry
    assert freeze(stored, POSTING_DAY, platforms=()) == {}
    assert freeze(stored, POSTING_DAY, platforms=[]) == {}
    # facebook + linkedin: both, each caption the platform's own under the narrated-video disclaimer
    both = freeze(stored, POSTING_DAY, platforms=("facebook", "linkedin"), run_id="r")
    assert both == {"extra_post_formats": {"facebook": "video", "linkedin": "video"},
                    "video_posts": {p: {"title": None, "caption": _video_caption(stored, p)}
                                    for p in ("facebook", "linkedin")}}
    assert list(both["extra_post_formats"]) == ["facebook", "linkedin"]
    # every listed platform, in EXTRA_VIDEO_PLATFORMS order whatever the input order; x / bluesky / junk ignored
    every = freeze(stored, POSTING_DAY, platforms=["threads", "x", "bluesky", 7, None, "linkedin", "facebook"])
    assert list(every["extra_post_formats"]) == ["facebook", "linkedin", "threads"]
    assert list(every["video_posts"]) == ["facebook", "linkedin", "threads"]
    assert stored == snapshot                                         # never written into
    # no primary video today: nothing (the unchanged worker renders no video)
    no_video = _b3_frozen(_b3_package(posts=("x", "bluesky", "facebook", "linkedin", "threads")))
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        assert freeze(no_video, POSTING_DAY, platforms=("facebook",), run_id="r-nv") == {}
    assert any("no video" in r.getMessage() and "r-nv" in r.getMessage() for r in caplog.records)
    # the judge dropped Facebook (no copy, no frozen format): no extra for it, LinkedIn still
    dropped = _b3_frozen(_b3_package(posts=tuple(p for p in _ALL_POSTS if p != "facebook")))
    got = freeze(dropped, POSTING_DAY, platforms=("facebook", "linkedin"))
    assert list(got["extra_post_formats"]) == ["linkedin"]
    # a hand-edited output whose Facebook copy has no frozen format: no extra for it (create_posts could not read
    # such an extra back), LinkedIn still
    unfrozen = copy.deepcopy(stored)
    del unfrozen["post_formats"]["facebook"]
    got = freeze(unfrozen, POSTING_DAY, platforms=("facebook", "linkedin"))
    assert list(got["extra_post_formats"]) == ["linkedin"]
    # the picture fell back to text (image posts off): its extra is frozen all the same
    text_day = _b3_frozen(_b3_package(), images=False)
    assert text_day["post_formats"]["facebook"] == "text"
    assert list(freeze(text_day, POSTING_DAY, platforms=("facebook",))["extra_post_formats"]) == ["facebook"]
    # a Threads video caption over 500 UTF-8 BYTES (yet under 500 characters) is skipped; the others stay
    wide = _b3_frozen(_b3_package(bodies={"threads": "—" * 120 + " compound."}))
    vc = _video_caption(wide, "threads")
    assert len(vc) <= 500 < len(vc.encode("utf-8"))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        got = freeze(wide, POSTING_DAY, platforms=("facebook", "threads"), run_id="r-th")
    assert list(got["extra_post_formats"]) == ["facebook"]
    assert any("Threads video caption" in r.getMessage() and "UTF-8 bytes" in r.getMessage() for r in caplog.records)
    # a caption this module did not compose does not re-derive: skipped with a WARNING, never the text
    junk = copy.deepcopy(stored)
    junk["posts"]["linkedin"]["caption"] = "copy linkedin, written by hand"
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        got = freeze(junk, POSTING_DAY, platforms=("facebook", "linkedin"), run_id="r-j")
    assert list(got["extra_post_formats"]) == ["facebook"]
    assert any("linkedin" in r.getMessage() and "r-j" in r.getMessage() for r in caplog.records)
    assert not any("written by hand" in r.getMessage() for r in caplog.records)
    # another run date: the long disclaimer is dated — nothing re-derives for Facebook / LinkedIn
    assert freeze(stored, POSTING_DAY + timedelta(days=1), platforms=("facebook", "linkedin")) == {}
    # an unknown authorship: nothing (ERROR)
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        assert freeze({**stored, "authorship": "human"}, POSTING_DAY, platforms=("facebook",)) == {}
    assert any("authorship" in r.getMessage() for r in caplog.records)
    # a string is not a collection of platform names
    for bad in ("facebook", b"facebook", "facebook,linkedin"):
        with pytest.raises(ValueError, match="collection"):
            freeze(stored, POSTING_DAY, platforms=bad)


def test_batch3_a_template_extra_is_rechecked_and_needs_its_fact_sheet(caplog):
    out = _b2_composed()
    stored = ss.freeze_post_formats(out, _B2_DAY, image_posts=True, x_images=False, run_id="r")
    assert "video" in stored["post_formats"].values()
    # B3-LIVE-1 (2026-10-20): `_b2_ceo_week` is a ROUNDUP — its Facebook / LinkedIn / Threads caption speaks of the
    # picture's rows ("The purchases: …", news-v20's format-neutral wording), so without the data card frozen (the
    # video will not draw the picture) no extra is frozen: WARNING naming `picture_caption`, never the caption's text
    assert "The purchases: " in stored["posts"]["facebook"]["caption"]
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        assert ss.freeze_extra_formats(stored, _B2_DAY, platforms=("facebook", "linkedin", "threads"), run_id="r-p",
                                       fact_sheet=_b2_sheet()) == {}
    pic = [r.getMessage() for r in caplog.records if "r-p" in r.getMessage()]
    assert len(pic) == 3 and all("picture_caption" in m and "The purchases" not in m and "The largest" not in m
                                 for m in pic)
    # a frozen video feature that is not the data card does not draw the picture either
    assert ss.freeze_extra_formats({**stored, "video_features": ["push_in"]}, _B2_DAY, platforms=("facebook",),
                                   run_id="r-p", fact_sheet=_b2_sheet()) == {}
    caplog.clear()
    stored["video_features"] = ["data_card", "push_in"]          # the video draws the day's picture
    got = ss.freeze_extra_formats(stored, _B2_DAY, platforms=("facebook", "linkedin", "threads"), run_id="r-t",
                                  fact_sheet=_b2_sheet())
    assert list(got["extra_post_formats"]) == ["facebook", "linkedin", "threads"]
    for p, post in got["video_posts"].items():
        assert post["caption"] == post_copy.extra_video_caption(p, stored["posts"][p]["caption"], _B2_DAY, "template")
        assert post_copy.NON_AFFILIATION in post["caption"] and "narration voiced with ai" in post["caption"].lower()
        assert not any(line.casefold() in post["caption"].casefold() for line in post_copy.AI_DISCLOSURES)
    # without its fact sheet a template's extra cannot be re-checked: none (WARNING)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        assert ss.freeze_extra_formats(stored, _B2_DAY, platforms=("facebook",), run_id="r-t") == {}
    assert any("fact sheet" in r.getMessage() and "r-t" in r.getMessage() for r in caplog.records)
    # a failed re-check: none for that platform (WARNING naming the codes)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        assert ss.freeze_extra_formats(stored, _B2_DAY, platforms=("facebook",), run_id="r-t",
                                       fact_sheet={"not": "a sheet"}) == {}
    assert any("facts_invalid" in r.getMessage() for r in caplog.records)


# ── _frozen_output: the default switch is byte-identical ─────────────────────────────────────


def _reference(svc_run, pkg, run_date, *, images, features, template):
    """What `_frozen_output` stored BEFORE Batch 3, for a holder that declared every capability: the formats
    freeze, then the video features of the package's kind."""
    ref = ss.freeze_post_formats(pkg, run_date, image_posts=images, x_images=False, run_id=svc_run,
                                 image_platforms=ss.image_platforms_setting(), image_blocked=None)
    kind = schemas.TEMPLATE_VIDEO_FEATURES if template else schemas.LESSON_VIDEO_FEATURES
    switch = tuple(f for f in ss.parse_video_features(features) if f in kind)
    ref.pop("video_features", None)
    feats = ss.freeze_video_features(ref, switch=switch, holder_v2=True, run_id=svc_run)
    if feats:
        ref["video_features"] = feats
    return ref


@pytest.mark.parametrize("kind", ["lesson", "template"])
@pytest.mark.parametrize("images", [False, True])
@pytest.mark.parametrize("features", ["", _ALL_FEATURES])
@pytest.mark.asyncio
async def test_batch3_the_default_switch_keeps_every_stored_output_byte_identical(world, monkeypatch, caplog, kind,
                                                                                images, features):
    from app.config import Settings

    sb, runs = world
    _extra_switch(monkeypatch, Settings.model_fields["MARKETING_VIDEO_EXTRA_PLATFORMS"].default)
    _video_switch(monkeypatch, features)
    calls: List[Any] = []

    def never(*a, **k):
        calls.append((a, k))
        raise RuntimeError("freeze_extra_formats must not run while the switch is empty")

    real = ss.freeze_extra_formats
    monkeypatch.setattr(ss, "freeze_extra_formats", never)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    day = _B2_DAY if kind == "template" else POSTING_DAY
    rid = _b2_run(sb, day)
    pkg, sheet = (_b2_composed(), _b2_sheet()) if kind == "template" else (_b3_package(), None)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        stored = await svc._frozen_output(copy.deepcopy(pkg), day, run_id=rid, gen_id="g", image_posts=images,
                                          fact_sheet=sheet)
    ref = _reference(rid, copy.deepcopy(pkg), day, images=images, features=features, template=kind == "template")
    assert json.dumps(stored, sort_keys=True, ensure_ascii=False) == json.dumps(ref, sort_keys=True, ensure_ascii=False)
    assert "extra_post_formats" not in stored and "video_posts" not in stored
    assert calls == [] and not [r for r in caplog.records if r.levelno >= logging.ERROR]
    # anti-vacuity: the very same output DOES freeze extras once the switch lists them — for the template (a Form 4
    # ROUNDUP, B3-LIVE-1) only while its video draws the day's picture (the data card frozen)
    monkeypatch.setattr(ss, "freeze_extra_formats", real)
    _extra_switch(monkeypatch, _EXTRA_ALL)
    on = await svc._frozen_output(copy.deepcopy(pkg), day, run_id=rid, gen_id="g", image_posts=images,
                                  fact_sheet=sheet)
    if kind == "template" and "data_card" not in stored.get("video_features", ()):
        assert "extra_post_formats" not in on and "video_posts" not in on
    else:
        assert set(on["extra_post_formats"]) >= {"facebook", "linkedin"}
    assert {k: v for k, v in on.items() if k not in ("extra_post_formats", "video_posts")} == stored


@pytest.mark.asyncio
async def test_batch3_extras_in_the_package_are_never_carried_over(world, monkeypatch):
    """A package that arrives carrying the keys (never one the writer or the templates write) is stored without
    them — the freeze's own decision only — with the switch off, and with it on."""
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _b2_run(sb)
    planted = _b3_package()
    planted["extra_post_formats"] = {"x": "video", "bluesky": "video"}
    planted["video_posts"] = {"x": {"title": None, "caption": "a planted caption"}}
    stored = await svc._frozen_output(copy.deepcopy(planted), POSTING_DAY, run_id=rid, gen_id="g", image_posts=True)
    assert "extra_post_formats" not in stored and "video_posts" not in stored
    _extra_switch(monkeypatch, "facebook")
    stored = await svc._frozen_output(copy.deepcopy(planted), POSTING_DAY, run_id=rid, gen_id="g", image_posts=True)
    assert stored["extra_post_formats"] == {"facebook": "video"} and list(stored["video_posts"]) == ["facebook"]


# ── _frozen_output through the kick ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_batch3_the_extra_switch_is_read_at_write_time(world, monkeypatch, caplog):
    """Read when the accepted output is written (like MARKETING_IMAGE_PLATFORMS): a flip during a slow generation
    lands on THIS write — and once written, a later flip changes nothing stored or read back."""
    sb, runs = world
    _image_switches(monkeypatch, images=True)
    _extra_switch(monkeypatch, "")
    writer = ExtraWriter(["accepted"])
    writer.gate = asyncio.Event()
    svc = ss.MarketingScriptService(runs, writer=writer)
    rid = _b2_run(sb)
    reads = _count_holder_reads(monkeypatch, runs, writer)
    await svc.kick(rid, claim=_holder(svc, rid))
    for _ in range(500):
        if writer.calls:
            break
        await asyncio.sleep(0.01)
    assert writer.calls and not writer.gate.is_set()
    _extra_switch(monkeypatch, "facebook,linkedin")
    writer.gate.set()
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        await _drain(svc)
    out = _script_row(sb, rid)["output"]
    assert out["extra_post_formats"] == {"facebook": "video", "linkedin": "video"}
    assert out["video_posts"] == {p: {"title": None, "caption": _video_caption(out, p)} for p in ("facebook", "linkedin")}
    assert reads == [rid] and ss._HOLDER_STATEMENTS == 1           # no run read for the extras
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("marketing script ACCEPTED")]
    assert "extras=facebook,linkedin" in line
    before = copy.deepcopy(out)
    _extra_switch(monkeypatch, "")
    script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert _script_row(sb, rid)["output"] == before
    assert "extra_post_formats" not in script and "video_posts" not in script


@pytest.mark.asyncio
async def test_batch3_the_accepted_line_says_extras_dash_by_default(world, monkeypatch, caplog):
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=ExtraWriter(["accepted"]))
    rid = _b2_run(sb)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    (line,) = [r.getMessage() for r in caplog.records if r.getMessage().startswith("marketing script ACCEPTED")]
    assert "extras=-" in line
    assert "extra_post_formats" not in _script_row(sb, rid)["output"]


@pytest.mark.asyncio
async def test_batch3_a_failing_extra_freeze_never_costs_the_package(world, monkeypatch, caplog):
    sb, runs = world
    _extra_switch(monkeypatch, _EXTRA_ALL)

    def broken(*_a, **_k):
        raise RuntimeError("extra freeze broke")

    monkeypatch.setattr(ss, "freeze_extra_formats", broken)
    svc = ss.MarketingScriptService(runs, writer=ExtraWriter(["accepted"]))
    rid = _b2_run(sb)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        await svc.kick(rid, claim=_holder(svc, rid))
        await _drain(svc)
    row = _script_row(sb, rid)
    assert row["status"] == "accepted" and row["generations"] == 1
    assert "extra_post_formats" not in row["output"] and "video_posts" not in row["output"]
    assert row["output"]["post_formats"]["facebook"] in ("image", "text")          # the formats are untouched
    (err,) = [r for r in caplog.records if r.levelno == logging.ERROR and "extra video posts" in r.getMessage()]
    assert rid in err.getMessage() and err.exc_info is not None


@pytest.mark.asyncio
async def test_batch3_the_worker_read_back_never_carries_extras(world, monkeypatch):
    sb, runs = world
    _extra_switch(monkeypatch, _EXTRA_ALL)
    svc = ss.MarketingScriptService(runs, writer=ExtraWriter(["accepted"]))
    rid = _b2_run(sb)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    out = _script_row(sb, rid)["output"]
    assert set(out["extra_post_formats"]) == {"facebook", "linkedin", "threads"}
    without = {k: v for k, v in out.items() if k not in ("extra_post_formats", "video_posts")}
    assert ss.worker_script(out, run_id=rid) == ss.worker_script(without, run_id=rid)
    script = (await svc.kick(rid, claim=_holder(svc, rid)))["script"]
    assert script == ss.worker_script(without, run_id=rid)
    wire = json.loads(schemas.WorkerScript(**script).model_dump_json())
    assert not {"extra_post_formats", "video_posts"} & set(wire)
    assert sorted(script["outlets"]) == sorted(out["posts"])      # the outlets are the PRIMARY posts' platforms


@pytest.mark.asyncio
async def test_batch3_a_lesson_day_with_extras_end_to_end(world, monkeypatch):
    """kick (switch facebook,linkedin) → accepted with the extras frozen → the worker's PRIMARY specs only →
    create_posts appends the Facebook and LinkedIn video posts on the run's one video, pending review."""
    sb, runs = world
    _image_switches(monkeypatch, images=True)
    _extra_switch(monkeypatch, "facebook,linkedin")
    svc = ss.MarketingScriptService(runs, writer=ExtraWriter(["accepted"]))
    rid = _b2_run(sb)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    out = _script_row(sb, rid)["output"]
    image = _asset(sb, rid, kind="card")
    next(a for a in sb.tables[mrs.ASSETS].rows if a["id"] == image)["metadata"] = {"image_role": "post_image"}
    _run_row(sb, rid)["metadata"]["image_asset_id"] = image
    video = _run_video(sb, rid)
    specs = [{"platform": p, "format": f, **({"asset_ids": [video if f == "video" else image]} if f != "text" else {})}
             for p, f in sorted(out["post_formats"].items())]
    posts = await runs.create_posts(rid, specs, claim=_holder_of(runs, rid))
    assert [(p["platform"], p["format"]) for p in posts][:len(specs)] == [(s["platform"], s["format"]) for s in specs]
    extra = posts[len(specs):]
    assert [(p["platform"], p["format"], p["status"]) for p in extra] == [
        ("facebook", "video", "pending_review"), ("linkedin", "video", "pending_review")]
    for p in extra:
        assert p["caption"] == out["video_posts"][p["platform"]]["caption"] == _video_caption(out, p["platform"])
        assert p["asset_ids"] == [video] and p["title"] is None
        assert p["idempotency_key"] == f"{POSTING_DAY.isoformat()}:{p['platform']}:video"
        assert p["metadata"]["made_with_ai"] is True
    # the worker can still never name one itself
    with pytest.raises(mrs.MarketingRequestInvalid):
        await runs.create_posts(rid, [{"platform": "facebook", "format": "video", "asset_ids": [video]}],
                                claim=_holder_of(runs, rid))


# ── the `world` fixture is a LESSON world, whatever this machine's environment says (HYG-1) ─────────────────


@pytest.mark.asyncio
async def test_the_world_is_a_lesson_world_whatever_the_environment_says(monkeypatch, request):
    """Review of 2026-10-20 (HYG-1): with C/F on in the environment (the planned production values), a lesson-day
    test whose holder declares `news_templates` built a template day through the REAL company-news adapter (FMP and
    Supabase, blocked by backend/conftest.py, the session exit set to 1) and reached the lesson it describes only by
    that fallback. `world` pins both switches at their declared defaults, so such a day never reaches the source."""
    from app.config import Settings

    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES",
                        "ceo_buys,company_stakes,congress_count,earnings,insider_buys,money_map,thirteen_f")
    sb, runs = request.getfixturevalue("world")          # after the environment's values, as in a real session
    for name in ("MARKETING_CONTENT_CLASSES", "MARKETING_NEWS_SERIES"):
        assert getattr(mrs.settings, name) == Settings.model_fields[name].default
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    reached: List[str] = []
    monkeypatch.setattr(svc, "_news_source_fn", lambda: reached.append("news source") or None)
    rid = _b2_run(sb)                                    # a holder that declares news_templates, on a Thursday
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    row = _script_row(sb, rid)
    assert reached == []
    assert row["status"] == "accepted"
    assert not str(row["source_ref"]).startswith(news_rules.LEDGER_PREFIX)        # the day is the lesson


# ── Spotlight lane (migration 210, 2026-11-15): the script row's lane comes from its run ──────────────────────
# `run_service.insert_script` is the ONE writer of `marketing_scripts.lane`, from the run row the kick already holds
# (no extra read); a main run's selecting INSERT stays byte for byte what it was (it never sends the column).


@pytest.mark.asyncio
async def test_spotlight_a_scripts_lane_is_its_runs_and_a_main_insert_never_sends_it(world, monkeypatch):
    sb, runs = world
    sent: List[Dict[str, Any]] = []
    real = sb.tables[mrs.SCRIPTS].insert

    def spy(payload):
        sent.append(dict(payload))
        return real(payload)

    monkeypatch.setattr(sb.tables[mrs.SCRIPTS], "insert", spy)
    handed: List[Any] = []
    real_insert_script = runs.insert_script

    async def recording_insert_script(row, **kw):
        handed.append((row["run_id"], (kw.get("run") or {}).get("id")))
        return await real_insert_script(row, **kw)

    monkeypatch.setattr(runs, "insert_script", recording_insert_script)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter([]))
    main_id = _run(sb, REST_DAY)
    spot_id = _run(sb, REST_DAY, lane="spotlight")          # the same day's second run (a rest day for both)
    for rid in (main_id, spot_id):
        state = await svc.kick(rid, claim=_holder(svc, rid))
        assert state["status"] == "rest_day", state
    assert [sorted(p) for p in sent] == [["run_date", "run_id", "status"], ["lane", "run_date", "run_id", "status"]]
    assert sent[1]["lane"] == "spotlight"
    assert "lane" not in _script_row(sb, main_id) and _script_row(sb, spot_id)["lane"] == "spotlight"
    # the kick hands insert_script the run it already read: the lane costs no extra read
    assert handed == [(main_id, main_id), (spot_id, spot_id)]


# ── Spotlight lane (migration 210, 2026-11-15): selection at the kick, the skip, the cross-lane exclusion ────────
# A spotlight run reads `selection.spotlight_plan_for` (never `plan_for`), narrowed by the SAME classes and
# per-series switch as the main lane, and its chain ends in `selection.SKIP`: a day where nothing qualifies writes
# ONE `rejected` / `nothing_qualified` script with its trail — never the lesson, never a rest day. Both lanes keep
# away from the other lane's day (`_cross_lane`): (a) its same-day ledger refs, (b) the LEAD company it drew within
# CROSS_LANE_COMPANY_DAYS (a main run checks only spotlight rows), (c) the series it used that day — and an
# exclusion never reaches the source's refusal memo (`remember_template_refusal`), only a real template refusal does.
# The records, the fake company-news source and the Storage fake are template_flow's (imported lazily: that module
# imports this one).

#: Thursday 2026-11-19: a spotlight weekday inside the Q3 13F season (not the week's last spotlight day), and a main
#: posting day whose enabled chain (the default series) is Money Map → lesson. Saturday the 21st leads with the 13F.
SPOT_TUE, SPOT_THU, SPOT_SAT = date(2026, 11, 17), date(2026, 11, 19), date(2026, 11, 21)
assert (SPOT_TUE.weekday(), SPOT_THU.weekday(), SPOT_SAT.weekday()) == (1, 3, 5)
#: What a spotlight-lane worker declares on its claim: the drop-2 worker's capabilities and `layout_feature`
#: (news-v22: the spotlight pair's `feature` picture — a holder without it has the pair dropped from its chain,
#: `script_service.FEATURE_LAYOUT_SERIES`).
#: news-v23 (2026-11-19): + look_v23 — the worker that ships the restyle declares it, and an older holder's chain never
#: holds earnings (`test_the_earnings_table_is_kept_off_the_chain_of_a_holder_without_look_v23`)
_SPOT_CAPS = ["layouts_2b", "news_templates", "post_image", "layout_feature", "look_v23"]


def _tf():
    import test_marketing_template_flow as TF

    return TF


@pytest.fixture
def spot_world(world, monkeypatch):
    """`world` plus a template day's settings (template_flow's `tworld`) and the spotlight lane OPEN: switch on,
    Tue/Thu/Sat, 10:00 + 6 attempts <= 16:00, the news classes — whatever this machine's environment says."""
    from app.config import Settings

    TF = _tf()
    sb, runs = world
    sb.storage = TF.FakeStorage()
    for name, value in (("MARKETING_CONTENT_CLASSES", "A,C,F"),
                        ("MARKETING_NEWS_SERIES", Settings.model_fields["MARKETING_NEWS_SERIES"].default),
                        ("MARKETING_IMAGE_POSTS", True), ("MARKETING_X_ALLOW_URLS", False),
                        ("MARKETING_FIRST_LINE_PREFIX", False),
                        ("SUPABASE_URL", TF._SB_PUBLIC), ("MARKETING_SPOTLIGHT_ENABLED", True),
                        ("MARKETING_SPOTLIGHT_WEEKDAYS", "tue,thu,sat"), ("MARKETING_SPOTLIGHT_HOUR_ET", 10),
                        ("MARKETING_MAX_RUN_ATTEMPTS", 6), ("MARKETING_RUN_HOUR_ET", 16)):
        monkeypatch.setattr(mrs.settings, name, value)
    monkeypatch.setattr(ss.smart_link, "store_state", lambda: "live")
    monkeypatch.setattr(ss, "_today_et", lambda: SPOT_THU)
    selection._log_lane_closed.cache_clear()
    selection._parse_spotlight_weekdays_text.cache_clear()
    yield sb, runs
    selection._log_lane_closed.cache_clear()
    selection._parse_spotlight_weekdays_text.cache_clear()


def _lane_run(sb: FakeSB, day: date, lane: str = "spotlight", *, caps=None) -> str:
    """A HELD run of `lane` on `day` whose holder declared `caps` (the drop-2 worker's by default)."""
    meta: Dict[str, Any] = {"claim_nonce": NONCE, "worker_capabilities": list(_SPOT_CAPS if caps is None else caps)}
    extra: Dict[str, Any] = {"metadata": meta, "worker_version": "spotlight-test"}
    if lane != "main":
        extra["lane"] = lane
    return _run(sb, day, **extra)


def _drawn(sb: FakeSB, day: date, lead: str, *, lane: str = "spotlight", template: str = "insider_spotlight",
           ref: Optional[str] = None) -> str:
    """An ACCEPTED script of another run on `day` whose post drew `lead` as its lead company (a spotlight-layout
    image with that header logo) — what the cross-lane read sees. Returns its source_ref."""
    ref = ref or f"news:{template}:{lead}:{day.isoformat()}"
    row = {"run_id": str(uuid.uuid4()), "run_date": day.isoformat(), "status": "accepted", "template_id": template,
           "source_ref": ref, "output": {"image_spec": {"layout": "spotlight", "header": {"logo": lead}},
                                         "logo_refs": [{"key": lead, "name": lead.title()}]}}
    if lane != "main":
        row["lane"] = lane
    sb.tables[mrs.SCRIPTS].rows.append(row)
    return ref


def _mm(sym: str, name: str):
    """A Money Map record the template accepts, for company `sym`."""
    TF = _tf()
    return TF.costco(company=TF.co(sym, name))


def test_spotlight_the_constants_the_kick_writes_are_the_schemas():
    assert ss.REASON_NOTHING_QUALIFIED == "nothing_qualified" and ss.REASON_NOTHING_QUALIFIED in schemas.SCRIPT_REJECT_REASONS
    assert ss.OTHER_LANE == "other_lane" and ss.OTHER_LANE in ss.SERIES_OUTCOMES
    assert len(ss.OTHER_LANE_SERIES) <= 60 and len(ss.OTHER_LANE_COMPANY) <= 60   # a trail reason is cut at 60
    assert ss.CROSS_LANE_COMPANY_DAYS == 2
    assert selection.SKIP not in selection.SERIES_BY_ID and selection.SKIP in selection.CHAIN_TERMINALS


@pytest.mark.parametrize("output, lead", [
    ({"image_spec": {"layout": "spotlight", "header": {"logo": "nvda"}},
      "logo_refs": [{"key": "NVDA", "name": "NVIDIA"}]}, "NVDA"),
    ({"image_spec": {"layout": "bars", "header": {"logo": " COST "}}, "logo_refs": []}, "COST"),
    # a roundup's header beats its rows; without a header the first row (the first logo ref) leads
    ({"image_spec": {"layout": "rows", "header": {"logo": "BRK-B"}, "sections": []},
      "logo_refs": [{"key": "BRK-B"}, {"key": "AAPL"}]}, "BRK-B"),
    ({"image_spec": {"layout": "rows", "sections": []}, "logo_refs": [{"key": "AAPL"}, {"key": "MSFT"}]}, "AAPL"),
    # a pair leads with its LEFT company (the investor), whatever the ref order says
    ({"image_spec": {"layout": "pair", "left": {"logo": "TSLA"}, "right": {"logo": "SPCX"}},
      "logo_refs": [{"key": "SPCX"}, {"key": "TSLA"}]}, "TSLA"),
    ({"image_spec": {"layout": "pair", "left": {"name": "Nscale"}, "right": {"logo": "NVDA"}},
      "logo_refs": [{"key": "NVDA"}]}, "NVDA"),
    ({"image_spec": {"layout": "grid", "tiles": []}, "logo_refs": [{"key": "AMD"}, {"key": "INTC"}]}, "AMD"),
    ({"hook": "a lesson", "cards": []}, None),                          # a lesson draws no company
    ({"image_spec": {"layout": "spotlight", "header": {"logo": ""}}, "logo_refs": "junk"}, None),
    ({"image_spec": "junk", "logo_refs": [None, {"key": 7}, {"key": "  "}, {"key": "ok"}]}, "OK"),
    (None, None), ("junk", None), ([], None),
])
def test_spotlight_lead_company_is_the_header_the_pairs_left_or_the_first_logo(output, lead):
    assert ss.lead_company(output) == lead


@pytest.mark.parametrize("row, series", [
    # a spotlight day that reached its SKIP: never the lesson
    ({"status": "rejected", "reject_reason": "nothing_qualified", "fact_sheet": {"selection": {"trail": [
        {"series": "money_map", "outcome": "no_candidates"}, {"series": "skip", "outcome": "no_candidates"}]}}},
     "skip"),
    ({"status": "rejected", "reject_reason": "nothing_qualified", "fact_sheet": {"selection": {"trail": []}}}, "skip"),
    ({"status": "rejected", "reject_reason": "nothing_qualified",
      "fact_sheet": {"selection": {"trail": [{"series": "money_map", "outcome": "other_lane"}]}}}, "skip"),
    # the main lane, unchanged: the chosen step, else the template's series, else the lesson
    ({"template_id": "money_map", "fact_sheet": {"selection": {"trail": [{"series": "money_map", "outcome": "chosen"}]}}},
     "money_map"),
    ({"template_id": "money_map", "fact_sheet": {"selection": {"trail": [
        {"series": "ceo_buys", "outcome": "no_candidates"}]}}}, "money_map"),
    ({"template_id": "myth_vs_fact", "fact_sheet": {"selection": {"trail": [
        {"series": "ceo_buys", "outcome": "no_candidates"}]}}}, "lesson"),
])
def test_spotlight_the_mirror_of_a_skip_trail_is_skip_never_the_lesson(row, series):
    assert ss._selection_mirror(row)["series"] == series


@pytest.mark.asyncio
async def test_spotlight_a_day_where_nothing_qualifies_is_one_rejected_nothing_qualified_row(spot_world, monkeypatch,
                                                                                               caplog):
    """The spotlight chain on the 13F season's Saturday is 13F spotlight → insider spotlight → Money Map → SKIP; the
    pair is not listed in the per-series switch (its default), so only Money Map is asked (the main Saturday is the
    lesson alone, so Money Map stays the spotlight's — C0). It has nothing: ONE `rejected` row, reason
    `nothing_qualified`, lane spotlight, its trail ending in the SKIP step — the kick answers `rejected` (the worker
    closes the day `skipped`), the writer is never called and nothing is mirrored as the lesson."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: SPOT_SAT)
    news = TF.FakeNews({})
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer, news=news)
    rid = _lane_run(sb, SPOT_SAT)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state == {"status": "rejected", "source_ref": None, "template_id": None, "reason": "nothing_qualified",
                     "violations": []}
    assert [c["series"] for c in news.calls] == ["money_map"] and writer.calls == []
    (row,) = sb.tables[mrs.SCRIPTS].rows
    assert (row["status"], row["reject_reason"], row["lane"], row["last_error"]) == (
        "rejected", "nothing_qualified", "spotlight", "spotlight: nothing qualified")
    assert row["fact_sheet"] == {"selection": {"plan": "spotlight_13f_lead", "chain": ["money_map", "skip"], "trail": [
        {"series": "money_map", "outcome": "no_candidates", "reason": "money_map_none_qualified"},
        {"series": "skip", "outcome": "no_candidates"}]}}
    assert ss._selection_mirror(row)["series"] == "skip"
    # the run mirrors the SKIP and its trail (never the lesson, never a pick it did not make): the run-health texts
    # and the digest read the run, and must tell this quiet day from a data source that failed (lane review)
    run_row = _run_row(sb, rid)
    assert run_row["metadata"]["series"] == "skip"
    assert run_row["metadata"]["series_trail"] == row["fact_sheet"]["selection"]["trail"]
    assert not run_row.get("source_ref") and not run_row.get("template_id") and not run_row.get("content_class")
    assert run_row["metadata"]["claim_nonce"] == NONCE                 # a merge: the claim is untouched
    assert any("NOTHING QUALIFIED" in r.getMessage() and rid in r.getMessage() for r in caplog.records)
    # idempotent: the next kick answers the same row, nothing rebuilt
    again = await svc.kick(rid, claim=_holder(svc, rid))
    assert again == state and len(news.calls) == 1 and len(sb.tables[mrs.SCRIPTS].rows) == 1


@pytest.mark.parametrize("day, plan, money_map", [(SPOT_THU, "spotlight_13f", False),
                                                   (SPOT_SAT, "spotlight_13f_lead", True)])
@pytest.mark.asyncio
async def test_spotlight_a_run_takes_the_spotlight_chain_and_posts_a_money_map(spot_world, monkeypatch, caplog, day,
                                                                               plan, money_map):
    """A spotlight run is selected from `spotlight_plan_for` (never the main calendar: a Saturday's main plan is the
    lesson alone, a Thursday's leads with earnings) narrowed by MARKETING_NEWS_SERIES — the weekly roundups and
    earnings listed never run there (the spotlight calendar holds none of them); the listed spotlight pair (shipped,
    news-v22) is asked first in the calendar's order. C0 (2026-11-16): a series the MAIN lane's chain holds that day
    leaves the spotlight's — Thursday's main chain is earnings → Money Map → lesson, so the Thursday spotlight never
    asks Money Map and, the pair finding nothing, SKIPS; Saturday's main day is the lesson alone, so Money Map is
    chosen there. Its accepted script is the spotlight's row; its ledger read names the lane."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: day)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES",
                        "ceo_buys,insider_buys,thirteen_f,money_map,insider_spotlight,thirteen_f_spotlight,earnings")
    reads: List[Any] = []
    real = runs.recent_source_refs

    async def spy(before, limit, **kw):
        reads.append((before, limit, kw))
        return await real(before, limit, **kw)

    monkeypatch.setattr(runs, "recent_source_refs", spy)
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())})
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer, news=news)
    rid = _lane_run(sb, day)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    pair = (["insider_spotlight", "thirteen_f_spotlight"] if plan == "spotlight_13f"
            else ["thirteen_f_spotlight", "insider_spotlight"])
    misses = [{"series": s, "outcome": "no_candidates", "reason": f"{s}_none_qualified"} for s in pair]
    row = _script_row(sb, rid)
    assert row["lane"] == "spotlight" and writer.calls == []
    assert reads == [(day, selection.news_recent_limit(), {"lane": "spotlight"})]
    assert selection.news_recent_limit() == 60 * (4 + 3)                # both lanes' days in the ledger read
    if money_map:
        assert state["status"] == "accepted" and state["template_id"] == "money_map", state
        assert [c["series"] for c in news.calls] == [*pair, "money_map"]
        assert row["source_ref"] == "news:money_map:COST:2025"
        assert row["fact_sheet"]["selection"] == {
            "plan": plan, "chain": [*pair, "money_map", "skip"],
            "trail": [*misses, {"series": "money_map", "outcome": "chosen"}]}
        assert _run_row(sb, rid)["metadata"]["series"] == "money_map"
    else:
        assert state["status"] == "rejected" and state["reason"] == "nothing_qualified", state
        assert [c["series"] for c in news.calls] == pair                # Money Map is the main lane's that day
        assert row["fact_sheet"]["selection"] == {
            "plan": plan, "chain": [*pair, "skip"], "trail": [*misses, {"series": "skip", "outcome": "no_candidates"}]}
        assert any("main lane's series left out=['money_map']" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("why, settings_patch, day", [
    ("the switch went off after the claim", {"MARKETING_SPOTLIGHT_ENABLED": False}, SPOT_THU),
    ("the weekday left the list", {"MARKETING_SPOTLIGHT_WEEKDAYS": "tue,sat"}, SPOT_THU),
    ("the weekdays became unreadable", {"MARKETING_SPOTLIGHT_WEEKDAYS": "someday"}, SPOT_THU),
    ("the news classes went off", {"MARKETING_CONTENT_CLASSES": "A"}, SPOT_THU),
    ("the hours stopped fitting", {"MARKETING_SPOTLIGHT_HOUR_ET": 11}, SPOT_THU),
])
@pytest.mark.asyncio
async def test_spotlight_a_lane_closed_after_the_claim_is_a_rest_day(spot_world, monkeypatch, why, settings_patch,
                                                                     day):
    TF = _tf()
    sb, runs = spot_world
    for name, value in settings_patch.items():
        monkeypatch.setattr(mrs.settings, name, value)
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, day)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rest_day", why
    assert news.calls == [] and _script_row(sb, rid)["status"] == "rest_day" and _script_row(sb, rid)["lane"] == "spotlight"


@pytest.mark.parametrize("caps", [["post_image"], [], ["layouts_2b", "post_image"]])
@pytest.mark.asyncio
async def test_spotlight_a_holder_that_cannot_draw_a_template_skips_never_the_lesson(spot_world, monkeypatch, caplog,
                                                                                      caps):
    """The spotlight lane is news-only: a holder without `news_templates` cannot draw its template, and the lesson
    is no fallback here (the main lane posts the week's) — the day skips `nothing_qualified`, WARNING naming the
    worker; the source and the writer are never asked."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: SPOT_SAT)
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())})
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer, news=news)
    rid = _lane_run(sb, SPOT_SAT, caps=caps)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified"
    assert news.calls == [] and writer.calls == [] and svc._builds == {}
    assert _script_row(sb, rid)["fact_sheet"]["selection"]["trail"] == [
        {"series": "skip", "outcome": "no_candidates", "reason": "worker_without_news_templates"}]
    assert any(r.levelno == logging.WARNING and "spotlight-test" in r.getMessage()
               and "never falls to the lesson" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("series, day", [
    # MARKETING_NEWS_SERIES gates both lanes: with Money Map pulled after the claim, the Saturday has no series left
    ("ceo_buys,insider_buys", SPOT_SAT),
    # C0 (2026-11-16): the Thursday's only spotlight series is the main lane's Money Map — no spotlight day at all
    (None, SPOT_THU),
])
@pytest.mark.asyncio
async def test_spotlight_a_chain_with_no_series_left_is_no_spotlight_day_and_the_kick_rests(spot_world, monkeypatch,
                                                                                           caplog, series, day):
    """A spotlight chain left with no series is NOT a spotlight day (`selection.spotlight_day_problem`, the one
    predicate the claim gate, the kick and the digest share): the claim answers `not_scheduled`, and a run claimed
    before the switch changed RESTS at the kick (the rest_day row, WARNING naming why) — no build task, no source
    asked, never a `nothing_qualified` alert about a day that could only skip."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: day)
    if series is not None:
        monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", series)
    assert selection.spotlight_day_problem(day) == selection.SPOTLIGHT_NO_SERIES
    assert await runs.claim_run(day, worker_version="t", dry_run=True, lane="spotlight") == (None, mrs.NOT_SCHEDULED)
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, day)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rest_day", state
    assert news.calls == [] and svc._builds == {}
    assert _script_row(sb, rid)["status"] == "rest_day" and _script_row(sb, rid)["lane"] == "spotlight"
    assert any(selection.SPOTLIGHT_NO_SERIES in r.getMessage() and rid in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_spotlight_the_skip_mirror_is_written_once_only_on_a_held_run_and_a_lost_write_is_logged(
        spot_world, monkeypatch, caplog):
    """`_heal_skip_mirror`: ONE fenced merge on a held run, nothing when it already holds the trail (a replayed kick
    writes nothing), nothing on a run no live claim holds, and a failed write is a WARNING naming the run — the kick
    still answers the skip (the mirror is informational: the script row is the record)."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: SPOT_SAT)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=TF.FakeNews({}))
    writes: List[Dict[str, Any]] = []
    real = runs.update_run

    async def spy(run_id, **kw):
        writes.append(kw)
        return await real(run_id, **kw)

    monkeypatch.setattr(runs, "update_run", spy)
    rid = _lane_run(sb, SPOT_SAT)
    first = await svc.kick(rid, claim=_holder(svc, rid))
    assert first["reason"] == "nothing_qualified"
    assert [sorted(w) for w in writes] == [["cas", "metadata"]] and writes[0]["cas"] is True
    assert set(writes[0]["metadata"]) == {"series", "series_trail"}
    assert await svc.kick(rid, claim=_holder(svc, rid)) == first and len(writes) == 1      # already mirrored

    # a run no live claim holds: nothing written (an update bumps updated_at, the claim's liveness)
    row = _script_row(sb, rid)
    stale = {**_run_row(sb, rid), "metadata": {"claim_nonce": NONCE}, "status": "skipped"}
    await svc._heal_skip_mirror(stale, row)
    assert len(writes) == 1

    # a lost write: logged with the run, never raised
    async def boom(run_id, **kw):
        raise mrs.MarketingRunError("update_run failed: RuntimeError: ledger down")

    monkeypatch.setattr(runs, "update_run", boom)
    held = {**_run_row(sb, rid), "metadata": {"claim_nonce": NONCE}}
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        await svc._heal_skip_mirror(held, row)
    assert any("could not mirror the skip trail" in r.getMessage() and rid in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("row", [
    {"status": "rest_day"},                                                                     # a rest day
    {"status": "rejected", "reject_reason": "empty_pool", "fact_sheet": {"selection": {"trail": [
        {"series": "ceo_buys", "outcome": "no_candidates"}]}}},                                  # a lesson day's empty pool
    {"status": "rejected", "reject_reason": "content", "fact_sheet": {}},
])
@pytest.mark.asyncio
async def test_a_row_without_a_pick_that_never_reached_skip_mirrors_nothing(world, monkeypatch, row):
    """The main lane, unchanged: a row with no source_ref mirrors nothing unless its trail reached SKIP (only a
    spotlight `nothing_qualified` row does)."""
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    writes: List[Any] = []

    async def spy(run_id, **kw):
        writes.append(kw)
        return None

    monkeypatch.setattr(runs, "update_run", spy)
    rid = _run(sb, POSTING_DAY)
    await svc._heal_mirror(_run_row(sb, rid), {"run_id": rid, **row})
    assert writes == []


@pytest.mark.asyncio
async def test_spotlight_an_unknown_lane_is_refused_before_any_selection(spot_world):
    sb, runs = spot_world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=_tf().FakeNews({}))
    rid = _lane_run(sb, SPOT_THU, lane="sideline")
    with pytest.raises(mrs.MarketingRequestInvalid):
        await svc.kick(rid, claim=_holder(svc, rid))
    assert sb.tables[mrs.SCRIPTS].rows == []


# ── the cross-lane exclusion ──────────────────────────────────────────────────


#: An earnings off-season Thursday inside the Q3 13F season (the second review's example): the main chain is Money Map
#: → lesson with the default switch.
SPOT_OFF_THU = date(2026, 12, 3)


@pytest.mark.asyncio
async def test_spotlight_an_off_season_thursday_leaves_the_money_map_to_the_main_lane(spot_world, monkeypatch):
    """C0 (2026-11-16, the second review's major): two runs on an off-season Thursday. The spotlight's chain never holds
    Money Map — the main lane's chain that day does — so the 10:00 spotlight asks only its insider spotlight and skips
    `nothing_qualified`; the 16:00 main run then posts ITS Money Map (Walmart), never a second weekly lesson. Before,
    the spotlight took Money Map and rule (c) sent the main day to the lesson."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: SPOT_OFF_THU)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "ceo_buys,insider_buys,thirteen_f,money_map,"
                                                               "insider_spotlight")
    spot_news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())})
    spot = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=spot_news)
    sid = _lane_run(sb, SPOT_OFF_THU)
    state = await spot.kick(sid, claim=_holder(spot, sid))
    await _drain(spot)
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified", state
    assert [c["series"] for c in spot_news.calls] == ["insider_spotlight"]
    assert _script_row(sb, sid)["fact_sheet"]["selection"]["chain"] == ["insider_spotlight", "skip"]
    main_news = TF.FakeNews({"money_map": TF.cands("money_map", _mm("WMT", "Walmart"))})
    main = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=main_news)
    mid = _lane_run(sb, SPOT_OFF_THU, lane="main")
    state = await main.kick(mid, claim=_holder(main, mid))
    await _drain(main)
    assert state["status"] == "accepted" and state["template_id"] == "money_map", state
    assert state["source_ref"] == "news:money_map:WMT:2025"
    assert _script_row(sb, mid)["fact_sheet"]["selection"]["trail"] == [{"series": "money_map", "outcome": "chosen"}]
    assert "lane" not in _script_row(sb, mid)                           # the main INSERT never sends the column


@pytest.mark.asyncio
async def test_spotlight_rule_c_still_guards_a_series_the_other_lane_used_that_day(spot_world):
    """Rule (c) stays as the guard for anything the calendars cannot see (a switch changed between the two runs of one
    day): an accepted spotlight Money Map on the Thursday — which C0 no longer produces under one set of settings — still
    sends the main lane's Money Map `other_lane` before the source is asked, and the day ends in the lesson."""
    TF = _tf()
    sb, runs = spot_world
    _drawn(sb, SPOT_THU, "COST", template="money_map", ref="news:money_map:COST:2025")
    main_news = TF.FakeNews({"money_map": TF.cands("money_map", _mm("WMT", "Walmart"))})
    main = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=main_news)
    mid = _lane_run(sb, SPOT_THU, lane="main")
    state = await main.kick(mid, claim=_holder(main, mid))
    await _drain(main)
    assert state["template_id"] in selection.LESSON_TEMPLATE_IDS and main_news.calls == []
    assert _script_row(sb, mid)["fact_sheet"]["selection"]["trail"] == [
        {"series": "money_map", "outcome": "other_lane", "reason": "posted by the other lane today"},
        {"series": "lesson", "outcome": "chosen"}]
    assert "lane" not in _script_row(sb, mid)                           # the main INSERT never sends the column


@pytest.mark.asyncio
async def test_spotlight_the_other_lanes_same_day_ref_joins_the_exclude_set(spot_world):
    """Rule (a): the `recent` read stops before the run date, so the other lane's SAME-DAY pick reaches the source's
    exclude set only through the lane-aware read — while the main lane's own earlier refs are there as before."""
    TF = _tf()
    sb, runs = spot_world
    same_day = _drawn(sb, SPOT_THU, "NVDA")
    TF._seed_recent(sb, SPOT_THU - timedelta(days=7), "news:money_map:AAPL:2025")
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    mid = _lane_run(sb, SPOT_THU, lane="main")
    state = await svc.kick(mid, claim=_holder(svc, mid))
    assert state["template_id"] == "money_map"
    assert news.calls[0]["exclude"] == frozenset({same_day, "news:money_map:AAPL:2025"})


@pytest.mark.parametrize("days_before, excluded", [(0, True), (1, True), (2, True), (3, False)])
@pytest.mark.asyncio
async def test_spotlight_the_main_lane_skips_a_company_the_spotlight_led_with_in_the_last_two_days(
        spot_world, monkeypatch, caplog, days_before, excluded):
    """Rule (b) on the main lane: a Money Map of Costco is EXCLUDED while a spotlight post led with Costco on a run
    dated within CROSS_LANE_COMPANY_DAYS (the next candidate posts) — and offered again from the third day. The
    exclusion is never reported to the source as a template refusal."""
    TF = _tf()
    sb, runs = spot_world
    _drawn(sb, SPOT_THU - timedelta(days=days_before), "COST")
    news = TF.ReportingNews({"money_map": TF.cands("money_map", TF.costco(), _mm("WMT", "Walmart"))})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    mid = _lane_run(sb, SPOT_THU, lane="main")
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        state = await svc.kick(mid, claim=_holder(svc, mid))
    assert state["source_ref"] == ("news:money_map:WMT:2025" if excluded else "news:money_map:COST:2025")
    assert news.reported == []
    assert _script_row(sb, mid)["fact_sheet"]["selection"]["trail"] == [{"series": "money_map", "outcome": "chosen"}]
    assert any("EXCLUDED" in r.getMessage() and "COST" in r.getMessage() for r in caplog.records) is excluded


@pytest.mark.asyncio
async def test_spotlight_the_main_lane_never_checks_its_own_companies(spot_world, monkeypatch):
    """The main lane's rule (b) reads only SPOTLIGHT rows: a main post that led with Costco yesterday changes
    nothing — so with the spotlight lane off (and no spotlight row) the main day is what it always was."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(mrs.settings, "MARKETING_SPOTLIGHT_ENABLED", False)
    _drawn(sb, SPOT_THU - timedelta(days=1), "COST", lane="main", template="money_map",
           ref="news:money_map:COST:2024")
    news = TF.ReportingNews({"money_map": TF.cands("money_map", TF.costco())})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    mid = _lane_run(sb, SPOT_THU, lane="main")
    state = await svc.kick(mid, claim=_holder(svc, mid))
    assert state["source_ref"] == "news:money_map:COST:2025" and news.reported == []


@pytest.mark.asyncio
async def test_spotlight_the_spotlight_keeps_away_from_both_lanes_recent_companies(spot_world, monkeypatch):
    """Rule (b) on the spotlight lane reads EVERY lane's rows, its own earlier days included: Costco (main, two days
    before) and Walmart (spotlight, two days before) are excluded, so Money Map falls back `other_lane` /
    `other_lane_company` and the day skips — and neither exclusion is reported to the source."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: SPOT_SAT)
    _drawn(sb, SPOT_THU, "COST", lane="main", template="money_map", ref="news:money_map:COST:2024")
    _drawn(sb, SPOT_THU, "WMT")
    news = TF.ReportingNews({"money_map": TF.cands("money_map", TF.costco(), _mm("WMT", "Walmart"))})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    sid = _lane_run(sb, SPOT_SAT)
    state = await svc.kick(sid, claim=_holder(svc, sid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified"
    assert news.reported == []
    assert _script_row(sb, sid)["fact_sheet"]["selection"]["trail"] == [
        {"series": "money_map", "outcome": "other_lane", "reason": "other_lane_company"},
        {"series": "skip", "outcome": "no_candidates"}]
    assert ss._selection_mirror(_script_row(sb, sid))["series"] == "skip"


@pytest.mark.asyncio
async def test_spotlight_an_excluded_money_map_is_never_remembered_but_a_refused_one_is(spot_world):
    """RR-major (the lane review of 2026-11-15): with a recording `remember_template_refusal`, a Money Map the
    template REFUSES is reported (its own code, the run date), the Costco Money Map the spotlight's same-day post
    led with is EXCLUDED and NOT reported — the 28-day memo never hears of it — and the next candidate posts."""
    TF = _tf()
    sb, runs = spot_world
    _drawn(sb, SPOT_THU, "COST")
    news = TF.ReportingNews({"money_map": TF.cands("money_map", TF.stale_costco(), TF.costco(),
                                                      _mm("WMT", "Walmart"))})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    mid = _lane_run(sb, SPOT_THU, lane="main")
    state = await svc.kick(mid, claim=_holder(svc, mid))
    assert state["source_ref"] == "news:money_map:WMT:2025"
    assert news.reported == [("news:money_map:COST:2022", ("stale_source",), SPOT_THU)]


@pytest.mark.asyncio
async def test_spotlight_an_excluded_stake_is_never_remembered_but_a_refused_one_is(spot_world, monkeypatch):
    """The stake twin: NVIDIA led a spotlight post the day before, so NVIDIA's stakes post is EXCLUDED and NOT
    reported (the stake memo would hold it for its fingerprint's life); a post whose stake the template refuses ("%" in
    its source title) IS reported. Every candidate gone, the series falls back `all_refused` and the day is the
    lesson."""
    import test_marketing_news_templates as NT

    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "company_stakes")
    monkeypatch.setattr(ss, "_today_et", lambda: NT.STAKES_RUN)
    _drawn(sb, NT.STAKES_RUN - timedelta(days=1), "NVDA")
    refused = NT.one(NT.stake(stake_id="0b8f3c2e-1d4a-4e6b-8c9d-2a7e5f1b3c04", source_title="Intel 8-K (a 9% stake)"))
    news = TF.ReportingNews({"company_stakes": TF.cands("company_stakes", refused, NT.nvidia_stakes())})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    mid = _lane_run(sb, NT.STAKES_RUN, lane="main")
    state = await svc.kick(mid, claim=_holder(svc, mid))
    await _drain(svc)
    assert state["template_id"] in selection.LESSON_TEMPLATE_IDS
    assert news.reported == [(TF.R.ledger_key(refused), ("slot_rejected",), NT.STAKES_RUN)]
    assert _script_row(sb, mid)["fact_sheet"]["selection"]["trail"][0] == {
        "series": "company_stakes", "outcome": "all_refused", "reason": "slot_rejected"}


@pytest.mark.asyncio
async def test_spotlight_a_stake_excluded_alone_falls_back_other_lane_and_is_never_reported(spot_world, monkeypatch):
    import test_marketing_news_templates as NT

    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "company_stakes")
    monkeypatch.setattr(ss, "_today_et", lambda: NT.STAKES_RUN)
    _drawn(sb, NT.STAKES_RUN, "NVDA")
    news = TF.ReportingNews({"company_stakes": TF.cands("company_stakes", NT.nvidia_stakes())})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    mid = _lane_run(sb, NT.STAKES_RUN, lane="main")
    state = await svc.kick(mid, claim=_holder(svc, mid))
    await _drain(svc)
    assert state["template_id"] in selection.LESSON_TEMPLATE_IDS and news.reported == []
    assert _script_row(sb, mid)["fact_sheet"]["selection"]["trail"] == [
        {"series": "company_stakes", "outcome": "other_lane", "reason": "other_lane_company"},
        {"series": "lesson", "outcome": "chosen"}]


@pytest.mark.asyncio
async def test_spotlight_a_failed_cross_lane_read_crashes_the_build_and_the_next_kick_builds_afresh(spot_world,
                                                                                                    monkeypatch):
    """The cross-lane read is never guessed: one that fails raises into the kick (a 5xx the worker retries) with
    nothing written, and the next kick builds the day afresh."""
    TF = _tf()
    sb, runs = spot_world
    real = runs.lane_scripts_between
    fail = [1]

    async def flaky(start, end):
        if fail[0]:
            fail[0] -= 1
            raise mrs.MarketingRunError("lane_scripts_between failed: 520")
        return await real(start, end)

    monkeypatch.setattr(runs, "lane_scripts_between", flaky)
    monkeypatch.setattr(ss, "_today_et", lambda: SPOT_SAT)
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    sid = _lane_run(sb, SPOT_SAT)
    with pytest.raises(mrs.MarketingRunError):
        await svc.kick(sid, claim=_holder(svc, sid))
    await _drain(svc)
    assert sb.tables[mrs.SCRIPTS].rows == [] and news.calls == []
    state = await svc.kick(sid, claim=_holder(svc, sid))
    assert state["status"] == "accepted" and state["template_id"] == "money_map"


@pytest.mark.asyncio
async def test_spotlight_with_the_switch_off_a_main_template_day_is_unchanged(spot_world, monkeypatch):
    """The main lane, the spotlight lane off and no spotlight row: the stored row is exactly what the same build
    stores when the cross-lane read is skipped altogether (the pre-lane flow) — same exclude set, same output, same
    fact sheet, the INSERT without `lane`."""
    TF = _tf()
    monkeypatch.setattr(mrs.settings, "MARKETING_SPOTLIGHT_ENABLED", False)
    stored = []
    for skip_cross_lane in (False, True):
        sb = FakeSB()
        sb.storage = TF.FakeStorage()
        runs = mrs.MarketingRunService(supabase=sb)
        TF._seed_recent(sb, SPOT_THU - timedelta(days=7), "news:money_map:AAPL:2025")
        _drawn(sb, SPOT_THU - timedelta(days=1), "COST", lane="main", template="money_map",
               ref="news:money_map:COST:2024")
        sent: List[Dict[str, Any]] = []
        real = sb.tables[mrs.SCRIPTS].insert
        sb.tables[mrs.SCRIPTS].insert = lambda payload, real=real, sent=sent: (sent.append(dict(payload)),
                                                                               real(payload))[1]
        svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]),
                                        news=TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())}))
        if skip_cross_lane:
            async def nothing(run, run_date, lane):
                return frozenset(), frozenset(), frozenset()

            monkeypatch.setattr(svc, "_cross_lane", nothing)
        mid = _lane_run(sb, SPOT_THU, lane="main")
        state = await svc.kick(mid, claim=_holder(svc, mid))
        assert state["template_id"] == "money_map"
        (payload,) = sent
        assert "lane" not in payload
        stored.append(({k: v for k, v in payload.items() if k not in ("run_id", "generation_id")},
                       svc._news.calls[0]["exclude"]))
    assert stored[0] == stored[1]


class _LaneRows:
    """A `runs` whose cross-lane read answers `rows` (and records the window it was asked for)."""

    def __init__(self, rows):
        self.rows, self.asked = rows, []

    async def lane_scripts_between(self, start, end):
        self.asked.append((start, end))
        return self.rows


def _out(lead: str) -> Dict[str, Any]:
    return {"image_spec": {"layout": "spotlight", "header": {"logo": lead}}, "logo_refs": [{"key": lead}]}


_ODD_ROWS = [
    "not a row", None,
    {"run_id": "self", "run_date": "2026-11-19", "lane": "main", "source_ref": "news:own", "template_id": "money_map",
     "output": _out("OWN")},                                                       # this run's own row: never counted
    {"run_id": "a", "run_date": "2026-11-19", "lane": "spotlight", "source_ref": "news:a", "template_id": "money_map",
     "output": _out("AAA")},                                                       # the other lane, today
    {"run_id": "b", "run_date": "2026-11-19T00:00:00", "lane": None, "source_ref": "news:b",
     "template_id": "theme_explainer", "output": _out("BBB")},                    # main (no lane), today
    {"run_id": "c", "run_date": "2026-11-18", "lane": "spotlight", "source_ref": "news:c", "template_id": "earnings",
     "output": _out("CCC")},                                                       # the other lane, yesterday
    {"run_id": "d", "run_date": "2026-11-17", "lane": "main", "source_ref": None, "template_id": 7,
     "output": "junk"},                                                            # main, unreadable
    {"run_id": "e", "run_date": None, "lane": "sideline", "source_ref": "news:e", "template_id": "money_map",
     "output": _out("EEE")},                                                       # an unknown lane, no date
    {"run_id": "f", "run_date": "2026-11-19", "lane": "sideline", "source_ref": "", "template_id": "",
     "output": _out("FFF")},                                                       # an unknown lane, today
    # tomorrow (only a MAIN read reaches it — C0's forward day): the spotlight's ref and lead count, its series never
    {"run_id": "g", "run_date": "2026-11-20", "lane": "spotlight", "source_ref": "news:g", "template_id": "company_stakes",
     "output": _out("GGG")},
]


def _roots(*syms: str) -> set:
    return {ss.LEAD_KEY_ROOT + sym for sym in syms}


@pytest.mark.parametrize("lane, refs, taken, leads", [
    # the main lane: the spotlight's (and any unknown lane's) refs of today and tomorrow, its series of today; leads of
    # non-main rows only (as issuer-root keys: these rows name no company)
    ("main", {"news:a", "news:g"}, {"money_map"}, _roots("AAA", "CCC", "EEE", "FFF", "GGG")),
    # the spotlight: the main lane's (and any unknown lane's) same-day refs and series; every lane's leads (its read
    # ends on the run date, so the fake's tomorrow row is one a real read never returns — counted as a lead only)
    ("spotlight", {"news:b"}, {"theme_explainer"}, _roots("AAA", "BBB", "CCC", "EEE", "FFF", "GGG")),
])
@pytest.mark.asyncio
async def test_spotlight_the_cross_lane_read_tolerates_odd_rows_and_never_counts_its_own(lane, refs, taken, leads):
    runs = _LaneRows(_ODD_ROWS)
    svc = ss.MarketingScriptService(runs, writer=FakeWriter([]))
    got = await svc._cross_lane({"id": "self"}, SPOT_THU, lane)
    assert got == (frozenset(refs), frozenset(taken), frozenset(leads))
    through = SPOT_THU + timedelta(days=1) if lane == "main" else SPOT_THU
    assert runs.asked == [(SPOT_THU - timedelta(days=ss.CROSS_LANE_COMPANY_DAYS), through)]


@pytest.mark.parametrize("output, keys", [
    ({"image_spec": {"layout": "spotlight", "header": {"logo": "GOOGL"}},
      "logo_refs": [{"key": "GOOGL", "name": "Alphabet"}]}, {"root:GOOG", "name:alphabet"}),
    ({"image_spec": {"layout": "bars", "header": {"logo": "GOOG"}},
      "logo_refs": [{"key": "GOOG", "name": "  ALPHABET  "}]}, {"root:GOOG", "name:alphabet"}),
    ({"image_spec": {"layout": "rows", "header": {"logo": "BRK-B"}, "sections": []},
      "logo_refs": [{"key": "BRK-B", "name": "Berkshire Hathaway"}]}, {"root:BRK", "name:berkshire hathaway"}),
    # an OTC foreign line is never folded into a root (often another company)
    ({"image_spec": {"layout": "spotlight", "header": {"logo": "CMPRF"}}, "logo_refs": [{"key": "CMPRF"}]},
     {"root:CMPRF"}),
    ({"hook": "a lesson"}, set()), (None, set()), ("junk", set()),
])
def test_spotlight_lead_keys_fold_share_classes_and_names(output, keys):
    assert ss.lead_keys(output) == frozenset(keys)


@pytest.mark.parametrize("drawn, name, excluded", [
    ("GOOGL", None, True),               # the second review's case: one company, two listings — by its issuer root
    ("ABCD", "Alphabet", True),          # another key, the same display name
    ("GOOGF", None, False),              # an OTC foreign line: not Alphabet's family, not its name
])
@pytest.mark.asyncio
async def test_spotlight_rule_b_compares_the_issuer_not_the_listing(spot_world, monkeypatch, caplog, drawn, name,
                                                                     excluded):
    """Rule (b) (the second review, 2026-11-16): the spotlight's post led with Alphabet as GOOGL (its Money Map seed
    symbol); the main lane's earnings walk keeps GOOG for Alphabet. A raw-symbol compare let both out within 48 h — the
    lead is now compared by its issuer root and its display name. Review 2026-11-20 (confirmed): the adapter returns
    ONE earnings record per call (`_earnings_day`), as fed here — the old version of this test fed two records and
    pinned "the next one posts", a world production never has: a matched LEAD excluded the whole day's results. Now
    Alphabet alone is DROPPED from the post (`ss.narrow_other_lane_lead`, before the fact sheet and the ledger key, so
    the key names only the companies drawn) and the other companies' results post; never reported to the source.
    Mutation-checked by hand: `narrow_other_lane_lead` returning the record unchanged turns this red."""
    import test_marketing_news_templates as NT

    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: NT.EARNINGS_RUN)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "earnings")
    ref = _drawn(sb, NT.EARNINGS_RUN - timedelta(days=2), drawn)
    if name is not None:
        row = next(r for r in sb.tables[mrs.SCRIPTS].rows if r["source_ref"] == ref)
        row["output"]["logo_refs"][0]["name"] = name
    # news-v23: ONE post of several companies; rule (b) compares its LEAD (the first, largest row)
    day = NT.earnings(lead=NT.earnings_row(company=NT.co("GOOG", "Alphabet")), others=2)
    assert [r.company.symbol for r in day.reports][0] == "GOOG" and len(day.reports) == 3
    news = TF.ReportingNews({"earnings": TF.cands("earnings", day)})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    mid = _lane_run(sb, NT.EARNINGS_RUN, lane="main")
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        state = await svc.kick(mid, claim=_holder(svc, mid))
    await _drain(svc)
    assert state["template_id"] == "earnings", state
    posted = dataclasses.replace(day, reports=day.reports[1:]) if excluded else day
    assert state["source_ref"] == TF.R.ledger_key(posted)
    assert ("GOOG" in state["source_ref"]) is not excluded
    assert news.reported == []
    assert any("GOOG DROPPED from the post" in r.getMessage() for r in caplog.records) is excluded
    trail = _script_row(sb, mid)["fact_sheet"]["selection"]["trail"]
    assert trail == [{"series": "earnings", "outcome": "chosen"}]


@pytest.mark.asyncio
async def test_spotlight_rule_b_a_two_company_day_left_with_one_falls_through_as_an_exclusion(spot_world, monkeypatch):
    """Review 2026-11-20: a two-company earnings day whose lead the other lane drew is left with ONE company — fewer
    than the post's minimum: the series falls through `other_lane` / `other_lane_company` (an EXCLUSION, never a
    template refusal: nothing reported to the source, never `all_refused`) and the day is the lesson."""
    import test_marketing_news_templates as NT

    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: NT.EARNINGS_RUN)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "earnings")
    _drawn(sb, NT.EARNINGS_RUN - timedelta(days=1), "GOOGL")
    day = NT.earnings(lead=NT.earnings_row(company=NT.co("GOOG", "Alphabet")), others=1)
    news = TF.ReportingNews({"earnings": TF.cands("earnings", day)})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    mid = _lane_run(sb, NT.EARNINGS_RUN, lane="main")
    state = await svc.kick(mid, claim=_holder(svc, mid))
    await _drain(svc)
    assert state["template_id"] != "earnings" and news.reported == []
    trail = _script_row(sb, mid)["fact_sheet"]["selection"]["trail"]
    assert trail[0] == {"series": "earnings", "outcome": "other_lane", "reason": "other_lane_company"}, trail


def test_narrow_other_lane_lead_drops_each_drawn_lead_of_a_several_company_record_only():
    """`ss.narrow_other_lane_lead` (review 2026-11-20): an earnings day or a Congress month drops its lead while the lead
    is one the other lane drew — the next lead checked the same way — and is None below its minimum; every other record
    (a stakes post's investor, a Money Map's one company) comes back as it is (the output check excludes it whole)."""
    import test_marketing_news_templates as NT

    avoid = ss.lead_keys({"image_spec": {"layout": "spotlight", "header": {"logo": "GOOGL"}},
                          "logo_refs": [{"key": "GOOGL", "name": "Alphabet"}]})
    day = NT.earnings(lead=NT.earnings_row(company=NT.co("GOOG", "Alphabet")), others=3)
    out, dropped = ss.narrow_other_lane_lead(day, avoid)
    assert dropped == ["GOOG"] and [r.company.symbol for r in out.reports] == [r.company.symbol
                                                                               for r in day.reports[1:]]
    # the next lead drawn too: both dropped, two left
    second = ss.lead_keys({"logo_refs": [{"key": day.reports[1].company.symbol,
                                          "name": day.reports[1].company.name}]})
    out2, dropped2 = ss.narrow_other_lane_lead(day, avoid | second)
    assert dropped2 == ["GOOG", day.reports[1].company.symbol] and len(out2.reports) == 2
    assert ss.narrow_other_lane_lead(NT.earnings(lead=NT.earnings_row(company=NT.co("GOOG", "Alphabet"))),
                                     avoid) == (None, ["GOOG"])
    assert ss.narrow_other_lane_lead(day, frozenset()) == (day, [])
    month = NT.congress(others=2)
    lead = month.counts[0].company
    cut, gone = ss.narrow_other_lane_lead(month, ss.lead_keys({"logo_refs": [{"key": lead.symbol, "name": lead.name}]}))
    assert gone == [lead.symbol] and [c.company.symbol for c in cut.counts] == [c.company.symbol
                                                                              for c in month.counts[1:]]
    assert TF_R().ledger_key(cut) == TF_R().ledger_key(month)          # a Congress post's key is its month's
    for other in (NT.nvidia_stakes(), NT.costco()):
        assert ss.narrow_other_lane_lead(other, avoid | ss.lead_keys({"logo_refs": [{"key": "NVDA"}, ]})) == (other, [])


def TF_R():
    return _tf().R


@pytest.mark.asyncio
async def test_spotlight_a_resumed_main_run_of_yesterday_sees_todays_spotlight_pick(spot_world, monkeypatch):
    """C0 (the second review's optional fix): yesterday's MAIN run, resumed today after today's spotlight accepted a
    Money Map of Costco, never takes the same ledger key — its cross-lane read reaches run_date + 1, so the spotlight's
    pick joins its exclude set (rule (a)) and its lead joins the companies to avoid (rule (b)): Walmart posts."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: SPOT_SAT)
    sat_ref = _drawn(sb, SPOT_SAT, "COST", template="money_map", ref="news:money_map:COST:2025")
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco(), _mm("WMT", "Walmart"))})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    fri = SPOT_SAT - timedelta(days=1)
    monkeypatch.setattr(mrs.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,fri,sat")
    mid = _lane_run(sb, fri, lane="main")
    state = await svc.kick(mid, claim=_holder(svc, mid))
    await _drain(svc)
    assert state["template_id"] == "money_map" and state["source_ref"] == "news:money_map:WMT:2025", state
    assert sat_ref in news.calls[0]["exclude"]


class _BuildRuns:
    """The four reads/writes `_build_template_day` makes, in memory."""

    def __init__(self):
        self.inserted: List[Dict[str, Any]] = []

    async def get_script(self, run_id):
        return None

    async def recent_source_refs(self, before, limit, **kw):
        return []

    async def lane_scripts_between(self, start, end):
        return []

    async def insert_script(self, row, **kw):
        self.inserted.append(dict(row))
        return dict(row), True


@pytest.mark.asyncio
async def test_spotlight_a_chain_whose_terminal_is_the_wrong_one_still_ends_in_its_lanes_own(monkeypatch, caplog):
    """Defensive (unreachable through the calendars): a spotlight chain that names the lesson still SKIPS, and a main
    chain that names the skip still selects the lesson — each logged at ERROR."""
    runs = _BuildRuns()
    svc = ss.MarketingScriptService(runs, writer=FakeWriter([]), news=_tf().FakeNews({}))
    lessons: List[Any] = []

    async def lesson(run, run_date, recent, *, classes, selection_block):
        lessons.append(selection_block)
        return {"status": "selected", "template_id": "myth_vs_fact"}

    monkeypatch.setattr(svc, "_select_lesson", lesson)
    classes = frozenset({"A", "C", "F"})
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        row = await svc._build_template_day({"id": "s", "lane": "spotlight"}, SPOT_THU,
                                            selection.DayPlan(False, (selection.LESSON,), "spotlight"),
                                            (selection.LESSON,), classes)
        assert row["reject_reason"] == "nothing_qualified" and lessons == []
        assert row["fact_sheet"]["selection"]["trail"] == [{"series": "skip", "outcome": "no_candidates"}]
        row = await svc._build_template_day({"id": "m"}, SPOT_THU,
                                            selection.DayPlan(False, (selection.SKIP,), "thursday"),
                                            (selection.SKIP,), classes)
        assert row["template_id"] == "myth_vs_fact" and lessons[0]["trail"] == [{"series": "lesson", "outcome": "chosen"}]
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("never posts the lesson" in m for m in errors) and any("always ends in the lesson" in m for m in errors)


@pytest.mark.asyncio
async def test_spotlight_a_broken_news_source_skips_the_day_never_the_lesson(spot_world, monkeypatch, caplog):
    """The company-news source cannot load: every series falls back `error` and the spotlight day skips
    `nothing_qualified` — the main lane's "every series falls through to the lesson" never happens here."""
    sb, runs = spot_world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))

    def broken():
        raise ImportError("adapter import failed")

    monkeypatch.setattr(svc, "_news_source_fn", broken)
    monkeypatch.setattr(ss, "_today_et", lambda: SPOT_SAT)
    sid = _lane_run(sb, SPOT_SAT)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        state = await svc.kick(sid, claim=_holder(svc, sid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified"
    assert _script_row(sb, sid)["fact_sheet"]["selection"]["trail"] == [
        {"series": "money_map", "outcome": "error", "reason": "news_source_unavailable"},
        {"series": "skip", "outcome": "no_candidates"}]
    assert any("falls through to the skip (nothing_qualified)" in r.getMessage() for r in caplog.records)
    # the outage reaches the RUN, where the nightly check reads it: never a quiet "nothing qualified" (OBS-1)
    meta = _run_row(sb, sid)["metadata"]
    assert meta["series"] == "skip" and meta["series_trail"][0]["outcome"] == "error"
    from app.services.marketing import digest_service as ds
    text = ds.evaluate_run_health({**_run_row(sb, sid), "status": "skipped",
                                   "metadata": {**meta, "skip_reason": "nothing_qualified"}},
                                  run_date=SPOT_SAT, posting_day=True, now=datetime.now(timezone.utc), max_attempts=6,
                                  stale_seconds=2700, lane="spotlight")
    assert text is not None and "Selection: nothing posted (fell back from Money Map: an error)" in text


# ── the spotlight lane, workstream B2 (2026-11-15): the insider spotlight's ledger inputs, the spotlight budgets ────
# B decisions 5-6, with C0 (2026-11-16): a build whose chain holds `insider_spotlight` reads the END of the last Form 4
# window an earlier insider spotlight CHECKED (`runs.last_checked_window`: recorded on that series' trail entry,
# `TRAIL_WINDOW_KEY`) and the insider spotlights accepted within the 14-day cooldown (`runs.lane_scripts_between`: their
# symbol and display name), and hands BOTH to that series' `candidates()` call only; every other series — and every
# build whose chain does not hold it — is asked exactly as before. A failed read stops the build (it raises into the
# kick), never a spotlight chosen on a guess. The window the collector checked rides back on its answer and is RECORDED
# on its trail entry — chosen, refused or nothing qualified, never when the feed was unavailable. The pair SHIPPED with
# its templates (news-v22); here it is LISTED in the per-series switch. On Thursday SPOT_THU the main lane's chain holds
# Money Map, so the spotlight's chain is the pair alone (C0).


def _ship_the_spotlight_pair(monkeypatch) -> None:
    """List the (shipped) spotlight pair in the per-series switch, beside Money Map."""
    assert selection.SPOTLIGHT_SERIES <= selection.SHIPPED_SERIES
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "money_map,insider_spotlight,thirteen_f_spotlight")


def _accepted(sb: FakeSB, day: date, template: str, ref: Optional[str], lead: Optional[str], name: Optional[str], *,
              lane: str = "spotlight") -> None:
    output: Dict[str, Any] = {}
    if lead is not None:
        output = {"image_spec": {"layout": "spotlight", "header": {"logo": lead}},
                  "logo_refs": [{"key": lead, **({"name": name} if name is not None else {})}]}
    row = {"run_id": str(uuid.uuid4()), "run_date": day.isoformat(), "status": "accepted", "template_id": template,
           "source_ref": ref, "output": output}
    if lane != "main":
        row["lane"] = lane
    sb.tables[mrs.SCRIPTS].rows.append(row)


def _checked(sb: FakeSB, day: date, start: date, end: date, *, lane: str = "spotlight") -> None:
    """A spotlight script of another run on `day` (a quiet `nothing_qualified` row) whose insider spotlight entry
    recorded the Form 4 window [start, end] as CHECKED."""
    row = {"run_id": str(uuid.uuid4()), "run_date": day.isoformat(), "status": "rejected",
           "reject_reason": "nothing_qualified", "fact_sheet": {"selection": {"trail": [
               {"series": "insider_spotlight", "outcome": "no_candidates", "reason": "insider_spotlight_none_qualified",
                ss.TRAIL_WINDOW_KEY: {"start": start.isoformat(), "end": end.isoformat()}},
               {"series": "skip", "outcome": "no_candidates"}]}}}
    if lane != "main":
        row["lane"] = lane
    sb.tables[mrs.SCRIPTS].rows.append(row)


@pytest.mark.asyncio
async def test_spotlight_b2_the_insider_spotlight_alone_gets_its_ledger_inputs(spot_world, monkeypatch, caplog):
    TF = _tf()
    sb, runs = spot_world
    _ship_the_spotlight_pair(monkeypatch)
    _checked(sb, SPOT_TUE, date(2026, 11, 10), date(2026, 11, 16))          # Tuesday's spotlight checked to Monday
    _checked(sb, SPOT_TUE - timedelta(days=1), date(2026, 11, 14), date(2026, 11, 15), lane="main")   # never read
    # within the 14-day cooldown (run dates Nov 5 .. Nov 19): Contoso (its key and its name), Fabrikam (a key that
    # does not parse: its lead and name), a row that names nothing; outside it or not an insider spotlight: ignored
    _accepted(sb, date(2026, 11, 10), "insider_spotlight", "news:insider_spotlight:CTSO:2026-11-03", "CTSO", "Contoso")
    _accepted(sb, date(2026, 11, 12), "insider_spotlight", "junk", "FBK", "Fabrikam")
    _accepted(sb, date(2026, 11, 14), "insider_spotlight", None, None, None)
    _accepted(sb, date(2026, 11, 4), "insider_spotlight", "news:insider_spotlight:OLD:2026-10-28", "OLD", "Old Co")
    _accepted(sb, date(2026, 11, 12), "money_map", "news:money_map:COST:2025", "COST", "Costco")
    _accepted(sb, date(2026, 11, 16), "ceo_buys", "news:ceo_buys:2026-11-09", "GME", "GameStop", lane="main")
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, SPOT_THU)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified", state
    assert [c["series"] for c in news.calls] == ["insider_spotlight", "thirteen_f_spotlight"]   # Money Map: main's
    first, *rest = news.calls
    assert first["kw"] == {"spotlight_checked_through": date(2026, 11, 16),
                           "spotlight_recent": (("CTSO", "Contoso"), ("FBK", "Fabrikam"))}
    assert all(c["kw"] == {} for c in rest)                    # the 13F spotlight: as before
    assert any("names no company" in r.getMessage() and rid in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_spotlight_b2_no_checked_window_within_the_cooldown_is_none(spot_world, monkeypatch):
    TF = _tf()
    sb, runs = spot_world
    _ship_the_spotlight_pair(monkeypatch)
    _checked(sb, SPOT_THU - timedelta(days=15), date(2026, 10, 28), date(2026, 11, 3))     # outside the 14 days
    news = TF.FakeNews({})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, SPOT_THU)
    await svc.kick(rid, claim=_holder(svc, rid))
    assert news.calls[0]["series"] == "insider_spotlight"
    assert news.calls[0]["kw"] == {"spotlight_checked_through": None, "spotlight_recent": ()}


@pytest.mark.parametrize("broken", ["last_checked_window", "lane_scripts_between"])
@pytest.mark.asyncio
async def test_spotlight_b2_a_failed_ledger_read_stops_the_build_never_a_guess(spot_world, monkeypatch, caplog, broken):
    TF = _tf()
    sb, runs = spot_world
    _ship_the_spotlight_pair(monkeypatch)
    real = getattr(runs, broken)
    fail = {"n": 1}

    async def flaky(*a, **kw):
        # the cross-lane read uses lane_scripts_between too (2 days): fail only the cooldown's 14-day read
        if broken == "lane_scripts_between" and (a[1] - a[0]).days < news_rules.SPOTLIGHT_ISSUER_COOLDOWN_DAYS:
            return await real(*a, **kw)
        if fail["n"]:
            fail["n"] -= 1
            raise mrs.MarketingRunError(f"{broken} failed: 520")
        return await real(*a, **kw)

    monkeypatch.setattr(runs, broken, flaky)
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, SPOT_THU)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        with pytest.raises(mrs.MarketingRunError):
            await svc.kick(rid, claim=_holder(svc, rid))
    assert news.calls == [] and not [r for r in sb.tables[mrs.SCRIPTS].rows if r.get("run_id") == rid]
    assert any("ledger reads failed" in r.getMessage() and rid in r.getMessage() for r in caplog.records)
    # the next kick builds the day afresh
    for _ in range(50):
        if not svc._builds:
            break
        await asyncio.sleep(0)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified", state
    assert news.calls[0]["kw"]["spotlight_checked_through"] is None


@pytest.mark.asyncio
async def test_spotlight_b2_no_ledger_input_is_read_unless_the_chain_holds_the_insider_spotlight(spot_world,
                                                                                               monkeypatch):
    """The two reads happen only for a build whose (enabled) chain holds `insider_spotlight`: never while the switch
    does not list it (its default, today's production), never on the main lane."""
    TF = _tf()
    sb, runs = spot_world
    reads: List[Any] = []
    real = runs.last_checked_window

    async def spy(*a, **kw):
        reads.append((a, kw))
        return await real(*a, **kw)

    monkeypatch.setattr(runs, "last_checked_window", spy)
    monkeypatch.setattr(ss, "_today_et", lambda: SPOT_SAT)
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, SPOT_SAT)                               # the pair not listed: the chain is Money Map → skip
    assert (await svc.kick(rid, claim=_holder(svc, rid)))["status"] == "accepted"
    assert reads == [] and all(c["kw"] == {} for c in news.calls)
    _ship_the_spotlight_pair(monkeypatch)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "money_map,thirteen_f_spotlight")
    news2 = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco(company=TF.co("WMT", "Walmart")))})
    svc2 = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news2)
    monkeypatch.setattr(ss, "_today_et", lambda: SPOT_THU)
    rid2 = _lane_run(sb, SPOT_THU)
    assert (await svc2.kick(rid2, claim=_holder(svc2, rid2)))["status"] in ("accepted", "rejected")
    assert reads == [] and [c["series"] for c in news2.calls] == ["thirteen_f_spotlight"]


# ── C0 (2026-11-16): the checked Form 4 window is recorded on the trail and anchors the next window ───────────────

_THU_WINDOW = (date(2026, 11, 13), date(2026, 11, 18))


def _insider_entry(row: Dict[str, Any]) -> Dict[str, Any]:
    return next(e for e in row["fact_sheet"]["selection"]["trail"] if e["series"] == "insider_spotlight")


@pytest.mark.parametrize("answer, recorded", [
    ("none_qualified", True), ("all_recent", True), ("unavailable", False), ("no_new_window", False),
])
@pytest.mark.asyncio
async def test_c0_the_checked_window_is_recorded_on_the_insider_spotlights_trail_entry(spot_world, monkeypatch, answer,
                                                                                       recorded):
    """Whenever the collector CHECKED a window — nothing qualified, everything already posted (or chosen / refused, the
    same code path) — the window rides on the insider spotlight's trail entry (`TRAIL_WINDOW_KEY`); never when its data
    source was unavailable (the feed behind) or there was no new day to check. The run's `series_trail` mirror never
    carries it."""
    TF = _tf()
    sb, runs = spot_world
    _ship_the_spotlight_pair(monkeypatch)
    answers = {
        "none_qualified": TF.cands("insider_spotlight", skip="insider_spotlight_none_qualified",
                                   checked_window=_THU_WINDOW),
        "all_recent": TF.cands("insider_spotlight", skip="insider_spotlight_none_qualified",
                               rejections={"already_posted": 2}, checked_window=_THU_WINDOW),
        "unavailable": TF.NewsUnavailable("insider_spotlight", "insider_feed_behind"),
        "no_new_window": TF.cands("insider_spotlight", skip="insider_spotlight_no_new_window"),
    }
    news = TF.FakeNews({"insider_spotlight": answers[answer]})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, SPOT_THU)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["reason"] == "nothing_qualified"
    entry = _insider_entry(_script_row(sb, rid))
    if recorded:
        assert entry[ss.TRAIL_WINDOW_KEY] == {"start": "2026-11-13", "end": "2026-11-18"}
    else:
        assert ss.TRAIL_WINDOW_KEY not in entry
    assert all(set(step) <= {"series", "outcome", "reason"} for step in _run_row(sb, rid)["metadata"]["series_trail"])
    assert await runs.last_checked_window("spotlight", "insider_spotlight", before=SPOT_SAT, within_days=14) == (
        _THU_WINDOW if recorded else None)


@pytest.mark.parametrize("case, recorded", [("too_few_rows", False), ("other_lane", False), ("stale_source", True),
                                           ("adapter_too_few", False)])
@pytest.mark.asyncio
async def test_review_1126_a_list_cut_below_three_leaves_its_window_unchecked(spot_world, monkeypatch, case, recorded):
    """Review of 2026-11-26 (correctness): with the 3-company minimum, recording every checked window dropped each
    1-2-company slice for good (each window is 1-2 business days on the Tue/Thu/Sat cadence). Now a window whose list
    fell below three — the adapter's `insider_spotlight_too_few_companies` (no window returned), the composer's
    `too_few_rows` after its per-company drops — or whose lead the other lane drew (`other_lane`) is NOT recorded:
    `last_checked_window` then reads the older one, and the next spotlight's window reaches back over these days. Any
    other refusal (a stale record) still records its window. Mutation-checked: passing `extra=checked` on both branches
    again turns the too_few_rows and other_lane legs red."""
    import test_marketing_news_templates as NT

    TF = _tf()
    sb, runs = spot_world
    _ship_the_spotlight_pair(monkeypatch)
    three = NT.isp_n(3)
    if case == "too_few_rows":
        # the composer's own drop: a row led by a sitting member of Congress (the roster) leaves two companies
        day = date(2026, 11, 18)
        rows = [NT.isp_company(NT.ip(sym, name, role, "Alma Adams" if i == 2 else None, amt, shares, 2, [day], [day],
                                     cost))
                for i, (sym, name, role, amt, shares, cost) in enumerate(NT._ISP7[:3])]
        answer = TF.cands("insider_spotlight", NT.isp_list(*rows, start=date(2026, 11, 17), end=day),
                          checked_window=_THU_WINDOW)
    elif case == "other_lane":
        _drawn(sb, date(2026, 11, 18), three.companies[0].lead.company.symbol, lane="main", template="ceo_buys",
               ref="news:ceo_buys:2026-11-09")
        answer = TF.cands("insider_spotlight", three, checked_window=_THU_WINDOW)
    elif case == "stale_source":
        stale = NT.isp_n(3, day=date(2026, 11, 6), start=date(2026, 11, 5), end=date(2026, 11, 6))
        answer = TF.cands("insider_spotlight", stale, checked_window=_THU_WINDOW)
    else:                                    # the adapter's own answer (`_collect_insider_spotlight`): no window
        answer = TF.cands("insider_spotlight", skip="insider_spotlight_too_few_companies")
    news = TF.FakeNews({"insider_spotlight": answer})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, SPOT_THU)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["reason"] == "nothing_qualified"
    entry = _insider_entry(_script_row(sb, rid))
    assert entry["outcome"] == {"too_few_rows": "all_refused", "other_lane": "other_lane",
                                "stale_source": "all_refused", "adapter_too_few": "no_candidates"}[case], entry
    assert (ss.TRAIL_WINDOW_KEY in entry) is recorded, entry
    if case in ("too_few_rows", "stale_source"):
        assert entry["reason"] == case, entry                                   # the composer's own code, nothing else
    assert await runs.last_checked_window("spotlight", "insider_spotlight", before=SPOT_SAT, within_days=14) == (
        _THU_WINDOW if recorded else None)


@pytest.mark.asyncio
async def test_review_1126_a_full_cooldown_of_full_company_lists_reaches_the_adapter(spot_world):
    """Review of 2026-11-26 (correctness): the cooldown input names EVERY company of every insider list accepted in the
    last 14 days; the adapter's bound (`SPOTLIGHT_RECENT_MAX`, 64 when a list was one company) refused all of them once
    a 5-day-a-week schedule had 9 full lists in the window — every insider list then failed into the theme. The bound
    is now one list a day of at most INSIDER_MAX_ROWS companies over the cooldown's 15 days: the worst case (a list
    on each of the 14 earlier days, five companies each since the final review — 70 pairs) is accepted.
    Mutation-checked: a bound below 70 raises."""
    from app.services.marketing import company_news_adapter as A

    assert A.SPOTLIGHT_RECENT_MAX == (news_rules.SPOTLIGHT_ISSUER_COOLDOWN_DAYS + 1) * news_rules.INSIDER_MAX_ROWS
    sb, runs = spot_world
    letters = "ABCDEFGHIJKLMNOP"
    for d in range(1, news_rules.SPOTLIGHT_ISSUER_COOLDOWN_DAYS + 1):
        day = SPOT_THU - timedelta(days=d)
        syms = [f"Q{letters[d]}{letters[k]}" for k in range(news_rules.INSIDER_MAX_ROWS)]
        sb.tables[mrs.SCRIPTS].rows.append({
            "run_id": str(uuid.uuid4()), "run_date": day.isoformat(), "lane": "spotlight", "status": "accepted",
            "template_id": "insider_spotlight",
            "source_ref": f"news:insider_spotlight:{'+'.join(syms)}:{(day - timedelta(days=2)).isoformat()}",
            "output": {"logo_refs": [{"key": s_, "name": f"Company {s_}"} for s_ in syms]}, "fact_sheet": {}})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=None)
    rid = _lane_run(sb, SPOT_THU)
    kw = await svc._spotlight_source_kw(_run_row(sb, rid), SPOT_THU)
    recent = kw["spotlight_recent"]
    assert len(recent) == news_rules.SPOTLIGHT_ISSUER_COOLDOWN_DAYS * news_rules.INSIDER_MAX_ROWS == 70
    assert A._spotlight_recent_arg(recent) == recent                      # accepted whole, never a shorter cooldown


@pytest.mark.asyncio
async def test_c0_a_13f_led_saturday_never_loses_the_thursday_filings(spot_world, monkeypatch):
    """The second-review scenario B asked for: Thursday's spotlight checks Nov 13-18 (recorded); Saturday — the season's
    13F-LED day — posts a 13F spotlight and never asks the insider spotlight (no window recorded); Tuesday's insider
    spotlight is then told the last checked day is WEDNESDAY Nov 18, so its window starts Thursday Nov 19. The previous
    RUN's date (Saturday) would have started it on Friday and lost Thursday's filings."""
    TF = _tf()
    sb, runs = spot_world
    _ship_the_spotlight_pair(monkeypatch)
    thu_news = TF.FakeNews({"insider_spotlight": TF.cands("insider_spotlight", skip="insider_spotlight_none_qualified",
                                                          checked_window=_THU_WINDOW)})
    thu = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=thu_news)
    tid = _lane_run(sb, SPOT_THU)
    assert (await thu.kick(tid, claim=_holder(thu, tid)))["reason"] == "nothing_qualified"
    # Saturday's 13F-led day: an accepted 13F spotlight whose trail never reached the insider spotlight
    sb.tables[mrs.SCRIPTS].rows.append({
        "run_id": str(uuid.uuid4()), "run_date": SPOT_SAT.isoformat(), "lane": "spotlight", "status": "accepted",
        "template_id": "thirteen_f_spotlight", "source_ref": "news:thirteen_f_spotlight:0001067983:2026Q3:NWT",
        "output": {}, "fact_sheet": {"selection": {"trail": [{"series": "thirteen_f_spotlight", "outcome": "chosen"}]}}})
    tue = date(2026, 11, 24)
    monkeypatch.setattr(ss, "_today_et", lambda: tue)
    tue_news = TF.FakeNews({})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=tue_news)
    rid = _lane_run(sb, tue)
    await svc.kick(rid, claim=_holder(svc, rid))
    insider = next(c for c in tue_news.calls if c["series"] == "insider_spotlight")
    assert insider["kw"]["spotlight_checked_through"] == date(2026, 11, 18)
    window = news_rules.spotlight_window(tue, insider["kw"]["spotlight_checked_through"], tue)
    assert window == (date(2026, 11, 19), date(2026, 11, 23))                  # Thursday through Monday


@pytest.mark.asyncio
async def test_c0_a_quiet_no_new_window_day_falls_through_and_stays_quiet(spot_world, monkeypatch):
    """The Friday-holiday Saturday (Dec 26, 2026: Christmas closed Friday, the newest filing is Thursday's): the insider
    spotlight answers `insider_spotlight_no_new_window` — a content skip. The chain falls through to its next series,
    the trail records no window (nothing was checked), and a day that ends in the skip is quiet in the nightly check."""
    from app.services.marketing import digest_service as ds

    TF = _tf()
    sb, runs = spot_world
    _ship_the_spotlight_pair(monkeypatch)
    sat = date(2026, 12, 26)
    monkeypatch.setattr(ss, "_today_et", lambda: sat)
    news = TF.FakeNews({"insider_spotlight": TF.cands("insider_spotlight", skip="insider_spotlight_no_new_window")})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, sat)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["reason"] == "nothing_qualified"
    assert [c["series"] for c in news.calls] == ["thirteen_f_spotlight", "insider_spotlight", "money_map"]
    entry = _insider_entry(_script_row(sb, rid))
    assert entry == {"series": "insider_spotlight", "outcome": "no_candidates",
                     "reason": "insider_spotlight_no_new_window"}
    run = {**_run_row(sb, rid), "status": "skipped",
           "metadata": {**_run_row(sb, rid)["metadata"], "skip_reason": "nothing_qualified"}}
    assert ds.evaluate_run_health(run, run_date=sat, posting_day=True, now=datetime.now(timezone.utc), max_attempts=6,
                                  stale_seconds=2700, lane="spotlight") is None


@pytest.mark.parametrize("output, name", [
    ({"image_spec": {"layout": "spotlight", "header": {"logo": "CTSO"}},
      "logo_refs": [{"key": "CTSO", "name": "  Contoso  Corp "}]}, "Contoso Corp"),
    ({"image_spec": {"layout": "rows", "sections": []}, "logo_refs": [{"key": "AAPL", "name": "Apple"},
                                                                       {"key": "MSFT", "name": "Microsoft"}]}, "Apple"),
    ({"image_spec": {"layout": "spotlight", "header": {"logo": "CTSO"}}, "logo_refs": [{"key": "CTSO"}]}, None),
    ({"image_spec": {"layout": "spotlight", "header": {"logo": "CTSO"}},
      "logo_refs": [{"key": "OTHER", "name": "Other"}]}, None),
    ({"image_spec": {"layout": "spotlight", "header": {"logo": "CTSO"}},
      "logo_refs": [{"key": "CTSO", "name": "  "}]}, None),
    ({"hook": "a lesson"}, None), (None, None), ("junk", None),
    ({"logo_refs": [{"key": "X", "name": "N" * 200}]}, "N" * 80),
])
def test_spotlight_b2_lead_company_name_is_the_leads_logo_ref_name(output, name):
    assert ss.lead_company_name(output) == name


def test_spotlight_b2_the_context_series_is_the_insider_spotlight():
    assert ss.SPOTLIGHT_CONTEXT_SERIES == "insider_spotlight" and ss.SPOTLIGHT_CONTEXT_SERIES in selection.SPOTLIGHT_SERIES
    assert ss.SERIES_BUDGET_SECONDS["insider_spotlight"] == 60.0
    assert ss.SERIES_BUDGET_SECONDS["thirteen_f_spotlight"] == 120.0


# ── news-v22 (B5): the first-line prefix switch is read ONCE per build and frozen ─────────────────────────────────────


@pytest.mark.parametrize("at_build, at_posts", [(True, False), (False, True)], ids=["on_then_off", "off_then_on"])
@pytest.mark.asyncio
async def test_spotlight_b5_the_first_line_prefix_switch_is_read_once_per_build_and_frozen(spot_world, monkeypatch,
                                                                                           at_build, at_posts):
    """MARKETING_FIRST_LINE_PREFIX is read ONCE when the template build starts (beside MARKETING_X_ALLOW_URLS) and frozen
    into the accepted output (`first_line_prefix_on`). Flipped while the series is being asked, the stored output keeps
    the build's first value — on: every caption the prefix fits opens on "🚨 BREAKING:" (every person of the insider
    list filed the business day before the Thursday run — owner decisions 2026-11-20: a list is BREAKING only when all
    are); off: no prefix anywhere. Flipped again before create_posts, the posts are
    recorded with exactly the stored captions: the re-check re-composes from the frozen value, never today's switch."""
    import test_marketing_news_templates as NT

    TF = _tf()
    sb, runs = spot_world
    _ship_the_spotlight_pair(monkeypatch)
    monkeypatch.setattr(mrs.settings, "MARKETING_X_IMAGES", False)
    monkeypatch.setattr(mrs.settings, "MARKETING_FIRST_LINE_PREFIX", at_build)

    async def answer():
        monkeypatch.setattr(mrs.settings, "MARKETING_FIRST_LINE_PREFIX", not at_build)   # flipped mid-build
        return TF.cands("insider_spotlight", NT.isp_n(3))

    news = TF.FakeNews({"insider_spotlight": answer})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, SPOT_THU)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "accepted" and state["template_id"] == "insider_spotlight", state
    assert mrs.settings.MARKETING_FIRST_LINE_PREFIX is (not at_build)     # the flip happened during the build
    row = _script_row(sb, rid)
    out = row["output"]
    assert out["first_line_prefix_on"] is at_build
    breaking = news_templates.FIRST_LINE_PREFIXES["insider.breaking"]
    assert out["first_line_prefix"] == (breaking if at_build else None)
    assert out["captions"]["x"].startswith(f"{breaking} ") is at_build
    assert not [b for b in out["captions"].values() if breaking in b] or at_build
    assert news_templates.revalidate(out, fact_sheet=row["fact_sheet"], run_date=SPOT_THU) == []
    # create_posts under the OTHER switch value: recorded with the stored (frozen) captions, pending review
    monkeypatch.setattr(mrs.settings, "MARKETING_FIRST_LINE_PREFIX", at_posts)
    assert out["post_formats"]["x"] == "text"
    (post,) = await runs.create_posts(rid, [{"platform": "x", "format": "text"}], claim=_holder(svc, rid))
    assert post["status"] == "pending_review" and post["caption"] == out["posts"]["x"]["caption"]
    assert post["caption"].startswith(f"{breaking} ") is at_build
    assert post["idempotency_key"] == f"{SPOT_THU.isoformat()}:spotlight:x:text"


def test_spotlight_b5_the_build_reads_the_switch_strictly_and_once():
    """Source pin (comments stripped): `_build_template_day` reads MARKETING_FIRST_LINE_PREFIX exactly once, strictly
    (`is True`: anything but a real True is off), and hands that one value to every candidate's compose."""
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(ss.MarketingScriptService._build_template_day))
    tree = ast.parse(src)
    reads = [n for n in ast.walk(tree) if isinstance(n, ast.Attribute) and n.attr == "MARKETING_FIRST_LINE_PREFIX"]
    assert len(reads) == 1
    compares = [n for n in ast.walk(tree) if isinstance(n, ast.Compare) and isinstance(n.left, ast.Attribute)
                and n.left.attr == "MARKETING_FIRST_LINE_PREFIX"]
    assert len(compares) == 1 and isinstance(compares[0].ops[0], ast.Is)
    assert isinstance(compares[0].comparators[0], ast.Constant) and compares[0].comparators[0].value is True
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "_compose_candidate"]
    assert len(calls) == 1 and any(k.arg == "first_line_prefix" for k in calls[0].keywords)
    # never re-read per candidate (the AST, so the docstring's mention of the name cannot hide a read)
    per_candidate = ast.parse(textwrap.dedent(inspect.getsource(ss.MarketingScriptService._compose_candidate)))
    assert not [n for n in ast.walk(per_candidate)
                if isinstance(n, ast.Attribute) and n.attr == "MARKETING_FIRST_LINE_PREFIX"]


# ══ Per-post art (workstream C, slice C3, 2026-11-16): the read-back and the freeze's one holder read ═══════════════
#
# The worker's read-back carries `art` — url, sha256, bytes, width, height ONLY — exactly while MARKETING_ART_ENABLED
# is on, read at CALL time (a real kill switch), and None for a lesson, a failed record or a record that does not
# validate. `_frozen_output` records its ONE holder read for the art gate (`art_holder`) with no extra statement.

from app.services.marketing import post_art  # noqa: E402

_ART_SB = "https://sb.example"


def _ok_art(sha: str = "d" * 64, **over) -> Dict[str, Any]:
    rec = {"version": post_art.ART_VERSION, "status": "ok", "reason": None, "surfaces": ["cover"],
           "subject": "a heavy cast-iron flywheel", "treatment": "duotone_press",
           "models": {"brief": "claude-opus-5-5", "check": "claude-opus-5-5", "image": "gemini-3-pro-image"},
           "usage": {"images": 1, "images_uncertain": 0, "estimated_micros": 134_000},
           "url": post_art.expected_art_url(sha), "sha256": sha, "bytes": 480_000, "width": 1376, "height": 774}
    rec.update(over)
    return rec


@pytest.fixture
def art_on(monkeypatch):
    from test_marketing_post_art import keep_art_on_money_map

    monkeypatch.setattr(mrs.settings, "SUPABASE_URL", _ART_SB)
    monkeypatch.setattr(mrs.settings, "MARKETING_MEDIA_BUCKET", "marketing-media")
    monkeypatch.setattr(mrs.settings, "MARKETING_ART_ENABLED", True)
    # owner decision 2026-11-20 ("use it nowhere for now"): no shipped series carries art; the kept code is exercised
    # on the Money Map, put back into the art set for the test only (shipped sets: test_marketing_post_art)
    keep_art_on_money_map(monkeypatch)


def _art_composed() -> Dict[str, Any]:
    """A Money Map's accepted output — the series the kept art code was built for (its cover); owner decision
    2026-11-20 ("use it nowhere for now"): shipped, no series carries art, so `art_on` puts the Money Map back."""
    import test_marketing_news_templates as NT

    out = news_templates.compose(NT.costco(), run_date=NT.THU, store_state="live", allow_x_url=False)
    out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None, "bytes": None,
                     "width": None, "height": None} for r in news_templates.logo_refs(out)]
    return out


def test_the_read_back_sends_the_art_five_keys_only_while_the_switch_is_on(art_on, monkeypatch):
    from app.schemas.marketing import WorkerScript

    out = {**_art_composed(), "art": _ok_art()}
    sent = ss.worker_script(out, run_id="r-art")
    assert sent["art"] == {k: out["art"][k] for k in ("url", "sha256", "bytes", "width", "height")}
    wire = json.loads(WorkerScript(**sent).model_dump_json())
    assert wire["art"] == sent["art"]
    blob = json.dumps(wire).lower()
    for word in ("claude", "gemini", "subject", "flywheel", "usage", "micros", "treatment"):
        assert word not in blob, word                       # never a model, the subject or the spend
    # the kill switch, read at CALL time: the very same accepted output, the switch off → no art
    monkeypatch.setattr(mrs.settings, "MARKETING_ART_ENABLED", False)
    assert ss.worker_script(out, run_id="r-art")["art"] is None
    monkeypatch.setattr(mrs.settings, "MARKETING_ART_ENABLED", "true")      # only a real True is on
    assert ss.worker_script(out, run_id="r-art")["art"] is None


@pytest.mark.parametrize("case", ["no_art", "failed", "lesson", "insider", "bad_url", "bad_size"])
def test_the_read_back_sends_no_art_that_may_not_be_drawn(art_on, caplog, case):
    base = _art_composed()
    out = {
        "no_art": base,
        "failed": {**base, "art": {**_ok_art(), "status": "failed", "reason": "check_failed"}},
        "lesson": {**_package(KEY), "art": _ok_art()},
        # owner decisions 2026-11-20 ("Drop it from insider posts"): art on a Form 4 week is never drawn
        "insider": {**_b2_composed(), "art": _ok_art()},
        "bad_url": {**base, "art": _ok_art(url="https://elsewhere.example/x.jpg")},
        "bad_size": {**base, "art": _ok_art(width=1376, height=1376)},
    }[case]
    with caplog.at_level(logging.ERROR):
        assert ss.worker_script(out, run_id="r-art")["art"] is None
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and "post art" in r.getMessage()]
    assert bool(errors) is (case in ("lesson", "insider", "bad_url", "bad_size"))   # a record where none belongs is loud


def test_a_raise_reading_the_art_is_no_art_never_a_failed_read_back(art_on, monkeypatch, caplog):
    def boom(_output):
        raise RuntimeError("a bug in the art reader")

    monkeypatch.setattr(ss.post_art, "worker_art", boom)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        sent = ss.worker_script({**_b2_composed(), "art": _ok_art()}, run_id="r-art")
    assert sent["art"] is None and sent["opening_card"] is not None
    assert any("art could not be read for the worker" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("image_posts", [False, True])
@pytest.mark.asyncio
async def test_the_freeze_records_its_one_holder_read_for_the_art_gate(world, monkeypatch, image_posts):
    """`art_holder` (a template build with the art switch on) receives the freeze's ONE holder read — made for it even
    while image posts and video features are off — and the stored output is the same with or without it. None: the
    reads are exactly today's. A lesson never reads for art."""
    sb, runs = world
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]))
    rid = _b2_run(sb, _B2_DAY, caps=[*_B2_CAPS, "post_art"])
    reads: List[str] = []
    real = runs.get_run

    async def counting(run_id):
        reads.append(run_id)
        return await real(run_id)

    monkeypatch.setattr(runs, "get_run", counting)
    plain = await svc._frozen_output(_b2_composed(), _B2_DAY, run_id=rid, gen_id="g", image_posts=image_posts,
                                     fact_sheet=_b2_sheet())
    assert len(reads) == (1 if image_posts else 0)
    reads.clear()
    holder: Dict[str, Any] = {}
    stored = await svc._frozen_output(_b2_composed(), _B2_DAY, run_id=rid, gen_id="g", image_posts=image_posts,
                                      fact_sheet=_b2_sheet(), art_holder=holder)
    assert reads == [rid]                                      # ONE read, whatever the other switches say
    run, error = holder["holder"]
    assert error is None and run["id"] == rid and "post_art" in mrs.run_worker_capabilities(run)
    assert json.dumps(stored, sort_keys=True) == json.dumps(plain, sort_keys=True)
    # a lesson package: no read for art (the art step never runs for a lesson)
    reads.clear()
    lesson_holder: Dict[str, Any] = {}
    await svc._frozen_output(_package(KEY), POSTING_DAY, run_id=rid, gen_id="g", image_posts=False,
                             art_holder=lesson_holder)
    assert reads == [] and lesson_holder == {}
    # a failed read is recorded as such (the art gate then says `ledger_unreadable`), the freeze goes on
    async def failing(run_id):
        raise mrs.MarketingRunError("PostgREST 503")

    monkeypatch.setattr(runs, "get_run", failing)
    broken: Dict[str, Any] = {}
    await svc._frozen_output(_b2_composed(), _B2_DAY, run_id=rid, gen_id="g", image_posts=False,
                             fact_sheet=_b2_sheet(), art_holder=broken)
    assert broken["holder"][0] is None and isinstance(broken["holder"][1], mrs.MarketingRunError)


# ══ news-v23 (2026-11-19): the ONE 13F list, read back by a worker older than look_v23 ═════════════════════════════


def _f13_list_output(factory: str) -> Dict[str, Any]:
    import test_marketing_news_templates as NT

    rec = getattr(NT, factory)()
    run_day = NT.SPOT_SAT if rec.series == "thirteen_f_spotlight" else NT.TUE
    out = news_templates.compose(rec, run_date=run_day, store_state="live", allow_x_url=False)
    out["post_formats"] = {p: "image" for p in out["posts"]}
    out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in out["logo_refs"]]
    return json.loads(json.dumps(out))


@pytest.mark.parametrize("factory", ["f13s_list8", "berkshire"])
def test_a_pre_v23_holder_gets_a_13f_list_its_walkers_take_and_the_server_still_allows(factory, caplog):
    """A worker older than look_v23 refuses, in its CLOSED walkers, the 13F list cover (`list`), a row's `chip` and a
    section of more than five rows: it is sent today's opening card, the rows without chips, at most five of them and
    then no "+N more" line — every string left one the server allows for this run, each step with a WARNING. A
    look_v23 holder gets the stored output untouched."""
    out = _f13_list_output(factory)
    stored = copy.deepcopy(out)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        old = ss.worker_script(out, run_id="r", capabilities=["news_templates", "post_image", "layouts_2b"])
    assert out == stored                                                       # the stored output is never touched
    assert "list" in stored["opening_card"] and "list" not in old["opening_card"]
    assert {k: v for k, v in stored["opening_card"].items() if k != "list"} == old["opening_card"]
    sec, was = old["image_spec"]["sections"][0], stored["image_spec"]["sections"][0]
    assert len(sec["rows"]) == min(len(was["rows"]), ss.PRE_V23_MAX_SECTION_ROWS) == min(len(was["rows"]), 5)
    assert all(set(r) == {"logo", "cells"} for r in sec["rows"])
    assert ("more" in sec) == ("more" in was and len(was["rows"]) <= 5)
    allowed = set(template_onscreen.image_strings(stored["image_spec"], stored["logos"]))
    assert set(template_onscreen.image_strings(old["image_spec"], stored["logos"])) <= allowed
    assert set(template_onscreen.opening_strings(old["opening_card"], stored["logos"])) <= set(
        template_onscreen.opening_strings(stored["opening_card"], stored["logos"]))
    assert ("its list cover (13F / Congress Count / Form 4 roundup) is sent without the list" in caplog.text
            and "ticker chips" in caplog.text)
    new = ss.worker_script(out, run_id="r", capabilities=["news_templates", "post_image", "layouts_2b", "look_v23"])
    assert new["image_spec"] == stored["image_spec"] and new["opening_card"] == stored["opening_card"]


def test_the_8_holding_list_is_the_case_that_needs_the_cut():
    """Anti-vacuity for the test above: the eight-holding spotlight's picture really holds six rows and a "+N more"
    line, more than the older walker's five."""
    sec = _f13_list_output("f13s_list8")["image_spec"]["sections"][0]
    assert len(sec["rows"]) == 6 > ss.PRE_V23_MAX_SECTION_ROWS and sec["more"] == "+2 more changes in the caption"


@pytest.mark.parametrize("factory, lead", [("berkshire", "STZ"), ("f13s_list_listed", "NWT"), ("f13s_list5", "NWT")])
def test_a_13f_list_leads_with_its_first_row_never_the_listed_filers_plate(factory, lead):
    """Rule (b) of the cross-lane exclusion compares LEADS: the 13F list's lead is its first (largest) holding change,
    as before the restyle — a listed filer's plate in the picture's header (Berkshire's BRK-B) is never the lead.
    Mutation-checked by hand (2026-11-19): without the 13F branch in `lead_company` the listed cases read the filer."""
    out = _f13_list_output(factory)
    assert out["image_spec"]["sections"][0]["rows"][0]["logo"] == lead == ss.lead_company(out)
    assert ss.lead_company_name(out) == next(r["name"] for r in out["logo_refs"] if r["key"] == lead)
    if out["image_spec"].get("header"):
        assert out["image_spec"]["header"]["logo"] != lead                 # anti-vacuity: a plate was there


# ══ news-v23 (2026-11-19): the earnings table and the Congress list, read back by a worker older than look_v23 ═══════


def _v23_list_output(series: str) -> Dict[str, Any]:
    import test_marketing_news_templates as NT

    rec, day = (NT.congress(), NT.CONGRESS_RUN) if series == "congress_count" else (NT.earnings(others=3),
                                                                                       NT.EARNINGS_RUN)
    out = news_templates.compose(rec, run_date=day, store_state="live", allow_x_url=False)
    out["post_formats"] = {p: "image" for p in out["posts"]}
    out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in out["logo_refs"]]
    return json.loads(json.dumps(out))


def test_a_pre_v23_holder_gets_the_congress_list_without_its_chips_and_list_cover(caplog):
    """The Congress Count's ONE list (news-v23) reaches an older worker as rows of `logo` + `cells` (no chip) and today's
    opening card (no `list`) — every string still one the server allows for the run; a look_v23 holder gets it as
    stored."""
    out = _v23_list_output("congress_count")
    stored = copy.deepcopy(out)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        old = ss.worker_script(out, run_id="r", capabilities=["news_templates", "post_image", "layouts_2b"])
    assert out == stored
    sec = old["image_spec"]["sections"][0]
    assert all(set(r) == {"logo", "cells"} for r in sec["rows"]) and len(sec["rows"]) == 3
    assert "list" in stored["opening_card"] and "list" not in old["opening_card"]
    allowed = set(template_onscreen.image_strings(stored["image_spec"], stored["logos"]))
    assert set(template_onscreen.image_strings(old["image_spec"], stored["logos"])) <= allowed
    assert set(template_onscreen.opening_strings(old["opening_card"], stored["logos"])) <= set(
        template_onscreen.opening_strings(stored["opening_card"], stored["logos"]))
    assert "its list cover (13F / Congress Count / Form 4 roundup)" in caplog.text
    new = ss.worker_script(out, run_id="r", capabilities=["news_templates", "post_image", "layouts_2b", "look_v23"])
    assert new["image_spec"] == stored["image_spec"] and new["opening_card"] == stored["opening_card"]


#: What an OLDER worker's closed walker takes (news-v22 — the worker on main before `look_v23`:
#: marketing/news_layouts.py `_w_rows` reads a row as `obj(raw, path, ("cells",), ("logo",))` and a section as
#: `obj(section, path, ("rows",), ("heading", "more"))`, and `cards.LAYOUTS` holds these five): any other key or layout
#: raises in its walk, and its render fails (render.RenderInputError) — loudly, the run recorded failed.
_OLDER_ROW_KEYS = frozenset({"cells", "logo"})
_OLDER_SECTION_KEYS = frozenset({"rows", "heading", "more"})
_OLDER_LAYOUTS = frozenset({"rows", "spotlight", "pair", "bars", "grid"})
#: An older worker's claim: every capability but `look_v23` and `theme_angles`.
_OLDER_CAPS = ["news_templates", "post_image", "layouts_2b", "video_v2"]


def test_final_review_an_older_worker_reclaiming_an_earnings_day_gets_the_table_and_fails_loudly():
    """Final review of 2026-11-26 (a worker rollback mid-run): an older worker re-claiming an earnings day a newer worker
    built used to get rows of names alone — the `table` (the row's ONLY figures) and the section's `columns` cut — a
    picture of reporters with no figure, which its walker drew and the server's allow-list accepted (a subset). The cut
    now keeps them (`template_onscreen.PRE_V23_ROW_DROPS`): that walker refuses the picture and the render fails loudly —
    no picture rather than one without its meaning. Only the ticker chip is dropped. Mutation-checked by hand: "table"
    back in PRE_V23_ROW_DROPS turns this red."""
    out = _v23_list_output("earnings")
    sec = out["image_spec"]["sections"][0]
    assert "columns" in sec and all("table" in r and "chip" in r for r in sec["rows"])           # anti-vacuity
    old = ss.worker_script(out, run_id="r", capabilities=_OLDER_CAPS)
    cut = old["image_spec"]["sections"][0]
    assert cut["columns"] == sec["columns"] and len(cut["rows"]) == len(sec["rows"]) == 4
    assert [r["table"] for r in cut["rows"]] == [r["table"] for r in sec["rows"]]
    assert all("chip" not in r for r in cut["rows"])
    # its closed walker refuses it: a loud render failure, never names without figures
    assert set(cut) - _OLDER_SECTION_KEYS and all(set(r) - _OLDER_ROW_KEYS for r in cut["rows"])
    assert ss._pre_v23_section(sec)["columns"] == sec["columns"]


def test_the_earnings_table_is_kept_off_the_chain_of_a_holder_without_look_v23(caplog):
    """The earnings post's picture IS the table, which an older worker cannot draw: `_without_look_series` drops
    `earnings` from that holder's chain (the day falls through to its next series), with a WARNING naming it; a
    look_v23 holder, or a chain without earnings, is left as it is. Mutation-checked by hand (2026-11-20): with
    LOOK_V23_SERIES empty the first assertion fails."""
    old_run = {"id": "r1", "metadata": {"worker_capabilities": ["news_templates", "post_image", "layouts_2b"]}}
    new_run = {"id": "r2", "metadata": {"worker_capabilities": ["news_templates", "post_image", "layouts_2b",
                                                                "look_v23"]}}
    chain = ("earnings", "money_map")
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        assert ss._without_look_series(chain, old_run, lane=ss.RUN_LANE_MAIN) == ("money_map",)
    assert "did not declare 'look_v23'" in caplog.text and "['earnings']" in caplog.text
    assert ss._without_look_series(chain, new_run, lane=ss.RUN_LANE_MAIN) == chain
    assert ss._without_look_series(("money_map", "congress_count"), old_run, lane=ss.RUN_LANE_MAIN) == (
        "money_map", "congress_count")
    # news-v23 (company stakes): one investor's list draws each stake's basis word as its row's `label` — an older
    # walker refuses the key, and without it an amount would stand without its basis: kept off that chain too
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        assert ss._without_look_series(("company_stakes", "theme_explainer"), old_run, lane=ss.RUN_LANE_MAIN) == (
            "theme_explainer",)
    assert "['company_stakes']" in caplog.text
    # owner decisions 2026-11-20: the insider LIST (its rows' grey detail line, its two-line headline) is look_v23's too
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        assert ss._without_look_series(("insider_spotlight", "theme_explainer"), old_run,
                                       lane=ss.RUN_LANE_SPOTLIGHT) == ("theme_explainer",)
    assert "['insider_spotlight']" in caplog.text
    assert ss.LOOK_V23_SERIES == frozenset({"earnings", "company_stakes", "insider_spotlight"})


def test_final_review_an_older_worker_reclaiming_a_stakes_day_keeps_every_basis_and_fails_loudly():
    """Final review of 2026-11-26 (a worker rollback mid-run): an older worker re-claiming a company-stakes day a newer
    worker built used to get each row WITHOUT its `label` — the stake's basis ("committed up to", "fair value", …), the
    one place the picture states it — so a commitment or a fair value drew as a bare "$5B", read as money invested, and
    the server's allow-list accepted it (a subset). The cut now keeps every basis label
    (`template_onscreen.PRE_V23_ROW_DROPS`): that worker's closed walker refuses the picture and the render fails loudly
    — never an amount without its basis. Only the ticker chip is dropped; the list cover goes as before. Mutation-checked
    by hand: "label" back in PRE_V23_ROW_DROPS turns this red."""
    import test_marketing_news_templates as NT

    for factory in (NT.berkshire_mixed, NT.nvidia_stakes_more):          # carrying + fair value; four "invested"
        out = news_templates.compose(factory(), run_date=NT.STAKES_RUN, store_state="live", allow_x_url=False)
        out["post_formats"] = {p: "image" for p in out["posts"]}
        out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in out["logo_refs"]]
        out = json.loads(json.dumps(out))
        _older_stakes_reclaim(out)
    mixed = news_templates.compose(NT.berkshire_mixed(), run_date=NT.STAKES_RUN, store_state="live", allow_x_url=False)
    assert {r["label"] for r in mixed["image_spec"]["sections"][0]["rows"]} - {"invested"}     # not vacuous


def _older_stakes_reclaim(out: Dict[str, Any]) -> None:
    sec = out["image_spec"]["sections"][0]
    assert all("label" in r and "chip" in r for r in sec["rows"])                        # anti-vacuity
    old = ss.worker_script(out, run_id="r", capabilities=_OLDER_CAPS)
    cut = old["image_spec"]["sections"][0]
    assert [r["label"] for r in cut["rows"]] == [r["label"] for r in sec["rows"]] and len(cut["rows"]) == len(sec["rows"])
    assert all("chip" not in r for r in cut["rows"])
    assert all(set(r) - _OLDER_ROW_KEYS for r in cut["rows"])          # its closed walker refuses it: a loud failure
    assert "list" not in old["opening_card"]
    allowed = set(template_onscreen.image_strings(out["image_spec"], out["logos"]))
    assert set(template_onscreen.image_strings(old["image_spec"], out["logos"])) <= allowed


@pytest.mark.parametrize("factory", ["power_machine", "silicon_growth", "cyber_insiders", "battlefield_congress"])
def test_final_review_an_older_worker_reclaiming_a_theme_angle_day_gets_the_layout_it_refuses(factory):
    """Final review of 2026-11-26: a theme angle 2-5 picture (`ranked` / `figures` / `ledger` / `tally`) a newer worker
    built and froze, re-claimed by a worker without `theme_angles`, is sent UNCHANGED — that worker's walker does not
    know the layout and its render fails loudly; nothing guesses a picture for it (the write-time freeze to TEXT covers
    only a holder that held the run while it was written)."""
    import test_marketing_news_templates as NT

    out = news_templates.compose(getattr(NT, factory)(), run_date=NT.THEME_WED, store_state="live", allow_x_url=False)
    out["post_formats"] = {p: "image" for p in out["posts"]}
    out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in out["logo_refs"]]
    out = json.loads(json.dumps(out))
    assert out["image_spec"]["layout"] not in _OLDER_LAYOUTS                             # anti-vacuity: angles 2-5
    old = ss.worker_script(out, run_id="r", capabilities=[*_OLDER_CAPS, "look_v23"])
    assert old["image_spec"] == out["image_spec"]



# ══ 2026-11-20: the Money Map cover's figure label and the Form 4 roundup's list cover, read back by an older worker ══


def _r5_output(factory: str, run_day: str) -> Dict[str, Any]:
    import test_marketing_news_templates as NT

    out = news_templates.compose(getattr(NT, factory)(), run_date=getattr(NT, run_day), store_state="live",
                                 allow_x_url=False)
    out["post_formats"] = {p: "image" for p in out["posts"]}
    out["logos"] = [{"key": r["key"], "name": r["name"], "url": None, "sha256": None} for r in out["logo_refs"]]
    return json.loads(json.dumps(out))


@pytest.mark.parametrize("factory", ["costco", "rivian"])
def test_r5_a_pre_v23_holder_gets_the_money_map_cover_without_its_label_and_its_figure(factory, caplog):
    """An older worker's CLOSED opening-card walker refuses `figure_label` (news-v23, 2026-11-20): it is sent the card
    WITHOUT the label AND without the figure the label explains (a bare "$2.94" beside "How Costco makes money" would
    read as something else) — the kicker, the logo, the ticker chip and the headline, a subset of the strings the server
    allows, with a WARNING. A look_v23 holder gets the stored card. Mutation-checked by hand (2026-11-20): with only the
    label dropped (the figure kept) the "figure" assertion turned red."""
    out = _r5_output(factory, "THU")
    stored = copy.deepcopy(out)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        old = ss.worker_script(out, run_id="r", capabilities=["news_templates", "post_image", "layouts_2b"])
    assert out == stored
    assert {"figure", "figure_label"} <= set(stored["opening_card"])                              # anti-vacuity
    # owner decision 2026-11-20: a LOSS year's cover is its costs card (its `costs` and the `subtitle` over it) — an
    # older worker gets neither (today's plain opening card); a profit year never carries them
    cover = {"costs", "subtitle"}
    assert (cover <= set(stored["opening_card"])) == (factory == "rivian")
    assert old["opening_card"] == {k: v for k, v in stored["opening_card"].items()
                                   if k not in ("figure", "figure_label", *cover)}
    assert set(template_onscreen.opening_strings(old["opening_card"], stored["logos"])) <= set(
        template_onscreen.opening_strings(stored["opening_card"], stored["logos"]))
    assert "without the figure and the label its walker would refuse" in caplog.text
    assert ("the loss year's costs cover its walker would refuse" in caplog.text) == (factory == "rivian")
    new = ss.worker_script(out, run_id="r", capabilities=["news_templates", "post_image", "layouts_2b", "look_v23"])
    assert new["opening_card"] == stored["opening_card"]


@pytest.mark.parametrize("factory", ["costco", "rivian"])
def test_loss_a_pre_v23_holder_gets_the_money_map_picture_without_its_costs_card(factory, caplog):
    """Owner decision 2026-11-20 ("A: costs vs revenue"): a LOSS year's picture carries a `costs` card an older worker's
    CLOSED `bars` walker refuses — such a holder is sent the picture WITHOUT it (and without the plate's chip), and
    draws today's flow card and callout: every string still one the server allows. A look_v23 holder gets the stored
    spec, costs card and all. A profit year never carries one. Mutation-checked by hand: with `costs` kept for the
    older holder, the "costs" assertion turned red."""
    out = _r5_output(factory, "THU")
    stored = copy.deepcopy(out)
    loss = factory == "rivian"
    assert ("costs" in stored["image_spec"]) == loss                                          # anti-vacuity
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        old = ss.worker_script(out, run_id="r", capabilities=["news_templates", "post_image", "layouts_2b"])
    assert out == stored and "costs" not in old["image_spec"] and "chip" not in old["image_spec"]["header"]
    assert old["image_spec"] == {**{k: v for k, v in stored["image_spec"].items() if k != "costs"},
                                 "header": {k: v for k, v in stored["image_spec"]["header"].items() if k != "chip"}}
    assert set(template_onscreen.image_strings(old["image_spec"], stored["logos"])) <= set(
        template_onscreen.image_strings(stored["image_spec"], stored["logos"]))
    assert ("a loss year's costs card" in caplog.text) is True                 # the warning names it either way
    new = ss.worker_script(out, run_id="r", capabilities=["news_templates", "post_image", "layouts_2b", "look_v23"])
    assert new["image_spec"] == stored["image_spec"]


def test_r5_a_pre_v23_holder_gets_the_form4_roundup_cover_without_its_list(caplog):
    """The Form 4 roundup's list cover (news-v23, 2026-11-20) reaches an older worker as the plain opening card — the
    kicker, the lead row's logo and the roundup's headline (no chip, no figure: none was stored) — every string one the
    server allows; a look_v23 holder gets the list."""
    out = _r5_output("ceo_roundup", "MON")
    stored = copy.deepcopy(out)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        old = ss.worker_script(out, run_id="r", capabilities=["news_templates", "post_image", "layouts_2b"])
    assert "list" in stored["opening_card"] and old["opening_card"] == {
        "kicker": stored["opening_card"]["kicker"], "logos": ["GME"], "headline": stored["opening_card"]["headline"]}
    assert set(template_onscreen.opening_strings(old["opening_card"], stored["logos"])) <= set(
        template_onscreen.opening_strings(stored["opening_card"], stored["logos"]))
    assert "Form 4 roundup" in caplog.text
    new = ss.worker_script(out, run_id="r", capabilities=["news_templates", "post_image", "layouts_2b", "look_v23"])
    assert new["opening_card"] == stored["opening_card"]


# ── owner decisions 2026-11-20: the theme is the quiet Form 4 week's fallback in BOTH lanes ─────────────────────────────
# "i pick option 1: 3-5, but if it's a quite week - if we don't have enough data, then use different topic like posting
# the theme (cyber war, AI chip,....)". The 28-day rule (`company_news_rules.THEME_REPEAT_DAYS`) is read from the theme
# ledger of BOTH lanes, so one theme never posts twice inside 28 days, whichever lane posted it first.


@pytest.mark.parametrize("posted_lane, posted_day, run_lane, run_day", [
    # a Monday's quiet Form 4 week posted a theme; the NEXT week's Thursday spotlight (its insider list too few) must
    # not repeat it (owner decision 2026-11-27: the same week's Thursday never asks — one backup theme a week)
    ("main", date(2026, 11, 9), "spotlight", SPOT_THU),
    # the spotlight posted it on Saturday; the next Monday's main lane (a quiet week again) must not repeat it
    ("spotlight", SPOT_SAT, "main", date(2026, 11, 23)),
    # 26 days before: a Monday main-lane theme, then a Saturday spotlight inside the four weeks
    ("main", date(2026, 10, 26), "spotlight", date(2026, 11, 22) - timedelta(days=1)),
], ids=["main_then_spotlight", "spotlight_then_main", "four_weeks_less_a_day"])
@pytest.mark.asyncio
async def test_theme_fallback_never_posts_one_theme_twice_inside_28_days_across_lanes(
        spot_world, monkeypatch, posted_lane, posted_day, run_lane, run_day):
    """The build hands the theme collector the theme ledger of BOTH lanes (`theme_scripts_between`, THEME_HISTORY_DAYS
    back, the run day included), and the REAL collector refuses the theme the other lane posted fewer than 28 days
    before (`already_posted`, before any read) — fed exactly what this build passed. Mutation-checked by hand
    (2026-11-20): `theme_scripts_between` reading the caller's lane only turned this red."""
    import importlib

    TA = importlib.import_module("test_marketing_company_news_adapter")
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: run_day)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES",
                        "ceo_buys,insider_buys,money_map,insider_spotlight,theme_explainer")
    monkeypatch.setattr(mrs.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,thu,sat")
    ref = f"news:theme_explainer:ai-chips:make_money:{posted_day.isoformat()}"
    _accepted(sb, posted_day, "theme_explainer", ref, None, None, lane=posted_lane)
    news = TF.FakeNews({})                                    # every series quiet: the chain reaches the theme
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, run_day, lane=run_lane)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    asked = [c["series"] for c in news.calls]
    assert "theme_explainer" in asked, asked
    if run_lane == "main" and run_day.weekday() == 0:
        assert asked[:3] == ["ceo_buys", "insider_buys", "theme_explainer"], asked      # right after the Form 4 pair
    if run_lane == "spotlight":
        assert asked[:2] == ["insider_spotlight", "theme_explainer"], asked             # right after the insider list
    (call,) = [c for c in news.calls if c["series"] == "theme_explainer"]
    history = call["kw"]["theme_history"]
    assert ref in history, history
    world = TA.theme_world()
    got = await TA.trun(world, run_date=run_day, theme_history=history)
    assert got.records == () and got.rejections == {"already_posted": 1}
    # anti-vacuity: the same collector posts the theme when the ledger is empty
    TA.A.clear_memo()
    assert [r.slug for r in (await TA.trun(TA.theme_world(), run_date=run_day)).records] == ["ai-chips"]


def test_theme_fallback_the_two_lanes_never_both_ask_for_the_theme_on_one_day(monkeypatch):
    """The same day is C0's (2026-11-16): a day whose MAIN chain holds the theme — every Monday (owner decisions
    2026-11-20, after the Form 4 pair) and every Wednesday it leads — drops it from that day's SPOTLIGHT chain, whatever
    weekdays the spotlight runs on; on any other day only the spotlight asks for it. Ten years, every series on."""
    acf = frozenset({"A", "C", "F"})
    every = frozenset(selection.SHIPPED_SERIES)
    every_day = (0, 1, 2, 3, 4, 5, 6)
    monkeypatch.setattr(selection.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,wed,thu,fri,sat,sun")
    main_days = {0, 2}
    seen = {"main": 0, "spotlight": 0}
    d = date(2026, 1, 1)
    while d < date(2036, 1, 1):
        main = selection.main_lane_series(d, acf, every)
        spot = selection.spotlight_chain(d, classes=acf, news_series=every, weekdays=every_day).chain
        both = "theme_explainer" in main and "theme_explainer" in spot
        assert not both, d
        if "theme_explainer" in main:
            assert d.weekday() in main_days, d
            seen["main"] += 1
        if "theme_explainer" in spot:
            assert d.weekday() not in main_days, d
            seen["spotlight"] += 1
        d += timedelta(days=1)
    assert seen["main"] > 900 and seen["spotlight"] > 2000, seen


# ── owner decision 2026-11-27: ONE quiet-week backup theme a week, across both lanes ─────────────────────────────────────
# "1 backup theme a week (Recommended)" — "At most one quiet-week theme per week across both daily posts, so Wednesday
# almost always keeps a theme." Every theme post but the MAIN lane's Wednesday lead is a backup
# (`selection.theme_is_fallback`); a backup step in an ET week (Monday..Sunday) that already holds one, in either lane,
# falls back `week_cap` before the source is asked (`script_service.theme_week_fallbacks`, from the build's ONE theme
# read). The posts counted are the ones made before this run, plus — for a main run built the next day — the next day's
# spotlight pick (`_cross_lane`'s forward day).

#: The week of 2026-11-16 (Mon) .. 2026-11-22 (Sun), and the next Monday / Tuesday.
CAP_MON, CAP_TUE, CAP_WED, CAP_THU = date(2026, 11, 16), date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19)
CAP_SUN, CAP_NEXT_MON, CAP_NEXT_TUE = date(2026, 11, 22), date(2026, 11, 23), date(2026, 11, 24)
_WEEK_CAP_ENTRY = {"series": "theme_explainer", "outcome": "week_cap", "reason": "theme_fallback_week_used"}


def _theme_pick(sb: FakeSB, lane: str, day: date, slug: str = "ai-chips") -> str:
    """A theme script another run of `lane` picked on `day` (an accepted theme post's ledger row)."""
    ref = f"news:theme_explainer:{slug}:make_money:{day.isoformat()}"
    _accepted(sb, day, "theme_explainer", ref, None, None, lane=lane)
    return ref


async def _theme_cap_day(spot_world, monkeypatch, run_lane: str, run_day: date, picks=(), *, today=None,
                         spotlight_days: str = "tue,thu,sat"):
    """Build `run_lane`'s `run_day` (seen from `today`, the run day by default) with the theme picks `picks` ((lane,
    day) pairs) in the ledger and every series quiet: (the series asked, the day's trail)."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: today or run_day)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES",
                        "ceo_buys,insider_buys,money_map,insider_spotlight,theme_explainer")
    monkeypatch.setattr(mrs.settings, "MARKETING_POST_WEEKDAYS", "mon,tue,wed,thu,fri,sat,sun")
    monkeypatch.setattr(mrs.settings, "MARKETING_SPOTLIGHT_WEEKDAYS", spotlight_days)
    selection._parse_spotlight_weekdays_text.cache_clear()
    for lane, day in picks:
        _theme_pick(sb, lane, day, slug=f"theme-{lane}-{day.isoformat()}")
    news = TF.FakeNews({})                                    # every series quiet: each chain reaches its theme step
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, run_day, lane=run_lane)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    row = _script_row(sb, rid)
    trail = ((row.get("fact_sheet") or {}).get("selection") or {}).get("trail") or []
    return [c["series"] for c in news.calls], trail


def test_week_cap_the_constants_and_the_owner_words():
    from app.services.marketing import digest_service as ds

    assert selection.THEME_FALLBACKS_PER_WEEK == 1
    assert ss.THEME_WEEK_CAP == "week_cap" and ss.THEME_WEEK_CAP in ss.SERIES_OUTCOMES
    assert len(ss.THEME_FALLBACK_WEEK_USED) <= 60                                   # a trail reason is cut at 60
    # a content outcome: a spotlight day that skips behind it is a QUIET day, never an alert
    assert ss.THEME_WEEK_CAP in ds._CONTENT_OUTCOMES and ss.THEME_WEEK_CAP not in ds._INFRA_OUTCOMES
    run = {"metadata": {"series": selection.SKIP, "series_trail": [
        {"series": "insider_spotlight", "outcome": "no_candidates"}, _WEEK_CAP_ENTRY,
        {"series": "skip", "outcome": "no_candidates"}]}}
    assert ds._quiet_nothing_qualified(run)
    assert "this week's one backup theme was already used" in ds._series_note(run)


@pytest.mark.parametrize("picks, run_lane, run_day, today", [
    # Monday's main lane used the week's backup: Thursday's spotlight (the other lane) asks no theme
    ([("main", CAP_MON)], "spotlight", CAP_THU, None),
    # Tuesday's spotlight used it: Thursday's spotlight (the same lane) asks no theme
    ([("spotlight", CAP_TUE)], "spotlight", CAP_THU, None),
    # Monday's main lane used it: Sunday's spotlight is still that week (Monday..Sunday, ET)
    ([("main", CAP_MON)], "spotlight", CAP_SUN, None),
    # order: Monday's main run, resumed on Tuesday after Tuesday's spotlight took the backup, counts that pick too
    ([("spotlight", CAP_TUE)], "main", CAP_MON, CAP_TUE),
], ids=["other_lane", "same_lane", "sunday_same_week", "main_resumed_after_the_spotlight"])
@pytest.mark.asyncio
async def test_week_cap_a_second_backup_in_one_week_is_never_asked(spot_world, monkeypatch, caplog, picks, run_lane,
                                                                    run_day, today):
    """The week already holds a backup theme: the theme step falls back `week_cap` (reason
    `theme_fallback_week_used`) WITHOUT asking the source — nothing reaches its refusal memo — and the chain goes on to
    its next series. Mutation-checked by hand (2026-11-27): with the cap check removed every case asked the theme; with
    the main run's read ending on its run date (no forward day) the last case asked it."""
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        asked, trail = await _theme_cap_day(spot_world, monkeypatch, run_lane, run_day, picks, today=today,
                                            spotlight_days="tue,thu,sat,sun")
    assert "theme_explainer" not in asked, asked
    assert _WEEK_CAP_ENTRY in trail, trail
    nxt = trail[trail.index(_WEEK_CAP_ENTRY) + 1]["series"]
    if run_lane == "main":
        assert asked == ["ceo_buys", "insider_buys", "money_map"] and nxt == "money_map"   # the chain went on
    else:
        assert asked[0] == "insider_spotlight" and nxt in ("money_map", "skip"), (asked, trail)
    assert any("this week's backup theme was already made" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("picks, run_lane, run_day, first", [
    # last week's backups never cap this week's first: Monday main, and a Tuesday spotlight
    ([("main", CAP_MON), ("spotlight", CAP_THU)], "main", CAP_NEXT_MON, False),
    ([("spotlight", CAP_SUN)], "main", CAP_NEXT_MON, False),            # Sunday → Monday is a new week
    ([("main", CAP_MON), ("spotlight", CAP_SUN)], "spotlight", CAP_NEXT_TUE, False),
    # Wednesday's main lead is never capped, whatever backups the week made …
    ([("main", CAP_MON), ("spotlight", CAP_TUE)], "main", CAP_WED, True),
    # … and is no backup itself: Thursday's spotlight may still take the week's one backup
    ([("main", CAP_WED)], "spotlight", CAP_THU, False),
    # a Monday main run built ON Monday cannot see a pick made after it (the week's first backup)
    ([], "main", CAP_MON, False),
], ids=["next_week_after_two", "sunday_then_monday", "next_tuesday", "wednesday_lead_after_backups",
        "the_lead_is_no_backup", "first_backup"])
@pytest.mark.asyncio
async def test_week_cap_the_weeks_first_backup_and_the_wednesday_lead_are_asked(spot_world, monkeypatch, picks,
                                                                                 run_lane, run_day, first):
    """The cap never touches the week's FIRST backup nor Wednesday's main lead: the theme is asked (the lead first in
    its chain), and no `week_cap` step is on the trail. Mutation-checked by hand (2026-11-27): `theme_is_fallback`
    answering True for the Wednesday lead turned "wednesday_lead_after_backups" and "the_lead_is_no_backup" red; a
    rolling seven-day window in place of the Monday..Sunday week turned "sunday_then_monday" and "next_tuesday" red."""
    asked, trail = await _theme_cap_day(spot_world, monkeypatch, run_lane, run_day, picks,
                                        spotlight_days="tue,thu,sat,sun")
    assert "theme_explainer" in asked, (asked, trail)
    assert (asked[0] == "theme_explainer") is first, asked
    assert all(t.get("outcome") != "week_cap" for t in trail), trail


@pytest.mark.asyncio
async def test_week_cap_an_unreadable_theme_ledger_stops_a_backup_day_never_a_guess(spot_world, monkeypatch, caplog):
    """Fail closed, as the 28-day rule does (one read serves both): a failed theme read on a BACKUP day (Thursday's
    spotlight) raises out of the build (ERROR with the run id) — no backup allowed, no script written, the source never
    asked — and the next kick builds the day afresh."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "money_map,insider_spotlight,theme_explainer")
    real = runs.theme_scripts_between
    fail = {"n": 1}

    async def flaky(*a, **kw):
        if fail["n"]:
            fail["n"] -= 1
            raise mrs.MarketingRunError("theme_scripts_between failed: 520")
        return await real(*a, **kw)

    monkeypatch.setattr(runs, "theme_scripts_between", flaky)
    news = TF.FakeNews({})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, SPOT_THU)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        with pytest.raises(mrs.MarketingRunError):
            await svc.kick(rid, claim=_holder(svc, rid))
    assert news.calls == [] and not [r for r in sb.tables[mrs.SCRIPTS].rows if r.get("run_id") == rid]
    assert any("theme rotation's ledger read failed" in r.getMessage() and rid in r.getMessage()
               for r in caplog.records)
    for _ in range(50):
        if not svc._builds:
            break
        await asyncio.sleep(0)
    await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert "theme_explainer" in [c["series"] for c in news.calls]


def test_week_cap_theme_week_fallbacks_counts_only_this_weeks_backups(caplog):
    """The pure count: in the week, not this run's own row, a backup (never the main lane's Wednesday lead), dated on or
    before the run day — or after it in the OTHER lane only — and a row whose day does not read back counts (fail
    closed, ERROR). Mutation-checked by hand (2026-11-27): `continue` on an unreadable day turned the last assertion
    red; dropping the own-row check turned the first red."""
    def row(day, lane=None, rid="other", ref=None):
        return {"run_id": rid, "run_date": day if isinstance(day, str) else day.isoformat(), "lane": lane,
                "source_ref": ref or f"news:theme_explainer:x:make_money:{day}"}

    fb = ss.theme_week_fallbacks
    assert fb([row(CAP_TUE, "spotlight", rid="me")], CAP_THU, "spotlight", run_id="me") == []         # its own row
    assert fb([row(CAP_WED)], CAP_THU, "spotlight", run_id="me") == []                # the main lane's Wednesday lead
    assert [b["lane"] for b in fb([row(CAP_WED, "spotlight")], CAP_THU, "spotlight", run_id="me")] == ["spotlight"]
    assert fb([row(CAP_MON - timedelta(days=1), "spotlight")], CAP_THU, "spotlight", run_id="me") == []  # last week
    assert fb([row(CAP_NEXT_MON)], CAP_SUN, "spotlight", run_id="me") == []                              # next week
    # a row dated after the run day counts only from the OTHER lane (a main run built the next day)
    assert fb([row(CAP_TUE, "main")], CAP_MON, "main", run_id="me") == []
    assert [b["run_date"] for b in fb([row(CAP_TUE, "spotlight")], CAP_MON, "main", run_id="me")] == ["2026-11-17"]
    # no lane is the main lane (a Monday main pick: a backup); a lane the code does not know is a backup
    assert [b["lane"] for b in fb([row(CAP_MON), row(CAP_WED, "odd")], CAP_THU, "spotlight", run_id="me")] == [
        "main", "odd"]
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        got = fb([row("not-a-date", "spotlight")], CAP_THU, "spotlight", run_id="me")
    assert [b["run_date"] for b in got] == ["not-a-date"]
    assert any("does not read back" in r.getMessage() and "fail closed" in r.getMessage() for r in caplog.records)


# ══ Replacement runs (migration 215, owner decision 2026-11-27: "🔄 Next company") — the build ════════════════════
# A replacement run (replacement 1 or 2, either lane) builds ONLY Money Map: `selection.replacement_chain` read NOW,
# ``(money_map, SKIP)`` — never the day's calendar, never the lesson. It keeps away from everything its own (day, lane)
# already drew: the replaced run's and every earlier replacement's ledger keys join the exclude set (the source refuses
# them `already_posted`), and a candidate whose LEAD company one of them drew — a share-class twin included — is
# EXCLUDED as `replaced_company` (never a template refusal, never reported to the refusal memo). Nothing left → the
# `nothing_qualified` skip with its trail, never the lesson. Amendment A3: a replacement that cannot ask Money Map at
# all (switched off now, or a holder that cannot draw it) skips with its OWN trail outcome under `money_map` —
# `switched_off` / `worker_capability`, infrastructure outcomes, never "no company qualified". A day's own run
# (replacement 0) reads nothing new: every test above is its byte-for-byte pin.

#: Monday 2026-11-16: a main posting day whose calendar LEADS with the CEO roundup (Money Map comes later in its chain).
REP_MON = date(2026, 11, 16)
assert REP_MON.weekday() == 0


def _rep_run(sb: FakeSB, day: date, n: int, *, lane: str = "main", caps=None, replaces: Optional[str] = None) -> str:
    """A HELD replacement run `n` of (`day`, `lane`) — what the existing claim makes of the `planned` row the request
    function inserted (`replaces_run_id`, the request stamp in its metadata)."""
    meta: Dict[str, Any] = {"claim_nonce": NONCE, "worker_capabilities": list(_SPOT_CAPS if caps is None else caps),
                            "replacement_request": {"by": "owner", "at": "2026-11-16T18:00:00+00:00"}}
    extra: Dict[str, Any] = {"metadata": meta, "worker_version": "replacement-test", "replacement": n}
    if replaces is not None:
        extra["replaces_run_id"] = replaces
    if lane != "main":
        extra["lane"] = lane
    return _run(sb, day, **extra)


def _ledger_news(answers: Dict[str, Any]):
    """The fake source (template_flow's, with the refusal hook) that ALSO models the adapter's ledger rule: a record
    whose ledger key is in the build's `exclude` set is refused `already_posted` before it is offered (the real
    `_collect_money_map` refuses an excluded `news:money_map:<SYM>:<FY>` before any profile call)."""
    TF = _tf()

    class LedgerNews(TF.ReportingNews):
        async def candidates(self, series, *, run_date, exclude, limit, deadline, **kw):
            got = await super().candidates(series, run_date=run_date, exclude=exclude, limit=limit, deadline=deadline,
                                           **kw)
            kept = tuple(r for r in got.records if news_rules.ledger_key(r) not in exclude)
            dropped = len(got.records) - len(kept)
            if not dropped:
                return got
            rejections = dict(got.rejections)
            rejections["already_posted"] = rejections.get("already_posted", 0) + dropped
            return TF.cands(series, *kept, skip=f"{series}_all_posted", rejections=rejections)

    return LedgerNews(answers)


async def _gen0(sb: FakeSB, runs, day: date, lane: str, *records) -> str:
    """The day's own run of `lane`, built through the REAL kick with Money Map offering `records` (every other series
    finds nothing): its accepted Money Map is what a replacement replaces. Returns its run id."""
    TF = _tf()
    news = TF.FakeNews({"money_map": TF.cands("money_map", *records)})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _lane_run(sb, day, lane=lane)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert state["status"] == "accepted" and state["template_id"] == "money_map", state
    _run_row(sb, rid)["status"] = "media_ready"
    return rid


def _forbid_the_calendar_and_the_lesson(monkeypatch) -> None:
    """A replacement never reads the day's calendar, the spotlight lane's predicate or the lesson pool."""
    def boom(*_a, **_k):
        raise AssertionError("a replacement run never reads the day's calendar or the lesson")

    async def aboom(*_a, **_k):
        raise AssertionError("a replacement run never selects the lesson or the spotlight calendar")

    for name in ("plan_for", "spotlight_plan_for", "spotlight_day_problem", "spotlight_chain", "spotlight_chain_now"):
        monkeypatch.setattr(selection, name, boom)
    monkeypatch.setattr(content_pool, "eligible_keys", boom)
    monkeypatch.setattr(ss.MarketingScriptService, "_select_lesson", aboom)
    monkeypatch.setattr(ss.MarketingScriptService, "_select_spotlight", aboom)


#: (lane, day): the main lane on a Monday (its calendar leads with the CEO roundup) and the spotlight lane on the 13F
#: season's Saturday (its calendar leads with the 13F spotlight).
_REP_LANES = [("main", REP_MON), ("spotlight", SPOT_SAT)]


def test_replacement_the_constants_and_the_owner_words():
    from app.services.marketing import digest_service as ds

    assert ss.SERIES_OUTCOMES[-3:] == ("replaced", "switched_off", "worker_capability")        # appended LAST
    assert (ss.REPLACED, ss.REPLACEMENT_SWITCHED_OFF, ss.REPLACEMENT_WORKER_CAPABILITY) == ss.SERIES_OUTCOMES[-3:]
    assert ss.REPLACED_COMPANY == "replaced_company" and ss.REPLACED_COMPANY != ss.OTHER_LANE_COMPANY
    for reason in (ss.REPLACED_COMPANY, ss.REPLACEMENT_MONEY_MAP_OFF, ss.REPLACEMENT_NO_TEMPLATES,
                   ss.REPLACEMENT_NO_LAYOUT):
        assert 0 < len(reason) <= 60                                                # a trail reason is cut at 60
    assert selection.REPLACEMENT_PLAN_REASON == "replacement"
    # `replaced` is a content outcome (a quiet "no other company" — notice N1); the two configuration skips are
    # infrastructure (amendment A3: they alert with operator words — notice N2 — never "no company qualified")
    assert ss.REPLACED in ds._CONTENT_OUTCOMES and ss.REPLACED not in ds._INFRA_OUTCOMES
    for code in (ss.REPLACEMENT_SWITCHED_OFF, ss.REPLACEMENT_WORKER_CAPABILITY):
        assert code in ds._INFRA_OUTCOMES and code not in ds._CONTENT_OUTCOMES
    for outcome, quiet in ((ss.REPLACED, True), (ss.REPLACEMENT_SWITCHED_OFF, False),
                           (ss.REPLACEMENT_WORKER_CAPABILITY, False)):
        run = {"metadata": {"series": selection.SKIP, "series_trail": [{"series": "money_map", "outcome": outcome}]}}
        assert ds._quiet_nothing_qualified(run) is quiet, outcome
        assert "Money Map: " + ds._SERIES_OUTCOME_WORDS[outcome] in ds._series_note(run)


@pytest.mark.parametrize("lane, day", _REP_LANES)
@pytest.mark.asyncio
async def test_replacement_builds_only_money_map_never_the_calendar_or_the_lesson(spot_world, monkeypatch, caplog,
                                                                                  lane, day):
    """The day's own run fell back to Money Map (Costco); the owner tapped Next. The replacement asks ONLY Money Map —
    never the CEO roundup the Monday calendar leads with, never the 13F spotlight the Saturday's leads with, never
    the lesson — and posts the NEXT company (Walmart): plan `replacement`, chain (money_map, skip), its script row
    carrying its lane and replacement number, the writer never called."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: day)
    gen0 = await _gen0(sb, runs, day, lane, TF.costco())
    before = copy.deepcopy(_script_row(sb, gen0))
    _forbid_the_calendar_and_the_lesson(monkeypatch)
    news = TF.ReportingNews({"ceo_buys": TF.cands("ceo_buys", TF.ceo_week(day)),
                             "money_map": TF.cands("money_map", TF.costco(), _mm("WMT", "Walmart"))})
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer, news=news)
    rid = _rep_run(sb, day, 1, lane=lane, replaces=gen0)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "accepted" and state["template_id"] == "money_map", state
    assert state["source_ref"] == "news:money_map:WMT:2025"
    assert [c["series"] for c in news.calls] == ["money_map"] and writer.calls == [] and news.reported == []
    row = _script_row(sb, rid)
    assert row["replacement"] == 1 and row.get("lane", "main") == lane
    assert row["fact_sheet"]["selection"] == {"plan": "replacement", "chain": ["money_map", "skip"],
                                              "trail": [{"series": "money_map", "outcome": "chosen"}]}
    run = _run_row(sb, rid)
    assert (run["template_id"], run["content_class"], run["metadata"]["series"]) == ("money_map", "F", "money_map")
    assert _script_row(sb, gen0) == before                                     # the replaced run's row untouched
    assert any("replacement 1" in r.getMessage() and "keeps away" in r.getMessage() and "COST" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("lane, day", _REP_LANES)
@pytest.mark.asyncio
async def test_replacement_the_same_pool_gives_the_second_company(spot_world, monkeypatch, lane, day):
    """§3.1's bug, on the adapter's own ledger rule: the replacement is offered the SAME pool as the run it replaces.
    `recent_source_refs` never reads the caller's own lane on its run date, so before migration 215's build the main
    lane re-picked Costco. Now the replaced run's ledger key is in the source's exclude set — Costco is refused
    `already_posted` at the source — and the next company (Walmart) posts. Nothing reaches the refusal memo."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: day)
    gen0 = await _gen0(sb, runs, day, lane, TF.costco(), _mm("WMT", "Walmart"))
    assert _script_row(sb, gen0)["source_ref"] == "news:money_map:COST:2025"
    news = _ledger_news({"money_map": TF.cands("money_map", TF.costco(), _mm("WMT", "Walmart"))})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _rep_run(sb, day, 1, lane=lane, replaces=gen0)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "accepted" and state["source_ref"] == "news:money_map:WMT:2025", state
    (call,) = news.calls
    assert "news:money_map:COST:2025" in call["exclude"]
    row = _script_row(sb, rid)
    assert row["fact_sheet"]["rejections"] == {"already_posted": 1}
    assert row["fact_sheet"]["selection"]["trail"] == [{"series": "money_map", "outcome": "chosen"}]
    assert news.reported == []


@pytest.mark.parametrize("lane, day", _REP_LANES)
@pytest.mark.asyncio
async def test_replacement_a_share_class_twin_of_the_replaced_company_is_excluded_never_refused(
        spot_world, monkeypatch, caplog, lane, day):
    """The replaced company drew GOOGL; its twin GOOG has another ledger key, so the source offers it — but its LEAD is
    the same issuer (root GOOG), so it is EXCLUDED `replaced_company` (checked before the cross-lane rule: on the
    spotlight lane the replaced lead is in both sets, and it is named as the replaced company) and the next company
    posts. Never a template refusal, never reported to the source's refusal memo."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: day)
    gen0 = await _gen0(sb, runs, day, lane, _mm("GOOGL", "Alphabet"))
    twin, nxt = _mm("GOOG", "Alphabet Class C"), _mm("MSFT", "Microsoft")
    news = _ledger_news({"money_map": TF.cands("money_map", twin, nxt)})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _rep_run(sb, day, 1, lane=lane, replaces=gen0)
    with caplog.at_level(logging.INFO, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["source_ref"] == "news:money_map:MSFT:2025", state
    assert news.reported == []
    assert any("EXCLUDED" in r.getMessage() and "GOOG" in r.getMessage() and "already drawn by this lane" in
               r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("lane, day", _REP_LANES)
@pytest.mark.asyncio
async def test_replacement_only_the_replaced_company_left_skips_replaced_never_the_lesson(spot_world, monkeypatch,
                                                                                         lane, day):
    """A source that does NOT model the ledger offers the replaced company again (Costco) and its twin: both are
    excluded at compose (`replaced_company`), the trail says `replaced` — not `other_lane`, even on the spotlight lane
    whose cross-lane set holds the same lead — and the run SKIPS: ONE `rejected` / `nothing_qualified` row, last_error
    "replacement 1: no other company qualified", never the lesson, nothing reported."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: day)
    gen0 = await _gen0(sb, runs, day, lane, _mm("GOOGL", "Alphabet"))
    _forbid_the_calendar_and_the_lesson(monkeypatch)
    news = TF.ReportingNews({"money_map": TF.cands("money_map", _mm("GOOGL", "Alphabet"),
                                                    _mm("GOOG", "Alphabet Class C"))})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _rep_run(sb, day, 1, lane=lane, replaces=gen0)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state == {"status": "rejected", "source_ref": None, "template_id": None, "reason": "nothing_qualified",
                     "violations": []}
    row = _script_row(sb, rid)
    assert (row["status"], row["reject_reason"], row["last_error"], row["replacement"]) == (
        "rejected", "nothing_qualified", "replacement 1: no other company qualified", 1)
    assert row["fact_sheet"]["selection"] == {"plan": "replacement", "chain": ["money_map", "skip"], "trail": [
        {"series": "money_map", "outcome": "replaced", "reason": "replaced_company"},
        {"series": "skip", "outcome": "no_candidates"}]}
    assert news.reported == []
    assert _run_row(sb, rid)["metadata"]["series"] == selection.SKIP          # the skip mirror: never the lesson


@pytest.mark.asyncio
async def test_replacement_a_main_run_with_nothing_left_skips_never_the_weekly_lesson(spot_world, monkeypatch, caplog):
    """D8: the MAIN lane's chain ends in the lesson — a replacement's never does. The pool holds only the replaced
    company, which the source refuses `already_posted`: ONE `rejected` / `nothing_qualified` row (the trail's
    `all_recent` step, then the SKIP), the kick answers the skip (the worker closes the run `skipped` — notice N1),
    the lesson is never selected and the writer never called."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: REP_MON)
    gen0 = await _gen0(sb, runs, REP_MON, "main", TF.costco())
    _forbid_the_calendar_and_the_lesson(monkeypatch)
    news = _ledger_news({"money_map": TF.cands("money_map", TF.costco())})
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer, news=news)
    rid = _rep_run(sb, REP_MON, 1, replaces=gen0)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified", state
    assert writer.calls == [] and [c["series"] for c in news.calls] == ["money_map"]
    rows = [r for r in sb.tables[mrs.SCRIPTS].rows if r["run_id"] == rid]
    assert len(rows) == 1 and "lane" not in rows[0]                        # a main INSERT never sends the lane
    assert (rows[0]["reject_reason"], rows[0]["last_error"], rows[0]["replacement"]) == (
        "nothing_qualified", "replacement 1: no other company qualified", 1)
    assert rows[0]["fact_sheet"]["selection"]["trail"] == [
        {"series": "money_map", "outcome": "all_recent", "reason": "money_map_all_posted"},
        {"series": "skip", "outcome": "no_candidates"}]
    assert any("NOTHING QUALIFIED" in r.getMessage() and rid in r.getMessage() for r in caplog.records)
    # idempotent: the next kick answers the same row, nothing rebuilt
    assert await svc.kick(rid, claim=_holder(svc, rid)) == state and len(news.calls) == 1


@pytest.mark.parametrize("lane, day", _REP_LANES)
@pytest.mark.asyncio
async def test_replacement_the_second_excludes_both_earlier_records(spot_world, monkeypatch, lane, day):
    """Replacement 2 keeps away from replacement 0 (Costco) AND replacement 1 (Walmart): both ledger keys reach the
    source's exclude set, and — with a source that does not model the ledger — both companies are excluded at compose
    (`replaced_company`), so Target posts."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: day)
    gen0 = await _gen0(sb, runs, day, lane, TF.costco())
    news1 = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco(), _mm("WMT", "Walmart"))})
    svc1 = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news1)
    gen1 = _rep_run(sb, day, 1, lane=lane, replaces=gen0)
    assert (await svc1.kick(gen1, claim=_holder(svc1, gen1)))["source_ref"] == "news:money_map:WMT:2025"
    _run_row(sb, gen1)["status"] = "media_ready"
    pool = (TF.costco(), _mm("WMT", "Walmart"), _mm("TGT", "Target"))
    for news in (_ledger_news({"money_map": TF.cands("money_map", *pool)}),
                 TF.ReportingNews({"money_map": TF.cands("money_map", *pool)})):
        sb.tables[mrs.SCRIPTS].rows[:] = [r for r in sb.tables[mrs.SCRIPTS].rows if r.get("replacement") != 2]
        sb.tables[mrs.RUNS].rows[:] = [r for r in sb.tables[mrs.RUNS].rows if r.get("replacement") != 2]
        svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
        rid = _rep_run(sb, day, 2, lane=lane, replaces=gen1)
        state = await svc.kick(rid, claim=_holder(svc, rid))
        assert state["source_ref"] == "news:money_map:TGT:2025", (type(news).__name__, state)
        assert {"news:money_map:COST:2025", "news:money_map:WMT:2025"} <= news.calls[0]["exclude"]
        assert _script_row(sb, rid)["replacement"] == 2 and news.reported == []


@pytest.mark.parametrize("lane, day", _REP_LANES)
@pytest.mark.parametrize("caps", [["post_image"], [], ["layouts_2b", "post_image", "look_v23"]])
@pytest.mark.asyncio
async def test_replacement_a_holder_that_cannot_draw_a_template_skips_worker_capability(spot_world, monkeypatch, caplog,
                                                                                       lane, day, caps):
    """Amendment A3: a holder without `news_templates` cannot draw Money Map, and a replacement never falls to the
    lesson: the run skips with ONE trail step under `money_map` — outcome `worker_capability` (an infrastructure
    outcome: notice N2 with operator words, never "no other company qualified"). The source and the writer are never
    asked; no build task starts; WARNING naming the worker."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: day)
    gen0 = await _gen0(sb, runs, day, lane, TF.costco())
    _forbid_the_calendar_and_the_lesson(monkeypatch)
    news = TF.FakeNews({"money_map": TF.cands("money_map", _mm("WMT", "Walmart"))})
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer, news=news)
    rid = _rep_run(sb, day, 1, lane=lane, caps=caps, replaces=gen0)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified"
    assert news.calls == [] and writer.calls == [] and svc._builds == {}
    row = _script_row(sb, rid)
    assert row["last_error"] == "replacement 1: the worker cannot draw a template"
    assert row["fact_sheet"]["selection"] == {"plan": "replacement", "chain": ["money_map", "skip"], "trail": [
        {"series": "money_map", "outcome": "worker_capability", "reason": "worker_without_news_templates"}]}
    assert _run_row(sb, rid)["metadata"]["series"] == selection.SKIP
    assert any(r.levelno == logging.WARNING and "replacement-test" in r.getMessage()
               and "never falls to the lesson" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("patched_set", ["LAYOUTS_2B_SERIES", "FEATURE_LAYOUT_SERIES", "LOOK_V23_SERIES"])
@pytest.mark.asyncio
async def test_replacement_a_holder_that_cannot_draw_money_maps_picture_skips_worker_capability(spot_world,
                                                                                               monkeypatch,
                                                                                               patched_set):
    """The layout narrowings every selection applies (no-ops for Money Map today) keep their one place: were Money
    Map's picture ever a layout the holder did not declare, the replacement would skip `worker_capability` —
    never the lesson, never a picture the worker refuses."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: REP_MON)
    gen0 = await _gen0(sb, runs, REP_MON, "main", TF.costco())
    monkeypatch.setattr(ss, patched_set, frozenset({*getattr(ss, patched_set), "money_map"}))
    news = TF.FakeNews({"money_map": TF.cands("money_map", _mm("WMT", "Walmart"))})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _rep_run(sb, REP_MON, 1, caps=["news_templates", "post_image"], replaces=gen0)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified" and news.calls == []
    row = _script_row(sb, rid)
    assert row["last_error"] == "replacement 1: the worker cannot draw Money Map"
    assert row["fact_sheet"]["selection"]["trail"] == [
        {"series": "money_map", "outcome": "worker_capability", "reason": "worker_cannot_draw_its_layout"}]


@pytest.mark.parametrize("lane, day", _REP_LANES)
@pytest.mark.parametrize("why, classes, series", [
    ("money_map left MARKETING_NEWS_SERIES", "A,C,F", "ceo_buys,insider_buys,thirteen_f"),
    ("class F went off", "A,C", "ceo_buys,insider_buys,thirteen_f,money_map"),
    ("the lesson alone", "A", "ceo_buys,insider_buys,thirteen_f,money_map"),
    ("an empty series list", "A,C,F", ""),
])
@pytest.mark.asyncio
async def test_replacement_with_money_map_switched_off_skips_switched_off(spot_world, monkeypatch, caplog, lane, day,
                                                                          why, classes, series):
    """Amendment A3: Money Map switched off after the tap (A2 refuses the tap itself once it is off) — the replacement
    reads the switches NOW, cannot ask Money Map and SKIPS with ONE trail step `money_map` / `switched_off` (reason
    `money_map_off`): configuration, never "no other company qualified", never the lesson, nothing asked."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: day)
    gen0 = await _gen0(sb, runs, day, lane, TF.costco())
    _forbid_the_calendar_and_the_lesson(monkeypatch)
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", classes)
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", series)
    news = TF.FakeNews({"money_map": TF.cands("money_map", _mm("WMT", "Walmart"))})
    writer = FakeWriter(["accepted"])
    svc = ss.MarketingScriptService(runs, writer=writer, news=news)
    rid = _rep_run(sb, day, 1, lane=lane, replaces=gen0)
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified", why
    assert news.calls == [] and writer.calls == [] and svc._builds == {}
    row = _script_row(sb, rid)
    assert row["last_error"] == "replacement 1: Money Map is switched off"
    assert row["fact_sheet"]["selection"] == {"plan": "replacement", "chain": ["skip"], "trail": [
        {"series": "money_map", "outcome": "switched_off", "reason": "money_map_off"}]}
    assert any("switched off NOW" in r.getMessage() and rid in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("lane, day", _REP_LANES)
@pytest.mark.asyncio
async def test_replacement_the_ledger_read_failing_raises_into_the_kick(spot_world, monkeypatch, caplog, lane, day):
    """`runs.replaced_scripts` failing — or vouching for nothing (a replacement whose replaced run left no script) —
    is never built on a guess: the build crashes into the kick (the worker retries), ERROR naming the run, no row is
    written and the source is never asked; the next kick (the ledger back) builds afresh."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: day)
    gen0 = await _gen0(sb, runs, day, lane, TF.costco())
    real = runs.replaced_scripts

    async def down(*_a, **_k):
        raise mrs.MarketingRunError("replaced_scripts failed: RuntimeError: ledger down")

    monkeypatch.setattr(runs, "replaced_scripts", down)
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco(), _mm("WMT", "Walmart"))})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _rep_run(sb, day, 1, lane=lane, replaces=gen0)
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        with pytest.raises(mrs.MarketingRunError, match="ledger down"):
            await svc.kick(rid, claim=_holder(svc, rid))
    await _drain(svc)
    assert news.calls == [] and not [r for r in sb.tables[mrs.SCRIPTS].rows if r["run_id"] == rid]
    assert any("the replacement's ledger read failed" in r.getMessage() and rid in r.getMessage()
               for r in caplog.records)
    # the ledger back: the next kick builds afresh, and the next company posts
    monkeypatch.setattr(runs, "replaced_scripts", real)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["source_ref"] == "news:money_map:WMT:2025"

    # a ledger that cannot vouch for what it replaces: the replaced run's script is gone (hand-deleted)
    sb.tables[mrs.SCRIPTS].rows[:] = [r for r in sb.tables[mrs.SCRIPTS].rows if r["run_id"] not in (gen0, rid)]
    svc2 = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=TF.FakeNews({}))
    with pytest.raises(mrs.MarketingRunError, match="refusing to build on a guess"):
        await svc2.kick(rid, claim=_holder(svc2, rid))
    await _drain(svc2)
    assert not [r for r in sb.tables[mrs.SCRIPTS].rows if r["run_id"] == rid]


@pytest.mark.asyncio
async def test_replacement_rule_c_the_other_lane_posted_money_map_today_skips_other_lane(spot_world, monkeypatch):
    """Rule (c) still holds for a replacement: the spotlight lane posted a Money Map today, so the MAIN replacement's
    Money Map falls back `other_lane` before the source is asked — and the run SKIPS (the main lane's own day would
    have fallen to the lesson; a replacement never does)."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: REP_MON)
    gen0 = await _gen0(sb, runs, REP_MON, "main", TF.costco())
    _drawn(sb, REP_MON, "NVDA", template="money_map", ref="news:money_map:NVDA:2025")
    _forbid_the_calendar_and_the_lesson(monkeypatch)
    news = TF.FakeNews({"money_map": TF.cands("money_map", _mm("WMT", "Walmart"))})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _rep_run(sb, REP_MON, 1, replaces=gen0)
    state = await svc.kick(rid, claim=_holder(svc, rid))
    assert state["status"] == "rejected" and state["reason"] == "nothing_qualified" and news.calls == []
    assert _script_row(sb, rid)["fact_sheet"]["selection"]["trail"] == [
        {"series": "money_map", "outcome": "other_lane", "reason": "posted by the other lane today"},
        {"series": "skip", "outcome": "no_candidates"}]


@pytest.mark.parametrize("lane, day", _REP_LANES)
@pytest.mark.asyncio
async def test_replacement_a_days_own_run_reads_nothing_new(spot_world, monkeypatch, lane, day):
    """Generation 0 byte for byte: a day's own run never enters `_select_replacement` and never reads the replacement
    ledger (`replaced_scripts`) — its build is exactly what every test above pins."""
    TF = _tf()
    sb, runs = spot_world
    monkeypatch.setattr(ss, "_today_et", lambda: day)

    async def boom(*_a, **_k):
        raise AssertionError("a day's own run never reads the replacement ledger")

    monkeypatch.setattr(runs, "replaced_scripts", boom)
    monkeypatch.setattr(ss.MarketingScriptService, "_select_replacement", boom)
    monkeypatch.setattr(ss.MarketingScriptService, "_replaced", boom)
    gen0 = await _gen0(sb, runs, day, lane, TF.costco())
    row = _script_row(sb, gen0)
    assert "replacement" not in row and row["source_ref"] == "news:money_map:COST:2025"
    assert row["fact_sheet"]["selection"]["plan"] != selection.REPLACEMENT_PLAN_REASON


@pytest.mark.parametrize("bad", ["1", True, 3, -1, 1.0, "r1"])
@pytest.mark.asyncio
async def test_replacement_an_unreadable_number_is_refused_before_any_selection(spot_world, monkeypatch, caplog, bad):
    """A replacement number that does not read back is never guessed (what the build may draw and what it must keep
    away from depend on it): MarketingRequestInvalid (a 422), ERROR naming the run, nothing written, nothing asked."""
    TF = _tf()
    sb, runs = spot_world
    news = TF.FakeNews({"money_map": TF.cands("money_map", TF.costco())})
    svc = ss.MarketingScriptService(runs, writer=FakeWriter(["accepted"]), news=news)
    rid = _rep_run(sb, SPOT_THU, 1)
    _run_row(sb, rid)["replacement"] = bad
    with caplog.at_level(logging.ERROR, logger=ss.logger.name):
        with pytest.raises(mrs.MarketingRequestInvalid):
            await svc.kick(rid, claim=_holder(svc, rid))
    assert sb.tables[mrs.SCRIPTS].rows == [] and news.calls == []
    assert any("REFUSED" in r.getMessage() and rid in r.getMessage() for r in caplog.records)


def test_replacement_replaced_lead_keys_reads_the_output_and_the_ledger_key(caplog):
    """The pure union: each replaced script's output leads (issuer root + display name) AND the issuer root of its
    Money Map ledger key — so a script whose stored output does not read back still keeps its company and its twin
    out; a script naming no company at all is a WARNING (its exact ledger key is still excluded)."""
    out = {"image_spec": {"layout": "bars", "header": {"logo": "GOOGL"}}, "logo_refs": [{"key": "GOOGL",
                                                                                         "name": "Alphabet"}]}
    keys = ss.replaced_lead_keys([{"run_id": "a", "replacement": 0, "source_ref": "news:money_map:GOOGL:2025",
                                   "output": out}])
    assert keys == {"root:GOOG", "name:alphabet"}
    # the output does not read back: the ledger key's issuer root alone
    assert ss.replaced_lead_keys([{"source_ref": "news:money_map:BRK-B:2025", "output": None}]) == {"root:BRK"}
    # two replaced runs: the union
    both = ss.replaced_lead_keys([{"source_ref": "news:money_map:COST:2025", "output": "junk"},
                                  {"source_ref": "news:money_map:WMT:2025", "output": {}}])
    assert both == {"root:COST", "root:WMT"}
    # junk never raises; a script with no company is logged
    with caplog.at_level(logging.WARNING, logger=ss.logger.name):
        assert ss.replaced_lead_keys([None, "x", 7, {"source_ref": "news:ceo_buys:2026-11-09", "output": None},
                                      {"source_ref": None}, {"source_ref": "news:money_map::2025"}]) == frozenset()
    assert sum("names no lead company" in r.getMessage() for r in caplog.records) == 3
    assert ss.replaced_lead_keys([]) == frozenset()
