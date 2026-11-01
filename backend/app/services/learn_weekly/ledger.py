"""The Wiser weekly ledger: every read and write of `learn_weekly_pieces` and `learn_narration_jobs`
(migration 204). Nothing else in the codebase writes those two tables.

WHY IT IS SHAPED THIS WAY
-------------------------
The pipeline runs in the web process's lifespan loop, and a Railway deploy overlaps two containers
for a while — so two writers can read the same row and both decide to act on it. Every state change
here is therefore a COMPARE-AND-SWAP: one conditional UPDATE whose WHERE clause names the status (and
the other columns) the caller observed. Exactly one writer matches; the other gets `None` back and
must re-read, never retry blindly. The patterns are copied from the marketing engine, where each one
was paid for:

  * claims are rows, not clocks (147) — `create_piece` / `enqueue_job` are first-write-wins on the
    UNIQUE period_key / (piece_id, round), and a second caller ADOPTS the first row;
  * a builder holds a piece through `lease_until` + a fresh `generation_id` taken by one
    conditional UPDATE, fenced on the OBSERVED lease_until as well (script_service: an owner whose
    refresh landed between our read and our write is alive and must not be taken over);
  * a narration job's claim bumps `attempts` and mints a fresh `claim_nonce`, and every worker write
    is fenced on both (run_service's caller claim: never fence on attempts alone, never on a value the
    server just read for someone else);
  * a terminal write replayed after a lost response answers with the row as it is and writes nothing.

Two invariants are enforced HERE, not left to callers, because a caller getting them wrong ships a
real problem:
  * a dry-run piece never publishes: every write into `publishing` / `published` is fenced on
    `dry_run = false` (and `rehearsed` on `dry_run = true`); the migration's CHECK is the backstop;
  * Approve and the publish CAS require the review hold to still be open (`within_hold_at`), so a
    stale Telegram button cannot approve an expired piece (plan §1.9 / §1.10).
`transition_problem` is the small table of statuses that may only be entered or left one way; it
is not a full state graph on purpose — the mid-pipeline moves (regenerate, retry narration, next
topic) belong to the modules that own them.

I/O CONTRACT
------------
The Supabase SDK is synchronous, so each operation is a SYNC core (`*_sync`, testable on its own and
usable from scripts) plus an `async` wrapper that runs the whole core — read, decide, conditional
write — in one `asyncio.to_thread` hop. ⚠️ Cancelling a wrapper does NOT stop its thread: a write in
flight can still commit after the cancel. Every write is fenced, so the answer to "did it land?" is
always a re-read, never a second unfenced write.

  * A lost compare-and-swap returns `None` (logged at INFO with every id — it is normal concurrency).
  * A malformed id that came from outside (a Telegram callback, a worker header) returns `None` with
    a WARNING: it cannot match a row, and sending it would only 22P02.
  * A malformed value from our own code (an unknown status, a naive datetime, NaN in a JSON field, an
    unwritable column) raises `ValueError` BEFORE any I/O — a bug, surfaced where it was made.
  * A Supabase failure raises `LearnLedgerError` naming the operation and ids (a 23505 the ledger
    does not adopt — e.g. a slug another piece holds — raises its subclass `LearnLedgerConflict`).
    Reads raise too: a no-repeat window that read nothing must not look like "nothing published".

Every figure that reaches an article comes from SEC filings or the verified case library, never FMP
(contracts.py); this module stores whatever the builder hands it and imports no data source.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import math
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.database import get_supabase
from app.services.learn_weekly import contracts as c
from app.services.learn_weekly.errors import LearnNarratorLedgerError
from app.utils.supabase_errors import is_unique_violation

logger = logging.getLogger(__name__)

PIECES = "learn_weekly_pieces"
JOBS = "learn_narration_jobs"


class LearnLedgerError(LearnNarratorLedgerError):
    """A ledger statement failed (a PostgREST error, a dropped connection, an unreadable answer).
    The message names the operation and the ids, so the failure is diagnosable from the log line
    alone. Subclassing `errors.LearnNarratorLedgerError` is what maps it to
    `LEARN_NARRATOR_LEDGER_ERROR` (503: retryable) — `classify_exception` walks the MRO for the
    classes defined in `learn_weekly.errors`."""


class LearnLedgerConflict(LearnLedgerError):
    """A 23505 the ledger does not adopt: the write named a slug (or another unique value) a
    different row already holds. Never retried as is — the caller picks a new value."""


# ── time ─────────────────────────────────────────────────────────────────────────────────────


def _now() -> datetime:
    """The ledger's clock (indirected so a test can move it)."""
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """UTC, microseconds, `Z` — never `+00:00`: these values also travel as PostgREST FILTER values,
    where a `+` decodes to a space and the request 503s (the notifications-cursor incident)."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _aware(value: Any, name: str) -> datetime:
    """A timezone-aware datetime, or ValueError. A naive one would be stored as if it were UTC
    whatever the caller meant (an ET wall-clock hour would publish 4-5 hours off)."""
    if not isinstance(value, datetime):
        raise ValueError(f"{name} must be a timezone-aware datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got the naive {value.isoformat()}")
    return value


# ── period keys ──────────────────────────────────────────────────────────────────────────────

# Stricter than `contracts.is_period_key`, whose `\d` also matches non-ASCII digits and whose `$`
# matches before a trailing newline — both pass Python and then fail the migration's CHECK as a
# 23514 in production. fullmatch + [0-9] closes both.
_STRICT_DEEP_DIVE = re.compile(r"([0-9]{4})-W([0-9]{2}):deep_dive")
_STRICT_SLOT = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}):slot")


def validate_period_key(key: Any, kind: Optional[str] = None) -> str:
    """`key` if it is a REAL period key (a real ISO week — W53 only in a 53-week year — or a real
    calendar date) whose suffix names `kind` when given; else ValueError."""
    if not isinstance(key, str) or not c.is_period_key(key):
        raise ValueError(f"not a period key: {key!r}")
    m = _STRICT_DEEP_DIVE.fullmatch(key)
    if m:
        found = c.KIND_DEEP_DIVE
        try:
            date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
        except ValueError as e:
            raise ValueError(f"period key {key!r} names no real ISO week ({e})") from e
    else:
        m = _STRICT_SLOT.fullmatch(key)
        if not m:
            raise ValueError(f"period key {key!r} carries non-ASCII digits or trailing characters")
        found = c.KIND_SLOT
        try:
            date.fromisoformat(m.group(1))
        except ValueError as e:
            raise ValueError(f"period key {key!r} names no real date ({e})") from e
    if kind is not None and kind != found:
        raise ValueError(f"period key {key!r} is a {found} key, not {kind!r}")
    return key


# ── values ───────────────────────────────────────────────────────────────────────────────────

_HEX64 = re.compile(r"[0-9a-f]{64}")
_NONCE = re.compile(r"[0-9a-f]{16}")
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# A Storage object path inside the private money-moves-media bucket (`audio/weekly/<slug>-<sha12>.m4a`).
_AUDIO_PATH = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_TICKER = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")

# Diagnostic free text (last_error, reject_reason, a job's error) is folded onto one line and capped
# — it is shown in Telegram and logs, and must never make a write fail. The column CHECKs allow 2000.
TEXT_CAP = 1000
MAX_LEASE_SECONDS = 6 * 3600
MAX_AUDIO_SECONDS = 4 * 3600.0
MAX_AUDIO_BYTES = 1 << 32
_CLAIM_SCAN = 10          # candidate jobs read per claim (one worker, a handful of jobs a week)
_RECENT_LIMIT = 500       # pieces read per no-repeat window (≈ 10 years of weekly pieces)


def _scrub(text: Any, cap: int = TEXT_CAP) -> str:
    """One line, no control characters, at most `cap` characters."""
    s = " ".join(_CONTROL.sub(" ", str(text)).split())
    return s if len(s) <= cap else s[: cap - 1] + "…"


def _int_or_none(value: Any) -> Optional[int]:
    """A stored integer column, or None when unreadable (never a bool, never a fraction)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _counter(row: Mapping[str, Any], col: str, *, op: str, rid: Any) -> int:
    value = _int_or_none(row.get(col))
    if value is None or value < 0:
        raise LearnLedgerError(f"{op}: row {rid} holds an unreadable {col}={row.get(col)!r}")
    return value


def _uuid_or_none(value: Any) -> Optional[str]:
    if isinstance(value, uuid.UUID):
        return str(value)
    if not isinstance(value, str):
        return None
    try:
        return str(uuid.UUID(value))
    except ValueError:
        return None


def _require_uuid(value: Any, name: str) -> str:
    out = _uuid_or_none(value)
    if out is None:
        raise ValueError(f"{name} must be a UUID, got {value!r}")
    return out


def _nonneg_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative int, got {value!r}")
    return value


def _seconds(value: Any, name: str, *, upper: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number of seconds, got {value!r}")
    v = float(value)
    if not math.isfinite(v) or v <= 0 or v > upper:
        raise ValueError(f"{name} must be in (0, {upper:g}] seconds, got {value!r}")
    return v


def _json_safe(value: Any, name: str) -> Any:
    """`value` if PostgREST can send it: JSON-serialisable with no NaN / inf (`json.dumps` would
    write the bare token `NaN`, which Postgres refuses as invalid JSON — a prod-only 22P02)."""
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} is not storable JSON ({type(e).__name__}: {e})") from e
    return value


def _status_tuple(values: Any, allowed: Sequence[str], name: str) -> Tuple[str, ...]:
    if isinstance(values, str):
        values = (values,)
    try:
        out = tuple(dict.fromkeys(values))
    except TypeError as e:
        raise ValueError(f"{name} must be a status or a sequence of statuses, got {values!r}") from e
    if not out:
        raise ValueError(f"{name} is empty")
    bad = [s for s in out if s not in allowed]
    if bad:
        raise ValueError(f"{name}: unknown status(es) {bad!r}")
    return out


# ── statement plumbing ───────────────────────────────────────────────────────────────────────


def _client(sb: Any) -> Any:
    return sb if sb is not None else get_supabase()


