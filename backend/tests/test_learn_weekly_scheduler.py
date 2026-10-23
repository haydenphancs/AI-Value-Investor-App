"""The Wiser weekly engine's loop (`learn_weekly.scheduler`): which step runs when, the kill
switches, step isolation, and the sweeps that keep a piece from stalling (a build killed by a
deploy, a draft whose narration could not be queued, a narration job the ledger closed at its
re-claim cap). Every collaborator is stubbed; nothing here reaches Supabase."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from app.config import settings
from app.services import notification_jobs
from app.services.learn_weekly import alerts, build, contracts as c, ledger, narration
from app.services.learn_weekly import publisher, review, schedule, scheduler, topic

# Friday 2026-10-23 is in ISO week 43.
DD = schedule.deep_dive_times_for(datetime(2026, 10, 23).date())


def run(coro):
    return asyncio.run(coro)


class Claims:
    """Stub of `notification_jobs.claimed_scheduled_job` + `scheduled_job_state`."""

    def __init__(self) -> None:
        self.enabled: Dict[str, Optional[bool]] = {}
        self.granted = True
        self.results: List[Any] = []
        self.claimed: List[str] = []

    def state(self, job: str) -> Optional[dict]:
        value = self.enabled.get(job, True)
        if value is None:
            return None
        return {"job": job, "run_day": None, "claim_at": None, "enabled": value}

    @contextlib.asynccontextmanager
    async def claim(self, job: str, *, timezone_name: str = "UTC", stale_seconds: Any = None):
        assert timezone_name == "America/New_York"
        if not self.granted:
            yield None
            return
        self.claimed.append(job)
        result = notification_jobs.ScheduledJobResult()
        yield result
        self.results.append((job, result.success, result.error))


@pytest.fixture
def claims(monkeypatch):
    cl = Claims()
    monkeypatch.setattr(notification_jobs, "scheduled_job_state", cl.state)
    monkeypatch.setattr(notification_jobs, "claimed_scheduled_job", cl.claim)
    return cl


@pytest.fixture
def switches(monkeypatch):
    monkeypatch.setattr(settings, "LEARN_WEEKLY_ENABLED", True)
    monkeypatch.setattr(settings, "LEARN_DEEP_DIVE_ENABLED", True)
    monkeypatch.setattr(settings, "LEARN_SLOT_ENABLED", False)
    monkeypatch.setattr(settings, "LEARN_NARRATOR_ENGINE", "chatterbox")


@pytest.fixture
def calls(monkeypatch):
    """Record the wave-B entry points the loop calls; each returns a small summary."""
    rec: Dict[str, List[Any]] = {k: [] for k in (
        "cache_sync", "auto_pick", "review", "publish", "expire", "topic", "build", "after_failed",
        "deadman", "terminal", "merged")}

    async def cache_sync():
        rec["cache_sync"].append(1)
        return False

    async def auto_pick(now):
        rec["auto_pick"].append(now)
        return {"picked": 0}

    async def review_cycle(now):
        rec["review"].append(now)
        return {"sent": 0}

    async def publish_due(now):
        rec["publish"].append(now)
        return {"published": 0}

    async def expire_due(now):
        rec["expire"].append(now)
        return {"expired": 0}

    async def run_topic_step(dd, *, now):
        rec["topic"].append((dd.period_key, now))
        return {"id": "p1", "status": c.TOPIC_PENDING}

    async def build_piece(piece, *, now):
        rec["build"].append(piece["id"])
        return dict(piece)

    async def after_job_failed(job, *, now=None):
        rec["after_failed"].append(job["id"])

    async def finish_ready_job(job):
        rec["merged"].append(job["id"])
        return True

    async def deadman_check(now, dd):
        rec["deadman"].append(now)
        return "ok"

    async def notify_terminal(piece, *, status, reason, stage=None):
        rec["terminal"].append((piece.get("id"), status, reason, stage))

    monkeypatch.setattr(publisher, "cache_sync", cache_sync)
    monkeypatch.setattr(review, "auto_pick", auto_pick)
    monkeypatch.setattr(review, "review_cycle", review_cycle)
    monkeypatch.setattr(publisher, "publish_due", publish_due)
    monkeypatch.setattr(publisher, "expire_due", expire_due)
    monkeypatch.setattr(topic, "run_topic_step", run_topic_step)
    monkeypatch.setattr(build, "build_piece", build_piece)
    monkeypatch.setattr(narration, "after_job_failed", after_job_failed)
    monkeypatch.setattr(narration, "finish_ready_job", finish_ready_job)
    monkeypatch.setattr(alerts, "deadman_check", deadman_check)
    monkeypatch.setattr(alerts, "notify_terminal", notify_terminal)
    return rec


class Pieces:
    """Stub of the ledger reads/writes the loop itself makes."""

    def __init__(self) -> None:
        self.rows: List[Dict[str, Any]] = []
        self.jobs: Dict[str, Dict[str, Any]] = {}
        self.by_key: Dict[str, Dict[str, Any]] = {}
        self.transitions: List[tuple] = []

    async def list_pieces(self, *, statuses=None, kind=None, limit=50):
        return [dict(r) for r in self.rows if statuses is None or r["status"] in statuses][:limit]

    async def get_piece(self, key):
        return self.by_key.get(key)

    async def get_job(self, job_id):
        return self.jobs.get(job_id)

    async def transition(self, piece_id, *, from_statuses, to_status, fields=None, fence=None,
                         within_hold_at=None, due_at=None):
        for r in self.rows:
            if r["id"] == piece_id and r["status"] in from_statuses:
                r["status"] = to_status
                r.update(fields or {})
                self.transitions.append((piece_id, to_status))
                return dict(r)
        return None


@pytest.fixture
def pieces(monkeypatch):
    p = Pieces()
    monkeypatch.setattr(ledger, "list_pieces", p.list_pieces)
    monkeypatch.setattr(ledger, "get_piece", p.get_piece)
    monkeypatch.setattr(ledger, "get_job", p.get_job)
    monkeypatch.setattr(ledger, "transition", p.transition)
    return p


def planned(pid="p1", *, chosen=True, key=DD.period_key, status=c.PLANNED, **extra) -> Dict[str, Any]:
    row = {"id": pid, "period_key": key, "kind": c.KIND_DEEP_DIVE, "status": status,
           "hold_until": DD.hold_until.isoformat(),
           "topic": {"version": 1, "candidates": [{"key": "silicon-rush"}],
                     "chosen_index": 0 if chosen else None}}
    row.update(extra)
    return row


# ── switches ──────────────────────────────────────────────────────────────────────────────────


def test_the_engine_idles_while_disabled(monkeypatch, calls, claims, pieces):
    monkeypatch.setattr(settings, "LEARN_WEEKLY_ENABLED", False)
    assert run(scheduler.tick(DD.build_at)) == {"enabled": False}
    assert all(not v for v in calls.values()) and claims.claimed == []


def test_the_deep_dive_switch_gates_topic_auto_pick_and_deadman(monkeypatch, switches, calls, claims,
                                                               pieces):
    monkeypatch.setattr(settings, "LEARN_DEEP_DIVE_ENABLED", False)
    out = run(scheduler.tick(DD.deadman_at))
    assert "topic" not in out and "auto_pick" not in out and "deadman" not in out
    assert calls["review"] and calls["publish"] and calls["expire"] and calls["cache_sync"]


def test_the_slot_switch_only_logs_once(monkeypatch, switches, calls, claims, pieces, caplog):
    monkeypatch.setattr(settings, "LEARN_SLOT_ENABLED", True)
    scheduler._logged_once.discard("slot")
    with caplog.at_level(logging.WARNING):
        run(scheduler.tick(DD.build_at))
        run(scheduler.tick(DD.build_at))
    assert caplog.text.count("phase 1c") == 1


def test_a_failing_step_never_stops_the_next(monkeypatch, switches, calls, claims, pieces, caplog):
    async def boom(now):
        raise RuntimeError("telegram down")

    monkeypatch.setattr(review, "review_cycle", boom)
    with caplog.at_level(logging.ERROR):
        out = run(scheduler.tick(DD.build_at))
    assert out["review"] is None and "step review failed" in caplog.text
    assert calls["publish"] and calls["expire"], "publish and expiry still ran"


def test_a_cancelled_tick_propagates(monkeypatch, switches, calls, claims, pieces):
    async def cancelled():
        raise asyncio.CancelledError()

    monkeypatch.setattr(publisher, "cache_sync", cancelled)
    with pytest.raises(asyncio.CancelledError):
        run(scheduler.tick(DD.build_at))


# ── topic (Thursday) ──────────────────────────────────────────────────────────────────────────


def test_the_topic_step_waits_for_thursday_and_stops_at_publish(switches, calls, claims, pieces):
    before = DD.topic_at - timedelta(seconds=1)
    assert schedule.current_deep_dive(before) is None or \
        run(scheduler._topic_step(before, DD)) == "not_due"
    assert run(scheduler._topic_step(DD.publish_at, DD)) == "not_due"
    assert calls["topic"] == []


def test_the_topic_step_runs_claimed_and_marks_the_day(switches, calls, claims, pieces):
    assert run(scheduler._topic_step(DD.topic_at, DD)) == "ran"
    assert calls["topic"] == [(DD.period_key, DD.topic_at)]
    assert claims.results == [(scheduler.JOB_TOPIC, True, None)]


def test_a_topic_step_with_no_piece_leaves_the_day_open(monkeypatch, switches, calls, claims, pieces):
    async def nothing(dd, *, now):
        return None

    monkeypatch.setattr(topic, "run_topic_step", nothing)
    assert run(scheduler._topic_step(DD.topic_at, DD)) == "ran"
    assert claims.results == [(scheduler.JOB_TOPIC, False, "topic step produced no piece")]


def test_the_topic_step_does_nothing_once_the_week_has_a_topic(switches, calls, claims, pieces):
    pieces.by_key[DD.period_key] = planned(status=c.TOPIC_PENDING)
    assert run(scheduler._topic_step(DD.topic_at, DD)) == "done"
    pieces.by_key[DD.period_key] = planned(chosen=False, status=c.PLANNED, topic=None)
    pieces.by_key[DD.period_key]["topic"] = {"version": 1, "candidates": []}
    assert run(scheduler._topic_step(DD.topic_at, DD)) == "done"
    assert calls["topic"] == [] and claims.claimed == []


def test_the_topic_kill_switch_and_an_unreadable_state(switches, calls, claims, pieces):
    claims.enabled[scheduler.JOB_TOPIC] = False
    assert run(scheduler._topic_step(DD.topic_at, DD)) == "disabled"
    claims.enabled[scheduler.JOB_TOPIC] = None
    assert run(scheduler._topic_step(DD.topic_at, DD)) == "unreadable"
    claims.enabled[scheduler.JOB_TOPIC] = True
    claims.granted = False
    assert run(scheduler._topic_step(DD.topic_at, DD)) == "held"
    assert calls["topic"] == []


# ── build (every tick) ────────────────────────────────────────────────────────────────────────


def test_a_planned_piece_waits_for_its_topic_and_its_build_time(switches, calls, claims, pieces):
    pieces.rows = [planned("a", chosen=False), planned("b")]
    out = run(scheduler._build_step(DD.build_at - timedelta(seconds=1)))
    assert out["waiting_topic"] == 1 and out["not_due"] == 1 and calls["build"] == []
    out = run(scheduler._build_step(DD.build_at))
    assert out["built"] == 1 and calls["build"] == ["b"]


def test_a_live_build_lease_is_left_alone_and_a_dead_one_rebuilt(switches, calls, claims, pieces):
    pieces.rows = [planned("live", status=c.BUILDING,
                           lease_until=(DD.build_at + timedelta(minutes=5)).isoformat()),
                   planned("dead", status=c.BUILDING,
                           lease_until=(DD.build_at - timedelta(minutes=1)).isoformat())]
    out = run(scheduler._build_step(DD.build_at))
    assert out["held"] == 1 and calls["build"] == ["dead"]


def test_a_drafted_piece_only_retries_its_narration_enqueue(switches, calls, claims, pieces):
    pieces.rows = [planned("d", status=c.DRAFTED, chosen=False)]
    out = run(scheduler._build_step(DD.build_at - timedelta(hours=10)))
    assert out["narration_retried"] == 1 and calls["build"] == ["d"]


def test_past_the_cutoff_a_planned_piece_is_skipped_loudly(switches, calls, claims, pieces):
    pieces.rows = [planned("late")]
    cutoff = schedule.catch_up_cutoff(DD.hold_until, "chatterbox", c.DEEP_DIVE_WORDS[1])
    out = run(scheduler._build_step(cutoff))
    assert out["skipped_late"] == 1 and calls["build"] == []
    assert pieces.transitions == [("late", c.SKIPPED)]
    assert calls["terminal"] == [("late", c.SKIPPED, "catch_up_cutoff_passed", "build")]


def test_an_unreadable_period_key_is_never_built(switches, calls, claims, pieces, caplog):
    pieces.rows = [planned("bad", key="2026-W99:deep_dive")]
    with caplog.at_level(logging.ERROR):
        out = run(scheduler._build_step(DD.build_at))
    assert out["not_due"] == 1 and calls["build"] == [] and "unreadable period_key" in caplog.text


def test_the_build_kill_switch(switches, calls, claims, pieces):
    pieces.rows = [planned("b")]
    claims.enabled[scheduler.JOB_BUILD] = False
    assert run(scheduler._build_step(DD.build_at))["disabled"] == 1
    claims.enabled[scheduler.JOB_BUILD] = None
    assert run(scheduler._build_step(DD.build_at))["disabled"] == 1
    assert calls["build"] == []


def test_junk_topics_are_waiting_not_crashes(switches, calls, claims, pieces):
    pieces.rows = [planned("j1", topic="junk"), planned("j2", topic={"chosen_index": True}),
                   planned("j3", topic={"chosen_index": "0"})]
    out = run(scheduler._build_step(DD.build_at))
    assert out["waiting_topic"] == 3 and calls["build"] == []


# ── narration sweep ───────────────────────────────────────────────────────────────────────────


def test_a_failed_current_job_is_handed_to_after_job_failed(switches, calls, claims, pieces):
    pieces.rows = [planned("m1", status=c.MEDIA_PENDING, narration_job_id="j1"),
                   planned("m2", status=c.MEDIA_PENDING, narration_job_id="j2"),
                   planned("m3", status=c.MEDIA_PENDING, narration_job_id=None)]
    pieces.jobs = {"j1": {"id": "j1", "status": c.JOB_FAILED}, "j2": {"id": "j2", "status": c.JOB_QUEUED}}
    out = run(scheduler._narration_step(DD.build_at))
    assert out == {"checked": 2, "failed_jobs": 1} and calls["after_failed"] == ["j1"]


def test_a_cancelled_current_job_gets_the_next_round_and_a_ready_one_is_merged(switches, calls, claims,
                                                                               pieces):
    # Cancelled: a Regenerate racing a Retry narration left the piece on a job no worker claims.
    # Ready: `complete` committed the job, then the piece write failed — finished audio, unmerged.
    pieces.rows = [planned("c", status=c.MEDIA_PENDING, narration_job_id="jc"),
                   planned("r", status=c.MEDIA_PENDING, narration_job_id="jr"),
                   planned("p", status=c.MEDIA_PENDING, narration_job_id="jp")]
    pieces.jobs = {"jc": {"id": "jc", "status": c.JOB_CANCELLED},
                   "jr": {"id": "jr", "status": c.JOB_READY},
                   "jp": {"id": "jp", "status": c.JOB_IN_PROGRESS}}
    out = run(scheduler._narration_step(DD.build_at))
    assert calls["after_failed"] == ["jc"] and calls["merged"] == ["jr"]
    assert out["failed_jobs"] == 1 and out["ready_unmerged"] == 1 and out["checked"] == 3


# ── writer back-off ───────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("failures,wait_min", [(1, 15), (2, 45), (3, 135)])
def test_a_writer_failure_backs_off_before_the_next_build(switches, calls, claims, pieces, failures, wait_min):
    failed_at = DD.build_at + timedelta(minutes=1)
    pieces.rows = [planned("w", writer_failures=failures, updated_at=failed_at.isoformat(),
                           last_error=f"writer_failures {failures}/3: writer_failed: outline: truncated")]
    out = run(scheduler._build_step(failed_at + timedelta(minutes=wait_min) - timedelta(seconds=1)))
    assert out.get("backing_off") == 1 and calls["build"] == []
    out = run(scheduler._build_step(failed_at + timedelta(minutes=wait_min)))
    assert out["built"] == 1 and calls["build"] == ["w"]


@pytest.mark.parametrize("row", [
    {"writer_failures": 1, "last_error": "fact_sheet_unavailable: SEC 503"},     # not a writer failure
    {"writer_failures": 0, "last_error": "writer_failures 0/3"},
    {"writer_failures": True, "last_error": "writer_failures 1/3"},              # bool is not a count
    {"writer_failures": 2, "last_error": "writer_failures 2/3", "updated_at": "junk"},
])
def test_no_back_off_without_a_readable_writer_failure(switches, calls, claims, pieces, row):
    pieces.rows = [planned("x", **{"updated_at": DD.build_at.isoformat(), **row})]
    out = run(scheduler._build_step(DD.build_at + timedelta(minutes=1)))
    assert out["built"] == 1 and "backing_off" not in out


def test_the_engine_literal_in_config_mirrors_contracts():
    from app.config import Settings

    for engine in c.ENGINES:
        assert Settings(LEARN_NARRATOR_ENGINE=engine.upper() + " ").LEARN_NARRATOR_ENGINE == engine
    with pytest.raises(Exception):
        Settings(LEARN_NARRATOR_ENGINE="Chatterbox2")
    with pytest.raises(Exception):
        Settings(LEARN_NARRATOR_KOKORO_VOICE="michael")
    assert Settings(LEARN_NARRATOR_KOKORO_VOICE=" AM_Michael ").LEARN_NARRATOR_KOKORO_VOICE == "am_michael"


# ── deadman (Friday 15:00 ET) ─────────────────────────────────────────────────────────────────


def test_the_deadman_runs_once_between_15_and_publish(switches, calls, claims, pieces):
    assert run(scheduler._deadman_step(DD.deadman_at - timedelta(seconds=1), DD, alerts)) == "not_due"
    assert run(scheduler._deadman_step(DD.publish_at, DD, alerts)) == "not_due"
    assert run(scheduler._deadman_step(DD.deadman_at, DD, alerts)) == "ok"
    assert claims.results == [(scheduler.JOB_DEADMAN, True, None)]


@pytest.mark.parametrize("verdict", ["unreadable", "unsent", None])
def test_an_undecided_deadman_leaves_the_day_open(monkeypatch, switches, calls, claims, pieces, verdict):
    async def undecided(now, dd):
        return verdict

    monkeypatch.setattr(alerts, "deadman_check", undecided)
    run(scheduler._deadman_step(DD.deadman_at, DD, alerts))
    assert claims.results[-1][1] is False


# ── the whole tick ────────────────────────────────────────────────────────────────────────────


def test_a_friday_tick_runs_every_step_in_order(switches, calls, claims, pieces):
    pieces.rows = [planned("p")]
    out = run(scheduler.tick(DD.deadman_at))
    assert out["enabled"] is True
    for key in ("cache_sync", "topic", "auto_pick", "build", "narration", "review", "publish", "expire",
                "deadman"):
        assert key in out, key
    order = list(out)
    assert order.index("publish") < order.index("build"), "a due publish never waits behind a build"
    assert calls["build"] == ["p"] and calls["deadman"] == [DD.deadman_at]


def test_outside_any_deep_dive_window_only_the_sweeps_run(switches, calls, claims, pieces):
    # Tuesday: after last week's hold, before this week's topic card.
    tuesday = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
    assert schedule.current_deep_dive(tuesday) is None
    out = run(scheduler.tick(tuesday))
    assert "topic" not in out and "deadman" not in out
    assert calls["publish"] and calls["expire"] and calls["review"]
