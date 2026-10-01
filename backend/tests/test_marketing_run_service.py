"""
Marketing run ledger (migration 170 / `app/services/marketing/run_service.py`) — the claim matrix, the
content-addressed paths, and the service against an in-memory PostgREST fake.

No network: the fake below stands in for `get_supabase()` (conftest blocks sockets anyway).
"""

from __future__ import annotations

import copy
import json
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
    MarketingRequestInvalid,
    MarketingRunNotFound,
    MarketingRunNotHeld,
    MarketingRunService,
    claim_window_ok,
    decide_claim,
    held_problem,
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
    for bad in [("bogus", SHA, "mp4"), ("video", SHA, "exe"), ("video", "abc", "mp4"),
                # kind and extension are paired: a video row can never point at a JSON object
                ("video", SHA, "json"), ("manifest", SHA, "mp4"), ("card", SHA, "mp3")]:
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


def _unique_violation(cols) -> Exception:
    # What postgrest-py actually raises for a duplicate key (a str code on the PostgREST path).
    from postgrest.exceptions import APIError

    return APIError({"code": "23505", "message": f"duplicate key value violates unique constraint {cols}",
                     "details": None, "hint": None})


class _Result:
    def __init__(self, data):
        self.data = data


def _same_value(stored: Any, wanted: Any) -> bool:
    a, b = str(stored), str(wanted)
    if a == b:
        return True
    if "T" in a and "T" in b:  # both timestamps: compare instants
        da, db = mrs._parse_ts(a), mrs._parse_ts(b)
        return da is not None and da == db
    return False


def _order_key(value: Any) -> Any:
    """How Postgres orders a value against a filter: a timestamptz by INSTANT (`…+00:00` and `…Z`
    and a missing microsecond part are the same instant), anything else as text (ISO dates sort
    correctly as text)."""
    text = str(value)
    if "T" in text:
        ts = mrs._parse_ts(text)
        if ts is not None:
            return (0, ts)
    return (1, text)


def _compare(stored: Any, wanted: Any, op: str) -> bool:
    """`col <op> value` with SQL NULL semantics (a NULL column matches no comparison)."""
    if stored is None:
        return False
    a, b = _order_key(stored), _order_key(wanted)
    if a[0] != b[0]:  # one timestamp, one plain text: compare as text, like a cast would
        a, b = (1, str(stored)), (1, str(wanted))
    return {"lt": a < b, "lte": a <= b, "gt": a > b, "gte": a >= b}[op]


def _col(row: Dict[str, Any], col: str) -> Any:
    """A column, or a PostgREST `json->>key` text path into a JSONB column."""
    if "->>" in col:
        base, key = col.split("->>", 1)
        doc = row.get(base)
        value = doc.get(key) if isinstance(doc, dict) else None
        if value is None:
            return None
        # `->>` renders a JSON value as TEXT: a string as itself, anything else as its JSON
        # (`false`, not Python's `False` — the publisher filters on `metadata->>dry_run = 'false'`).
        return value if isinstance(value, str) else json.dumps(value)
    return row.get(col)


class _Query:
    def __init__(self, table: "_Table", op: str, payload=None):
        self.t, self.op, self.payload = table, op, payload
        self.filters: List[Any] = []  # row predicates, AND-ed like PostgREST query params
        self._order: List[tuple] = []
        self._limit: Optional[int] = None
        self._negate = False

    def select(self, *_):
        return self

    @property
    def not_(self):
        self._negate = True
        return self

    def eq(self, col, val):
        # SQL: `NULL = x` is never true, so a NULL column matches no eq filter. A timestamptz
        # compares by INSTANT, not by text: `…+00:00` stored and `…Z` filtered are equal, as in
        # Postgres (the service renders filter timestamps in the `Z` form, see `_ts_filter`).
        # `metadata->>claim_nonce` is PostgREST's JSON text path (the caller-claim fence).
        assert not self._negate, "the fake models not_ only in front of is_ and in_"
        self.filters.append(lambda r, c=col, v=val: _col(r, c) is not None and _same_value(_col(r, c), v))
        return self

    def in_(self, col, values):
        # `col=in.(a,b)`: NULL is in no list — and NOT in no list either (`not.in` never matches a
        # NULL column, as in SQL). `col` may be a `json->>key` text path.
        neg, self._negate = self._negate, False
        wanted = [str(v) for v in values]

        def _in(r, c=col):
            v = _col(r, c)
            if v is None:
                return False
            return (str(v) not in wanted) if neg else (str(v) in wanted)

        self.filters.append(_in)
        return self

    def is_(self, col, val):
        # postgrest-py `is_(col, "null")` → `col=is.null`. Only NULL is modelled; anything else
        # fails loudly instead of being silently mis-simulated. `col` may be a `json->>key` path.
        assert val == "null", f"the fake models is_(col, 'null') only, got {val!r}"
        neg, self._negate = self._negate, False
        self.filters.append(lambda r, c=col: (_col(r, c) is not None) if neg else (_col(r, c) is None))
        return self

    def _cmp_filter(self, op, col, val):
        assert not self._negate, "the fake models not_ only in front of is_ and in_"
        self.filters.append(lambda r, c=col, v=val: _compare(_col(r, c), v, op))
        return self

    def lt(self, col, val):
        # `NULL < x` is never true; timestamps compare by instant, everything else as text.
        return self._cmp_filter("lt", col, val)

    def lte(self, col, val):
        return self._cmp_filter("lte", col, val)

    def gt(self, col, val):
        return self._cmp_filter("gt", col, val)

    def gte(self, col, val):
        return self._cmp_filter("gte", col, val)

    def order(self, col, *, desc=False, nullsfirst=None, **_k):
        self._order.append((col, desc, nullsfirst))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def _matches(self, row):
        return all(f(row) for f in self.filters)

    def _sorted(self, rows):
        # ORDER BY is applied BEFORE LIMIT, as in Postgres, with its NULL placement defaults
        # (ASC → NULLS LAST, DESC → NULLS FIRST). Stable, so earlier keys stay primary.
        for col, desc, nullsfirst in reversed(self._order):
            nulls_first = desc if nullsfirst is None else nullsfirst
            present = sorted((r for r in rows if r.get(col) is not None),
                             key=lambda r: r[col], reverse=desc)
            absent = [r for r in rows if r.get(col) is None]
            rows = absent + present if nulls_first else present + absent
        return rows

    def execute(self):
        if self.op == "select":
            rows = self._sorted([dict(r) for r in self.t.rows if self._matches(r)])
            return _Result(rows if self._limit is None else rows[: self._limit])
        if self.op == "insert":
            row = dict(self.payload)
            for cols in self.t.unique:
                key = tuple(str(row.get(c)) for c in cols)
                if any(tuple(str(r.get(c)) for c in cols) == key for r in self.t.rows):
                    raise _unique_violation(cols)
            if self.t.generated_id:
                row.setdefault("id", str(uuid.uuid4()))
            for k, v in self.t.defaults.items():
                row.setdefault(k, v() if callable(v) else v)
            self.t.rows.append(row)
            return _Result([dict(row)])
        if self.op == "update":
            if self.t.fail_updates:
                # A test-injected ledger failure (a PostgREST 5xx, a dropped connection) on the
                # NEXT update of this table — nothing is written.
                raise self.t.fail_updates.pop(0)
            out = []
            for r in self.t.rows:
                if self._matches(r):
                    r.update(self.payload)
                    out.append(dict(r))
            return _Result(out)
        raise AssertionError(self.op)


class _Table:
    def __init__(self, unique, defaults=None, *, generated_id=True):
        self.rows: List[Dict[str, Any]] = []
        self.unique = unique
        self.defaults = defaults or {}
        # False for a table whose PK is a natural key (marketing_scripts: run_id) — it has no
        # `id` column, so the fake must not invent one a caller could come to rely on.
        self.generated_id = generated_id
        #: Exceptions raised (one each, in order) by the next UPDATEs of this table.
        self.fail_updates: List[Exception] = []

    def select(self, *_):
        return _Query(self, "select")

    def insert(self, payload):
        return _Query(self, "insert", payload)

    def update(self, payload):
        return _Query(self, "update", payload)


class _Bucket:
    """An object is a PATH in `store`. Its listed `metadata` (size, mimetype — what Storage
    recorded for the upload) is `meta[path]` when a test set one, else the registered asset row's
    own bytes and content type: an upload that matches what was registered, the normal case."""

    def __init__(self, store, meta, assets):
        self.store, self.meta, self.assets = store, meta, assets

    def create_signed_upload_url(self, path):
        return {"signed_url": f"https://sb.example/upload/sign/marketing-media/{path}?token=t", "token": "t", "path": path}

    def exists(self, path):
        return path in self.store

    def _metadata(self, path):
        if path in self.meta:
            return self.meta[path]
        row = next((r for r in self.assets.rows if r.get("storage_path") == path), None)
        if row is None:
            return {"size": 0, "mimetype": "application/octet-stream"}
        return {"size": row.get("bytes"), "mimetype": row.get("content_type")}

    def list(self, prefix, options=None):
        name = (options or {}).get("search")
        return [{"name": p.rsplit("/", 1)[-1], "metadata": self._metadata(p)} for p in self.store
                if p.rsplit("/", 1)[0] == prefix and (not name or p.endswith(name))]

    def remove(self, paths):
        self.removed.extend(paths)
        for p in paths:
            self.store.discard(p)
        return [{"name": p} for p in paths]


class _Storage:
    def __init__(self, store, meta, assets):
        self.store, self.meta, self.assets = store, meta, assets
        self.removed: list = []

    def from_(self, _bucket):
        bucket = _Bucket(self.store, self.meta, self.assets)
        bucket.removed = self.removed
        return bucket


class FakeSupabase:
    def __init__(self):
        self.objects: set = set()
        #: path -> {"size", "mimetype"} overriding what the listing reports (a mismatch test).
        self.object_meta: dict = {}
        self.tables = {
            mrs.RUNS: _Table([("run_date",)], {"stage": "planned", "status": "planned", "attempts": 0,
                                                "timings": dict, "metadata": dict, "content_class": "A"}),
            mrs.ASSETS: _Table([("storage_path",)], {"metadata": dict}),
            mrs.POSTS: _Table([("idempotency_key",), ("run_id", "platform", "format")],
                              {"attempts": 0, "cost_micros": 0, "metadata": dict}),
            # migration 173: run_id is the PRIMARY KEY (first-write-wins selection claim).
            mrs.SCRIPTS: _Table([("run_id",)], {"status": "selected", "fact_sheet": dict,
                                                "violations": list, "generations": 0,
                                                "tokens_used": 0}, generated_id=False),
        }
        self.storage = _Storage(self.objects, self.object_meta, self.tables[mrs.ASSETS])

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


def _server_copy(platform: str) -> Dict[str, Any]:
    return {"platform": platform, "title": f"Server title ({platform})",
            "caption": f"server copy for {platform}"}


_NONCES = iter(f"{i:032x}" for i in range(1, 10**6))


def _n() -> str:
    """A fresh claim nonce (hex, the shape the worker mints) — distinct per claim, so two
    claims in one test are two processes unless the test passes the same one on purpose."""
    return next(_NONCES)


def _holder(svc, run_id: str) -> mrs.CallerClaim:
    """The claim of whoever holds `run_id` NOW (its row's attempts + nonce): the caller every
    worker write in this file speaks as. Zombie claims are built explicitly where tested."""
    for r in svc.fake.tables[mrs.RUNS].rows:
        if r.get("id") == run_id:
            meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
            return mrs.CallerClaim(int(r.get("attempts") or 0), meta.get("claim_nonce") or "")
    return mrs.CallerClaim(1, "0" * 32)


def _asset_holder(svc, asset_id: str) -> mrs.CallerClaim:
    for a in svc.fake.tables[mrs.ASSETS].rows:
        if a.get("id") == asset_id:
            return _holder(svc, a.get("run_id"))
    return mrs.CallerClaim(1, "0" * 32)


#: The accepted script's disclaimer card and one card: what a rendered video may draw (§12.8).
_DISCLAIMER_CARD = "Educational only, not advice. Caydex · Sep 17, 2026"
_CARD = {"title": "Why it matters", "body": "A calm plan beats a loud market."}


async def _accept_script(svc, run_id: str, platforms, **extra) -> Dict[str, Any]:
    """Seed the run's ACCEPTED script (migration 173) carrying composed copy for `platforms` —
    what `create_posts` requires before it records any post (the caption is server-authored) —
    judged in `enforce` mode (anything else never becomes a post), with a card and a disclaimer
    card for a video to draw."""
    row = {
        "run_id": run_id, "status": "accepted", "source_ref": "money_moves:test-item",
        "template_id": "checklist", "generation_id": str(uuid.uuid4()),
        "output": {"posts": {p: _server_copy(p) for p in platforms}, "judge": {"mode": "enforce"},
                   "cards": [dict(_CARD)], "disclaimer_card": _DISCLAIMER_CARD},
        **extra,
    }
    created, ours = await svc.insert_script(row)
    assert ours, f"a script already existed for run {run_id}"
    return created


def _ready_voice(svc, run_id: str) -> Dict[str, Any]:
    """A ready, checked narration of `run_id` (seeded directly: its own registration check is
    tested elsewhere) — what a video's burned captions must come from."""
    rows = svc.fake.tables[mrs.ASSETS].rows
    existing = next((a for a in rows if a.get("run_id") == run_id and a.get("kind") == "audio"), None)
    if existing is not None:
        return existing
    voice = {"id": str(uuid.uuid4()), "run_id": run_id, "kind": "audio", "status": "ready",
             "storage_path": f"voice/{run_id}.m4a", "content_type": "audio/mp4", "bytes": 10,
             "sha256": "9" * 64, "metadata": {"words": [{"w": "hi", "s": 0.0, "e": 0.5, "line": 0}]}}
    rows.append(voice)
    return voice


def _video_metadata(svc, run_id: str) -> Dict[str, Any]:
    """What a well-behaved worker declares for a video: every string it drew (the card and the
    disclaimer card of the accepted script, the end card) and the narration its captions burn."""
    return {"onscreen_text": [_CARD["title"], _CARD["body"], _DISCLAIMER_CARD, "caydexinvest.com"],
            "voice_asset_id": _ready_voice(svc, run_id)["id"]}


async def _ready_asset(svc, run_id: str, sha: str = SHA) -> Dict[str, Any]:
    """A `ready` VIDEO of `run_id`: registered, uploaded (object in the fake bucket), verified."""
    asset, _ = await svc.register_asset(run_id, kind="video", ext="mp4", sha256=sha, size_bytes=1,
                                        metadata=_video_metadata(svc, run_id), claim=_holder(svc, run_id))
    svc.fake.objects.add(asset["storage_path"])
    return await svc.complete_asset(asset["id"], claim=_asset_holder(svc, asset["id"]))


# ── claim ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_claim_inserts_and_second_claim_sees_in_progress(svc):
    row, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    assert reason == CLAIMED and row["status"] == "in_progress" and row["attempts"] == 1
    again, reason2 = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    assert reason2 == IN_PROGRESS and again["id"] == row["id"]
    assert len(svc.fake.tables[mrs.RUNS].rows) == 1


