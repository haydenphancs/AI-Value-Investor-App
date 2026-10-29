"""Supabase I/O for the Trillion Club stake finder and its Telegram taps — nothing else.

The finder (``stake_finder``) and the owner's taps (``stake_finder.apply_owner_decision``) touch
three tables, and only through this module: ``trillion_club_stakes`` (the rows Home shows),
the finder's ledger ``trillion_club_stake_findings`` (migration 205) and one read of
``trillion_club_filings`` (a member's latest complete 13F). Companies are read through
``TrillionClubStore.read_companies``; nothing here calls ``.rpc()``.

READS ARE RETRIED, WRITES ARE NOT
---------------------------------
A read is idempotent, so it runs through ``retry_idempotent_async`` (a transient 520 is
retried). A write is SINGLE-SHOT through ``sb_exec``: a 520 says nothing about whether
Postgres committed (``app/utils/supabase_errors.py``), and replaying the write is wrong both
ways — an INSERT whose first attempt committed answers 23505 (read as a conflict it is not),
a compare-and-swap whose first attempt committed matches no row (read as a lost race). So
when a write raises, the store RE-READS the row and decides:

  * the write's effect is there → success, as if the answer had arrived;
  * the row could still take the write but does not show it → ``StakeStoreError`` (the caller
    counts an error; the ledger makes the next run retry from where this one stopped);
  * the row can no longer take it (withdrawn, re-owned, gone) → the lost-race answer, False.

A fence that matches no row is a lost race (False, logged at INFO), never an error.
⚠️ Cancelling a call does NOT stop its worker thread: a write in flight may still commit
after the cancel. Every write is fenced, so "did it land?" is always answered by a re-read.

PAGED READS
-----------
PostgREST clamps every answer to its server-side max-rows (~1,000 on this project,
``app/utils/postgrest_paging.py``) whatever ``.limit()`` asks, and the clamp is silent, so a
single ``limit(5000)`` read could never reach a 5,000-row ceiling. A table read here pages
by primary key (keyset ``id > last``) until an EMPTY page — no server cap can end it early —
and refuses to return part of a table: reaching ``MAX_STAKE_ROWS`` / ``MAX_FINDING_ROWS``
raises, because the identity dedupe, the volume cap and the breaker must never run on a
sample.

WHAT IS REFUSED BEFORE ANY I/O
------------------------------
A value from our own code that migrations 175/205 would refuse raises ``ValueError`` before
a request is built: an undashed accession, an unpadded CIK, an unknown status, a decision
without its time, a naive datetime, NaN in a JSON column, a machine row with a background
line. ``stake_check_violations`` / ``finding_check_violations`` state the 205 rules in
Python; ``tests/test_trillion_club_stake_store.py`` pins them to the migration's own probe
catalogue, so a 23514 never reaches production from a row this module let through.

Log lines carry the operation, ids, slug and accession — never a row's other values, and
never PostgREST's ``details`` (Postgres puts the failing row there).
"""

from __future__ import annotations

import json
import logging
import math
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from app.schemas.trillion_club import STAKE_KINDS, VALUE_BASES
from app.services.trillion_club.stake_contracts import (
    FINDING_OUTCOMES,
    FINDING_SOURCES,
    FINDING_STATUSES,
    FINDING_TIERS,
    MACHINE_ORIGINS,
    ORIGIN_AUTO,
    ORIGIN_PROPOSAL,
    ORIGIN_SEED,
    ORIGINS,
    OUTCOME_AUTO,
    OWNER_DECISIONS,
    REASON_CODES,
    STATUS_NEW,
    STATUS_NOTED,
    STATUS_PROPOSED,
    STATUS_PUBLISHING,
    STATUS_STAGED,
)
from app.services.trillion_club.stake_names import (
    KIND_ON_13F_NOTE,
    KIND_US_LISTED_OFF_13F,
    cusip_identity,
    normalize_symbol,
    unique_preserving,
)
from app.services.trillion_club.stake_rules import STAKE_COLUMNS
from app.utils.supabase_async import sb_exec
from app.utils.supabase_errors import is_unique_violation, retry_idempotent_async

logger = logging.getLogger(__name__)

_TAG = "[tc-stakes-store]"

# Plain module-level constants: the package's isolation scan (tests/test_trillion_club_jobs.py)
# resolves every `.table(X)` to an `X = "trillion_club_…"` assignment.
STAKES_TABLE = "trillion_club_stakes"
FINDINGS_TABLE = "trillion_club_stake_findings"
FILINGS_TABLE = "trillion_club_filings"

#: Migration 205's columns on the stakes table, plus the three 175 never let the seed write.
STAKE_EXTRA_COLUMNS: Tuple[str, ...] = (
    "id", "origin", "owner_kept_at", "withdrawn_at", "investee_cik", "finding_id",
    "created_at", "updated_at",
)
STAKE_READ_COLUMNS: Tuple[str, ...] = STAKE_COLUMNS + STAKE_EXTRA_COLUMNS
#: Every column of trillion_club_stake_findings, in the order migration 205 declares them.
FINDING_COLUMNS: Tuple[str, ...] = (
    "id", "company_slug", "source", "accession", "filer_cik", "investee_cik", "form",
    "filed_on", "document", "source_url", "tier", "outcome", "reason", "proposed_row",
    "verified_spans", "fact_sha", "stake_id", "status", "telegram_message_id", "notified_at",
    "published_at", "owner_decision", "decided_at", "attempts", "last_error", "created_at",
    "updated_at",
)
#: An AUTO finding in one of these is a publish in waiting (no row live yet): gate 8 counts it
#: against the day it will go live — today at the earliest — whatever its created_at.
AUTO_IN_FLIGHT_STATUSES: Tuple[str, ...] = (STATUS_NEW, STATUS_STAGED, STATUS_NOTED)

#: Ceilings that make a runaway table loud. Reaching one raises — a ceiling hit is never
#: treated as a complete read. Read at call time (tests lower them).
MAX_STAKE_ROWS = 5000
MAX_FINDING_ROWS = 5000
#: Rows asked for per page; PostgREST may answer fewer (its max-rows), and paging goes on
#: until an empty page whatever it answers.
PAGE_ROWS = 1000
#: Accessions per `IN (...)` read: an answer holds at most this many rows (UNIQUE
#: (company_slug, accession)), far below any server cap, so one request per chunk is complete.
ACCESSION_CHUNK = 100
#: The read retry budget (supabase_errors' defaults), read at call time so tests can zero the
#: backoff.
READ_ATTEMPTS = 3
READ_BACKOFF_SECONDS = 0.25

#: trillion_club/builder.py's BUILD_COMPLETE (not imported: the builder pulls in the 13F
#: machinery; a test pins the two equal).
BUILD_COMPLETE = "complete"
#: Statuses in which a proposal's message is (or may be) out with Publish on it: its
#: ``proposed_row`` / ``fact_sha`` are frozen (update_finding refuses to change them in place).
_FACTS_FROZEN = frozenset({STATUS_PROPOSED, STATUS_PUBLISHING})

# Column caps and formats of migrations 175 (stakes) and 205 (stakes + ledger).
_SLUG_RE = re.compile(r"[a-z0-9-]{1,40}")
_CIK_RE = re.compile(r"[0-9]{10}")
_ACCESSION_RE = re.compile(r"[0-9]{10}-[0-9]{2}-[0-9]{6}")
_SHA_RE = re.compile(r"[0-9a-f]{64}")
_PERIOD_RE = re.compile(r"[0-9]{4}-Q[1-4]")
_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CUSIP_RE = re.compile(r"[0-9A-Z]{9}")
_UNSTORABLE = re.compile(r"[\x00\ud800-\udfff]")   # NUL (22P05) or a lone surrogate (no UTF-8)
_SEC_ARCHIVES = "https://www.sec.gov/Archives/edgar/data/"
_ZERO_CIK = "0" * 10
_INT4_MIN, _INT4_MAX = -(2 ** 31), 2 ** 31 - 1
_INT8_MAX = 2 ** 63 - 1
_PROPOSED_ROW_MAX_BYTES = 16384
_SPANS_MAX_ITEMS = 100
_SPANS_MAX_BYTES = 65536
_LAST_ERROR_MAX = 2000
_JSON_MAX_DEPTH = 20
_SOURCE_CONFIDENCES = ("primary", "secondary")
_AUTO_KINDS = (KIND_ON_13F_NOTE, KIND_US_LISTED_OFF_13F)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

