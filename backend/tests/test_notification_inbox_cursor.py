"""The inbox's keyset cursor survives the query-string round trip (TestFlight, 2026-09-11).

    "Why we have this error? Also, what is the time interval for notification?
     2 weeks only?"

Page 2 of Tracking › Alerts failed with 503 NOTIFICATIONS_UNAVAILABLE on every build.
The server minted `next_cursor = "2026-08-28T14:17:21.462+00:00|<uuid>"`; iOS put it in
a query value through `URLComponents`, which leaves a literal `+` unencoded; Starlette's
form decoder turned that `+` into a SPACE; the service interpolated
`claimed_at.lt.2026-08-28T14:17:21.462 00:00` into a PostgREST filter, and Postgres
answered 22007 (invalid timestamptz) — wrapped into the 503. Everything older than the
first page was unreachable, which read as a two-week retention window.

Both halves are pinned here: the cursor is now minted with no `+` in it at all (a `Z`
stamp), and the parser repairs the space-for-plus mangling from the cursors unpatched
clients still hold. The iOS half (`%2B`) is pinned in `test_ios_query_plus_encoding.py`.
No network: a recording fake stands in for the table.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qsl, urlencode

import pytest

import app.api.v1.endpoints.users as users_endpoint
from app.api.error_response import ErrorCode
from app.api.v1.endpoints.users import list_my_notifications
from app.services.notification_inbox_service import (
    InvalidCursor,
    NotificationInboxService,
    NotificationInboxUnavailable,
    mint_cursor,
    parse_cursor,
)

UUID1 = "3f2b7c0e-1111-2222-3333-444444444444"
UUID2 = "3f2b7c0e-5555-6666-7777-888888888888"
# Exactly as PostgREST returns `claimed_at` — a +00:00 offset, sometimes < 6 fractional digits.
STAMP_DB = "2026-08-28T14:17:21.462+00:00"
STAMP_SHORT_DB = "2026-08-22T21:48:56.57591+00:00"


# ── fake table ───────────────────────────────────────────────────────────────────

class _FakeQuery:
    """Records every filter; the limit is applied at `execute()` like the real thing."""

    def __init__(self, rows, log):
        self._rows = rows
        self._log = log
        self._limit = None

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._log.append(("eq", col, val))
        return self

    def is_(self, col, val):
        self._log.append(("is", col, val))
        return self

    def or_(self, expr):
        self._log.append(("or", expr))
        return self

    def lt(self, col, val):
        self._log.append(("lt", col, val))
        return self

    def order(self, col, desc=False):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        rows = self._rows if self._limit is None else self._rows[: self._limit]

        class _R:
            data = rows
        return _R()


class _FakeSupabase:
    def __init__(self, rows):
        self.rows = rows
        self.log = []

    def table(self, name):
        return _FakeQuery(self.rows, self.log)


def _row(i, claimed_at, row_id):
    return {
        "id": row_id, "kind": "ticker_move", "category": "watchlist", "title": f"T{i}",
        "body": "b", "route": {"ticker": "TER"}, "claimed_at": claimed_at, "read_at": None,
        "push_state": "sent",
    }


def _service(rows):
    svc = NotificationInboxService.__new__(NotificationInboxService)
    svc.supabase = _FakeSupabase(rows)
    return svc


def _filters(svc):
    return [e for e in svc.supabase.log if e[0] in ("or", "lt")]


# ── minting ──────────────────────────────────────────────────────────────────────

def test_the_minted_cursor_carries_no_plus_sign():
    cur = mint_cursor(STAMP_DB, UUID1)
    assert cur == f"2026-08-28T14:17:21.462000Z|{UUID1}"
    assert "+" not in cur


def test_the_minted_cursor_survives_a_form_decoder_byte_for_byte():
    """The proof the fix is by construction: an UNPATCHED client that sends the `+`
    raw cannot mangle a cursor that has none. `urlencode` here plays URLComponents
    (it encodes `|` but a raw `+` would pass), `parse_qsl` plays Starlette."""
    cur = mint_cursor(STAMP_DB, UUID1)
    wire = urlencode({"before": cur}, safe="+:")
    assert dict(parse_qsl(wire))["before"] == cur


def test_a_short_fraction_is_padded_not_rejected():
    assert mint_cursor(STAMP_SHORT_DB, UUID1) == f"2026-08-22T21:48:56.575910Z|{UUID1}"


@pytest.mark.parametrize("bad", [None, "", "yesterday", 12345])
def test_an_unusable_claimed_at_mints_no_cursor(bad):
    assert mint_cursor(bad, UUID1) is None


def test_a_page_with_more_rows_mints_from_the_raw_last_row():
    rows = [_row(i, STAMP_DB, UUID1 if i < 2 else UUID2) for i in range(3)]
    page = _service(rows).list_for_user("u1", limit=2)
    assert page.next_cursor == f"2026-08-28T14:17:21.462000Z|{UUID1}"
    assert len(page.items) == 2


# ── parsing ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cursor", [
    f"2026-08-28T14:17:21.462000Z|{UUID1}",   # what this service mints
    f"2026-08-28T14:17:21.462+00:00|{UUID1}",  # what older servers minted
    f"2026-08-28T14:17:21.462 00:00|{UUID1}",  # …after the client's raw `+` was form-decoded
    f"2026-08-28T14:17:21.462Z|{UUID1}",
])
def test_every_shape_in_the_wild_parses_to_one_canonical_stamp(cursor):
    stamp, last_id = parse_cursor(cursor)
    assert stamp == "2026-08-28T14:17:21.462000+00:00"
    assert last_id == UUID1


def test_the_repaired_stamp_reaches_postgrest_as_a_valid_timestamptz():
    """The 22007 path: the mangled cursor now produces the composite filter with a
    parseable stamp — the exact request that used to 503."""
    svc = _service([_row(0, STAMP_DB, UUID2)])
    svc.list_for_user("u1", limit=30, before=f"2026-08-28T14:17:21.462 00:00|{UUID1}")
    assert _filters(svc) == [(
        "or",
        f"claimed_at.lt.2026-08-28T14:17:21.462000+00:00,"
        f"and(claimed_at.eq.2026-08-28T14:17:21.462000+00:00,id.lt.{UUID1})",
    )]


def test_a_stamp_only_cursor_degrades_to_the_single_column_keyset():
    svc = _service([])
    svc.list_for_user("u1", limit=30, before="2026-08-28T14:17:21.462Z")
    assert _filters(svc) == [("lt", "claimed_at", "2026-08-28T14:17:21.462000+00:00")]


def test_a_naive_stamp_is_read_as_utc():
    assert parse_cursor(f"2026-08-28T14:17:21|{UUID1}")[0] == "2026-08-28T14:17:21+00:00"


def test_a_non_utc_offset_is_normalised_to_utc():
    assert parse_cursor(f"2026-08-28T10:17:21.462-04:00|{UUID1}")[0] == "2026-08-28T14:17:21.462000+00:00"


@pytest.mark.parametrize("cursor", [
    "garbage", "|", f"|{UUID1}", "2026-13-45T99:99:99Z", "2026-08-28T14:17:21.462Z|not-a-uuid",
    f"2026-08-28T14:17:21.462Z|{UUID1}|extra", "'; drop table notification_events; --",
    f"2026-08-28T14:17:21.462Z|{UUID1},or(user_id.neq.x)",
])
def test_anything_else_is_refused_before_the_database(cursor):
    svc = _service([_row(0, STAMP_DB, UUID2)])
    with pytest.raises(InvalidCursor):
        svc.list_for_user("u1", limit=30, before=cursor)
    assert svc.supabase.log == [], "a refused cursor must never reach the table"


def test_a_bad_cursor_is_not_a_503():
    """`InvalidCursor` must not be wrapped into `NotificationInboxUnavailable` — one is
    the caller's problem (400), the other the database's (503, retry later)."""
    assert not issubclass(InvalidCursor, NotificationInboxUnavailable)
    with pytest.raises(InvalidCursor):
        _service([]).list_for_user("u1", before="nope")


def test_a_page_round_trips_through_its_own_cursor():
    rows = [_row(i, STAMP_DB, UUID1) for i in range(2)]
    svc = _service(rows)
    page = svc.list_for_user("u1", limit=1)
    assert page.next_cursor
    stamp, last_id = parse_cursor(page.next_cursor)
    assert stamp == "2026-08-28T14:17:21.462000+00:00" and last_id == UUID1


# ── the endpoint's two error contracts ──────────────────────────────────────────

_USER = {"id": "u1", "email": "x@y", "tier": "free"}


class _Raising:
    def __init__(self, exc):
        self.exc = exc

    def list_for_user(self, user_id, *, limit, before):
        raise self.exc


@pytest.mark.asyncio
async def test_the_endpoint_answers_a_bad_cursor_with_400_invalid_input(monkeypatch):
    monkeypatch.setattr(users_endpoint, "get_notification_inbox_service",
                        lambda: _Raising(InvalidCursor("unreadable cursor stamp 'nope'")))
    resp = await list_my_notifications(limit=30, before="nope", user=_USER)
    assert resp.status_code == 400
    body = json.loads(resp.body)
    assert body["error_code"] == ErrorCode.INVALID_INPUT.value
    assert body["user_message"]


@pytest.mark.asyncio
async def test_the_endpoint_still_answers_a_read_failure_with_503(monkeypatch):
    monkeypatch.setattr(users_endpoint, "get_notification_inbox_service",
                        lambda: _Raising(NotificationInboxUnavailable("22007")))
    resp = await list_my_notifications(limit=30, before=None, user=_USER)
    assert resp.status_code == 503
    assert json.loads(resp.body)["error_code"] == ErrorCode.NOTIFICATIONS_UNAVAILABLE.value
