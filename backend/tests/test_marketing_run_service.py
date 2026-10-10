"""
Marketing run ledger (migration 170 / `app/services/marketing/run_service.py`) — the claim matrix, the
content-addressed paths, and the service against an in-memory PostgREST fake.

No network: the fake below stands in for `get_supabase()` (conftest blocks sockets anyway).
"""

from __future__ import annotations

import copy
import json
import re
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
            # `metrics` defaults to {} like the real column (migration 170: NOT NULL DEFAULT '{}').
            mrs.POSTS: _Table([("idempotency_key",), ("run_id", "platform", "format")],
                              {"attempts": 0, "cost_micros": 0, "metadata": dict, "metrics": dict}),
            # migration 173: run_id is the PRIMARY KEY (first-write-wins selection claim).
            mrs.SCRIPTS: _Table([("run_id",)], {"status": "selected", "fact_sheet": dict,
                                                "violations": list, "generations": 0,
                                                "tokens_used": 0}, generated_id=False),
            # migration 173: PRIMARY KEY (campaign, day); written by smart_link's increment RPC
            # (not modelled — tests seed rows), read by the weekly digest.
            mrs.LINK_HITS: _Table([("campaign", "day")], {"hits": 0}, generated_id=False),
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
    # A lesson template id (drop 2: the post gate reads the class from the SCRIPT's template id, so a row
    # without one is the 422 class refusal — tested below — not the unusable-copy refusal under test here).
    await svc.insert_script({"run_id": row["id"], "template_id": "checklist", **script})
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
    # content_class: 170's inline CHECK is SUPERSEDED by 190's named one (drop 2 widened it to A/C/F),
    # so it is compared against the LATEST migration that ADDs the named constraint —
    # test_content_class_check_reads_the_latest_migration_that_adds_it. 170's list must stay a subset.
    assert check_values("content_class") <= set(schemas.CONTENT_CLASSES)
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
    # K1: nor `closed` — close_finished_runs' record of WHY it closed a day, which the weekly digest prints
    upd = await svc.update_run(rid, metadata={"closed": {"reason": "posted", "posts": {"published": 9}},
                                              "voice_asset_id": "a1"}, worker=True, claim=_holder(svc, rid))
    assert "closed" not in upd["metadata"] and upd["metadata"]["voice_asset_id"] == "a1"
    assert "closed" not in svc.fake.tables[mrs.RUNS].rows[0]["metadata"]
    _, reason = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW,
                                    claim_nonce="forged-nonce")
    assert reason == IN_PROGRESS
    await svc.update_run(rid, status="skipped", finished=True, worker=True, claim=_holder(svc, rid))
    with pytest.raises(MarketingRunNotHeld):
        await svc.update_run(rid, status="failed", worker=True, claim=_holder(svc, rid))  # a closed day stays closed
    assert svc.fake.tables[mrs.RUNS].rows[0]["status"] == "skipped"
    # the SERVER's own writes (the selection mirror) are not fenced
    await svc.update_run(rid, source_ref="journey:x")


def test_the_server_owned_run_metadata_keys():
    """K1: `claim_nonce` (trusted by decide_claim ahead of the attempts cap) and `closed` (why
    close_finished_runs closed the day — printed by the weekly digest) are written by the server only.
    Drop 1 (compat F2): so is `worker_capabilities` — what the claiming worker declared it can render."""
    # Drop 2: `series` / `series_trail` (the day's news series and its fallback chain, mirrored from
    # the script's fact sheet) are the server's too.
    assert set(schemas.SERVER_OWNED_RUN_METADATA) == {"claim_nonce", "closed", "worker_capabilities",
                                                      "series", "series_trail"}
    assert schemas.RUN_WORKER_CAPABILITIES_KEY in schemas.SERVER_OWNED_RUN_METADATA


@pytest.mark.asyncio
async def test_a_worker_patch_can_never_plant_a_close_record(svc, caplog):
    """K1: the worker closes its own day `skipped` and smuggles `metadata.closed` with it. The digest reads
    `closed.reason` for a skipped day without a skip_reason, so a planted one would explain the day in
    the least-trusted process's words. The key is dropped (logged WARNING); every other key lands."""
    import logging

    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    rid = row["id"]
    planted = {"closed": {"at": "2026-09-17T22:00:00+00:00", "reason": "posted", "posts": {"published": 9},
                          "skip_reasons": {}},
               "claim_nonce": "f" * 32, "note": "kept"}
    with caplog.at_level(logging.WARNING, logger=mrs.logger.name):
        upd = await svc.update_run(rid, status="skipped", finished=True, metadata=planted, worker=True,
                                   claim=_holder(svc, rid))
    stored = svc.fake.tables[mrs.RUNS].rows[0]
    assert upd["status"] == stored["status"] == "skipped"
    assert "closed" not in stored["metadata"] and stored["metadata"]["note"] == "kept"
    assert stored["metadata"]["claim_nonce"] == row["metadata"]["claim_nonce"]
    assert any("server-owned metadata" in r.getMessage() and "closed" in r.getMessage() for r in caplog.records)
    # a PATCH that carries nothing but server-owned keys writes no metadata at all
    row2, _ = await svc.claim_run(date(2026, 9, 18), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    upd2 = await svc.update_run(row2["id"], stage="selected", metadata={"closed": {"reason": "mixed"}}, worker=True,
                                claim=_holder(svc, row2["id"]))
    assert upd2["stage"] == "selected" and "closed" not in upd2["metadata"]


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
    await svc.insert_script({"run_id": row["id"], "status": "accepted", "template_id": "checklist",
                             "output": output})
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
    # the metrics document has ONE writer (merge_post_metrics, fenced on its own rev)
    ({"metrics": {"likes": 1}}, "merge_post_metrics"),
    ({"metrics": {}}, "merge_post_metrics"),
    ({"metrics": None}, "merge_post_metrics"),
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


# ══ Measurement + run health (2026-10-01): the metrics writer, the digest reads, reject reasons ══
#
# `merge_post_metrics` is the ONLY writer of `marketing_posts.metrics` and must never move the
# publisher's `updated_at` fence; the digest's reads keep every filter in the query; a reject
# reason is a fenced, idempotent annotation of a REJECTED post; a closed run says why.


def _published(svc, **kw) -> Dict[str, Any]:
    kw.setdefault("published_at", _T0)
    return _p5_post(svc, status="published", external_id="1", **kw)


def _bump_likes(old: Dict[str, Any]) -> Dict[str, Any]:
    return {**old, "likes": int(old.get("likes") or 0) + 1}


# ── metrics_rev (pure) ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("metrics, expected", [
    ({}, (None, 1)), (None, (None, 1)), ("junk", (None, 1)), (["rev", 3], (None, 1)),
    ({"rev": None}, (None, 1)),                      # JSON null: `->>` is SQL NULL → IS NULL
    ({"rev": 0}, ("0", 1)), ({"rev": 1}, ("1", 2)), ({"rev": 41}, ("41", 42)),
    # hand-edited revs: fenced on EXACTLY what `->>` renders (or the write could never land), and
    # the counter restarts at 1
    ({"rev": "7"}, ("7", 1)), ({"rev": "abc"}, ("abc", 1)), ({"rev": True}, ("true", 1)),
    ({"rev": False}, ("false", 1)), ({"rev": 2.0}, ("2.0", 1)), ({"rev": -3}, ("-3", 1)),
    ({"rev": [1]}, ("[1]", 1)),
])
def test_metrics_rev_fences_on_the_stored_text_and_counts_only_a_real_int(metrics, expected):
    assert mrs.metrics_rev(metrics) == expected


def test_metrics_rev_text_is_what_the_fake_json_path_renders():
    """Anti-vacuity for the fence tests below: the fake's `->>` (the PostgREST text path) and
    `metrics_rev` render the same stored value identically."""
    for value in (0, 7, "7", "abc", True, 2.0, -3, [1]):
        fence, _ = mrs.metrics_rev({"rev": value})
        assert _col({"metrics": {"rev": value}}, "metrics->>rev") == fence, value


# ── merge_post_metrics: the one writer of the metrics column ──────────────────


@pytest.mark.asyncio
async def test_merge_post_metrics_first_write_is_rev_1_and_each_later_write_bumps_it(svc, monkeypatch):
    row = _published(svc)
    assert "metrics" not in row          # a seeded row: the column absent reads as {} with no rev
    calls = _spy_updates(monkeypatch, svc)
    out = await svc.merge_post_metrics(row["id"], observed=_read(row), merge=_bump_likes)
    assert out["metrics"] == {"likes": 1, "rev": 1}
    for n in (2, 3):
        out = await svc.merge_post_metrics(row["id"], observed=_read(_live_post(svc, row["id"])),
                                           merge=_bump_likes)
        assert out["metrics"] == {"likes": n, "rev": n}
    # ONE column per write: never metadata, never updated_at
    assert [sorted(c) for c in calls] == [["metrics"]] * 3


@pytest.mark.asyncio
async def test_a_post_created_through_the_ledger_starts_from_the_column_default(svc):
    post = await _pending_post(svc)
    assert post["metrics"] == {}          # the fake's default, like the real column's
    _live_post(svc, post["id"]).update(status="published", published_at=_T0)
    out = await svc.merge_post_metrics(post["id"], observed=None, merge=_bump_likes)   # reads it itself
    assert out["metrics"] == {"likes": 1, "rev": 1}


@pytest.mark.asyncio
async def test_a_metrics_write_never_moves_the_publishers_fence(svc):
    row = _published(svc, metadata={**_p5_meta(), "publish": {"state": "published"}})
    publisher_read = _read(row)                    # the publisher read the row BEFORE the metrics write
    await svc.merge_post_metrics(row["id"], observed=_read(row), merge=lambda old: {"likes": 5})
    live = _live_post(svc, row["id"])
    assert live["updated_at"] == _T0 and live["metadata"] == publisher_read["metadata"]
    # …so the publisher's fenced write still lands, and does not clobber the metrics in between
    done = await svc.transition_post(row["id"], expect_status="published", observed=publisher_read, retries=0,
                                     meta={"retract_requested_at": _T1})
    assert done is not None and done["metadata"]["retract_requested_at"] == _T1
    assert done["metrics"] == {"likes": 5, "rev": 1}
    # …and a metrics write whose read predates that publisher write still lands too: its fence is the
    # rev, and it writes only its own column (the publisher's new key survives)
    out = await svc.merge_post_metrics(row["id"], observed=_read(live) | {"metadata": {}, "updated_at": _T0},
                                       merge=_bump_likes)
    assert out["metrics"] == {"likes": 6, "rev": 2}
    assert out["metadata"]["retract_requested_at"] == _T1 and out["updated_at"] == done["updated_at"]


@pytest.mark.asyncio
async def test_a_lost_rev_fence_re_reads_and_re_applies_merge_to_the_fresh_document(svc, monkeypatch):
    row = _published(svc)
    await svc.merge_post_metrics(row["id"], observed=_read(row), merge=lambda old: {"history": ["d1"]})
    stale = _read(_live_post(svc, row["id"]))                                  # rev 1
    await svc.merge_post_metrics(row["id"], observed=_read(stale),
                                 merge=lambda old: {**old, "history": old["history"] + ["d2"]})   # rev 2 lands
    seen: List[Dict[str, Any]] = []

    def merge(old):
        seen.append(copy.deepcopy(old))
        return {**old, "history": old.get("history", []) + ["d3"]}

    calls = _spy_updates(monkeypatch, svc)
    out = await svc.merge_post_metrics(row["id"], observed=stale, merge=merge)
    assert len(calls) == 2 and [s["rev"] for s in seen] == [1, 2]   # re-applied to the FRESH document
    assert out["metrics"] == {"history": ["d1", "d2", "d3"], "rev": 3}   # the concurrent d2 is kept


@pytest.mark.asyncio
async def test_with_no_retries_a_lost_rev_fence_is_none_and_writes_nothing(svc, monkeypatch):
    row = _published(svc)
    seen = _read(row)
    _live_post(svc, row["id"])["metrics"] = {"likes": 9, "rev": 4}      # someone else wrote meanwhile
    calls = _spy_updates(monkeypatch, svc)
    assert await svc.merge_post_metrics(row["id"], observed=seen, merge=_bump_likes, retries=0) is None
    assert len(calls) == 1 and _live_post(svc, row["id"])["metrics"] == {"likes": 9, "rev": 4}


@pytest.mark.asyncio
async def test_constant_rev_contention_is_none_after_the_budget_and_warns(svc, monkeypatch, caplog):
    import logging

    row = _published(svc)

    def land(n):
        _live_post(svc, row["id"])["metrics"] = {"rev": 100 + n}

    calls = _spy_updates(monkeypatch, svc, before=land)
    with caplog.at_level(logging.WARNING, logger=mrs.logger.name):
        assert await svc.merge_post_metrics(row["id"], observed=_read(row), merge=_bump_likes, retries=2) is None
    assert len(calls) == 3 and _live_post(svc, row["id"])["metrics"] == {"rev": 102}
    assert any("metrics NOT written" in r.getMessage() and row["id"] in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("status", ["retracted", "queued", "failed", "pending_review", "skipped"])
@pytest.mark.asyncio
async def test_merge_post_metrics_writes_nothing_once_the_post_left_published(svc, monkeypatch, status):
    row = _published(svc)
    seen = _read(row)
    _live_post(svc, row["id"])["status"] = status        # e.g. retracted between the read and the write
    calls = _spy_updates(monkeypatch, svc)
    assert await svc.merge_post_metrics(row["id"], observed=seen, merge=_bump_likes) is None
    assert len(calls) == 1          # the fenced attempt missed; the re-read saw the new status and stopped
    assert "metrics" not in _live_post(svc, row["id"])
    # an observed row already outside expect_status: no write at all
    assert await svc.merge_post_metrics(row["id"], observed=_read(_live_post(svc, row["id"])),
                                        merge=_bump_likes) is None
    assert len(calls) == 1
    # …unless the caller expects that status too (a status tuple, like transition_post)
    out = await svc.merge_post_metrics(row["id"], observed=None, merge=_bump_likes,
                                       expect_status=("published", status))
    assert out["metrics"] == {"likes": 1, "rev": 1}


@pytest.mark.asyncio
async def test_merge_post_metrics_on_a_missing_post_is_none(svc, monkeypatch):
    calls = _spy_updates(monkeypatch, svc)
    assert await svc.merge_post_metrics(str(uuid.uuid4()), observed=None, merge=_bump_likes) is None
    assert calls == []


@pytest.mark.parametrize("stored", [{"rev": "abc", "likes": 2}, {"rev": True}, {"rev": 2.0}, {"rev": -1},
                                    {"rev": None, "likes": 3}, ["not", "an", "object"], "junk", None])
@pytest.mark.asyncio
async def test_a_hand_edited_or_malformed_metrics_document_is_still_written_and_restarts_the_rev(svc, stored):
    row = _published(svc, metrics=stored)
    out = await svc.merge_post_metrics(row["id"], observed=_read(row), merge=_bump_likes)
    assert out is not None and out["metrics"]["rev"] == 1
    base = stored.get("likes") if isinstance(stored, dict) else None
    assert out["metrics"]["likes"] == int(base or 0) + 1
    again = await svc.merge_post_metrics(row["id"], observed=_read(out), merge=_bump_likes)
    assert again["metrics"]["rev"] == 2


@pytest.mark.asyncio
async def test_the_writer_owns_rev_and_merge_gets_a_private_copy(svc):
    row = _published(svc, metrics={"history": [{"day": "2026-09-01", "likes": 1}], "rev": 3})
    seen = _read(row)

    def vandal(old):
        old["history"].append({"day": "x"})      # mutates its argument…
        return {**old, "rev": 999}              # …and tries to choose the rev

    out = await svc.merge_post_metrics(row["id"], observed=seen, merge=vandal)
    assert out["metrics"]["rev"] == 4
    assert seen["metrics"]["history"] == [{"day": "2026-09-01", "likes": 1}]   # the caller's row untouched


@pytest.mark.parametrize("bad", [None, [], "metrics", 7, ({"likes": 1},)])
@pytest.mark.asyncio
async def test_a_merge_that_returns_no_dict_raises_before_any_write(svc, monkeypatch, bad):
    row = _published(svc)
    calls = _spy_updates(monkeypatch, svc)
    with pytest.raises(ValueError, match="not a dict"):
        await svc.merge_post_metrics(row["id"], observed=_read(row), merge=lambda old: bad)
    assert calls == [] and "metrics" not in _live_post(svc, row["id"])


@pytest.mark.asyncio
async def test_a_metrics_ledger_failure_is_a_marketing_run_error_and_writes_nothing(svc):
    row = _published(svc)
    svc.fake.tables[mrs.POSTS].fail_updates.append(RuntimeError("520: origin unreachable"))
    with pytest.raises(mrs.MarketingRunError, match="merge_post_metrics failed") as info:
        await svc.merge_post_metrics(row["id"], observed=_read(row), merge=_bump_likes)
    assert row["id"] in str(info.value)
    assert "metrics" not in _live_post(svc, row["id"])


@pytest.mark.asyncio
async def test_the_legacy_mark_post_cannot_write_metrics_either(svc, monkeypatch):
    row = _published(svc)
    calls = _spy_updates(monkeypatch, svc)
    with pytest.raises(ValueError, match="not writable"):
        await svc.mark_post(row["id"], "published", metrics={"likes": 1})
    assert calls == [] and "metrics" not in mrs._POST_WRITABLE


# ── marketing_posts carries NO trigger (#19): the database half of merge_post_metrics' fence ──
#
# The payload spies above prove the CODE half: a metrics write sends only `metrics`, never
# `updated_at`. The DATABASE half is that nothing moves `updated_at` behind it — the repo's standard
# `BEFORE UPDATE … update_updated_at_column()` trigger (on users, whales, user_credits, agent_personas)
# would, and every daily metrics write would then break the publisher's `updated_at` fence on the same
# row (a retract request, a reconcile transition read before the measure write would lose its CAS).
# FakeSupabase models no triggers, so only a scan of the schema can see one.

_SQL_COMMENT_RE = re.compile(r"('(?:[^']|'')*')|(--[^\n]*)|(/\*.*?\*/)", re.S)
_CREATE_TRIGGER_RE = re.compile(
    r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:CONSTRAINT\s+)?TRIGGER\b[^;]*?\bON\s+(?:ONLY\s+)?"
    r"((?:\"?[A-Za-z_][A-Za-z0-9_$]*\"?\s*\.\s*)?\"?[A-Za-z_][A-Za-z0-9_$]*\"?)",
    re.I | re.S,
)


def _sql_without_comments(sql: str) -> str:
    """`sql` with every `--` and `/* */` comment blanked and every string literal kept whole — so a
    `/*` INSIDE a string (the snapshot's `COMMENT ON COLUMN public.users.is_admin IS '…/admin/*. …'`)
    cannot swallow the real SQL after it, which the naive strip of the other scans here would."""
    return _SQL_COMMENT_RE.sub(lambda m: m.group(1) if m.group(1) is not None else " ", sql)


def _trigger_tables(sql: str) -> List[str]:
    """The table of every CREATE TRIGGER in `sql` (comments stripped), as `schema.table` in lower case;
    an unqualified table is `public.<table>` (the migrations' search_path)."""
    out = []
    for m in _CREATE_TRIGGER_RE.finditer(_sql_without_comments(sql)):
        name = re.sub(r'["\s]', "", m.group(1)).lower()
        out.append(name if "." in name else f"public.{name}")
    return out


@pytest.mark.parametrize("sql, tables", [
    ("CREATE TRIGGER t BEFORE UPDATE ON public.marketing_posts FOR EACH ROW EXECUTE FUNCTION f();",
     ["public.marketing_posts"]),
    ("create or replace trigger trg_touch\n  before update\n  on marketing_posts\n  for each row execute function f();",
     ["public.marketing_posts"]),
    ('CREATE CONSTRAINT TRIGGER "t" AFTER UPDATE ON "public"."Marketing_Posts" FOR EACH ROW EXECUTE FUNCTION f();',
     ["public.marketing_posts"]),
    ("CREATE TRIGGER t AFTER INSERT OR UPDATE OF metrics, status ON ONLY public . marketing_posts EXECUTE FUNCTION f();",
     ["public.marketing_posts"]),
    # the trigger name may start with "on_": the ON clause still decides
    ("CREATE TRIGGER on_auth_user_created\n    AFTER INSERT ON auth.users FOR EACH ROW EXECUTE FUNCTION g();",
     ["auth.users"]),
    # commented out — line and block — is not a trigger
    ("-- CREATE TRIGGER t BEFORE UPDATE ON public.marketing_posts FOR EACH ROW EXECUTE FUNCTION f();", []),
    ("/* CREATE TRIGGER t\n BEFORE UPDATE ON public.marketing_posts\n FOR EACH ROW EXECUTE FUNCTION f(); */", []),
    # a `/*` inside a string literal opens no comment: the trigger after it is still seen
    ("COMMENT ON COLUMN public.users.is_admin IS 'see /api/v1/admin/*. it''s fine';\n"
     "CREATE TRIGGER t BEFORE UPDATE ON public.marketing_posts FOR EACH ROW EXECUTE FUNCTION f();\n"
     "/* a later */ SELECT 1;", ["public.marketing_posts"]),
    # neighbours are not the table
    ("CREATE TRIGGER t BEFORE UPDATE ON public.marketing_runs FOR EACH ROW EXECUTE FUNCTION f();",
     ["public.marketing_runs"]),
    ("CREATE TRIGGER t BEFORE UPDATE ON public.marketing_posts_archive FOR EACH ROW EXECUTE FUNCTION f();",
     ["public.marketing_posts_archive"]),
    ("DROP TRIGGER IF EXISTS t ON public.marketing_posts;", []),
])
def test_the_trigger_scanner_reads_real_sql_shapes(sql, tables):
    assert _trigger_tables(sql) == tables


