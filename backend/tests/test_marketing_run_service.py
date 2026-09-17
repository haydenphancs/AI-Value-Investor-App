"""
Marketing run ledger (migration 170 / `app/services/marketing/run_service.py`) — the claim matrix, the
content-addressed paths, and the service against an in-memory PostgREST fake.

No network: the fake below stands in for `get_supabase()` (conftest blocks sockets anyway).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pytest

from app.schemas import marketing as schemas
from app.services.marketing import run_service as mrs
from app.services.marketing.run_service import (
    ALREADY_DONE,
    ATTEMPTS_EXHAUSTED,
    CLAIMED,
    IN_PROGRESS,
    MEDIA_READY,
    NO_RUN,
    MarketingAssetMissingInStorage,
    MarketingRunNotFound,
    MarketingRunService,
    decide_claim,
    idempotency_key_for,
    next_stage,
    run_date_et,
    storage_path_for,
)

SHA = "a" * 64
NOW = datetime(2026, 9, 17, 21, 30, tzinfo=timezone.utc)


# ── pure helpers ──────────────────────────────────────────────────────────────


def test_run_date_is_the_new_york_calendar_day():
    # 03:30 UTC on the 18th is still the 17th in New York (EDT, UTC-4).
    assert run_date_et(datetime(2026, 9, 18, 3, 30, tzinfo=timezone.utc)) == date(2026, 9, 17)
    # 04:30 UTC is 00:30 ET → the 18th.
    assert run_date_et(datetime(2026, 9, 18, 4, 30, tzinfo=timezone.utc)) == date(2026, 9, 18)
    # Naive input is treated as UTC, never as local time.
    assert run_date_et(datetime(2026, 9, 18, 3, 30)) == date(2026, 9, 17)


def test_next_stage_walks_the_pipeline_and_ends_in_none():
    seq = []
    s: Optional[str] = "planned"
    while s is not None:
        seq.append(s)
        s = next_stage(s)
    assert tuple(seq) == schemas.RUN_STAGES
    with pytest.raises(ValueError):
        next_stage("bogus")


def test_storage_path_is_content_addressed_and_immutable():
    p = storage_path_for(date(2026, 9, 17), "video", SHA, "mp4")
    assert p == "2026-09-17/video-aaaaaaaaaaaaaaaa.mp4"
    # Same bytes → same key; different bytes → different key. Case and dots are normalised.
    assert storage_path_for(date(2026, 9, 17), "video", SHA.upper(), ".MP4") == p
    assert storage_path_for(date(2026, 9, 17), "video", "b" * 64, "mp4") != p
    for bad in [("bogus", SHA, "mp4"), ("video", SHA, "exe"), ("video", "abc", "mp4")]:
        with pytest.raises(ValueError):
            storage_path_for(date(2026, 9, 17), *bad)


def test_idempotency_key_is_stable_and_validated():
    assert idempotency_key_for(date(2026, 9, 17), "x", "text") == "2026-09-17:x:text"
    with pytest.raises(ValueError):
        idempotency_key_for(date(2026, 9, 17), "myspace", "text")
    with pytest.raises(ValueError):
        idempotency_key_for(date(2026, 9, 17), "x", "hologram")


@pytest.mark.parametrize(
    "existing, expected",
    [
        (None, CLAIMED),
        ({"status": "published"}, ALREADY_DONE),
        ({"status": "skipped"}, ALREADY_DONE),
        ({"status": "media_ready"}, MEDIA_READY),
        ({"status": "planned"}, CLAIMED),
        ({"status": "failed", "started_at": NOW.isoformat()}, CLAIMED),
        # fresh in_progress → leave it alone
        ({"status": "in_progress", "started_at": (NOW - timedelta(minutes=5)).isoformat()}, IN_PROGRESS),
        # stale in_progress → the container died; re-claim
        ({"status": "in_progress", "started_at": (NOW - timedelta(hours=2)).isoformat()}, CLAIMED),
        # in_progress with no start stamp (malformed) → nothing proves it is alive; re-claim
        ({"status": "in_progress", "started_at": None}, CLAIMED),
        ({"status": "in_progress", "started_at": "not a date"}, CLAIMED),
        # 'Z' suffix and a datetime object both parse
        ({"status": "in_progress", "started_at": (NOW - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")}, IN_PROGRESS),
        ({"status": "in_progress", "started_at": NOW - timedelta(minutes=1)}, IN_PROGRESS),
        # a status a later migration might add: let the worker take it rather than wedge
        ({"status": "something_new"}, CLAIMED),
        # liveness is the LATER of started_at and updated_at: an old start with a fresh
        # checkpoint is alive; an old start with an old checkpoint is dead
        ({"status": "in_progress", "started_at": (NOW - timedelta(hours=3)).isoformat(),
          "updated_at": (NOW - timedelta(minutes=2)).isoformat()}, IN_PROGRESS),
        ({"status": "in_progress", "started_at": (NOW - timedelta(hours=3)).isoformat(),
          "updated_at": (NOW - timedelta(hours=2)).isoformat()}, CLAIMED),
    ],
)
def test_decide_claim_matrix(existing, expected):
    assert decide_claim(existing, now=NOW, stale_seconds=3600) == expected


def test_decide_claim_stale_window_below_cron_period_reclaims_a_killed_run_on_the_next_tick():
    """The invariant behind MARKETING_RUN_STALE_SECONDS=2700: tick-1 claimed at +8 s, the
    container died, tick-2 lands at +3603 s. With a 3600 s window this was a coin flip."""
    started = NOW + timedelta(seconds=8)
    tick2 = NOW + timedelta(seconds=3603)
    row = {"status": "in_progress", "started_at": started.isoformat()}
    assert decide_claim(row, now=tick2, stale_seconds=2700) == CLAIMED
    # and a genuinely alive run (checkpointed 5 minutes ago) is still left alone
    row["updated_at"] = (tick2 - timedelta(minutes=5)).isoformat()
    assert decide_claim(row, now=tick2, stale_seconds=2700) == IN_PROGRESS


def test_decide_claim_attempts_cap_and_nonce():
    dead = {"status": "failed", "attempts": 6}
    assert decide_claim(dead, now=NOW, stale_seconds=60, max_attempts=6) == ATTEMPTS_EXHAUSTED
    assert decide_claim(dead, now=NOW, stale_seconds=60, max_attempts=0) == CLAIMED
    assert decide_claim({"status": "failed", "attempts": 5}, now=NOW, stale_seconds=60, max_attempts=6) == CLAIMED
    # our own fresh claim, response lost → recognised by nonce, not "someone else has it"
    mine = {"status": "in_progress", "started_at": NOW.isoformat(), "metadata": {"claim_nonce": "abc"}}
    assert decide_claim(mine, now=NOW, stale_seconds=3600, claim_nonce="abc") == CLAIMED
    assert decide_claim(mine, now=NOW, stale_seconds=3600, claim_nonce="zzz") == IN_PROGRESS
    assert decide_claim(mine, now=NOW, stale_seconds=3600) == IN_PROGRESS


def test_decide_claim_with_zero_stale_window_always_reclaims_in_progress():
    row = {"status": "in_progress", "started_at": NOW.isoformat()}
    assert decide_claim(row, now=NOW, stale_seconds=0) == CLAIMED
    assert decide_claim(row, now=NOW, stale_seconds=-5) == CLAIMED


# ── an in-memory PostgREST fake ───────────────────────────────────────────────


class _UniqueViolation(Exception):
    code = "23505"


class _Result:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, table: "_Table", op: str, payload=None):
        self.t, self.op, self.payload = table, op, payload
        self.filters: List[tuple] = []
        self._limit: Optional[int] = None

    def select(self, *_):
        return self

    def eq(self, col, val):
        self.filters.append((col, val))
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row):
        return all(str(row.get(c)) == str(v) for c, v in self.filters)

    def execute(self):
        if self.op == "select":
            rows = [dict(r) for r in self.t.rows if self._matches(r)]
            return _Result(rows[: self._limit] if self._limit else rows)
        if self.op == "insert":
            row = dict(self.payload)
            for cols in self.t.unique:
                key = tuple(str(row.get(c)) for c in cols)
                if any(tuple(str(r.get(c)) for c in cols) == key for r in self.t.rows):
                    raise _UniqueViolation(f"duplicate {cols}")
            row.setdefault("id", str(uuid.uuid4()))
            for k, v in self.t.defaults.items():
                row.setdefault(k, v() if callable(v) else v)
            self.t.rows.append(row)
            return _Result([dict(row)])
        if self.op == "update":
            out = []
            for r in self.t.rows:
                if self._matches(r):
                    r.update(self.payload)
                    out.append(dict(r))
            return _Result(out)
        raise AssertionError(self.op)


class _Table:
    def __init__(self, unique, defaults=None):
        self.rows: List[Dict[str, Any]] = []
        self.unique = unique
        self.defaults = defaults or {}

    def select(self, *_):
        return _Query(self, "select")

    def insert(self, payload):
        return _Query(self, "insert", payload)

    def update(self, payload):
        return _Query(self, "update", payload)


class _Bucket:
    def __init__(self, store):
        self.store = store

    def create_signed_upload_url(self, path):
        return {"signed_url": f"https://sb.example/upload/sign/marketing-media/{path}?token=t", "token": "t", "path": path}

    def exists(self, path):
        return path in self.store

    def list(self, prefix, options=None):
        name = (options or {}).get("search")
        return [{"name": p.rsplit("/", 1)[-1]} for p in self.store
                if p.rsplit("/", 1)[0] == prefix and (not name or p.endswith(name))]


class _Storage:
    def __init__(self, store):
        self.store = store

    def from_(self, _bucket):
        return _Bucket(self.store)


class FakeSupabase:
    def __init__(self):
        self.objects: set = set()
        self.tables = {
            mrs.RUNS: _Table([("run_date",)], {"stage": "planned", "status": "planned", "attempts": 0,
                                                "timings": dict, "metadata": dict, "content_class": "A"}),
            mrs.ASSETS: _Table([("storage_path",)], {"metadata": dict}),
            mrs.POSTS: _Table([("idempotency_key",), ("run_id", "platform", "format")],
                              {"attempts": 0, "cost_micros": 0, "metadata": dict}),
        }
        self.storage = _Storage(self.objects)

    def table(self, name):
        return self.tables[name]


@pytest.fixture
def svc(monkeypatch):
    fake = FakeSupabase()
    monkeypatch.setattr(mrs.settings, "MARKETING_RUN_STALE_SECONDS", 3600)
    monkeypatch.setattr(mrs.settings, "MARKETING_MAX_RUN_ATTEMPTS", 6)
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", False)
    service = MarketingRunService(supabase=fake)
    service.fake = fake  # type: ignore[attr-defined]
    return service


# ── claim ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_claim_inserts_and_second_claim_sees_in_progress(svc):
    row, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    assert reason == CLAIMED and row["status"] == "in_progress" and row["attempts"] == 1
    again, reason2 = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    assert reason2 == IN_PROGRESS and again["id"] == row["id"]
    assert len(svc.fake.tables[mrs.RUNS].rows) == 1


@pytest.mark.asyncio
async def test_failed_run_is_reclaimed_with_attempts_bumped_and_error_cleared(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    await svc.update_run(row["id"], status="failed", last_error="boom", finished=True)
    re, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t2", dry_run=False, now=NOW)
    assert reason == CLAIMED
    assert re["attempts"] == 2 and re["last_error"] is None and re["finished_at"] is None
    assert re["worker_version"] == "t2" and re["dry_run"] is False


@pytest.mark.asyncio
async def test_stale_in_progress_is_reclaimed_but_fresh_is_not(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW - timedelta(hours=3))
    re, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    assert reason == CLAIMED and re["attempts"] == 2
    _, reason2 = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    assert reason2 == IN_PROGRESS


@pytest.mark.asyncio
async def test_terminal_runs_are_never_reclaimed(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    await svc.update_run(row["id"], status="skipped", finished=True)
    _, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    assert reason == ALREADY_DONE
    await svc.update_run(row["id"], status="media_ready")
    _, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    assert reason == MEDIA_READY


@pytest.mark.asyncio
async def test_two_claimers_on_one_stale_snapshot_yield_exactly_one_winner(svc):
    """The CAS is on `attempts` (which the re-claim increments), not on `status` (which the
    re-claim leaves at in_progress — a no-op guard, so both writers used to win)."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW - timedelta(hours=3))
    table = svc.fake.tables[mrs.RUNS]
    # Interleave: both read the same stale snapshot, then both write.
    snap_a = dict(table.rows[0]); snap_b = dict(table.rows[0])

    async def reclaim(snapshot):
        upd = _Query(table, "update", {"status": "in_progress", "attempts": int(snapshot["attempts"]) + 1,
                                       "started_at": NOW.isoformat(), "updated_at": NOW.isoformat()})
        upd.eq("id", snapshot["id"]).eq("status", snapshot["status"]).eq("attempts", snapshot["attempts"])
        return upd.execute().data

    a = await reclaim(snap_a)
    b = await reclaim(snap_b)
    assert (len(a), len(b)) == (1, 0)
    assert table.rows[0]["attempts"] == 2  # not 3, not lost


