"""An in-memory PostgREST for the Trillion Club stake finder's tests — migrations 175 + 205 enforced.

WHY THIS EXISTS
---------------
The finder's write orders, taps and reconcile are only as correct as the database answers they
are tested against. A fake that accepts a row Postgres refuses, ignores a filter, or returns
more rows than PostgREST would, makes a broken write order look green. So this one refuses
what the real tables refuse and answers the way PostgREST answers:

* every 205 rule through ``test_trillion_club_stake_migration.model_violations`` (A6's Python
  statement of 205, itself checked against Postgres by the migration's probe catalogue), plus
  175's NOT NULLs and CHECKs, on every INSERT and on every post-UPDATE row → an ``APIError``
  with Postgres' SQLSTATE (23502 / 23514) naming the column / the constraint;
* UNIQUE (company_slug, investee_name, kind) and UNIQUE (company_slug, accession) and every
  primary key → 23505 (``supabase_errors.is_unique_violation`` is True on it);
* the foreign keys → 23503, and ``stake_id`` ON DELETE SET NULL (``studio_delete_stake``);
* values as PostgREST returns them: uuids lower-case, dates ``YYYY-MM-DD``, timestamps
  ``…+00:00`` (a value written with ``Z`` comes back with ``+00:00``), JSON round-tripped;
  the request body encoded the way httpx 0.28 encodes it (``allow_nan=False``: NaN and a
  ``date`` object fail CLIENT-side, before any write);
* filters with SQL semantics (NULL never matches ``eq`` / ``neq`` / ``in``; ``is`` for
  null / true / false; typed comparison) and ``ORDER BY`` NULLS LAST / DESC NULLS FIRST;
* the server's ``max-rows`` (``max_rows``, 1,000 like this project): an answer is clamped
  whatever ``.limit()`` asks — the silent truncation ``app/utils/postgrest_paging.py`` is about;
* an UPDATE / DELETE with no filter is refused (Supabase runs pg_safeupdate).

Anything else — an unknown builder method, ``.rpc()``, ``.upsert()``, a filter on a JSON
column, a statement executed ON the event loop — raises :class:`FakeMisuse`, a BaseException
that no ``except Exception`` in the code under test can swallow (a silently ignored filter is
how fakes lie).

FAILURE INJECTION AND RACES
---------------------------
``fail(table, op, when=..., times=..., exc=..., match=...)``:
  ``when="before"`` raises without writing; ``when="after"`` APPLIES the write, then raises —
  a LOST RESPONSE. ``exc`` defaults to :func:`lost_response_error` (Cloudflare's 520, which
  ``supabase_errors`` classifies transient: a READ is retried by the store, a write never).
``on_before(table, op, callback, times=..., match=...)`` runs ``callback(fake)`` right before
  the matching statement executes — e.g. a concurrent Withdraw between a read and an UPDATE.
  Writes the callback makes are logged with ``injected=True``.
``rewrite(table, op, fn, times=..., match=...)`` replaces the next matching answer's ``data``
  with ``fn(data)`` (an answer PostgREST should never give).
``match(statement)`` narrows any of them to one statement (see :class:`Statement`).
Order inside one execute: hooks → ``before`` failure → the statement → ``after`` failure →
rewrite.

Every executed statement is logged in ``calls`` (:class:`FakeCall`); ``writes()`` / ``reads()``
filter it. Test-setup helpers (``admin_insert``, ``admin_update``, ``studio_delete_stake``,
``seed_companies_and_stakes``, ``add_filing``) run the same constraint checks but are not
logged, hooked or failed.
"""

from __future__ import annotations

import asyncio
import copy
import json
import re
import threading
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from postgrest.exceptions import APIError

from app.schemas.trillion_club import STAKE_KINDS, VALUE_BASES
from test_trillion_club_stake_migration import model_violations

BACKEND = Path(__file__).resolve().parents[1]
SEED_JSON = BACKEND / "data" / "trillion_club_seed.json"
UTC = timezone.utc

COMPANIES = "trillion_club_companies"
STAKES = "trillion_club_stakes"
FINDINGS = "trillion_club_stake_findings"
FILINGS = "trillion_club_filings"

#: PostgREST's server-side max-rows on this project (app/utils/postgrest_paging.py).
DEFAULT_MAX_ROWS = 1000


# ── Errors ───────────────────────────────────────────────────────────────────────────────


class FakeMisuse(BaseException):
    """The code under test used something this fake does not model (or must never do, like
    executing on the event loop). A BaseException on purpose: an ``except Exception`` in the
    code under test cannot turn it into a handled database error."""


def api_error(code: Any, message: str, details: Optional[str] = None, hint: Optional[str] = None) -> APIError:
    """What postgrest-py raises for an error answer (a ``str`` code on the PostgREST path)."""
    return APIError({"code": code, "message": message, "details": details, "hint": hint})


def lost_response_error() -> APIError:
    """Cloudflare's 520 as postgrest-py raises it: an INT code, an HTML body — no information
    about whether Postgres committed (``supabase_errors``: transient)."""
    return APIError({"code": 520, "message": "JSON could not be generated",
                     "details": "b'<!DOCTYPE html>… 520: Web server is returning an unknown error …'",
                     "hint": None})


# ── Schema (migrations 175 + 205) ────────────────────────────────────────────────────────

_NOW_DEFAULT = "now()"
_UUID_DEFAULT = "gen_random_uuid()"


@dataclass(frozen=True)
class Column:
    name: str
    type: str                 # text | uuid | bool | float8 | int4 | int8 | date | timestamptz | jsonb | text[]
    not_null: bool = False
    default: Any = None       # a constant, or _NOW_DEFAULT / _UUID_DEFAULT


def _cols(*specs: Tuple[Any, ...]) -> Tuple[Column, ...]:
    return tuple(Column(*s) for s in specs)