def test_marketing_posts_carries_no_trigger_in_the_snapshot_or_any_migration():
    """#19. If this fails, a migration added a trigger on marketing_posts. `merge_post_metrics` writes the
    `metrics` column ONLY and must never move `updated_at` — the fence of every publisher / review write
    on the same row (rules marketing.md §2: fenced, merging transitions). Drop the trigger, or move the
    metrics document to its own table before adding it."""
    from pathlib import Path

    database = Path(__file__).resolve().parents[1] / "database"
    snapshot = database / "schema_snapshot.sql"
    migrations = sorted((database / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
    snapshot_sql = snapshot.read_text()
    found = {"schema_snapshot.sql": _trigger_tables(snapshot_sql)}
    for path in migrations:
        found[path.name] = _trigger_tables(path.read_text())
    # Anti-vacuity: the scan reads the real files and sees the triggers we know exist, in both formats
    # (pg_dump one-liners and hand-written multi-line migrations).
    assert "CREATE TABLE public.marketing_posts" in snapshot_sql and len(migrations) >= 150
    assert {"public.users", "public.whales", "public.user_credits", "public.agent_personas"} <= set(
        found["schema_snapshot.sql"])
    assert "auth.users" in found["044_auth_trigger_bypass_rls.sql"]
    assert "public.whale_filing_snapshots" in found["143_whale_trades_dedupe.sql"]
    offenders = sorted(name for name, tables in found.items() if "public.marketing_posts" in tables)
    assert offenders == [], (
        f"a CREATE TRIGGER on public.marketing_posts in {offenders}: merge_post_metrics' fence assumes the "
        f"table has none (a trigger moving updated_at breaks the publisher's updated_at CAS on every "
        f"metrics write)")


# ── list_measurable_posts ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_measurable_posts_filters_in_the_query_oldest_published_first(svc):
    since = datetime(2026, 9, 3, tzinfo=timezone.utc)
    at_since = _published(svc, published_at="2026-09-03T00:00:00+00:00")          # the boundary counts
    _published(svc, published_at="2026-09-02T23:59:59.999999+00:00")               # a microsecond early
    later = _published(svc, platform="bluesky", published_at="2026-09-10T12:00:00+00:00")
    mid = _published(svc, published_at="2026-09-05T12:00:00+00:00")
    et_form = _published(svc, published_at="2026-09-04T08:00:00+00:00")            # 04:00 EDT
    _p5_post(svc, status="retracted", published_at="2026-09-06T00:00:00+00:00")
    _p5_post(svc, status="queued", published_at="2026-09-06T00:00:00+00:00")
    _p5_post(svc, status="published", published_at=None)                          # matches no comparison
    svc.fake.tables[mrs.POSTS].rows.reverse()                                       # ORDER BY must do it

    def ids(rows):
        return [r["id"] for r in rows]

    assert ids(await svc.list_measurable_posts(since=since)) == [at_since["id"], et_form["id"], mid["id"],
                                                                  later["id"]]
    # the platform filter is in the query, before the LIMIT
    assert ids(await svc.list_measurable_posts(since=since, platforms=["bluesky"], limit=1)) == [later["id"]]
    assert ids(await svc.list_measurable_posts(since=since, limit=2)) == [at_since["id"], et_form["id"]]
    assert await svc.list_measurable_posts(since=since, platforms=[]) == []
    assert await svc.list_measurable_posts(since=since, platforms=["tiktok"]) == []
    assert await svc.list_measurable_posts(since=since, limit=0) == []
    # the same instant written other ways
    assert ids(await svc.list_measurable_posts(since="2026-09-02T20:00:00-04:00"))[0] == at_since["id"]
    assert ids(await svc.list_measurable_posts(since=datetime(2026, 9, 3)))[0] == at_since["id"]   # naive = UTC


@pytest.mark.asyncio
async def test_list_measurable_posts_on_an_empty_ledger_and_bad_arguments(svc):
    assert await svc.list_measurable_posts(since=datetime(2026, 9, 1, tzinfo=timezone.utc)) == []
    for bad in (date(2026, 9, 1), "2026-09-01", None, 1759000000, "yesterday"):
        with pytest.raises(ValueError):
            await svc.list_measurable_posts(since=bad)


# ── list_posts_created_between ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_posts_created_between_is_half_open_and_any_status(svc):
    start = datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)      # Monday 00:00 EDT
    end = datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc)        # the next Monday 00:00 EDT

    def made(at, status="published"):
        return _p5_post(svc, status=status, created_at=at, updated_at=at)

    at_start = made("2026-09-28T04:00:00+00:00", "rejected")            # included
    made("2026-09-28T03:59:59.999999+00:00")                            # Sunday 23:59 ET: excluded
    inside = [made("2026-10-01T12:00:00+00:00", "skipped"), made("2026-10-03T20:15:00+00:00", "pending_review")]
    made("2026-10-05T04:00:00+00:00")                                   # the end is excluded
    last = made("2026-10-05T03:59:59.999999+00:00", "failed")
    svc.fake.tables[mrs.POSTS].rows.reverse()
    rows = await svc.list_posts_created_between(start, end)
    assert [r["id"] for r in rows] == [at_start["id"], *(r["id"] for r in inside), last["id"]]
    # the same window written as ET strings
    assert [r["id"] for r in await svc.list_posts_created_between("2026-09-28T00:00:00-04:00",
                                                                  "2026-10-05T00:00:00-04:00")] == \
        [r["id"] for r in rows]
    assert [r["id"] for r in await svc.list_posts_created_between(start, end, limit=1)] == [at_start["id"]]


@pytest.mark.asyncio
async def test_list_posts_created_between_reads_nothing_for_an_empty_window(svc, monkeypatch):
    made = _p5_post(svc, created_at="2026-10-01T12:00:00+00:00")
    t = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
    reads: List[str] = []
    real_exec = mrs._exec

    async def counting(query, *, op, **ids):
        reads.append(op)
        return await real_exec(query, op=op, **ids)

    monkeypatch.setattr(mrs, "_exec", counting)
    assert await svc.list_posts_created_between(t, t) == []                         # empty
    assert await svc.list_posts_created_between(t, t - timedelta(days=1)) == []     # inverted
    assert await svc.list_posts_created_between(t - timedelta(days=1), t, limit=0) == []
    assert reads == []
    assert [r["id"] for r in await svc.list_posts_created_between(t, t + timedelta(microseconds=1))] == [made["id"]]
    for bad in ((date(2026, 10, 1), t), (t, "2026-10-02"), (None, t), (t, 5)):
        with pytest.raises(ValueError):
            await svc.list_posts_created_between(*bad)


@pytest.mark.asyncio
async def test_list_posts_created_between_warns_when_the_limit_may_hide_rows(svc, caplog):
    import logging

    for i in range(3):
        _p5_post(svc, created_at=f"2026-10-01T12:00:0{i}+00:00")
    with caplog.at_level(logging.WARNING, logger=mrs.logger.name):
        rows = await svc.list_posts_created_between(datetime(2026, 10, 1, tzinfo=timezone.utc),
                                                    datetime(2026, 10, 2, tzinfo=timezone.utc), limit=2)
    assert len(rows) == 2 and any("hit the limit" in r.getMessage() for r in caplog.records)


# ── runs by date ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_run_for_date_and_list_runs_between(svc):
    assert await svc.get_run_for_date(date(2026, 9, 28)) is None          # an empty ledger
    assert await svc.list_runs_between(date(2026, 9, 1), date(2026, 9, 30)) == []
    seeded = {d: _seed_run(svc, date(2026, 9, d), "media_ready", touched=datetime(2026, 9, d, tzinfo=timezone.utc))
              for d in (30, 26, 28, 27, 29)}                                # stored out of order
    assert (await svc.get_run_for_date(date(2026, 9, 28)))["id"] == seeded[28]["id"]
    assert (await svc.get_run_for_date("2026-09-28"))["id"] == seeded[28]["id"]
    assert await svc.get_run_for_date(date(2026, 9, 25)) is None
    got = await svc.list_runs_between(date(2026, 9, 27), date(2026, 9, 29))     # inclusive both ends
    assert [r["run_date"] for r in got] == ["2026-09-27", "2026-09-28", "2026-09-29"]
    assert [r["run_date"] for r in await svc.list_runs_between("2026-09-29", "2026-09-29")] == ["2026-09-29"]
    assert [r["run_date"] for r in await svc.list_runs_between(date(2026, 9, 1), date(2026, 12, 31))] == \
        [f"2026-09-{d}" for d in (26, 27, 28, 29, 30)]
    assert await svc.list_runs_between(date(2026, 9, 29), date(2026, 9, 27)) == []   # inverted


@pytest.mark.parametrize("bad", [datetime(2026, 9, 28, tzinfo=timezone.utc), datetime(2026, 9, 28),
                                 "28/09/2026", "2026-09-31", "", None, 20260928])
@pytest.mark.asyncio
async def test_a_run_date_must_be_a_calendar_date(svc, bad):
    """A datetime is refused: which ET day it is depends on a zone the caller did not say."""
    with pytest.raises(ValueError):
        await svc.get_run_for_date(bad)
    with pytest.raises(ValueError):
        await svc.list_runs_between(bad, date(2026, 9, 30))
    with pytest.raises(ValueError):
        await svc.link_hits_between(date(2026, 9, 1), bad)
    # the weekly cost line's script read takes the same calendar days, at either end
    with pytest.raises(ValueError):
        await svc.script_tokens_between(bad, date(2026, 9, 30))
    with pytest.raises(ValueError):
        await svc.script_tokens_between(date(2026, 9, 1), bad)


# ── spend_by_op_since / charges_by_op_since ──────────────────────────────────


_MESSY_JOURNAL = [
    {"at": "2026-09-30T23:59:59.999999+00:00", "op": "x_create", "micros": 15000},   # last month
    {"at": "2026-10-01T00:00:00Z", "op": "x_create", "micros": 15000},                # the boundary counts
    {"at": "2026-10-02T10:00:00+00:00", "op": "x_metrics_read", "micros": 5000},
    {"at": "2026-10-02T10:00:01+00:00", "op": "x_metrics_read_correction", "micros": -4000},
    {"at": "2026-10-02T10:00:02+00:00", "op": "x_account_read", "micros": "10000"},   # numeric text
    {"at": "not a time", "op": "x_create", "micros": 15000},                          # unreadable: COUNTED
    {"op": "x_metrics_read", "micros": 7},                                            # no time: counted
    {"at": "2026-10-03T00:00:00+00:00", "micros": 3},                                 # no op: `unknown`
    {"at": "2026-10-03T00:00:00+00:00", "op": None, "micros": 2},
    {"at": "2026-10-03T00:00:00+00:00", "op": 42, "micros": 1},
    {"at": "2026-10-03T00:00:00+00:00", "op": "   ", "micros": 1},
    {"at": "2026-10-03T00:00:00+00:00", "op": "x_create"},                            # no amount: skipped
    {"at": "2026-10-03T00:00:00+00:00", "op": "x_ghost"},                             # …so no `x_ghost: 0`
    {"at": "2026-10-03T00:00:00+00:00", "op": "x_ghost", "micros": None},
    {"at": "2026-10-03T00:00:00+00:00", "op": "x_ghost", "micros": True},             # a bool is no amount
    {"at": "2026-10-03T00:00:00+00:00", "op": "x_create", "micros": "abc"},           # junk: skipped
    {"at": "2026-10-03T00:00:00+00:00", "op": "x_create", "micros": [1]},
    {"at": "2026-10-03T00:00:00+00:00", "op": "x_create", "micros": float("inf")},    # too large: skipped
    {"at": "2026-10-03T00:00:00+00:00", "op": "x_create", "micros": float("nan")},
    "garbage", 42, None, ["x"],                                                       # not entries
]


@pytest.mark.parametrize("post", [
    {"metadata": {"charges": _MESSY_JOURNAL}},
    {"metadata": {"charges": "junk"}}, {"metadata": {"charges": 5}}, {"metadata": {"charges": {"a": 1}}},
    {"metadata": {"charges": None}}, {"metadata": None}, {"metadata": "junk"}, {},
])
def test_charges_by_op_since_always_sums_to_charges_since(post):
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    by_op = mrs.charges_by_op_since(post, since)
    assert sum(by_op.values()) == mrs.charges_since(post, since)
    assert mrs.charges_by_op_since(post, datetime(2026, 10, 1)) == by_op       # a naive since is UTC