_TS_COLUMNS = frozenset({"notified_at", "published_at", "decided_at", "created_at", "updated_at",
                         "owner_kept_at", "withdrawn_at"})
_FLOAT_COLUMNS = frozenset({"ownership_pct", "disclosed_value_usd"})


# ── Errors ───────────────────────────────────────────────────────────────────────────────


def _clip(value: Any, cap: int = 300) -> str:
    return " ".join(str(value).split())[:cap]


def _error_text(e: BaseException) -> str:
    """``Type code=… message=…`` — PostgREST's code and message, never its ``details`` (the
    failing row's values)."""
    code = getattr(e, "code", None)
    message = getattr(e, "message", None)
    if code is not None or isinstance(message, str):
        return f"{type(e).__name__} code={code!r} message={_clip(message)}"
    return f"{type(e).__name__}: {_clip(e)}"


class StakeStoreError(Exception):
    """A read or write of the finder's tables failed, or answered in a shape it never should.
    Carries the operation (with its ids) and the underlying cause, so the log line is
    diagnosable alone. For a write it means NOT APPLIED: the re-read found the row still
    able to take the write and not showing it — the next run retries."""

    def __init__(self, op: str, cause: BaseException):
        self.op = op
        self.cause = cause
        super().__init__(f"trillion club stake store: {op} failed ({_error_text(cause)})")


class StakeConflict(StakeStoreError):
    """The stake key UNIQUE (company_slug, investee_name, kind) refused the write: ANOTHER
    row holds that key. Never retried as is."""


# ── Small validators (ValueError = a bug in the caller, raised before any I/O) ───────────


def _aware(value: Any, what: str) -> datetime:
    """A timezone-aware datetime, or ValueError. A naive one would be stored as UTC whatever
    the caller meant (an ET wall-clock hour would land 4-5 hours off)."""
    if not isinstance(value, datetime):
        raise ValueError(f"{what} must be a timezone-aware datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{what} must be timezone-aware, got the naive {value.isoformat()}")
    return value


def _iso(dt: datetime) -> str:
    """UTC, microseconds, ``Z`` — the ledger convention (learn_weekly/ledger.py), so a value
    never carries a ``+`` into a PostgREST filter."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _parse_ts(value: Any) -> Optional[datetime]:
    """A stored or caller-given timestamp as an aware datetime (naive → UTC, the Supabase
    session zone), or None when it is not one."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None and value.utcoffset() is not None \
            else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _same_instant(stored: Any, wanted: Any) -> bool:
    a, b = _parse_ts(stored), _parse_ts(wanted)
    return a is not None and b is not None and a == b


def _same(col: str, stored: Any, wanted: Any) -> bool:
    """Equality as Postgres stores the value: an instant for a timestamp, a number for a
    double, and never ``True == 1``."""
    if stored is None or wanted is None:
        return stored is None and wanted is None
    if col in _TS_COLUMNS:
        return _same_instant(stored, wanted)
    if isinstance(stored, bool) or isinstance(wanted, bool):
        return stored is wanted
    if col in _FLOAT_COLUMNS:
        try:
            return float(stored) == float(wanted)
        except (TypeError, ValueError):
            return False
    return stored == wanted