def _exec(query: Any, *, op: str, **ids: Any) -> Any:
    """`query.execute()` with every failure re-raised as a `LearnLedgerError` carrying the operation
    and ids — never a raw postgrest `APIError`, which the endpoint classifier would report as a
    generic failure with no idea which piece or job it was about."""
    try:
        return query.execute()
    except Exception as e:
        ctx = ", ".join(f"{k}={v}" for k, v in ids.items())
        if is_unique_violation(e):
            raise LearnLedgerConflict(
                f"{op} refused by a unique constraint ({ctx}): {type(e).__name__}: {e}") from e
        raise LearnLedgerError(f"{op} failed ({ctx}): {type(e).__name__}: {e}") from e


def _rows(result: Any, *, op: str) -> List[Dict[str, Any]]:
    data = getattr(result, "data", None)
    if data is None:
        return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise LearnLedgerError(f"{op}: unreadable answer of type {type(data).__name__}")
    bad = [r for r in data if not isinstance(r, dict)]
    if bad:
        raise LearnLedgerError(f"{op}: {len(bad)} unreadable row(s) in the answer")
    return data


def _one(result: Any, *, op: str) -> Optional[Dict[str, Any]]:
    rows = _rows(result, op=op)
    return rows[0] if rows else None


# ── piece columns ────────────────────────────────────────────────────────────────────────────

_PIECE_TS = frozenset({
    "scheduled_publish_at", "hold_until", "lease_until", "reviewed_at", "publish_lease_until",
    "published_at", "unpublished_at", "created_at", "updated_at",
})
_PIECE_INTS = frozenset({
    "generations", "content_rejections", "writer_failures", "build_attempts", "tokens_used",
    "review_round",
})
_PIECE_OBJ_NULLABLE = frozenset({"topic", "fact_sheet", "draft", "validation"})
_PIECE_SHA = frozenset({"fact_sheet_sha256", "draft_sha256"})

PIECE_COLUMNS: FrozenSet[str] = frozenset({
    "id", "period_key", "kind", "category", "scheduled_publish_at", "hold_until", "dry_run",
    "status", "generation_id", "lease_until", "generations", "content_rejections",
    "writer_failures", "build_attempts", "progress", "topic", "subjects", "fact_sheet",
    "fact_sheet_sha256", "slug", "title", "draft", "draft_sha256", "validation", "tokens_used",
    "narration_job_id", "review_round", "review_message_ids", "reviewed_by", "reviewed_at",
    "reject_reason", "publish_lease_until", "published_at", "unpublished_at", "last_error",
    "last_stage", "created_at", "updated_at",
})
#: What `transition(fields=…)` may write. Never: the identity (id, period_key, kind), `status` (it
#: is `to_status`), `dry_run` (frozen at creation — flipping it would let a rehearsal publish),
#: `generation_id` (only `acquire_lease` takes a generation), or the stamps the ledger owns.
PIECE_WRITABLE: FrozenSet[str] = PIECE_COLUMNS - {
    "id", "period_key", "kind", "status", "dry_run", "generation_id", "created_at", "updated_at",
}
#: What a `fence` may name. `status` is fenced through `from_statuses`, never here.
PIECE_FENCEABLE: FrozenSet[str] = PIECE_COLUMNS - {"status"}
#: The counters `bump` may increment (read-modify-CAS on the observed value).
BUMPABLE: Tuple[str, ...] = ("build_attempts", "generations", "content_rejections", "writer_failures")
#: What the no-repeat windows count by default: pieces the owner approved or that went live.
RECENT_STATUSES: Tuple[str, ...] = (c.APPROVED, c.PUBLISHING, c.PUBLISHED)

#: Every column of `learn_narration_jobs` (pinned equal to migration 204 by the ledger test).
JOB_COLUMNS: FrozenSet[str] = frozenset({
    "id", "piece_id", "round", "script", "script_sha256", "engine", "voice", "pipeline_version",
    "status", "attempts", "worker_failures", "lease_reclaims", "claim_nonce", "claimed_at",
    "heartbeat_at", "lease_until", "audio_path", "audio_bytes", "duration_seconds", "timings",
    "checks", "error", "finished_at", "created_at", "updated_at",
})
_JOB_TS = frozenset({"claimed_at", "heartbeat_at", "lease_until", "finished_at", "created_at",
                     "updated_at"})
_TS_COLUMNS = _PIECE_TS | _JOB_TS


def _validate_piece_fields(fields: Mapping[str, Any]) -> Dict[str, Any]:
    """The normalised patch for `fields`, or ValueError naming the first bad column."""
    if not isinstance(fields, Mapping):
        raise ValueError(f"fields must be a mapping, got {type(fields).__name__}")
    unknown = sorted(set(fields) - PIECE_WRITABLE)
    if unknown:
        raise ValueError(f"not writable on {PIECES}: {unknown}")
    out: Dict[str, Any] = {}
    for col, v in fields.items():
        if col in _PIECE_TS:
            out[col] = None if v is None else _iso(_aware(v, col))
        elif col in _PIECE_INTS:
            out[col] = _nonneg_int(v, col)
        elif col == "progress":
            if not isinstance(v, dict):
                raise ValueError(f"progress must be an object, got {type(v).__name__}")
            out[col] = _json_safe(v, col)
        elif col in _PIECE_OBJ_NULLABLE:
            if v is not None and not isinstance(v, dict):
                raise ValueError(f"{col} must be an object or None, got {type(v).__name__}")
            out[col] = None if v is None else _json_safe(v, col)
        elif col == "subjects":
            if not isinstance(v, list) or not all(isinstance(s, dict) for s in v):
                raise ValueError("subjects must be a list of objects (contracts.Subject dicts)")
            out[col] = _json_safe(v, col)
        elif col == "review_message_ids":
            if not isinstance(v, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in v):
                raise ValueError("review_message_ids must be a list of int Telegram message ids")
            out[col] = v
        elif col in _PIECE_SHA:
            if v is not None and not (isinstance(v, str) and _HEX64.fullmatch(v)):
                raise ValueError(f"{col} must be 64 lowercase hex characters or None, got {v!r}")
            out[col] = v
        elif col == "slug":
            if v is not None and not (isinstance(v, str) and len(v) <= 160 and _SLUG.fullmatch(v)):
                raise ValueError(f"slug must be lowercase kebab-case (<= 160 chars) or None, got {v!r}")
            out[col] = v
        elif col == "title":
            if v is not None and not (isinstance(v, str) and v.strip() and len(v) <= 300
                                      and not _CONTROL.search(v)):
                raise ValueError(f"title must be 1-300 characters on one line or None, got {v!r}")
            out[col] = v
        elif col == "category":
            if v is not None and v not in c.CATEGORIES:
                raise ValueError(f"category must be one of {c.CATEGORIES} or None, got {v!r}")
            out[col] = v
        elif col == "narration_job_id":
            out[col] = None if v is None else _require_uuid(v, col)
        elif col == "reviewed_by":
            if v is not None and not (isinstance(v, str) and 0 < len(v) <= 200 and not _CONTROL.search(v)):
                raise ValueError(f"reviewed_by must be 1-200 characters on one line or None, got {v!r}")
            out[col] = v
        elif col == "last_stage":
            if v is not None and not (isinstance(v, str) and 0 < len(v) <= 100 and not _CONTROL.search(v)):
                raise ValueError(f"last_stage must be 1-100 characters on one line or None, got {v!r}")
            out[col] = v
        elif col in ("last_error", "reject_reason"):
            out[col] = None if v is None else _scrub(v)
        else:  # pragma: no cover — every PIECE_WRITABLE column has a branch above
            raise ValueError(f"no validator for {col}")
    sched, hold = out.get("scheduled_publish_at"), out.get("hold_until")
    if sched and hold and _parse_ts(hold) < _parse_ts(sched):
        raise ValueError(f"hold_until {hold} is before scheduled_publish_at {sched}")
    return out


