"""Supabase I/O for the Trillion-Dollar Club jobs (``jobs.py``). Nothing else lives here.

Every statement runs OFF the event loop through ``retry_idempotent_async`` (supabase-py is
synchronous and Railway runs one uvicorn worker), and every statement is idempotent by
construction, which is what makes that retry safe:

* reads;
* a slug-keyed UPDATE with a fixed payload (the membership columns);
* an UPSERT on the ``(cik, period)`` primary key (a built 13F snapshot);
* an INSERT … ON CONFLICT (slug) DO NOTHING (a discovered company, always unpublished).

A transient 520 is retried by the helper; anything else — and a response that is not the
shape PostgREST promises — raises :class:`TrillionClubStoreError` naming the operation. The
jobs FAIL CLOSED on it: a failed read writes nothing, a failed write is reported and the
run is marked unsuccessful (retried within the per-day attempt cap).

The only tables touched are the three ``trillion_club_*`` ones; a test pins that no
``.table()`` call in this package names anything else (no ``whales``, no follows, no
notification tables).
"""

from __future__ import annotations

import logging
import math
import re
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from app.services.trillion_club.rules import MembershipState
from app.utils.supabase_errors import retry_idempotent_async

logger = logging.getLogger(__name__)

COMPANIES_TABLE = "trillion_club_companies"
FILINGS_TABLE = "trillion_club_filings"

#: The registry is ~20 rows; this is a ceiling that makes truncation loud, not a page size.
MAX_COMPANY_ROWS = 500
#: Stored quarters per CIK the jobs look at (they only ever consider the newest few).
MAX_FILING_ROWS_PER_CIK = 60

_COMPANY_COLUMNS = (
    "slug, display_name, ciks, card_kind, use_13f, cap_symbol, symbol_aliases, "
    "detail_symbol, logo_symbol, home_country, cap_source, manual_cap_usd, manual_cap_as_of, "
    "membership_mode, is_member, member_since, last_market_cap, last_cap_date, "
    "closes_at_or_above, closes_below, membership_checked_at, link_whale, published"
)
_FILING_INDEX_COLUMNS = "cik, period, raw_hash, build_status, unresolved, built_at"

_SLUG_RE = re.compile(r"^[a-z0-9-]{1,40}$")
_CIK_RE = re.compile(r"^[0-9]{10}$")
_PERIOD_RE = re.compile(r"^[0-9]{4}-Q[1-4]$")

#: Keys a discovered company row must carry (every row the same key set, so PostgREST's
#: column list never turns an omitted NOT NULL column into an explicit NULL).
DISCOVERED_ROW_KEYS = frozenset({
    "slug", "display_name", "ciks", "card_kind", "use_13f", "cap_symbol", "symbol_aliases",
    "detail_symbol", "logo_symbol", "home_country", "cap_source", "membership_mode",
    "link_whale", "published",
})

_FILING_ROW_KEYS = frozenset({
    "cik", "period", "period_end", "filed_on", "amended_on", "accessions", "total_value",
    "position_count", "holdings", "changes", "excluded_rows", "unresolved", "raw_hash",
    "build_status", "source",
})


class TrillionClubStoreError(Exception):
    """A Supabase read or write for the Trillion-Dollar Club jobs failed (or answered in a
    shape it never should). Carries the operation so the log line is diagnosable alone."""

    def __init__(self, op: str, cause: BaseException):
        self.op = op
        self.cause = cause
        super().__init__(f"trillion club store: {op} failed ({type(cause).__name__}: {cause})")


def _aware(ts: datetime, what: str) -> datetime:
    if not isinstance(ts, datetime) or ts.tzinfo is None:
        raise ValueError(f"trillion club store: {what} must be a timezone-aware datetime, got {ts!r}")
    return ts