def test_charges_by_op_since_breaks_the_messy_journal_down():
    by_op = mrs.charges_by_op_since({"metadata": {"charges": _MESSY_JOURNAL}}, datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert by_op == {"x_create": 15000 + 15000, "x_metrics_read": 5000 + 7,
                     "x_metrics_read_correction": -4000, "x_account_read": 10000,
                     mrs.UNKNOWN_CHARGE_OP: 3 + 2 + 1 + 1}
    assert "x_ghost" not in by_op


@pytest.mark.asyncio
async def test_spend_by_op_since_reads_like_spend_since_and_sums_to_it(svc):
    since = mrs.month_start_utc(datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert await svc.spend_by_op_since("x", since) == {}                       # an empty ledger
    _p5_post(svc, status="published", updated_at="2026-10-03T00:00:00+00:00", metadata={"charges": _MESSY_JOURNAL})
    _p5_post(svc, status="published", updated_at="2026-10-01T00:00:00Z",           # touched at the boundary
             metadata={"charges": [{"at": "2026-10-01T00:00:00+00:00", "op": "x_create", "micros": 15000}]})
    _p5_post(svc, status="failed", updated_at="2026-10-04T00:00:00+00:00", metadata=None)
    _p5_post(svc, status="failed", updated_at="2026-10-04T00:00:00+00:00", metadata={"charges": "junk"})
    _p5_post(svc, platform="bluesky", status="published", updated_at="2026-10-02T00:00:00+00:00",
             metadata={"charges": [{"at": "2026-10-02T00:00:00+00:00", "op": "bluesky_x", "micros": 99}]})
    _p5_post(svc, status="published", updated_at="2026-09-30T23:59:59+00:00",     # not touched since: not read
             metadata={"charges": [{"at": "2026-10-02T00:00:00+00:00", "op": "x_create", "micros": 777}]})
    by_op = await svc.spend_by_op_since("x", since)
    assert by_op == {"x_create": 45000, "x_metrics_read": 5007, "x_metrics_read_correction": -4000,
                     "x_account_read": 10000, mrs.UNKNOWN_CHARGE_OP: 7}
    assert sum(by_op.values()) == await svc.spend_since("x", since)
    assert await svc.spend_by_op_since("bluesky", since) == {"bluesky_x": 99}
    assert await svc.spend_by_op_since("threads", since) == {}


@pytest.mark.parametrize("bad", [date(2026, 10, 1), "2026-10-01", None, 1759276800])
@pytest.mark.asyncio
async def test_spend_by_op_since_refuses_a_since_that_is_not_an_instant(svc, bad):
    with pytest.raises(ValueError):
        await svc.spend_by_op_since("x", bad)


@pytest.mark.asyncio
async def test_spend_by_op_since_reads_a_naive_since_as_utc(svc):
    _p5_post(svc, status="published", updated_at="2026-10-02T00:00:00+00:00",
             metadata={"charges": [{"at": "2026-10-02T00:00:00+00:00", "op": "x_create", "micros": 15000}]})
    assert await svc.spend_by_op_since("x", datetime(2026, 10, 1)) == {"x_create": 15000}
    assert await svc.spend_by_op_since("x", "2026-09-30T20:00:00-04:00") == {"x_create": 15000}


@pytest.mark.asyncio
async def test_spend_by_op_since_raises_when_the_read_fails_never_a_silent_empty(svc, monkeypatch):
    async def unavailable(query):
        raise RuntimeError("503 from PostgREST")

    monkeypatch.setattr(mrs, "sb_exec", unavailable)
    with pytest.raises(mrs.MarketingRunError, match="spend_by_op_since"):
        await svc.spend_by_op_since("x", datetime(2026, 10, 1, tzinfo=timezone.utc))


# ── link_hits_between ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_link_hits_between_is_inclusive_ordered_and_projected(svc):
    assert await svc.link_hits_between(date(2026, 9, 28), date(2026, 10, 4)) == []   # an empty table
    table = svc.fake.tables[mrs.LINK_HITS].rows
    for campaign, day, hits in [("x", "2026-10-04", 5), ("tiktok", "2026-09-27", 1), ("x", "2026-09-28", 2),
                                ("tiktok", "2026-09-28", 3), ("other", "2026-10-05", 9),
                                ("bluesky", "2026-10-01", 0)]:
        table.append({"campaign": campaign, "day": day, "hits": hits, "updated_at": _T0})
    got = await svc.link_hits_between(date(2026, 9, 28), date(2026, 10, 4))
    assert got == [{"campaign": "tiktok", "day": "2026-09-28", "hits": 3},
                   {"campaign": "x", "day": "2026-09-28", "hits": 2},
                   {"campaign": "bluesky", "day": "2026-10-01", "hits": 0},
                   {"campaign": "x", "day": "2026-10-04", "hits": 5}]
    assert await svc.link_hits_between("2026-10-05", "2026-10-05") == [{"campaign": "other", "day": "2026-10-05",
                                                                        "hits": 9}]
    assert await svc.link_hits_between(date(2026, 10, 4), date(2026, 9, 28)) == []    # inverted


# ── record_reject_reason ─────────────────────────────────────────────────────


def _rejected(svc, **kw) -> Dict[str, Any]:
    meta = {"dry_run": False, "review_notified_at": _T0, "review_message_id": 500,
            "review": {"decision": "rejected", "by": "telegram:42", "at": _T0}}
    return _p5_post(svc, status="rejected", metadata=kw.pop("metadata", meta), **kw)


@pytest.mark.asyncio
async def test_record_reject_reason_records_keeps_the_review_and_a_repeat_writes_nothing(svc, monkeypatch):
    post = _rejected(svc)
    before = _read(post)
    assert await svc.record_reject_reason(post["id"], "tone", by="telegram:42") == "recorded"
    live = _live_post(svc, post["id"])
    review = live["metadata"]["review"]
    assert {k: review[k] for k in ("decision", "by", "at")} == before["metadata"]["review"]
    assert review["reason"] == "tone" and review["reason_by"] == "telegram:42"
    assert mrs._parse_ts(review["reason_at"]) is not None
    assert {k: v for k, v in live["metadata"].items() if k != "review"} == \
        {k: v for k, v in before["metadata"].items() if k != "review"}            # everything else kept
    assert live["status"] == "rejected" and live["approved_by"] is None
    calls = _spy_updates(monkeypatch, svc)
    assert await svc.record_reject_reason(post["id"], "tone", by="telegram:42") == "unchanged"   # a double tap
    assert calls == []
    # a different later reason wins
    assert await svc.record_reject_reason(post["id"], "weak", by="telegram:7") == "recorded"
    review = _live_post(svc, post["id"])["metadata"]["review"]
    assert (review["reason"], review["reason_by"], review["decision"]) == ("weak", "telegram:7", "rejected")
    assert len(calls) == 1


@pytest.mark.parametrize("status", ["pending_review", "approved", "queued", "published", "failed", "skipped",
                                    "retracted"])
@pytest.mark.asyncio
async def test_record_reject_reason_never_touches_a_post_that_is_not_rejected(svc, monkeypatch, status):
    post = _p5_post(svc, status=status)
    snapshot = _read(post)
    calls = _spy_updates(monkeypatch, svc)
    assert await svc.record_reject_reason(post["id"], "accuracy", by="telegram:42") == f"already_{status}"
    assert calls == [] and _live_post(svc, post["id"]) == snapshot


@pytest.mark.asyncio
async def test_record_reject_reason_on_a_missing_post(svc):
    assert await svc.record_reject_reason(str(uuid.uuid4()), "other", by="telegram:42") == "not_found"


@pytest.mark.parametrize("reason", ["", "Tone", "TONE", "tone ", "spam", "reason_tone", "t", None, 1, ["tone"]])
@pytest.mark.asyncio
async def test_an_unknown_reason_is_refused_before_any_read(svc, monkeypatch, reason):
    post = _rejected(svc)
    snapshot = _read(post)
    reads: List[str] = []
    real_exec = mrs._exec

    async def counting(query, *, op, **ids):
        reads.append(op)
        return await real_exec(query, op=op, **ids)

    monkeypatch.setattr(mrs, "_exec", counting)
    with pytest.raises(ValueError, match="unknown reject reason"):
        await svc.record_reject_reason(post["id"], reason, by="telegram:42")
    assert reads == [] and _live_post(svc, post["id"]) == snapshot


@pytest.mark.parametrize("metadata", [None, "junk", {}, {"review": "junk"}, {"review": None},
                                      {"review": {"decision": "rejected", "reason": 5}}])
@pytest.mark.asyncio
async def test_a_reason_on_malformed_metadata_writes_a_clean_review(svc, metadata):
    post = _rejected(svc, metadata=metadata)
    assert await svc.record_reject_reason(post["id"], "compliance", by="telegram:42") == "recorded"
    review = _live_post(svc, post["id"])["metadata"]["review"]
    assert isinstance(review, dict) and review["reason"] == "compliance" and review["reason_by"] == "telegram:42"


@pytest.mark.asyncio
async def test_a_reason_write_that_loses_its_fence_once_is_retried_from_a_fresh_read(svc, monkeypatch):
    post = _rejected(svc)

    def land(n):
        if n == 0:   # the review sweep re-stamped the row between our read and our write
            live = _live_post(svc, post["id"])
            live.update(updated_at=_T1, metadata={**live["metadata"], "other_key": 1})

    calls = _spy_updates(monkeypatch, svc, before=land)
    assert await svc.record_reject_reason(post["id"], "other", by="telegram:42") == "recorded"
    meta = _live_post(svc, post["id"])["metadata"]
    assert len(calls) == 2 and meta["other_key"] == 1 and meta["review"]["reason"] == "other"


@pytest.mark.asyncio
async def test_a_reason_under_constant_contention_is_busy_and_writes_nothing(svc, monkeypatch):
    post = _rejected(svc)

    def land(n):
        _live_post(svc, post["id"])["updated_at"] = f"2026-09-01T13:{n:02d}:00+00:00"

    calls = _spy_updates(monkeypatch, svc, before=land)
    assert await svc.record_reject_reason(post["id"], "tone", by="telegram:42") == "busy"
    assert len(calls) == 2 and "reason" not in _live_post(svc, post["id"])["metadata"]["review"]


@pytest.mark.parametrize("landing, expected", [
    # the same reason recorded by a concurrent tap: nothing left to do
    (lambda live: live.update(updated_at=_T1, metadata={**live["metadata"], "review": {
        **live["metadata"]["review"], "reason": "tone"}}), "unchanged"),
    # a hand edit moved it out of rejected
    (lambda live: live.update(updated_at=_T1, status="skipped"), "already_skipped"),
], ids=["same-reason", "status-moved"])
@pytest.mark.asyncio
async def test_a_reason_against_a_concurrent_write(svc, monkeypatch, landing, expected):
    post = _rejected(svc)

    def land(n):
        if n == 0:
            landing(_live_post(svc, post["id"]))

    _spy_updates(monkeypatch, svc, before=land)
    assert await svc.record_reject_reason(post["id"], "tone", by="telegram:42") == expected


@pytest.mark.asyncio
async def test_a_reason_ledger_failure_raises_and_writes_nothing(svc):
    post = _rejected(svc)
    snapshot = _read(post)
    svc.fake.tables[mrs.POSTS].fail_updates.append(RuntimeError("PostgREST 520"))
    with pytest.raises(mrs.MarketingRunError):
        await svc.record_reject_reason(post["id"], "tone", by="telegram:42")
    assert _live_post(svc, post["id"]) == snapshot


# ── expire_stale_posts records where a post expired FROM ─────────────────────


@pytest.mark.asyncio
async def test_expiry_records_the_status_each_post_expired_from(svc):
    t = _P5_TODAY
    approved = _p5_post(svc, status="approved", run_day=t - timedelta(days=2))
    pending = _p5_post(svc, status="pending_review", run_day=t - timedelta(days=3),
                       metadata={"dry_run": False, "review_notified_at": _T0})
    preview = _p5_post(svc, status="pending_review", run_day=t - timedelta(days=3), fmt="video",
                       metadata={"dry_run": False, "review_preview_at": _T0})
    assert await svc.expire_stale_posts(t) == 3
    expect = {approved["id"]: "approved", pending["id"]: "pending_review", preview["id"]: "pending_review"}
    for pid, was in expect.items():
        meta = _live_post(svc, pid)["metadata"]
        assert meta["expired_from"] == was and meta["skip_reason"] == "expired"
    assert [mrs.expired_unreviewed(_live_post(svc, pid)) for pid in expect] == [False, True, True]


# ── close_summary / expired_unreviewed (pure) ───────────────────────────────


def _row(status, **meta):
    return {"status": status, "metadata": meta}


_ASKED = {"review_notified_at": _T0}
_PREVIEW_EXPIRED = _row("skipped", skip_reason="expired", expired_from="pending_review", review_preview_at=_T0)
_APPROVED = {**_ASKED, "review": {"decision": "approved", "by": "telegram:1", "at": _T0}}
#: Approved, never claimed (the X cap, a dry run, an unwired platform) — `expire_stale_posts`.
_APPROVED_EXPIRED = _row("skipped", skip_reason="expired", expired_from="approved", **_APPROVED)
#: Approved, sent with an unknown outcome the platform turned out not to hold, past its day — reconcile.
_QUEUED_EXPIRED = _row("skipped", skip_reason="expired", expired_from="queued", **_APPROVED,
                       publish={"state": "absent_expired"})


@pytest.mark.parametrize("post, unreviewed", [
    (_row("skipped", skip_reason="expired", expired_from="pending_review"), True),
    (_row("skipped", skip_reason="expired", expired_from="approved"), False),
    # an older row without expired_from: unreviewed iff it carries no review decision
    (_row("skipped", skip_reason="expired"), True),
    (_row("skipped", skip_reason="expired", review={"decision": "approved"}), False),
    (_row("skipped", skip_reason="expired", review="junk"), True),
    # the publisher's expiry of an unknown outcome (queued → skipped) was approved
    (_row("skipped", skip_reason="expired", review={"decision": "approved"}, publish={"state": "absent_expired"}),
     False),
    (_row("skipped", skip_reason="other"), False), (_row("skipped"), False),
    (_row("rejected", skip_reason="expired"), False), ({"status": "skipped", "metadata": None}, False), ({}, False),
])
def test_expired_unreviewed(post, unreviewed):
    assert mrs.expired_unreviewed(post) is unreviewed


@pytest.mark.parametrize("posts, reason", [
    ([], "no_posts"),
    ([_row("published", **_ASKED), _row("rejected", **_ASKED)], "posted"),
    ([_row("retracted", **_ASKED), _row("failed", **_ASKED)], "posted"),
    # the real day: two outlets with buttons, six read-only previews that always expire unreviewed
    ([_row("rejected", **_ASKED), _row("rejected", review={"decision": "rejected"}), *[_PREVIEW_EXPIRED] * 6],
     "all_rejected"),
    ([_row("failed", **_ASKED), _row("failed", **_ASKED), _PREVIEW_EXPIRED], "failed"),
    ([_row("skipped", skip_reason="expired", expired_from="pending_review", **_ASKED), _PREVIEW_EXPIRED],
     "expired_unreviewed"),
    ([_PREVIEW_EXPIRED, _PREVIEW_EXPIRED], "expired_unreviewed"),     # the bot was off: nobody was asked
    ([_row("skipped", skip_reason="expired"), _row("skipped", skip_reason="expired")], "expired_unreviewed"),
    ([_row("rejected", **_ASKED), _row("skipped", skip_reason="expired", expired_from="approved", **_ASKED)],
     "mixed"),
    ([_row("rejected", **_ASKED), _row("failed", **_ASKED)], "mixed"),
    ([_row("rejected"), _row("failed")], "mixed"),
    # #6: every post the owner was asked about was APPROVED and expired unsent — the X cap, a dry run, an
    # unwired platform (expired_from approved), or an unknown outcome the platform did not hold (queued)
    ([_APPROVED_EXPIRED, _APPROVED_EXPIRED, *[_PREVIEW_EXPIRED] * 6], "approved_unsent"),
    ([_APPROVED_EXPIRED, _QUEUED_EXPIRED], "approved_unsent"),
    ([_QUEUED_EXPIRED], "approved_unsent"),
    # an older row without expired_from counts by its review decision
    ([_row("skipped", skip_reason="expired", review={"decision": "approved", "by": "telegram:1"}), _PREVIEW_EXPIRED],
     "approved_unsent"),
    # the bot was off and nothing was asked: the basis is every post — unsent approvals alone still qualify
    ([_row("skipped", skip_reason="expired", expired_from="approved")], "approved_unsent"),
    # …but approved-unsent beside a post the owner never decided, a rejection or a failure is mixed
    ([_APPROVED_EXPIRED, _row("skipped", skip_reason="expired", expired_from="pending_review", **_ASKED)], "mixed"),
    ([_APPROVED_EXPIRED, _row("rejected", **_ASKED)], "mixed"),
    ([_APPROVED_EXPIRED, _row("failed", **_ASKED)], "mixed"),
    # a hand-edited expired_from is neither unreviewed nor approved-unsent: never a reassuring reason
    ([_row("skipped", skip_reason="expired", expired_from="published", **_ASKED)], "mixed"),
    ([_row("skipped", skip_reason="expired", expired_from=7, **_ASKED)], "mixed"),
    # a legacy row whose decision was a rejection (contradictory) is not an approval
    ([_row("skipped", skip_reason="expired", review={"decision": "rejected"})], "mixed"),
    # skipped for a reason other than expiry is not an expiry
    ([_row("skipped", skip_reason="dry_run", expired_from="approved", **_ASKED)], "mixed"),
])
def test_close_summary_reason(posts, reason):
    out = mrs.close_summary(posts, now=_T1)
    assert out["reason"] == reason and reason in mrs.CLOSE_REASONS and out["at"] == _T1


@pytest.mark.parametrize("post, unsent", [
    (_row("skipped", skip_reason="expired", expired_from="approved"), True),
    (_row("skipped", skip_reason="expired", expired_from="queued"), True),
    (_row("skipped", skip_reason="expired", expired_from="pending_review"), False),
    (_row("skipped", skip_reason="expired", expired_from="Approved"), False),
    (_row("skipped", skip_reason="expired", expired_from=["approved"]), False),
    (_row("skipped", skip_reason="expired", review={"decision": "approved"}), True),      # legacy
    (_row("skipped", skip_reason="expired", review={"decision": "approved"}, expired_from="pending_review"), False),
    (_row("skipped", skip_reason="expired"), False),
    (_row("skipped", skip_reason="expired", review="approved"), False),
    (_row("skipped", skip_reason="other", expired_from="approved"), False),
    (_row("approved", skip_reason="expired", expired_from="approved"), False),
    ({"status": "skipped", "metadata": None}, False), ({}, False),
])
def test_expired_approved_unsent(post, unsent):
    assert mrs.expired_approved_unsent(post) is unsent
    if unsent:
        assert mrs.expired_unreviewed(post) is False     # the two never claim the same post


def test_close_summary_counts_and_never_emits_an_unsafe_key():
    posts = [_row("published"), _row("published"), _row("rejected"), _PREVIEW_EXPIRED,
             _row("skipped", skip_reason="expired"), _row("skipped"), _row("skipped", skip_reason="Bad\nKey"),
             _row("skipped", skip_reason=7), {"status": None}, {"status": "weird status"}, "junk", None, 5]
    out = mrs.close_summary(posts)
    assert out["posts"] == {"published": 2, "rejected": 1, "skipped": 5, "unknown": 2}
    assert out["skip_reasons"] == {"expired": 2, "unknown": 3}
    assert mrs._parse_ts(out["at"]) is not None
    assert json.loads(json.dumps(out)) == out                      # plain JSON for the metadata column


# ── close_finished_runs says why, and keeps the worker's finished_at ───────


def _run_with(svc, run_day: date, posts, **run_extra) -> Dict[str, Any]:
    run = _seed_run(svc, run_day, "media_ready", touched=datetime(2026, 9, 1, tzinfo=timezone.utc), **run_extra)
    for i, (status, meta) in enumerate(posts):
        _p5_post(svc, status=status, run_id=run["id"], run_day=run_day, fmt=f"f{i}",
                 metadata=copy.deepcopy(meta))
    return run


@pytest.mark.asyncio
async def test_close_finished_runs_records_why_and_keeps_the_workers_finished_at(svc):
    worker_finished = "2026-09-21T20:31:07+00:00"
    asked, preview = dict(_ASKED), _PREVIEW_EXPIRED["metadata"]
    runs = {
        "posted": _run_with(svc, date(2026, 9, 21), [("published", asked), ("skipped", preview)],
                            finished_at=worker_finished, metadata={"claim_nonce": "n1", "video_asset_id": "v"}),
        "all_rejected": _run_with(svc, date(2026, 9, 22), [("rejected", asked), ("rejected", asked),
                                                           ("skipped", preview)]),
        "failed": _run_with(svc, date(2026, 9, 23), [("failed", asked)], finished_at=None),
        "expired_unreviewed": _run_with(svc, date(2026, 9, 24), [("skipped", {**asked, "skip_reason": "expired",
                                                                             "expired_from": "pending_review"})]),
        "no_posts": _run_with(svc, date(2026, 9, 25), []),
        "mixed": _run_with(svc, date(2026, 9, 26), [("rejected", asked), ("failed", asked)], metadata="junk"),
        # #6: X approved but capped, Bluesky approved but its unknown outcome expired, six previews
        "approved_unsent": _run_with(svc, date(2026, 9, 20), [("skipped", _APPROVED_EXPIRED["metadata"]),
                                                              ("skipped", _QUEUED_EXPIRED["metadata"]),
                                                              *[("skipped", preview)] * 6]),
    }
    assert await svc.close_finished_runs(_P5_TODAY) == len(runs)
    for reason, run in runs.items():
        live = _stored(svc, run["id"])
        closed = live["metadata"]["closed"]
        assert closed["reason"] == reason, (reason, closed)
        assert closed["at"] == live["updated_at"]
        assert live["status"] == ("published" if reason == "posted" else "skipped")
    posted = _stored(svc, runs["posted"]["id"])
    assert posted["finished_at"] == worker_finished                           # KEPT
    assert posted["metadata"]["claim_nonce"] == "n1" and posted["metadata"]["video_asset_id"] == "v"
    assert posted["metadata"]["closed"]["posts"] == {"published": 1, "skipped": 1}
    assert posted["metadata"]["closed"]["skip_reasons"] == {"expired": 1}
    for reason in ("failed", "all_rejected", "no_posts"):                   # NULL or absent → filled
        live = _stored(svc, runs[reason]["id"])
        assert live["finished_at"] == live["updated_at"] == live["metadata"]["closed"]["at"]
    assert _stored(svc, runs["no_posts"]["id"])["metadata"]["closed"]["posts"] == {}
    assert set(_stored(svc, runs["mixed"]["id"])["metadata"]) == {"closed"}    # junk metadata replaced cleanly
    unsent = _stored(svc, runs["approved_unsent"]["id"])["metadata"]["closed"]
    assert unsent["posts"] == {"skipped": 8} and unsent["skip_reasons"] == {"expired": 8}


@pytest.mark.asyncio
async def test_close_finished_runs_fences_a_null_updated_at_as_null(svc, monkeypatch):
    """The metadata merge rests on the CAS: an observed NULL updated_at is fenced IS NULL (it used to
    be no fence at all), so a write landing in between wins."""
    quiet = _run_with(svc, date(2026, 9, 20), [("failed", dict(_ASKED))], updated_at=None)
    assert await svc.close_finished_runs(_P5_TODAY) == 1
    assert _stored(svc, quiet["id"])["metadata"]["closed"]["reason"] == "failed"
    raced = _run_with(svc, date(2026, 9, 19), [("failed", dict(_ASKED))], updated_at=None)

    def land(n):
        if n == 0:
            _stored(svc, raced["id"]).update(updated_at="2026-09-02T00:00:00+00:00",
                                             metadata={"late": 1})

    calls = _spy_updates(monkeypatch, svc, table=mrs.RUNS, before=land)
    assert await svc.close_finished_runs(_P5_TODAY) == 0 and len(calls) == 1
    live = _stored(svc, raced["id"])
    assert live["status"] == "media_ready" and live["metadata"] == {"late": 1}


# ══ 2026-10-05 — the weekly cost line's ledger half (C4) ═══════════════════════════════════════════
#
# `_charge_entry` is the ONE reading rule for a journal entry, shared by the X cap (`_dated_charges`:
# unchanged and still fail-closed — the charges_since / charges_by_op_since / spend_since tests above
# are its regression guard and stay as they are) and the digest's weekly cost line (`charges_between`,
# which never counts an undated entry and reads an in-window amount it cannot price as UNREADABLE).
# The two new reads never return a partial sum: they ask for limit + 1 rows and RAISE past the limit.

_AT = "2026-10-01T00:00:00Z"
_AT_DT = datetime(2026, 10, 1, tzinfo=timezone.utc)
_WIN_START = datetime(2026, 10, 1, tzinfo=timezone.utc)
_WIN_END = datetime(2026, 10, 4, tzinfo=timezone.utc)
#: Monday 2026-09-21 00:00 EDT — where the digest's X journal read starts (the week before's start).
_PREV_WEEK_START = datetime(2026, 9, 21, 4, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("entry, expected", [
    # the amount: an int, numeric text and a float read (int() truncates); 0 and a negative ARE amounts
    ({"at": _AT, "op": "x_create", "micros": 15000}, ("x_create", 15000, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": "15000"}, ("x_create", 15000, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": 15000.7}, ("x_create", 15000, _AT_DT)),
    ({"at": _AT, "op": "x_create_refund", "micros": -15000}, ("x_create_refund", -15000, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": 0}, ("x_create", 0, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": 2 * 10**9}, ("x_create", 2 * 10**9, _AT_DT)),   # no bound HERE
    # an amount that cannot be read is None — never 0, never a crash
    ({"at": _AT, "op": "x_create"}, ("x_create", None, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": None}, ("x_create", None, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": True}, ("x_create", None, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": False}, ("x_create", None, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": "abc"}, ("x_create", None, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": "15000.7"}, ("x_create", None, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": [1]}, ("x_create", None, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": {"n": 1}}, ("x_create", None, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": float("inf")}, ("x_create", None, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": float("-inf")}, ("x_create", None, _AT_DT)),
    ({"at": _AT, "op": "x_create", "micros": float("nan")}, ("x_create", None, _AT_DT)),
    # the time: any ISO instant, a naive one (text or datetime) read as UTC; anything else is None
    ({"at": "2026-09-30T20:00:00-04:00", "op": "x_create", "micros": 1}, ("x_create", 1, _AT_DT)),
    ({"at": "2026-10-01T00:00:00", "op": "x_create", "micros": 1}, ("x_create", 1, _AT_DT)),
    ({"at": datetime(2026, 10, 1), "op": "x_create", "micros": 1}, ("x_create", 1, _AT_DT)),
    ({"at": "not a time", "op": "x_create", "micros": 1}, ("x_create", 1, None)),
    ({"at": None, "op": "x_create", "micros": 1}, ("x_create", 1, None)),
    ({"at": 7, "op": "x_create", "micros": 1}, ("x_create", 1, None)),
    ({"at": "", "op": "x_create", "micros": 1}, ("x_create", 1, None)),
    ({"at": {}, "op": "x_create", "micros": 1}, ("x_create", 1, None)),
    ({"op": "x_create", "micros": 1}, ("x_create", 1, None)),
    # the op: a missing or junk op is `unknown`; a long one is cut at 64 characters
    ({"at": _AT, "micros": 1}, (mrs.UNKNOWN_CHARGE_OP, 1, _AT_DT)),
    ({"at": _AT, "op": None, "micros": 1}, (mrs.UNKNOWN_CHARGE_OP, 1, _AT_DT)),
    ({"at": _AT, "op": 42, "micros": 1}, (mrs.UNKNOWN_CHARGE_OP, 1, _AT_DT)),
    ({"at": _AT, "op": "   ", "micros": 1}, (mrs.UNKNOWN_CHARGE_OP, 1, _AT_DT)),
    ({"at": _AT, "op": "o" * 100, "micros": 1}, ("o" * 64, 1, _AT_DT)),
    # an entry that is not a dict is no entry at all
    ("garbage", None), (42, None), (None, None), (["x"], None), (("x_create", 1), None),
])
def test_charge_entry_reads_amount_time_and_op_by_the_one_rule(entry, expected):
    assert mrs._charge_entry(entry) == expected


def test_charges_between_is_half_open_and_reports_undated_and_unreadable_apart():
    import dataclasses

    messy = {"metadata": {"charges": _MESSY_JOURNAL}}
    # [10-01, 10-04): 15000 + 5000 - 4000 + 10000 + 3 + 2 + 1 + 1 summed; the two undated entries are
    # REPORTED, never summed (the cap counts them); the eight in-window entries whose amount cannot be
    # read are reported as unreadable (the cap skips them) — a wrong number is never the answer
    assert mrs.charges_between(messy, _WIN_START, _WIN_END) == mrs.ChargeWindow(26_007, 2, 8)
    # a later window holds none of the dated entries — the same two undated ones are reported again,
    # and summed into neither window
    assert mrs.charges_between(messy, _WIN_END, datetime(2026, 10, 5, tzinfo=timezone.utc)) == \
        mrs.ChargeWindow(0, 2, 0)
    # naive instants are UTC; the same window on the New York clock is the same window
    assert mrs.charges_between(messy, datetime(2026, 10, 1), datetime(2026, 10, 4)) == mrs.ChargeWindow(26_007, 2, 8)
    assert mrs.charges_between(messy, datetime(2026, 10, 1), _WIN_END) == mrs.ChargeWindow(26_007, 2, 8)
    assert mrs.charges_between(messy, datetime(2026, 9, 30, 20, tzinfo=mrs.ET),
                               datetime(2026, 10, 3, 20, tzinfo=mrs.ET)) == mrs.ChargeWindow(26_007, 2, 8)

    edges = {"metadata": {"charges": [
        {"at": "2026-10-01T00:00:00Z", "op": "x_create", "micros": 1},                 # exactly the start: in
        {"at": "2026-09-30T23:59:59.999999+00:00", "op": "x_create", "micros": 10},    # a microsecond before: out
        {"at": "2026-10-03T23:59:59.999999+00:00", "op": "x_create", "micros": 100},   # a microsecond before the end: in
        {"at": "2026-10-04T00:00:00+00:00", "op": "x_create", "micros": 1000},         # exactly the end: out
        {"at": "2026-10-03T20:00:00-04:00", "op": "x_create", "micros": 10000},        # the end, New York time: out
        {"at": "2026-09-30T20:00:00-04:00", "op": "x_create", "micros": 100000},       # the start, New York time: in
    ]}}
    within = mrs.charges_between(edges, _WIN_START, _WIN_END)
    assert within == mrs.ChargeWindow(micros=100_101)
    # [t, t) is empty — not even an entry exactly at t
    assert mrs.charges_between(edges, _WIN_START, _WIN_START) == mrs.ChargeWindow()
    # an inverted window holds no dated entry (nor an unreadable one); the undated ones are still
    # reported, never summed
    assert mrs.charges_between(edges, _WIN_END, _WIN_START) == mrs.ChargeWindow()
    assert mrs.charges_between(messy, _WIN_END, _WIN_START) == mrs.ChargeWindow(0, 2, 0)
    # adjacent half-open windows partition the dated entries: each one lands in exactly one week
    before = mrs.charges_between(edges, datetime(2026, 9, 28, tzinfo=timezone.utc), _WIN_START)
    after = mrs.charges_between(edges, _WIN_END, datetime(2026, 10, 7, tzinfo=timezone.utc))
    assert (before.micros, after.micros) == (10, 11_000)
    assert before.micros + within.micros + after.micros == 111_111

    # the result is a frozen (micros, undated, unreadable) triple, all zero by default
    w = mrs.ChargeWindow(1, 2, 3)
    assert (w.micros, w.undated, w.unreadable) == (1, 2, 3)
    assert mrs.ChargeWindow() == mrs.ChargeWindow(0, 0, 0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        w.micros = 5  # type: ignore[misc]


def test_charges_between_reads_an_amount_beyond_a_thousand_dollars_as_unreadable():
    """One journal entry beyond ±$1,000 is a hand edit, not a charge: the cost line says "unreadable"
    for that week instead of printing it. Only inside the window, and never for an undated entry."""
    assert mrs.CHARGE_MICROS_BOUND == 10**9
    day2, day3 = datetime(2026, 10, 2, tzinfo=timezone.utc), datetime(2026, 10, 3, tzinfo=timezone.utc)
    journal = {"metadata": {"charges": [
        {"at": "2026-10-02T00:00:00Z", "op": "x_create", "micros": 10**9},             # $1,000 exactly: readable
        {"at": "2026-10-02T00:00:00Z", "op": "x_create", "micros": 5},
        {"at": "2026-10-02T00:00:00Z", "op": "x_create", "micros": 10**9 + 1},         # one micro over: unreadable
        {"at": "2026-10-02T00:00:00Z", "op": "x_create", "micros": -(10**9 + 1)},
        {"at": "2026-10-02T00:00:00Z", "op": "x_create", "micros": str(10**9 + 1)},    # text, by the one rule
        {"at": "2026-10-02T00:00:00Z", "op": "x_create", "micros": "9" * 5000},        # hostile text: no crash
        {"at": "2026-10-02T00:00:00Z", "op": "x_create", "micros": 1e300},             # a finite float far past it
        {"at": "2026-10-03T00:00:00Z", "op": "x_create_refund", "micros": -10**9},     # -$1,000 exactly: readable
        {"at": "2026-10-10T00:00:00Z", "op": "x_create", "micros": 10**12},            # outside: ignored
        {"op": "x_create", "micros": 10**12},                                          # undated, whatever the amount
    ]}}
    assert mrs.charges_between(journal, day2, day3) == mrs.ChargeWindow(10**9 + 5, 1, 5)
    assert mrs.charges_between(journal, day3, _WIN_END) == mrs.ChargeWindow(-10**9, 1, 0)


def test_charges_between_never_raises_on_the_first_or_last_representable_instant():
    extremes = {"metadata": {"charges": [
        {"at": "0001-01-01T00:00:00+00:00", "op": "x_create", "micros": 1},
        {"at": "0001-01-01T00:00:00+05:00", "op": "x_create", "micros": 2},           # before year 1 in UTC
        {"at": "9999-12-31T23:59:59.999999+00:00", "op": "x_create", "micros": 4},    # the last instant: end-exclusive
        {"at": "9999-12-31T23:59:59-05:00", "op": "x_create", "micros": 8},           # after year 9999 in UTC
    ]}}
    assert mrs.charges_between(extremes, _WIN_START, _WIN_END) == mrs.ChargeWindow()
    widest = (datetime.min.replace(tzinfo=timezone.utc), datetime.max.replace(tzinfo=timezone.utc))
    assert mrs.charges_between(extremes, *widest) == mrs.ChargeWindow(1)
    assert mrs.charges_between(extremes, datetime.min, datetime.max) == mrs.ChargeWindow(1)      # naive = UTC


@pytest.mark.parametrize("post", [
    {}, {"metadata": None}, {"metadata": "junk"}, {"metadata": ["a"]}, {"metadata": {}},
    {"metadata": {"charges": None}}, {"metadata": {"charges": []}}, {"metadata": {"charges": "junk"}},
    {"metadata": {"charges": 5}}, {"metadata": {"charges": {"at": _AT, "micros": 1}}},
    {"metadata": {"charges": ["garbage", 42, None, ["x"]]}},
])
def test_charges_between_without_a_journal_is_an_empty_window(post):
    assert mrs.charges_between(post, _WIN_START, _WIN_END) == mrs.ChargeWindow() == mrs.ChargeWindow(0, 0, 0)


@pytest.mark.asyncio
async def test_the_cap_still_counts_an_out_of_range_or_undated_amount(svc):
    """The ±$1,000 bound is the cost line's alone. The X cap keeps counting every amount it can read,
    and every undated one — an over-count can only pause X early (fail-closed)."""
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    journal = [{"at": "2026-10-02T00:00:00+00:00", "op": "x_create", "micros": 2 * 10**9},
               {"op": "x_metrics_read", "micros": 7}]
    post = {"metadata": {"charges": journal}}
    assert mrs.charges_since(post, since) == 2_000_000_007
    assert mrs.charges_by_op_since(post, since) == {"x_create": 2_000_000_000, "x_metrics_read": 7}
    # the cost line reads the same journal as one unreadable and one undated entry — never as a number
    assert mrs.charges_between(post, since, since + timedelta(days=7)) == mrs.ChargeWindow(0, 1, 1)
    # and the cap's own reads through the ledger
    _p5_post(svc, status="published", updated_at="2026-10-02T00:00:00+00:00",
             metadata={"charges": copy.deepcopy(journal)})
    assert await svc.spend_since("x", since) == 2_000_000_007
    assert await svc.spend_by_op_since("x", since) == {"x_create": 2_000_000_000, "x_metrics_read": 7}


# ── list_charge_rows_since / script_tokens_between ───────────────────────────


def _spy_selects(monkeypatch, svc, table) -> List[tuple]:
    """Record the column list of every SELECT on `table`. The fake returns every column whatever the
    select names, so without this a read that stopped asking for a column it needs would pass."""
    t = svc.fake.tables[table]
    real = t.select
    calls: List[tuple] = []

    def spy(*cols):
        calls.append(cols)
        return real(*cols)

    monkeypatch.setattr(t, "select", spy)
    return calls


def _counting_reads(monkeypatch) -> List[str]:
    """The op of every ledger round trip from now on (each one goes through `_exec`)."""
    reads: List[str] = []
    real_exec = mrs._exec

    async def counting(query, *, op, **ids):
        reads.append(op)
        return await real_exec(query, op=op, **ids)

    monkeypatch.setattr(mrs, "_exec", counting)
    return reads


@pytest.mark.asyncio
async def test_list_charge_rows_since_reads_one_platform_touched_since_the_instant(svc, monkeypatch):
    since = _PREV_WEEK_START
    assert await svc.list_charge_rows_since("x", since) == []                     # an empty ledger
    late = _p5_post(svc, status="published", updated_at="2026-10-04T12:00:00+00:00",
                    metadata={"charges": _charges(("2026-10-04T12:00:00+00:00", 15000))})
    at_since = _p5_post(svc, status="published", updated_at="2026-09-21T04:00:00+00:00",   # the boundary: in
                        metadata={"charges": _charges(("2026-09-21T04:00:00+00:00", 15000))})
    quiet = _p5_post(svc, status="failed", updated_at="2026-09-29T09:00:00+00:00", metadata=None)
    # touched before `since`: not read, even carrying an entry dated after it (impossible in production —
    # every journal write bumps updated_at — but it proves the read is bounded by the touch)
    _p5_post(svc, status="published", updated_at="2026-09-21T03:59:59.999999+00:00",
             metadata={"charges": _charges(("2026-09-25T00:00:00+00:00", 777))})
    blue = _p5_post(svc, platform="bluesky", status="published", updated_at="2026-09-30T00:00:00+00:00",
                    metadata={"charges": _charges(("2026-09-30T00:00:00+00:00", 99))})
    selects = _spy_selects(monkeypatch, svc, mrs.POSTS)
    rows = await svc.list_charge_rows_since("x", since)
    assert [r["id"] for r in rows] == [at_since["id"], quiet["id"], late["id"]]         # oldest touch first
    assert rows[0]["metadata"] == at_since["metadata"] and rows[1]["metadata"] is None  # raw rows
    assert selects == [("id,metadata,updated_at",)]                                     # the journal is asked for
    # a naive since is UTC; the same instant on the New York clock, or as text, is the same read
    for same in (datetime(2026, 9, 21, 4, 0), datetime(2026, 9, 21, 0, 0, tzinfo=mrs.ET),
                 "2026-09-21T00:00:00-04:00", "2026-09-21T04:00:00Z"):
        assert [r["id"] for r in await svc.list_charge_rows_since("x", same)] == [r["id"] for r in rows]
    assert [r["id"] for r in await svc.list_charge_rows_since("bluesky", since)] == [blue["id"]]
    assert await svc.list_charge_rows_since("threads", since) == []


@pytest.mark.parametrize("bad", [date(2026, 10, 1), "2026-10-01", None, 1759276800, "2026-10-01Tlate"])
@pytest.mark.asyncio
async def test_list_charge_rows_since_refuses_a_since_that_is_not_an_instant(svc, monkeypatch, bad):
    reads = _counting_reads(monkeypatch)
    with pytest.raises(ValueError):
        await svc.list_charge_rows_since("x", bad)
    assert reads == []                                                              # before any read


@pytest.mark.asyncio
async def test_list_charge_rows_since_raises_when_the_read_fails_never_a_silent_empty(svc, monkeypatch):
    async def unavailable(query):
        raise RuntimeError("503 from PostgREST")

    monkeypatch.setattr(mrs, "sb_exec", unavailable)
    with pytest.raises(mrs.MarketingRunError, match="list_charge_rows_since") as ei:
        await svc.list_charge_rows_since("x", _PREV_WEEK_START)
    assert "platform=x" in str(ei.value) and "RuntimeError: 503 from PostgREST" in str(ei.value)


@pytest.mark.asyncio
async def test_list_charge_rows_since_refuses_a_partial_read(svc, monkeypatch):
    since = _PREV_WEEK_START
    # Another platform's rows and rows untouched since `since` are filtered IN the query, so they never
    # count toward the limit — and they are the oldest rows, the first an unfiltered read would see.
    for i in range(3):
        _p5_post(svc, status="published", updated_at=f"2026-09-20T00:00:0{i}+00:00")
        _p5_post(svc, platform="bluesky", status="published", updated_at=f"2026-09-22T00:00:0{i}+00:00")
    x_rows = [_p5_post(svc, status="published", updated_at=f"2026-09-2{3 + i}T00:00:00+00:00") for i in range(3)]
    with pytest.raises(mrs.MarketingRunError, match="partial") as ei:
        await svc.list_charge_rows_since("x", since, limit=2)
    assert str(ei.value) == ("list_charge_rows_since: more than 2 x posts touched since "
                             "2026-09-21T04:00:00.000000Z — the cost line is never a partial sum")
    # exactly the limit is a complete read (the probe asks for limit + 1)
    assert [r["id"] for r in await svc.list_charge_rows_since("x", since, limit=3)] == [r["id"] for r in x_rows]
    svc.fake.tables[mrs.POSTS].rows.remove(x_rows[-1])
    assert [r["id"] for r in await svc.list_charge_rows_since("x", since, limit=2)] == [r["id"] for r in x_rows[:2]]
    # a limit that could read nothing, or that is not an int, is refused before any read; keyword-only
    reads = _counting_reads(monkeypatch)
    for bad in (0, -1, True, 2.0, "2"):
        with pytest.raises(ValueError):
            await svc.list_charge_rows_since("x", since, limit=bad)
    assert reads == []
    with pytest.raises(TypeError):
        await svc.list_charge_rows_since("x", since, 2)  # type: ignore[misc]


@pytest.mark.asyncio
async def test_list_charge_rows_since_reads_at_most_five_hundred_rows_by_default(svc):
    base = datetime(2026, 9, 22, tzinfo=timezone.utc)
    for i in range(500):
        _p5_post(svc, status="published", updated_at=(base + timedelta(seconds=i)).isoformat())
    assert len(await svc.list_charge_rows_since("x", _PREV_WEEK_START)) == 500
    _p5_post(svc, status="published", updated_at=(base + timedelta(seconds=500)).isoformat())
    with pytest.raises(mrs.MarketingRunError, match="more than 500 x posts"):
        await svc.list_charge_rows_since("x", _PREV_WEEK_START)


@pytest.mark.asyncio
async def test_list_charge_rows_since_probe_fires_under_postgrests_max_rows(svc, monkeypatch):
    """Production PostgREST cuts every answer at max-rows (≈1,000 here) whatever `.limit()` asks, so the
    fake here does too. Review 2026-10-07: at the old limit of 1,000 the probe asked for 1,001 rows, got
    1,000 and read as complete — a silent partial sum. Every limit whose probe fits still fires; a limit
    whose probe cannot fit is refused before any read."""
    real_execute = _Query.execute

    def capped(self):
        res = real_execute(self)
        if self.op == "select":
            res.data = res.data[:mrs.POSTGREST_MAX_ROWS]
        return res

    monkeypatch.setattr(_Query, "execute", capped)
    assert mrs.POSTGREST_MAX_ROWS == 1000
    base = datetime(2026, 9, 22, tzinfo=timezone.utc)
    for i in range(1200):
        _p5_post(svc, status="published", updated_at=(base + timedelta(seconds=i)).isoformat())
    for limit, shown in ((None, 500), (999, 999), (1, 1)):
        kw = {} if limit is None else {"limit": limit}
        with pytest.raises(mrs.MarketingRunError, match=f"more than {shown} x posts"):
            await svc.list_charge_rows_since("x", _PREV_WEEK_START, **kw)
    reads = _counting_reads(monkeypatch)
    for too_big in (1000, 5000):
        with pytest.raises(ValueError, match="does not fit in one PostgREST response"):
            await svc.list_charge_rows_since("x", _PREV_WEEK_START, limit=too_big)
    assert reads == []


def _script_row(svc, run_date: str, tokens: Any, **extra) -> Dict[str, Any]:
    """A `marketing_scripts` row seeded straight into the fake — what the cost line reads back."""
    row = {"run_id": str(uuid.uuid4()), "run_date": run_date, "status": "accepted", "tokens_used": tokens,
           "source_ref": "money_moves:test-item", "template_id": "checklist", "generations": 2,
           "output": {"posts": {"x": _server_copy("x")}, "judge": {"mode": "enforce"}},
           "fact_sheet": {"facts": ["a fact"]}, **extra}
    svc.fake.tables[mrs.SCRIPTS].rows.append(row)
    return row


@pytest.mark.asyncio
async def test_script_tokens_between_is_inclusive_projected_and_ordered(svc, monkeypatch):
    assert await svc.script_tokens_between(date(2026, 9, 28), date(2026, 10, 4)) == []    # an empty table
    for day, tokens in [("2026-10-04", 22_100), ("2026-09-27", 60_000), ("2026-10-01", None),
                        ("2026-09-28", 21_000), ("2026-10-05", 5), ("2026-09-30", "12")]:
        _script_row(svc, day, tokens)                                                      # stored out of order
    selects = _spy_selects(monkeypatch, svc, mrs.SCRIPTS)
    got = await svc.script_tokens_between(date(2026, 9, 28), date(2026, 10, 4))
    # inclusive at both ends, oldest first, two keys only, RAW values (the digest validates each count:
    # a junk one must reach it as junk and read "unreadable", never be coerced to 0 here)
    assert got == [{"run_date": "2026-09-28", "tokens_used": 21_000},
                   {"run_date": "2026-09-30", "tokens_used": "12"},
                   {"run_date": "2026-10-01", "tokens_used": None},
                   {"run_date": "2026-10-04", "tokens_used": 22_100}]
    assert selects == [("run_date,tokens_used",)]
    assert await svc.script_tokens_between("2026-09-28", "2026-10-04") == got             # ISO strings
    assert await svc.script_tokens_between(date(2026, 10, 5), date(2026, 10, 5)) == [
        {"run_date": "2026-10-05", "tokens_used": 5}]                                     # a one-day window
    # an inverted window, and a datetime at either end, read nothing at all
    reads = _counting_reads(monkeypatch)
    assert await svc.script_tokens_between(date(2026, 10, 4), date(2026, 9, 28)) == []
    for bad in (datetime(2026, 9, 28, tzinfo=timezone.utc), datetime(2026, 9, 28)):
        with pytest.raises(ValueError):
            await svc.script_tokens_between(bad, date(2026, 10, 4))
        with pytest.raises(ValueError):
            await svc.script_tokens_between(date(2026, 9, 28), bad)
    assert reads == []


@pytest.mark.asyncio
async def test_script_tokens_between_refuses_more_scripts_than_days(svc):
    """One script per run (PK run_id) and one run per day (UNIQUE run_date): more rows than days means
    a day holds two, and a sum could count one twice — the read raises instead of returning them."""
    for day in ("2026-10-01", "2026-10-02", "2026-10-03"):
        _script_row(svc, day, 1000)
    assert len(await svc.script_tokens_between(date(2026, 10, 1), date(2026, 10, 3))) == 3   # one a day
    _script_row(svc, "2026-10-02", 1000)                       # a second script on one day (a hand edit)
    with pytest.raises(mrs.MarketingRunError,
                       match=r"script_tokens_between: 4 scripts for the 3 days 2026-10-01\.\.2026-10-03"):
        await svc.script_tokens_between(date(2026, 10, 1), date(2026, 10, 3))
    with pytest.raises(mrs.MarketingRunError, match="script_tokens_between"):
        await svc.script_tokens_between(date(2026, 10, 2), date(2026, 10, 2))
    _script_row(svc, "2026-10-03", 1000)                       # past the probe: still refused
    with pytest.raises(mrs.MarketingRunError, match="script_tokens_between"):
        await svc.script_tokens_between(date(2026, 10, 1), date(2026, 10, 3))


@pytest.mark.asyncio
async def test_script_tokens_between_refuses_a_repeated_day_in_a_sparse_window_too(svc):
    """The digest reads 14 days that hold about 8 scripts, so "more rows than days" alone would let a
    second script on one day through and the Gemini part would sum it twice. Two rows on one day are
    refused whatever the window's size (found by the 2026-10-05 test writers)."""
    for day in ("2026-09-22", "2026-09-29", "2026-10-01", "2026-10-03"):
        _script_row(svc, day, 1000)
    assert len(await svc.script_tokens_between(date(2026, 9, 21), date(2026, 10, 4))) == 4
    _script_row(svc, "2026-10-01", 2000)                       # a second script on one day (a hand edit)
    with pytest.raises(mrs.MarketingRunError, match=r"5 scripts for the 14 days .* a day holds two"):
        await svc.script_tokens_between(date(2026, 9, 21), date(2026, 10, 4))
    # a window that does not include the repeated day is unaffected
    assert len(await svc.script_tokens_between(date(2026, 9, 21), date(2026, 9, 30))) == 2


@pytest.mark.asyncio
async def test_script_tokens_between_raises_when_the_read_fails_never_a_silent_empty(svc, monkeypatch):
    async def unavailable(query):
        raise RuntimeError("503 from PostgREST")

    monkeypatch.setattr(mrs, "sb_exec", unavailable)
    with pytest.raises(mrs.MarketingRunError, match="script_tokens_between"):
        await svc.script_tokens_between(date(2026, 9, 21), date(2026, 10, 4))


# ══ create_posts dispatches on the SCRIPT's content class (2026-10-05 C5; drop 2 D12, 2026-10-09) ═════
#
# The class is `selection.content_class_of(script.template_id)` — a lesson template → "A" (the judge
# gate), a news series → "C"/"F" (the template gate) — and the accepted output must say the same.
# `marketing_runs.content_class` is a MIRROR: a mismatch is a WARNING, never obeyed. Anything else (an
# unknown / retired / mis-cased id, a NULL, an output that contradicts its id) is the 422
# MarketingRequestInvalid, AFTER the hold and accepted-script checks and BEFORE any asset read or INSERT.
# (Before drop 2 the gate read the run's mirror, and every non-"A" mirror was refused.)

_UNGATED_TEMPLATE_IDS = [None, "", "x", "CHECKLIST", " checklist", "checklist ", "lesson", "news:ceo_buys",
                         "ceo-buys", 1, True, ["checklist"], "class-C-template-" * 6]


async def _class_run(svc, template_id: Any, *, mirror: Any = "A", output_extra: Optional[Dict[str, Any]] = None):
    """A real (non-dry-run) run with an ACCEPTED script judged in `enforce` mode and a ready video —
    everything a class-A run needs to become posts — whose script's template id (and optionally its
    run's mirror and its output) is then hand-edited."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    rid = row["id"]
    await _accept_script(svc, rid, ["x", "tiktok"])
    video = await _ready_asset(svc, rid)
    script = next(r for r in svc.fake.tables[mrs.SCRIPTS].rows if r["run_id"] == rid)
    script["template_id"] = template_id
    script["output"].update(output_extra or {})
    _stored(svc, rid)["content_class"] = mirror
    specs = [{"platform": "x", "format": "text"},
             {"platform": "tiktok", "format": "video", "asset_ids": [video["id"]]}]
    return rid, specs


@pytest.mark.parametrize("template_id", _UNGATED_TEMPLATE_IDS, ids=lambda v: repr(v)[:24])
@pytest.mark.asyncio
async def test_create_posts_refuses_a_script_whose_template_has_no_class_and_writes_nothing(
        svc, monkeypatch, caplog, template_id):
    import logging

    from app.api.error_response import ErrorCode, classify_exception

    rid, specs = await _class_run(svc, template_id)

    async def no_asset_read(run_id):
        raise AssertionError("the class refusal must come before any asset read")

    monkeypatch.setattr(svc, "list_assets", no_asset_read)
    before = copy.deepcopy({name: t.rows for name, t in svc.fake.tables.items()})
    with caplog.at_level(logging.ERROR, logger=mrs.logger.name):
        with pytest.raises(mrs.MarketingRequestInvalid) as ei:
            await svc.create_posts(rid, specs, claim=_holder(svc, rid))
        with pytest.raises(mrs.MarketingRequestInvalid):          # a text-only request, no asset read either way
            await svc.create_posts(rid, specs[:1], claim=_holder(svc, rid))
    shown = str(template_id)[:40]
    assert type(ei.value) is mrs.MarketingRequestInvalid
    assert str(ei.value) == (f"run {rid}: the script's template {shown!r} (class None, output class None) has "
                             "no post gate; nothing recorded")
    assert classify_exception(ei.value) == (ErrorCode.MARKETING_REQUEST_INVALID, 422)
    assert svc.fake.tables[mrs.POSTS].rows == []
    assert {name: t.rows for name, t in svc.fake.tables.items()} == before        # nothing written anywhere
    refused = [r for r in caplog.records if r.levelno == logging.ERROR and "REFUSED" in r.getMessage()]
    assert len(refused) == 2
    assert all(rid in r.getMessage() and f"template {shown!r}" in r.getMessage() for r in refused)


@pytest.mark.parametrize("template_id, output_class", [
    ("checklist", "C"), ("checklist", "F"), ("checklist", "a"), ("checklist", "B"), ("checklist", ["A"]),
    ("ceo_buys", None), ("ceo_buys", "A"), ("ceo_buys", "F"), ("money_map", "C"), ("thirteen_f", " C"),
])
@pytest.mark.asyncio
async def test_an_output_whose_class_contradicts_its_template_is_refused(svc, monkeypatch, template_id,
                                                                          output_class):
    """The output's own class must be the one its template id decides: a lesson package claiming "C",
    or a template package with no class / another class, is a hand edit — 422, nothing written, and
    never the judge's 409 or the template's re-check (both would name the wrong cause)."""
    rid, specs = await _class_run(svc, template_id, output_extra={"content_class": output_class})

    async def no_asset_read(run_id):
        raise AssertionError("the class refusal must come before any asset read")

    monkeypatch.setattr(svc, "list_assets", no_asset_read)
    with pytest.raises(mrs.MarketingRequestInvalid) as ei:
        await svc.create_posts(rid, specs, claim=_holder(svc, rid))
    assert type(ei.value) is mrs.MarketingRequestInvalid and "has no post gate" in str(ei.value)
    assert svc.fake.tables[mrs.POSTS].rows == []


@pytest.mark.parametrize("mirror", ["C", "F", None, "", "a", "B", 1])
@pytest.mark.asyncio
async def test_the_runs_mirror_never_gates_a_lesson_it_is_only_a_warning(svc, caplog, mirror):
    """The run's `content_class` is the mirror `_heal_mirror` writes: a lesson script whose run says "C"
    (or anything else) still records its posts — the SCRIPT decides — and the disagreement is logged."""
    import logging

    rid, specs = await _class_run(svc, "checklist", mirror=mirror)
    with caplog.at_level(logging.WARNING, logger=mrs.logger.name):
        posts = await svc.create_posts(rid, specs, claim=_holder(svc, rid))
    assert [(p["platform"], p["format"], p["status"]) for p in posts] == [
        ("x", "text", "pending_review"), ("tiktok", "video", "pending_review")]
    warned = [r for r in caplog.records if r.levelno == logging.WARNING and "the script decides" in r.getMessage()]
    assert len(warned) == 1 and rid in warned[0].getMessage()


@pytest.mark.asyncio
async def test_class_a_judged_in_enforce_mode_still_becomes_posts(svc, caplog):
    """The control for the refusals above: a lesson script, its run mirroring "A", records its posts
    with no warning — and each post carries what it is and its AI flag (drop 2)."""
    import logging

    rid, specs = await _class_run(svc, "checklist")
    with caplog.at_level(logging.WARNING, logger=mrs.logger.name):
        posts = await svc.create_posts(rid, specs, claim=_holder(svc, rid))
    assert [(p["platform"], p["format"], p["status"]) for p in posts] == [
        ("x", "text", "pending_review"), ("tiktok", "video", "pending_review")]
    assert len(svc.fake.tables[mrs.POSTS].rows) == 2
    assert not [r for r in caplog.records if "REFUSED" in r.getMessage() or "the script decides" in r.getMessage()]
    for p in posts:
        md = p["metadata"]
        assert (md["content_class"], md["series"], md["authorship"], md["series_trail"], md["made_with_ai"]) == (
            "A", "lesson", "ai", [], True)


@pytest.mark.asyncio
async def test_a_lesson_output_claiming_template_authorship_is_refused(svc):
    rid, specs = await _class_run(svc, "checklist", output_extra={"authorship": "template"})
    with pytest.raises(mrs.MarketingRequestInvalid, match="claims authorship 'template'"):
        await svc.create_posts(rid, specs, claim=_holder(svc, rid))
    assert svc.fake.tables[mrs.POSTS].rows == []


@pytest.mark.parametrize("dry_run", [False, True], ids=["live", "rehearsal"])
@pytest.mark.parametrize("judge", [{"mode": "enforce"}, {"mode": "shadow"}, {"mode": "off"}, None])
@pytest.mark.asyncio
async def test_a_template_script_is_never_judged_its_recheck_decides(svc, monkeypatch, judge, dry_run):
    """The judge decides only inside the class-A branch. A class-C script whose output is not what the
    template composes from its fact sheet is the 409 MarketingTemplateRefused (the worker skips the day
    `template_refused`), never the judge refusal — whose hint would tell the owner to set
    MARKETING_JUDGE_MODE=enforce, false while the judge is enforcing. A rehearsal is refused the same
    way: the class dispatch has no dry-run branch."""
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=dry_run, now=NOW, claim_nonce=_n())
    output: Dict[str, Any] = {"posts": {"x": _server_copy("x")}, "content_class": "C", "authorship": "template",
                              "series": "ceo_buys"}
    if judge is not None:
        output["judge"] = judge
    await svc.insert_script({"run_id": row["id"], "status": "accepted", "template_id": "ceo_buys",
                             "source_ref": "news:ceo_buys:2026-09-07", "fact_sheet": {}, "output": output})
    with pytest.raises(mrs.MarketingTemplateRefused) as ei:
        await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, row["id"]))
    assert type(ei.value) is mrs.MarketingTemplateRefused and "failed its re-check" in str(ei.value)
    assert svc.fake.tables[mrs.POSTS].rows == []


@pytest.mark.parametrize("classes", ["A", "", "A,F", "a, f", "C", "X"])
@pytest.mark.asyncio
async def test_a_template_whose_class_is_switched_off_is_refused_before_its_recheck(svc, monkeypatch, caplog,
                                                                                    classes):
    """Rollback (contract D17): switching MARKETING_CONTENT_CLASSES back to "A" refuses the day's
    accepted template at create_posts (409 → skipped `template_refused`), before any re-check or read."""
    import logging

    from app.api.error_response import ErrorCode, classify_exception

    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", classes)
    expect_off = "C" not in mrs.parse_content_classes(classes)

    def recheck(*_a, **_k):
        raise AssertionError("a switched-off class is refused before its re-check")

    monkeypatch.setattr(mrs.news_templates, "revalidate", recheck if expect_off else (lambda *_a, **_k: []))
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await svc.insert_script({"run_id": row["id"], "status": "accepted", "template_id": "ceo_buys",
                             "source_ref": "news:ceo_buys:2026-09-07", "fact_sheet": {},
                             "output": {"posts": {"x": _server_copy("x")}, "content_class": "C",
                                        "authorship": "template", "series": "ceo_buys"}})
    spec = [{"platform": "x", "format": "text"}]
    with caplog.at_level(logging.ERROR, logger=mrs.logger.name):
        if expect_off:
            with pytest.raises(mrs.MarketingTemplateRefused, match="switched off") as ei:
                await svc.create_posts(row["id"], spec, claim=_holder(svc, row["id"]))
            assert classify_exception(ei.value) == (ErrorCode.MARKETING_TEMPLATE_REFUSED, 409)
            assert any("TEMPLATE REFUSED" in r.getMessage() and row["id"] in r.getMessage() for r in caplog.records)
            assert svc.fake.tables[mrs.POSTS].rows == []
        else:
            (post,) = await svc.create_posts(row["id"], spec, claim=_holder(svc, row["id"]))
            assert post["status"] == "pending_review"


_NEWS_SERIES_DEFAULT = "ceo_buys,insider_buys,thirteen_f,money_map"      # Settings' default (the 2a four)


@pytest.mark.parametrize("series, raw, withdrawn, expect_on", [
    ("congress_count", _NEWS_SERIES_DEFAULT, (), False),     # shipped (2b) but off by default
    ("company_stakes", _NEWS_SERIES_DEFAULT, (), False),
    ("earnings", _NEWS_SERIES_DEFAULT, (), False),
    ("theme_explainer", _NEWS_SERIES_DEFAULT, (), False),
    ("congress_count", "congress_count", (), True),
    ("theme_explainer", " THEME_EXPLAINER ,money_map", (), True),
    ("ceo_buys", _NEWS_SERIES_DEFAULT, (), True),
    ("ceo_buys", "insider_buys,money_map", (), False),       # a 2a series switched off
    ("money_map", "", (), False),
    ("earnings", "earnings", ("earnings",), False),          # listed, but this deploy no longer ships it
])
@pytest.mark.asyncio
async def test_a_template_whose_series_is_switched_off_is_refused_before_its_recheck(
        svc, monkeypatch, caplog, series, raw, withdrawn, expect_on):
    """Drop 2b: the per-series switch is read at create_posts too, like the class switch. A template whose
    series MARKETING_NEWS_SERIES no longer lists — or this deploy no longer ships — is refused 409
    MARKETING_TEMPLATE_REFUSED (the worker skips the day `template_refused`), before its re-check and any
    asset read; nothing is recorded. Listed and shipped, the same script records its post."""
    import logging

    from app.api.error_response import ErrorCode, classify_exception
    from app.services.marketing import selection

    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", raw)
    if withdrawn:
        monkeypatch.setattr(selection, "SHIPPED_SERIES", selection.SHIPPED_SERIES - set(withdrawn))

    def recheck(*_a, **_k):
        raise AssertionError("a switched-off series is refused before its re-check")

    monkeypatch.setattr(mrs.news_templates, "revalidate", (lambda *_a, **_k: []) if expect_on else recheck)
    klass = selection.content_class_of(series)
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await svc.insert_script({"run_id": row["id"], "status": "accepted", "template_id": series,
                             "source_ref": f"news:{series}:x", "fact_sheet": {},
                             "output": {"posts": {"x": _server_copy("x")}, "content_class": klass,
                                        "authorship": "template", "series": series}})

    async def no_asset_read(run_id):
        raise AssertionError("the series refusal comes before any asset read")

    monkeypatch.setattr(svc, "list_assets", no_asset_read)
    spec = [{"platform": "x", "format": "text"}]
    with caplog.at_level(logging.ERROR, logger=mrs.logger.name):
        if not expect_on:
            with pytest.raises(mrs.MarketingTemplateRefused, match="switched off") as ei:
                await svc.create_posts(row["id"], spec, claim=_holder(svc, row["id"]))
            assert classify_exception(ei.value) == (ErrorCode.MARKETING_TEMPLATE_REFUSED, 409)
            assert series in str(ei.value) and "MARKETING_NEWS_SERIES" in str(ei.value)
            assert any("TEMPLATE REFUSED" in r.getMessage() and row["id"] in r.getMessage()
                       and series in r.getMessage() for r in caplog.records)
            assert svc.fake.tables[mrs.POSTS].rows == []
        else:
            (post,) = await svc.create_posts(row["id"], spec, claim=_holder(svc, row["id"]))
            assert post["status"] == "pending_review" and post["metadata"]["series"] == series
            assert not [r for r in caplog.records if "REFUSED" in r.getMessage()]


@pytest.mark.asyncio
async def test_the_class_switch_is_checked_before_the_series_switch(svc, monkeypatch):
    """Both switches off: the refusal names the CLASS (the coarser switch, the documented rollback)."""
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A")
    monkeypatch.setattr(mrs.settings, "MARKETING_NEWS_SERIES", "")
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await svc.insert_script({"run_id": row["id"], "status": "accepted", "template_id": "congress_count",
                             "source_ref": "news:congress_count:2026-08", "fact_sheet": {},
                             "output": {"posts": {"x": _server_copy("x")}, "content_class": "C",
                                        "authorship": "template", "series": "congress_count"}})
    with pytest.raises(mrs.MarketingTemplateRefused, match="MARKETING_CONTENT_CLASSES"):
        await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, row["id"]))
    assert svc.fake.tables[mrs.POSTS].rows == []


@pytest.mark.parametrize("authorship", [None, "ai", "Template", "templates", 1])
@pytest.mark.asyncio
async def test_a_template_script_without_template_authorship_is_a_422(svc, monkeypatch, authorship):
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    monkeypatch.setattr(mrs.news_templates, "revalidate", lambda *_a, **_k: [])
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await svc.insert_script({"run_id": row["id"], "status": "accepted", "template_id": "money_map",
                             "output": {"posts": {"x": _server_copy("x")}, "content_class": "F",
                                        "authorship": authorship, "series": "money_map"}})
    with pytest.raises(mrs.MarketingRequestInvalid, match="must carry template authorship"):
        await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, row["id"]))
    assert svc.fake.tables[mrs.POSTS].rows == []


@pytest.mark.asyncio
async def test_a_template_whose_output_names_another_series_is_refused(svc, monkeypatch):
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    monkeypatch.setattr(mrs.news_templates, "revalidate", lambda *_a, **_k: [])     # the series check alone
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await svc.insert_script({"run_id": row["id"], "status": "accepted", "template_id": "insider_buys",
                             "output": {"posts": {"x": _server_copy("x")}, "content_class": "C",
                                        "authorship": "template", "series": "ceo_buys"}})
    with pytest.raises(mrs.MarketingTemplateRefused, match="series_mismatch"):
        await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, row["id"]))
    assert svc.fake.tables[mrs.POSTS].rows == []


@pytest.mark.parametrize("auto", [True, False])
@pytest.mark.asyncio
async def test_a_template_post_is_never_auto_approved_and_carries_its_metadata(svc, monkeypatch, auto):
    """MARKETING_AUTO_PUBLISH approves a media-less TEXT post of a JUDGED (class A) script only. A
    template (C/F) text post stays pending_review: a human approves every one. Its metadata records the
    class, the series, the authorship, the selection's trail (≤ 8) and `made_with_ai` False (a fixed
    template wrote it; only a narrated template video is made with AI)."""
    monkeypatch.setattr(mrs.settings, "MARKETING_CONTENT_CLASSES", "A,C,F")
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", auto)
    monkeypatch.setattr(mrs.news_templates, "revalidate", lambda *_a, **_k: [])
    trail = [{"series": f"s{i}", "outcome": "no_candidates", "reason": "r"} for i in range(11)]
    trail.insert(1, "junk")
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await svc.insert_script({"run_id": row["id"], "status": "accepted", "template_id": "money_map",
                             "source_ref": "news:money_map:COST:2025",
                             "fact_sheet": {"selection": {"plan": "thursday_off", "chain": ["money_map", "lesson"],
                                                          "trail": trail}},
                             "output": {"posts": {"x": _server_copy("x")}, "content_class": "F",
                                        "authorship": "template", "series": "money_map"}})
    (post,) = await svc.create_posts(row["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, row["id"]))
    assert post["status"] == "pending_review" and post["approved_by"] is None
    md = post["metadata"]
    assert (md["content_class"], md["series"], md["authorship"], md["made_with_ai"]) == (
        "F", "money_map", "template", False)
    assert md["series_trail"] == trail[:1] + trail[2:9] and len(md["series_trail"]) == mrs.SERIES_TRAIL_MAX
    # the control: the same switch DOES approve a class-A text post
    real, _ = await svc.claim_run(date(2026, 9, 18), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await _accept_script(svc, real["id"], ["x"])
    (lesson,) = await svc.create_posts(real["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, real["id"]))
    assert lesson["status"] == ("approved" if auto else "pending_review")


@pytest.mark.asyncio
async def test_the_class_refusal_comes_after_the_hold_and_the_script(svc, caplog):
    import logging

    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    rid = row["id"]
    spec = [{"platform": "x", "format": "text"}]
    with caplog.at_level(logging.ERROR, logger=mrs.logger.name):
        # no accepted script yet: the script refusal (409 — the worker waits for the writer)
        with pytest.raises(mrs.MarketingScriptNotReady):
            await svc.create_posts(rid, spec, claim=_holder(svc, rid))
        await _accept_script(svc, rid, ["x"], template_id="retired_template")
        # a zombie whose claim was taken over: not held (409), whatever the class
        with pytest.raises(MarketingRunNotHeld):
            await svc.create_posts(rid, spec, claim=mrs.CallerClaim(1, "f" * 32))
        with pytest.raises(MarketingRunNotFound):
            await svc.create_posts(str(uuid.uuid4()), spec, claim=_holder(svc, rid))
        assert not [r for r in caplog.records if "REFUSED" in r.getMessage()]
        # held, with an accepted script: only now does the class decide
        with pytest.raises(mrs.MarketingRequestInvalid):
            await svc.create_posts(rid, spec, claim=_holder(svc, rid))
        # and once the day is closed, "not held" comes first again
        await svc.update_run(rid, status="skipped", finished=True)
        with pytest.raises(MarketingRunNotHeld):
            await svc.create_posts(rid, spec, claim=_holder(svc, rid))
    assert len([r for r in caplog.records if "REFUSED" in r.getMessage()]) == 1
    assert svc.fake.tables[mrs.POSTS].rows == []


def test_an_ungated_class_is_a_422_and_a_refused_template_a_409_never_a_retried_5xx():
    from app.api.error_response import ErrorCode, classify_exception

    assert classify_exception(mrs.MarketingRequestInvalid("run r: the script's template 'x' has no post gate")) == (
        ErrorCode.MARKETING_REQUEST_INVALID, 422)
    assert classify_exception(mrs.MarketingTemplateRefused("run r: failed its re-check (timeout, 503)")) == (
        ErrorCode.MARKETING_TEMPLATE_REFUSED, 409)
    assert ErrorCode.MARKETING_TEMPLATE_REFUSED.value == "MARKETING_TEMPLATE_REFUSED"


# ══ Drop 1 — image posts: the frozen formats, the post image's text, one post per platform ══════
#
# Contract C2/C6/C7 (2026-10-09). The accepted output freezes each platform's format at write time
# (`post_formats`, script_service); the worker renders ONE post image from `image_post` + the
# code-owned `image_footer` and declares what it drew; the server checks that text at registration,
# verifies the run's pointer to it, and records each platform in exactly its frozen format.

_IMAGE_POST = {"title": "Three habits that compound",
               "paragraphs": ["Small, steady saving adds up over the years.",
                              "Costs you avoid keep working for you."]}
_IMAGE_FOOTER = "Educational only · not investment advice · Written with AI assistance · Sep 17, 2026 · Caydex"
_TEXT_PLATFORMS = ("facebook", "linkedin", "x", "threads", "bluesky")


async def _accept_image_script(svc, run_id: str, formats: Dict[str, str], **output_extra) -> Dict[str, Any]:
    """An accepted script whose output froze `formats` (with copy for each platform), carrying the
    image post and its footer — what `script_service.freeze_post_formats` stores."""
    row = {
        "run_id": run_id, "status": "accepted", "source_ref": "money_moves:test-item",
        "template_id": "checklist", "generation_id": str(uuid.uuid4()),
        "output": {"posts": {p: _server_copy(p) for p in formats}, "judge": {"mode": "enforce"},
                   "cards": [dict(_CARD)], "disclaimer_card": _DISCLAIMER_CARD,
                   "post_formats": dict(formats), "image_post": copy.deepcopy(_IMAGE_POST),
                   "image_footer": _IMAGE_FOOTER, **output_extra},
    }
    created, ours = await svc.insert_script(row)
    assert ours
    return created


def _drawn() -> List[str]:
    return [_IMAGE_POST["title"], *_IMAGE_POST["paragraphs"], _IMAGE_FOOTER]


def _image_meta(**extra) -> Dict[str, Any]:
    return {"onscreen_text": _drawn(), "image_role": schemas.IMAGE_ROLE_POST, **extra}


async def _post_image(svc, run_id: str, sha: str = "c" * 64, *, metadata=None, size: int = 1,
                      point: bool = True) -> Dict[str, Any]:
    """The run's post image: registered (checked), uploaded, verified READY — and, with `point`,
    named by the run's `metadata.image_asset_id` the way the worker's `rendered` PATCH names it."""
    asset, _ = await svc.register_asset(run_id, kind="card", ext="jpg", sha256=sha, size_bytes=size,
                                        metadata=_image_meta() if metadata is None else metadata,
                                        claim=_holder(svc, run_id))
    svc.fake.objects.add(asset["storage_path"])
    ready = await svc.complete_asset(asset["id"], claim=_asset_holder(svc, asset["id"]))
    if point:
        await svc.update_run(run_id, metadata={"image_asset_id": ready["id"]}, worker=True,
                             claim=_holder(svc, run_id))
    return ready


async def _point_video(svc, run_id: str, video_id: str) -> None:
    """Name `video_id` as the run's video the way the worker's `rendered` PATCH does
    (`metadata.video_asset_id`) — the verified pointer every video post of a run must carry (F1)."""
    await svc.update_run(run_id, metadata={"video_asset_id": video_id}, worker=True, claim=_holder(svc, run_id))


async def _image_run(svc, formats: Optional[Dict[str, str]] = None, *, dry_run: bool = False):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=dry_run, now=NOW, claim_nonce=_n())
    formats = formats or {"x": "image", "bluesky": "image", "facebook": "text", "tiktok": "video"}
    await _accept_image_script(svc, row["id"], formats)
    return row["id"]


# ── C2: the format map ────────────────────────────────────────────────────────


def test_the_five_text_platforms_may_take_an_image_and_the_video_platforms_only_video():
    assert schemas.POST_FORMATS_BY_PLATFORM == {
        "tiktok": ("video",), "youtube": ("video",), "instagram": ("video",),
        "facebook": ("text", "image"), "linkedin": ("text", "image"), "x": ("text", "image"),
        "threads": ("text", "image"), "bluesky": ("text", "image"),
    }
    assert set(schemas.FROZEN_POST_FORMATS) == {"video", "image", "text"} <= set(schemas.POST_FORMATS)
    # An image post carries exactly a card (the post image) — never a video or a manifest.
    assert schemas.POST_MEDIA_KINDS["image"] == ("card",) and "image" in schemas.MEDIA_REQUIRED_FORMATS
    assert schemas.ASSET_KIND_EXTENSIONS["card"] == ("png", "jpg") and schemas.POST_IMAGE_EXT == "jpg"
    assert schemas.POST_IMAGE_MAX_BYTES == 950_000 and schemas.IMAGE_ROLES == ("post_image",)


# ── the image_post shape ──────────────────────────────────────────────────────


@pytest.mark.parametrize("value, problem", [
    (None, "not an object"),
    (["t", "p"], "not an object"),
    ("a title", "not an object"),
    ({"paragraphs": ["a", "b"]}, "title"),
    ({"title": "", "paragraphs": ["a", "b"]}, "title"),
    ({"title": "   ", "paragraphs": ["a", "b"]}, "title"),
    ({"title": 7, "paragraphs": ["a", "b"]}, "title"),
    ({"title": "x" * (schemas.ONSCREEN_TEXT_MAX_CHARS + 1), "paragraphs": ["a", "b"]}, "title"),
    ({"title": "t"}, "paragraphs must be a list"),
    ({"title": "t", "paragraphs": "a b"}, "paragraphs must be a list"),
    ({"title": "t", "paragraphs": ["only one"]}, "1 entries"),
    ({"title": "t", "paragraphs": []}, "0 entries"),
    ({"title": "t", "paragraphs": ["a", "b", "c", "d", "e"]}, "5 entries"),
    ({"title": "t", "paragraphs": ["a", None]}, "paragraphs[1]"),
    ({"title": "t", "paragraphs": ["a", " \n"]}, "paragraphs[1]"),
    ({"title": "t", "paragraphs": [{"text": "a"}, "b"]}, "paragraphs[0]"),
    ({"title": "t", "paragraphs": ["a", "x" * (schemas.ONSCREEN_TEXT_MAX_CHARS + 1)]}, "paragraphs[1]"),
])
def test_an_unusable_image_post_names_its_problem_and_normalizes_to_none(value, problem):
    got = schemas.image_post_problem(value)
    assert got is not None and problem in got
    assert schemas.normalize_image_post(value) is None


@pytest.mark.parametrize("n", [2, 3, 4])
def test_a_usable_image_post_keeps_its_strings_verbatim_and_drops_extra_keys(n):
    value = {"title": "  Spaced title ", "paragraphs": [f"Paragraph {i}. " for i in range(n)],
             "verdicts": ["ignored"], "font": "Comic Sans"}
    assert schemas.image_post_problem(value) is None
    got = schemas.normalize_image_post(value)
    assert got == {"title": "  Spaced title ", "paragraphs": value["paragraphs"]}
    got["paragraphs"].append("mutated")   # a copy: the stored package is never aliased
    assert len(value["paragraphs"]) == n


# ── frozen_post_formats ───────────────────────────────────────────────────────


def _with_formats(formats, **extra):
    return {"post_formats": formats, "image_post": copy.deepcopy(_IMAGE_POST), "image_footer": _IMAGE_FOOTER,
            **extra}


@pytest.mark.parametrize("output", [None, [], "x", {}, {"posts": {}}, {"post_formats": None}])
def test_an_output_without_frozen_formats_reads_as_none(output):
    assert mrs.frozen_post_formats(output) is None


def test_frozen_formats_read_back_exactly():
    formats = {"x": "image", "bluesky": "text", "tiktok": "video", "youtube": "video"}
    assert mrs.frozen_post_formats(_with_formats(formats)) == formats
    # text and video alone need no image post beside them
    assert mrs.frozen_post_formats({"post_formats": {"x": "text", "tiktok": "video"}}) == {
        "x": "text", "tiktok": "video"}


@pytest.mark.parametrize("output, match", [
    (_with_formats(["x", "image"]), "not an object"),
    (_with_formats("image"), "not an object"),
    (_with_formats({"x": "carousel"}), "not a format"),
    (_with_formats({"x": "IMAGE"}), "not a format"),
    (_with_formats({"x": None}), "not a format"),
    (_with_formats({"x": ["image"]}), "not a format"),
    (_with_formats({"x": "video"}), "not a format"),           # X never takes a video
    (_with_formats({"tiktok": "image"}), "not a format"),      # TikTok never takes an image here
    (_with_formats({"myspace": "text"}), "not a format"),
    (_with_formats({1: "text"}), "not a format"),
    ({"post_formats": {"x": "image"}}, "no usable image_post"),
    (_with_formats({"x": "image"}, image_post=None), "no usable image_post"),
    (_with_formats({"x": "image"}, image_post={"title": "t", "paragraphs": ["one"]}), "no usable image_post"),
    (_with_formats({"x": "image"}, image_footer=None), "no usable image_post"),
    (_with_formats({"x": "image"}, image_footer="  "), "no usable image_post"),
])
def test_frozen_formats_that_do_not_read_back_raise_never_guess(output, match):
    with pytest.raises(ValueError, match=match):
        mrs.frozen_post_formats(output)


# ── C6: the request schema bounds the post image ──────────────────────────────


def test_the_request_schema_bounds_the_post_image():
    base = {"kind": "card", "ext": "jpg", "sha256": SHA, "bytes": 900_000}
    ok = schemas.AssetRegisterRequest(**base, metadata=_image_meta(render_key="k", card_version=3))
    assert ok.metadata["image_role"] == "post_image"
    assert schemas.AssetRegisterRequest(**{**base, "bytes": schemas.POST_IMAGE_MAX_BYTES}, metadata=_image_meta())
    # an ordinary card (no role, no text) is unchanged
    assert schemas.AssetRegisterRequest(kind="card", ext="png", sha256=SHA, bytes=10).metadata == {}
    bad = [
        (base, {"image_role": "post_image"}, "must declare metadata.onscreen_text"),
        (base, {"image_role": "post_image", "onscreen_text": []}, "non-empty"),
        (base, {"image_role": "post_image", "onscreen_text": [1]}, "characters"),
        ({**base, "ext": "png"}, _image_meta(), r"must be a \.jpg"),
        ({**base, "bytes": schemas.POST_IMAGE_MAX_BYTES + 1}, _image_meta(), "max 950000"),
        (base, {"onscreen_text": _drawn()}, "only a video or a post image"),
        (base, {"onscreen_text": _drawn(), "image_role": "cover"}, "image_role must be one of"),
        (base, {"onscreen_text": _drawn(), "image_role": None}, "image_role must be one of"),
        (base, {"onscreen_text": _drawn(), "image_role": ["post_image"]}, "image_role must be one of"),
        ({"kind": "video", "ext": "mp4", "sha256": SHA, "bytes": 1},
         {"onscreen_text": ["a"], "voice_asset_id": "v", "image_role": "post_image"}, "only a card"),
        ({"kind": "carousel", "ext": "jpg", "sha256": SHA, "bytes": 1}, _image_meta(), "only a card"),
        ({"kind": "audio", "ext": "m4a", "sha256": SHA, "bytes": 1}, {"onscreen_text": ["a"]},
         "only a video or a post image"),
    ]
    for req, metadata, match in bad:
        with pytest.raises(ValueError, match=match):
            schemas.AssetRegisterRequest(**req, metadata=metadata)


# ── C6: what the post image draws ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_post_image_drawing_the_accepted_text_and_its_footer_registers(svc):
    rid = await _image_run(svc)
    image = await _post_image(svc, rid)
    assert image["status"] == "ready" and image["kind"] == "card"
    assert image["metadata"]["image_role"] == "post_image" and image["storage_path"].endswith(".jpg")
    # The footer alone (with nothing else) is a legal, if empty, image.
    rid2 = (await svc.claim_run(date(2026, 9, 16), worker_version="t", dry_run=False, now=NOW,
                                claim_nonce=_n()))[0]["id"]
    await _accept_image_script(svc, rid2, {"x": "image"})
    await _post_image(svc, rid2, sha="d" * 64, metadata=_image_meta(onscreen_text=[_IMAGE_FOOTER]))


@pytest.mark.parametrize("drawn, match", [
    (_drawn() + ["Buy now"], "not the accepted image post"),
    ([t.upper() if i == 0 else t for i, t in enumerate(_drawn())], "not the accepted image post"),
    (_drawn() + [_CARD["title"]], "not the accepted image post"),        # a VIDEO card is not image text
    (_drawn() + [_DISCLAIMER_CARD], "not the accepted image post"),
    (_drawn() + ["Caydex"], "not the accepted image post"),              # the video's end card neither
    (_drawn()[:-1], "does not draw its footer"),
    ([_IMAGE_POST["title"]], "does not draw its footer"),
    (_drawn() + [{"t": "x"}], "not the accepted image post"),            # never a TypeError
    ([], "must declare"),
    ("not a list", "must declare"),
])
@pytest.mark.asyncio
async def test_a_post_image_declaring_other_text_or_no_footer_is_refused(svc, drawn, match):
    rid = await _image_run(svc)
    with pytest.raises(MarketingRequestInvalid, match=match):
        await svc.register_asset(rid, kind="card", ext="jpg", sha256="c" * 64, size_bytes=1,
                                 metadata=_image_meta(onscreen_text=drawn), claim=_holder(svc, rid))
    assert not [a for a in svc.fake.tables[mrs.ASSETS].rows if a["kind"] == "card"]


@pytest.mark.parametrize("output_patch, match", [
    ({"image_post": None}, "carries no image post"),
    ({"image_footer": None}, "carries no image post"),
    ({"image_post": {"title": "t", "paragraphs": ["only one"]}}, "carries no image post"),
])
@pytest.mark.asyncio
async def test_a_post_image_needs_an_accepted_image_post_to_be_checked_against(svc, output_patch, match):
    rid = await _image_run(svc)
    script = next(r for r in svc.fake.tables[mrs.SCRIPTS].rows if r["run_id"] == rid)
    script["output"].update(output_patch)
    with pytest.raises(MarketingRequestInvalid, match=match):
        await svc.register_asset(rid, kind="card", ext="jpg", sha256="c" * 64, size_bytes=1,
                                 metadata=_image_meta(), claim=_holder(svc, rid))


@pytest.mark.asyncio
async def test_a_post_image_before_the_script_is_accepted_is_not_ready(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    with pytest.raises(mrs.MarketingScriptNotReady):
        await svc.register_asset(row["id"], kind="card", ext="jpg", sha256="c" * 64, size_bytes=1,
                                 metadata=_image_meta(), claim=_holder(svc, row["id"]))


@pytest.mark.parametrize("ext, size, metadata, match", [
    ("png", 1, _image_meta(), r"\.jpg"),
    ("jpg", schemas.POST_IMAGE_MAX_BYTES + 1, _image_meta(), "at most"),
    ("jpg", 1, {"onscreen_text": _drawn()}, "only as the post image"),
    ("jpg", 1, {"onscreen_text": _drawn(), "image_role": "cover"}, "only as the post image"),
    ("jpg", 1, {"image_role": "post_image"}, "must declare"),
])
@pytest.mark.asyncio
async def test_the_service_fences_the_post_image_even_without_the_request_schema(svc, ext, size, metadata, match):
    """The endpoint's schema checks these first; the service is its own fence (a direct caller)."""
    rid = await _image_run(svc)
    with pytest.raises(MarketingRequestInvalid, match=match):
        await svc.register_asset(rid, kind="card", ext=ext, sha256="c" * 64, size_bytes=size,
                                 metadata=metadata, claim=_holder(svc, rid))
    assert not [a for a in svc.fake.tables[mrs.ASSETS].rows if a["kind"] == "card"]


@pytest.mark.asyncio
async def test_an_ordinary_card_is_not_checked_as_a_post_image(svc):
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    # no accepted script at all, and still fine: a card that declares nothing is not text the server
    # vouches for (it can never be an image post's media — that needs the verified pointer)
    asset, upload = await svc.register_asset(row["id"], kind="card", ext="png", sha256="e" * 64,
                                             size_bytes=10, metadata={"render_key": "k"},
                                             claim=_holder(svc, row["id"]))
    assert asset["status"] == "pending_upload" and upload is not None


# ── C6: the read-back's image pointer ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_read_back_verifies_the_image_pointer_kind_role_and_readiness(svc):
    rid = await _image_run(svc)
    image = await _post_image(svc, rid)
    back = await svc.read_back(rid, claim=_holder(svc, rid))
    assert back["image_asset_id"] == image["id"]
    assert back["voice_asset_id"] is None and back["video_asset_id"] is None
    run_row = next(r for r in svc.fake.tables[mrs.RUNS].rows if r["id"] == rid)
    video = await _ready_asset(svc, rid)
    plain = await _asset_of(svc, rid, "card", "png", "f" * 64)
    pending, _ = await svc.register_asset(rid, kind="card", ext="jpg", sha256="9" * 64, size_bytes=1,
                                          metadata=_image_meta(), claim=_holder(svc, rid))
    for wrong in (video["id"], plain["id"], pending["id"], str(uuid.uuid4()), ""):
        run_row["metadata"]["image_asset_id"] = wrong
        assert (await svc.read_back(rid, claim=_holder(svc, rid)))["image_asset_id"] is None, wrong
    # a ready card whose role was edited away no longer verifies
    run_row["metadata"]["image_asset_id"] = image["id"]
    stored = next(a for a in svc.fake.tables[mrs.ASSETS].rows if a["id"] == image["id"])
    stored["metadata"] = {**stored["metadata"], "image_role": "cover"}
    assert (await svc.read_back(rid, claim=_holder(svc, rid)))["image_asset_id"] is None


def test_the_assets_route_carries_the_image_pointer(monkeypatch):
    from fastapi.testclient import TestClient

    import app.api.v1.endpoints.marketing_internal as mi
    from app.main import app

    monkeypatch.setattr(mi.settings, "MARKETING_WORKER_TOKEN", "tok")

    class Svc:
        def __init__(self, back):
            self.back = back

        async def read_back(self, run_id, *, claim):
            return self.back

    headers = {"X-Marketing-Worker-Token": "tok", "X-Marketing-Claim": "1." + "a" * 32}
    client = TestClient(app)
    monkeypatch.setattr(mi, "get_marketing_run_service", lambda: Svc(
        {"voice_asset_id": None, "video_asset_id": None, "image_asset_id": "img-1", "assets": []}))
    r = client.get("/api/v1/internal/marketing/runs/r1/assets", headers=headers)
    assert r.status_code == 200 and r.json()["image_asset_id"] == "img-1"
    # a service that predates the pointer (no key) still answers, with None
    monkeypatch.setattr(mi, "get_marketing_run_service", lambda: Svc(
        {"voice_asset_id": None, "video_asset_id": None, "assets": []}))
    r = client.get("/api/v1/internal/marketing/runs/r1/assets", headers=headers)
    assert r.status_code == 200 and r.json()["image_asset_id"] is None


# ── C7: create_posts records each platform in its frozen format ───────────────


@pytest.mark.asyncio
async def test_image_posts_carry_the_runs_verified_image_and_are_born_pending_review(svc, monkeypatch):
    monkeypatch.setattr(mrs.settings, "MARKETING_AUTO_PUBLISH", True)   # a real run, auto-publish on
    rid = await _image_run(svc)
    image = await _post_image(svc, rid)
    video = await _ready_asset(svc, rid)
    await _point_video(svc, rid, video["id"])
    posts = await svc.create_posts(rid, [
        {"platform": "x", "format": "image", "asset_ids": [image["id"]]},
        {"platform": "bluesky", "format": "image", "asset_ids": [image["id"]]},
        {"platform": "facebook", "format": "text"},
        {"platform": "tiktok", "format": "video", "asset_ids": [video["id"]]},
    ], claim=_holder(svc, rid))
    by = {p["platform"]: p for p in posts}
    assert {p: by[p]["format"] for p in by} == {"x": "image", "bluesky": "image", "facebook": "text",
                                               "tiktok": "video"}
    # media posts always wait for a reviewer; only the media-less text post is born approved
    assert by["x"]["status"] == by["bluesky"]["status"] == by["tiktok"]["status"] == "pending_review"
    assert by["facebook"]["status"] == "approved" and by["facebook"]["approved_by"] == "auto"
    assert by["x"]["asset_ids"] == by["bluesky"]["asset_ids"] == [image["id"]]
    # captions unchanged: the platform's own accepted copy
    assert by["x"]["caption"] == _server_copy("x")["caption"] and by["x"]["idempotency_key"] == "2026-09-17:x:image"
    # a resumed worker re-sending the same pairs gets the same rows back, untouched
    before = [dict(r) for r in svc.fake.tables[mrs.POSTS].rows]
    again = await svc.create_posts(rid, [{"platform": "x", "format": "image", "asset_ids": [image["id"]]}],
                                   claim=_holder(svc, rid))
    assert again[0]["id"] == by["x"]["id"] and svc.fake.tables[mrs.POSTS].rows == before


@pytest.mark.parametrize("label, spec", [
    ("text where the run froze an image", {"platform": "x", "format": "text"}),
    ("an image where the run froze text", {"platform": "facebook", "format": "image", "asset_ids": ["IMAGE"]}),
    ("a video platform asked for an image", {"platform": "tiktok", "format": "image", "asset_ids": ["IMAGE"]}),
    ("an image without its asset", {"platform": "x", "format": "image"}),
    ("an image carrying another card", {"platform": "x", "format": "image", "asset_ids": ["CARD"]}),
    ("an image carrying the video", {"platform": "x", "format": "image", "asset_ids": ["VIDEO"]}),
    ("an image carrying two cards", {"platform": "x", "format": "image", "asset_ids": ["IMAGE", "CARD"]}),
    ("an image carrying an unchecked card of the right shape", {"platform": "x", "format": "image",
                                                               "asset_ids": ["UNPOINTED"]}),
])
@pytest.mark.asyncio
async def test_a_spec_off_the_frozen_format_or_the_verified_image_is_refused_and_writes_nothing(svc, label, spec):
    rid = await _image_run(svc)
    ids = {"IMAGE": (await _post_image(svc, rid))["id"],
           "CARD": (await _asset_of(svc, rid, "card", "png", "3" * 64))["id"],
           "VIDEO": (await _ready_asset(svc, rid))["id"],
           "UNPOINTED": (await _post_image(svc, rid, sha="8" * 64, point=False))["id"]}
    bad = {**spec, "asset_ids": [ids[a] for a in spec.get("asset_ids", [])]}
    good = [{"platform": "bluesky", "format": "image", "asset_ids": [ids["IMAGE"]]}]
    with pytest.raises(MarketingRequestInvalid):
        await svc.create_posts(rid, [*good, bad], claim=_holder(svc, rid))   # the invalid spec LAST
    assert svc.fake.tables[mrs.POSTS].rows == [], label


@pytest.mark.parametrize("pointer", ["missing", "a plain card", "the video", "another run's image"])
@pytest.mark.asyncio
async def test_an_image_post_needs_the_runs_own_verified_pointer(svc, pointer):
    rid = await _image_run(svc)
    image = await _post_image(svc, rid, point=False)
    run_row = next(r for r in svc.fake.tables[mrs.RUNS].rows if r["id"] == rid)
    if pointer == "a plain card":
        run_row["metadata"]["image_asset_id"] = (await _asset_of(svc, rid, "card", "png", "3" * 64))["id"]
    elif pointer == "the video":
        run_row["metadata"]["image_asset_id"] = (await _ready_asset(svc, rid))["id"]
    elif pointer == "another run's image":
        # A ready post image of ANOTHER run (one run per day: its row is seeded directly).
        foreign = dict(image, id=str(uuid.uuid4()), run_id=str(uuid.uuid4()))
        svc.fake.tables[mrs.ASSETS].rows.append(foreign)
        run_row["metadata"]["image_asset_id"] = foreign["id"]
    with pytest.raises(MarketingRequestInvalid, match="no verified post image"):
        await svc.create_posts(rid, [{"platform": "x", "format": "image", "asset_ids": [image["id"]]}],
                               claim=_holder(svc, rid))
    assert svc.fake.tables[mrs.POSTS].rows == []


@pytest.mark.asyncio
async def test_an_image_post_without_frozen_formats_is_refused_exactly_as_before(svc):
    """A script accepted before drop 1 carries no `post_formats`: it validates as it always did (text
    and video), and an `image` spec — newly in the format map — is still refused for it."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    rid = row["id"]
    await _accept_script(svc, rid, ["x", "bluesky"])
    card = await _asset_of(svc, rid, "card", "png", "3" * 64)
    with pytest.raises(MarketingRequestInvalid, match="froze no post formats"):
        await svc.create_posts(rid, [{"platform": "bluesky", "format": "text"},
                                     {"platform": "x", "format": "image", "asset_ids": [card["id"]]}],
                               claim=_holder(svc, rid))
    assert svc.fake.tables[mrs.POSTS].rows == []
    posts = await svc.create_posts(rid, [{"platform": "x", "format": "text"},
                                         {"platform": "bluesky", "format": "text"}], claim=_holder(svc, rid))
    assert [p["format"] for p in posts] == ["text", "text"]


@pytest.mark.parametrize("bad_formats", [{"x": "carousel"}, ["x"], {"x": "image", "tiktok": "image"}])
@pytest.mark.asyncio
async def test_frozen_formats_that_do_not_read_back_refuse_every_post_of_the_run(svc, caplog, bad_formats):
    rid = await _image_run(svc)
    script = next(r for r in svc.fake.tables[mrs.SCRIPTS].rows if r["run_id"] == rid)
    script["output"]["post_formats"] = bad_formats
    import logging

    with caplog.at_level(logging.ERROR, logger=mrs.logger.name):
        with pytest.raises(MarketingRequestInvalid, match="do not read back"):
            await svc.create_posts(rid, [{"platform": "facebook", "format": "text"}], claim=_holder(svc, rid))
    assert svc.fake.tables[mrs.POSTS].rows == []
    assert any("do not read back" in r.getMessage() and r.levelno == logging.ERROR for r in caplog.records)


@pytest.mark.asyncio
async def test_one_post_per_platform_within_a_request_and_against_the_ledger(svc):
    # A legacy run (no frozen formats) is where two formats of one platform could otherwise both pass
    # the format map: text, and a hand-made image row already in the ledger.
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    rid = row["id"]
    await _accept_script(svc, rid, ["x", "bluesky"])
    svc.fake.tables[mrs.POSTS].rows.append({
        "id": str(uuid.uuid4()), "run_id": rid, "platform": "x", "format": "image", "status": "skipped",
        "idempotency_key": "2026-09-17:x:image", "asset_ids": [], "metadata": {}})
    with pytest.raises(MarketingRequestInvalid, match=r"already recorded x as \['image'\]"):
        await svc.create_posts(rid, [{"platform": "bluesky", "format": "text"},
                                     {"platform": "x", "format": "text"}], claim=_holder(svc, rid))
    assert [p["platform"] for p in svc.fake.tables[mrs.POSTS].rows] == ["x"]
    # another run's rows do not count
    other, _ = await svc.claim_run(date(2026, 9, 16), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await _accept_script(svc, other["id"], ["x"])
    (p,) = await svc.create_posts(other["id"], [{"platform": "x", "format": "text"}], claim=_holder(svc, other["id"]))
    assert p["format"] == "text"


@pytest.mark.asyncio
async def test_one_platform_named_in_two_formats_in_one_request_is_refused(svc, monkeypatch):
    rid = await _image_run(svc)
    image = await _post_image(svc, rid)
    with pytest.raises(MarketingRequestInvalid):
        await svc.create_posts(rid, [{"platform": "x", "format": "image", "asset_ids": [image["id"]]},
                                     {"platform": "x", "format": "text"}], claim=_holder(svc, rid))
    assert svc.fake.tables[mrs.POSTS].rows == []
    # The rule on its own (every other check passing): a legacy run under a map that would let X take
    # two media-less formats. Defence in depth — today's map plus the frozen formats already exclude it.
    legacy, _ = await svc.claim_run(date(2026, 9, 16), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    await _accept_script(svc, legacy["id"], ["x"])
    monkeypatch.setitem(mrs.POST_FORMATS_BY_PLATFORM, "x", ("text", "article"))
    with pytest.raises(MarketingRequestInvalid, match="named in 2 formats"):
        await svc.create_posts(legacy["id"], [{"platform": "x", "format": "text"},
                                              {"platform": "x", "format": "article"}],
                               claim=_holder(svc, legacy["id"]))
    assert svc.fake.tables[mrs.POSTS].rows == []


@pytest.mark.asyncio
async def test_an_outlet_the_script_dropped_is_still_script_not_ready_with_frozen_formats(svc):
    rid = await _image_run(svc)   # threads has neither copy nor a frozen format
    with pytest.raises(mrs.MarketingScriptNotReady):
        await svc.create_posts(rid, [{"platform": "threads", "format": "text"}], claim=_holder(svc, rid))


@pytest.mark.asyncio
async def test_a_failed_ledger_read_of_the_recorded_posts_raises_and_writes_nothing(svc, monkeypatch):
    rid = await _image_run(svc)

    async def boom(run_id):
        raise mrs.MarketingRunError(f"create_posts.recorded failed (run_id={run_id})")

    monkeypatch.setattr(svc, "_recorded_formats", boom)
    with pytest.raises(mrs.MarketingRunError, match="recorded"):
        await svc.create_posts(rid, [{"platform": "facebook", "format": "text"}], claim=_holder(svc, rid))
    assert svc.fake.tables[mrs.POSTS].rows == []


# ── server F1: every video post of a run carries the run's ONE verified video ─────
#
# The review bundle shows the owner ONE video for all of a run's video posts and one "Approve all"
# decides them; the server cannot read pixels. So a video post is pinned to the run's verified
# `metadata.video_asset_id` (as an image post is to `image_asset_id`) once the formats are frozen, and a
# script accepted before drop 1 must at least name the same video on every video post.


@pytest.mark.parametrize("case", ["another ready video", "no pointer", "the pointer names a card",
                                  "two videos on one post"])
@pytest.mark.asyncio
async def test_a_video_post_under_frozen_formats_carries_exactly_the_runs_verified_video(svc, case):
    rid = await _image_run(svc, {"tiktok": "video", "youtube": "video", "facebook": "text"})
    video = await _ready_asset(svc, rid)
    other = await _ready_asset(svc, rid, sha="d" * 64)
    if case == "the pointer names a card":
        await _point_video(svc, rid, (await _asset_of(svc, rid, "card", "png", "3" * 64))["id"])
    elif case != "no pointer":
        await _point_video(svc, rid, video["id"])
    youtube = {"another ready video": [other["id"]], "two videos on one post": [video["id"], other["id"]]}.get(
        case, [video["id"]])
    with pytest.raises(MarketingRequestInvalid, match="video"):
        await svc.create_posts(rid, [{"platform": "tiktok", "format": "video", "asset_ids": [video["id"]]},
                                     {"platform": "youtube", "format": "video", "asset_ids": youtube}],
                               claim=_holder(svc, rid))
    assert svc.fake.tables[mrs.POSTS].rows == [], case


@pytest.mark.asyncio
async def test_video_posts_naming_the_runs_verified_video_are_recorded(svc):
    rid = await _image_run(svc, {"tiktok": "video", "youtube": "video", "facebook": "text"})
    video = await _ready_asset(svc, rid)
    await _ready_asset(svc, rid, sha="d" * 64)            # a second ready video nobody names
    await _point_video(svc, rid, video["id"])
    posts = await svc.create_posts(rid, [{"platform": "tiktok", "format": "video", "asset_ids": [video["id"]]},
                                         {"platform": "youtube", "format": "video", "asset_ids": [video["id"]]},
                                         {"platform": "facebook", "format": "text"}], claim=_holder(svc, rid))
    assert [(p["platform"], p["asset_ids"]) for p in posts] == [
        ("tiktok", [video["id"]]), ("youtube", [video["id"]]), ("facebook", [])]


@pytest.mark.asyncio
async def test_a_script_accepted_before_drop_1_still_needs_one_video_for_every_video_post(svc):
    """No frozen formats: no pointer is required (validated as before) — but two video posts naming
    different videos are refused, and nothing is written."""
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=False, now=NOW, claim_nonce=_n())
    rid = row["id"]
    await _accept_script(svc, rid, ["tiktok", "youtube"])
    a = await _ready_asset(svc, rid)
    b = await _ready_asset(svc, rid, sha="d" * 64)
    with pytest.raises(MarketingRequestInvalid, match="different asset lists"):
        await svc.create_posts(rid, [{"platform": "tiktok", "format": "video", "asset_ids": [a["id"]]},
                                     {"platform": "youtube", "format": "video", "asset_ids": [b["id"]]}],
                               claim=_holder(svc, rid))
    assert svc.fake.tables[mrs.POSTS].rows == []
    posts = await svc.create_posts(rid, [{"platform": "tiktok", "format": "video", "asset_ids": [a["id"]]},
                                         {"platform": "youtube", "format": "video", "asset_ids": [a["id"]]}],
                                   claim=_holder(svc, rid))
    assert [p["asset_ids"] for p in posts] == [[a["id"]], [a["id"]]]


# ── compat F2: the claim records what the holding worker can render ───────────


def _run_meta(svc, run_id: str) -> Dict[str, Any]:
    return next(r for r in svc.fake.tables[mrs.RUNS].rows if r["id"] == run_id)["metadata"]


@pytest.mark.asyncio
async def test_a_claim_records_the_workers_capabilities_and_a_reclaim_replaces_them(svc):
    day = date(2026, 9, 17)
    nonce = _n()
    row, reason = await svc.claim_run(day, worker_version="drop1", dry_run=True, now=NOW, claim_nonce=nonce,
                                      capabilities=("post_image", "post_image"))
    assert reason == CLAIMED and row["metadata"] == {"claim_nonce": nonce, "worker_capabilities": ["post_image"]}
    assert mrs.run_worker_capabilities(row) == {"post_image"}
    rid = row["id"]
    await svc.update_run(rid, metadata={"voice_asset_id": "v-1"}, worker=True, claim=_holder(svc, rid))
    # An OLDER worker re-claims the failed run: the previous holder's capability does not outlive its
    # claim (the key is removed), everything else in metadata is kept.
    await svc.update_run(rid, status="failed", last_error="boom", finished=True)
    old_nonce = _n()
    re, reason = await svc.claim_run(day, worker_version="phase4", dry_run=True, now=NOW, claim_nonce=old_nonce)
    assert reason == CLAIMED and re["attempts"] == 2
    assert _run_meta(svc, rid) == {"claim_nonce": old_nonce, "voice_asset_id": "v-1"}
    assert mrs.run_worker_capabilities(re) == frozenset()
    # …and a drop-1 worker re-claiming it after that declares it again.
    await svc.update_run(rid, status="failed", last_error="boom", finished=True)
    new_nonce = _n()
    re, _ = await svc.claim_run(day, worker_version="drop1", dry_run=True, now=NOW, claim_nonce=new_nonce,
                                capabilities=("post_image",))
    assert _run_meta(svc, rid) == {"claim_nonce": new_nonce, "voice_asset_id": "v-1",
                                   "worker_capabilities": ["post_image"]}


@pytest.mark.asyncio
async def test_an_old_workers_claim_leaves_the_run_exactly_as_before_and_unknowns_are_dropped(svc):
    nonce = _n()
    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="phase4", dry_run=True, now=NOW,
                                 claim_nonce=nonce)
    assert row["metadata"] == {"claim_nonce": nonce}
    # A direct caller (the route's schema drops these first) cannot record an unknown capability.
    row2, _ = await svc.claim_run(date(2026, 9, 18), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n(),
                                  capabilities=("carousel", "POST_IMAGE"))
    assert "worker_capabilities" not in row2["metadata"]


@pytest.mark.asyncio
async def test_a_worker_patch_can_never_forge_its_capabilities(svc, caplog):
    import logging

    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="phase4", dry_run=True, now=NOW, claim_nonce=_n())
    rid = row["id"]
    with caplog.at_level(logging.WARNING, logger=mrs.logger.name):
        await svc.update_run(rid, stage="selected", metadata={"worker_capabilities": ["post_image"], "note": "kept"},
                             worker=True, claim=_holder(svc, rid))
    meta = _run_meta(svc, rid)
    assert "worker_capabilities" not in meta and meta["note"] == "kept"
    assert mrs.run_worker_capabilities({"metadata": meta}) == frozenset()
    assert any("server-owned metadata" in r.getMessage() and "worker_capabilities" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("meta, expected", [
    ({"worker_capabilities": ["post_image"]}, {"post_image"}),
    ({"worker_capabilities": ["post_image", "carousel", 7, None]}, {"post_image"}),
    ({"worker_capabilities": "post_image"}, set()),        # a string is not a list of capabilities
    ({"worker_capabilities": {"post_image": True}}, set()),
    ({"worker_capabilities": None}, set()),
    ({}, set()),
    (None, set()),
])
def test_run_worker_capabilities_reads_only_a_list_of_known_values(meta, expected):
    assert mrs.run_worker_capabilities({"metadata": meta}) == expected
    assert mrs.run_worker_capabilities(None) == frozenset()


def test_drop_2a_adds_the_news_templates_capability():
    assert schemas.WORKER_CAPABILITIES[:2] == ("post_image", "news_templates")
    assert schemas.WORKER_CAPABILITY_NEWS_TEMPLATES == "news_templates"
    assert mrs.run_worker_capabilities({"metadata": {"worker_capabilities": ["news_templates", "x"]}}) == {
        "news_templates"}


@pytest.mark.asyncio
async def test_drop_2b_adds_the_layouts_2b_capability_and_a_claim_records_it(svc):
    """Review R9 (critic): the drop-2b worker declares `layouts_2b` (it draws the `pair` / `grid` template
    images); the server knows it, records it on the claim like any capability, and a re-claim by a drop-2a
    image (news_templates only) removes it — `script_service` then drops the 2b-layout series."""
    assert schemas.WORKER_CAPABILITIES == ("post_image", "news_templates", "layouts_2b")
    assert schemas.WORKER_CAPABILITY_LAYOUTS_2B == "layouts_2b" and schemas.WORKER_LAYOUTS_2B == ("pair", "grid")
    day = date(2026, 9, 17)
    nonce = _n()
    row, reason = await svc.claim_run(day, worker_version="drop2b", dry_run=True, now=NOW, claim_nonce=nonce,
                                      capabilities=("post_image", "news_templates", "layouts_2b"))
    assert reason == CLAIMED
    assert row["metadata"]["worker_capabilities"] == ["layouts_2b", "news_templates", "post_image"]
    assert mrs.run_worker_capabilities(row) == {"post_image", "news_templates", "layouts_2b"}
    rid = row["id"]
    await svc.update_run(rid, status="failed", last_error="boom", finished=True)
    re, reason = await svc.claim_run(day, worker_version="drop2a", dry_run=True, now=NOW, claim_nonce=_n(),
                                     capabilities=("post_image", "news_templates"))
    assert reason == CLAIMED and mrs.run_worker_capabilities(re) == {"post_image", "news_templates"}
    assert _run_meta(svc, rid)["worker_capabilities"] == ["news_templates", "post_image"]


_CLAIM_BASE = {"run_date": "2026-11-16", "worker_version": "drop3", "claim_nonce": "ab" * 16}


@pytest.mark.parametrize("declared, kept", [
    (["post_image", "news_templates", "carousel_v2"], ["news_templates", "post_image"]),
    (["carousel_v2"], []),
    (["POST_IMAGE", "post_image"], ["post_image"]),
    (["post_image", "post_image"], ["post_image"]),
])
def test_a_claim_declaring_a_capability_this_web_does_not_know_keeps_the_claim(declared, kept, caplog):
    """Review PW-1: a NEWER worker against an older web (the "web back" rollback) must not lose every
    claim to a 422 — an unknown capability is dropped with a WARNING, never recorded, never credited."""
    import logging

    with caplog.at_level(logging.WARNING, logger=schemas.logger.name):
        req = schemas.RunClaimRequest.model_validate({**_CLAIM_BASE, "capabilities": declared})
    assert req.capabilities == kept
    unknown = sorted({c for c in declared if c not in schemas.WORKER_CAPABILITIES})
    warned = [r.getMessage() for r in caplog.records if "does not know" in r.getMessage()]
    assert len(warned) == (1 if unknown else 0)
    assert all(repr(c) in warned[0] for c in unknown)


@pytest.mark.parametrize("bad", [[42], "post_image", ["post_image"] * 9, None, [None], [["post_image"]]])
def test_a_claim_with_a_malformed_capability_list_is_still_refused(bad):
    """Typed and bounded as before: only an unknown STRING is forgiven."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        schemas.RunClaimRequest.model_validate({**_CLAIM_BASE, "capabilities": bad})


# ── Drop 2 D1: migration 190 widens marketing_runs.content_class to A/C/F ─────────────────

import re as _re_d1  # noqa: E402  (kept local to this block: the file's own `re` import is shared)
from pathlib import Path as _Path_d1  # noqa: E402

_MIGRATIONS_D1 = _Path_d1(__file__).resolve().parents[1] / "database" / "migrations"
_MIGRATION_190 = _MIGRATIONS_D1 / "190_marketing_company_classes.sql"
_CC_CONSTRAINT = "marketing_runs_content_class_check"


def _strip_sql_comments(text: str) -> str:
    """Drop `--` line comments and `/* */` blocks — but never inside a string literal (the table
    comments carry prose). A small scanner, not a regex, so a quote in a comment cannot confuse it."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "'":
            j = i + 1
            while j < n:
                if text[j] == "'" and j + 1 < n and text[j + 1] == "'":
                    j += 2
                    continue
                if text[j] == "'":
                    break
                j += 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("--", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


_ADD_CC_RE = _re_d1.compile(
    rf"ADD\s+CONSTRAINT\s+{_CC_CONSTRAINT}\s+CHECK\s*\(\s*content_class\s+IN\s*\(([^)]*)\)\s*\)", _re_d1.I)
_DROP_CC_RE = _re_d1.compile(rf"DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+{_CC_CONSTRAINT}\b", _re_d1.I)


def _latest_content_class_check(migrations: _Path_d1):
    """(file name, class set) of the LATEST migration that ADDs the named content-class CHECK."""
    found = []
    for path in sorted(migrations.glob("[0-9][0-9][0-9]_*.sql")):
        for m in _ADD_CC_RE.finditer(_strip_sql_comments(path.read_text())):
            found.append((path.name, set(_re_d1.findall(r"'([A-Za-z_]+)'", m.group(1)))))
    return found[-1] if found else None


_SAME_LINE = "<same-line literals>"


def _comment_literal(sql: str, target: str) -> Optional[str]:
    """The text of `COMMENT ON <target> IS '…' '…' …;` (adjacent literals joined, '' unescaped), or
    None. Postgres joins adjacent string constants ONLY across a newline — a same-line pair is a
    syntax error — so a separator without one returns `_SAME_LINE`."""
    m = _re_d1.search(rf"COMMENT\s+ON\s+{_re_d1.escape(target)}\s+IS\s+((?:'(?:[^']|'')*'\s*)+);", sql, _re_d1.I)
    if not m:
        return None
    body = m.group(1)
    lits = list(_re_d1.finditer(r"'((?:[^']|'')*)'", body))
    for a, b in zip(lits, lits[1:]):
        if "\n" not in body[a.end():b.start()]:
            return _SAME_LINE
    return "".join(x.group(1).replace("''", "'") for x in lits)


def _without_literals(sql: str) -> str:
    """`sql` with every string literal emptied, so a keyword inside comment prose is not a statement."""
    return _re_d1.sub(r"'(?:[^']|'')*'", "''", sql)


def _static_190_problems(text: str) -> List[str]:
    """Why a migration-190 text breaks its contract (D1), or [] — every check names itself."""
    sql = _strip_sql_comments(text)
    bare = _without_literals(sql)
    stmts = [s.strip() for s in bare.split(";") if s.strip()]
    problems: List[str] = []
    if not stmts or stmts[0].upper() != "BEGIN":
        problems.append("does not open with BEGIN")
    if not stmts or stmts[-1].upper() != "COMMIT":
        problems.append("does not end with COMMIT")
    drop = _DROP_CC_RE.search(sql)
    add = _ADD_CC_RE.search(sql)
    if not drop:
        problems.append("no DROP CONSTRAINT IF EXISTS of the named CHECK (not idempotent)")
    if not add:
        problems.append("no ADD of the named CHECK")
    elif set(_re_d1.findall(r"'([A-Za-z_]+)'", add.group(1))) != set(schemas.CONTENT_CLASSES):
        problems.append("the CHECK does not list exactly CONTENT_CLASSES")
    if drop and add and drop.start() > add.start():
        problems.append("ADD before DROP")
    for bad in (r"\bINSERT\s+INTO\b", r"\bUPDATE\s+\w", r"\bDELETE\s+FROM\b", r"\bDROP\s+TABLE\b",
                r"\bTRUNCATE\b", r"\bGRANT\b", r"\bREVOKE\b", r"\bCREATE\s+TRIGGER\b",
                r"ALTER\s+TABLE\s+(?:public\.)?marketing_scripts\b"):
        if _re_d1.search(bad, bare, _re_d1.I):
            problems.append(f"unexpected statement {bad}")
    assets = _comment_literal(sql, "TABLE public.marketing_assets")
    scripts_c = _comment_literal(sql, "TABLE public.marketing_scripts")
    column = _comment_literal(sql, "COLUMN public.marketing_runs.content_class")
    for name, got in (("marketing_assets", assets), ("marketing_scripts", scripts_c),
                      ("marketing_runs.content_class", column)):
        if got is None:
            problems.append(f"no comment on {name}")
        elif got == _SAME_LINE:
            problems.append(f"the {name} comment joins string literals on one line (a syntax error)")
    if assets and assets != _SAME_LINE:
        if "nothing fmp-licensed" in assets.lower():
            problems.append("the marketing_assets comment still says 'Nothing FMP-licensed'")
        for must in ("never a market price", "price chart", "FMP credit", "logos/"):
            if must.lower() not in assets.lower():
                problems.append(f"the marketing_assets comment does not say {must!r}")
    if scripts_c and scripts_c != _SAME_LINE:
        if "template" not in scripts_c or "accepted" not in scripts_c:
            problems.append("the marketing_scripts comment does not describe template rows")
    if column and column != _SAME_LINE and "mirror" not in column.lower():
        problems.append("the content_class column comment does not call it a mirror")
    return problems


def test_content_class_check_reads_the_latest_migration_that_adds_it():
    """D1: the CHECK the database enforces is the LATEST migration that ADDs the named constraint
    (170 created it inline with A, C; 190 widens it), and it must equal CONTENT_CLASSES — or the 'F'
    mirror write 23514s with a message that names nothing."""
    latest = _latest_content_class_check(_MIGRATIONS_D1)
    assert latest is not None, "no migration ADDs marketing_runs_content_class_check"
    name, classes = latest
    assert int(name[:3]) >= 190, name
    assert classes == set(schemas.CONTENT_CLASSES) == {"A", "C", "F"}, (name, classes)
    # Every migration that ADDs it DROPs it IF EXISTS first (a re-run must not fail 42710).
    for path in sorted(_MIGRATIONS_D1.glob("[0-9][0-9][0-9]_*.sql")):
        sql = _strip_sql_comments(path.read_text())
        add = _ADD_CC_RE.search(sql)
        if add:
            drop = _DROP_CC_RE.search(sql)
            assert drop and drop.start() < add.start(), path.name
    # The DROP names the constraint Postgres actually generated for 170's inline CHECK.
    snapshot = (_MIGRATIONS_D1.parent / "schema_snapshot.sql").read_text()
    assert f"CONSTRAINT {_CC_CONSTRAINT} CHECK" in snapshot


def test_migration_190_is_transactional_idempotent_and_rewords_the_assets_comment():
    text = _MIGRATION_190.read_text()
    assert _static_190_problems(text) == []
    # The why-header is there (the rules require one) and names the deploy order.
    assert text.startswith("-- 190_marketing_company_classes.sql")
    assert "apply BEFORE the drop-2 web deploy" in text


@pytest.mark.parametrize("mutate, expect", [
    (lambda t: t.replace("BEGIN;", "", 1), "does not open with BEGIN"),
    (lambda t: t.replace("COMMIT;", "", 1), "does not end with COMMIT"),
    (lambda t: _re_d1.sub(r"ALTER TABLE public\.marketing_runs DROP CONSTRAINT IF EXISTS "
                          r"marketing_runs_content_class_check;", "", t), "not idempotent"),
    (lambda t: t.replace("('A', 'C', 'F')", "('A', 'C')"), "exactly CONTENT_CLASSES"),
    (lambda t: t.replace("'verified the object (size and content type). Since",
                         "'verified the object. Nothing FMP-licensed may be rendered. Since"),
     "Nothing FMP-licensed"),
    (lambda t: t.replace("COMMIT;", "UPDATE public.marketing_runs SET content_class = 'A';\nCOMMIT;"),
     "unexpected statement"),
    (lambda t: t.replace("'Informational mirror of the day''s content class, written best-effort by "
                         "script_service._heal_mirror: '\n",
                         "'Informational mirror of the day''s content class, written best-effort by "
                         "script_service._heal_mirror: ' "), "one line"),
])
def test_the_190_static_check_fails_on_each_broken_variant(mutate, expect):
    """Guard against the guard: each mutation of the real file is caught, by the named check."""
    original = _MIGRATION_190.read_text()
    broken = mutate(original)
    assert broken != original, "the mutation did not apply — the fixture text moved"
    assert any(expect in p for p in _static_190_problems(broken)), _static_190_problems(broken)


def test_add_before_drop_is_refused_by_the_static_check():
    original = _MIGRATION_190.read_text()
    drop = ("ALTER TABLE public.marketing_runs DROP CONSTRAINT IF EXISTS "
            "marketing_runs_content_class_check;\n")
    moved = original.replace(drop, "", 1).replace("COMMENT ON COLUMN", drop + "COMMENT ON COLUMN", 1)
    assert moved != original
    assert "ADD before DROP" in _static_190_problems(moved)


# ── Drop 2 D2: content classes, the setting, server-owned keys, WorkerScript ───────────────


def test_content_class_constants():
    assert schemas.CONTENT_CLASSES == ("A", "C", "F")
    assert schemas.NEWS_CLASSES == ("C", "F")
    assert set(schemas.NEWS_CLASSES) < set(schemas.CONTENT_CLASSES) and "A" not in schemas.NEWS_CLASSES
    assert schemas.TEMPLATE_AUTHORSHIP == "template"
    assert schemas.VIDEO_LAYOUT_PER_LINE == "per_line"


@pytest.mark.parametrize("raw, expected, logs_error", [
    (" a , c ", {"A", "C"}, False),
    ("C,F", {"A", "C", "F"}, False),
    ("A,X", {"A"}, True),
    ("f", {"A", "F"}, False),
    ("c,C, c ,f,F", {"A", "C", "F"}, False),
    (",, ,", {"A"}, False),
    ("A;C", {"A"}, True),                 # one unknown token "A;C" — never half-parsed
    ("B,C", {"A", "C"}, True),            # there is no class B, ever
    ("", {"A"}, False),
    (None, {"A"}, False),
    (1, {"A"}, True),
    (True, {"A"}, True),
    (["C", "F"], {"A"}, True),
])
def test_parse_content_classes_fails_closed_to_a(raw, expected, logs_error, caplog):
    import logging

    with caplog.at_level(logging.ERROR, logger=schemas.logger.name):
        got = schemas.parse_content_classes(raw)
    assert isinstance(got, frozenset) and got == expected
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR and r.name == schemas.logger.name]
    assert bool(errors) == logs_error, [r.getMessage() for r in errors]
    for r in errors:
        assert "MARKETING_CONTENT_CLASSES" in r.getMessage()


def test_parse_content_classes_never_echoes_an_unbounded_value(caplog):
    import logging

    with caplog.at_level(logging.ERROR, logger=schemas.logger.name):
        schemas.parse_content_classes(",".join(f"Z{i}{'q' * 500}" for i in range(50)))
    (rec,) = [r for r in caplog.records if r.name == schemas.logger.name]
    assert len(rec.getMessage()) < 400


def test_the_content_classes_setting_is_declared_with_a_lesson_only_default():
    from app.config import Settings, settings

    assert hasattr(settings, "MARKETING_CONTENT_CLASSES")
    assert Settings.model_fields["MARKETING_CONTENT_CLASSES"].default == "A"
    assert schemas.parse_content_classes(Settings.model_fields["MARKETING_CONTENT_CLASSES"].default) == {"A"}


@pytest.mark.parametrize("raw, expected, logs_error", [
    (" c , f ", "A,C,F", False),
    ("C,X", "A,C", True),
    ("", "A", False),
    ("f,F,a", "A,F", False),
    ("A", "A", False),
    ("nope", "A", True),
])
def test_the_setting_normalises_to_the_sorted_set_and_never_fails_the_deploy(monkeypatch, caplog, raw,
                                                                              expected, logs_error):
    import logging

    from app.config import Settings

    monkeypatch.setenv("MARKETING_CONTENT_CLASSES", raw)
    with caplog.at_level(logging.ERROR, logger="app.config"):
        got = Settings().MARKETING_CONTENT_CLASSES
    assert got == expected
    assert schemas.parse_content_classes(got) == set(expected.split(","))
    errors = [r for r in caplog.records if r.name == "app.config" and r.levelno >= logging.ERROR]
    assert bool(errors) == logs_error
    for r in errors:
        assert "MARKETING_CONTENT_CLASSES" in r.getMessage()


def test_a_non_string_setting_reads_as_a(caplog):
    import logging

    from app.config import Settings

    with caplog.at_level(logging.ERROR, logger="app.config"):
        assert Settings(MARKETING_CONTENT_CLASSES=1).MARKETING_CONTENT_CLASSES == "A"
    assert any("MARKETING_CONTENT_CLASSES" in r.getMessage() for r in caplog.records if r.name == "app.config")


def test_the_config_literal_equals_content_classes():
    """config.py imports no app module, so its validator carries an inline copy of CONTENT_CLASSES.
    Read by AST (the literal itself) and by behaviour (every class survives the validator)."""
    import ast

    from app.config import Settings

    src = (_Path_d1(__file__).resolve().parents[1] / "app" / "config.py").read_text()
    fn = next(n for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.FunctionDef) and n.name == "_content_classes_fail_closed")
    literals = [ast.literal_eval(a.value) for a in ast.walk(fn)
                if isinstance(a, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "known" for t in a.targets)]
    assert literals == [schemas.CONTENT_CLASSES]
    assert Settings(MARKETING_CONTENT_CLASSES=",".join(schemas.CONTENT_CLASSES)).MARKETING_CONTENT_CLASSES \
        == ",".join(sorted(schemas.CONTENT_CLASSES))


@pytest.mark.asyncio
async def test_a_worker_patch_can_never_write_the_days_series(svc, caplog):
    """D2: `series` / `series_trail` are mirrored by the server from the script's fact sheet; a
    worker PATCH carrying them is stripped (logged WARNING) and every other key lands."""
    import logging

    row, _ = await svc.claim_run(date(2026, 9, 17), worker_version="t", dry_run=True, now=NOW, claim_nonce=_n())
    rid = row["id"]
    planted = {"series": "ceo_buys", "series_trail": [{"series": "ceo_buys", "outcome": "chosen"}], "note": "kept"}
    with caplog.at_level(logging.WARNING, logger=mrs.logger.name):
        await svc.update_run(rid, stage="selected", metadata=planted, worker=True, claim=_holder(svc, rid))
    meta = _run_meta(svc, rid)
    assert "series" not in meta and "series_trail" not in meta and meta["note"] == "kept"
    assert any("server-owned metadata" in r.getMessage() and "series" in r.getMessage() for r in caplog.records)


_D2_WORKER_FIELDS = {
    "content_class": "C",
    "authorship": "template",
    "series": "ceo_buys",
    "video_layout": "per_line",
    "opening_card": {"kicker": "FILED LAST WEEK · FORM 4", "logos": ["GME"], "figure": "$74.4M",
                     "headline": "GameStop's CEO disclosed buying GameStop stock"},
    "image_spec": {"layout": "spotlight", "version": 1, "kicker": "FORM 4", "footer": "f",
                   "header": {"logo": "GME", "name": "GameStop"}, "figure": "$74.4M", "headline": "h",
                   "lines": ["one", "two"]},
    "logos": [{"key": "GME", "name": "GameStop", "url": None, "sha256": None, "bytes": None,
               "width": None, "height": None},
              {"key": "AAPL", "name": "Apple", "url": "https://x.supabase.co/storage/v1/object/public/"
               "marketing-media/logos/" + "a" * 32 + ".png", "sha256": "a" * 64, "bytes": 1234,
               "width": 200, "height": 200}],
}


def test_the_kick_response_round_trip_keeps_every_new_worker_script_field():
    """D2: Pydantic DROPS undeclared keys from ScriptKickResponse, so a worker field missing from
    WorkerScript never reaches the worker. Every drop-2 field survives a dict and a JSON round trip,
    beside the drop-1 ones."""
    script = {"hook": "h", "video_script": ["a", "b", "c", "d"], "cards": [{"title": "t", "body": "b"}] * 4,
              "carousel_slides": [], "disclaimer_card": "d", "outlets": ["x"],
              "post_formats": {"x": "image"}, "image_post": {"title": "t", "paragraphs": ["p", "q"]},
              "image_footer": "Educational only", **_D2_WORKER_FIELDS}
    body = {"status": "accepted", "source_ref": "news:ceo_buys:2026-11-09", "template_id": "ceo_buys",
            "script": script}
    once = schemas.ScriptKickResponse.model_validate(body)
    for key, value in _D2_WORKER_FIELDS.items():
        assert getattr(once.script, key) == value, key
    dumped = once.model_dump()["script"]
    assert {k: dumped[k] for k in _D2_WORKER_FIELDS} == _D2_WORKER_FIELDS
    again = schemas.ScriptKickResponse.model_validate_json(once.model_dump_json())
    assert again.model_dump() == once.model_dump()
    assert set(_D2_WORKER_FIELDS) <= set(schemas.WorkerScript.model_fields)


def test_a_lesson_script_without_the_new_fields_keeps_their_absent_defaults():
    minimal = {"hook": "h", "video_script": ["a"], "cards": [], "carousel_slides": [], "disclaimer_card": "d"}
    got = schemas.WorkerScript.model_validate(minimal)
    assert (got.content_class, got.authorship, got.series, got.video_layout, got.opening_card,
            got.image_spec, got.logos) == (None, None, None, None, None, None, [])
    # A mutable default is never shared between two scripts.
    got.logos.append({"key": "X"})
    assert schemas.WorkerScript.model_validate(minimal).logos == []


# ══ Drop 2 (D13): the worker-only error codes ═══════════════════════════════════════════════════════
#
# /list-error-codes, by hand: every MARKETING_* ErrorCode is emitted only to the media worker (or never
# leaves the web process) — iOS never reaches those routes, so none has an AppError branch BY DESIGN.
# The list below is that set, pinned: a new marketing code must be added here deliberately (and to the
# command's "no iOS branch by design" list), and none may grow a dead iOS branch.

WORKER_ONLY_ERROR_CODES = (
    "MARKETING_NOT_FOUND", "MARKETING_ASSET_MISSING", "MARKETING_LEDGER_ERROR", "MARKETING_SCRIPT_NOT_READY",
    "MARKETING_RUN_NOT_HELD", "MARKETING_REQUEST_INVALID", "MARKETING_JUDGE_NOT_ENFORCED",
    "MARKETING_TEMPLATE_REFUSED", "MARKETING_REVIEW_BOT_UNAVAILABLE", "MARKETING_PUBLISHER_UNAVAILABLE",
)


def test_the_marketing_error_codes_are_exactly_the_worker_only_list_and_none_is_mapped_on_ios():
    from pathlib import Path

    from app.api.error_response import _DEFAULT_STATUS, _USER_MESSAGES, ErrorCode

    marketing = sorted(c.value for c in ErrorCode if c.value.startswith("MARKETING_"))
    assert marketing == sorted(WORKER_ONLY_ERROR_CODES)
    for value in WORKER_ONLY_ERROR_CODES:
        code = ErrorCode(value)
        assert code in _USER_MESSAGES and code in _DEFAULT_STATUS
    assert _DEFAULT_STATUS[ErrorCode.MARKETING_TEMPLATE_REFUSED] == 409          # deterministic: never retried
    swift = Path(__file__).resolve().parents[2] / "frontend/ios/ios/Core/Utilities/AppError.swift"
    src = re.sub(r"//[^\n]*", "", swift.read_text(encoding="utf-8"))           # comments may name a code
    assert [v for v in WORKER_ONLY_ERROR_CODES if f'"{v}"' in src] == []