def _filter_value(col: str, value: Any) -> Optional[str]:
    """`value` rendered for a PostgREST equality filter; None means `IS NULL`."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        return _iso(_aware(value, col))
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"fence {col}: a non-finite number cannot be compared")
        return repr(value)
    if isinstance(value, str):
        if col in _TS_COLUMNS:
            parsed = _parse_ts(value)
            if parsed is None:
                raise ValueError(f"fence {col}: unreadable timestamp {value!r}")
            return _iso(parsed)
        return value
    if isinstance(value, (list, dict)):
        # jsonb equality is semantic, so compact JSON compares equal to the stored document.
        return json.dumps(_json_safe(value, col), separators=(",", ":"))
    raise ValueError(f"fence {col}: cannot compare a {type(value).__name__}")


def _validate_fence(fence: Mapping[str, Any], allowed: FrozenSet[str], table: str) -> Dict[str, Any]:
    if not isinstance(fence, Mapping):
        raise ValueError(f"fence must be a mapping, got {type(fence).__name__}")
    unknown = sorted(set(fence) - allowed)
    if unknown:
        raise ValueError(f"cannot fence {table} on {unknown}")
    for col, v in fence.items():
        _filter_value(col, v)  # raises on an uncomparable value, before any I/O
    return dict(fence)


def _apply_fence(query: Any, fence: Mapping[str, Any]) -> Any:
    for col, value in fence.items():
        rendered = _filter_value(col, value)
        query = query.is_(col, "null") if rendered is None else query.eq(col, rendered)
    return query


def _same(col: str, stored: Any, wanted: Any) -> bool:
    """Does a stored column value equal a fence value, as Postgres would compare them?"""
    if wanted is None:
        return stored is None
    if stored is None:
        return False
    if col in _TS_COLUMNS:
        a, b = _parse_ts(stored), _parse_ts(wanted)
        return a is not None and b is not None and a == b
    if isinstance(wanted, bool):
        return stored is wanted or str(stored).lower() == ("true" if wanted else "false")
    if isinstance(wanted, (list, dict)):
        return stored == wanted
    return str(stored) == _filter_value(col, wanted)


def _fence_matches(row: Mapping[str, Any], fence: Mapping[str, Any]) -> bool:
    return all(_same(col, row.get(col), v) for col, v in fence.items())


# ── status rules ─────────────────────────────────────────────────────────────────────────────

_LIVE = frozenset({c.PUBLISHING, c.PUBLISHED})

#: A status that may be ENTERED only from these (itself included, for an annotating write).
_ONLY_FROM: Mapping[str, FrozenSet[str]] = {
    # `planned` is the build queue. It is entered from: a rebuild of a terminal piece
    # (REBUILDABLE); the owner's topic pick or the auto-pick (topic_pending); a build that gives
    # the lease back for a retry (building); and Regenerate / Next topic, which send a reviewed
    # piece back to be rewritten (drafted / media_pending / ready_for_review / in_review).
    c.PLANNED: frozenset(c.REBUILDABLE_STATUSES | {
        c.PLANNED, c.TOPIC_PENDING, c.BUILDING, c.DRAFTED, c.MEDIA_PENDING,
        c.READY_FOR_REVIEW, c.IN_REVIEW,
    }),
    c.APPROVED: frozenset({c.IN_REVIEW, c.APPROVED}),
    c.PUBLISHING: frozenset({c.APPROVED, c.PUBLISHING}),
    c.PUBLISHED: frozenset({c.PUBLISHING, c.PUBLISHED}),
    c.UNPUBLISHED: frozenset({c.PUBLISHED, c.UNPUBLISHED}),
    c.REHEARSED: frozenset({c.APPROVED, c.REHEARSED}),
    c.OWNER_REJECTED: frozenset({c.READY_FOR_REVIEW, c.IN_REVIEW, c.APPROVED, c.OWNER_REJECTED}),
    # Expiry never takes `publishing` as an ordinary expiry — its row may already be live — but the
    # publishing RECOVERY ends there when the row does not exist and the hold has closed (§1.10).
    c.EXPIRED: frozenset(c.EXPIRABLE_STATUSES | {c.PUBLISHING, c.EXPIRED}),
}
#: A status that may be LEFT only for these.
_ONLY_TO: Mapping[str, FrozenSet[str]] = {
    c.PUBLISHING: frozenset({c.PUBLISHING, c.PUBLISHED, c.EXPIRED}),
    c.PUBLISHED: frozenset({c.PUBLISHED, c.UNPUBLISHED}),
    **{t: frozenset({t} | ({c.PLANNED} if t in c.REBUILDABLE_STATUSES else set()))
       for t in c.TERMINAL_STATUSES},
}


def transition_problem(from_statuses: Iterable[str], to_status: str) -> Optional[str]:
    """Why moving a piece from any of `from_statuses` to `to_status` is refused, or None.

    The rules (each one a defect it prevents):
      * a terminal status is left only by a rebuild back to `planned` (and only from
        REBUILDABLE_STATUSES); `rehearsed` and `unpublished` are never left;
      * `published` is entered only from `publishing`, `publishing` only from `approved`, and
        `approved` only from `in_review` — nothing skips the owner's tap;
      * `publishing` is left only for `published` / `expired` (its row may be live), `published`
        only for `unpublished`;
      * `rehearsed` (the dry-run end) only from `approved`.
    """
    froms = tuple(from_statuses)
    if to_status not in c.PIECE_STATUSES:
        return f"unknown to_status {to_status!r}"
    for f in froms:
        if f not in c.PIECE_STATUSES:
            return f"unknown from status {f!r}"
        allowed_from = _ONLY_FROM.get(to_status)
        if allowed_from is not None and f not in allowed_from:
            return f"{to_status!r} cannot be entered from {f!r} (only from {sorted(allowed_from)})"
        allowed_to = _ONLY_TO.get(f)
        if allowed_to is not None and to_status not in allowed_to:
            return f"{f!r} cannot be left for {to_status!r} (only for {sorted(allowed_to)})"
    return None


def _dry_run_fence(to_status: str) -> Optional[bool]:
    """The `dry_run` value a write into `to_status` must find: a rehearsal never goes live."""
    if to_status in _LIVE:
        return False
    if to_status == c.REHEARSED:
        return True
    return None


# ── pieces: reads ────────────────────────────────────────────────────────────────────────────


def get_piece_sync(period_key: str, *, sb: Any = None) -> Optional[Dict[str, Any]]:
    """The piece for `period_key`, or None. ValueError on a malformed key (our own code built it)."""
    key = validate_period_key(period_key)
    return _one(_exec(_client(sb).table(PIECES).select("*").eq("period_key", key).limit(1),
                      op="get_piece", period_key=key), op="get_piece")


def get_piece_by_id_sync(piece_id: Any, *, sb: Any = None) -> Optional[Dict[str, Any]]:
    """The piece with `piece_id`, or None (a malformed id — e.g. from a tampered callback — is None
    with a WARNING, never a query)."""
    pid = _uuid_or_none(piece_id)
    if pid is None:
        logger.warning("learn ledger get_piece_by_id: %r is not a UUID — no such piece", piece_id)
        return None
    return _one(_exec(_client(sb).table(PIECES).select("*").eq("id", pid).limit(1),
                      op="get_piece_by_id", piece_id=pid), op="get_piece_by_id")


def list_pieces_sync(*, statuses: Optional[Iterable[str]] = None, kind: Optional[str] = None,
                     limit: int = 50, sb: Any = None) -> List[Dict[str, Any]]:
    """Pieces in `statuses` (all when None) of `kind` (both when None), oldest due first — the
    order the every-tick sweeps (review send, publish, expiry) work in."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
        raise ValueError(f"limit must be an int in 1..1000, got {limit!r}")
    if kind is not None and kind not in c.KINDS:
        raise ValueError(f"kind must be one of {c.KINDS} or None, got {kind!r}")
    query = _client(sb).table(PIECES).select("*")
    if statuses is not None:
        query = query.in_("status", list(_status_tuple(statuses, c.PIECE_STATUSES, "statuses")))
    if kind is not None:
        query = query.eq("kind", kind)
    query = query.order("scheduled_publish_at").order("id").limit(limit)
    return _rows(_exec(query, op="list_pieces", statuses=statuses, kind=kind), op="list_pieces")


@dataclass(frozen=True)
class RecentSubjects:
    """What the no-repeat windows must not pick again (plan §1.5: theme ≥ 4 weeks, company ≥ 12)."""

    themes: FrozenSet[str]
    tickers: FrozenSet[str]
    ciks: FrozenSet[str]
    period_keys: Tuple[str, ...]


def _subject_ids(entries: Any, *, period_key: Any) -> Tuple[set, set]:
    tickers: set = set()
    ciks: set = set()
    if entries is None:
        return tickers, ciks
    if not isinstance(entries, list):
        logger.warning("learn ledger recent_subjects: piece %s carries subjects of type %s — skipped",
                       period_key, type(entries).__name__)
        return tickers, ciks
    for s in entries:
        if not isinstance(s, dict):
            logger.warning("learn ledger recent_subjects: piece %s carries a non-object subject %r — "
                           "skipped", period_key, s)
            continue
        t = s.get("ticker")
        if isinstance(t, str) and t.strip():
            t = t.strip().upper()
            if _TICKER.fullmatch(t):
                tickers.add(t)
            else:
                logger.warning("learn ledger recent_subjects: piece %s carries an unreadable ticker %r — "
                               "skipped", period_key, s.get("ticker"))
        cik = s.get("cik")
        if cik is None or (isinstance(cik, str) and not cik.strip()):
            continue
        # SEC gives a CIK as digits; a bool, a float or a negative number is not one.
        text = "" if isinstance(cik, bool) or not isinstance(cik, (str, int)) else str(cik).strip()
        if text.isascii() and text.isdigit() and len(text) <= 10 and int(text) > 0:
            ciks.add(text.zfill(10))
        else:
            logger.warning("learn ledger recent_subjects: piece %s carries an unreadable cik %r — "
                           "skipped", period_key, cik)
    return tickers, ciks


def recent_subjects_sync(kind: str, since: datetime, *, statuses: Iterable[str] = RECENT_STATUSES,
                         sb: Any = None) -> RecentSubjects:
    """Themes, tickers and CIKs of the `kind` pieces in `statuses` (approved / publishing / published
    by default) scheduled at or after `since`.

    Read from `fact_sheet.theme_slug` (falling back to `topic.theme_slug`) and from the `subjects`
    column plus `fact_sheet.subjects` — each a list of `contracts.Subject` dicts. A malformed entry
    is skipped with a WARNING naming the piece; a failed READ raises `LearnLedgerError` (a window
    that read nothing must not look like "nothing to avoid")."""
    if kind not in c.KINDS:
        raise ValueError(f"kind must be one of {c.KINDS}, got {kind!r}")
    since_iso = _iso(_aware(since, "since"))
    wanted = _status_tuple(statuses, c.PIECE_STATUSES, "statuses")
    rows = _rows(_exec(
        _client(sb).table(PIECES)
        .select("period_key,status,scheduled_publish_at,fact_sheet,subjects,topic")
        .eq("kind", kind).in_("status", list(wanted)).gte("scheduled_publish_at", since_iso)
        .order("scheduled_publish_at", desc=True).limit(_RECENT_LIMIT),
        op="recent_subjects", kind=kind, since=since_iso), op="recent_subjects")
    if len(rows) >= _RECENT_LIMIT:
        logger.warning("learn ledger recent_subjects: %d %s pieces since %s — the window was CUT at "
                       "the read limit; older subjects may repeat", len(rows), kind, since_iso)
    themes: set = set()
    tickers: set = set()
    ciks: set = set()
    keys: List[str] = []
    for r in rows:
        pk = r.get("period_key")
        keys.append(str(pk))
        sheet = r.get("fact_sheet") if isinstance(r.get("fact_sheet"), dict) else {}
        topic = r.get("topic") if isinstance(r.get("topic"), dict) else {}
        theme = sheet.get("theme_slug") or topic.get("theme_slug")
        if isinstance(theme, str) and theme.strip():
            themes.add(theme.strip().lower())
        elif theme is not None:
            logger.warning("learn ledger recent_subjects: piece %s carries an unreadable theme %r — "
                           "skipped", pk, theme)
        for entries in (r.get("subjects"), sheet.get("subjects")):
            t, k = _subject_ids(entries, period_key=pk)
            tickers |= t
            ciks |= k
    return RecentSubjects(frozenset(themes), frozenset(tickers), frozenset(ciks), tuple(keys))