def membership_payload(state: MembershipState, *, checked_at: datetime) -> Dict[str, Any]:
    """The UPDATE payload for one company's membership columns (pure; validated).

    Raises ``ValueError`` / ``TypeError`` for a value the table CHECKs would reject — a bug
    upstream, never something to write and let Postgres refuse.
    """
    if not isinstance(state, MembershipState):
        raise TypeError(f"membership_payload: expected MembershipState, got {type(state).__name__}")
    stamp = _aware(checked_at, "checked_at").isoformat()
    last_cap = state.last_cap
    if last_cap is not None:
        if isinstance(last_cap, bool) or not isinstance(last_cap, (int, float)) \
                or not math.isfinite(last_cap) or last_cap <= 0:
            raise ValueError(f"membership_payload: last_cap must be finite and > 0, got {last_cap!r}")
    for name in ("closes_at_or_above", "closes_below"):
        v = getattr(state, name)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ValueError(f"membership_payload: {name} must be an int >= 0, got {v!r}")
    for name in ("member_since", "last_cap_date"):
        v = getattr(state, name)
        if v is not None and not isinstance(v, date):
            raise ValueError(f"membership_payload: {name} must be a date or None, got {v!r}")
    return {
        "is_member": bool(state.is_member),
        "member_since": state.member_since.isoformat() if state.member_since else None,
        "closes_at_or_above": state.closes_at_or_above,
        "closes_below": state.closes_below,
        "last_market_cap": float(last_cap) if last_cap is not None else None,
        "last_cap_date": state.last_cap_date.isoformat() if state.last_cap_date else None,
        "membership_checked_at": stamp,
        "updated_at": stamp,
    }