@pytest.mark.asyncio
async def test_failed_run_is_reclaimed_with_attempts_bumped_and_error_cleared(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    await svc.update_run(row["id"], status="failed", last_error="boom", finished=True)
    re, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t2", dry_run=False, now=NOW, claim_nonce=_n())
    assert reason == CLAIMED
    assert re["attempts"] == 2 and re["last_error"] is None and re["finished_at"] is None
    assert re["worker_version"] == "t2" and re["dry_run"] is False


@pytest.mark.asyncio
async def test_stale_in_progress_is_reclaimed_but_fresh_is_not(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW - timedelta(hours=3), claim_nonce=_n())
    re, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    assert reason == CLAIMED and re["attempts"] == 2
    _, reason2 = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    assert reason2 == IN_PROGRESS


@pytest.mark.asyncio
async def test_terminal_runs_are_never_reclaimed(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    await svc.update_run(row["id"], status="skipped", finished=True)
    _, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    assert reason == ALREADY_DONE
    await svc.update_run(row["id"], status="media_ready")
    _, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    assert reason == MEDIA_READY


@pytest.mark.asyncio
async def test_two_claimers_on_one_stale_snapshot_yield_exactly_one_winner(svc, monkeypatch):
    """Through `claim_run` itself: both claimers read the SAME stale `in_progress` snapshot
    (patched on the instance, which `claim_run` reads through `self`), then both write. The CAS
    is on `attempts` (which the re-claim increments), not on `status` (which the re-claim leaves
    at in_progress — a no-op guard, so both writers used to win). A STALE in_progress row, not a
    failed one: on `failed` the status guard alone would stop the second writer and hide a
    regression that drops `attempts`. Fresh nonces for both, or the nonce-recovery early return
    would skip the CAS altogether."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="orig", dry_run=True,
                                 now=NOW - timedelta(hours=3), claim_nonce="nonce-orig")
    snapshot = dict(svc.fake.tables[mrs.RUNS].rows[0])

    async def stale_read(_run_date):
        return dict(snapshot)

    monkeypatch.setattr(svc, "get_run_by_date", stale_read)
    a_row, a = await svc.claim_run(date(2026, 9, 17), worker_version="worker-a", dry_run=True, now=NOW,
                                   claim_nonce="nonce-aaaa")
    b_row, b = await svc.claim_run(date(2026, 9, 17), worker_version="worker-b", dry_run=True, now=NOW,
                                   claim_nonce="nonce-bbbb")
    assert (a, b) == (CLAIMED, IN_PROGRESS)
    stored = svc.fake.tables[mrs.RUNS].rows[0]
    assert stored["attempts"] == 2 and stored["worker_version"] == "worker-a"
    # the loser reports the WINNER's row (re-read through the unpatched get_run)
    assert b_row["worker_version"] == "worker-a" and b_row["metadata"]["claim_nonce"] == "nonce-aaaa"


@pytest.mark.asyncio
async def test_the_fake_ands_update_filters(svc):
    """Self-check of the fake the CAS tests stand on: a conditional UPDATE on a stale
    snapshot matches nothing once another writer bumped `attempts`."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW - timedelta(hours=3), claim_nonce=_n())
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
    run, reason = await svc.claim_run(date(2026, 9, 16), worker_version="t", dry_run=True, now=NOW, resume_only=True, claim_nonce=_n())
    assert run is None and reason == NO_RUN
    assert svc.fake.tables[mrs.RUNS].rows == []
    row, _ = await svc.claim_run(date(2026, 9, 16), worker_version="t", dry_run=True, now=NOW - timedelta(hours=5), claim_nonce=_n())
    await svc.update_run(row["id"], status="failed", last_error="killed", finished=True)
    re, reason = await svc.claim_run(date(2026, 9, 16), worker_version="t", dry_run=True, now=NOW, resume_only=True, claim_nonce=_n())
    assert reason == CLAIMED and re["id"] == row["id"] and re["attempts"] == 2


@pytest.mark.asyncio
async def test_attempts_are_capped(svc, monkeypatch):
    monkeypatch.setattr(mrs.settings, "MARKETING_MAX_RUN_ATTEMPTS", 3)
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    for _ in range(2):
        await svc.update_run(row["id"], status="failed", finished=True)
        _, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
        assert reason == CLAIMED
    await svc.update_run(row["id"], status="failed", finished=True)
    cur, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
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
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    await svc.update_run(row["id"], stage="selected", timings={"select_s": 1.5}, metadata={"a": 1})
    upd = await svc.update_run(row["id"], stage="scripted", timings={"script_s": 2}, metadata={"b": 2})
    assert upd["stage"] == "scripted"
    assert upd["timings"] == {"select_s": 1.5, "script_s": 2.0}
    # The claim's own nonce stays alongside (the merge never drops a key it was not given).
    assert upd["metadata"] == {"a": 1, "b": 2, "claim_nonce": row["metadata"]["claim_nonce"]}
    assert upd.get("finished_at") is None


@pytest.mark.asyncio
async def test_update_rejects_unknown_stage_and_missing_run(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    with pytest.raises(ValueError):
        await svc.update_run(row["id"], stage="teleported")
    with pytest.raises(ValueError):
        await svc.update_run(row["id"], status="vanished")
    with pytest.raises(MarketingRunNotFound):
        await svc.update_run(str(uuid.uuid4()), stage="selected")


@pytest.mark.asyncio
async def test_last_error_is_truncated_to_the_column_budget(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    upd = await svc.update_run(row["id"], status="failed", last_error="x" * 5000)
    assert len(upd["last_error"]) == 2000


# ── assets ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_then_complete_asset_requires_the_object_to_exist(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    asset, upload = await svc.register_asset(row["id"], kind="manifest", ext="json", sha256=SHA, size_bytes=12, claim=_holder(svc, row["id"]))
    assert asset["status"] == "pending_upload"
    assert asset["storage_path"] == "2026-09-17/manifest-aaaaaaaaaaaaaaaa.json"
    assert upload["path"] == asset["storage_path"] and upload["token"] == "t"
    assert upload["content_type"] == "application/json" and upload["bucket"] == "marketing-media"
    # The worker claims it uploaded, but nothing is in the bucket → refuse.
    with pytest.raises(MarketingAssetMissingInStorage):
        await svc.complete_asset(asset["id"], claim=_asset_holder(svc, asset["id"]))
    svc.fake.objects.add(asset["storage_path"])
    done = await svc.complete_asset(asset["id"], claim=_asset_holder(svc, asset["id"]))
    assert done["status"] == "ready"


@pytest.mark.asyncio
async def test_storage_outage_on_complete_is_a_ledger_error_not_asset_missing(svc, monkeypatch):
    """storage3.exists() answers False for ANY non-200 HEAD, so a 5xx used to read as 'the
    worker never uploaded' (409, terminal). The LIST (which also carries the size and type the
    object is verified against) raises on an outage."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    asset, _ = await svc.register_asset(row["id"], kind="manifest", ext="json", sha256=SHA, size_bytes=1, claim=_holder(svc, row["id"]))

    def outage(self, prefix, options=None):
        raise RuntimeError("storage 520")

    monkeypatch.setattr(_Bucket, "list", outage)
    with pytest.raises(mrs.MarketingRunError, match="LIST failed"):
        await svc.complete_asset(asset["id"], claim=_asset_holder(svc, asset["id"]))


@pytest.mark.asyncio
async def test_re_registering_identical_bytes_returns_the_row_without_a_new_upload(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    a1, up1 = await svc.register_asset(row["id"], kind="manifest", ext="json", sha256=SHA, size_bytes=1, claim=_holder(svc, row["id"]))
    # Not yet ready: a resumed run gets a FRESH signed URL for the same row.
    a2, up2 = await svc.register_asset(row["id"], kind="manifest", ext="json", sha256=SHA, size_bytes=1, claim=_holder(svc, row["id"]))
    assert a2["id"] == a1["id"] and up2 is not None
    svc.fake.objects.add(a1["storage_path"])
    await svc.complete_asset(a1["id"], claim=_asset_holder(svc, a1["id"]))
    a3, up3 = await svc.register_asset(row["id"], kind="manifest", ext="json", sha256=SHA, size_bytes=1, claim=_holder(svc, row["id"]))
    assert a3["id"] == a1["id"] and a3["status"] == "ready" and up3 is None
    assert len(svc.fake.tables[mrs.ASSETS].rows) == 1


@pytest.mark.asyncio
async def test_pending_row_whose_object_already_landed_is_finished_without_a_new_url(svc):
    """The wedge: PUT succeeded, process died before `complete`. The next tick must not be
    handed a URL it can only 409 against — the row is completed from the bucket's truth."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    a1, up1 = await svc.register_asset(row["id"], kind="audio", ext="m4a", sha256=SHA, size_bytes=1, claim=_holder(svc, row["id"]))
    assert up1 is not None and a1["status"] == "pending_upload"
    svc.fake.objects.add(a1["storage_path"])          # the bytes landed; complete never ran
    a2, up2 = await svc.register_asset(row["id"], kind="audio", ext="m4a", sha256=SHA, size_bytes=1, claim=_holder(svc, row["id"]))
    assert a2["id"] == a1["id"] and a2["status"] == "ready" and up2 is None


@pytest.mark.asyncio
async def test_existence_precheck_failure_still_mints_a_url(svc, monkeypatch):
    """A Storage blip on the pre-check must not block the upload path; the PUT will tell."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())

    def boom(self, prefix, options=None):
        raise RuntimeError("storage 520")

    monkeypatch.setattr(_Bucket, "list", boom)
    a, up = await svc.register_asset(row["id"], kind="manifest", ext="json", sha256=SHA, size_bytes=1, claim=_holder(svc, row["id"]))
    assert a["status"] == "pending_upload" and up is not None


@pytest.mark.asyncio
async def test_register_asset_on_unknown_run_is_loud(svc):
    with pytest.raises(MarketingRunNotFound):
        await svc.register_asset(str(uuid.uuid4()), kind="video", ext="mp4", sha256=SHA, size_bytes=1, claim=_holder(svc, str(uuid.uuid4())))


# ── posts ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_posts_are_born_pending_review_and_recreation_is_idempotent(svc, monkeypatch):
    # A REAL run, so the AUTO_PUBLISH flip below is a live threat, not a dry-run no-op.
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    script = await _accept_script(svc, row["id"], ["x", "bluesky", "tiktok"])
    video = await _ready_asset(svc, row["id"])
    # The worker still sends its own words; the ACCEPTED script's copy is what gets recorded.
    specs = [{"platform": "x", "format": "text", "caption": "hi", "title": "worker title"},
             {"platform": "bluesky", "format": "text", "caption": "hi"},
             {"platform": "tiktok", "format": "video", "caption": "hi", "asset_ids": [video["id"]]}]
    first = await svc.create_posts(row["id"], specs, claim=_holder(svc, row["id"]))
    assert [p["status"] for p in first] == ["pending_review"] * 3
    assert [p["approved_by"] for p in first] == [None] * 3
    assert first[0]["idempotency_key"] == "2026-09-17:x:text"
    assert [p["caption"] for p in first] == [_server_copy(p)["caption"] for p in ("x", "bluesky", "tiktok")]
    assert first[0]["title"] == "Server title (x)"
    assert first[2]["asset_ids"] == [video["id"]]
    assert {p["metadata"]["generation_id"] for p in first} == {script["generation_id"]}

    # An admin approves one; a resumed worker re-sends the pairs → every row comes back
    # untouched: the approval is not reset, AUTO_PUBLISH flipped on in between does not approve
    # the text post that was born pending, and a re-rendered video does not re-point the post.
    await svc.mark_post(first[0]["id"], "approved", approved_by="admin", approved_at=NOW.isoformat())
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", True)
    rerender = await _ready_asset(svc, row["id"], sha="b" * 64)
    before = [dict(r) for r in svc.fake.tables[mrs.POSTS].rows]
    again = await svc.create_posts(row["id"], [*specs[:2], {**specs[2], "asset_ids": [rerender["id"]]}], claim=_holder(svc, row["id"]))
    assert [p["id"] for p in again] == [p["id"] for p in first]
    assert [p["status"] for p in again] == ["approved", "pending_review", "pending_review"]
    assert again[0]["approved_by"] == "admin" and again[2]["asset_ids"] == [video["id"]]
    assert svc.fake.tables[mrs.POSTS].rows == before  # re-creation wrote nothing


@pytest.mark.asyncio
async def test_auto_publish_births_posts_approved(svc, monkeypatch):
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", True)
    # A real (non-dry-run) run is the only one auto-publish may approve.
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await _accept_script(svc, row["id"], ["bluesky"])
    posts = await svc.create_posts(row["id"], [{"platform": "bluesky", "format": "text"}], claim=_holder(svc, row["id"]))
    assert posts[0]["status"] == "approved" and posts[0]["approved_by"] == "auto"
    assert posts[0]["approved_at"] and posts[0]["caption"] == "server copy for bluesky"


@pytest.mark.asyncio
async def test_a_dry_run_day_never_auto_approves_and_marks_every_row(svc, monkeypatch):
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", True)
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    await _accept_script(svc, row["id"], ["x"])
    # `metadata` is not the worker's to write: it cannot talk a rehearsal out of dry-run.
    (post,) = await svc.create_posts(
        row["id"], [{"platform": "x", "format": "text", "metadata": {"dry_run": False}}], claim=_holder(svc, row["id"]))
    assert post["status"] == "pending_review" and post["metadata"]["dry_run"] is True
    assert post["approved_by"] is None and post["approved_at"] is None
    real, _ = await svc.claim_run(date(2026, 9, 18), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await _accept_script(svc, real["id"], ["x"])
    (p2,) = await svc.create_posts(real["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, real["id"]))
    assert p2["status"] == "approved" and p2["metadata"]["dry_run"] is False


_ENFORCED = {"mode": "enforce"}


@pytest.mark.parametrize(
    "script",
    [
        # accepted is the ONLY status whose output may be posted
        {"status": "generating", "output": {"posts": {"x": _server_copy("x")}}},
        {"status": "rejected", "output": {"posts": {"x": _server_copy("x")}}},
        # accepted, but the package is missing or malformed (a hand-edited / truncated row)
        {"status": "accepted", "output": None},
        {"status": "accepted", "output": ["not", "a", "package"]},
        {"status": "accepted", "output": {"posts": [_server_copy("x")], "judge": _ENFORCED}},
        {"status": "accepted", "output": {"posts": {"x": "bare caption string"}, "judge": _ENFORCED}},
        {"status": "accepted", "output": {"posts": {"x": {**_server_copy("x"), "caption": ""}}, "judge": _ENFORCED}},
        {"status": "accepted", "output": {"posts": {"x": {**_server_copy("x"), "caption": None}}, "judge": _ENFORCED}},
    ],
)
@pytest.mark.asyncio
async def test_create_posts_refuses_an_unusable_script_and_writes_nothing(svc, script):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await svc.insert_script({"run_id": row["id"], **script})
    # Even a worker caption cannot stand in for the missing server copy.
    with pytest.raises(mrs.MarketingScriptNotReady):
        await svc.create_posts(row["id"], [{"platform": "x", "format": "text", "caption": "hi"}], claim=_holder(svc, row["id"]))
    assert svc.fake.tables[mrs.POSTS].rows == []


@pytest.mark.asyncio
async def test_create_posts_on_an_unknown_run_is_not_found(svc):
    with pytest.raises(MarketingRunNotFound):
        await svc.create_posts(str(uuid.uuid4()), [{"platform": "x", "format": "text"}], claim=_holder(svc, str(uuid.uuid4())))
    assert svc.fake.tables[mrs.POSTS].rows == []


@pytest.mark.asyncio
async def test_mark_post_rejects_unknown_columns_and_statuses(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    await _accept_script(svc, row["id"], ["x"])
    (post,) = await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, row["id"]))
    with pytest.raises(ValueError, match="not writable"):
        await svc.mark_post(post["id"], "published", run_id="other")
    with pytest.raises(ValueError, match="unknown post status"):
        await svc.mark_post(post["id"], "teleported")
    ok = await svc.mark_post(post["id"], "published", cost_micros=15000, external_id="tw-1")
    assert ok["cost_micros"] == 15000


@pytest.mark.asyncio
async def test_claim_post_is_atomic_on_status(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    await _accept_script(svc, row["id"], ["x"])
    (post,) = await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, row["id"]))
    assert await svc.claim_post(post["id"]) is None  # pending_review is not claimable
    await svc.mark_post(post["id"], "approved")
    first = await svc.claim_post(post["id"])
    assert first["status"] == "queued" and first["claimed_at"]
    assert await svc.claim_post(post["id"]) is None  # second tick loses
    assert [p["id"] for p in await svc.list_posts("queued")] == [post["id"]]


# ── scripts (migration 173) — the ledger primitives script_service builds on ────


@pytest.mark.asyncio
async def test_insert_script_is_first_write_wins(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    mine, ours = await svc.insert_script({"run_id": row["id"], "status": "selected",
                                          "source_ref": "journey:a"})
    assert ours and mine["generations"] == 0 and "id" not in mine  # PK is run_id
    theirs, ours2 = await svc.insert_script({"run_id": row["id"], "status": "rest_day"})
    assert not ours2 and theirs["status"] == "selected" and theirs["source_ref"] == "journey:a"
    assert len(svc.fake.tables[mrs.SCRIPTS].rows) == 1


@pytest.mark.asyncio
async def test_update_script_where_is_a_cas_on_the_observed_columns_null_included(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    rid = row["id"]
    await svc.insert_script({"run_id": rid, "status": "selected"})
    lease = {"status": "generating", "generation_id": "g1", "lease_until": NOW.isoformat()}
    won = await svc.update_script_where(rid, lease, expect={"status": "selected", "generation_id": None})
    assert won["generation_id"] == "g1" and won["updated_at"]
    # A second writer that observed the same NULL generation_id loses (IS NULL, not `= 'None'`).
    assert await svc.update_script_where(rid, {**lease, "generation_id": "g2"},
                                         expect={"generation_id": None}) is None
    # A fenced write carrying a generation id that does not hold the lease is a no-op.
    assert await svc.update_script_where(rid, {"status": "rejected"}, expect={"generation_id": "g2"}) is None
    (stored,) = svc.fake.tables[mrs.SCRIPTS].rows
    assert (stored["status"], stored["generation_id"]) == ("generating", "g1")
    # The holder's own fenced terminal write lands.
    done = await svc.update_script_where(rid, {"status": "accepted", "lease_until": None},
                                         expect={"generation_id": "g1", "status": "generating"})
    assert done["status"] == "accepted" and done["lease_until"] is None


@pytest.mark.asyncio
async def test_recent_source_refs_are_newest_first_strictly_before_the_day_and_skip_rest_days(svc):
    # Read from marketing_scripts (run_date is written in the selecting INSERT). Scrambled
    # insertion order, a rest day (NULL source_ref) as the newest past run, the day itself and
    # a later day present: none of those may leak into "recent". A run whose MIRROR says
    # "mirror-only" but that has no script row is not a pick either — the mirror is never read.
    days = [("2026-09-11", "b"), ("2026-09-15", "future"), ("2026-09-10", "a"),
            ("2026-09-14", "today"), ("2026-09-13", None), ("2026-09-12", "c")]
    for d, ref in days:
        run, _ = await svc.claim_run(date.fromisoformat(d), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
        await svc.insert_script({"run_id": run["id"], "run_date": d,
                                 "status": "selected" if ref else "rest_day", "source_ref": ref})
    ghost, _ = await svc.claim_run(date(2026, 9, 9), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    await svc.update_run(ghost["id"], source_ref="mirror-only")
    before = date(2026, 9, 14)
    assert await svc.recent_source_refs(before, 2) == ["c", "b"]
    assert await svc.recent_source_refs(before, 5) == ["c", "b", "a"]
    assert await svc.recent_source_refs(before, 1) == ["c"]
    assert await svc.recent_source_refs(before, 0) == []
    assert await svc.recent_source_refs(date(2026, 9, 10), 3) == []  # nothing strictly earlier


def test_schema_constants_match_the_migration_check_constraints():
    """The CHECK lists in 170 and the tuples in schemas/marketing.py must agree, or a
    valid-looking request 23514s with a message that names nothing.

    The bucket's MIME allow-list is compared against the LATEST migration that sets it (170's
    INSERT, then 173's UPDATE): an older setter is superseded, not wrong, and asserting against
    170 forever would pin the text/html the worker must no longer be able to upload."""
    import re
    from pathlib import Path

    migrations = Path(__file__).resolve().parents[1] / "database" / "migrations"

    def strip_comments(text: str) -> str:
        text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        return "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())

    sql = strip_comments((migrations / "170_marketing_engine.sql").read_text())

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

    # The bucket's mime allow-list: every `INSERT INTO` / `UPDATE storage.buckets` statement
    # that names the marketing-media bucket and sets allowed_mime_types, in migration order.
    # A setter that is not an ARRAY literal (e.g. `= NULL`, which allows EVERY type) fails
    # here rather than being skipped.
    setters = []
    for path in sorted(migrations.glob("[0-9][0-9][0-9]_*.sql")):
        for stmt in strip_comments(path.read_text()).split(";"):
            if not re.match(r"\s*(?:INSERT\s+INTO|UPDATE)\s+storage\.buckets\b", stmt, re.I):
                continue
            if "'marketing-media'" not in stmt or not re.search(r"\ballowed_mime_types\b", stmt, re.I):
                continue
            arr = re.search(r"ARRAY\s*\[([^\]]*)\]", stmt, re.I)
            assert arr, (
                f"{path.name} sets the marketing-media allowed_mime_types to something that is "
                f"not an ARRAY literal (NULL = every type allowed): {stmt.strip()[:200]}"
            )
            setters.append((path.name, set(re.findall(r"'([^']*)'", arr.group(1)))))
    names = [n for n, _ in setters]
    assert names[:1] == ["170_marketing_engine.sql"] and any(n.startswith("173_") for n in names), (
        f"the bucket-setter scan no longer sees 170's INSERT and 173's UPDATE: {names}"
    )
    latest_name, latest = setters[-1]
    expected = set(schemas.ASSET_EXTENSIONS.values())
    assert latest == expected, (
        f"{latest_name} (the latest setter) allows {sorted(latest)}; ASSET_EXTENSIONS serves "
        f"{sorted(expected)}. Keep them equal: an extra type is an upload nothing registers, a "
        "missing one is a 4xx on the worker's signed PUT."
    )
    assert not any(t.startswith("text/") for t in latest), (
        f"{latest_name}: a PUBLIC brand bucket must not accept text/* — a compromised worker "
        "could host a phishing page under the brand (migration 173 §F)"
    )


def _migration_sql(prefix: str) -> "tuple":
    """(path, SQL with comments stripped) of the ONE migration numbered `prefix`."""
    import re
    from pathlib import Path

    (path,) = sorted(
        (Path(__file__).resolve().parents[1] / "database" / "migrations").glob(f"{prefix}_*.sql")
    )
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    return path, "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())


def test_migration_176_adds_the_caps_and_run_date_the_script_service_writes():
    """173 was APPLIED before the hardening review added three columns, and its CREATE TABLE IF
    NOT EXISTS is skipped on an existing table — so the delta lives in 176, and 176 must carry
    it in a form that works on the LIVE table (ALTER … ADD COLUMN IF NOT EXISTS), not only on a
    fresh database:

    * `reject_reason` CHECK == the reasons the service writes and the worker maps, NULL allowed;
    * `run_date` ends NOT NULL (selection's `recent` reads it) and is backfilled from the run
      BEFORE the constraint, so a pre-existing row cannot fail the migration;
    * `content_rejections` NOT NULL DEFAULT 0 (the content cap counts from it);
    * the recent-picks index on run_date exists."""
    import re

    path, sql = _migration_sql("176")

    def add_column(name: str) -> str:
        m = re.search(rf"ALTER\s+TABLE\s+public\.marketing_scripts\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+"
                      rf"{name}\b(.*?);", sql, re.S | re.I)
        assert m, f"{path.name} does not ADD COLUMN IF NOT EXISTS {name} on public.marketing_scripts"
        return m.group(1)

    rr = add_column("reject_reason")
    assert re.match(r"\s*TEXT\b", rr, re.I), rr
    cm = re.search(r"CHECK\s*\((.*)\)", rr, re.S | re.I)
    assert cm, "no reject_reason CHECK"
    assert set(re.findall(r"'([a-z_]+)'", cm.group(1))) == set(schemas.SCRIPT_REJECT_REASONS)
    assert re.search(r"reject_reason\s+IS\s+NULL", cm.group(1), re.I), "reject_reason must allow NULL"

    assert re.search(r"\bINTEGER\s+NOT\s+NULL\s+DEFAULT\s+0", add_column("content_rejections"), re.I)

    assert re.match(r"\s*DATE\s*$", add_column("run_date"), re.I), "run_date is added nullable, then backfilled"
    backfill = re.search(r"UPDATE\s+public\.marketing_scripts\b.*?SET\s+run_date\s*=.*?"
                         r"FROM\s+public\.marketing_runs\b.*?run_date\s+IS\s+NULL\s*;", sql, re.S | re.I)
    not_null = re.search(r"ALTER\s+TABLE\s+public\.marketing_scripts\s+ALTER\s+COLUMN\s+run_date\s+"
                         r"SET\s+NOT\s+NULL\s*;", sql, re.I)
    assert backfill and not_null and backfill.start() < not_null.start(), (
        "run_date must be backfilled from marketing_runs BEFORE SET NOT NULL")

    assert re.search(r"CREATE\s+INDEX\s+IF\s+NOT\s+EXISTS\s+\w+\s+ON\s+public\.marketing_scripts\s*"
                     r"\(\s*run_date\s*\)", sql, re.I), "the recent-picks read needs its index"
    assert re.search(r"^\s*BEGIN\s*;", sql, re.M) and re.search(r"^\s*COMMIT\s*;", sql, re.M)


def test_migration_173_matches_the_script_statuses_and_the_link_contract():
    """173 against the code that will write it:

    * `marketing_scripts.status` CHECK == `schemas.SCRIPT_STATUSES` (a status the service
      writes that the CHECK lacks is a 23514 on the kick path; one the schema lacks is a row
      the service cannot read back), and `run_id` is the PK (first-write-wins selection);
    * `marketing_link_hits.campaign` CHECK admits every campaign the smart link can emit
      (a platform, or the `other` fallback) and nothing hostile — the value comes from a
      public URL, and a rejected key would fail its flush forever;
    * the increment RPC is INVOKER with a pinned search_path and stays off the public RPC
      surface (INVOKER is why `test_security_definer_grants` does not cover it)."""
    import re
    from pathlib import Path

    (path,) = sorted(
        (Path(__file__).resolve().parents[1] / "database" / "migrations").glob("173_*.sql")
    )
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    sql = "\n".join(re.sub(r"--.*$", "", line) for line in text.splitlines())

    def table_body(name: str) -> str:
        # Bound to ONE CREATE TABLE statement so a CHECK in the other table cannot vouch.
        m = re.search(
            rf"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+public\.{name}\s*\((.*?)\n\)\s*;", sql, re.S | re.I
        )
        assert m, f"{path.name} no longer creates public.{name}"
        return m.group(1)

    scripts = table_body("marketing_scripts")
    m = re.search(r"\bstatus\s+TEXT[^,]*?CHECK\s*\(\s*status\s+IN\s*\(([^)]*)\)\s*\)", scripts, re.S | re.I)
    assert m, "no status CHECK on marketing_scripts"
    assert set(re.findall(r"'([a-z_]+)'", m.group(1))) == set(schemas.SCRIPT_STATUSES)
    default = re.search(r"\bstatus\s+TEXT[^,]*?DEFAULT\s+'([a-z_]+)'", scripts, re.S | re.I)
    assert default and default.group(1) in schemas.SCRIPT_STATUSES, "status DEFAULT outside the CHECK"
    assert re.search(
        r"\brun_id\s+UUID\s+PRIMARY\s+KEY\s+REFERENCES\s+public\.marketing_runs\s*\(\s*id\s*\)"
        r"\s+ON\s+DELETE\s+RESTRICT", scripts, re.I,
    ), "run_id must be the PRIMARY KEY (the selection claim) and RESTRICT like 170's children"
    # 169's lesson, BEFORE the snapshot can see these tables: a service_role policy without a
    # service_role GRANT admits nobody, and RLS must be on.
    for table in ("marketing_scripts", "marketing_link_hits"):
        assert re.search(rf"GRANT\s+ALL\s+ON\s+(?:TABLE\s+)?public\.{table}\s+TO\s+service_role\s*;",
                         sql, re.I), f"{table}: no service_role GRANT"
        assert re.search(rf"ALTER\s+TABLE\s+public\.{table}\s+ENABLE\s+ROW\s+LEVEL\s+SECURITY",
                         sql, re.I), f"{table}: RLS not enabled"

    hits = table_body("marketing_link_hits")
    cm = re.search(r"CHECK\s*\(\s*campaign\s*~\s*'([^']*)'\s*\)", hits, re.I)
    assert cm, "no campaign CHECK on marketing_link_hits"
    pattern = cm.group(1)
    assert pattern.startswith("^") and pattern.endswith("$"), f"campaign CHECK is unanchored: {pattern}"
    # fullmatch on the unanchored body: Python's `$` admits a trailing newline, Postgres' does not.
    campaign = re.compile(pattern[1:-1])
    for ok in (*schemas.POST_PLATFORMS, "other", "a" * 40):
        assert campaign.fullmatch(ok), f"the campaign CHECK rejects {ok!r}, which the smart link emits"
    for bad in ("", "a" * 41, "TikTok", "x/y", "../x", "x\n", "tik tok", "tiktok%2f", "été"):
        assert not campaign.fullmatch(bad), f"the campaign CHECK admits hostile {bad!r}"

    fn = re.search(
        r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+public\.increment_marketing_link_hits\s*\(.*?\)"
        r"\s*RETURNS\s+BIGINT(.*?)\$\$", sql, re.S | re.I,
    )
    assert fn, f"{path.name} no longer defines increment_marketing_link_hits(...) RETURNS BIGINT"
    assert re.search(r"SECURITY\s+INVOKER", fn.group(1), re.I), "the RPC must stay INVOKER"
    assert not re.search(r"SECURITY\s+DEFINER", fn.group(1), re.I)
    assert re.search(r"SET\s+search_path\s*=\s*public\s*,\s*pg_temp", fn.group(1), re.I)
    rev = re.search(
        r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+public\.increment_marketing_link_hits\s*\([^)]*\)"
        r"\s+FROM\s+([^;]+);", sql, re.I,
    )
    assert rev, "the RPC is not revoked"
    assert {r.strip().lower() for r in rev.group(1).split(",")} >= {"public", "anon", "authenticated"}
    grants = re.findall(
        r"GRANT\s+[A-Z ,]+?\s+ON\s+FUNCTION\s+public\.increment_marketing_link_hits\s*\([^)]*\)"
        r"\s+TO\s+([^;]+);", sql, re.I,
    )
    assert [g.strip().lower() for g in grants] == ["service_role"], grants


# ── held runs: the window + liveness a kick needs before any writer spend ──────


def test_held_problem_matrix():
    today = date(2026, 9, 17)
    fresh = (NOW - timedelta(minutes=5)).isoformat()
    base = {"status": "in_progress", "run_date": today.isoformat(), "started_at": fresh}

    def problem(**over):
        return held_problem({**base, **over}, now=NOW, today=today, stale_seconds=2700)

    assert problem() is None
    assert problem(run_date=(today - timedelta(days=1)).isoformat()) is None  # yesterday's resume
    for bad in ({"status": "skipped"}, {"status": "failed"}, {"status": "planned"},
                {"run_date": (today - timedelta(days=2)).isoformat()},
                {"run_date": (today + timedelta(days=1)).isoformat()},
                {"run_date": "not a date"},
                {"started_at": None},
                {"started_at": (NOW - timedelta(hours=2)).isoformat()}):
        assert problem(**bad) is not None, bad
    # liveness is the LATER of started_at / updated_at, exactly decide_claim's
    assert problem(started_at=(NOW - timedelta(hours=2)).isoformat(),
                   updated_at=(NOW - timedelta(minutes=1)).isoformat()) is None
    assert claim_window_ok(today, today) and not claim_window_ok(today - timedelta(days=2), today)


# ── the attempts cap closes an abandoned day ───────────────────────────────────


@pytest.mark.asyncio
async def test_an_abandoned_run_at_the_cap_is_closed_failed_exactly_once(svc, monkeypatch, caplog):
    import logging

    monkeypatch.setattr(mrs.settings, "MARKETING_MAX_RUN_ATTEMPTS", 2)
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW - timedelta(hours=5), claim_nonce=_n())
    _, r2 = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW - timedelta(hours=4), claim_nonce=_n())
    assert r2 == CLAIMED  # attempt 2 … which is killed too: stale in_progress at the cap
    with caplog.at_level(logging.INFO, logger=mrs.logger.name):
        cur, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
        again, reason2 = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    assert reason == reason2 == ATTEMPTS_EXHAUSTED
    stored = svc.fake.tables[mrs.RUNS].rows[0]
    assert stored["status"] == "failed" and stored["finished_at"] and stored["attempts"] == 2
    assert "attempts exhausted" in stored["last_error"] and cur["status"] == "failed"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "ABANDONED" in r.getMessage()]
    assert len(warnings) == 1  # the transition, not every later tick


@pytest.mark.asyncio
async def test_a_live_run_at_the_cap_is_never_closed_under_its_worker(svc, monkeypatch):
    monkeypatch.setattr(mrs.settings, "MARKETING_MAX_RUN_ATTEMPTS", 1)
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    _, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    assert reason == IN_PROGRESS and svc.fake.tables[mrs.RUNS].rows[0]["status"] == "in_progress"


@pytest.mark.asyncio
async def test_closing_an_exhausted_run_loses_cleanly_to_a_late_workers_own_write(svc, monkeypatch):
    """The close is a CAS on the observed (status, attempts): a late worker that recorded its
    own `failed` in between keeps its last_error."""
    monkeypatch.setattr(mrs.settings, "MARKETING_MAX_RUN_ATTEMPTS", 1)
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW - timedelta(hours=5), claim_nonce=_n())
    snapshot = dict(svc.fake.tables[mrs.RUNS].rows[0])  # stale in_progress, attempts 1
    svc.fake.tables[mrs.RUNS].rows[0].update({"status": "failed", "last_error": "late worker: boom"})

    async def stale_read(_d):
        return dict(snapshot)

    monkeypatch.setattr(svc, "get_run_by_date", stale_read)
    cur, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    assert reason == ATTEMPTS_EXHAUSTED and cur["last_error"] == "late worker: boom"
    assert svc.fake.tables[mrs.RUNS].rows[0]["last_error"] == "late worker: boom"


# ── the worker writes only its own live run ────────────────────────────────────


@pytest.mark.asyncio
async def test_the_worker_writes_only_its_own_in_progress_run(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW,
                                 claim_nonce="nonce-real")
    rid = row["id"]
    upd = await svc.update_run(rid, stage="selected", worker=True, claim=_holder(svc, rid))
    assert upd["stage"] == "selected"
    for bad in ("in_progress", "planned", "published"):
        with pytest.raises(MarketingRequestInvalid):
            await svc.update_run(rid, status=bad, worker=True, claim=_holder(svc, rid))
    with pytest.raises(MarketingRequestInvalid):
        await svc.update_run(rid, stage="planned", worker=True, claim=_holder(svc, rid))  # backwards
    # claim_nonce is trusted by decide_claim AHEAD of the attempts cap: never the worker's to set
    upd = await svc.update_run(rid, metadata={"claim_nonce": "forged-nonce", "preflight": {"ok": 1}}, worker=True, claim=_holder(svc, rid))
    assert upd["metadata"]["claim_nonce"] == "nonce-real" and upd["metadata"]["preflight"] == {"ok": 1}
    _, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW,
                                    claim_nonce="forged-nonce")
    assert reason == IN_PROGRESS
    await svc.update_run(rid, status="skipped", finished=True, worker=True, claim=_holder(svc, rid))
    with pytest.raises(MarketingRunNotHeld):
        await svc.update_run(rid, status="failed", worker=True, claim=_holder(svc, rid))  # a closed day stays closed
    assert svc.fake.tables[mrs.RUNS].rows[0]["status"] == "skipped"
    # the SERVER's own writes (the selection mirror) are not fenced
    await svc.update_run(rid, source_ref="journey:x")


@pytest.mark.asyncio
async def test_the_worker_fence_is_in_the_update_not_only_in_the_read(svc, monkeypatch):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    snapshot = dict(svc.fake.tables[mrs.RUNS].rows[0])
    svc.fake.tables[mrs.RUNS].rows[0]["status"] = "skipped"  # closed between the read and the write

    async def stale(_rid):
        return dict(snapshot)

    monkeypatch.setattr(svc, "get_run", stale)
    with pytest.raises(MarketingRunNotHeld):
        await svc.update_run(row["id"], status="failed", last_error="zombie", worker=True, claim=_holder(svc, row["id"]))
    assert svc.fake.tables[mrs.RUNS].rows[0]["status"] == "skipped"


@pytest.mark.asyncio
async def test_assets_and_posts_are_refused_on_a_closed_run(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await _accept_script(svc, row["id"], ["x"])
    await svc.update_run(row["id"], status="skipped", finished=True)
    with pytest.raises(MarketingRunNotHeld):
        await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1, claim=_holder(svc, row["id"]))
    with pytest.raises(MarketingRunNotHeld):
        await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, row["id"]))
    assert svc.fake.tables[mrs.ASSETS].rows == [] and svc.fake.tables[mrs.POSTS].rows == []


# ── create_posts: the server decides the formats, and writes all or nothing ─────


async def _asset_of(svc, run_id, kind, ext, sha, *, ready=True):
    metadata = _video_metadata(svc, run_id) if kind == "video" else None
    asset, _ = await svc.register_asset(run_id, kind=kind, ext=ext, sha256=sha, size_bytes=1,
                                        metadata=metadata, claim=_holder(svc, run_id))
    if not ready:
        return asset
    svc.fake.objects.add(asset["storage_path"])
    return await svc.complete_asset(asset["id"], claim=_asset_holder(svc, asset["id"]))


@pytest.mark.parametrize("label, bad_spec, exc", [
    ("an outlet the script dropped", {"platform": "threads", "format": "text"}, mrs.MarketingScriptNotReady),
    ("a pending asset", {"platform": "tiktok", "format": "video", "asset_ids": ["PENDING"]},
     MarketingAssetMissingInStorage),
    ("a format the platform does not take", {"platform": "x", "format": "video", "asset_ids": ["VIDEO"]},
     MarketingRequestInvalid),
    ("text on a video-only outlet", {"platform": "tiktok", "format": "text"}, MarketingRequestInvalid),
    ("a media-less video", {"platform": "tiktok", "format": "video"}, MarketingRequestInvalid),
    ("the manifest as video media", {"platform": "tiktok", "format": "video", "asset_ids": ["MANIFEST"]},
     MarketingRequestInvalid),
    ("one pair twice with different media", {"platform": "x", "format": "text", "asset_ids": ["CARD"]},
     MarketingRequestInvalid),
])
@pytest.mark.asyncio
async def test_create_posts_validates_every_spec_before_writing_any(svc, label, bad_spec, exc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    rid = row["id"]
    await _accept_script(svc, rid, ["x", "bluesky", "tiktok"])
    ids = {
        "VIDEO": (await _asset_of(svc, rid, "video", "mp4", "1" * 64))["id"],
        "MANIFEST": (await _asset_of(svc, rid, "manifest", "json", "2" * 64))["id"],
        "CARD": (await _asset_of(svc, rid, "card", "png", "3" * 64))["id"],
        "PENDING": (await _asset_of(svc, rid, "video", "mp4", "4" * 64, ready=False))["id"],
    }
    bad = {**bad_spec, "asset_ids": [ids[a] for a in bad_spec.get("asset_ids", [])]}
    good = [{"platform": "x", "format": "text"}, {"platform": "bluesky", "format": "text"}]
    with pytest.raises(exc):
        await svc.create_posts(rid, [*good, bad], claim=_holder(svc, rid))  # the invalid spec is LAST
    assert svc.fake.tables[mrs.POSTS].rows == [], label


@pytest.mark.asyncio
async def test_only_a_media_less_text_post_is_born_approved(svc, monkeypatch):
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", True)
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    rid = row["id"]
    await _accept_script(svc, rid, ["facebook", "x", "instagram"])
    video = await _asset_of(svc, rid, "video", "mp4", "1" * 64)
    card = await _asset_of(svc, rid, "card", "png", "3" * 64)
    posts = await svc.create_posts(rid, [
        {"platform": "facebook", "format": "text"},
        {"platform": "instagram", "format": "video", "asset_ids": [video["id"]]},
        {"platform": "x", "format": "text", "asset_ids": [card["id"]]},
    ], claim=_holder(svc, rid))
    assert [p["status"] for p in posts] == ["approved", "pending_review", "pending_review"]
    # one caption per platform, at most as many posts as the server's format map allows
    assert {p["caption"] for p in posts if p["platform"] == "facebook"} == {"server copy for facebook"}


@pytest.mark.parametrize("spec", [
    {"platform": "facebook", "format": "video"}, {"platform": "linkedin", "format": "video"},
    {"platform": "instagram", "format": "carousel"},
])
@pytest.mark.asyncio
async def test_a_format_whose_caption_disclaimer_does_not_fit_is_refused(svc, spec):
    """The caption disclaimer is per PLATFORM; a worker (the least-trusted process) asking for a
    narrated video on Facebook/LinkedIn or an image carousel on Instagram is refused by the
    SERVER, whatever render.POST_FORMAT says (review 2026-09-29)."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    rid = row["id"]
    await _accept_script(svc, rid, [spec["platform"]])
    kind, ext = ("video", "mp4") if spec["format"] == "video" else ("carousel", "png")
    media = await _asset_of(svc, rid, kind, ext, "5" * 64)
    with pytest.raises(MarketingRequestInvalid):
        await svc.create_posts(rid, [{**spec, "asset_ids": [media["id"]]}], claim=_holder(svc, rid))
    assert svc.fake.tables[mrs.POSTS].rows == []


def test_every_recordable_format_has_a_caption_disclaimer_that_fits_it():
    """A video post's caption must say narration was generated with AI; no other post may claim
    it. The disclaimer is composed per platform (post_copy.disclaimer_for), so this pins the
    server's format map to it."""
    from datetime import date as _date

    from app.services.marketing import post_copy

    field = {"youtube": "youtube_description"}
    for platform, formats in schemas.POST_FORMATS_BY_PLATFORM.items():
        text = post_copy.disclaimer_for(field.get(platform, platform), _date(2026, 9, 17)) or ""
        says_narration = "narration" in text
        for fmt in formats:
            assert says_narration == (fmt == "video"), (platform, fmt, text)


def test_the_format_map_covers_exactly_the_outlets_the_writer_composes():
    from app.services.marketing import post_copy

    assert set(schemas.POST_FORMATS_BY_PLATFORM) == set(post_copy.PLATFORMS)
    for platform, formats in schemas.POST_FORMATS_BY_PLATFORM.items():
        assert platform in schemas.POST_PLATFORMS and set(formats) <= set(schemas.POST_FORMATS)
    assert set(schemas.POST_MEDIA_KINDS) == set(schemas.POST_FORMATS)
    for kinds in schemas.POST_MEDIA_KINDS.values():
        assert set(kinds) <= set(schemas.ASSET_KINDS) and not {"manifest", "audio", "script"} & set(kinds)
    assert set(schemas.ASSET_KIND_EXTENSIONS) == set(schemas.ASSET_KINDS)
    for exts in schemas.ASSET_KIND_EXTENSIONS.values():
        assert set(exts) <= set(schemas.ASSET_EXTENSIONS)
    assert set(schemas.WORKER_RUN_STATUSES) <= set(schemas.RUN_STATUSES)
    assert not {"in_progress", "planned", "published"} & set(schemas.WORKER_RUN_STATUSES)


# ── a worker PATCH is idempotent: a retried terminal write replays, never 409s ──
# The worker retries every call after a transport error or a 502/503/504. A terminal PATCH whose
# first attempt COMMITTED but whose response was lost used to come back 409 MARKETING_RUN_NOT_HELD
# on the retry (the run was no longer in_progress), and the worker logged "could not record
# deferral/failure" for a write that had landed.


@pytest.mark.parametrize("terminal", ["failed", "skipped", "media_ready"])
@pytest.mark.asyncio
async def test_a_retried_terminal_patch_replays_and_writes_nothing(svc, terminal):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    rid = row["id"]
    first = await svc.update_run(rid, status=terminal, finished=True, last_error="deferred: slow",
                                 metadata={"skip_reason": "rest_day"}, timings={"a_s": 1.0}, worker=True, claim=_holder(svc, rid))
    assert first["status"] == terminal
    before = dict(svc.fake.tables[mrs.RUNS].rows[0])
    # The same request again (the retry), and one that carries different fields: both are replays
    # of an effect that is already there, and neither may merge anything into the closed run.
    for extra in ({"last_error": "deferred: slow", "metadata": {"skip_reason": "rest_day"}},
                  {"last_error": "a different error", "metadata": {"other": 1}, "timings": {"b_s": 2.0}}):
        again = await svc.update_run(rid, status=terminal, finished=True, worker=True, **extra, claim=_holder(svc, rid))
        assert again["status"] == terminal and again["id"] == rid
        assert svc.fake.tables[mrs.RUNS].rows[0] == before  # not even updated_at moved


@pytest.mark.asyncio
async def test_a_replay_is_only_the_same_terminal_state(svc):
    """The fence still holds for everything that is NOT the effect already present."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    rid = row["id"]
    await svc.update_run(rid, stage="selected", worker=True, claim=_holder(svc, rid))
    await svc.update_run(rid, status="skipped", finished=True, worker=True, claim=_holder(svc, rid))
    before = dict(svc.fake.tables[mrs.RUNS].rows[0])
    for kw in ({"status": "failed"},                        # skipped → failed
               {"status": "media_ready"},                   # skipped → media_ready
               {"stage": "scripted"},                       # a bare checkpoint on a closed run
               {"stage": "selected"},                       # …even one naming the stage it holds
               {"status": "skipped", "stage": "scripted"}):  # the status matches, the stage does not
        with pytest.raises(MarketingRunNotHeld):
            await svc.update_run(rid, worker=True, **kw, claim=_holder(svc, rid))
    with pytest.raises(MarketingRequestInvalid):
        await svc.update_run(rid, status="in_progress", worker=True, claim=_holder(svc, rid))  # a reopen is never a replay
    assert svc.fake.tables[mrs.RUNS].rows[0] == before


@pytest.mark.asyncio
async def test_a_retry_whose_read_raced_its_own_first_attempt_replays(svc, monkeypatch):
    """The retry READ the run while attempt 1 was still in flight (in_progress); attempt 1 then
    committed `failed`, so the retry's fenced UPDATE matches nothing. That is the same replay."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    rid = row["id"]
    snapshot = dict(svc.fake.tables[mrs.RUNS].rows[0])
    await svc.update_run(rid, status="failed", finished=True, last_error="boom", worker=True, claim=_holder(svc, rid))
    real_get = svc.get_run
    reads = []

    async def first_read_is_stale(run_id):
        reads.append(run_id)
        return dict(snapshot) if len(reads) == 1 else await real_get(run_id)

    monkeypatch.setattr(svc, "get_run", first_read_is_stale)
    again = await svc.update_run(rid, status="failed", finished=True, last_error="boom", worker=True, claim=_holder(svc, rid))
    assert again["status"] == "failed" and len(reads) == 2
    # …and a different terminal status in the same race is still refused
    reads.clear()
    with pytest.raises(MarketingRunNotHeld):
        await svc.update_run(rid, status="skipped", worker=True, claim=_holder(svc, rid))


@pytest.mark.asyncio
async def test_a_retried_checkpoint_whose_read_raced_its_first_attempt_lands(svc, monkeypatch):
    """The retry read stage=planned; its own first attempt then committed stage=selected. A fence
    on the observed stage ONLY missed and 409'd a live run (the worker exits, the day waits out
    the stale window). The fence is the observed stage OR the requested one — never "any stage
    ahead": a newer holder that moved further on is not written over."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    rid = row["id"]
    snapshot = dict(svc.fake.tables[mrs.RUNS].rows[0])  # stage planned
    svc.fake.tables[mrs.RUNS].rows[0]["stage"] = "selected"  # attempt 1 committed after the read

    async def stale(_rid):
        return dict(snapshot)

    monkeypatch.setattr(svc, "get_run", stale)
    upd = await svc.update_run(rid, stage="selected", timings={"selected_s": 1.5}, worker=True, claim=_holder(svc, rid))
    assert upd["stage"] == "selected" and upd["timings"] == {"selected_s": 1.5}
    # A newer holder already at `scripted`: the same stale request must not drag it back.
    svc.fake.tables[mrs.RUNS].rows[0]["stage"] = "scripted"
    with pytest.raises(MarketingRunNotHeld):
        await svc.update_run(rid, stage="selected", worker=True, claim=_holder(svc, rid))
    assert svc.fake.tables[mrs.RUNS].rows[0]["stage"] == "scripted"


@pytest.mark.asyncio
async def test_a_reclaim_between_the_workers_read_and_write_is_not_written_over(svc, monkeypatch):
    """The stage `in_` accepts the stage we ask for; a newer holder that re-claimed the run (still
    in_progress) and reached that stage must not have its row merged over by our stale read."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    rid = row["id"]
    snapshot = dict(svc.fake.tables[mrs.RUNS].rows[0])  # attempts 1, stage planned
    svc.fake.tables[mrs.RUNS].rows[0].update(
        {"attempts": 2, "stage": "selected", "timings": {"newer_s": 9.0}, "worker_version": "newer"})

    async def stale(_rid):
        return dict(snapshot)

    monkeypatch.setattr(svc, "get_run", stale)
    with pytest.raises(MarketingRunNotHeld):
        await svc.update_run(rid, stage="selected", timings={"selected_s": 1.0}, worker=True, claim=_holder(svc, rid))
    assert svc.fake.tables[mrs.RUNS].rows[0]["timings"] == {"newer_s": 9.0}


@pytest.mark.asyncio
@pytest.mark.parametrize("reclaimed_attempts", [2, 1], ids=["new-attempts", "same-attempts-new-nonce"])
async def test_the_update_itself_is_fenced_on_the_callers_claim(svc, monkeypatch, reclaimed_attempts):
    """The WRITE-side fence (review 2026-09-26: deleting it left every test green). The zombie's
    claim is fixed BEFORE the re-claim and its read is stale, so the read-side `claim_problem`
    passes; only the conditional UPDATE's `attempts` + `metadata->>claim_nonce` filters stop it.
    The same-attempts case pins the NONCE half (attempts alone collide after a manual reset)."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW,
                                 claim_nonce=_n())
    rid = row["id"]
    zombie = _holder(svc, rid)                            # (1, A): taken before the re-claim
    snapshot = dict(svc.fake.tables[mrs.RUNS].rows[0])     # what the zombie read
    stored = svc.fake.tables[mrs.RUNS].rows[0]
    stored.update({"attempts": reclaimed_attempts, "stage": "selected",
                   "timings": {"newer_s": 9.0}, "worker_version": "newer",
                   "metadata": {**(stored.get("metadata") or {}), "claim_nonce": _n()}})
    before = copy.deepcopy(stored)

    async def stale(_rid):
        return dict(snapshot)

    monkeypatch.setattr(svc, "get_run", stale)
    assert mrs.claim_problem(snapshot, zombie) is None     # the read-side check is satisfied
    with pytest.raises(MarketingRunNotHeld):
        await svc.update_run(rid, stage="selected", timings={"zombie_s": 1.0}, worker=True,
                             claim=zombie)
    assert stored == before


# ── runs abandoned OUTSIDE the claim window are closed by the next claim of any date ──
# `_close_exhausted` runs only from a claim of the run's own date, and the window stops admitting
# that date after yesterday ET: a run killed on its last claimable tick read `in_progress` forever.

_TODAY = date(2026, 9, 17)  # run_date_et(NOW)
assert run_date_et(NOW) == _TODAY


def _seed_run(svc, run_date: date, status: str, *, touched: Optional[datetime], **extra) -> Dict[str, Any]:
    stamp = touched.isoformat() if touched else None
    row = {"id": str(uuid.uuid4()), "run_date": run_date.isoformat(), "status": status,
           "stage": "selected", "attempts": 2, "timings": {}, "metadata": {},
           "started_at": stamp, "updated_at": stamp, **extra}
    svc.fake.tables[mrs.RUNS].rows.append(row)
    return row


def _stored(svc, run_id):
    return next(r for r in svc.fake.tables[mrs.RUNS].rows if r["id"] == run_id)


@pytest.mark.asyncio
async def test_a_run_abandoned_outside_the_window_is_closed_once_by_the_next_claim(svc, caplog):
    import logging

    long_ago = NOW - timedelta(hours=26)
    dead = _seed_run(svc, _TODAY - timedelta(days=2), "in_progress", touched=long_ago)  # the D+1 resume, killed
    never = _seed_run(svc, _TODAY - timedelta(days=3), "planned", touched=None)
    resumable = _seed_run(svc, _TODAY - timedelta(days=1), "in_progress", touched=long_ago)
    alive = _seed_run(svc, _TODAY - timedelta(days=4), "in_progress", touched=NOW - timedelta(minutes=5))
    handed_over = _seed_run(svc, _TODAY - timedelta(days=5), "media_ready", touched=long_ago)
    closed = _seed_run(svc, _TODAY - timedelta(days=6), "failed", touched=long_ago, last_error="boom")
    with caplog.at_level(logging.INFO, logger=mrs.logger.name):
        today_row, reason = await svc.claim_run(_TODAY, worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
        await svc.claim_run(_TODAY - timedelta(days=1), worker_version="t", dry_run=True, now=NOW,
                            resume_only=True, claim_nonce=_n())
    assert reason == CLAIMED and today_row["run_date"] == _TODAY.isoformat()
    for row in (dead, never):
        stored = _stored(svc, row["id"])
        assert stored["status"] == "failed" and stored["finished_at"], row["run_date"]
        assert stored["attempts"] == 2 and "abandoned outside the claim window" in stored["last_error"]
    assert _stored(svc, resumable["id"])["status"] == "in_progress"  # yesterday is still resumable…
    assert _stored(svc, alive["id"])["status"] == "in_progress"      # …and a touched run is not abandoned
    assert _stored(svc, handed_over["id"])["status"] == "media_ready"  # the publisher's
    assert _stored(svc, closed["id"])["last_error"] == "boom"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING
                and "ABANDONED outside the claim window" in r.getMessage()]
    assert len(warnings) == 2  # the transitions only — the second claim found nothing to close


@pytest.mark.parametrize("label, late_write", [
    ("a worker checkpoint", lambda r: r.update(stage="scripted", updated_at=NOW.isoformat())),
    # attempts only: each conjunct of the CAS is pinned by a write that only it can see
    ("a re-claim", lambda r: r.update(attempts=r["attempts"] + 1)),
    ("a late worker's own failed", lambda r: r.update(status="failed", last_error="late worker")),
])
@pytest.mark.asyncio
async def test_the_sweep_close_is_fenced_on_the_row_it_judged(svc, monkeypatch, label, late_write):
    """A write landing between the sweep's read and its close wins: the close is a CAS on the
    observed (status, attempts, updated_at), never on status alone."""
    dead = _seed_run(svc, _TODAY - timedelta(days=2), "in_progress", touched=NOW - timedelta(hours=26))
    real_exec = mrs._exec

    async def late(query, *, op, **ids):
        res = await real_exec(query, op=op, **ids)
        if op == "sweep_abandoned.select":
            late_write(_stored(svc, dead["id"]))  # lands after the read, before the close
        return res

    monkeypatch.setattr(mrs, "_exec", late)
    await svc.claim_run(_TODAY, worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    stored = _stored(svc, dead["id"])
    assert "abandoned outside" not in str(stored.get("last_error")), label
    assert not stored.get("finished_at"), label


@pytest.mark.asyncio
async def test_a_sweep_failure_never_fails_the_claim(svc, monkeypatch, caplog):
    import logging

    _seed_run(svc, _TODAY - timedelta(days=2), "in_progress", touched=NOW - timedelta(hours=26))
    real_exec = mrs._exec

    async def flaky(query, *, op, **ids):
        if op.startswith("sweep_abandoned"):
            raise mrs.MarketingRunError(f"{op} failed: APIError: 520")
        return await real_exec(query, op=op, **ids)

    monkeypatch.setattr(mrs, "_exec", flaky)
    with caplog.at_level(logging.WARNING, logger=mrs.logger.name):
        row, reason = await svc.claim_run(_TODAY, worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    assert reason == CLAIMED and row["status"] == "in_progress"
    assert any("sweep could not read" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_one_sweep_closes_at_most_the_limit_and_the_oldest_first(svc):
    """W3-SWW-2 (b): the sweep runs INSIDE the claim request on the single uvicorn worker, so it
    is bounded (`_SWEEP_LIMIT`) and oldest-first — the rest wait for the next hourly claim. The
    rows are seeded OUT of date order: the fake returns insertion order, so date-ordered seeding
    would let a dropped `.order("run_date")` pass."""
    import random

    extra = 5
    days_back = list(range(2, 2 + mrs._SWEEP_LIMIT + extra))  # never yesterday (still resumable)
    random.Random(17).shuffle(days_back)
    long_ago = NOW - timedelta(hours=26)
    seeded = {_seed_run(svc, _TODAY - timedelta(days=d), "in_progress", touched=long_ago)["id"]: d
              for d in days_back}
    assert list(seeded.values()) != sorted(seeded.values(), reverse=True), "sentinel: shuffled"
    _row, reason = await svc.claim_run(_TODAY, worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    assert reason == CLAIMED
    closed = sorted(d for rid, d in seeded.items() if _stored(svc, rid)["status"] == "failed")
    oldest = sorted(days_back)[-mrs._SWEEP_LIMIT:]
    assert len(closed) == mrs._SWEEP_LIMIT, closed
    assert closed == oldest, "the oldest abandoned runs go first"
    assert all(_stored(svc, rid)["status"] == "in_progress"
               for rid, d in seeded.items() if d not in oldest)


# ── Step 0 / Phase 4 (2026-09-29): judge gate, review, object verification, on-screen text ──


@pytest.mark.parametrize("judge", [None, {}, {"mode": "shadow"}, {"mode": "off"}, {"mode": "ENFORCE"},
                                   "enforce", {"mode": None}])
@pytest.mark.asyncio
async def test_create_posts_refuses_a_script_the_judge_did_not_enforce(svc, judge):
    """`shadow` accepts drafts the judge flagged and `off` never asks it: such a package may be
    voiced and rendered, but it never becomes a post — nothing is written."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    output = {"posts": {"x": _server_copy("x")}}
    if judge is not None:
        output["judge"] = judge
    await svc.insert_script({"run_id": row["id"], "status": "accepted", "output": output})
    with pytest.raises(mrs.MarketingJudgeNotEnforced):
        await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, row["id"]))
    assert svc.fake.tables[mrs.POSTS].rows == []


def test_the_judge_refusal_is_its_own_code_and_not_retried():
    from app.api.error_response import ErrorCode, classify_exception

    code, status = classify_exception(mrs.MarketingJudgeNotEnforced("x"))
    assert code == ErrorCode.MARKETING_JUDGE_NOT_ENFORCED and status == 409
    # A mismatching object is "the registered object is not there": retried by the next tick.
    assert classify_exception(mrs.MarketingAssetMismatch("size 1 != 2 timeout")) == (
        ErrorCode.MARKETING_ASSET_MISSING, 409)


async def _pending_post(svc, platform="x", *, dry_run=False, run_date=date(2026, 9, 17)):
    row, _ = await svc.claim_run(run_date, worker_version="t", dry_run=dry_run, now=NOW, claim_nonce=_n())
    await _accept_script(svc, row["id"], [platform])
    (post,) = await svc.create_posts(row["id"], [{"platform": platform, "format": "text"}], claim=_holder(svc, row["id"]))
    assert post["status"] == "pending_review"
    return post


@pytest.mark.asyncio
async def test_review_post_decides_once_and_records_who(svc):
    post = await _pending_post(svc)
    outcome, row = await svc.review_post(post["id"], "approve", reviewed_by="telegram:42")
    assert outcome == "approved" and row["status"] == "approved"
    assert row["approved_by"] == "telegram:42" and row["approved_at"]
    assert row["metadata"]["review"]["decision"] == "approved" and row["metadata"]["dry_run"] is False
    # A double tap, or a late reject, changes nothing.
    assert (await svc.review_post(post["id"], "approve", reviewed_by="telegram:42"))[0] == "already_approved"
    outcome, again = await svc.review_post(post["id"], "reject", reviewed_by="telegram:7")
    assert outcome == "already_approved" and again["status"] == "approved" and again["approved_by"] == "telegram:42"


@pytest.mark.asyncio
async def test_review_post_reject_and_the_edges(svc):
    post = await _pending_post(svc)
    outcome, row = await svc.review_post(post["id"], "reject", reviewed_by="telegram:42")
    assert outcome == "rejected" and row["status"] == "rejected" and row["approved_by"] is None
    assert row["metadata"]["review"]["by"] == "telegram:42"
    assert (await svc.review_post(post["id"], "approve", reviewed_by="x"))[0] == "already_rejected"
    assert await svc.review_post(str(uuid.uuid4()), "approve", reviewed_by="x") == ("not_found", None)
    with pytest.raises(ValueError):
        await svc.review_post(post["id"], "publish", reviewed_by="x")


@pytest.mark.asyncio
async def test_review_post_loses_a_race_without_overwriting_the_winner(svc, monkeypatch):
    """The conditional UPDATE is the authority: another decision landing between our read and our
    write must win, and we must report it rather than flip the row."""
    post = await _pending_post(svc)
    real_get = svc.get_post
    calls = {"n": 0}

    async def racing_get(post_id):
        calls["n"] += 1
        row = await real_get(post_id)
        if calls["n"] == 1:   # after OUR read, a rejection lands
            for r in svc.fake.tables[mrs.POSTS].rows:
                if r["id"] == post_id:
                    r["status"] = "rejected"
        return row

    monkeypatch.setattr(svc, "get_post", racing_get)
    outcome, row = await svc.review_post(post["id"], "approve", reviewed_by="telegram:42")
    assert outcome == "already_rejected" and row["status"] == "rejected" and row.get("approved_by") is None


@pytest.mark.asyncio
async def test_list_posts_filters_before_the_limit(svc):
    """What the publisher asks for: only the platforms it can send and only real (non-rehearsal)
    rows — applied IN the query, so older unsendable rows cannot fill the window."""
    await _pending_post(svc, "x", dry_run=True, run_date=date(2026, 9, 16))
    real = await _pending_post(svc, "x", run_date=date(2026, 9, 17))
    for r in svc.fake.tables[mrs.POSTS].rows:
        r["status"] = "approved"
    rows = await svc.list_posts("approved", limit=1, platforms=["x"], live_only=True)
    assert [r["id"] for r in rows] == [real["id"]]
    assert await svc.list_posts("approved", platforms=[]) == []
    assert await svc.list_posts("approved", platforms=["tiktok"]) == []
    assert len(await svc.list_posts("approved", platforms=["x"])) == 2
    # A row whose metadata carries no dry_run flag at all is a rehearsal to `live_only`.
    for r in svc.fake.tables[mrs.POSTS].rows:
        r["metadata"] = {}
    assert await svc.list_posts("approved", platforms=["x"], live_only=True) == []


@pytest.mark.parametrize("path", ["complete", "register"])
@pytest.mark.parametrize("stored", [{"size": 2, "mimetype": "video/mp4"},
                                    {"size": 1, "mimetype": "text/html"},
                                    {"size": 1, "mimetype": "application/json"}])
@pytest.mark.asyncio
async def test_a_mismatching_object_is_deleted_and_failed_on_both_paths_to_ready(svc, path, stored):
    """Size or content type not what was registered → the object is removed (its immutable key is
    free again), the row is failed, and nothing becomes `ready` — on `complete_asset` AND on
    `register_asset`'s already-in-bucket branch (formerly a second path to ready with no check)."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    await _accept_script(svc, row["id"], ["tiktok"])
    meta = _video_metadata(svc, row["id"])
    asset, _ = await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1,
                                        metadata=meta, claim=_holder(svc, row["id"]))
    svc.fake.objects.add(asset["storage_path"])
    svc.fake.object_meta[asset["storage_path"]] = stored
    with pytest.raises(mrs.MarketingAssetMismatch):
        if path == "complete":
            await svc.complete_asset(asset["id"], claim=_asset_holder(svc, asset["id"]))
        else:
            await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1,
                                     metadata=meta, claim=_holder(svc, row["id"]))
    assert asset["storage_path"] not in svc.fake.objects
    assert svc.fake.storage.removed == [asset["storage_path"]]
    (stored_row,) = [a for a in svc.fake.tables[mrs.ASSETS].rows if a["id"] == asset["id"]]
    assert stored_row["status"] == "failed"
    # The next tick re-uploads the right bytes to the SAME key and it verifies.
    svc.fake.object_meta.pop(asset["storage_path"])
    again, upload = await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1,
                                             metadata=meta, claim=_holder(svc, row["id"]))
    assert upload is not None and again["id"] == asset["id"]
    svc.fake.objects.add(asset["storage_path"])
    assert (await svc.complete_asset(asset["id"], claim=_asset_holder(svc, asset["id"])))["status"] == "ready"


@pytest.mark.parametrize("stored", [{}, {"size": None, "mimetype": "video/mp4"}, {"size": 1, "mimetype": ""},
                                    {"size": "big", "mimetype": "video/mp4"}])
@pytest.mark.asyncio
async def test_an_unknown_size_or_type_is_a_ledger_error_and_deletes_nothing(svc, stored):
    """Storage not reporting what it stored is an unknown, not a mismatch: nothing is deleted,
    nothing becomes ready, and the 503 is retried."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    asset, _ = await svc.register_asset(row["id"], kind="manifest", ext="json", sha256=SHA, size_bytes=1,
                                        claim=_holder(svc, row["id"]))
    svc.fake.objects.add(asset["storage_path"])
    svc.fake.object_meta[asset["storage_path"]] = stored
    with pytest.raises(mrs.MarketingRunError) as info:
        await svc.complete_asset(asset["id"], claim=_asset_holder(svc, asset["id"]))
    assert not isinstance(info.value, mrs.MarketingAssetMismatch)
    assert asset["storage_path"] in svc.fake.objects and svc.fake.storage.removed == []
    assert svc.fake.tables[mrs.ASSETS].rows[0]["status"] == "pending_upload"


@pytest.mark.asyncio
async def test_a_content_type_parameter_is_not_a_mismatch(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    asset, _ = await svc.register_asset(row["id"], kind="manifest", ext="json", sha256=SHA, size_bytes=7,
                                        claim=_holder(svc, row["id"]))
    svc.fake.objects.add(asset["storage_path"])
    svc.fake.object_meta[asset["storage_path"]] = {"size": "7", "mimetype": "Application/JSON; charset=utf-8"}
    assert (await svc.complete_asset(asset["id"], claim=_asset_holder(svc, asset["id"])))["status"] == "ready"


@pytest.mark.parametrize("mutate, match", [
    (lambda md: md.update(onscreen_text=md["onscreen_text"] + ["Buy now"]), "not the accepted script"),
    (lambda md: md.update(onscreen_text=[t for t in md["onscreen_text"] if t != _DISCLAIMER_CARD]),
     "disclaimer card"),
    (lambda md: md.update(onscreen_text=[]), "onscreen_text"),
    (lambda md: md.pop("onscreen_text"), "onscreen_text"),
    (lambda md: md.update(voice_asset_id=str(uuid.uuid4())), "narration"),
    (lambda md: md.pop("voice_asset_id"), "narration"),
    # a card BODY alone is fine to declare only if it is the accepted body — a paraphrase is not
    (lambda md: md.update(onscreen_text=md["onscreen_text"] + [_CARD["body"].upper()]), "not the accepted script"),
])
@pytest.mark.asyncio
async def test_a_video_declaring_text_the_script_does_not_carry_is_refused(svc, mutate, match):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    await _accept_script(svc, row["id"], ["tiktok"])
    meta = _video_metadata(svc, row["id"])
    mutate(meta)
    with pytest.raises(MarketingRequestInvalid, match=match):
        await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1,
                                 metadata=meta, claim=_holder(svc, row["id"]))
    assert not [a for a in svc.fake.tables[mrs.ASSETS].rows if a["kind"] == "video"]


@pytest.mark.asyncio
async def test_a_video_whose_narration_is_not_a_ready_checked_audio_is_refused(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    await _accept_script(svc, row["id"], ["tiktok"])
    meta = _video_metadata(svc, row["id"])
    voice = next(a for a in svc.fake.tables[mrs.ASSETS].rows if a["kind"] == "audio")
    for bad in ({"status": "pending_upload"}, {"metadata": {}}, {"kind": "video"}, {"run_id": str(uuid.uuid4())}):
        saved = dict(voice)
        voice.update(bad)
        with pytest.raises(MarketingRequestInvalid, match="narration"):
            await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1,
                                     metadata=meta, claim=_holder(svc, row["id"]))
        voice.clear()
        voice.update(saved)


@pytest.mark.asyncio
async def test_a_video_needs_an_accepted_script_to_be_checked_against(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    with pytest.raises(mrs.MarketingScriptNotReady):
        await svc.register_asset(row["id"], kind="video", ext="mp4", sha256=SHA, size_bytes=1,
                                 metadata={"onscreen_text": ["x"], "voice_asset_id": "v"}, claim=_holder(svc, row["id"]))


def test_the_request_schema_requires_and_bounds_onscreen_text():
    base = {"kind": "video", "ext": "mp4", "sha256": SHA, "bytes": 1}
    ok = schemas.AssetRegisterRequest(**base, metadata={"onscreen_text": ["a"], "voice_asset_id": "v"})
    assert ok.metadata["onscreen_text"] == ["a"]
    for metadata in ({}, {"onscreen_text": []}, {"onscreen_text": [""]}, {"onscreen_text": ["  "]},
                     {"onscreen_text": [1]}, {"onscreen_text": "a"},
                     {"onscreen_text": ["x" * (schemas.ONSCREEN_TEXT_MAX_CHARS + 1)]},
                     {"onscreen_text": ["x"] * (schemas.ONSCREEN_TEXT_MAX + 1)}):
        with pytest.raises(ValueError):
            schemas.AssetRegisterRequest(**base, metadata=metadata)
    with pytest.raises(ValueError, match="only a video"):
        schemas.AssetRegisterRequest(kind="manifest", ext="json", sha256=SHA, bytes=1,
                                     metadata={"onscreen_text": ["a"]})


@pytest.mark.asyncio
async def test_read_back_verifies_the_video_pointer_like_the_voice_pointer(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    await _accept_script(svc, row["id"], ["tiktok"])
    video = await _ready_asset(svc, row["id"])
    voice = next(a for a in svc.fake.tables[mrs.ASSETS].rows if a["kind"] == "audio")
    run_row = svc.fake.tables[mrs.RUNS].rows[0]
    run_row["metadata"] = {**run_row["metadata"], "voice_asset_id": voice["id"], "video_asset_id": video["id"]}
    back = await svc.read_back(row["id"], claim=_holder(svc, row["id"]))
    assert back["voice_asset_id"] == voice["id"] and back["video_asset_id"] == video["id"]
    assert all(a["public_url"].endswith(a["storage_path"]) for a in back["assets"])
    # A pointer at the wrong kind (the worker writes run metadata itself) verifies to None.
    run_row["metadata"]["video_asset_id"] = voice["id"]
    run_row["metadata"]["voice_asset_id"] = video["id"]
    back = await svc.read_back(row["id"], claim=_holder(svc, row["id"]))
    assert back["voice_asset_id"] is None and back["video_asset_id"] is None


# ══ Phase 5 — the publisher ledger (design §12.10) ════════════════════════════════════════════
#
# The pure helpers and the fenced, merging writes that the publisher, the review bot and the feed
# build on. Rows are seeded straight into the fake (the publisher's inputs are rows the review
# bot already moved), and every `observed` row is a DEEP COPY — what a real read hands back — so
# a test can make the stored row move on underneath it.

_P5_TODAY = date(2026, 9, 30)
#: Fixed instants well in the past of any clock the suite runs on (a write stamps the real now).
_T0 = "2026-09-01T12:00:00+00:00"
_T1 = "2026-09-01T12:05:00+00:00"
_DEFAULT_META = object()


def _p5_meta() -> Dict[str, Any]:
    """What the review bot leaves on an approved live post — the keys no publisher write may drop."""
    return {"dry_run": False, "review": {"decision": "approved", "by": "telegram:42", "at": _T0},
            "review_notified_at": _T0}


def _p5_post(svc, *, status="approved", platform="x", run_day=_P5_TODAY, fmt="text", updated_at=_T0,
             created_at=None, metadata=_DEFAULT_META, key=None, run_id=None, **extra) -> Dict[str, Any]:
    row = {
        "id": str(uuid.uuid4()), "run_id": run_id or str(uuid.uuid4()), "platform": platform,
        "format": fmt, "status": status,
        "idempotency_key": key if key is not None else f"{run_day.isoformat()}:{platform}:{fmt}",
        "caption": "Three habits that quietly compound.", "attempts": 0, "cost_micros": 0,
        "metadata": _p5_meta() if metadata is _DEFAULT_META else metadata,
        "created_at": created_at or updated_at, "updated_at": updated_at, "claimed_at": None,
        "published_at": None, "approved_at": None, "approved_by": None, "external_id": None,
        "external_url": None, "last_error": None, **extra,
    }
    svc.fake.tables[mrs.POSTS].rows.append(row)
    return row


def _live_post(svc, post_id: str) -> Dict[str, Any]:
    return next(r for r in svc.fake.tables[mrs.POSTS].rows if r["id"] == post_id)


def _read(row: Dict[str, Any]) -> Dict[str, Any]:
    """What a real read hands the caller: a copy that does not move when the stored row does."""
    return copy.deepcopy(row)


def _spy_updates(monkeypatch, svc, table=mrs.POSTS, before=None) -> List[Dict[str, Any]]:
    """Record every UPDATE payload sent to `table`. `before(n)` runs just before the n-th (0-based)
    UPDATE's filters are evaluated — a concurrent writer landing between a read and its write."""
    t = svc.fake.tables[table]
    real = t.update
    calls: List[Dict[str, Any]] = []

    def spy(payload):
        if before is not None:
            before(len(calls))
        calls.append(copy.deepcopy(payload))
        return real(payload)

    monkeypatch.setattr(t, "update", spy)
    return calls


# ── post_run_date / is_fresh / month_start_utc / charges_since (pure) ──────────


@pytest.mark.parametrize("post, expected", [
    ({"idempotency_key": "2026-09-30:x:text"}, date(2026, 9, 30)),
    ({"idempotency_key": "2026-09-29:bluesky:text"}, date(2026, 9, 29)),
    ({"idempotency_key": "2026-02-30:x:text"}, None),     # not a calendar day
    ({"idempotency_key": "2026-9-30:x:text"}, None),      # unpadded: the first 10 chars are no date
    ({"idempotency_key": "garbage"}, None),
    ({"idempotency_key": ""}, None),
    ({"idempotency_key": None}, None),
    ({}, None),
])
def test_post_run_date_reads_the_idempotency_key_and_never_guesses(post, expected):
    assert mrs.post_run_date(post) == expected


@pytest.mark.parametrize("run_day, fresh", [
    (date(2026, 9, 30), True),    # its run day
    (date(2026, 9, 29), True),    # the next day
    (date(2026, 9, 28), False),   # two days on: a backlog, never sent
    (date(2026, 10, 1), False),   # a future day is not a claimable day either
    (date(2025, 9, 30), False),
])
def test_a_post_is_fresh_on_its_run_day_and_the_next_only(run_day, fresh):
    assert mrs.is_fresh({"idempotency_key": f"{run_day.isoformat()}:x:text"}, _P5_TODAY) is fresh


@pytest.mark.parametrize("post", [{}, {"idempotency_key": None}, {"idempotency_key": "x:text"},
                                  {"idempotency_key": "2026-13-01:x:text"}])
def test_a_post_with_an_unreadable_key_is_never_fresh(post):
    assert mrs.is_fresh(post, _P5_TODAY) is False


def test_freshness_turns_over_at_midnight_new_york_not_utc():
    post = {"idempotency_key": "2026-09-29:x:text"}
    last_second = datetime(2026, 10, 1, 3, 59, 59, tzinfo=timezone.utc)    # 23:59:59 EDT on the 30th
    midnight = datetime(2026, 10, 1, 4, 0, 0, tzinfo=timezone.utc)          # 00:00:00 EDT on the 1st
    assert run_date_et(last_second) == date(2026, 9, 30) and mrs.is_fresh(post, run_date_et(last_second))
    assert run_date_et(midnight) == date(2026, 10, 1) and not mrs.is_fresh(post, run_date_et(midnight))
    # 00:00 UTC is still the previous evening in New York: no early expiry on the UTC rollover.
    assert mrs.is_fresh(post, run_date_et(datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)))


def test_month_start_utc_is_the_utc_month_whatever_the_input_zone():
    utc = timezone.utc
    naive = mrs.month_start_utc(datetime(2026, 9, 15, 12, 34, 56, 789))     # naive = UTC
    assert naive == datetime(2026, 9, 1, tzinfo=utc) and naive.tzinfo == utc
    # 21:00 EDT on Sep 30 is 01:00 UTC on Oct 1: the cap's month is already October ...
    assert mrs.month_start_utc(datetime(2026, 9, 30, 21, 0, tzinfo=mrs.ET)) == datetime(2026, 10, 1, tzinfo=utc)
    # ... while 19:59 EDT (23:59 UTC) is still September.
    assert mrs.month_start_utc(datetime(2026, 9, 30, 19, 59, tzinfo=mrs.ET)) == datetime(2026, 9, 1, tzinfo=utc)
    # the boundary instant belongs to its own month; the year rolls over; microseconds are dropped
    assert mrs.month_start_utc(datetime(2026, 10, 1, tzinfo=utc)) == datetime(2026, 10, 1, tzinfo=utc)
    assert mrs.month_start_utc(datetime(2026, 12, 31, 23, 59, 59, 999999, tzinfo=utc)) == datetime(2026, 12, 1, tzinfo=utc)
    assert mrs.month_start_utc(datetime(2027, 1, 1, 0, 0, 0, 1, tzinfo=utc)) == datetime(2027, 1, 1, tzinfo=utc)
    now = mrs.month_start_utc()
    assert (now.day, now.hour, now.minute, now.second, now.microsecond) == (1, 0, 0, 0, 0)
    assert now.tzinfo == utc and now <= datetime.now(utc)


def test_charges_since_counts_the_window_and_fails_closed_on_an_unreadable_time():
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    post = {"metadata": {"charges": [
        {"at": "2026-09-30T23:59:59.999999+00:00", "op": "x_create", "micros": 15000},   # last month
        {"at": "2026-10-01T00:00:00Z", "op": "x_create", "micros": 15000},                # the boundary counts
        {"at": "2026-09-30T21:30:00-04:00", "op": "x_read", "micros": 5000},              # 01:30 UTC Oct 1
        {"at": "2026-10-02T10:00:00+00:00", "op": "x_create_refund", "micros": -15000},   # a refund nets out
        {"at": "not a time", "op": "x_create", "micros": 15000},                          # unreadable: COUNTED
        {"at": None, "op": "x_read", "micros": 7},                                        # no time: counted
        {"op": "x_read", "micros": 3},
        {"at": "2026-10-03T00:00:00+00:00", "op": "x_create", "micros": "15000"},         # numeric text
        "garbage", 42, None, ["x"],                                                       # not entries
        {"at": "2026-10-03T00:00:00+00:00", "micros": "abc"},                             # junk micros
        {"at": "2026-10-03T00:00:00+00:00", "micros": [1]},
        {"at": "2026-10-03T00:00:00+00:00", "micros": {"n": 1}},
        {"at": "2026-10-03T00:00:00+00:00", "micros": None},                              # = 0
    ]}}
    assert mrs.charges_since(post, since) == 15000 + 5000 - 15000 + 15000 + 7 + 3 + 15000


@pytest.mark.parametrize("post", [{}, {"metadata": None}, {"metadata": "junk"}, {"metadata": ["a"]},
                                  {"metadata": {}}, {"metadata": {"charges": None}},
                                  {"metadata": {"charges": []}}])
def test_charges_since_without_a_journal_is_zero(post):
    assert mrs.charges_since(post, datetime(2026, 10, 1, tzinfo=timezone.utc)) == 0


# ── transition_post: fenced, merging, journaled ─────────────────────────────


@pytest.mark.asyncio
async def test_transition_merges_metadata_and_never_drops_the_review_keys(svc):
    meta = {**_p5_meta(), "next_attempt_at": _T1,
            "publish": {"state": "sending", "attempt": 1, "text_sha256": "h1",
                        "bluesky": {"rkey": "3abc", "record": {"text": "t"}}}}
    row = _p5_post(svc, status="queued", metadata=meta)
    out = await svc.transition_post(
        row["id"], expect_status="queued", observed=_read(row), status="published",
        meta={"posted": {"url": "u"}}, publish={"state": "published", "bluesky": {"rkey": "3abc"}},
        unset=("next_attempt_at", "never_there"), external_id="1", external_url="https://x.com/i/1",
        published_at=_T1, last_error=None,
    )
    assert out is not None and out == _live_post(svc, row["id"])
    m = out["metadata"]
    assert m["dry_run"] is False and m["review"] == _p5_meta()["review"] and m["review_notified_at"] == _T0
    assert "next_attempt_at" not in m and m["posted"] == {"url": "u"}
    # metadata.publish merges SHALLOWLY: untouched keys stay, a nested dict passed in replaces its namesake
    assert m["publish"] == {"state": "published", "attempt": 1, "text_sha256": "h1", "bluesky": {"rkey": "3abc"}}
    assert out["status"] == "published" and out["external_id"] == "1" and out["published_at"] == _T1
    assert mrs._parse_ts(out["updated_at"]) > mrs._parse_ts(_T0)


@pytest.mark.asyncio
async def test_unset_runs_before_meta_so_a_key_can_be_replaced(svc):
    row = _p5_post(svc, metadata={**_p5_meta(), "next_attempt_at": _T0, "alert_notified_at": _T0})
    out = await svc.transition_post(row["id"], expect_status="approved", observed=_read(row),
                                    unset=("next_attempt_at", "alert_notified_at"), meta={"next_attempt_at": _T1})
    assert out["metadata"]["next_attempt_at"] == _T1 and "alert_notified_at" not in out["metadata"]
    assert out["status"] == "approved"   # no status given: an annotation only


@pytest.mark.parametrize("bad", [None, "junk", ["a"], 7, {"publish": "junk"}, {"publish": None, "charges": "junk"}])
@pytest.mark.asyncio
async def test_transition_on_malformed_metadata_writes_a_clean_dict(svc, bad):
    row = _p5_post(svc, status="queued", metadata=bad)
    out = await svc.transition_post(row["id"], expect_status="queued", observed=_read(row),
                                    publish={"state": "unknown"}, charge=("x_create", 15000))
    assert out["metadata"]["publish"] == {"state": "unknown"}
    assert [(c["op"], c["micros"]) for c in out["metadata"]["charges"]] == [("x_create", 15000)]
    assert out["cost_micros"] == 15000


@pytest.mark.asyncio
async def test_a_write_that_landed_after_our_read_is_kept_and_the_retry_works_from_the_fresh_row(svc, monkeypatch):
    row = _p5_post(svc, status="queued", cost_micros=15000,
                   metadata={**_p5_meta(), "charges": [{"at": _T0, "op": "x_create", "micros": 15000}]})
    seen = _read(row)
    live = _live_post(svc, row["id"])
    # Meanwhile the feed stamped it and a reconcile read was charged.
    live.update(updated_at=_T1, cost_micros=20000,
                metadata={**live["metadata"], "posted_notified_at": _T1,
                          "charges": live["metadata"]["charges"] + [{"at": _T1, "op": "x_read", "micros": 5000}]})
    calls = _spy_updates(monkeypatch, svc)
    out = await svc.transition_post(row["id"], expect_status="queued", observed=seen,
                                    charge=("x_read_correction", -5000), meta={"note": 1})
    assert len(calls) == 2   # the stale fence lost; the one built from the fresh row won
    assert out["metadata"]["posted_notified_at"] == _T1 and out["metadata"]["note"] == 1
    assert [c["op"] for c in out["metadata"]["charges"]] == ["x_create", "x_read", "x_read_correction"]
    # the concurrent charge is not lost: cost and journal both derive from the FRESH row
    assert out["cost_micros"] == 15000 == sum(c["micros"] for c in out["metadata"]["charges"])


@pytest.mark.asyncio
async def test_with_no_retries_a_lost_fence_is_none_and_writes_nothing(svc, monkeypatch):
    row = _p5_post(svc, status="queued")
    seen = _read(row)
    _live_post(svc, row["id"])["updated_at"] = _T1
    snapshot = _read(_live_post(svc, row["id"]))
    calls = _spy_updates(monkeypatch, svc)
    assert await svc.transition_post(row["id"], expect_status="queued", observed=seen, retries=0,
                                     status="published", meta={"note": 1}, charge=("x_create", 15000)) is None
    assert len(calls) == 1 and _live_post(svc, row["id"]) == snapshot


@pytest.mark.asyncio
async def test_a_concurrent_write_between_the_read_and_the_update_is_retried_once(svc, monkeypatch):
    row = _p5_post(svc, status="queued")

    def land(n):
        if n == 0:
            live = _live_post(svc, row["id"])
            live.update(updated_at=_T1, metadata={**live["metadata"], "alert_notified_at": _T1})

    calls = _spy_updates(monkeypatch, svc, before=land)
    out = await svc.transition_post(row["id"], expect_status="queued", meta={"x": 1})   # reads it itself
    assert len(calls) == 2 and out["metadata"]["alert_notified_at"] == _T1 and out["metadata"]["x"] == 1


@pytest.mark.asyncio
async def test_contention_beyond_the_retry_budget_is_none(svc, monkeypatch):
    row = _p5_post(svc, status="queued")

    def land(n):
        _live_post(svc, row["id"])["updated_at"] = f"2026-09-01T13:{n:02d}:00+00:00"

    calls = _spy_updates(monkeypatch, svc, before=land)
    assert await svc.transition_post(row["id"], expect_status="queued", meta={"x": 1}, retries=1) is None
    assert len(calls) == 2 and "x" not in _live_post(svc, row["id"])["metadata"]


@pytest.mark.asyncio
async def test_transition_returns_none_once_the_status_moved_and_writes_nothing(svc, monkeypatch):
    row = _p5_post(svc, status="queued")
    seen = _read(row)
    _live_post(svc, row["id"]).update(status="published", updated_at=_T1)
    calls = _spy_updates(monkeypatch, svc)
    assert await svc.transition_post(row["id"], expect_status="queued", observed=seen, status="failed",
                                     meta={"x": 1}) is None
    live = _live_post(svc, row["id"])
    assert live["status"] == "published" and "x" not in live["metadata"]
    assert len(calls) == 1   # the fenced attempt; the re-read saw `published` and stopped there
    # An observed row already outside expect_status is refused without any write at all.
    assert await svc.transition_post(row["id"], expect_status="approved", observed=_read(live),
                                     status="queued") is None
    assert len(calls) == 1
    # A post that does not exist.
    assert await svc.transition_post(str(uuid.uuid4()), expect_status="queued", meta={"x": 1}) is None


@pytest.mark.asyncio
async def test_the_fence_compares_instants_handles_a_null_stamp_and_accepts_a_status_tuple(svc):
    row = _p5_post(svc, status="approved", updated_at="2026-09-01T12:00:00+00:00")
    seen = _read(row)
    seen["updated_at"] = "2026-09-01T12:00:00Z"   # the same instant, written the other way
    assert await svc.transition_post(row["id"], expect_status="approved", observed=seen, retries=0,
                                     meta={"a": 1}) is not None
    never = _p5_post(svc, status="pending_review", updated_at=None, created_at=_T0)
    out = await svc.transition_post(never["id"], expect_status=("approved", "pending_review"),
                                    observed=_read(never), status="skipped", retries=0)
    assert out is not None and out["status"] == "skipped" and out["updated_at"]


@pytest.mark.asyncio
async def test_a_charge_is_journaled_and_added_to_cost_micros_in_the_same_write(svc, monkeypatch):
    row = _p5_post(svc, status="approved", cost_micros=None)
    calls = _spy_updates(monkeypatch, svc)
    before = datetime.now(timezone.utc)
    out = await svc.transition_post(row["id"], expect_status="approved", observed=_read(row),
                                    charge=("x_create", 15000))
    after = datetime.now(timezone.utc)
    assert len(calls) == 1
    assert calls[0]["cost_micros"] == 15000 and calls[0]["metadata"]["charges"][-1]["micros"] == 15000
    (entry,) = out["metadata"]["charges"]
    assert set(entry) == {"at", "op", "micros"} and (entry["op"], entry["micros"]) == ("x_create", 15000)
    assert before <= mrs._parse_ts(entry["at"]) <= after
    # One instant: a charge bumps updated_at to its own time, which is what spend_since reads by.
    assert entry["at"] == out["updated_at"]
    for op, micros in (("x_read", 5000), ("x_read_correction", -5000), ("x_create_refund", -15000),
                       ("x_delete", 10000)):
        out = await svc.transition_post(row["id"], expect_status="approved", charge=(op, micros))
    assert len(out["metadata"]["charges"]) == 5
    assert out["cost_micros"] == 10000 == sum(c["micros"] for c in out["metadata"]["charges"])
    assert out["metadata"]["review"] == _p5_meta()["review"]


@pytest.mark.parametrize("kwargs, match", [
    ({"status": "teleported"}, "unknown post status"),
    ({"status": "Published"}, "unknown post status"),
    ({"platform": "tiktok"}, "not writable"),
    ({"run_id": "other"}, "not writable"),
    ({"updated_at": _T1}, "not writable"),
    ({"idempotency_key": "2026-09-30:x:text"}, "not writable"),
    ({"charge": ("x_create", 15000), "cost_micros": 0}, "a charge OR cost_micros"),
])
@pytest.mark.asyncio
async def test_transition_refuses_a_bad_request_before_any_io(svc, monkeypatch, kwargs, match):
    row = _p5_post(svc, status="approved")
    snapshot = _read(row)
    calls = _spy_updates(monkeypatch, svc)
    with pytest.raises(ValueError, match=match):
        await svc.transition_post(row["id"], expect_status="approved", observed=_read(row), **kwargs)
    assert calls == [] and _live_post(svc, row["id"]) == snapshot


@pytest.mark.asyncio
async def test_transition_refuses_a_raw_metadata_column_that_would_bypass_the_merge(svc):
    row = _p5_post(svc, status="queued")
    with pytest.raises(ValueError, match="not writable"):
        await svc.transition_post(row["id"], expect_status="queued", observed=_read(row),
                                  charge=("x_create", 15000), metadata={"publish": {"state": "sending"}})


@pytest.mark.asyncio
async def test_a_ledger_failure_is_a_marketing_run_error_naming_the_post_and_writes_nothing(svc):
    row = _p5_post(svc, status="queued")
    snapshot = _read(row)
    svc.fake.tables[mrs.POSTS].fail_updates.append(RuntimeError("520: origin unreachable"))
    with pytest.raises(mrs.MarketingRunError, match="transition_post failed") as info:
        await svc.transition_post(row["id"], expect_status="queued", observed=_read(row), status="published",
                                  charge=("x_create", 15000))
    assert row["id"] in str(info.value) and "RuntimeError" in str(info.value)
    assert _live_post(svc, row["id"]) == snapshot and svc.fake.tables[mrs.POSTS].fail_updates == []


# ── claim_post: the write-ahead ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_claim_is_the_write_ahead_in_one_fenced_update(svc, monkeypatch):
    row = _p5_post(svc, status="approved", attempts=1, cost_micros=15000, metadata={
        **_p5_meta(), "charges": [{"at": _T0, "op": "x_create", "micros": 15000}],
        "publish": {"state": "not_sent", "last_outcome": "not_sent", "attempt": 1}})
    before = _read(row)
    calls = _spy_updates(monkeypatch, svc)
    publish = {"attempt": 2, "started_at": _T1, "state": "sending", "text_sha256": "h"}
    out = await svc.claim_post(row["id"], observed=_read(row), publish=publish, charge=("x_create", 15000))
    assert len(calls) == 1   # ONE write: status, attempts, claimed_at, publish and the charge together
    assert out["status"] == "queued" and out["attempts"] == 2 and mrs._parse_ts(out["claimed_at"]) is not None
    assert out["metadata"]["publish"] == {**before["metadata"]["publish"], **publish}
    assert [c["micros"] for c in out["metadata"]["charges"]] == [15000, 15000] and out["cost_micros"] == 30000
    assert out["metadata"]["review"] == before["metadata"]["review"] and out["metadata"]["dry_run"] is False
    assert out["metadata"]["review_notified_at"] == _T0


@pytest.mark.asyncio
async def test_a_claim_on_a_row_that_changed_since_it_was_read_is_refused_and_not_retried(svc, monkeypatch):
    row = _p5_post(svc, status="approved")
    seen = _read(row)
    _live_post(svc, row["id"]).update(updated_at=_T1)   # e.g. the review sweep re-stamped it
    snapshot = _read(_live_post(svc, row["id"]))
    calls = _spy_updates(monkeypatch, svc)
    assert await svc.claim_post(row["id"], observed=seen, publish={"state": "sending"},
                                charge=("x_create", 15000)) is None
    # No re-read-and-retry: the publisher re-judges the row (fresh? backoff? cap?) on its next pass.
    assert len(calls) == 1 and _live_post(svc, row["id"]) == snapshot


@pytest.mark.asyncio
async def test_two_containers_claiming_the_same_observed_row_charge_and_count_it_once(svc):
    row = _p5_post(svc, status="approved")
    seen = _read(row)
    a = await svc.claim_post(row["id"], observed=_read(seen), publish={"state": "sending"}, charge=("x_create", 15000))
    b = await svc.claim_post(row["id"], observed=_read(seen), publish={"state": "sending"}, charge=("x_create", 15000))
    assert a is not None and b is None
    live = _live_post(svc, row["id"])
    assert live["attempts"] == 1 and live["cost_micros"] == 15000 and len(live["metadata"]["charges"]) == 1


@pytest.mark.parametrize("status", ["pending_review", "queued", "rejected", "published"])
@pytest.mark.asyncio
async def test_only_an_approved_row_can_be_claimed(svc, monkeypatch, status):
    row = _p5_post(svc, status=status)
    calls = _spy_updates(monkeypatch, svc)
    assert await svc.claim_post(row["id"], observed=_read(row), charge=("x_create", 15000)) is None
    assert calls == [] and _live_post(svc, row["id"])["status"] == status


@pytest.mark.asyncio
async def test_a_claim_counts_attempts_from_a_missing_value_and_fails_loudly_on_a_ledger_error(svc):
    row = _p5_post(svc, status="approved", attempts=None)
    out = await svc.claim_post(row["id"], observed=_read(row), publish={"state": "sending"})
    assert out["attempts"] == 1 and "charges" not in out["metadata"] and out["cost_micros"] == 0
    other = _p5_post(svc, status="approved")
    svc.fake.tables[mrs.POSTS].fail_updates.append(ConnectionError("reset by peer"))
    with pytest.raises(mrs.MarketingRunError):   # the publisher must not send without its write-ahead
        await svc.claim_post(other["id"], observed=_read(other), charge=("x_create", 15000))
    assert _live_post(svc, other["id"])["status"] == "approved" and _live_post(svc, other["id"])["attempts"] == 0


@pytest.mark.asyncio
async def test_claim_post_without_observed_keeps_its_old_contract(svc):
    row = _p5_post(svc, status="approved", attempts=1)
    meta_before = _read(row)["metadata"]
    first = await svc.claim_post(row["id"])
    assert first["status"] == "queued" and first["claimed_at"] and first["attempts"] == 1
    assert first["metadata"] == meta_before and first["cost_micros"] == 0
    assert await svc.claim_post(row["id"]) is None
    assert await svc.claim_post(str(uuid.uuid4())) is None


# ── spend_since / any_post_with_meta ─────────────────────────────────────────


def _charges(*pairs) -> List[Dict[str, Any]]:
    return [{"at": at, "op": "x_create", "micros": micros} for at, micros in pairs]


@pytest.mark.asyncio
async def test_spend_since_sums_only_this_platforms_charges_since_the_instant(svc):
    since = mrs.month_start_utc(datetime(2026, 10, 5, tzinfo=timezone.utc))
    # Touched this month but carrying LAST month's charge too: only the new charge counts.
    _p5_post(svc, status="published", updated_at="2026-10-02T09:00:00+00:00", cost_micros=30000,
             metadata={"charges": _charges(("2026-09-30T23:00:00+00:00", 15000), ("2026-10-02T09:00:00+00:00", 15000))})
    # Touched at exactly the month start; an ET-evening charge that is already October in UTC.
    _p5_post(svc, status="queued", updated_at="2026-10-01T00:00:00Z",
             metadata={"charges": _charges(("2026-09-30T21:30:00-04:00", 5000))})
    # An unreadable entry time on a row touched this month counts (fail-closed).
    _p5_post(svc, status="failed", updated_at="2026-10-03T00:00:00+00:00", metadata={"charges": _charges(("??", 7))})
    # A 402 refund nets its charge out.
    _p5_post(svc, status="failed", updated_at="2026-10-04T00:00:01+00:00",
             metadata={"charges": _charges(("2026-10-04T00:00:00+00:00", 15000), ("2026-10-04T00:00:01+00:00", -15000))})
    # Malformed metadata adds nothing and breaks nothing.
    _p5_post(svc, status="failed", updated_at="2026-10-05T00:00:00+00:00", metadata=None)
    # Another platform's charges are not X's.
    _p5_post(svc, platform="bluesky", status="published", updated_at="2026-10-02T00:00:00+00:00",
             metadata={"charges": _charges(("2026-10-02T00:00:00+00:00", 99999))})
    # An X row not touched since the month start is not read at all.
    _p5_post(svc, status="published", updated_at="2026-09-30T23:59:59+00:00",
             metadata={"charges": _charges(("2026-09-30T23:59:59+00:00", 15000))})
    assert await svc.spend_since("x", since) == 15000 + 5000 + 7
    assert await svc.spend_since("bluesky", since) == 99999
    assert await svc.spend_since("threads", since) == 0
    # Last month, read from September: the September charges, and the refunded pair nets to 0.
    assert await svc.spend_since("x", datetime(2026, 9, 1, tzinfo=timezone.utc)) == 15000 + 15000 + 5000 + 7 + 15000


@pytest.mark.asyncio
async def test_spend_since_raises_when_the_read_fails_never_a_silent_zero(svc, monkeypatch):
    async def unavailable(query):
        raise RuntimeError("503 from PostgREST")

    monkeypatch.setattr(mrs, "sb_exec", unavailable)
    with pytest.raises(mrs.MarketingRunError, match="spend_since"):
        await svc.spend_since("x", datetime(2026, 10, 1, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_any_post_with_meta_matches_platform_key_and_value(svc):
    _p5_post(svc, status="skipped", metadata={**_p5_meta(), "x_cap_alert_month": "2026-10"})
    _p5_post(svc, platform="bluesky", metadata={"x_cap_alert_month": "2026-11"})
    _p5_post(svc, metadata=None)
    assert await svc.any_post_with_meta("x", "x_cap_alert_month", "2026-10") is True
    assert await svc.any_post_with_meta("x", "x_cap_alert_month", "2026-09") is False   # last month's marker
    assert await svc.any_post_with_meta("x", "x_cap_alert_month", "2026-11") is False   # another platform's
    assert await svc.any_post_with_meta("bluesky", "x_cap_alert_month", "2026-11") is True
    assert await svc.any_post_with_meta("x", "other_marker", "2026-10") is False


# ── expire_stale_posts ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expiry_closes_only_stale_approved_and_pending_rows(svc):
    t = _P5_TODAY
    stale = [
        _p5_post(svc, status="approved", run_day=t - timedelta(days=2)),
        _p5_post(svc, status="pending_review", run_day=t - timedelta(days=3)),
        _p5_post(svc, status="approved", key="not-a-key"),            # never fresh → never sent
        _p5_post(svc, status="approved", run_day=t + timedelta(days=1)),
    ]
    kept = [
        _p5_post(svc, status="approved", run_day=t - timedelta(days=1)),
        _p5_post(svc, status="pending_review", run_day=t),
        _p5_post(svc, status="queued", run_day=t - timedelta(days=10)),   # its outcome may be live
        _p5_post(svc, status="published", run_day=t - timedelta(days=10)),
        _p5_post(svc, status="failed", run_day=t - timedelta(days=10)),
        _p5_post(svc, status="rejected", run_day=t - timedelta(days=10)),
    ]
    kept_before = [_read(r) for r in kept]
    assert await svc.expire_stale_posts(t) == len(stale)
    for r in stale:
        live = _live_post(svc, r["id"])
        assert live["status"] == "skipped" and live["metadata"]["skip_reason"] == "expired"
        assert mrs._parse_ts(live["metadata"]["expired_at"]) is not None
        assert live["metadata"]["review"] == _p5_meta()["review"] and live["metadata"]["dry_run"] is False
    assert [_live_post(svc, r["id"]) for r in kept] == kept_before
    assert await svc.expire_stale_posts(t) == 0


@pytest.mark.asyncio
async def test_expiry_turns_over_at_midnight_new_york(svc):
    post = _p5_post(svc, status="approved", run_day=date(2026, 9, 29))
    assert await svc.expire_stale_posts(run_date_et(datetime(2026, 10, 1, 3, 59, 59, tzinfo=timezone.utc))) == 0
    assert _live_post(svc, post["id"])["status"] == "approved"
    assert await svc.expire_stale_posts(run_date_et(datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc))) == 1
    assert _live_post(svc, post["id"])["status"] == "skipped"


@pytest.mark.asyncio
async def test_expiry_drains_a_backlog_oldest_first_within_the_limit(svc):
    rows = [_p5_post(svc, status="approved", run_day=date(2026, 9, 1) + timedelta(days=i),
                     created_at=f"2026-09-{1 + i:02d}T20:00:00+00:00") for i in range(5)]
    svc.fake.tables[mrs.POSTS].rows.reverse()   # stored newest first: the ORDER BY must do the work

    def skipped():
        return [r["id"] for r in rows if _live_post(svc, r["id"])["status"] == "skipped"]

    assert await svc.expire_stale_posts(_P5_TODAY, limit=2) == 2 and skipped() == [r["id"] for r in rows[:2]]
    assert await svc.expire_stale_posts(_P5_TODAY, limit=2) == 2 and skipped() == [r["id"] for r in rows[:4]]
    assert await svc.expire_stale_posts(_P5_TODAY, limit=2) == 1 and skipped() == [r["id"] for r in rows]


@pytest.mark.asyncio
async def test_expiry_loses_cleanly_to_a_review_landing_between_its_read_and_its_write(svc, monkeypatch):
    post = _p5_post(svc, status="pending_review", run_day=date(2026, 9, 27))

    def land(n):
        if n == 0:
            _live_post(svc, post["id"]).update(status="approved", updated_at=_T1, approved_by="telegram:42")

    calls = _spy_updates(monkeypatch, svc, before=land)
    assert await svc.expire_stale_posts(_P5_TODAY) == 0
    live = _live_post(svc, post["id"])
    assert live["status"] == "approved" and "skip_reason" not in live["metadata"] and len(calls) == 1
    # The next pass judges the row afresh — it is still stale, so it closes.
    assert await svc.expire_stale_posts(_P5_TODAY) == 1 and live["status"] == "skipped"


# ── request_retract (the webhook's only write for `k`) ─────────────────────────


@pytest.mark.asyncio
async def test_request_retract_records_only_the_request(svc, monkeypatch):
    post = _p5_post(svc, status="published", external_id="1")
    outcome, row = await svc.request_retract(post["id"], by="telegram:42")
    assert outcome == "requested" and row["status"] == "published" and row["external_id"] == "1"
    meta = row["metadata"]
    assert mrs._parse_ts(meta["retract_requested_at"]) is not None
    assert meta["retract"] == {"requested_at": meta["retract_requested_at"], "by": "telegram:42",
                               "attempts": 0, "state": "requested"}
    assert meta["review"] == _p5_meta()["review"] and meta["dry_run"] is False
    calls = _spy_updates(monkeypatch, svc)
    again, row2 = await svc.request_retract(post["id"], by="telegram:7")   # a double confirm
    assert again == "already_requested" and row2["metadata"]["retract"]["by"] == "telegram:42" and calls == []


@pytest.mark.parametrize("status", ["pending_review", "approved", "queued", "failed", "skipped", "rejected",
                                    "retracted"])
@pytest.mark.asyncio
async def test_request_retract_never_touches_a_row_that_is_not_published(svc, monkeypatch, status):
    post = _p5_post(svc, status=status)
    snapshot = _read(post)
    calls = _spy_updates(monkeypatch, svc)
    outcome, row = await svc.request_retract(post["id"], by="telegram:42")
    assert outcome == f"already_{status}" and row["id"] == post["id"]
    assert calls == [] and _live_post(svc, post["id"]) == snapshot


@pytest.mark.asyncio
async def test_request_retract_on_a_missing_post(svc):
    assert await svc.request_retract(str(uuid.uuid4()), by="telegram:42") == ("not_found", None)


@pytest.mark.parametrize("landing, expected", [
    (lambda live: live.update(updated_at=_T1, metadata={**live["metadata"], "posted_notified_at": _T1}),
     "requested"),
    (lambda live: live.update(updated_at=_T1, metadata={**live["metadata"], "retract_requested_at": _T1,
                                                         "retract": {"by": "telegram:7"}}),
     "already_requested"),
    (lambda live: live.update(updated_at=_T1, status="retracted"), "already_retracted"),
], ids=["feed-stamp", "other-tap", "retracted"])
@pytest.mark.asyncio
async def test_request_retract_against_a_concurrent_write(svc, monkeypatch, landing, expected):
    post = _p5_post(svc, status="published")

    def land(n):
        if n == 0:
            landing(_live_post(svc, post["id"]))

    _spy_updates(monkeypatch, svc, before=land)
    outcome, row = await svc.request_retract(post["id"], by="telegram:42")
    assert outcome == expected
    if expected == "requested":
        assert row["metadata"]["posted_notified_at"] == _T1 and row["metadata"]["retract"]["by"] == "telegram:42"
    if expected == "already_requested":
        assert row["metadata"]["retract"] == {"by": "telegram:7"}


@pytest.mark.asyncio
async def test_request_retract_under_constant_contention_says_busy_and_writes_nothing(svc, monkeypatch):
    post = _p5_post(svc, status="published")

    def land(n):
        _live_post(svc, post["id"])["updated_at"] = f"2026-09-01T13:{n:02d}:00+00:00"

    calls = _spy_updates(monkeypatch, svc, before=land)
    outcome, row = await svc.request_retract(post["id"], by="telegram:42")
    assert outcome == "busy" and len(calls) == 2
    assert "retract_requested_at" not in _live_post(svc, post["id"])["metadata"]


# ── resolve_unknown (the owner's "It's live" / "Not posted") ─────────────────────


def _escalated(svc, **kw) -> Dict[str, Any]:
    return _p5_post(svc, status="queued", attempts=1, cost_micros=15000, metadata={
        **_p5_meta(), "charges": [{"at": _T0, "op": "x_create", "micros": 15000}],
        "publish": {"state": "escalated", "attempt": 1, "escalated_at": _T0, "text_sha256": "h"}}, **kw)


@pytest.mark.asyncio
async def test_resolve_unknown_live_marks_it_published_with_the_owner_outcome(svc):
    post = _escalated(svc)
    outcome, row = await svc.resolve_unknown(post["id"], "live", by="telegram:42")
    assert outcome == "published" and row["status"] == "published" and mrs._parse_ts(row["published_at"])
    assert row["last_error"] == "owner confirmed it is live; the platform id is unknown"
    owner = row["metadata"]["owner_outcome"]
    assert (owner["decision"], owner["by"]) == ("live", "telegram:42") and mrs._parse_ts(owner["at"])
    assert row["metadata"]["publish"]["state"] == "published" and row["metadata"]["publish"]["text_sha256"] == "h"
    assert row["metadata"]["review"] == _p5_meta()["review"] and row["external_id"] is None
    assert row["cost_micros"] == 15000 and len(row["metadata"]["charges"]) == 1   # an answer costs nothing
    assert (await svc.resolve_unknown(post["id"], "not_posted", by="telegram:42"))[0] == "already_published"


@pytest.mark.asyncio
async def test_resolve_unknown_not_posted_marks_it_failed(svc):
    post = _escalated(svc)
    outcome, row = await svc.resolve_unknown(post["id"], "not_posted", by="telegram:42")
    assert outcome == "failed" and row["status"] == "failed" and row["published_at"] is None
    assert row["last_error"] == "owner confirmed it was not posted"
    assert row["metadata"]["publish"]["state"] == "owner_not_posted"
    assert row["metadata"]["owner_outcome"]["decision"] == "not_posted"
    assert (await svc.resolve_unknown(post["id"], "live", by="telegram:42"))[0] == "already_failed"


@pytest.mark.parametrize("metadata", [
    {"publish": {"state": "sending"}}, {"publish": {"state": "unknown"}}, {"publish": {}},
    {"publish": "escalated"}, {}, None, "junk",
])
@pytest.mark.asyncio
async def test_resolve_unknown_answers_only_an_escalated_row(svc, monkeypatch, metadata):
    post = _p5_post(svc, status="queued", metadata=metadata)
    snapshot = _read(post)
    calls = _spy_updates(monkeypatch, svc)
    for decision in ("live", "not_posted"):
        outcome, row = await svc.resolve_unknown(post["id"], decision, by="telegram:42")
        assert outcome == "not_escalated" and row["id"] == post["id"]
    assert calls == [] and _live_post(svc, post["id"]) == snapshot


@pytest.mark.parametrize("status", ["published", "failed", "skipped", "approved", "retracted"])
@pytest.mark.asyncio
async def test_resolve_unknown_on_a_settled_row_reports_its_status(svc, monkeypatch, status):
    post = _p5_post(svc, status=status, metadata={**_p5_meta(), "publish": {"state": "escalated"}})
    calls = _spy_updates(monkeypatch, svc)
    assert (await svc.resolve_unknown(post["id"], "live", by="telegram:42"))[0] == f"already_{status}"
    assert calls == []


@pytest.mark.parametrize("decision", ["retry", "resend", "LIVE", "", "approve"])
@pytest.mark.asyncio
async def test_resolve_unknown_refuses_any_other_answer_before_reading(svc, monkeypatch, decision):
    post = _escalated(svc)
    snapshot = _read(post)
    with pytest.raises(ValueError, match="unknown resolution"):
        await svc.resolve_unknown(post["id"], decision, by="telegram:42")
    with pytest.raises(ValueError):
        await svc.resolve_unknown(str(uuid.uuid4()), decision, by="telegram:42")
    assert _live_post(svc, post["id"]) == snapshot


@pytest.mark.asyncio
async def test_resolve_unknown_on_a_missing_post(svc):
    assert await svc.resolve_unknown(str(uuid.uuid4()), "live", by="telegram:42") == ("not_found", None)


@pytest.mark.asyncio
async def test_resolve_unknown_loses_to_a_reconcile_that_settled_it_first(svc, monkeypatch):
    post = _escalated(svc)

    def land(n):
        if n == 0:
            _live_post(svc, post["id"]).update(status="published", updated_at=_T1, external_id="99")

    _spy_updates(monkeypatch, svc, before=land)
    outcome, row = await svc.resolve_unknown(post["id"], "not_posted", by="telegram:42")
    assert outcome == "already_published" and row["external_id"] == "99"
    assert _live_post(svc, post["id"])["status"] == "published" and "owner_outcome" not in row["metadata"]


# ── list_posts_filtered ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_posts_filtered_applies_every_filter_in_the_query_before_the_limit(svc):
    def at(day):
        return f"2026-09-{day:02d}T00:00:00+00:00"

    req = {"retract_requested_at": _T0}
    plain = _p5_post(svc, status="published", updated_at=at(1))                                     # no request
    blue = _p5_post(svc, status="published", platform="bluesky", updated_at=at(2), metadata=dict(req))
    _p5_post(svc, status="queued", updated_at=at(3), metadata=dict(req))                             # wrong status
    want = _p5_post(svc, status="published", updated_at=at(5), metadata=dict(req))
    later = _p5_post(svc, status="published", updated_at=at(7), metadata=dict(req))
    tiktok = _p5_post(svc, status="published", platform="tiktok", updated_at=at(8), metadata=None)
    svc.fake.tables[mrs.POSTS].rows.reverse()

    def ids(rows):
        return [r["id"] for r in rows]

    nn = ("metadata->>retract_requested_at",)
    assert ids(await svc.list_posts_filtered(status="published", limit=1, not_null=nn, platforms=["x"])) == [want["id"]]
    assert ids(await svc.list_posts_filtered(status="published", not_null=nn)) == [blue["id"], want["id"], later["id"]]
    assert ids(await svc.list_posts_filtered(status="published", limit=1, null=nn)) == [plain["id"]]
    assert ids(await svc.list_posts_filtered(status="published", null=nn, not_platforms=["x"])) == [tiktok["id"]]
    assert ids(await svc.list_posts_filtered(status="published", limit=1, not_platforms=["x", "tiktok"])) == [blue["id"]]
    assert await svc.list_posts_filtered(status="published", platforms=[]) == []
    assert await svc.list_posts_filtered(status="queued", platforms=["bluesky"]) == []


# ── close_finished_runs ──────────────────────────────────────────────────────


def _run_with_posts(svc, run_day: date, post_statuses, *, status="media_ready") -> Dict[str, Any]:
    run = _seed_run(svc, run_day, status, touched=datetime(2026, 9, 1, tzinfo=timezone.utc))
    for i, s in enumerate(post_statuses):
        _p5_post(svc, status=s, run_id=run["id"], run_day=run_day, fmt=f"f{i}")
    return run


@pytest.mark.asyncio
async def test_close_finished_runs_closes_only_settled_days_before_yesterday(svc):
    d = date
    closes = {
        "published": [_run_with_posts(svc, d(2026, 9, 21), ["published", "failed", "rejected"]),
                      _run_with_posts(svc, d(2026, 9, 22), ["retracted", "skipped"]),
                      _run_with_posts(svc, d(2026, 9, 28), ["published"])],   # the day before yesterday
        "skipped": [_run_with_posts(svc, d(2026, 9, 23), ["failed", "skipped", "rejected"]),
                    _run_with_posts(svc, d(2026, 9, 24), [])],
    }
    stays = [
        _run_with_posts(svc, d(2026, 9, 25), ["published", "approved"]),
        _run_with_posts(svc, d(2026, 9, 26), ["pending_review"]),
        _run_with_posts(svc, d(2026, 9, 27), ["failed", "queued"]),
        _run_with_posts(svc, d(2026, 9, 29), ["published"]),                  # yesterday: still in the window
        _run_with_posts(svc, d(2026, 9, 30), ["published"]),
        _run_with_posts(svc, d(2026, 9, 19), ["published"], status="in_progress"),
        _run_with_posts(svc, d(2026, 9, 18), ["skipped"], status="failed"),
    ]
    stays_before = [_read(_stored(svc, r["id"])) for r in stays]
    assert await svc.close_finished_runs(_P5_TODAY) == 5
    for final, runs in closes.items():
        for r in runs:
            live = _stored(svc, r["id"])
            assert live["status"] == final, (r["run_date"], live["status"])
            assert mrs._parse_ts(live["finished_at"]) is not None and live["updated_at"] == live["finished_at"]
    assert [_stored(svc, r["id"]) for r in stays] == stays_before
    assert await svc.close_finished_runs(_P5_TODAY) == 0


@pytest.mark.parametrize("landing", [
    lambda run: run.update(updated_at="2026-09-02T00:00:00+00:00"),
    lambda run: run.update(status="failed", updated_at="2026-09-02T00:00:00+00:00"),
], ids=["touched", "status-moved"])
@pytest.mark.asyncio
async def test_close_finished_runs_is_a_cas_on_the_run_it_judged(svc, monkeypatch, landing):
    run = _run_with_posts(svc, date(2026, 9, 20), ["published"])

    def land(n):
        if n == 0:
            landing(_stored(svc, run["id"]))

    calls = _spy_updates(monkeypatch, svc, table=mrs.RUNS, before=land)
    assert await svc.close_finished_runs(_P5_TODAY) == 0 and len(calls) == 1
    assert _stored(svc, run["id"])["status"] in ("media_ready", "failed")
    assert "finished_at" not in _stored(svc, run["id"]) or _stored(svc, run["id"])["finished_at"] is None


@pytest.mark.asyncio
async def test_close_finished_runs_is_bounded_and_oldest_first(svc):
    import inspect

    # 50 a tick: runs still holding a queued post (an unanswered escalation) are skipped AFTER the
    # limit, so a smaller window could be filled by them and stop newer runs from ever closing.
    assert inspect.signature(svc.close_finished_runs).parameters["limit"].default == 50
    runs = [_run_with_posts(svc, date(2026, 9, 1) + timedelta(days=i), ["failed"]) for i in range(4)]
    svc.fake.tables[mrs.RUNS].rows.reverse()
    assert await svc.close_finished_runs(_P5_TODAY, limit=3) == 3
    assert [_stored(svc, r["id"])["status"] for r in runs] == ["skipped", "skipped", "skipped", "media_ready"]
    assert await svc.close_finished_runs(_P5_TODAY, limit=3) == 1