def subjects_json(subjects: Iterable[c.Subject]) -> List[Dict[str, Any]]:
    """The `subjects` column value for a fact sheet's subjects (what `recent_subjects` reads)."""
    return [{"name": s.name, "cik": s.cik, "ticker": s.ticker} for s in subjects]


# ── pieces: writes ───────────────────────────────────────────────────────────────────────────


def create_piece_sync(period_key: str, kind: str, *, scheduled_publish_at: datetime,
                      hold_until: datetime, dry_run: bool, category: Optional[str] = None,
                      sb: Any = None) -> Optional[Dict[str, Any]]:
    """Claim the period: INSERT … ON CONFLICT (period_key) DO NOTHING, then read the row back. The
    FIRST write wins — a second caller (an overlapping container, a retried tick) gets the existing
    row, whose frozen fields (`dry_run`, the schedule) are NEVER rewritten. None only when the
    read-back finds nothing (logged at ERROR)."""
    key = validate_period_key(period_key, kind)
    if not isinstance(dry_run, bool):
        raise ValueError(f"dry_run must be a bool, got {dry_run!r}")
    sched = _aware(scheduled_publish_at, "scheduled_publish_at")
    hold = _aware(hold_until, "hold_until")
    if hold < sched:
        raise ValueError(f"hold_until {hold.isoformat()} is before scheduled_publish_at {sched.isoformat()}")
    if category is not None and category not in c.CATEGORIES:
        raise ValueError(f"category must be one of {c.CATEGORIES} or None, got {category!r}")
    client = _client(sb)
    row = {
        "period_key": key, "kind": kind, "category": category,
        "scheduled_publish_at": _iso(sched), "hold_until": _iso(hold), "dry_run": dry_run,
        "status": c.PLANNED,
    }
    inserted = _rows(_exec(client.table(PIECES).upsert(row, on_conflict="period_key",
                                                       ignore_duplicates=True),
                           op="create_piece", period_key=key), op="create_piece")
    existing = get_piece_sync(key, sb=client)
    if existing is None:
        logger.error("learn ledger create_piece: period %s was inserted-or-ignored but reads back as "
                     "MISSING — a concurrent delete or a read on a lagging replica", key)
        return None
    if inserted:
        logger.info("learn ledger: created piece %s (%s, id=%s, dry_run=%s, publish %s, hold %s)",
                    key, kind, existing.get("id"), dry_run, row["scheduled_publish_at"], row["hold_until"])
        return existing
    drift = [col for col, want in (("kind", kind), ("dry_run", dry_run), ("category", category),
                                   ("scheduled_publish_at", sched), ("hold_until", hold))
             if not _same(col, existing.get(col), want)]
    if drift:
        logger.warning("learn ledger create_piece: period %s already exists (id=%s, status=%s) with "
                       "different %s — first write wins, nothing rewritten", key, existing.get("id"),
                       existing.get("status"), drift)
    else:
        logger.info("learn ledger create_piece: period %s already exists (id=%s, status=%s) — adopted",
                    key, existing.get("id"), existing.get("status"))
    return existing


def _log_lost(op: str, client: Any, piece_id: str, detail: str) -> None:
    """A lost compare-and-swap is normal concurrency — but the log line must say who holds the row
    now, or a stuck piece is undiagnosable. The describing read is best-effort."""
    try:
        cur = _one(client.table(PIECES).select("status,generation_id,lease_until,updated_at")
                   .eq("id", piece_id).limit(1).execute(), op=op)
    except Exception as e:  # never let the diagnostic read mask the real answer (None)
        logger.info("learn ledger %s: lost the compare-and-swap on piece %s (%s); describing it "
                    "failed (%s: %s)", op, piece_id, detail, type(e).__name__, e)
        return
    if cur is None:
        logger.info("learn ledger %s: piece %s does not exist (%s)", op, piece_id, detail)
    else:
        logger.info("learn ledger %s: lost the compare-and-swap on piece %s (%s) — it is now status=%s "
                    "generation=%s lease_until=%s updated_at=%s", op, piece_id, detail, cur.get("status"),
                    cur.get("generation_id"), cur.get("lease_until"), cur.get("updated_at"))


def transition_sync(piece_id: Any, *, from_statuses: Iterable[str], to_status: str,
                    fields: Optional[Mapping[str, Any]] = None,
                    fence: Optional[Mapping[str, Any]] = None,
                    within_hold_at: Optional[datetime] = None, due_at: Optional[datetime] = None,
                    sb: Any = None) -> Optional[Dict[str, Any]]:
    """Move a piece to `to_status` — one conditional UPDATE matching only while its status is one of
    `from_statuses` AND every `fence` column equals its value (None = IS NULL). Returns the updated
    row, or None when another writer got there first (or the id names no piece).

    `fields` are columns from `PIECE_WRITABLE`; `updated_at` is stamped here.
    `within_hold_at=now` also requires `hold_until > now` — REQUIRED to enter `approved` (a stale
    button cannot approve an expired piece) and `publishing`; `due_at=now` requires
    `scheduled_publish_at <= now` — REQUIRED to enter `publishing`.
    A write into `publishing` / `published` is additionally fenced on `dry_run = false`, and into
    `rehearsed` on `dry_run = true`: a rehearsal can never go live, whatever the caller passed."""
    froms = _status_tuple(from_statuses, c.PIECE_STATUSES, "from_statuses")
    problem = transition_problem(froms, to_status)
    if problem:
        raise ValueError(f"learn ledger transition refused: {problem}")
    # ENTERING (from another status) — an annotating write on an already-approved piece is not an
    # approval and may land after the hold.
    entering = any(f != to_status for f in froms)
    if entering and to_status in (c.APPROVED, c.PUBLISHING) and within_hold_at is None:
        raise ValueError(f"entering {to_status!r} requires within_hold_at (the hold must still be open)")
    if entering and to_status == c.PUBLISHING and due_at is None:
        raise ValueError("entering 'publishing' requires due_at (scheduled_publish_at must have come)")
    patch = _validate_piece_fields(fields or {})
    fence_n = _validate_fence(fence or {}, PIECE_FENCEABLE, PIECES)
    dry = _dry_run_fence(to_status)
    if dry is not None:
        if "dry_run" in fence_n and not _same("dry_run", dry, fence_n["dry_run"]):
            raise ValueError(f"entering {to_status!r} needs dry_run={dry}, but the fence says "
                             f"dry_run={fence_n['dry_run']!r}")
        fence_n["dry_run"] = dry
    hold_iso = None if within_hold_at is None else _iso(_aware(within_hold_at, "within_hold_at"))
    due_iso = None if due_at is None else _iso(_aware(due_at, "due_at"))
    pid = _uuid_or_none(piece_id)
    if pid is None:
        logger.warning("learn ledger transition → %s: %r is not a UUID — nothing written", to_status, piece_id)
        return None
    client = _client(sb)
    patch["status"] = to_status
    patch["updated_at"] = _iso(_now())
    query = client.table(PIECES).update(patch).eq("id", pid).in_("status", list(froms))
    query = _apply_fence(query, fence_n)
    if hold_iso is not None:
        query = query.gt("hold_until", hold_iso)
    if due_iso is not None:
        query = query.lte("scheduled_publish_at", due_iso)
    row = _one(_exec(query, op="transition", piece_id=pid, to=to_status), op="transition")
    if row is None:
        _log_lost("transition", client, pid,
                  f"wanted {list(froms)} → {to_status}, fence {sorted(fence_n)}"
                  + (f", hold open at {hold_iso}" if hold_iso else "")
                  + (f", due at {due_iso}" if due_iso else ""))
        return None
    logger.info("learn ledger: piece %s (%s) → %s%s", pid, row.get("period_key"), to_status,
                f" [{', '.join(sorted(k for k in patch if k not in ('status', 'updated_at')))}]"
                if len(patch) > 2 else "")
    return row


def promote_rehearsed_sync(piece_id: Any, *, review_round: int, old_draft_sha256: str,
                           narration_job_id: Any, observed_updated_at: Any, draft: Mapping[str, Any],
                           draft_sha256: str, scheduled_publish_at: datetime, hold_until: datetime,
                           validation: Mapping[str, Any], now: datetime,
                           sb: Any = None) -> Optional[Dict[str, Any]]:
    """The ONE way a `rehearsed` piece goes live: rehearsed → approved with `dry_run` false, a new
    draft and a new schedule — for the owner's 2026-11-01 decision to publish the W44 rehearsal
    (`scripts/learn_weekly_promote.py`). Deliberately OUTSIDE `transition()`, so "rehearsed is never
    left" and "dry_run is frozen" still hold for every other caller. The owner approved this piece
    in review; the deployed publisher then takes it live on its next due tick, unchanged.

    One conditional UPDATE fenced on status = rehearsed, dry_run = true, the observed review_round,
    draft_sha256, narration_job_id and updated_at. Arguments are validated before any I/O (a
    schedule not after `now`, or a hold before the schedule, is refused). Returns the row, or None
    when the piece moved on (logged)."""
    pid = _uuid_or_none(piece_id)
    if pid is None:
        raise ValueError(f"promote: piece_id {piece_id!r} is not a UUID")
    rnd = _nonneg_int(review_round, "review_round")
    if not (isinstance(old_draft_sha256, str) and _HEX64.fullmatch(old_draft_sha256)):
        raise ValueError("promote: old_draft_sha256 must be 64 lowercase hex characters")
    jid = _require_uuid(narration_job_id, "narration_job_id")
    observed = _parse_ts(observed_updated_at)
    if observed is None:
        raise ValueError(f"promote: observed_updated_at {observed_updated_at!r} is not a timestamp")
    for name, value in (("draft", draft), ("validation", validation)):
        if not isinstance(value, Mapping):
            raise ValueError(f"promote: {name} must be an object, got {type(value).__name__}")
    at = _aware(now, "now")
    if _aware(scheduled_publish_at, "scheduled_publish_at") <= at:
        raise ValueError("promote: scheduled_publish_at must be after now")
    patch = _validate_piece_fields({
        "draft": dict(draft), "draft_sha256": draft_sha256, "scheduled_publish_at": scheduled_publish_at,
        "hold_until": hold_until, "validation": dict(validation), "publish_lease_until": None,
        "last_stage": "promote", "last_error": None,
    })
    if patch["draft_sha256"] == old_draft_sha256:
        raise ValueError("promote: the new draft hashes the same as the rehearsed one — nothing to promote")
    patch.update({"status": c.APPROVED, "dry_run": False, "updated_at": _iso(_now())})
    client = _client(sb)
    query = (client.table(PIECES).update(patch).eq("id", pid).eq("status", c.REHEARSED).eq("dry_run", "true")
             .eq("review_round", str(rnd)).eq("draft_sha256", old_draft_sha256)
             .eq("narration_job_id", jid).eq("updated_at", _iso(observed)))
    row = _one(_exec(query, op="promote_rehearsed", piece_id=pid), op="promote_rehearsed")
    if row is None:
        _log_lost("promote_rehearsed", client, pid, "wanted rehearsed (dry run) → approved (live)")
        return None
    logger.warning("learn ledger: piece %s (%s) PROMOTED from rehearsal → approved, live at %s",
                   pid, row.get("period_key"), row.get("scheduled_publish_at"))
    return row