@pytest.mark.asyncio
async def test_lost_claim_response_is_recovered_by_nonce(svc):
    row, r1 = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce="n0nce123")
    assert r1 == CLAIMED and row["metadata"]["claim_nonce"] == "n0nce123"
    again, r2 = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce="n0nce123")
    assert r2 == CLAIMED and again["id"] == row["id"] and again["attempts"] == 1
    other, r3 = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce="different")
    assert r3 == IN_PROGRESS


@pytest.mark.asyncio
async def test_resume_only_never_creates_and_resumes_a_failed_yesterday(svc):
    run, reason = await svc.claim_run(date(2026, 9, 16), worker_version="t", dry_run=True, now=NOW, resume_only=True)
    assert run is None and reason == NO_RUN
    assert svc.fake.tables[mrs.RUNS].rows == []
    row, _ = await svc.claim_run(date(2026, 9, 16), worker_version="t", dry_run=True, now=NOW - timedelta(hours=5))
    await svc.update_run(row["id"], status="failed", last_error="killed", finished=True)
    re, reason = await svc.claim_run(date(2026, 9, 16), worker_version="t", dry_run=True, now=NOW, resume_only=True)
    assert reason == CLAIMED and re["id"] == row["id"] and re["attempts"] == 2


@pytest.mark.asyncio
async def test_attempts_are_capped(svc, monkeypatch):
    monkeypatch.setattr(mrs.settings, "MARKETING_MAX_RUN_ATTEMPTS", 3)
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    for _ in range(2):
        await svc.update_run(row["id"], status="failed", finished=True)
        _, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
        assert reason == CLAIMED
    await svc.update_run(row["id"], status="failed", finished=True)
    cur, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    assert reason == ATTEMPTS_EXHAUSTED and cur["attempts"] == 3