SCHEMA: Dict[str, Tuple[Column, ...]] = {
    COMPANIES: _cols(
        ("slug", "text", True), ("display_name", "text", True), ("ciks", "text[]", True, []),
        ("card_kind", "text", True), ("use_13f", "bool", True, False), ("cap_symbol", "text"),
        ("symbol_aliases", "text[]", True, []), ("detail_symbol", "text"), ("logo_symbol", "text"),
        ("home_country", "text", True, "US"), ("cap_source", "text", True), ("manual_cap_usd", "float8"),
        ("manual_cap_as_of", "date"), ("manual_cap_source_url", "text"), ("manual_fx_rate", "float8"),
        ("manual_fx_source", "text"), ("membership_mode", "text", True, "auto"),
        ("is_member", "bool", True, False), ("member_since", "date"), ("last_market_cap", "float8"),
        ("last_cap_date", "date"), ("closes_at_or_above", "int4", True, 0), ("closes_below", "int4", True, 0),
        ("membership_checked_at", "timestamptz"), ("link_whale", "bool", True, False),
        ("published", "bool", True, False), ("reviewed_on", "date"),
        ("created_at", "timestamptz", True, _NOW_DEFAULT), ("updated_at", "timestamptz", True, _NOW_DEFAULT),
    ),
    STAKES: _cols(
        ("id", "uuid", True, _UUID_DEFAULT), ("company_slug", "text", True), ("kind", "text", True),
        ("investee_name", "text", True), ("investee_cusip", "text"), ("investee_us_symbol", "text"),
        ("local_listing", "text"), ("ownership_pct", "float8"), ("ownership_basis", "text"),
        ("disclosed_value_usd", "float8"), ("value_basis", "text"), ("as_of", "date", True),
        ("source_title", "text", True), ("source_url", "text", True),
        ("source_confidence", "text", True, "primary"), ("material", "bool", True, False),
        ("tied_to_deal", "bool", True, False), ("listed_since", "date"), ("background", "text"),
        ("verified_on", "date", True), ("published", "bool", True, False), ("sort_order", "int4", True, 0),
        ("created_at", "timestamptz", True, _NOW_DEFAULT), ("updated_at", "timestamptz", True, _NOW_DEFAULT),
        # 205
        ("origin", "text", True, "seed"), ("owner_kept_at", "timestamptz"), ("withdrawn_at", "timestamptz"),
        ("investee_cik", "text"), ("finding_id", "uuid"),
    ),
    FINDINGS: _cols(
        ("id", "uuid", True, _UUID_DEFAULT), ("company_slug", "text", True), ("source", "text", True),
        ("accession", "text", True), ("filer_cik", "text", True), ("investee_cik", "text"),
        ("form", "text", True), ("filed_on", "date", True), ("document", "text", True),
        ("source_url", "text", True), ("tier", "text", True), ("outcome", "text", True),
        ("reason", "text", True), ("proposed_row", "jsonb"), ("verified_spans", "jsonb", True, []),
        ("fact_sha", "text"), ("stake_id", "uuid"), ("status", "text", True, "new"),
        ("telegram_message_id", "int8"), ("notified_at", "timestamptz"), ("published_at", "timestamptz"),
        ("owner_decision", "text"),
        ("decided_at", "timestamptz"), ("attempts", "int4", True, 0), ("last_error", "text"),
        ("created_at", "timestamptz", True, _NOW_DEFAULT), ("updated_at", "timestamptz", True, _NOW_DEFAULT),
    ),
    FILINGS: _cols(
        ("cik", "text", True), ("period", "text", True), ("period_end", "date", True), ("filed_on", "date"),
        ("amended_on", "date"), ("accessions", "text[]", True, []), ("total_value", "float8"),
        ("position_count", "int4", True, 0), ("holdings", "jsonb", True, []), ("changes", "jsonb", True, {}),
        ("excluded_rows", "int4", True, 0), ("unresolved", "jsonb", True, {}), ("raw_hash", "text", True),
        ("build_status", "text", True), ("source", "text", True, "fmp"),
        ("built_at", "timestamptz", True, _NOW_DEFAULT),
    ),
}
PRIMARY_KEYS: Dict[str, Tuple[str, ...]] = {
    COMPANIES: ("slug",), STAKES: ("id",), FINDINGS: ("id",), FILINGS: ("cik", "period"),
}
#: (constraint, columns) — the primary key first, then the UNIQUE constraints.
UNIQUES: Dict[str, Tuple[Tuple[str, Tuple[str, ...]], ...]] = {
    COMPANIES: ((f"{COMPANIES}_pkey", ("slug",)),),
    STAKES: ((f"{STAKES}_pkey", ("id",)),
             ("trillion_club_stake_natural_key", ("company_slug", "investee_name", "kind"))),
    FINDINGS: ((f"{FINDINGS}_pkey", ("id",)),
               (f"{FINDINGS}_company_slug_accession_key", ("company_slug", "accession"))),
    FILINGS: ((f"{FILINGS}_pkey", ("cik", "period")),),
}
#: (constraint, column, referenced table, referenced column, ON DELETE)
FOREIGN_KEYS: Dict[str, Tuple[Tuple[str, str, str, str, str], ...]] = {
    COMPANIES: (),
    STAKES: ((f"{STAKES}_company_slug_fkey", "company_slug", COMPANIES, "slug", "cascade"),),
    FINDINGS: ((f"{FINDINGS}_company_slug_fkey", "company_slug", COMPANIES, "slug", "cascade"),
               (f"{FINDINGS}_stake_id_fkey", "stake_id", STAKES, "id", "set null")),
    FILINGS: (),
}
_COLUMN = {t: {c.name: c for c in cols} for t, cols in SCHEMA.items()}


# ── 175's CHECKs (205's come from model_violations) ──────────────────────────────────────


def _len_between(v: Any, lo: int, hi: int) -> bool:
    return v is None or lo <= len(v) <= hi


def _rx(v: Any, pattern: str) -> bool:
    """Postgres `~ '^…$'`: anchored at both ends, no end-of-line exception (fullmatch)."""
    return v is None or re.fullmatch(pattern, v) is not None


def _positive(v: Any) -> bool:
    return v is None or v > 0