_NO_LEASE_STATUSES = frozenset(c.TERMINAL_STATUSES | _LIVE)


def acquire_lease_sync(piece_id: Any, *, generation_id: Any, lease_seconds: float,
                       from_statuses: Iterable[str], to_status: Optional[str] = None,
                       now: Optional[datetime] = None, sb: Any = None) -> Optional[Dict[str, Any]]:
    """Take the piece for ONE generation, or return None if it is not ours to take.

    Reads the row; refuses (None) when its status is not in `from_statuses` or another generation's
    lease is still running; otherwise ONE conditional UPDATE sets the new `generation_id`, a lease of
    `lease_seconds` and `generations + 1` (a generation that dies before writing anything still
    counts), optionally moving the status to `to_status` in the same write. The UPDATE is fenced on
    the observed status, generation_id, generations AND lease_until — an owner whose refresh landed
    between our read and this write is alive and must not be taken over.

    A repeated acquire by the SAME generation whose lease is still running (a retried call whose
    first answer was lost) returns the row as it is: it is already ours."""
    gen = _require_uuid(generation_id, "generation_id")
    secs = _seconds(lease_seconds, "lease_seconds", upper=MAX_LEASE_SECONDS)
    froms = _status_tuple(from_statuses, c.PIECE_STATUSES, "from_statuses")
    leased = [s for s in froms if s in _NO_LEASE_STATUSES]
    if leased:
        raise ValueError(f"a build lease is meaningless on {leased} (terminal or live)")
    if to_status is not None:
        if to_status in _NO_LEASE_STATUSES:
            raise ValueError(f"acquire_lease cannot move a piece to {to_status!r}")
        problem = transition_problem(froms, to_status)
        if problem:
            raise ValueError(f"learn ledger acquire_lease refused: {problem}")
    at = _aware(now, "now") if now is not None else _now()
    pid = _uuid_or_none(piece_id)
    if pid is None:
        logger.warning("learn ledger acquire_lease: %r is not a UUID — nothing taken", piece_id)
        return None
    client = _client(sb)
    row = get_piece_by_id_sync(pid, sb=client)
    if row is None:
        logger.warning("learn ledger acquire_lease: piece %s does not exist", pid)
        return None
    status = row.get("status")
    lease = _parse_ts(row.get("lease_until"))
    live = lease is not None and lease > at
    if live and str(row.get("generation_id")) == gen and (status in froms or status == to_status):
        logger.info("learn ledger acquire_lease: piece %s is already held by generation %s until %s — "
                    "a replayed acquire", pid, gen, row.get("lease_until"))
        return row
    if status not in froms:
        logger.info("learn ledger acquire_lease: piece %s is %s, not one of %s — not taken",
                    pid, status, list(froms))
        return None
    if live:
        logger.info("learn ledger acquire_lease: piece %s is held by generation %s until %s — not taken",
                    pid, row.get("generation_id"), row.get("lease_until"))
        return None
    generations = _counter(row, "generations", op="acquire_lease", rid=pid)
    patch: Dict[str, Any] = {
        "generation_id": gen, "lease_until": _iso(at + timedelta(seconds=secs)),
        "generations": generations + 1, "updated_at": _iso(_now()),
    }
    if to_status is not None:
        patch["status"] = to_status
    query = client.table(PIECES).update(patch).eq("id", pid).eq("status", status)
    query = _apply_fence(query, {"generation_id": row.get("generation_id"),
                                 "lease_until": row.get("lease_until"),
                                 "generations": generations})
    taken = _one(_exec(query, op="acquire_lease", piece_id=pid, generation=gen), op="acquire_lease")
    if taken is None:
        _log_lost("acquire_lease", client, pid, f"generation {gen} from status {status}")
        return None
    logger.info("learn ledger: generation %s took piece %s (%s, %s%s, generation #%d) until %s",
                gen, pid, row.get("period_key"), status, f" → {to_status}" if to_status else "",
                generations + 1, patch["lease_until"])
    return taken


def refresh_lease_sync(piece_id: Any, *, generation_id: Any, lease_seconds: float,
                       now: Optional[datetime] = None, sb: Any = None) -> Optional[Dict[str, Any]]:
    """Extend `generation_id`'s lease (before every model call). None = the lease was lost — taken
    over, released, or the piece left the build (terminal / live): the generation must stop."""
    gen = _require_uuid(generation_id, "generation_id")
    secs = _seconds(lease_seconds, "lease_seconds", upper=MAX_LEASE_SECONDS)
    at = _aware(now, "now") if now is not None else _now()
    pid = _uuid_or_none(piece_id)
    if pid is None:
        logger.warning("learn ledger refresh_lease: %r is not a UUID", piece_id)
        return None
    client = _client(sb)
    query = (client.table(PIECES)
             .update({"lease_until": _iso(at + timedelta(seconds=secs)), "updated_at": _iso(_now())})
             .eq("id", pid).eq("generation_id", gen))
    query = query.not_.is_("lease_until", "null").not_.in_("status", sorted(_NO_LEASE_STATUSES))
    row = _one(_exec(query, op="refresh_lease", piece_id=pid, generation=gen), op="refresh_lease")
    if row is None:
        _log_lost("refresh_lease", client, pid, f"generation {gen}")
    return row


def release_lease_sync(piece_id: Any, *, generation_id: Any, sb: Any = None) -> Optional[Dict[str, Any]]:
    """Clear the lease `generation_id` holds (its work is written). None = it no longer held it."""
    gen = _require_uuid(generation_id, "generation_id")
    pid = _uuid_or_none(piece_id)
    if pid is None:
        logger.warning("learn ledger release_lease: %r is not a UUID", piece_id)
        return None
    client = _client(sb)
    row = _one(_exec(client.table(PIECES).update({"lease_until": None, "updated_at": _iso(_now())})
                     .eq("id", pid).eq("generation_id", gen),
                     op="release_lease", piece_id=pid, generation=gen), op="release_lease")
    if row is None:
        _log_lost("release_lease", client, pid, f"generation {gen}")
    else:
        logger.info("learn ledger: generation %s released piece %s", gen, pid)
    return row


def bump_sync(piece_id: Any, column: str, *, fence: Mapping[str, Any],
              statuses: Optional[Iterable[str]] = None, retries: int = 2,
              sb: Any = None) -> Optional[Dict[str, Any]]:
    """`column += 1` (one of BUMPABLE) as a read-modify-CAS on the OBSERVED value, while every
    `fence` column (and the status, when `statuses` is given) still matches. A race with another
    bump of the same counter re-reads and retries up to `retries` times — both increments count; a
    fence that no longer matches returns None (the caller lost the piece)."""
    if column not in BUMPABLE:
        raise ValueError(f"bump: {column!r} is not one of {BUMPABLE}")
    fence_n = _validate_fence(fence, PIECE_FENCEABLE - {column}, PIECES)
    wanted = None if statuses is None else _status_tuple(statuses, c.PIECE_STATUSES, "statuses")
    if isinstance(retries, bool) or not isinstance(retries, int) or not 0 <= retries <= 10:
        raise ValueError(f"retries must be an int in 0..10, got {retries!r}")
    pid = _uuid_or_none(piece_id)
    if pid is None:
        logger.warning("learn ledger bump %s: %r is not a UUID", column, piece_id)
        return None
    client = _client(sb)
    for attempt in range(retries + 1):
        row = get_piece_by_id_sync(pid, sb=client)
        if row is None:
            logger.warning("learn ledger bump %s: piece %s does not exist", column, pid)
            return None
        if wanted is not None and row.get("status") not in wanted:
            logger.info("learn ledger bump %s: piece %s is %s, not one of %s", column, pid,
                        row.get("status"), list(wanted))
            return None
        if not _fence_matches(row, fence_n):
            logger.info("learn ledger bump %s: piece %s no longer matches the fence %s", column, pid,
                        sorted(fence_n))
            return None
        observed = _counter(row, column, op="bump", rid=pid)
        query = (client.table(PIECES).update({column: observed + 1, "updated_at": _iso(_now())})
                 .eq("id", pid).eq(column, str(observed)))
        if wanted is not None:
            query = query.in_("status", list(wanted))
        query = _apply_fence(query, fence_n)
        row = _one(_exec(query, op="bump", piece_id=pid, column=column), op="bump")
        if row is not None:
            logger.info("learn ledger: piece %s %s %d → %d", pid, column, observed, observed + 1)
            return row
        logger.info("learn ledger bump %s: piece %s moved under us (attempt %d/%d) — re-reading",
                    column, pid, attempt + 1, retries + 1)
    logger.warning("learn ledger bump %s: piece %s kept moving; gave up after %d attempts",
                   column, pid, retries + 1)
    return None


# ── narration jobs ───────────────────────────────────────────────────────────────────────────

_OPEN_JOB_STATUSES = (c.JOB_QUEUED, c.JOB_IN_PROGRESS)