@pytest.mark.asyncio
async def test_raw_ledger_errors_are_wrapped_not_leaked(svc, monkeypatch):
    class Boom(Exception):
        pass

    def explode(self):
        raise Boom("edge 520")

    monkeypatch.setattr(_Query, "execute", explode)
    with pytest.raises(mrs.MarketingRunError, match="get_run_by_date"):
        await svc.get_run_by_date(date(2026, 9, 17))


# ── update ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_merges_timings_and_metadata_instead_of_replacing(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    await svc.update_run(row["id"], stage="selected", timings={"select_s": 1.5}, metadata={"a": 1})
    upd = await svc.update_run(row["id"], stage="scripted", timings={"script_s": 2}, metadata={"b": 2})
    assert upd["stage"] == "scripted"
    assert upd["timings"] == {"select_s": 1.5, "script_s": 2.0}
    assert upd["metadata"] == {"a": 1, "b": 2}
    assert upd.get("finished_at") is None


@pytest.mark.asyncio
async def test_update_rejects_unknown_stage_and_missing_run(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    with pytest.raises(ValueError):
        await svc.update_run(row["id"], stage="teleported")
    with pytest.raises(ValueError):
        await svc.update_run(row["id"], status="vanished")
    with pytest.raises(MarketingRunNotFound):
        await svc.update_run(str(uuid.uuid4()), stage="selected")


@pytest.mark.asyncio
async def test_last_error_is_truncated_to_the_column_budget(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    upd = await svc.update_run(row["id"], status="failed", last_error="x" * 5000)
    assert len(upd["last_error"]) == 2000


# ── assets ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_then_complete_asset_requires_the_object_to_exist(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    asset, upload = await svc.register_asset(row["id"], kind="manifest", ext="json", sha256=SHA, size_bytes=12)
    assert asset["status"] == "pending_upload"
    assert asset["storage_path"] == "2026-09-17/manifest-aaaaaaaaaaaaaaaa.json"
    assert upload["path"] == asset["storage_path"] and upload["token"] == "t"
    assert upload["content_type"] == "application/json" and upload["bucket"] == "marketing-media"
    # The worker claims it uploaded, but nothing is in the bucket → refuse.
    with pytest.raises(MarketingAssetMissingInStorage):
        await svc.complete_asset(asset["id"])
    svc.fake.objects.add(asset["storage_path"])
    done = await svc.complete_asset(asset["id"])
    assert done["status"] == "ready"


@pytest.mark.asyncio
async def test_storage_outage_on_complete_is_a_ledger_error_not_asset_missing(svc, monkeypatch):
    """storage3.exists() answers False for ANY non-200 HEAD, so a 5xx used to read as 'the
    worker never uploaded' (409, terminal). The LIST confirmation raises on an outage."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    asset, _ = await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1)

    def outage(self, prefix, options=None):
        raise RuntimeError("storage 520")

    monkeypatch.setattr(_Bucket, "list", outage)
    with pytest.raises(mrs.MarketingRunError, match="LIST failed"):
        await svc.complete_asset(asset["id"])


@pytest.mark.asyncio
async def test_re_registering_identical_bytes_returns_the_row_without_a_new_upload(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    a1, up1 = await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1)
    # Not yet ready: a resumed run gets a FRESH signed URL for the same row.
    a2, up2 = await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1)
    assert a2["id"] == a1["id"] and up2 is not None
    svc.fake.objects.add(a1["storage_path"])
    await svc.complete_asset(a1["id"])
    a3, up3 = await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1)
    assert a3["id"] == a1["id"] and a3["status"] == "ready" and up3 is None
    assert len(svc.fake.tables[mrs.ASSETS].rows) == 1


@pytest.mark.asyncio
async def test_pending_row_whose_object_already_landed_is_finished_without_a_new_url(svc):
    """The wedge: PUT succeeded, process died before `complete`. The next tick must not be
    handed a URL it can only 409 against — the row is completed from the bucket's truth."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    a1, up1 = await svc.register_asset(row["id"], kind="audio", ext="m4a", sha256=SHA, size_bytes=1)
    assert up1 is not None and a1["status"] == "pending_upload"
    svc.fake.objects.add(a1["storage_path"])          # the bytes landed; complete never ran
    a2, up2 = await svc.register_asset(row["id"], kind="audio", ext="m4a", sha256=SHA, size_bytes=1)
    assert a2["id"] == a1["id"] and a2["status"] == "ready" and up2 is None


@pytest.mark.asyncio
async def test_existence_precheck_failure_still_mints_a_url(svc, monkeypatch):
    """A Storage blip on the pre-check must not block the upload path; the PUT will tell."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)

    def boom(self, path):
        raise RuntimeError("storage 520")

    monkeypatch.setattr(_Bucket, "exists", boom)
    a, up = await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1)
    assert a["status"] == "pending_upload" and up is not None


@pytest.mark.asyncio
async def test_register_asset_on_unknown_run_is_loud(svc):
    with pytest.raises(MarketingRunNotFound):
        await svc.register_asset(str(uuid.uuid4()), kind="video", ext="mp4", sha256=SHA, size_bytes=1)


# ── posts ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_posts_are_born_pending_review_and_recreation_is_idempotent(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    specs = [{"platform": "x", "format": "text", "caption": "hi"},
             {"platform": "tiktok", "format": "video", "caption": "hi", "asset_ids": ["a"]}]
    first = await svc.create_posts(row["id"], specs)
    assert [p["status"] for p in first] == ["pending_review", "pending_review"]
    assert first[0]["idempotency_key"] == "2026-09-17:x:text"
    # An admin approves one; a resumed worker re-sends the same specs → nothing is reset.
    await svc.mark_post(first[0]["id"], "approved")
    again = await svc.create_posts(row["id"], specs)
    assert [p["id"] for p in again] == [p["id"] for p in first]
    assert again[0]["status"] == "approved"
    assert len(svc.fake.tables[mrs.POSTS].rows) == 2


@pytest.mark.asyncio
async def test_auto_publish_births_posts_approved(svc, monkeypatch):
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", True)
    # A real (non-dry-run) run is the only one auto-publish may approve.
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW)
    posts = await svc.create_posts(row["id"], [{"platform": "bluesky", "format": "text"}])
    assert posts[0]["status"] == "approved" and posts[0]["approved_by"] == "auto"


@pytest.mark.asyncio
async def test_a_dry_run_day_never_auto_approves_and_marks_every_row(svc, monkeypatch):
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", True)
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    (post,) = await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}])
    assert post["status"] == "pending_review" and post["metadata"]["dry_run"] is True
    real, _ = await svc.claim_run(date(2026, 9, 18), worker_version="t", dry_run=False, now=NOW)
    (p2,) = await svc.create_posts(real["id"], [{"platform": "x", "format": "text"}])
    assert p2["status"] == "approved" and p2["metadata"]["dry_run"] is False


@pytest.mark.asyncio
async def test_mark_post_rejects_unknown_columns_and_statuses(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    (post,) = await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}])
    with pytest.raises(ValueError, match="not writable"):
        await svc.mark_post(post["id"], "published", run_id="other")
    with pytest.raises(ValueError, match="unknown post status"):
        await svc.mark_post(post["id"], "teleported")
    ok = await svc.mark_post(post["id"], "published", cost_micros=15000, external_id="tw-1")
    assert ok["cost_micros"] == 15000


@pytest.mark.asyncio
async def test_claim_post_is_atomic_on_status(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW)
    (post,) = await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}])
    assert await svc.claim_post(post["id"]) is None  # pending_review is not claimable
    await svc.mark_post(post["id"], "approved")
    first = await svc.claim_post(post["id"])
    assert first["status"] == "queued" and first["claimed_at"]
    assert await svc.claim_post(post["id"]) is None  # second tick loses
    assert [p["id"] for p in await svc.list_posts("queued")] == [post["id"]]


def test_schema_constants_match_the_migration_check_constraints():
    """The CHECK lists in 170 and the tuples in schemas/marketing.py must agree, or a
    valid-looking request 23514s with a message that names nothing."""
    import re
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "database" / "migrations" / "170_marketing_engine.sql").read_text()
    sql = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))

    def check_values(column: str) -> set:
        m = re.search(rf"{column}\s+TEXT[^,]*?CHECK \({column} IN \(([^)]*)\)\)", sql, re.S)
        assert m, f"no CHECK found for {column}"
        return set(re.findall(r"'([a-z_A-Z]+)'", m.group(1)))

    assert check_values("status") == set(schemas.RUN_STATUSES)  # first `status` CHECK is runs
    assert check_values("stage") == set(schemas.RUN_STAGES)
    assert check_values("content_class") == set(schemas.CONTENT_CLASSES)
    assert check_values("kind") == set(schemas.ASSET_KINDS)
    assert check_values("platform") == set(schemas.POST_PLATFORMS)
    assert check_values("format") == set(schemas.POST_FORMATS)
    # posts.status is the SECOND status CHECK in the file
    statuses = re.findall(r"status\s+TEXT[^,]*?CHECK \(status IN \(([^)]*)\)\)", sql, re.S)
    assert len(statuses) == 3, "expected runs, assets and posts status CHECKs"
    assert set(re.findall(r"'([a-z_]+)'", statuses[1])) == set(schemas.ASSET_STATUSES)
    assert set(re.findall(r"'([a-z_]+)'", statuses[2])) == set(schemas.POST_STATUSES)
    # And the bucket's mime allow-list is exactly the extension map's values.
    m = re.search(r"ARRAY\[([^\]]*)\]", sql)
    assert set(re.findall(r"'([a-z0-9/.+-]+)'", m.group(1))) == set(schemas.ASSET_EXTENSIONS.values())