class TrillionClubStore:
    """Supabase access for the jobs. ``client`` is a Supabase client (the service-role one
    from ``get_supabase()`` when omitted); tests pass an in-memory stand-in."""

    def __init__(self, client: Any = None):
        self._client = client

    def _db(self) -> Any:
        if self._client is None:
            from app.database import get_supabase

            self._client = get_supabase()
        return self._client

    async def _run(self, op: str, build: Callable[[], Any]) -> Any:
        """Execute ``build()`` off the loop with the transient-error retry; wrap failures."""
        try:
            response = await retry_idempotent_async(
                lambda: build().execute(), what=f"trillion club {op}", logger=logger,
            )
        except Exception as e:
            raise TrillionClubStoreError(op, e) from e
        return getattr(response, "data", None)

    # ── Companies ────────────────────────────────────────────────────────────────────

    async def read_companies(self) -> List[Dict[str, Any]]:
        """Every registry row (published or not — the job owns membership for all of them)."""
        op = "read companies"
        data = await self._run(
            op,
            lambda: self._db().table(COMPANIES_TABLE).select(_COMPANY_COLUMNS)
            .order("slug").limit(MAX_COMPANY_ROWS),
        )
        if not isinstance(data, list):
            raise TrillionClubStoreError(op, TypeError(f"expected a list, got {type(data).__name__}"))
        if len(data) >= MAX_COMPANY_ROWS:
            logger.warning(
                "trillion club store: %s returned %d rows — the %d-row ceiling was hit, so "
                "some companies may be missing from this run", op, len(data), MAX_COMPANY_ROWS,
            )
        rows: List[Dict[str, Any]] = []
        for r in data:
            if isinstance(r, dict):
                rows.append(dict(r))
            else:
                logger.warning("trillion club store: %s skipped a non-object row (%s)",
                               op, type(r).__name__)
        return rows

    async def update_membership(
        self, slug: str, state: MembershipState, *, checked_at: datetime
    ) -> None:
        """Write the job-owned membership columns for ``slug`` and stamp
        ``membership_checked_at``. Raises when no row matched (the slug vanished mid-run)."""
        if not isinstance(slug, str) or not _SLUG_RE.match(slug):
            raise ValueError(f"update_membership: bad slug {slug!r}")
        payload = membership_payload(state, checked_at=checked_at)
        op = f"update membership slug={slug}"
        data = await self._run(
            op, lambda: self._db().table(COMPANIES_TABLE).update(payload).eq("slug", slug),
        )
        if not isinstance(data, list) or not data:
            raise TrillionClubStoreError(op, LookupError("no row matched the slug"))

    async def insert_discovered(self, rows: Sequence[Mapping[str, Any]]) -> List[str]:
        """Insert newly discovered companies, ALWAYS unpublished and never 13F-ingested.

        ``published`` and ``use_13f`` are forced False here whatever the caller passed —
        nothing reaches Home without the owner. An existing slug is left untouched
        (ON CONFLICT DO NOTHING). Returns the slugs actually inserted.
        """
        payload: List[Dict[str, Any]] = []
        for r in rows or ():
            if not isinstance(r, Mapping):
                raise ValueError(f"insert_discovered: row is {type(r).__name__}, not a mapping")
            missing = DISCOVERED_ROW_KEYS - set(r)
            extra = set(r) - DISCOVERED_ROW_KEYS
            if missing or extra:
                raise ValueError(
                    f"insert_discovered: row {r.get('slug')!r} keys differ — missing "
                    f"{sorted(missing)}, unexpected {sorted(extra)}"
                )
            slug = r.get("slug")
            if not isinstance(slug, str) or not _SLUG_RE.match(slug):
                raise ValueError(f"insert_discovered: bad slug {slug!r}")
            row = dict(r)
            row["published"] = False
            row["use_13f"] = False
            payload.append(row)
        if not payload:
            return []
        op = f"insert {len(payload)} discovered company row(s)"
        data = await self._run(
            op,
            lambda: self._db().table(COMPANIES_TABLE)
            .upsert(payload, on_conflict="slug", ignore_duplicates=True),
        )
        if data is None:
            return []
        if not isinstance(data, list):
            raise TrillionClubStoreError(op, TypeError(f"expected a list, got {type(data).__name__}"))
        return [str(r["slug"]) for r in data if isinstance(r, dict) and r.get("slug")]

    # ── Filings ──────────────────────────────────────────────────────────────────────

    async def read_filings(self, cik: str) -> Dict[str, Dict[str, Any]]:
        """``{period: {raw_hash, build_status, unresolved, built_at}}`` for one CIK."""
        if not isinstance(cik, str) or not _CIK_RE.match(cik):
            raise ValueError(f"read_filings: cik must be 10 digits, got {cik!r}")
        op = f"read filings cik={cik}"
        data = await self._run(
            op,
            lambda: self._db().table(FILINGS_TABLE).select(_FILING_INDEX_COLUMNS)
            .eq("cik", cik).order("period", desc=True).limit(MAX_FILING_ROWS_PER_CIK),
        )
        if not isinstance(data, list):
            raise TrillionClubStoreError(op, TypeError(f"expected a list, got {type(data).__name__}"))
        out: Dict[str, Dict[str, Any]] = {}
        for r in data:
            period = r.get("period") if isinstance(r, dict) else None
            if not isinstance(period, str) or not _PERIOD_RE.match(period) or r.get("cik") not in (None, cik):
                logger.warning("trillion club store: %s skipped a malformed row (%r)", op,
                               {k: r.get(k) for k in ("cik", "period")} if isinstance(r, dict) else type(r).__name__)
                continue
            unresolved = r.get("unresolved")
            out[period] = {
                "raw_hash": r.get("raw_hash") if isinstance(r.get("raw_hash"), str) else None,
                "build_status": r.get("build_status"),
                "unresolved": dict(unresolved) if isinstance(unresolved, dict) else {},
                "built_at": r.get("built_at"),
            }
        return out

    async def upsert_filing(self, row: Mapping[str, Any], *, built_at: datetime) -> None:
        """Upsert one ``BuiltFiling.as_row()`` on ``(cik, period)`` and stamp ``built_at``
        (the column DEFAULT applies only on INSERT, so a rebuild must set it)."""
        if not isinstance(row, Mapping) or set(row) != _FILING_ROW_KEYS:
            got = sorted(row) if isinstance(row, Mapping) else type(row).__name__
            raise ValueError(f"upsert_filing: expected BuiltFiling.as_row() keys, got {got}")
        payload = dict(row)
        payload["built_at"] = _aware(built_at, "built_at").isoformat()
        op = f"upsert filing cik={payload.get('cik')} period={payload.get('period')}"
        await self._run(
            op,
            lambda: self._db().table(FILINGS_TABLE)
            .upsert(payload, on_conflict="cik,period", returning="minimal"),
        )


__all__ = [
    "COMPANIES_TABLE", "FILINGS_TABLE", "MAX_COMPANY_ROWS", "MAX_FILING_ROWS_PER_CIK",
    "DISCOVERED_ROW_KEYS", "TrillionClubStoreError", "TrillionClubStore", "membership_payload",
]