def get_job_sync(job_id: Any, *, sb: Any = None) -> Optional[Dict[str, Any]]:
    jid = _uuid_or_none(job_id)
    if jid is None:
        logger.warning("learn ledger get_job: %r is not a UUID — no such job", job_id)
        return None
    return _one(_exec(_client(sb).table(JOBS).select("*").eq("id", jid).limit(1),
                      op="get_job", job_id=jid), op="get_job")


def list_jobs_sync(piece_id: Any, *, sb: Any = None) -> List[Dict[str, Any]]:
    """Every narration job of a piece, by round (the next "Retry narration" round is max + 1)."""
    pid = _uuid_or_none(piece_id)
    if pid is None:
        logger.warning("learn ledger list_jobs: %r is not a UUID", piece_id)
        return []
    return _rows(_exec(_client(sb).table(JOBS).select("*").eq("piece_id", pid).order("round").limit(100),
                       op="list_jobs", piece_id=pid), op="list_jobs")


def enqueue_job_sync(piece_id: Any, *, round: int, script: Any, script_sha256: str, engine: str,
                     voice: str, pipeline_version: str, sb: Any = None) -> Optional[Dict[str, Any]]:
    """Queue the narration of one piece round — first write wins on (piece_id, round).

    A second enqueue of the same round with the SAME script / engine / voice / pipeline adopts the
    existing job (a retried tick). With a DIFFERENT one it returns None and logs an ERROR: the round
    already narrates another script, and handing back the old job would put stale audio in front of
    the owner. Open a new round instead (`list_jobs` → max round + 1)."""
    pid = _require_uuid(piece_id, "piece_id")
    if isinstance(round, bool) or not isinstance(round, int) or round < 1:
        raise ValueError(f"round must be an int >= 1, got {round!r}")
    if not isinstance(script, (list, dict)) or not script:
        raise ValueError("script must be a non-empty list or object")
    _json_safe(script, "script")
    if not (isinstance(script_sha256, str) and _HEX64.fullmatch(script_sha256)):
        raise ValueError(f"script_sha256 must be 64 lowercase hex characters, got {script_sha256!r}")
    if engine not in c.ENGINES:
        raise ValueError(f"engine must be one of {c.ENGINES}, got {engine!r}")
    for name, v in (("voice", voice), ("pipeline_version", pipeline_version)):
        if not (isinstance(v, str) and 0 < len(v) <= 80 and v.strip() == v and not _CONTROL.search(v)):
            raise ValueError(f"{name} must be 1-80 characters with no surrounding space, got {v!r}")
    client = _client(sb)
    row = {"piece_id": pid, "round": round, "script": script, "script_sha256": script_sha256,
           "engine": engine, "voice": voice, "pipeline_version": pipeline_version,
           "status": c.JOB_QUEUED}
    inserted = _rows(_exec(client.table(JOBS).upsert(row, on_conflict="piece_id,round",
                                                    ignore_duplicates=True),
                           op="enqueue_job", piece_id=pid, round=round), op="enqueue_job")
    existing = _one(_exec(client.table(JOBS).select("*").eq("piece_id", pid).eq("round", str(round))
                          .limit(1), op="enqueue_job_read", piece_id=pid, round=round),
                    op="enqueue_job_read")
    if existing is None:
        logger.error("learn ledger enqueue_job: piece %s round %d was inserted-or-ignored but reads back "
                     "as MISSING", pid, round)
        return None
    if inserted:
        logger.info("learn ledger: queued narration job %s for piece %s round %d (%s/%s, %s, script %s)",
                    existing.get("id"), pid, round, engine, voice, pipeline_version, script_sha256[:12])
        return existing
    drift = [k for k in ("script_sha256", "engine", "voice", "pipeline_version")
             if existing.get(k) != row[k]]
    if drift:
        logger.error("learn ledger enqueue_job: piece %s round %d already holds job %s with a different "
                     "%s — refusing to hand back a job for another script; open a new round",
                     pid, round, existing.get("id"), drift)
        return None
    logger.info("learn ledger enqueue_job: piece %s round %d already queued as job %s (status %s) — adopted",
                pid, round, existing.get("id"), existing.get("status"))
    return existing


def _engines(engines: Any) -> Tuple[str, ...]:
    if isinstance(engines, str):
        engines = (engines,)
    try:
        listed = list(engines)
    except TypeError:
        logger.warning("learn ledger claim_job: engines %r is not a list — nothing offered", engines)
        return ()
    known = tuple(dict.fromkeys(e for e in listed if e in c.ENGINES))
    unknown = [e for e in listed if e not in c.ENGINES]
    if unknown:
        logger.warning("learn ledger claim_job: the worker declared unknown engine(s) %r — ignored "
                       "(known: %s)", unknown, c.ENGINES)
    return known


def _claim_args(op: str, job_id: Any, attempts: Any, nonce: Any) -> Optional[Tuple[str, int, str]]:
    """The (job id, attempts, nonce) a worker presented, or None when malformed (WARNING) — these
    come from a request header, so a bad one is a refused call, not a crash."""
    jid = _uuid_or_none(job_id)
    if jid is None:
        logger.warning("learn ledger %s: job id %r is not a UUID", op, job_id)
        return None
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        logger.warning("learn ledger %s: job %s presented an unreadable claim attempts=%r", op, jid, attempts)
        return None
    if not (isinstance(nonce, str) and _NONCE.fullmatch(nonce)):
        logger.warning("learn ledger %s: job %s presented an unreadable claim nonce", op, jid)
        return None
    return jid, attempts, nonce


def _holds(row: Mapping[str, Any], attempts: int, nonce: str) -> bool:
    stored = row.get("claim_nonce")
    return (_int_or_none(row.get("attempts")) == attempts and isinstance(stored, str)
            and hmac.compare_digest(stored, nonce))


def _close_exhausted(client: Any, row: Mapping[str, Any], at: datetime) -> None:
    """An in_progress job whose lease ran out once more than MAX_JOB_LEASE_RECLAIMS allow: close it
    `failed` (fenced on the observed attempts and lease) instead of rendering it a fifth time — a
    render the platform keeps killing would otherwise loop until the hold expired."""
    jid = row.get("id")
    attempts = _counter(row, "attempts", op="claim_job", rid=jid)
    reclaims = _counter(row, "lease_reclaims", op="claim_job", rid=jid)
    query = (client.table(JOBS).update({
        "status": c.JOB_FAILED, "lease_until": None, "finished_at": _iso(at), "updated_at": _iso(at),
        "error": _scrub(f"lease_reclaims_exhausted: the lease ran out {reclaims + 1} times "
                        f"(cap {c.MAX_JOB_LEASE_RECLAIMS} re-claims) — the render keeps being killed"),
    }).eq("id", jid).eq("status", c.JOB_IN_PROGRESS).eq("attempts", str(attempts)))
    query = _apply_fence(query, {"lease_until": row.get("lease_until")})
    closed = _one(_exec(query, op="claim_job_close", job_id=jid), op="claim_job_close")
    if closed is not None:
        logger.error("learn ledger: narration job %s (piece %s round %s) FAILED — its lease ran out after "
                     "%d re-claims (cap %d); the render keeps dying (OOM? a redeploy mid-render?)",
                     jid, row.get("piece_id"), row.get("round"), reclaims, c.MAX_JOB_LEASE_RECLAIMS)
    else:
        logger.info("learn ledger claim_job: job %s moved while being closed for its re-claim cap", jid)


def claim_job_sync(engines: Iterable[str], *, now: datetime,
                   lease_seconds: float = c.JOB_LEASE_SECONDS, sb: Any = None) -> Optional[Dict[str, Any]]:
    """Claim the oldest job the worker can render: a `queued` one, or an `in_progress` one whose lease
    ran out (a killed render) while its `lease_reclaims` is under MAX_JOB_LEASE_RECLAIMS (one past it
    closes the job `failed`). The claim is ONE conditional UPDATE fenced on the observed status and
    `attempts` (and, for a re-claim, the observed lease_until — a heartbeat that landed meanwhile
    means the worker is alive): it sets `attempts + 1`, a fresh `claim_nonce`, the lease and the
    heartbeat. Returns the claimed row (its `attempts` and `claim_nonce` are the worker's claim), or
    None when there is nothing to claim."""
    eng = _engines(engines)
    at = _aware(now, "now")
    secs = _seconds(lease_seconds, "lease_seconds", upper=MAX_LEASE_SECONDS)
    if not eng:
        logger.warning("learn ledger claim_job: the worker declared no known engine — nothing offered")
        return None
    client = _client(sb)
    queued = _rows(_exec(client.table(JOBS).select("*").eq("status", c.JOB_QUEUED).in_("engine", list(eng))
                         .order("created_at").order("id").limit(_CLAIM_SCAN),
                         op="claim_job_queued", engines=eng), op="claim_job_queued")
    stale = _rows(_exec(client.table(JOBS).select("*").eq("status", c.JOB_IN_PROGRESS)
                        .in_("engine", list(eng)).lt("lease_until", _iso(at))
                        .order("lease_until").order("id").limit(_CLAIM_SCAN),
                        op="claim_job_stale", engines=eng), op="claim_job_stale")
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    candidates = sorted(queued + stale,
                        key=lambda r: (_parse_ts(r.get("created_at")) or epoch, str(r.get("id"))))
    for row in candidates:
        jid = row.get("id")
        status = row.get("status")
        attempts = _counter(row, "attempts", op="claim_job", rid=jid)
        patch: Dict[str, Any] = {
            "status": c.JOB_IN_PROGRESS, "attempts": attempts + 1, "claim_nonce": secrets.token_hex(8),
            "claimed_at": _iso(at), "heartbeat_at": _iso(at),
            "lease_until": _iso(at + timedelta(seconds=secs)), "updated_at": _iso(at),
        }
        query = client.table(JOBS)
        if status == c.JOB_IN_PROGRESS:
            reclaims = _counter(row, "lease_reclaims", op="claim_job", rid=jid)
            if reclaims >= c.MAX_JOB_LEASE_RECLAIMS:
                _close_exhausted(client, row, at)
                continue
            patch["lease_reclaims"] = reclaims + 1
            query = _apply_fence(query.update(patch).eq("id", jid).eq("status", status)
                                 .eq("attempts", str(attempts)).eq("lease_reclaims", str(reclaims)),
                                 {"lease_until": row.get("lease_until")})
        else:
            query = query.update(patch).eq("id", jid).eq("status", status).eq("attempts", str(attempts))
        won = _one(_exec(query, op="claim_job", job_id=jid), op="claim_job")
        if won is not None:
            if status == c.JOB_IN_PROGRESS:
                logger.warning("learn ledger: RE-CLAIMED narration job %s (piece %s round %s, %s) — its "
                               "lease ran out at %s; re-claim %d of %d", jid, row.get("piece_id"),
                               row.get("round"), row.get("engine"), row.get("lease_until"),
                               patch["lease_reclaims"], c.MAX_JOB_LEASE_RECLAIMS)
            else:
                logger.info("learn ledger: claimed narration job %s (piece %s round %s, %s) — attempt %d",
                            jid, row.get("piece_id"), row.get("round"), row.get("engine"), attempts + 1)
            return won
        logger.info("learn ledger claim_job: job %s was claimed by someone else first", jid)
    return None