def _uuid(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be a UUID string, got {type(value).__name__}")
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise ValueError(f"{what} {value!r} is not a UUID") from None


def _cik(value: Any, what: str) -> str:
    """SEC's zero-padded 10-digit CIK (``sec_edgar.normalize_cik``'s output), never zero."""
    if not isinstance(value, str) or not _CIK_RE.fullmatch(value) or value == _ZERO_CIK:
        raise ValueError(f"{what} must be a 10-digit, zero-padded, non-zero CIK, got {value!r}")
    return value


def _slug(value: Any) -> str:
    if not isinstance(value, str) or not _SLUG_RE.fullmatch(value):
        raise ValueError(f"company_slug must match [a-z0-9-]{{1,40}}, got {value!r}")
    return value


def _accession(value: Any) -> str:
    """The DASHED accession (``sec_edgar.normalize_accession``'s output)."""
    if not isinstance(value, str) or not _ACCESSION_RE.fullmatch(value):
        raise ValueError(f"accession must be dashed 0000000000-00-000000, got {value!r}")
    return value


def _text(value: Any, what: str, lo: int, hi: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{what} must be a string, got {type(value).__name__}")
    if not lo <= len(value) <= hi:
        raise ValueError(f"{what} must be {lo}-{hi} characters, got {len(value)}")
    if _UNSTORABLE.search(value):
        raise ValueError(f"{what} holds a NUL or a lone surrogate, which Postgres cannot store")
    return value


def _opt_text(value: Any, what: str, hi: int = 10_000) -> Optional[str]:
    return None if value is None else _text(value, what, 0, hi)


def _https(value: Any, what: str, hi: int) -> str:
    text = _text(value, what, len("https://"), hi)
    if not text.startswith("https://"):
        raise ValueError(f"{what} must start with https://")
    return text


def _iso_date(value: Any, what: str) -> str:
    """A ``date`` (never a ``datetime``) or a strict ``YYYY-MM-DD`` string → the ISO string."""
    if isinstance(value, datetime):
        raise ValueError(f"{what} must be a date, not a datetime ({value.isoformat()})")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and _DATE_RE.fullmatch(value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    raise ValueError(f"{what} must be a date or YYYY-MM-DD, got {value!r}")


def _opt_iso_date(value: Any, what: str) -> Optional[str]:
    return None if value is None else _iso_date(value, what)


def _timestamp(value: Any, what: str) -> Optional[str]:
    """None, an aware datetime or an ISO string WITH its offset → the ``Z`` form."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _iso(_aware(value, what))
    if isinstance(value, str):
        parsed = _parse_ts(value)
        if parsed is not None and re.search(r"(?:Z|[+-][0-9]{2}:[0-9]{2})$", value.strip()):
            return _iso(parsed)
    raise ValueError(f"{what} must be an aware datetime or an ISO timestamp with an offset, got {value!r}")


def _bool(value: Any, what: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{what} must be true/false, got {value!r}")
    return value


def _int(value: Any, what: str, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise ValueError(f"{what} must be an integer in [{lo}, {hi}], got {value!r}")
    return value


def _opt_number(value: Any, what: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{what} must be a number, got {type(value).__name__}")
    try:
        f = float(value)
    except OverflowError:
        raise ValueError(f"{what} is too large for a double") from None
    if not math.isfinite(f):
        raise ValueError(f"{what} must be finite (JSON cannot carry {value!r})")
    return f


def _closed(value: Any, allowed: Sequence[str], what: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise ValueError(f"{what} {value!r} is not one of {tuple(allowed)}")
    return value


def _json_value(value: Any, what: str, depth: int = 0) -> Any:
    """A deep copy that JSON (and jsonb) carries unchanged: str keys only, finite numbers, no
    NUL / lone surrogate, bounded depth. ``json.dumps`` would silently turn an int key into a
    string and NaN into invalid JSON; httpx refuses NaN only after the request is built."""
    if depth > _JSON_MAX_DEPTH:
        raise ValueError(f"{what} is nested more than {_JSON_MAX_DEPTH} levels")
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{what} holds a non-finite number")
        return value
    if isinstance(value, str):
        if _UNSTORABLE.search(value):
            raise ValueError(f"{what} holds a NUL or a lone surrogate, which jsonb cannot store")
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(v, what, depth + 1) for v in value]
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str) or _UNSTORABLE.search(k):
                raise ValueError(f"{what} has a key that is not a storable string: {k!r}")
            out[k] = _json_value(v, what, depth + 1)
        return out
    raise ValueError(f"{what} holds a {type(value).__name__}, which JSON cannot carry")


def _json_text_bytes(value: Any) -> int:
    """jsonb's text form (``", "`` / ``": "`` separators), as the 205 size caps measure it."""
    return len(json.dumps(value, ensure_ascii=False, separators=(", ", ": ")).encode("utf-8"))


def _sanitize_error(value: Any) -> Optional[str]:
    """``last_error`` is diagnostic text: control characters folded, NUL / lone surrogates
    dropped and clipped to the 2,000-character cap — a long exception must never make the
    write that records it fail."""
    if value is None:
        return None
    text = _UNSTORABLE.sub("", " ".join(str(value).split()))
    return text[:_LAST_ERROR_MAX]


# ── The 205 rules in Python (the store's own pre-I/O guard) ──────────────────────────────

#: DEFAULTs a missing key takes (175's for the columns the 205 rules read, and 205's).
_STAKE_DEFAULTS: Dict[str, Any] = {
    "origin": ORIGIN_SEED, "source_confidence": "primary", "material": False,
    "tied_to_deal": False, "published": False,
}
#: The NOT NULL columns the 205 stake rules read (checked first, as Postgres does).
_STAKE_RULE_NOT_NULL = ("origin", "kind", "source_url", "source_confidence", "material",
                        "tied_to_deal", "published")


def _cik_ok(value: Any) -> bool:
    return isinstance(value, str) and bool(_CIK_RE.fullmatch(value)) and value != _ZERO_CIK


def stake_check_violations(row: Mapping[str, Any]) -> List[str]:
    """The migration-205 rules a ``trillion_club_stakes`` row breaks, by constraint name
    (``NOTNULL:<column>`` first, as Postgres checks it before any CHECK). Empty = 205 accepts
    it. Pure; ``row`` holds wire values, missing keys take the column DEFAULT."""
    r: Dict[str, Any] = {**_STAKE_DEFAULTS, **row}
    missing = [f"NOTNULL:{c}" for c in _STAKE_RULE_NOT_NULL if r.get(c) is None]
    if missing:
        return missing
    g = r.get
    origin, machine = g("origin"), g("origin") in MACHINE_ORIGINS
    out: List[str] = []
    if origin not in ORIGINS:
        out.append("trillion_club_stakes_origin_check")
    if g("investee_cik") is not None and not _cik_ok(g("investee_cik")):
        out.append("trillion_club_stakes_investee_cik_check")
    if (g("finding_id") is not None) != machine:
        out.append("trillion_club_stake_finding_iff_machine")
    if g("published") is True and g("withdrawn_at") is not None:
        out.append("trillion_club_stake_withdrawn_never_published")
    url = g("source_url")
    if machine and not (g("background") is None and g("tied_to_deal") is False
                        and g("source_confidence") == "primary"
                        and isinstance(url, str) and url.startswith(_SEC_ARCHIVES)):
        out.append("trillion_club_stake_machine_shape")
    if origin == ORIGIN_AUTO and g("owner_kept_at") is None and not (
            g("material") is False and g("kind") in _AUTO_KINDS
            and g("disclosed_value_usd") is None and g("value_basis") is None):
        out.append("trillion_club_stake_auto_scope")
    if g("owner_kept_at") is not None and origin != ORIGIN_AUTO:
        out.append("trillion_club_stake_kept_only_auto")
    return sorted(out)


#: DEFAULTs of the ledger (the server fills id / created_at / updated_at; no rule reads them).
_FINDING_DEFAULTS: Dict[str, Any] = {
    "verified_spans": [], "status": "new", "attempts": 0,
    "id": "<gen_random_uuid()>", "created_at": "<now()>", "updated_at": "<now()>",
}
_FINDING_NOT_NULL = (
    "id", "company_slug", "source", "accession", "filer_cik", "form", "filed_on", "document",
    "source_url", "tier", "outcome", "reason", "verified_spans", "status", "attempts",
    "created_at", "updated_at",
)
_F = FINDINGS_TABLE


def finding_check_violations(row: Mapping[str, Any]) -> List[str]:
    """The migration-205 rules a ``trillion_club_stake_findings`` row breaks, by constraint
    name (``NOTNULL:<column>`` first). Empty = 205 accepts it. Pure; wire values."""
    r: Dict[str, Any] = {**_FINDING_DEFAULTS, **row}
    missing = [f"NOTNULL:{c}" for c in _FINDING_NOT_NULL if r.get(c) is None]
    if missing:
        return missing
    g = r.get
    out: List[str] = []
    for col, allowed, name in (
        ("source", FINDING_SOURCES, f"{_F}_source_check"), ("tier", FINDING_TIERS, f"{_F}_tier_check"),
        ("outcome", FINDING_OUTCOMES, f"{_F}_outcome_check"), ("reason", REASON_CODES, f"{_F}_reason_check"),
        ("status", FINDING_STATUSES, f"{_F}_status_check"),
    ):
        if not isinstance(g(col), str) or g(col) not in allowed:
            out.append(name)
    decision = g("owner_decision")
    if decision is not None and (not isinstance(decision, str) or decision not in OWNER_DECISIONS):
        out.append(f"{_F}_decision_check")
    if (decision is None) != (g("decided_at") is None):
        out.append(f"{_F}_decision_dated")
    if not (isinstance(g("accession"), str) and _ACCESSION_RE.fullmatch(g("accession"))):
        out.append(f"{_F}_accession_check")
    if not _cik_ok(g("filer_cik")) or (g("investee_cik") is not None and not _cik_ok(g("investee_cik"))):
        out.append(f"{_F}_cik_check")
    row_ok = g("proposed_row") is None or (
        isinstance(g("proposed_row"), dict) and _json_text_bytes(g("proposed_row")) <= _PROPOSED_ROW_MAX_BYTES)
    spans = g("verified_spans")
    spans_ok = isinstance(spans, list) and len(spans) <= _SPANS_MAX_ITEMS \
        and _json_text_bytes(spans) <= _SPANS_MAX_BYTES
    if not (row_ok and spans_ok):
        out.append(f"{_F}_json_shapes")
    sha = g("fact_sha")
    if sha is not None and not (isinstance(sha, str) and _SHA_RE.fullmatch(sha)):
        out.append(f"{_F}_sha_check")
    form, document, url, err = g("form"), g("document"), g("source_url"), g("last_error")
    if not (isinstance(form, str) and 1 <= len(form) <= 40
            and isinstance(document, str) and 1 <= len(document) <= 255
            and isinstance(url, str) and url.startswith("https://") and len(url) <= 500
            and (err is None or (isinstance(err, str) and len(err) <= _LAST_ERROR_MAX))):
        out.append(f"{_F}_text_caps")
    attempts, message = g("attempts"), g("telegram_message_id")
    if not (isinstance(attempts, int) and not isinstance(attempts, bool) and attempts >= 0
            and (message is None or (isinstance(message, int) and not isinstance(message, bool)
                                     and message > 0))):
        out.append(f"{_F}_ranges")
    return sorted(out)


# ── Row payloads ─────────────────────────────────────────────────────────────────────────

#: Ledger columns no caller sets: the server owns id; the store stamps created_at/updated_at.
_FINDING_SERVER_COLUMNS = frozenset({"id", "created_at", "updated_at"})
#: Columns a compare-and-swap may never change (the claim's identity and the stamps).
_FINDING_FIXED_COLUMNS = frozenset({"id", "company_slug", "accession", "created_at", "updated_at"})
_FINDING_REQUIRED = ("company_slug", "source", "accession", "filer_cik", "form", "filed_on",
                     "document", "source_url", "tier", "outcome", "reason")


def _finding_value(col: str, value: Any) -> Any:
    """One ledger column as it goes on the wire, or ValueError (every 205 rule on one column)."""
    if col == "company_slug":
        return _slug(value)
    if col == "source":
        return _closed(value, FINDING_SOURCES, col)
    if col == "accession":
        return _accession(value)
    if col == "filer_cik":
        return _cik(value, col)
    if col == "investee_cik":
        return None if value is None else _cik(value, col)
    if col == "form":
        return _text(value, col, 1, 40)
    if col == "filed_on":
        return _iso_date(value, col)
    if col == "document":
        return _text(value, col, 1, 255)
    if col == "source_url":
        return _https(value, col, 500)
    if col == "tier":
        return _closed(value, FINDING_TIERS, col)
    if col == "outcome":
        return _closed(value, FINDING_OUTCOMES, col)
    if col == "reason":
        return _closed(value, REASON_CODES, col)
    if col == "status":
        return _closed(value, FINDING_STATUSES, col)
    if col == "owner_decision":
        return None if value is None else _closed(value, OWNER_DECISIONS, col)
    if col == "proposed_row":
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError(f"proposed_row must be a JSON object, got {type(value).__name__}")
        doc = _json_value(value, col)
        if _json_text_bytes(doc) > _PROPOSED_ROW_MAX_BYTES:
            raise ValueError(f"proposed_row is over {_PROPOSED_ROW_MAX_BYTES} bytes")
        return doc
    if col == "verified_spans":
        if not isinstance(value, (list, tuple)):
            raise ValueError(f"verified_spans must be a JSON array, got {type(value).__name__}")
        spans = _json_value(value, col)
        if len(spans) > _SPANS_MAX_ITEMS or _json_text_bytes(spans) > _SPANS_MAX_BYTES:
            raise ValueError(f"verified_spans is over {_SPANS_MAX_ITEMS} entries or {_SPANS_MAX_BYTES} bytes")
        return spans
    if col == "fact_sha":
        if value is not None and not (isinstance(value, str) and _SHA_RE.fullmatch(value)):
            raise ValueError("fact_sha must be 64 lower-case hex characters")
        return value
    if col == "stake_id":
        return None if value is None else _uuid(value, col)
    if col == "telegram_message_id":
        return None if value is None else _int(value, col, 1, _INT8_MAX)
    if col in ("notified_at", "published_at", "decided_at"):
        return _timestamp(value, col)
    if col == "attempts":
        return _int(value, col, 0, _INT4_MAX)
    if col == "last_error":
        return _sanitize_error(value)
    raise ValueError(f"{col!r} is not a column of {FINDINGS_TABLE}")


def _decision_pairing(payload: Mapping[str, Any]) -> None:
    """owner_decision and decided_at travel together (decision_dated): both keys or neither,
    both NULL or both set — the breaker counts Withdraws BY decided_at."""
    has_decision, has_time = "owner_decision" in payload, "decided_at" in payload
    if has_decision != has_time:
        raise ValueError("owner_decision and decided_at must be written together")
    if has_decision and (payload["owner_decision"] is None) != (payload["decided_at"] is None):
        raise ValueError("owner_decision and decided_at must both be set or both be NULL")


def _finding_insert_payload(row: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError(f"a finding row must be a mapping, got {type(row).__name__}")
    unknown = sorted(str(k) for k in row if k not in FINDING_COLUMNS)
    server = sorted(k for k in row if k in _FINDING_SERVER_COLUMNS)
    if unknown or server:
        raise ValueError(f"finding row: unknown columns {unknown}, server-owned columns {server}")
    missing = [c for c in _FINDING_REQUIRED if row.get(c) is None]
    if missing:
        raise ValueError(f"finding row: missing required columns {missing}")
    payload = {col: _finding_value(col, value) for col, value in row.items()}
    # A key left out takes its DEFAULT (NULL for the decision pair), so the rule check below
    # also catches a decision without its time.
    problems = finding_check_violations(payload)
    if problems:
        raise ValueError(f"finding row breaks migration 205: {problems}")
    return payload


def _finding_changes_payload(changes: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("update_finding needs a non-empty mapping of changes")
    bad = sorted(str(k) for k in changes if k not in FINDING_COLUMNS or k in _FINDING_FIXED_COLUMNS)
    if bad:
        raise ValueError(f"update_finding cannot change {bad} (unknown, or the claim's identity / the "
                         "store's stamps)")
    payload = {col: _finding_value(col, value) for col, value in changes.items()}
    for col in ("source", "accession", "filer_cik", "form", "filed_on", "document", "source_url",
                "tier", "outcome", "reason", "verified_spans", "status", "attempts"):
        if col in payload and payload[col] is None:
            raise ValueError(f"update_finding: {col} is NOT NULL")
    _decision_pairing(payload)
    return payload


#: A machine stake row: the 21 hand-kept columns plus what 205 adds for the finder.
_STAKE_INSERT_EXTRA = ("origin", "finding_id", "investee_cik")


def _stake_value(col: str, value: Any) -> Any:
    """One stakes column as it goes on the wire, or ValueError (175's column CHECKs)."""
    if col == "company_slug":
        return _slug(value)
    if col == "kind":
        return _closed(value, STAKE_KINDS, col)
    if col == "investee_name":
        return _text(value, col, 1, 60)
    if col == "investee_cusip":
        if value is not None and not (isinstance(value, str) and _CUSIP_RE.fullmatch(value)):
            raise ValueError(f"investee_cusip must be 9 upper-case letters/digits, got {value!r}")
        return value
    if col in ("investee_us_symbol", "local_listing", "ownership_basis"):
        return _opt_text(value, col)
    if col == "ownership_pct":
        pct = _opt_number(value, col)
        if pct is not None and not 0 < pct <= 100:
            raise ValueError(f"ownership_pct must be in (0, 100], got {value!r}")
        return pct
    if col == "disclosed_value_usd":
        amount = _opt_number(value, col)
        if amount is not None and amount <= 0:
            raise ValueError(f"disclosed_value_usd must be positive, got {value!r}")
        return amount
    if col == "value_basis":
        return None if value is None else _closed(value, VALUE_BASES, col)
    if col in ("as_of", "verified_on"):
        return _iso_date(value, col)
    if col == "listed_since":
        return _opt_iso_date(value, col)
    if col == "source_title":
        return _text(value, col, 1, 120)
    if col == "source_url":
        return _https(value, col, 10_000)
    if col == "source_confidence":
        return _closed(value, _SOURCE_CONFIDENCES, col)
    if col in ("material", "tied_to_deal", "published"):
        return _bool(value, col)
    if col == "background":
        return _opt_text(value, col, 90)
    if col == "sort_order":
        return _int(value, col, _INT4_MIN, _INT4_MAX)
    if col == "investee_cik":
        return None if value is None else _cik(value, col)
    if col == "origin":
        return _closed(value, ORIGINS, col)
    if col == "finding_id":
        return _uuid(value, col)
    raise ValueError(f"{col!r} is not a column a machine stake row carries")


def _stake_payload(row: Mapping[str, Any], *, extra: Sequence[str], required_extra: Sequence[str]) -> Dict[str, Any]:
    if not isinstance(row, Mapping):
        raise ValueError(f"a stake row must be a mapping, got {type(row).__name__}")
    allowed = set(STAKE_COLUMNS) | set(extra)
    unknown = sorted(str(k) for k in row if k not in allowed)
    missing = [c for c in (*STAKE_COLUMNS, *required_extra) if c not in row]
    if unknown or missing:
        raise ValueError(f"stake row: unknown columns {unknown}, missing columns {missing}")
    payload = {col: _stake_value(col, value) for col, value in row.items()}
    # 175's table CHECKs (the column ones ran in _stake_value).
    if payload["disclosed_value_usd"] is not None and payload["value_basis"] is None:
        raise ValueError("stake row breaks trillion_club_stake_value_has_basis")
    if payload["kind"] == "commitment" and payload["value_basis"] not in (None, "committed_up_to"):
        raise ValueError("stake row breaks trillion_club_commitment_basis")
    if payload["published"] and payload["source_confidence"] == "secondary":
        raise ValueError("stake row breaks trillion_club_secondary_never_published")
    return payload


def _rows_of(response: Any) -> Optional[List[Dict[str, Any]]]:
    """The rows a write answered, or None when the answer is not the list PostgREST promises
    (treated like a lost response: the store re-reads and decides)."""
    data = getattr(response, "data", None)
    if isinstance(data, list) and all(isinstance(r, dict) for r in data):
        return [dict(r) for r in data]
    return None


def _created_key(row: Mapping[str, Any], col: str) -> Tuple[datetime, str]:
    return _parse_ts(row.get(col)) or _EPOCH, str(row.get("id") or "")


class _AmbiguousAnswer(Exception):
    """A write answered in a shape PostgREST never sends — decided like a lost response."""


# ── The store ────────────────────────────────────────────────────────────────────────────


class StakeStore:
    """The finder's and the taps' only Supabase access. ``client`` is a Supabase client (the
    service-role one from ``get_supabase()`` when omitted, resolved on first use); tests pass
    ``tests/_trillion_club_stake_fakes.FakeSupabase``.

    Every write takes ``now`` (timezone-aware) and stamps ``updated_at`` with it (the tables
    have no trigger); timestamps travel as ISO strings."""

    def __init__(self, client: Any = None):
        self._client = client

    def _db(self) -> Any:
        if self._client is None:
            from app.database import get_supabase

            self._client = get_supabase()
        return self._client

    # ── plumbing ─────────────────────────────────────────────────────────────────────

    async def _read(self, op: str, build: Callable[[], Any]) -> List[Dict[str, Any]]:
        """One SELECT off the event loop, retried on a transient failure (reads are
        idempotent). ``build`` returns a FRESH query each attempt."""
        try:
            response = await retry_idempotent_async(
                lambda: build().execute(), what=f"trillion club stakes {op}",
                attempts=READ_ATTEMPTS, backoff_seconds=READ_BACKOFF_SECONDS, logger=logger,
            )
        except Exception as e:
            raise StakeStoreError(op, e) from e
        data = getattr(response, "data", None)
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise StakeStoreError(op, TypeError(f"expected a list of rows, got {type(data).__name__}"))
        return [dict(r) for r in data]

    async def _read_all(self, op: str, build: Callable[[], Any], *, max_rows: int) -> List[Dict[str, Any]]:
        """Every row ``build()`` selects, keyset-paged on ``id`` until an EMPTY page — so a
        server max-rows below the page size cannot end the read early. Reaching ``max_rows``
        raises (never a partial table)."""
        if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
            raise ValueError(f"{op}: max_rows must be a positive integer, got {max_rows!r}")
        rows: List[Dict[str, Any]] = []
        after: Optional[str] = None
        while True:
            want = min(PAGE_ROWS, max_rows - len(rows))

            def page(after: Optional[str] = after, want: int = want) -> Any:
                query = build()
                if after is not None:
                    query = query.gt("id", after)
                return query.order("id").limit(want)

            batch = await self._read(op, page)
            if not batch:
                return rows
            if len(batch) > want:
                raise StakeStoreError(op, ValueError(f"asked for {want} rows, the answer held {len(batch)}"))
            for r in batch:
                rid = r.get("id")
                if not isinstance(rid, str) or (after is not None and not rid > after):
                    raise StakeStoreError(op, ValueError("the answer is not in id order (a page "
                                                         "boundary moved, or a row has no id)"))
                after = rid
            rows.extend(batch)
            if len(rows) >= max_rows:
                raise StakeStoreError(op, OverflowError(
                    f"reached the {max_rows}-row ceiling — refusing to treat it as the whole table"))

    async def _reread_stake(self, op: str, error: BaseException, stake_id: str) -> Optional[Dict[str, Any]]:
        try:
            return await self.read_stake(stake_id)
        except StakeStoreError as read_error:
            logger.warning("%s %s: the re-read after a failed write failed too (%s)", _TAG, op,
                           _error_text(read_error.cause))
            raise StakeStoreError(op, error) from error

    async def _stake_write(
        self,
        op: str,
        stake_id: str,
        query: Any,
        decide: Callable[[Optional[Dict[str, Any]]], Optional[bool]],
        *,
        reread_on_miss: bool = False,
    ) -> bool:
        """One fenced stakes UPDATE (single-shot). ``decide(row)`` reads a re-read row:
        True = the write's effect is there, False = the row can no longer take it, None =
        it could but does not (not applied)."""
        try:
            rows = _rows_of(await sb_exec(query))
            if rows is None or len(rows) > 1 or any(r.get("id") != stake_id for r in rows):
                raise _AmbiguousAnswer("the UPDATE answered something that is not the row it was sent for")
        except Exception as e:
            logger.warning("%s %s: the write raised (%s) — re-reading to decide", _TAG, op, _error_text(e))
            row = await self._reread_stake(op, e, stake_id)
            verdict = decide(row)
            if verdict is None:
                if is_unique_violation(e):
                    raise StakeConflict(op, e) from e
                raise StakeStoreError(op, e) from e
            logger.info("%s %s: %s", _TAG, op, "the write landed despite the error" if verdict
                        else "the row can no longer take the write (lost race)")
            return verdict
        if rows:
            return True
        if reread_on_miss:
            row = await self.read_stake(stake_id)
            verdict = decide(row)
            if verdict is None:
                raise StakeStoreError(op, LookupError(
                    "the fence matched no row, yet the re-read shows a row that could take the write"))
            return verdict
        logger.info("%s %s: the fence matched no row (lost race)", _TAG, op)
        return False

    # ── Reads ────────────────────────────────────────────────────────────────────────

    async def read_stakes(self) -> List[Dict[str, Any]]:
        """Every row of ``trillion_club_stakes`` (any origin, kind or published state),
        ``STAKE_READ_COLUMNS``, ordered by id. Raises at ``MAX_STAKE_ROWS``."""
        cols = ",".join(STAKE_READ_COLUMNS)
        return await self._read_all(
            "read stakes", lambda: self._db().table(STAKES_TABLE).select(cols), max_rows=MAX_STAKE_ROWS,
        )

    async def read_stake(self, stake_id: str) -> Optional[Dict[str, Any]]:
        sid = _uuid(stake_id, "stake_id")
        op = f"read stake={sid}"
        rows = await self._read(op, lambda: self._db().table(STAKES_TABLE)
                                .select(",".join(STAKE_READ_COLUMNS)).eq("id", sid).limit(2))
        if len(rows) > 1 or (rows and rows[0].get("id") != sid):
            raise StakeStoreError(op, ValueError("the answer is not the one row asked for"))
        return rows[0] if rows else None

    async def read_stake_by_finding(self, finding_id: str) -> Optional[Dict[str, Any]]:
        """The stake row carrying ``finding_id``, or None. Two rows carrying one finding is
        never guessed between: it raises."""
        fid = _uuid(finding_id, "finding_id")
        op = f"read stake by finding={fid}"
        rows = await self._read(op, lambda: self._db().table(STAKES_TABLE)
                                .select(",".join(STAKE_READ_COLUMNS)).eq("finding_id", fid).limit(2))
        if len(rows) > 1:
            raise StakeStoreError(op, ValueError(f"{len(rows)} stake rows carry one finding"))
        if rows and rows[0].get("finding_id") != fid:
            raise StakeStoreError(op, ValueError("the answer carries another finding"))
        return rows[0] if rows else None

    async def read_latest_complete_holdings(self, cik: str) -> Optional[Dict[str, Any]]:
        """The newest ``complete`` 13F build of ``cik`` → ``{"period", "cusips", "symbols"}``
        (CUSIPs through ``cusip_identity``, symbols through ``normalize_symbol``; unusable
        entries dropped), or None when the CIK has no complete build. One query."""
        cik = _cik(cik, "cik")
        op = f"read latest complete 13F cik={cik}"
        rows = await self._read(op, lambda: self._db().table(FILINGS_TABLE)
                                .select("cik,period,build_status,holdings").eq("cik", cik)
                                .eq("build_status", BUILD_COMPLETE).order("period", desc=True).limit(1))
        if not rows:
            return None
        row = rows[0]
        period = row.get("period")
        if (len(rows) > 1 or row.get("cik") != cik or row.get("build_status") != BUILD_COMPLETE
                or not isinstance(period, str) or not _PERIOD_RE.fullmatch(period)):
            raise StakeStoreError(op, ValueError("the answer is not one complete build of that CIK"))
        holdings = row.get("holdings")
        if not isinstance(holdings, list):
            # A complete build whose holdings cannot be read: never guess "not on the 13F".
            raise StakeStoreError(op, TypeError(f"holdings is {type(holdings).__name__}, not a list"))
        cusips, symbols, skipped = set(), set(), 0
        for h in holdings:
            if not isinstance(h, Mapping):
                skipped += 1
                continue
            cusip, symbol = cusip_identity(h.get("cusip")), normalize_symbol(h.get("symbol"))
            if cusip:
                cusips.add(cusip)
            if symbol:
                symbols.add(symbol)
        if skipped:
            logger.warning("%s %s period=%s: skipped %d holdings entries that are not objects",
                           _TAG, op, period, skipped)
        return {"period": period, "cusips": frozenset(cusips), "symbols": frozenset(symbols)}

    async def read_finding(self, finding_id: str) -> Optional[Dict[str, Any]]:
        fid = _uuid(finding_id, "finding_id")
        op = f"read finding={fid}"
        rows = await self._read(op, lambda: self._db().table(FINDINGS_TABLE)
                                .select(",".join(FINDING_COLUMNS)).eq("id", fid).limit(2))
        if len(rows) > 1 or (rows and rows[0].get("id") != fid):
            raise StakeStoreError(op, ValueError("the answer is not the one row asked for"))
        return rows[0] if rows else None

    async def _finding_by_key(self, slug: str, accession: str) -> Optional[Dict[str, Any]]:
        found = await self.read_findings_for_accessions(slug, [accession])
        return found.get(accession)

    async def read_findings_for_accessions(self, slug: str, accessions: Iterable[str]) -> Dict[str, Dict[str, Any]]:
        """``{accession: finding}`` for the member's findings among ``accessions`` (dashed;
        validated before any I/O). One request per ``ACCESSION_CHUNK`` accessions."""
        slug = _slug(slug)
        wanted = unique_preserving(_accession(a) for a in accessions)
        out: Dict[str, Dict[str, Any]] = {}
        for start in range(0, len(wanted), ACCESSION_CHUNK):
            chunk = wanted[start:start + ACCESSION_CHUNK]
            op = f"read findings slug={slug} accessions={len(chunk)} (from {chunk[0]})"
            rows = await self._read(
                op,
                lambda chunk=chunk: self._db().table(FINDINGS_TABLE).select(",".join(FINDING_COLUMNS))
                .eq("company_slug", slug).in_("accession", chunk).limit(len(chunk) + 1),
            )
            asked = set(chunk)
            for r in rows:
                acc = r.get("accession")
                if r.get("company_slug") != slug or acc not in asked or acc in out:
                    raise StakeStoreError(op, ValueError("the answer holds a row that was not asked for, "
                                                         "or one accession twice"))
                out[acc] = r
        return out

    async def _findings(self, op: str, build: Callable[[], Any], order_col: str) -> List[Dict[str, Any]]:
        rows = await self._read_all(op, build, max_rows=MAX_FINDING_ROWS)
        return sorted(rows, key=lambda r: _created_key(r, order_col))

    async def read_findings_by_status(self, statuses: Iterable[str]) -> List[Dict[str, Any]]:
        """Every finding in one of ``statuses``, oldest ``created_at`` first (id breaks ties)."""
        wanted = unique_preserving(statuses) if not isinstance(statuses, str) else [statuses]
        if not wanted:
            raise ValueError("read_findings_by_status needs at least one status")
        for s in wanted:
            _closed(s, FINDING_STATUSES, "status")
        cols = ",".join(FINDING_COLUMNS)
        return await self._findings(
            f"read findings statuses={','.join(wanted)}",
            lambda: self._db().table(FINDINGS_TABLE).select(cols).in_("status", list(wanted)),
            "created_at",
        )

    async def read_findings_for_stake(self, stake_id: str) -> List[Dict[str, Any]]:
        """Every finding linked to ``stake_id``, oldest first."""
        sid = _uuid(stake_id, "stake_id")
        cols = ",".join(FINDING_COLUMNS)
        return await self._findings(
            f"read findings for stake={sid}",
            lambda: self._db().table(FINDINGS_TABLE).select(cols).eq("stake_id", sid),
            "created_at",
        )

    async def read_findings_since(self, since: datetime) -> List[Dict[str, Any]]:
        """Every finding with ``created_at >= since``, oldest first."""
        stamp = _iso(_aware(since, "since"))
        cols = ",".join(FINDING_COLUMNS)
        return await self._findings(
            f"read findings since={stamp}",
            lambda: self._db().table(FINDINGS_TABLE).select(cols).gte("created_at", stamp),
            "created_at",
        )

    async def read_auto_volume(self, since: datetime) -> List[Dict[str, Any]]:
        """What gate 8 (the auto-publish volume caps) counts — never by ``created_at``, which says
        nothing about when a row went live. Two paged reads: every AUTO finding still on its way
        to a publish (``AUTO_IN_FLIGHT_STATUSES``, whatever its age — each is a publish in
        waiting), and every auto finding PUBLISHED at or after ``since`` (``published_at``),
        whatever became of it afterwards. A finding in both comes back once; oldest created
        first. Raises at ``MAX_FINDING_ROWS`` (a cap is never counted on a sample)."""
        stamp = _iso(_aware(since, "since"))
        cols = ",".join(FINDING_COLUMNS)
        waiting = await self._findings(
            "read auto findings in flight",
            lambda: self._db().table(FINDINGS_TABLE).select(cols).eq("outcome", OUTCOME_AUTO)
            .in_("status", list(AUTO_IN_FLIGHT_STATUSES)),
            "created_at",
        )
        live = await self._findings(
            f"read auto findings published since={stamp}",
            lambda: self._db().table(FINDINGS_TABLE).select(cols).eq("outcome", OUTCOME_AUTO)
            .gte("published_at", stamp),
            "created_at",
        )
        out: Dict[str, Dict[str, Any]] = {}
        for r in waiting + live:
            out.setdefault(str(r.get("id")), r)
        return sorted(out.values(), key=lambda r: _created_key(r, "created_at"))

    async def read_findings_for_pair(self, slug: str, investee_cik: str) -> List[Dict[str, Any]]:
        """Every finding of ONE (member, investee CIK) pair, any status, oldest first — the exit
        filings on file for the pair (the publish gates and the Publish tap refuse a row dated on
        or before the newest one) and the Monday re-check's "superseding filing" test. One paged
        read over the (company_slug, investee_cik) index; raises at ``MAX_FINDING_ROWS``."""
        slug = _slug(slug)
        cik = _cik(investee_cik, "investee_cik")
        cols = ",".join(FINDING_COLUMNS)
        return await self._findings(
            f"read findings slug={slug} investee_cik={cik}",
            lambda: self._db().table(FINDINGS_TABLE).select(cols).eq("company_slug", slug)
            .eq("investee_cik", cik),
            "created_at",
        )

    async def read_decisions_since(self, decision: str, since: datetime) -> List[Dict[str, Any]]:
        """Every finding whose ``owner_decision`` is ``decision`` with ``decided_at >= since``,
        oldest decision first (the breaker counts Withdraws this way)."""
        decision = _closed(decision, OWNER_DECISIONS, "decision")
        stamp = _iso(_aware(since, "since"))
        cols = ",".join(FINDING_COLUMNS)
        return await self._findings(
            f"read decisions={decision} since={stamp}",
            lambda: self._db().table(FINDINGS_TABLE).select(cols).eq("owner_decision", decision)
            .gte("decided_at", stamp),
            "decided_at",
        )

    # ── Ledger writes ────────────────────────────────────────────────────────────────

    async def insert_finding(self, row: Mapping[str, Any], *, now: datetime) -> Tuple[Dict[str, Any], bool]:
        """Write-ahead INSERT of one finding (single-shot) → ``(stored row, created)``.

        UNIQUE (company_slug, accession) is the exactly-once claim: an existing finding comes
        back as ``(that row, False)``. After an ambiguous error the row is re-read: ours (its
        created_at is this call's ``now``) → ``(row, True)``; someone else's → ``(row, False)``;
        absent → StakeStoreError. The dashed accession, the 10-digit CIKs and every 205 rule
        are checked before any I/O (ValueError)."""
        stamp = _iso(_aware(now, "now"))
        payload = _finding_insert_payload(row)
        payload["created_at"] = payload["updated_at"] = stamp
        slug, acc = payload["company_slug"], payload["accession"]
        op = f"insert finding slug={slug} accession={acc} cik={payload['filer_cik']} reason={payload['reason']}"
        query = self._db().table(FINDINGS_TABLE).insert(payload)
        try:
            rows = _rows_of(await sb_exec(query))
            if rows is None or len(rows) != 1 or rows[0].get("accession") != acc:
                raise _AmbiguousAnswer("the INSERT did not answer with the inserted row")
            return rows[0], True
        except Exception as e:
            unique = is_unique_violation(e)
            if not unique:
                logger.warning("%s %s: the write raised (%s) — re-reading to decide", _TAG, op, _error_text(e))
            try:
                existing = await self._finding_by_key(slug, acc)
            except StakeStoreError as read_error:
                logger.warning("%s %s: the re-read after a failed write failed too (%s)", _TAG, op,
                               _error_text(read_error.cause))
                raise StakeStoreError(op, e) from e
            if existing is None:
                raise StakeStoreError(op, e) from e
            created = not unique and _same_instant(existing.get("created_at"), now)
            logger.info("%s %s: finding=%s already %s", _TAG, op, existing.get("id"),
                        "written by this call (the answer was lost)" if created else "on file")
            return existing, created

    async def update_finding(
        self,
        finding_id: str,
        *,
        expected_status: str,
        changes: Mapping[str, Any],
        now: datetime,
        message_id: Optional[int] = None,
        fact_sha: Optional[str] = None,
    ) -> bool:
        """Compare-and-swap on the finding's observed status (and its Telegram message /
        fact_sha when given). True = this call's change landed; False = the fence missed (a
        lost race, or the row moved on). After an ambiguous error the row is re-read: our
        change with our ``updated_at`` → True; still at the fence without it →
        StakeStoreError; anything else → False."""
        fid = _uuid(finding_id, "finding_id")
        expected = _closed(expected_status, FINDING_STATUSES, "expected_status")
        stamp = _iso(_aware(now, "now"))
        payload = _finding_changes_payload(changes)
        if message_id is not None:
            message_id = _int(message_id, "message_id", 1, _INT8_MAX)
        if fact_sha is not None and not (isinstance(fact_sha, str) and _SHA_RE.fullmatch(fact_sha)):
            raise ValueError("fact_sha must be 64 lower-case hex characters")
        if (expected in _FACTS_FROZEN and payload.get("status", expected) in _FACTS_FROZEN
                and ("proposed_row" in payload or "fact_sha" in payload)):
            # A proposal's message is out: a Publish tap carries only (verb, finding id, message id),
            # so the row it publishes must be the row the message showed. Facts change only by
            # leaving the open states (a heads-up, an expiry) — never in place.
            raise ValueError(f"finding={fid}: the facts of an open proposal never change in place")
        payload["updated_at"] = stamp
        op = f"update finding={fid} from status={expected} to {payload.get('status', expected)}"
        query = self._db().table(FINDINGS_TABLE).update(payload).eq("id", fid).eq("status", expected)
        if message_id is not None:
            query = query.eq("telegram_message_id", message_id)
        if fact_sha is not None:
            query = query.eq("fact_sha", fact_sha)
        try:
            rows = _rows_of(await sb_exec(query))
            if rows is None or len(rows) > 1 or any(r.get("id") != fid for r in rows):
                raise _AmbiguousAnswer("the UPDATE answered something that is not the row it was sent for")
        except Exception as e:
            logger.warning("%s %s: the write raised (%s) — re-reading to decide", _TAG, op, _error_text(e))
            try:
                current = await self.read_finding(fid)
            except StakeStoreError as read_error:
                logger.warning("%s %s: the re-read after a failed write failed too (%s)", _TAG, op,
                               _error_text(read_error.cause))
                raise StakeStoreError(op, e) from e
            if current is None:
                logger.info("%s %s: the finding is gone (lost race)", _TAG, op)
                return False
            if all(_same(col, current.get(col), value) for col, value in payload.items()):
                logger.info("%s %s slug=%s accession=%s: the write landed despite the error", _TAG, op,
                            current.get("company_slug"), current.get("accession"))
                return True
            at_fence = (current.get("status") == expected
                        and (message_id is None or current.get("telegram_message_id") == message_id)
                        and (fact_sha is None or current.get("fact_sha") == fact_sha))
            if at_fence:
                raise StakeStoreError(op, e) from e
            logger.info("%s %s slug=%s accession=%s: the row moved on (now status=%s; lost race)", _TAG, op,
                        current.get("company_slug"), current.get("accession"), current.get("status"))
            return False
        if not rows:
            logger.info("%s %s: the fence matched no row (lost race)", _TAG, op)
        return bool(rows)

    # ── Stake writes ─────────────────────────────────────────────────────────────────

    async def insert_stake(self, row: Mapping[str, Any], *, now: datetime) -> Dict[str, Any]:
        """Single-shot INSERT of an UNPUBLISHED machine row → the stored row.

        ``row`` = the 21 ``STAKE_COLUMNS`` + ``origin`` (auto | proposal) + ``finding_id``
        (+ optional ``investee_cik``); ``published`` must be False. Another row holding the
        stake key → StakeConflict. After any error the row is re-read by finding_id: present →
        it is returned (this finding's row exists, whichever attempt wrote it); absent →
        StakeConflict / StakeStoreError."""
        stamp = _iso(_aware(now, "now"))
        payload = _stake_payload(row, extra=_STAKE_INSERT_EXTRA, required_extra=("origin", "finding_id"))
        if payload["published"] is not False:
            raise ValueError("insert_stake writes an UNPUBLISHED row (publish_stake flips it)")
        if payload["origin"] not in MACHINE_ORIGINS:
            raise ValueError(f"insert_stake writes machine rows only, got origin {payload['origin']!r}")
        problems = stake_check_violations({**payload, "owner_kept_at": None, "withdrawn_at": None})
        if problems:
            raise ValueError(f"stake row breaks migration 205: {problems}")
        payload["created_at"] = payload["updated_at"] = stamp
        fid, slug = payload["finding_id"], payload["company_slug"]
        op = f"insert stake slug={slug} finding={fid} origin={payload['origin']}"
        query = self._db().table(STAKES_TABLE).insert(payload)
        try:
            rows = _rows_of(await sb_exec(query))
            if rows is None or len(rows) != 1 or rows[0].get("finding_id") != fid:
                raise _AmbiguousAnswer("the INSERT did not answer with the inserted row")
            return rows[0]
        except Exception as e:
            logger.warning("%s %s: the write raised (%s) — re-reading by finding id", _TAG, op, _error_text(e))
            try:
                existing = await self.read_stake_by_finding(fid)
            except StakeStoreError as read_error:
                logger.warning("%s %s: the re-read after a failed write failed too (%s)", _TAG, op,
                               _error_text(read_error.cause))
                raise StakeStoreError(op, e) from e
            if existing is not None:
                logger.info("%s %s: stake=%s for this finding is on file", _TAG, op, existing.get("id"))
                return existing
            if is_unique_violation(e):
                raise StakeConflict(op, e) from e
            raise StakeStoreError(op, e) from e

    async def publish_stake(self, stake_id: str, finding_id: str, *, now: datetime) -> bool:
        """The flip: ``published=true WHERE id AND finding_id AND origin IN (auto, proposal)
        AND published=false AND withdrawn_at IS NULL``. A Withdraw that landed first wins (False).
        Idempotent: a row this finding already put live answers True, so a retry after an
        unknown outcome (the answer lost, then the re-read failed too) never reads as a lost race."""
        sid, fid = _uuid(stake_id, "stake_id"), _uuid(finding_id, "finding_id")
        stamp = _iso(_aware(now, "now"))
        query = (self._db().table(STAKES_TABLE).update({"published": True, "updated_at": stamp})
                 .eq("id", sid).eq("finding_id", fid).in_("origin", list(MACHINE_ORIGINS))
                 .eq("published", False).is_("withdrawn_at", "null"))

        def decide(row: Optional[Dict[str, Any]]) -> Optional[bool]:
            if (row is None or row.get("finding_id") != fid or row.get("origin") not in MACHINE_ORIGINS
                    or row.get("withdrawn_at") is not None):
                return False
            return True if row.get("published") is True else None

        return await self._stake_write(f"publish stake={sid} finding={fid}", sid, query, decide,
                                       reread_on_miss=True)

    async def withdraw_stake(self, stake_id: str, *, now: datetime) -> bool:
        """The owner's Withdraw: ``published=false, withdrawn_at=now WHERE id AND origin IN
        (auto, proposal) AND withdrawn_at IS NULL`` — no finding or message fence (unpublishing
        is always safe). Already withdrawn → True (idempotent); missing or a seed row → False."""
        sid = _uuid(stake_id, "stake_id")
        stamp = _iso(_aware(now, "now"))
        query = (self._db().table(STAKES_TABLE)
                 .update({"published": False, "withdrawn_at": stamp, "updated_at": stamp})
                 .eq("id", sid).in_("origin", list(MACHINE_ORIGINS)).is_("withdrawn_at", "null"))

        def decide(row: Optional[Dict[str, Any]]) -> Optional[bool]:
            if row is None or row.get("origin") not in MACHINE_ORIGINS:
                return False
            return True if row.get("withdrawn_at") is not None else None

        return await self._stake_write(f"withdraw stake={sid}", sid, query, decide, reread_on_miss=True)

    async def keep_stake(self, stake_id: str, *, now: datetime) -> bool:
        """The owner's Keep: ``owner_kept_at=now WHERE id AND origin='auto' AND withdrawn_at
        IS NULL`` (the tap's message fence lives on the finding's compare-and-swap)."""
        sid = _uuid(stake_id, "stake_id")
        stamp = _iso(_aware(now, "now"))
        query = (self._db().table(STAKES_TABLE).update({"owner_kept_at": stamp, "updated_at": stamp})
                 .eq("id", sid).eq("origin", ORIGIN_AUTO).is_("withdrawn_at", "null"))

        def decide(row: Optional[Dict[str, Any]]) -> Optional[bool]:
            if row is None or row.get("origin") != ORIGIN_AUTO or row.get("withdrawn_at") is not None:
                return False
            return True if row.get("owner_kept_at") is not None else None

        return await self._stake_write(f"keep stake={sid}", sid, query, decide)

    async def unpublish_auto(self, stake_id: str, *, now: datetime) -> bool:
        """The exit path: ``published=false WHERE id AND origin='auto'`` — ``withdrawn_at``
        untouched (an exit filing is not the owner's no)."""
        sid = _uuid(stake_id, "stake_id")
        stamp = _iso(_aware(now, "now"))
        query = (self._db().table(STAKES_TABLE).update({"published": False, "updated_at": stamp})
                 .eq("id", sid).eq("origin", ORIGIN_AUTO))

        def decide(row: Optional[Dict[str, Any]]) -> Optional[bool]:
            if row is None or row.get("origin") != ORIGIN_AUTO:
                return False
            return True if row.get("published") is False else None

        return await self._stake_write(f"unpublish auto stake={sid}", sid, query, decide)

    async def bump_verified_on(self, stake_id: str, finding_id: str, day: date, *, now: datetime) -> bool:
        """The Monday re-check's pass: ``verified_on=day`` on a LIVE machine row of this
        finding (id + finding_id + origin IN machine + published=true)."""
        sid, fid = _uuid(stake_id, "stake_id"), _uuid(finding_id, "finding_id")
        if isinstance(day, datetime) or not isinstance(day, date):
            raise ValueError(f"day must be a date, got {day!r}")
        stamp, day_iso = _iso(_aware(now, "now")), day.isoformat()
        query = (self._db().table(STAKES_TABLE).update({"verified_on": day_iso, "updated_at": stamp})
                 .eq("id", sid).eq("finding_id", fid).in_("origin", list(MACHINE_ORIGINS))
                 .eq("published", True))

        def decide(row: Optional[Dict[str, Any]]) -> Optional[bool]:
            if (row is None or row.get("finding_id") != fid or row.get("origin") not in MACHINE_ORIGINS
                    or row.get("published") is not True):
                return False
            return True if row.get("verified_on") == day_iso else None

        return await self._stake_write(f"bump verified_on stake={sid} finding={fid} day={day_iso}",
                                       sid, query, decide)

    async def adopt_as_proposal(self, stake_id: str, finding_id: str, *, now: datetime) -> bool:
        """Reuse an unpublished auto row as this finding's proposal (a gate downgraded it during
        reconcile): ``origin='proposal', owner_kept_at=NULL WHERE id AND finding_id AND
        origin='auto' AND published=false AND withdrawn_at IS NULL`` — Keep cleared in the same
        write (kept_only_auto). Idempotent: this finding's row that is already a proposal (and not
        withdrawn) answers True; a withdrawn one answers False."""
        sid, fid = _uuid(stake_id, "stake_id"), _uuid(finding_id, "finding_id")
        stamp = _iso(_aware(now, "now"))
        query = (self._db().table(STAKES_TABLE)
                 .update({"origin": ORIGIN_PROPOSAL, "owner_kept_at": None, "updated_at": stamp})
                 .eq("id", sid).eq("finding_id", fid).eq("origin", ORIGIN_AUTO).eq("published", False)
                 .is_("withdrawn_at", "null"))

        def decide(row: Optional[Dict[str, Any]]) -> Optional[bool]:
            if row is None or row.get("finding_id") != fid or row.get("withdrawn_at") is not None:
                return False
            if row.get("origin") == ORIGIN_PROPOSAL:
                return True
            if row.get("origin") == ORIGIN_AUTO and row.get("published") is False:
                return None
            return False

        return await self._stake_write(f"adopt as proposal stake={sid} finding={fid}", sid, query, decide,
                                       reread_on_miss=True)

    async def replace_stake(
        self,
        stake_id: str,
        *,
        expected_updated_at: Any,
        row: Mapping[str, Any],
        finding_id: str,
        now: datetime,
    ) -> bool:
        """An UPDATE proposal, published: the whole row replaced in place — the 21
        ``STAKE_COLUMNS`` and ``investee_cik`` from ``row`` (both required; investee_cik may be
        None), ``origin='proposal'``, ``finding_id``, ``owner_kept_at=NULL`` — fenced on id,
        the ``updated_at`` the caller read, ``withdrawn_at IS NULL``, a machine origin and the
        same member (a replace never moves a row to another company). Another row holding the
        new stake key → StakeConflict."""
        sid, fid = _uuid(stake_id, "stake_id"), _uuid(finding_id, "finding_id")
        stamp = _iso(_aware(now, "now"))
        expected = _parse_ts(expected_updated_at)
        if expected is None:
            raise ValueError(f"expected_updated_at must be the row's updated_at as read, got {expected_updated_at!r}")
        payload = _stake_payload(row, extra=("investee_cik",), required_extra=("investee_cik",))
        payload.update(origin=ORIGIN_PROPOSAL, finding_id=fid, owner_kept_at=None)
        problems = stake_check_violations({**payload, "withdrawn_at": None})
        if problems:
            raise ValueError(f"stake row breaks migration 205: {problems}")
        payload["updated_at"] = stamp
        slug = payload["company_slug"]
        query = (self._db().table(STAKES_TABLE).update(payload)
                 .eq("id", sid).eq("updated_at", _iso(expected)).is_("withdrawn_at", "null")
                 .in_("origin", list(MACHINE_ORIGINS)).eq("company_slug", slug))

        def decide(current: Optional[Dict[str, Any]]) -> Optional[bool]:
            if current is None:
                return False
            if all(_same(col, current.get(col), value) for col, value in payload.items() if col != "updated_at"):
                return True
            if (_same_instant(current.get("updated_at"), expected) and current.get("withdrawn_at") is None
                    and current.get("origin") in MACHINE_ORIGINS and current.get("company_slug") == slug):
                return None
            return False

        return await self._stake_write(f"replace stake={sid} slug={slug} finding={fid}", sid, query, decide)

    async def withdraw_stakes_for_finding(self, finding_id: str, *, now: datetime) -> int:
        """Dismiss / expiry: ``published=false, withdrawn_at=now`` on every machine stake
        carrying ``finding_id`` with ``withdrawn_at`` NULL → how many this call withdrew
        (after a lost answer: the rows whose withdrawn_at is this call's ``now``)."""
        fid = _uuid(finding_id, "finding_id")
        stamp = _iso(_aware(now, "now"))
        op = f"withdraw stakes for finding={fid}"
        query = (self._db().table(STAKES_TABLE)
                 .update({"published": False, "withdrawn_at": stamp, "updated_at": stamp})
                 .eq("finding_id", fid).in_("origin", list(MACHINE_ORIGINS)).is_("withdrawn_at", "null"))
        try:
            rows = _rows_of(await sb_exec(query))
            if rows is None:
                raise _AmbiguousAnswer("the UPDATE answered something that is not a list of rows")
            return len(rows)
        except Exception as e:
            logger.warning("%s %s: the write raised (%s) — re-reading to decide", _TAG, op, _error_text(e))
            try:
                current = await self._read_all(
                    op, lambda: self._db().table(STAKES_TABLE).select(",".join(STAKE_READ_COLUMNS))
                    .eq("finding_id", fid), max_rows=MAX_STAKE_ROWS)
            except StakeStoreError as read_error:
                logger.warning("%s %s: the re-read after a failed write failed too (%s)", _TAG, op,
                               _error_text(read_error.cause))
                raise StakeStoreError(op, e) from e
            machine = [r for r in current if r.get("origin") in MACHINE_ORIGINS]
            if any(r.get("withdrawn_at") is None for r in machine):
                raise StakeStoreError(op, e) from e
            mine = sum(1 for r in machine if _same_instant(r.get("withdrawn_at"), now))
            logger.info("%s %s: %d row(s) withdrawn by this call despite the error", _TAG, op, mine)
            return mine


__all__ = [
    "STAKES_TABLE", "FINDINGS_TABLE", "FILINGS_TABLE", "STAKE_EXTRA_COLUMNS", "STAKE_READ_COLUMNS",
    "FINDING_COLUMNS", "AUTO_IN_FLIGHT_STATUSES", "MAX_STAKE_ROWS", "MAX_FINDING_ROWS", "PAGE_ROWS", "ACCESSION_CHUNK",
    "READ_ATTEMPTS", "READ_BACKOFF_SECONDS", "BUILD_COMPLETE", "StakeStoreError", "StakeConflict",
    "StakeStore", "stake_check_violations", "finding_check_violations",
]