def _company_175(r: Mapping[str, Any]) -> List[str]:
    t, out = COMPANIES, []
    checks = (
        (f"{t}_slug_check", _rx(r["slug"], r"[a-z0-9-]{1,40}")),
        (f"{t}_display_name_check", _len_between(r["display_name"], 1, 60)),
        (f"{t}_ciks_check", r["ciks"] is None or all(isinstance(x, str) and re.fullmatch(r"[0-9]{10}", x)
                                                     for x in r["ciks"])),
        (f"{t}_card_kind_check", r["card_kind"] in ("thirteen_f", "no_thirteen_f", "non_us", "whale_link")),
        (f"{t}_home_country_check", _rx(r["home_country"], r"[A-Z]{2}")),
        (f"{t}_cap_source_check", r["cap_source"] in ("fmp_us", "fmp_adr", "manual")),
        (f"{t}_manual_cap_usd_check", _positive(r["manual_cap_usd"])),
        (f"{t}_manual_cap_source_url_check", r["manual_cap_source_url"] is None
         or r["manual_cap_source_url"].startswith("https://")),
        (f"{t}_manual_fx_rate_check", _positive(r["manual_fx_rate"])),
        (f"{t}_membership_mode_check", r["membership_mode"] in ("auto", "force_in", "force_out")),
        (f"{t}_last_market_cap_check", _positive(r["last_market_cap"])),
        (f"{t}_closes_at_or_above_check", r["closes_at_or_above"] >= 0),
        (f"{t}_closes_below_check", r["closes_below"] >= 0),
        ("trillion_club_manual_cap_is_explicit", r["cap_source"] != "manual" or (
            r["manual_cap_usd"] is not None and r["manual_cap_as_of"] is not None
            and r["manual_cap_source_url"] is not None and r["membership_mode"] in ("force_in", "force_out"))),
        ("trillion_club_fmp_cap_has_symbol", r["cap_source"] == "manual" or r["cap_symbol"] is not None),
        ("trillion_club_13f_needs_cik", not r["use_13f"] or (r["card_kind"] == "thirteen_f" and len(r["ciks"]) >= 1)),
        ("trillion_club_whale_link_kind", r["link_whale"] == (r["card_kind"] == "whale_link")),
    )
    out.extend(name for name, ok in checks if not ok)
    return out


def _stake_175(r: Mapping[str, Any]) -> List[str]:
    t = STAKES
    checks = (
        (f"{t}_kind_check", r["kind"] in STAKE_KINDS),
        (f"{t}_investee_name_check", _len_between(r["investee_name"], 1, 60)),
        (f"{t}_investee_cusip_check", _rx(r["investee_cusip"], r"[0-9A-Z]{9}")),
        (f"{t}_ownership_pct_check", r["ownership_pct"] is None or 0 < r["ownership_pct"] <= 100),
        (f"{t}_disclosed_value_usd_check", _positive(r["disclosed_value_usd"])),
        (f"{t}_value_basis_check", r["value_basis"] is None or r["value_basis"] in VALUE_BASES),
        (f"{t}_source_title_check", _len_between(r["source_title"], 1, 120)),
        (f"{t}_source_url_check", r["source_url"].startswith("https://")),
        (f"{t}_source_confidence_check", r["source_confidence"] in ("primary", "secondary")),
        (f"{t}_background_check", r["background"] is None or len(r["background"]) <= 90),
        ("trillion_club_stake_value_has_basis", r["disclosed_value_usd"] is None or r["value_basis"] is not None),
        ("trillion_club_commitment_basis", r["kind"] != "commitment" or r["value_basis"] in (None, "committed_up_to")),
        ("trillion_club_secondary_never_published", not (r["published"] and r["source_confidence"] == "secondary")),
    )
    return [name for name, ok in checks if not ok]


def _filing_175(r: Mapping[str, Any]) -> List[str]:
    t = FILINGS
    checks = (
        (f"{t}_cik_check", _rx(r["cik"], r"[0-9]{10}")),
        (f"{t}_period_check", _rx(r["period"], r"[0-9]{4}-Q[1-4]")),
        (f"{t}_total_value_check", r["total_value"] is None or r["total_value"] >= 0),
        (f"{t}_position_count_check", r["position_count"] >= 0),
        (f"{t}_holdings_check", isinstance(r["holdings"], list)),
        (f"{t}_changes_check", isinstance(r["changes"], dict)),
        (f"{t}_excluded_rows_check", r["excluded_rows"] >= 0),
        (f"{t}_unresolved_check", isinstance(r["unresolved"], dict)),
        (f"{t}_build_status_check", r["build_status"] in ("complete", "degraded")),
        (f"{t}_source_check", r["source"] in ("fmp", "edgar")),
    )
    return [name for name, ok in checks if not ok]


def check_violations(table: str, row: Mapping[str, Any]) -> List[str]:
    """Every CHECK constraint ``row`` (a complete wire row) breaks — 175's and 205's, sorted
    (Postgres reports the alphabetically first)."""
    if table == STAKES:
        found = set(_stake_175(row)) | {v for v in model_violations(STAKES, row) if not v.startswith("NOTNULL:")}
    elif table == FINDINGS:
        found = {v for v in model_violations(FINDINGS, row) if not v.startswith("NOTNULL:")}
    elif table == COMPANIES:
        found = set(_company_175(row))
    else:
        found = set(_filing_175(row))
    return sorted(found)


# ── Wire values ──────────────────────────────────────────────────────────────────────────

_BOOL_TRUE = frozenset({"t", "true", "y", "yes", "on", "1"})
_BOOL_FALSE = frozenset({"f", "false", "n", "no", "off", "0"})
_INT_RANGE = {"int4": (-(2 ** 31), 2 ** 31 - 1), "int8": (-(2 ** 63), 2 ** 63 - 1)}


def _invalid(type_name: str, text: Any, code: str = "22P02") -> APIError:
    return api_error(code, f'invalid input syntax for type {type_name}: "{text}"')