def job_claim_ok_sync(job_id: Any, attempts: Any, nonce: Any, *, sb: Any = None) -> Optional[Dict[str, Any]]:
    """The job row when (attempts, nonce) is its CURRENT claim and it is still in_progress, else None
    (cancelled, re-claimed, finished, or a forged / malformed claim)."""
    args = _claim_args("job_claim_ok", job_id, attempts, nonce)
    if args is None:
        return None
    jid, att, non = args
    row = get_job_sync(jid, sb=sb)
    if row is None or row.get("status") != c.JOB_IN_PROGRESS or not _holds(row, att, non):
        return None
    return row


def heartbeat_job_sync(job_id: Any, attempts: Any, nonce: Any, *, now: datetime,
                       lease_seconds: float = c.JOB_LEASE_SECONDS, sb: Any = None) -> Optional[Dict[str, Any]]:
    """Extend the claim's lease. None = the job was cancelled, re-claimed or finished — the worker
    must stop rendering (the internal API answers 409)."""
    args = _claim_args("heartbeat_job", job_id, attempts, nonce)
    if args is None:
        return None
    jid, att, non = args
    at = _aware(now, "now")
    secs = _seconds(lease_seconds, "lease_seconds", upper=MAX_LEASE_SECONDS)
    row = _one(_exec(_client(sb).table(JOBS).update({
        "lease_until": _iso(at + timedelta(seconds=secs)), "heartbeat_at": _iso(at), "updated_at": _iso(at),
    }).eq("id", jid).eq("status", c.JOB_IN_PROGRESS).eq("attempts", str(att)).eq("claim_nonce", non),
        op="heartbeat_job", job_id=jid), op="heartbeat_job")
    if row is None:
        logger.info("learn ledger heartbeat_job: job %s claim %d is no longer current (cancelled, "
                    "re-claimed or finished)", jid, att)
    return row


def complete_job_sync(job_id: Any, attempts: Any, nonce: Any, *, audio_path: str, audio_bytes: int,
                      duration_seconds: float, timings: Any, checks: Mapping[str, Any],
                      now: Optional[datetime] = None, sb: Any = None) -> Optional[Dict[str, Any]]:
    """in_progress → ready with the rendered audio, fenced on the claim. The caller (the internal API)
    has already verified the object and the timing table. A replay of a completion that already
    landed for the SAME claim returns the row unchanged and writes nothing; anything else (cancelled,
    re-claimed, a stale claim) returns None."""
    args = _claim_args("complete_job", job_id, attempts, nonce)
    if args is None:
        return None
    jid, att, non = args
    if not (isinstance(audio_path, str) and len(audio_path) <= 512 and _AUDIO_PATH.fullmatch(audio_path)
            and ".." not in audio_path.split("/")):
        raise ValueError(f"audio_path must be a relative Storage path, got {audio_path!r}")
    if isinstance(audio_bytes, bool) or not isinstance(audio_bytes, int) or not 0 < audio_bytes < MAX_AUDIO_BYTES:
        raise ValueError(f"audio_bytes must be a positive int, got {audio_bytes!r}")
    duration = _seconds(duration_seconds, "duration_seconds", upper=MAX_AUDIO_SECONDS)
    if not isinstance(timings, (list, dict)):
        raise ValueError(f"timings must be a list or object, got {type(timings).__name__}")
    if not isinstance(checks, dict):
        raise ValueError(f"checks must be an object, got {type(checks).__name__}")
    _json_safe(timings, "timings")
    _json_safe(checks, "checks")
    at = _aware(now, "now") if now is not None else _now()
    client = _client(sb)
    row = _one(_exec(client.table(JOBS).update({
        "status": c.JOB_READY, "audio_path": audio_path, "audio_bytes": audio_bytes,
        "duration_seconds": duration, "timings": timings, "checks": checks, "lease_until": None,
        "finished_at": _iso(at), "updated_at": _iso(at), "error": None,
    }).eq("id", jid).eq("status", c.JOB_IN_PROGRESS).eq("attempts", str(att)).eq("claim_nonce", non),
        op="complete_job", job_id=jid), op="complete_job")
    if row is not None:
        logger.info("learn ledger: narration job %s (piece %s round %s) READY — %s, %d bytes, %.1fs",
                    jid, row.get("piece_id"), row.get("round"), audio_path, audio_bytes, duration)
        return row
    cur = get_job_sync(jid, sb=client)
    if cur is not None and cur.get("status") == c.JOB_READY and _holds(cur, att, non):
        logger.info("learn ledger complete_job: job %s is already ready under claim %d — a replay; "
                    "nothing written", jid, att)
        return cur
    logger.info("learn ledger complete_job: job %s claim %d refused — the job is %s (attempts %s)", jid,
                att, None if cur is None else cur.get("status"), None if cur is None else cur.get("attempts"))
    return None


def fail_job_sync(job_id: Any, attempts: Any, nonce: Any, *, error: Any, final: bool = False,
                  now: Optional[datetime] = None, sb: Any = None) -> Optional[Dict[str, Any]]:
    """The worker reports a failed render: `worker_failures + 1`, then back to `queued` while under
    MAX_JOB_WORKER_FAILURES, `failed` at it — or `failed` at once when `final` (the caller knows a
    retry of this job fails the same way: `contracts.DETERMINISTIC_FAIL_CODES`). Fenced on the claim
    AND the observed worker_failures. A replay of a failure that already landed for the SAME claim
    returns the row unchanged."""
    args = _claim_args("fail_job", job_id, attempts, nonce)
    if args is None:
        return None
    jid, att, non = args
    err = _scrub(error) or "unspecified worker failure"
    at = _aware(now, "now") if now is not None else _now()
    client = _client(sb)
    row = get_job_sync(jid, sb=client)
    if row is None:
        logger.warning("learn ledger fail_job: job %s does not exist", jid)
        return None
    if row.get("status") == c.JOB_IN_PROGRESS and _holds(row, att, non):
        failures = _counter(row, "worker_failures", op="fail_job", rid=jid) + 1
        capped = failures >= c.MAX_JOB_WORKER_FAILURES
        closes = capped or final is True
        patch = {"worker_failures": failures, "error": err, "lease_until": None, "updated_at": _iso(at),
                 "status": c.JOB_FAILED if closes else c.JOB_QUEUED,
                 "finished_at": _iso(at) if closes else None}
        done = _one(_exec(client.table(JOBS).update(patch).eq("id", jid).eq("status", c.JOB_IN_PROGRESS)
                          .eq("attempts", str(att)).eq("claim_nonce", non)
                          .eq("worker_failures", str(failures - 1)),
                          op="fail_job", job_id=jid), op="fail_job")
        if done is not None:
            if closes:
                logger.error("learn ledger: narration job %s (piece %s round %s) FAILED — worker failure "
                             "%d of %d%s: %s", jid, row.get("piece_id"), row.get("round"), failures,
                             c.MAX_JOB_WORKER_FAILURES,
                             "" if capped else " (deterministic: a retry would render the same file)", err)
            else:
                logger.warning("learn ledger: narration job %s (piece %s round %s) re-queued after worker "
                               "failure %d of %d: %s", jid, row.get("piece_id"), row.get("round"), failures,
                               c.MAX_JOB_WORKER_FAILURES, err)
            return done
        row = get_job_sync(jid, sb=client)
        if row is None:
            return None
    if row.get("status") in (c.JOB_QUEUED, c.JOB_FAILED) and _holds(row, att, non):
        logger.info("learn ledger fail_job: job %s already recorded the failure of claim %d — a replay; "
                    "nothing written", jid, att)
        return row
    logger.info("learn ledger fail_job: job %s claim %d refused — the job is %s (attempts %s)", jid, att,
                row.get("status"), row.get("attempts"))
    return None


def cancel_open_jobs_sync(piece_id: Any, *, reason: str = "piece_closed", now: Optional[datetime] = None,
                          sb: Any = None) -> List[Dict[str, Any]]:
    """queued / in_progress → cancelled for every job of the piece (expiry, rejection, a regenerate).
    Not fenced on a claim: the piece decides, and a worker mid-render learns it from its next
    heartbeat (409). Returns the cancelled rows."""
    pid = _uuid_or_none(piece_id)
    if pid is None:
        logger.warning("learn ledger cancel_open_jobs: %r is not a UUID", piece_id)
        return []
    at = _aware(now, "now") if now is not None else _now()
    rows = _rows(_exec(_client(sb).table(JOBS).update({
        "status": c.JOB_CANCELLED, "lease_until": None, "finished_at": _iso(at), "updated_at": _iso(at),
    }).eq("piece_id", pid).in_("status", list(_OPEN_JOB_STATUSES)),
        op="cancel_open_jobs", piece_id=pid), op="cancel_open_jobs")
    if rows:
        logger.warning("learn ledger: cancelled %d narration job(s) of piece %s (%s): %s", len(rows), pid,
                       _scrub(reason, 200), ", ".join(f"{r.get('id')} (round {r.get('round')})" for r in rows))
    return rows


def _weekly_audio_path_ok(path: Any) -> bool:
    return (isinstance(path, str) and len(path) <= 512 and path.startswith("audio/weekly/")
            and bool(_AUDIO_PATH.fullmatch(path)) and ".." not in path.split("/"))


def repoint_ready_audio_sync(job_id: Any, *, piece_id: Any, old_audio_path: str, new_audio_path: str,
                             new_audio_bytes: int, new_duration_seconds: float, note: Mapping[str, Any],
                             sb: Any = None) -> Optional[Dict[str, Any]]:
    """Point a READY narration job at a re-cut file of the same render (the promote script's trimmed
    W44 audio): `audio_path`, `audio_bytes`, `duration_seconds` and `checks.promote` = `note`.
    `script`, `timings` and `script_sha256` stay — they are the record of the original render, and
    nothing re-validates a ready job outside media_pending.

    Reads the job, then ONE UPDATE fenced on id, piece_id, status = ready, audio_path = old and the
    read's updated_at. A replay (the job already on `new_audio_path` with `checks.promote`) returns
    the row and writes nothing. None when the job is gone, not ready, another piece's, on a third
    path, or moved on (logged)."""
    jid = _require_uuid(job_id, "job_id")
    pid = _require_uuid(piece_id, "piece_id")
    for name, path in (("old_audio_path", old_audio_path), ("new_audio_path", new_audio_path)):
        if not _weekly_audio_path_ok(path):
            raise ValueError(f"repoint: {name} must be an audio/weekly/ Storage path, got {path!r}")
    if old_audio_path == new_audio_path:
        raise ValueError("repoint: the new audio path equals the old one")
    if isinstance(new_audio_bytes, bool) or not isinstance(new_audio_bytes, int) \
            or not 0 < new_audio_bytes < MAX_AUDIO_BYTES:
        raise ValueError(f"repoint: new_audio_bytes must be a positive int, got {new_audio_bytes!r}")
    duration = _seconds(new_duration_seconds, "new_duration_seconds", upper=MAX_AUDIO_SECONDS)
    if not isinstance(note, Mapping):
        raise ValueError(f"repoint: note must be an object, got {type(note).__name__}")
    note_d = _json_safe(dict(note), "note")
    client = _client(sb)
    cur = get_job_sync(jid, sb=client)
    if cur is None or str(cur.get("piece_id")) != pid or cur.get("status") != c.JOB_READY:
        logger.warning("learn ledger repoint: job %s is %s for piece %s — not a ready job of piece %s; "
                       "nothing written", jid, None if cur is None else cur.get("status"),
                       None if cur is None else cur.get("piece_id"), pid)
        return None
    checks = cur.get("checks") if isinstance(cur.get("checks"), dict) else {}
    if cur.get("audio_path") == new_audio_path and isinstance(checks.get("promote"), dict):
        logger.info("learn ledger repoint: job %s already points at %s — a replay; nothing written",
                    jid, new_audio_path)
        return cur
    if cur.get("audio_path") != old_audio_path:
        logger.warning("learn ledger repoint: job %s is on %r, not %r — nothing written", jid,
                       cur.get("audio_path"), old_audio_path)
        return None
    observed = _parse_ts(cur.get("updated_at"))
    if observed is None:
        logger.error("learn ledger repoint: job %s has an unreadable updated_at %r — nothing written",
                     jid, cur.get("updated_at"))
        return None
    row = _one(_exec(client.table(JOBS).update({
        "audio_path": new_audio_path, "audio_bytes": new_audio_bytes, "duration_seconds": duration,
        "checks": {**checks, "promote": note_d}, "updated_at": _iso(_now()),
    }).eq("id", jid).eq("piece_id", pid).eq("status", c.JOB_READY).eq("audio_path", old_audio_path)
        .eq("updated_at", _iso(observed)), op="repoint_ready_audio", job_id=jid), op="repoint_ready_audio")
    if row is None:
        logger.warning("learn ledger repoint: job %s moved on between the read and the write — nothing "
                       "written", jid)
        return None
    logger.warning("learn ledger: narration job %s (piece %s) repointed %s → %s (%d bytes, %.1fs)",
                   jid, pid, old_audio_path, new_audio_path, new_audio_bytes, duration)
    return row


# ── async wrappers (one `asyncio.to_thread` hop per operation; see the module docstring) ─────


async def get_piece(period_key: str, *, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(get_piece_sync, period_key, sb=sb)


async def get_piece_by_id(piece_id: Any, *, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(get_piece_by_id_sync, piece_id, sb=sb)


async def list_pieces(*, statuses: Optional[Iterable[str]] = None, kind: Optional[str] = None,
                      limit: int = 50, sb: Any = None) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(list_pieces_sync, statuses=statuses, kind=kind, limit=limit, sb=sb)


async def recent_subjects(kind: str, since: datetime, *, statuses: Iterable[str] = RECENT_STATUSES,
                          sb: Any = None) -> RecentSubjects:
    return await asyncio.to_thread(recent_subjects_sync, kind, since, statuses=statuses, sb=sb)


async def create_piece(period_key: str, kind: str, *, scheduled_publish_at: datetime, hold_until: datetime,
                       dry_run: bool, category: Optional[str] = None, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(create_piece_sync, period_key, kind,
                                   scheduled_publish_at=scheduled_publish_at, hold_until=hold_until,
                                   dry_run=dry_run, category=category, sb=sb)


async def transition(piece_id: Any, *, from_statuses: Iterable[str], to_status: str,
                     fields: Optional[Mapping[str, Any]] = None, fence: Optional[Mapping[str, Any]] = None,
                     within_hold_at: Optional[datetime] = None, due_at: Optional[datetime] = None,
                     sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(transition_sync, piece_id, from_statuses=from_statuses,
                                   to_status=to_status, fields=fields, fence=fence,
                                   within_hold_at=within_hold_at, due_at=due_at, sb=sb)


async def acquire_lease(piece_id: Any, *, generation_id: Any, lease_seconds: float,
                        from_statuses: Iterable[str], to_status: Optional[str] = None,
                        now: Optional[datetime] = None, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(acquire_lease_sync, piece_id, generation_id=generation_id,
                                   lease_seconds=lease_seconds, from_statuses=from_statuses,
                                   to_status=to_status, now=now, sb=sb)


async def refresh_lease(piece_id: Any, *, generation_id: Any, lease_seconds: float,
                        now: Optional[datetime] = None, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(refresh_lease_sync, piece_id, generation_id=generation_id,
                                   lease_seconds=lease_seconds, now=now, sb=sb)


async def release_lease(piece_id: Any, *, generation_id: Any, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(release_lease_sync, piece_id, generation_id=generation_id, sb=sb)


async def bump(piece_id: Any, column: str, *, fence: Mapping[str, Any],
               statuses: Optional[Iterable[str]] = None, retries: int = 2,
               sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(bump_sync, piece_id, column, fence=fence, statuses=statuses,
                                   retries=retries, sb=sb)


async def get_job(job_id: Any, *, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(get_job_sync, job_id, sb=sb)


async def list_jobs(piece_id: Any, *, sb: Any = None) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(list_jobs_sync, piece_id, sb=sb)


async def enqueue_job(piece_id: Any, *, round: int, script: Any, script_sha256: str, engine: str,
                      voice: str, pipeline_version: str, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(enqueue_job_sync, piece_id, round=round, script=script,
                                   script_sha256=script_sha256, engine=engine, voice=voice,
                                   pipeline_version=pipeline_version, sb=sb)


async def claim_job(engines: Iterable[str], *, now: datetime, lease_seconds: float = c.JOB_LEASE_SECONDS,
                    sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(claim_job_sync, engines, now=now, lease_seconds=lease_seconds, sb=sb)


async def job_claim_ok(job_id: Any, attempts: Any, nonce: Any, *, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(job_claim_ok_sync, job_id, attempts, nonce, sb=sb)


async def heartbeat_job(job_id: Any, attempts: Any, nonce: Any, *, now: datetime,
                        lease_seconds: float = c.JOB_LEASE_SECONDS, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(heartbeat_job_sync, job_id, attempts, nonce, now=now,
                                   lease_seconds=lease_seconds, sb=sb)


async def complete_job(job_id: Any, attempts: Any, nonce: Any, *, audio_path: str, audio_bytes: int,
                       duration_seconds: float, timings: Any, checks: Mapping[str, Any],
                       now: Optional[datetime] = None, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(complete_job_sync, job_id, attempts, nonce, audio_path=audio_path,
                                   audio_bytes=audio_bytes, duration_seconds=duration_seconds,
                                   timings=timings, checks=checks, now=now, sb=sb)


async def fail_job(job_id: Any, attempts: Any, nonce: Any, *, error: Any, final: bool = False,
                   now: Optional[datetime] = None, sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(fail_job_sync, job_id, attempts, nonce, error=error, final=final, now=now,
                                   sb=sb)


async def cancel_open_jobs(piece_id: Any, *, reason: str = "piece_closed", now: Optional[datetime] = None,
                           sb: Any = None) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(cancel_open_jobs_sync, piece_id, reason=reason, now=now, sb=sb)


async def promote_rehearsed(piece_id: Any, *, review_round: int, old_draft_sha256: str,
                            narration_job_id: Any, observed_updated_at: Any, draft: Mapping[str, Any],
                            draft_sha256: str, scheduled_publish_at: datetime, hold_until: datetime,
                            validation: Mapping[str, Any], now: datetime,
                            sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(promote_rehearsed_sync, piece_id, review_round=review_round,
                                   old_draft_sha256=old_draft_sha256, narration_job_id=narration_job_id,
                                   observed_updated_at=observed_updated_at, draft=draft,
                                   draft_sha256=draft_sha256, scheduled_publish_at=scheduled_publish_at,
                                   hold_until=hold_until, validation=validation, now=now, sb=sb)


async def repoint_ready_audio(job_id: Any, *, piece_id: Any, old_audio_path: str, new_audio_path: str,
                              new_audio_bytes: int, new_duration_seconds: float, note: Mapping[str, Any],
                              sb: Any = None) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(repoint_ready_audio_sync, job_id, piece_id=piece_id,
                                   old_audio_path=old_audio_path, new_audio_path=new_audio_path,
                                   new_audio_bytes=new_audio_bytes, new_duration_seconds=new_duration_seconds,
                                   note=note, sb=sb)