def _parse_ts_text(text: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _no_nul(text: str) -> str:
    if "\x00" in text:
        raise api_error("22P05", "unsupported Unicode escape sequence",
                        details="\\u0000 cannot be converted to text.")
    return text


def _jsonb(value: Any) -> Any:
    if isinstance(value, str):
        return _no_nul(value)
    if isinstance(value, list):
        return [_jsonb(v) for v in value]
    if isinstance(value, dict):
        return {_no_nul(k): _jsonb(v) for k, v in value.items()}
    return value


def _store_value(col: Column, value: Any) -> Any:
    """A JSON-decoded body value cast into ``col`` (json_populate_record semantics), in the
    form PostgREST will answer with."""
    if value is None:
        return None
    t = col.type
    if t == "text":
        if isinstance(value, str):
            return _no_nul(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return _no_nul(json.dumps(value, ensure_ascii=False, separators=(", ", ": ")))
    if t == "uuid":
        try:
            return str(uuid.UUID(value)) if isinstance(value, str) else str(uuid.UUID(str(value)))
        except ValueError:
            raise _invalid("uuid", value) from None
    if t == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in _BOOL_TRUE or text in _BOOL_FALSE:
            return text in _BOOL_TRUE
        raise _invalid("boolean", value)
    if t in _INT_RANGE:
        if isinstance(value, bool) or isinstance(value, float):
            raise _invalid("integer" if t == "int4" else "bigint", json.dumps(value))
        if isinstance(value, str):
            if not re.fullmatch(r"\s*[+-]?[0-9]+\s*", value):
                raise _invalid("integer" if t == "int4" else "bigint", value)
            value = int(value)
        lo, hi = _INT_RANGE[t]
        if not isinstance(value, int) or not lo <= value <= hi:
            raise api_error("22003", f"value \"{value}\" is out of range for type "
                                     f"{'integer' if t == 'int4' else 'bigint'}")
        return value
    if t == "float8":
        if isinstance(value, bool):
            raise _invalid("double precision", json.dumps(value))
        try:
            return float(value)
        except (TypeError, ValueError):
            raise _invalid("double precision", value) from None
    if t == "date":
        if isinstance(value, str) and re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value.strip()):
            try:
                return date.fromisoformat(value.strip()).isoformat()
            except ValueError:
                pass
        raise api_error("22007", f'invalid input syntax for type date: "{value}"')
    if t == "timestamptz":
        dt = _parse_ts_text(value) if isinstance(value, str) else None
        if dt is None:
            raise api_error("22007", f'invalid input syntax for type timestamp with time zone: "{value}"')
        return dt.astimezone(UTC).isoformat()
    if t == "jsonb":
        return _jsonb(copy.deepcopy(value))
    if t == "text[]":
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise api_error("22P02", f'malformed array literal: "{value}"')
        return [_no_nul(x) for x in value]
    raise FakeMisuse(f"column type {t} is not modelled")


def _filter_value(col: Column, raw: Any) -> Any:
    """A filter's value as PostgREST receives it — ``str(value)``, exactly what postgrest-py
    puts in the query string — cast to the column type (a bad cast is Postgres' error)."""
    text = str(raw)   # `.eq(col, None)` really sends "eq.None" — Postgres then refuses or mis-matches it
    t = col.type
    if t == "text":
        return text
    if t == "uuid":
        try:
            return str(uuid.UUID(text))
        except ValueError:
            raise _invalid("uuid", text) from None
    if t == "bool":
        low = text.strip().lower()
        if low in _BOOL_TRUE or low in _BOOL_FALSE:
            return low in _BOOL_TRUE
        raise _invalid("boolean", text)
    if t in _INT_RANGE:
        if not re.fullmatch(r"\s*[+-]?[0-9]+\s*", text):
            raise _invalid("integer" if t == "int4" else "bigint", text)
        return int(text)
    if t == "float8":
        try:
            return float(text)
        except ValueError:
            raise _invalid("double precision", text) from None
    if t == "date":
        if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", text.strip()):
            try:
                return date.fromisoformat(text.strip())
            except ValueError:
                pass
        raise api_error("22007", f'invalid input syntax for type date: "{text}"')
    if t == "timestamptz":
        dt = _parse_ts_text(text)
        if dt is None:
            raise api_error("22007", f'invalid input syntax for type timestamp with time zone: "{text}"')
        return dt
    raise FakeMisuse(f"a filter on the {t} column {col.name} is not modelled")


def _typed(col: Column, value: Any) -> Any:
    """A stored wire value as the comparable Python value Postgres compares."""
    if value is None:
        return None
    if col.type == "date":
        return date.fromisoformat(value)
    if col.type == "timestamptz":
        return _parse_ts_text(value)
    return value


def _encode_body(payload: Any) -> Any:
    """httpx 0.28's request body: compact JSON with ``allow_nan=False`` — NaN raises ValueError,
    a ``date`` / ``set`` / ``Decimal`` raises TypeError, a lone surrogate raises
    UnicodeEncodeError, all CLIENT-side before anything is sent. Returns the body as the server
    decodes it (int keys become strings, tuples lists)."""
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return json.loads(body.decode("utf-8"))


# ── Statements, calls and the builder ────────────────────────────────────────────────────


@dataclass
class Statement:
    """One statement as the code under test built it (what ``match`` predicates receive)."""

    table: str
    op: str                                   # select | insert | update | delete
    payload: Any = None                       # the body (insert / update)
    columns: Tuple[str, ...] = ()
    filters: List[Tuple[str, str, Any]] = field(default_factory=list)   # (eq|neq|in|is|gt|…, column, raw value)
    order: List[Tuple[str, bool]] = field(default_factory=list)
    limit: Optional[int] = None
    returning: str = "representation"

    def filter_value(self, op: str, column: str) -> Any:
        """The raw value of the first ``op`` filter on ``column`` (KeyError when there is none)."""
        for f_op, f_col, f_val in self.filters:
            if f_op == op and f_col == column:
                return f_val
        raise KeyError((op, column))


@dataclass(frozen=True)
class FakeCall:
    seq: int
    table: str
    op: str
    payload: Any
    filters: Tuple[Tuple[str, str, Any], ...]
    outcome: str        # ok | failed_before | failed_after | error:<sqlstate> | client_error
    rows: int           # rows answered (select) or affected (writes)
    injected: bool      # made from inside an on_before callback
    thread: str


class _Result:
    def __init__(self, data: Any):
        self.data = data
        self.count = None


def _returning(value: Any) -> str:
    text = getattr(value, "value", value)
    if text not in ("representation", "minimal"):
        raise FakeMisuse(f"returning={value!r} is not modelled")
    return text


class _Query:
    """A built, not yet executed statement. Only the methods postgrest-py offers for this kind
    of statement exist; anything else raises FakeMisuse."""

    _FILTERS = ("eq", "neq", "gt", "gte", "lt", "lte", "in_", "is_")

    def __init__(self, fake: "FakeSupabase", stmt: Statement):
        self._fake = fake
        self._stmt = stmt

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        self._fake._misuse(f"{self._stmt.table}: .{name}() on a {self._stmt.op} is not modelled")

    def _add(self, op: str, column: str, value: Any) -> "_Query":
        if self._stmt.op == "insert":
            self._fake._misuse(f"{self._stmt.table}: a filter after insert() does not exist in postgrest-py")
        if not isinstance(column, str):
            self._fake._misuse(f"{self._stmt.table}: filter column must be a str, got {column!r}")
        self._stmt.filters.append((op, column, value))
        return self

    def eq(self, column: str, value: Any) -> "_Query":
        return self._add("eq", column, value)

    def neq(self, column: str, value: Any) -> "_Query":
        return self._add("neq", column, value)

    def gt(self, column: str, value: Any) -> "_Query":
        return self._add("gt", column, value)

    def gte(self, column: str, value: Any) -> "_Query":
        return self._add("gte", column, value)

    def lt(self, column: str, value: Any) -> "_Query":
        return self._add("lt", column, value)

    def lte(self, column: str, value: Any) -> "_Query":
        return self._add("lte", column, value)

    def in_(self, column: str, values: Iterable[Any]) -> "_Query":
        return self._add("in", column, list(values))

    def is_(self, column: str, value: Any) -> "_Query":
        text = "null" if value is None else value
        if text not in ("null", "true", "false"):
            self._fake._misuse(f"{self._stmt.table}: is_({column!r}, {value!r}) is not modelled")
        return self._add("is", column, text)

    def order(self, column: str, *, desc: bool = False, nullsfirst: Optional[bool] = None,
              foreign_table: Optional[str] = None) -> "_Query":
        if self._stmt.op != "select" or nullsfirst is not None or foreign_table is not None:
            self._fake._misuse(f"{self._stmt.table}: order() is modelled on a plain select only")
        self._stmt.order.append((column, bool(desc)))
        return self

    def limit(self, size: int, *, foreign_table: Optional[str] = None) -> "_Query":
        if self._stmt.op != "select" or foreign_table is not None:
            self._fake._misuse(f"{self._stmt.table}: limit() is modelled on a plain select only")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            self._fake._misuse(f"{self._stmt.table}: limit({size!r})")
        self._stmt.limit = size
        return self

    def execute(self) -> _Result:
        return self._fake._execute(self._stmt)


class _TableRef:
    def __init__(self, fake: "FakeSupabase", name: str):
        self._fake = fake
        self._name = name

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        self._fake._misuse(f"{self._name}: .{name}() is not modelled (only select/insert/update/delete)")

    def select(self, *columns: str, count: Any = None, head: Any = None) -> _Query:
        if count is not None or head is not None:
            self._fake._misuse(f"{self._name}: select(count=/head=) is not modelled")
        names: List[str] = []
        for spec in columns or ("*",):
            if not isinstance(spec, str):
                self._fake._misuse(f"{self._name}: select({spec!r})")
            names.extend(p.strip() for p in spec.split(",") if p.strip())
        return _Query(self._fake, Statement(self._name, "select", columns=tuple(names)))

    def insert(self, json: Any, *, count: Any = None, returning: Any = "representation",
               upsert: bool = False, default_to_null: bool = True) -> _Query:
        if count is not None or upsert or not default_to_null:
            self._fake._misuse(f"{self._name}: insert(count=/upsert=/default_to_null=False) is not modelled")
        return _Query(self._fake, Statement(self._name, "insert", payload=json, returning=_returning(returning)))

    def update(self, json: Any, *, count: Any = None, returning: Any = "representation") -> _Query:
        if count is not None:
            self._fake._misuse(f"{self._name}: update(count=) is not modelled")
        return _Query(self._fake, Statement(self._name, "update", payload=json, returning=_returning(returning)))

    def delete(self, *, count: Any = None, returning: Any = "representation") -> _Query:
        if count is not None:
            self._fake._misuse(f"{self._name}: delete(count=) is not modelled")
        return _Query(self._fake, Statement(self._name, "delete", returning=_returning(returning)))


@dataclass
class _Injection:
    table: str
    op: str
    times: int
    match: Optional[Callable[[Statement], bool]]
    when: str = ""
    exc: Any = None
    callback: Optional[Callable[["FakeSupabase"], Any]] = None
    fn: Optional[Callable[[Any], Any]] = None

    def matches(self, stmt: Statement) -> bool:
        return (self.times > 0 and self.table in (stmt.table, "*") and self.op in (stmt.op, "*")
                and (self.match is None or bool(self.match(stmt))))


_OPS = ("select", "insert", "update", "delete", "*")


# ── The fake ─────────────────────────────────────────────────────────────────────────────


class FakeSupabase:
    """The four ``trillion_club_*`` tables the finder reads and writes, enforced as migrations
    175 + 205 define them. Pass it as the client: ``StakeStore(fake)``,
    ``TrillionClubStore(fake)``."""

    def __init__(self, *, max_rows: int = DEFAULT_MAX_ROWS, forbid_event_loop: bool = True,
                 clock_start: datetime = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)):
        self.max_rows = max_rows
        self.forbid_event_loop = forbid_event_loop
        self.tables: Dict[str, List[Dict[str, Any]]] = {t: [] for t in SCHEMA}
        self.calls: List[FakeCall] = []
        self.misuse: List[str] = []
        self._failures: List[_Injection] = []
        self._hooks: List[_Injection] = []
        self._rewrites: List[_Injection] = []
        self._lock = threading.RLock()
        self._local = threading.local()
        self._clock = clock_start
        self._seq = 0

    # ── client surface ───────────────────────────────────────────────────────────────

    def table(self, name: str) -> _TableRef:
        if name not in SCHEMA:
            self._misuse(f"table {name!r} is not modelled")
        return _TableRef(self, name)

    #: The rest of a supabase-py Client: used, they are FakeMisuse; any other name is a plain
    #: AttributeError, so ``hasattr(client, "read_companies")`` (jobs._store_for) stays False.
    _CLIENT_SURFACE = frozenset({"rpc", "from_", "schema", "auth", "storage", "functions", "postgrest",
                                 "realtime", "channel", "channels", "remove_channel", "options"})

    def __getattr__(self, name: str) -> Any:
        if name in FakeSupabase._CLIENT_SURFACE:
            self._misuse(f"client.{name} is not modelled (only .table() is)")
        raise AttributeError(name)

    def _misuse(self, message: str) -> None:
        self.misuse.append(message)
        raise FakeMisuse(message)

    # ── injection ────────────────────────────────────────────────────────────────────

    def fail(self, table: str, op: str, *, when: str = "before", times: int = 1, exc: Any = None,
             match: Optional[Callable[[Statement], bool]] = None) -> None:
        """Make the next ``times`` matching statements raise. ``when="before"``: nothing is
        written; ``when="after"``: the write is APPLIED, then the error (a lost response).
        ``exc``: an exception, or a zero-argument factory (default: a fresh 520 each time)."""
        if when not in ("before", "after") or op not in _OPS or times < 1:
            raise ValueError(f"fail({table!r}, {op!r}, when={when!r}, times={times!r})")
        self._failures.append(_Injection(table, op, times, match, when=when, exc=exc))

    def on_before(self, table: str, op: str, callback: Callable[["FakeSupabase"], Any], *, times: int = 1,
                  match: Optional[Callable[[Statement], bool]] = None) -> None:
        """Run ``callback(fake)`` right before the next ``times`` matching statements execute."""
        if op not in _OPS or times < 1:
            raise ValueError(f"on_before({table!r}, {op!r}, times={times!r})")
        self._hooks.append(_Injection(table, op, times, match, callback=callback))

    def rewrite(self, table: str, op: str, fn: Callable[[Any], Any], *, times: int = 1,
                match: Optional[Callable[[Statement], bool]] = None) -> None:
        """Replace the next ``times`` matching answers' ``data`` with ``fn(data)``."""
        if op not in _OPS or times < 1:
            raise ValueError(f"rewrite({table!r}, {op!r}, times={times!r})")
        self._rewrites.append(_Injection(table, op, times, match, fn=fn))

    def pending_injections(self) -> List[str]:
        """Injections that never fired — a test that armed one and saw nothing is vacuous."""
        out = []
        for kind, items in (("fail", self._failures), ("on_before", self._hooks), ("rewrite", self._rewrites)):
            out.extend(f"{kind} {i.table}.{i.op} x{i.times}" for i in items if i.times > 0)
        return out

    @staticmethod
    def _take(items: List[_Injection], stmt: Statement, when: str = "") -> Optional[_Injection]:
        for item in items:
            if (not when or item.when == when) and item.matches(stmt):
                item.times -= 1
                return item
        return None

    # ── the call log ─────────────────────────────────────────────────────────────────

    def writes(self, table: Optional[str] = None, op: Optional[str] = None, *,
               injected: Optional[bool] = False) -> List[FakeCall]:
        """Logged INSERT / UPDATE / DELETE statements (by default the code under test's own,
        not those an ``on_before`` callback made)."""
        return [c for c in self.calls if c.op != "select" and (table is None or c.table == table)
                and (op is None or c.op == op) and (injected is None or c.injected == injected)]

    def reads(self, table: Optional[str] = None) -> List[FakeCall]:
        return [c for c in self.calls if c.op == "select" and (table is None or c.table == table)]

    def clear_calls(self) -> None:
        self.calls.clear()

    # ── execution ────────────────────────────────────────────────────────────────────

    def now_text(self) -> str:
        """The next DEFAULT now() value (a deterministic clock, 1 ms per call)."""
        with self._lock:
            self._clock += timedelta(milliseconds=1)
            return self._clock.isoformat()

    def _execute(self, stmt: Statement) -> _Result:
        if self.forbid_event_loop:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                self._misuse(f"{stmt.table}.{stmt.op}.execute() ran ON the event loop — the Supabase SDK is "
                             "sync: run it through asyncio.to_thread / sb_exec")
        injected = getattr(self._local, "in_hook", False)
        thread = threading.current_thread().name
        body = None
        if stmt.op in ("insert", "update"):
            try:
                body = _encode_body(stmt.payload)
            except (TypeError, ValueError, UnicodeEncodeError):
                with self._lock:
                    self._log(stmt, "client_error", 0, injected, thread)
                raise
        with self._lock:
            seq_outcome = "ok"
            hook = self._take(self._hooks, stmt)
            if hook is not None and hook.callback is not None:
                self._local.in_hook = True
                try:
                    hook.callback(self)
                finally:
                    self._local.in_hook = injected
            before = self._take(self._failures, stmt, "before")
            if before is not None:
                self._log(stmt, "failed_before", 0, injected, thread)
                raise self._exception(before)
            try:
                data = self._apply(stmt, body)
            except APIError as e:
                self._log(stmt, f"error:{e.code}", 0, injected, thread)
                raise
            after = self._take(self._failures, stmt, "after")
            if after is not None:
                self._log(stmt, "failed_after", len(data), injected, thread)
                raise self._exception(after)
            self._log(stmt, seq_outcome, len(data), injected, thread)
            answer: Any = data if stmt.returning == "representation" or stmt.op == "select" else []
            rewrite = self._take(self._rewrites, stmt)
            if rewrite is not None and rewrite.fn is not None:
                answer = rewrite.fn(answer)
            return _Result(answer)

    @staticmethod
    def _exception(item: _Injection) -> BaseException:
        exc = item.exc
        if exc is None:
            return lost_response_error()
        if isinstance(exc, BaseException):
            return exc
        return exc()

    def _log(self, stmt: Statement, outcome: str, rows: int, injected: bool, thread: str) -> None:
        self._seq += 1
        self.calls.append(FakeCall(
            seq=self._seq, table=stmt.table, op=stmt.op, payload=copy.deepcopy(stmt.payload),
            filters=tuple((o, c, copy.deepcopy(v)) for o, c, v in stmt.filters), outcome=outcome,
            rows=rows, injected=injected, thread=thread))

    def _column(self, table: str, name: str, *, write: bool = False) -> Column:
        col = _COLUMN[table].get(name)
        if col is None:
            if write:
                raise api_error("PGRST204", f"Could not find the '{name}' column of '{table}' in the schema cache")
            raise api_error("42703", f"column {table}.{name} does not exist")
        return col

    def _apply(self, stmt: Statement, body: Any) -> List[Dict[str, Any]]:
        if stmt.op == "select":
            return self._select(stmt)
        if stmt.op == "insert":
            return self._insert(stmt.table, body)
        if stmt.op == "update":
            return self._update(stmt, body)
        return self._delete(stmt)

    # ── filters ──────────────────────────────────────────────────────────────────────

    def _matcher(self, stmt: Statement) -> Callable[[Dict[str, Any]], bool]:
        tests: List[Callable[[Dict[str, Any]], bool]] = []
        for op, name, raw in stmt.filters:
            col = self._column(stmt.table, name)
            if col.type in ("jsonb", "text[]") and not (op == "is" and raw == "null"):
                self._misuse(f"{stmt.table}: a {op} filter on the {col.type} column {name} is not modelled")
            if op == "is":
                if raw == "null":
                    tests.append(lambda r, n=name: r.get(n) is None)
                else:
                    if col.type != "bool":
                        raise api_error("22P02", f'invalid input syntax for type {col.type}: "{raw}"')
                    want = raw == "true"
                    tests.append(lambda r, n=name, w=want: r.get(n) is w)
                continue
            if op == "in":
                wanted = [_filter_value(col, v) for v in raw]
                tests.append(lambda r, c=col, w=wanted: r.get(c.name) is not None and _typed(c, r[c.name]) in w)
                continue
            value = _filter_value(col, raw)
            compare = {"eq": lambda a, b: a == b, "neq": lambda a, b: a != b, "gt": lambda a, b: a > b,
                       "gte": lambda a, b: a >= b, "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b}[op]
            tests.append(lambda r, c=col, v=value, f=compare: r.get(c.name) is not None
                         and f(_typed(c, r[c.name]), v))
        return lambda r: all(t(r) for t in tests)

    def _select(self, stmt: Statement) -> List[Dict[str, Any]]:
        columns = [c.name for c in SCHEMA[stmt.table]] if stmt.columns in ((), ("*",)) else list(stmt.columns)
        for name in columns:
            self._column(stmt.table, name)
        keep = self._matcher(stmt)
        rows = [r for r in self.tables[stmt.table] if keep(r)]
        for name, desc in reversed(stmt.order):
            col = self._column(stmt.table, name)
            if col.type in ("jsonb", "text[]"):
                self._misuse(f"{stmt.table}: ORDER BY the {col.type} column {name} is not modelled")
            present = sorted((r for r in rows if r.get(name) is not None), key=lambda r: _typed(col, r[name]),
                             reverse=desc)
            absent = [r for r in rows if r.get(name) is None]
            rows = absent + present if desc else present + absent   # ASC NULLS LAST, DESC NULLS FIRST
        if stmt.limit is not None:
            rows = rows[: stmt.limit]
        rows = rows[: self.max_rows]          # the server's max-rows clamp, whatever was asked
        return [{name: copy.deepcopy(r.get(name)) for name in columns} for r in rows]

    # ── constraint checks ────────────────────────────────────────────────────────────

    def _check_row(self, table: str, row: Dict[str, Any], others: Sequence[Dict[str, Any]], *,
                   changed: Optional[Iterable[str]] = None) -> None:
        for col in SCHEMA[table]:
            if col.not_null and row.get(col.name) is None:
                raise api_error("23502", f'null value in column "{col.name}" of relation "{table}" violates '
                                         "not-null constraint", details=self._failing_row(table, row))
        problems = check_violations(table, row)
        if problems:
            raise api_error("23514", f'new row for relation "{table}" violates check constraint "{problems[0]}"',
                            details=self._failing_row(table, row) + f" (all broken: {problems})")
        for name, cols in UNIQUES[table]:
            key = tuple(row.get(c) for c in cols)
            if any(v is None for v in key):
                continue
            if any(tuple(o.get(c) for c in cols) == key for o in others):
                raise api_error("23505", f'duplicate key value violates unique constraint "{name}"',
                                details=f"Key ({', '.join(cols)})=({', '.join(map(str, key))}) already exists.")
        changed_set = None if changed is None else set(changed)
        for name, col, ref_table, ref_col, _on_delete in FOREIGN_KEYS[table]:
            value = row.get(col)
            if value is None or (changed_set is not None and col not in changed_set):
                continue
            if not any(r.get(ref_col) == value for r in self.tables[ref_table]):
                raise api_error("23503", f'insert or update on table "{table}" violates foreign key constraint '
                                         f'"{name}"', details=f'Key ({col})=({value}) is not present in table '
                                                              f'"{ref_table}".')

    @staticmethod
    def _failing_row(table: str, row: Mapping[str, Any]) -> str:
        values = ", ".join("null" if row.get(c.name) is None else str(row.get(c.name))[:60] for c in SCHEMA[table])
        return f"Failing row contains ({values})."

    def _new_row(self, table: str, given: Mapping[str, Any]) -> Dict[str, Any]:
        if not isinstance(given, Mapping):
            raise api_error("PGRST102", "All object keys must match / the body must hold objects")
        for name in given:
            self._column(table, name, write=True)
        row: Dict[str, Any] = {}
        for col in SCHEMA[table]:
            if col.name in given:
                row[col.name] = _store_value(col, given[col.name])
            elif col.default == _NOW_DEFAULT:
                row[col.name] = self.now_text()
            elif col.default == _UUID_DEFAULT:
                row[col.name] = str(uuid.uuid4())
            else:
                row[col.name] = copy.deepcopy(col.default)
        return row

    # ── writes ───────────────────────────────────────────────────────────────────────

    def _insert(self, table: str, body: Any) -> List[Dict[str, Any]]:
        batch = body if isinstance(body, list) else [body]
        if isinstance(body, list) and len({frozenset(r) for r in body if isinstance(r, Mapping)}) > 1:
            raise api_error("PGRST102", "All object keys must match")
        staged: List[Dict[str, Any]] = []
        for given in batch:
            row = self._new_row(table, given)
            self._check_row(table, row, [*self.tables[table], *staged])
            staged.append(row)
        self.tables[table].extend(staged)
        return [copy.deepcopy(r) for r in staged]

    def _update(self, stmt: Statement, body: Any, *, admin: bool = False) -> List[Dict[str, Any]]:
        table = stmt.table
        if not admin and table in (COMPANIES, FILINGS):
            self._misuse(f"an UPDATE of {table} is not modelled (the finder never writes it)")
        if not stmt.filters:
            raise api_error("21000", "UPDATE requires a WHERE clause")
        if not isinstance(body, Mapping) or not body:
            self._misuse(f"{table}: update() needs a non-empty object")
        changes = {}
        for name, value in body.items():
            col = self._column(table, name, write=True)
            if name in PRIMARY_KEYS[table]:
                self._misuse(f"{table}: changing the primary key {name} is not modelled")
            changes[name] = _store_value(col, value)
        keep = self._matcher(stmt)
        matched = [r for r in self.tables[table] if keep(r)]
        matched_ids = {id(r) for r in matched}
        untouched = [r for r in self.tables[table] if id(r) not in matched_ids]
        staged: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for r in matched:
            new = {**r, **copy.deepcopy(changes)}
            self._check_row(table, new, [*untouched, *(n for _, n in staged)], changed=changes)
            staged.append((r, new))
        for r, new in staged:      # all or nothing, like one UPDATE statement
            r.clear()
            r.update(new)
        return [copy.deepcopy(r) for r, _ in staged]

    def _delete(self, stmt: Statement) -> List[Dict[str, Any]]:
        table = stmt.table
        if table not in (STAKES, FINDINGS):
            self._misuse(f"a DELETE of {table} is not modelled")
        if not stmt.filters:
            raise api_error("21000", "DELETE requires a WHERE clause")
        keep = self._matcher(stmt)
        gone = [r for r in self.tables[table] if keep(r)]
        return self._remove(table, gone)

    def _remove(self, table: str, gone: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ids = {id(r) for r in gone}
        self.tables[table] = [r for r in self.tables[table] if id(r) not in ids]
        if table == STAKES:                   # trillion_club_stake_findings.stake_id ON DELETE SET NULL
            dead = {r["id"] for r in gone}
            for f in self.tables[FINDINGS]:
                if f.get("stake_id") in dead:
                    new = {**f, "stake_id": None}
                    self._check_row(FINDINGS, new, [o for o in self.tables[FINDINGS] if o is not f], changed=())
                    f["stake_id"] = None
        return [copy.deepcopy(r) for r in gone]

    # ── test-setup helpers (checked, never logged / hooked / failed) ─────────────────

    def admin_insert(self, table: str, row: Mapping[str, Any]) -> Dict[str, Any]:
        """Insert ``row`` the way a migration or Studio would — every constraint applies."""
        with self._lock:
            return self._insert(table, _encode_body(dict(row)))[0]

    def admin_update(self, table: str, where: Mapping[str, Any], changes: Mapping[str, Any]) -> List[Dict[str, Any]]:
        """``UPDATE table SET changes WHERE col = value AND …`` (Studio, the daily job's
        membership columns, or a concurrent writer inside an ``on_before`` callback) — every
        constraint applies. Any of the four tables; a primary-key change is not modelled."""
        if not where:
            raise ValueError("admin_update needs a WHERE")
        stmt = Statement(table, "update", payload=dict(changes),
                         filters=[("eq", c, v) for c, v in where.items()])
        with self._lock:
            return self._update(stmt, _encode_body(dict(changes)), admin=True)

    def studio_delete_stake(self, stake_id: str) -> bool:
        """The owner's Studio DELETE of one stake row: the findings that pointed at it keep
        their status with ``stake_id`` NULL (ON DELETE SET NULL). True when a row went."""
        with self._lock:
            gone = [r for r in self.tables[STAKES] if r["id"] == str(uuid.UUID(stake_id))]
            self._remove(STAKES, gone)
            return bool(gone)

    def rows(self, table: str) -> List[Dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self.tables[table])

    def get(self, table: str, **key: Any) -> Optional[Dict[str, Any]]:
        """The one row whose columns equal ``key`` (e.g. ``get(STAKES, id=…)``), or None."""
        with self._lock:
            hits = [r for r in self.tables[table] if all(r.get(k) == v for k, v in key.items())]
            if len(hits) > 1:
                raise AssertionError(f"{len(hits)} rows of {table} match {key}")
            return copy.deepcopy(hits[0]) if hits else None

    def stake(self, stake_id: str) -> Optional[Dict[str, Any]]:
        return self.get(STAKES, id=stake_id)

    def finding(self, finding_id: str) -> Optional[Dict[str, Any]]:
        return self.get(FINDINGS, id=finding_id)

    def snapshot(self) -> Dict[str, List[Dict[str, Any]]]:
        with self._lock:
            return copy.deepcopy(self.tables)

    def restore(self, snapshot: Mapping[str, List[Dict[str, Any]]]) -> None:
        with self._lock:
            self.tables = copy.deepcopy(dict(snapshot))

    def seed_companies_and_stakes(self, *, members: Optional[Iterable[str]] = None, include_stakes: bool = True,
                                  path: Path = SEED_JSON) -> Dict[str, List[Dict[str, Any]]]:
        """Load the REAL seed (``backend/data/trillion_club_seed.json``) as the seed script and
        the daily job leave it: every company (``is_member`` True for each published one, or
        for exactly ``members``), and every stake with no 205 column (origin DEFAULT 'seed').
        Returns the stored rows."""
        seed = json.loads(Path(path).read_text(encoding="utf-8"))
        wanted = None if members is None else set(members)
        companies = []
        for co in seed["companies"]:
            is_member = co["published"] if wanted is None else co["slug"] in wanted
            companies.append(self.admin_insert(COMPANIES, {**co, "is_member": bool(is_member)}))
        stakes = [self.admin_insert(STAKES, s) for s in seed["stakes"]] if include_stakes else []
        return {"companies": companies, "stakes": stakes}

    def add_filing(self, cik: str, period: str, build_status: str, holdings: Sequence[Mapping[str, Any]],
                   **extra: Any) -> Dict[str, Any]:
        """A built 13F row (``trillion_club_filings``) for ``cik`` / ``period`` (``2026-Q2``)."""
        m = re.fullmatch(r"([0-9]{4})-Q([1-4])", period)
        if m is None:
            raise ValueError(f"period {period!r}")
        year, q = int(m.group(1)), int(m.group(2))
        period_end = date(year, 3 * q, 30 if q in (2, 3) else 31)
        row = {"cik": cik, "period": period, "period_end": period_end.isoformat(),
               "holdings": copy.deepcopy(list(holdings)), "position_count": len(holdings),
               "raw_hash": f"test-{cik}-{period}", "build_status": build_status, **extra}
        return self.admin_insert(FILINGS, row)


def make_fake(*, seed: bool = True, members: Optional[Iterable[str]] = None, include_stakes: bool = True,
              max_rows: int = DEFAULT_MAX_ROWS) -> FakeSupabase:
    """A fake holding the real registry (and, by default, the real seed stakes)."""
    fake = FakeSupabase(max_rows=max_rows)
    if seed:
        fake.seed_companies_and_stakes(members=members, include_stakes=include_stakes)
    return fake


def error_constraint(exc: BaseException) -> Optional[str]:
    """The constraint (or, for a NOT NULL, the column) a fake APIError names."""
    message = getattr(exc, "message", None) or ""
    m = re.search(r'constraint "([^"]+)"', message) or re.search(r'column "([^"]+)"', message)
    return m.group(1) if m else None


__all__ = [
    "COMPANIES", "STAKES", "FINDINGS", "FILINGS", "DEFAULT_MAX_ROWS", "SCHEMA", "PRIMARY_KEYS", "UNIQUES",
    "FOREIGN_KEYS", "Column", "Statement", "FakeCall", "FakeMisuse", "FakeSupabase", "api_error",
    "lost_response_error", "check_violations", "make_fake", "error_constraint",
]
