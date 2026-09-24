"""`GET /users/me/notifications/lookup?dedup_key=…` — the row behind a TAPPED PUSH.

WHY THIS EXISTS. A push tap now opens the notification's DETAIL screen before anything else
(developer, 2026-09-23: *"open the detail screen first, so they can read the content before
they decide to go any further. Not to open the ticker right away."*). The app renders that
screen from the payload at once, but the payload cannot carry what the screen is for:

  * APNs gets the body cut to `BANNER_BODY_LIMIT` (180 chars + "…"); the row keeps up to
    `LEDGER_BODY_LIMIT`. For a `ticker_move` the part that was cut IS the catalyst.
  * The payload has no `notification_events.id` — only `dedup_key`, the other half of the
    `(user_id, dedup_key)` unique index (migration 119). `claim_send`'s bool contract is
    deliberately left alone, so that is the lookup key.

What must hold, and what each test pins:
  1. The row comes back flattened EXACTLY as the list flattens it — the detail screen builds
     its destination rows from `route`, and the two doors must offer the same ones.
  2. It is scoped to the caller. Keys are guessable (`move:TER:2026-09-14` exists once per
     watcher) and the backend reads with the service-role key, so the `user_id` filter is the
     only wall — the same IDOR reasoning as `mark_read`.
  3. "No such row" is a normal answer (`item: null`), "couldn't look" is a 503 — never
     confused, because a client keeps its pushed copy on either but only one is an outage.
  4. Over the wire: strict auth, a bounded key, and the iOS decoder agrees on the shape.
"""

import logging
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.error_response import ErrorCode
from app.api.v1.endpoints import users as users_endpoint
from app.dependencies import get_current_user
from app.main import app
from app.schemas.notifications import NotificationLookupResponse
from app.services.notification_inbox_service import (
    NotificationInboxService,
    NotificationInboxUnavailable,
)

_PATH = "/api/v1/users/me/notifications/lookup"
_USER = {"id": "u1", "email": "user@example.test", "is_guest": False}
_KEY = "move:TER:2026-09-14"
_MODELS = (
    Path(__file__).resolve().parents[2] / "frontend/ios/ios/Models/NotificationModels.swift"
)

_ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "kind": "ticker_move",
    "category": "watchlist",
    "title": "TER -10.4%",
    # Longer than the 180-char banner cut — the whole reason the lookup exists.
    "body": ("Teradyne fell after guiding third-quarter revenue below consensus, citing a "
             "slower recovery in its semiconductor test business and weaker mobile demand, "
             "while reiterating its full-year robotics outlook."),
    "route": {"route": "ticker", "ticker": "TER", "asset_type": "stock", "kind": "ticker_move",
              "nested": {"dropped": True}},
    "claimed_at": "2026-09-14T13:57:21.462+00:00",
    "read_at": None,
    "push_state": "sent",
}


# ── the service ──────────────────────────────────────────────────────────────


class _Query:
    """Records every filter and whether the query ran, so a MISSING filter is assertable."""

    def __init__(self, rec, rows, raises):
        self.rec, self.rows, self.raises = rec, rows, raises

    def select(self, cols):
        self.rec["select"] = cols
        return self

    def eq(self, col, val):
        self.rec.setdefault("eq", []).append((col, val))
        return self

    def limit(self, n):
        self.rec["limit"] = n
        return self

    def execute(self):
        self.rec["executed"] = self.rec.get("executed", 0) + 1
        if self.raises:
            raise self.raises
        rows = self.rows

        class _R:
            data = rows
        return _R()


def _service(rows=None, raises=None):
    rec = {}

    class _Supa:
        def table(self, name):
            rec["table"] = name
            return _Query(rec, rows if rows is not None else [], raises)

    svc = object.__new__(NotificationInboxService)
    svc.supabase = _Supa()
    return svc, rec


def test_the_row_comes_back_flattened_exactly_as_the_list_flattens_it():
    svc, _ = _service(rows=[dict(_ROW)])
    got = svc.get_by_dedup_key("u1", _KEY)
    assert got == svc._to_response(dict(_ROW)), (
        "the lookup maps a row differently from the list — the push door and the Alerts door "
        "would offer different destinations for the same notification"
    )
    assert got.body == _ROW["body"], "the full body is the point of the lookup"
    assert "nested" not in got.route, "a non-scalar route value reached iOS"


def test_the_lookup_is_scoped_to_the_caller_as_well_as_the_key():
    """The IDOR wall. Without `user_id`, any account could read any other account's alert by
    guessing a ticker and a date."""
    svc, rec = _service(rows=[dict(_ROW)])
    svc.get_by_dedup_key("u1", _KEY)
    assert ("user_id", "u1") in rec["eq"], (
        "the lookup dropped the user scope — one user could read another's notification by "
        "guessing `move:<ticker>:<date>`"
    )
    assert ("dedup_key", _KEY) in rec["eq"]
    assert rec["table"] == "notification_events"
    assert rec["executed"] == 1


def test_no_row_is_none_not_an_error():
    """A push can outlive its row (90-day retention)."""
    svc, _ = _service(rows=[])
    assert svc.get_by_dedup_key("u1", _KEY) is None


def test_an_unusable_row_is_none_not_a_crash():
    svc, _ = _service(rows=[{"kind": "ticker_move"}])   # no id
    assert svc.get_by_dedup_key("u1", _KEY) is None


def test_a_read_failure_raises_instead_of_reading_as_not_found():
    """A confident `None` over a database error would tell the client "no such row" when the
    truth is "couldn't look" — and nobody would ever see the outage."""
    svc, _ = _service(raises=RuntimeError("db down"))
    with pytest.raises(NotificationInboxUnavailable):
        svc.get_by_dedup_key("u1", _KEY)


# ── over the wire ────────────────────────────────────────────────────────────


class _Stub:
    def __init__(self, result=None, raises=None):
        self.result, self.raises, self.calls = result, raises, []

    def get_by_dedup_key(self, user_id, dedup_key):
        self.calls.append((user_id, dedup_key))
        if self.raises:
            raise self.raises
        return self.result


def _install(monkeypatch, stub):
    monkeypatch.setattr(users_endpoint, "get_notification_inbox_service", lambda: stub)
    return stub


@pytest.fixture
def client():
    # NOT `with TestClient(app)` — the lifespan's startup jobs reach Supabase, which conftest
    # blocks.
    logging.disable(logging.CRITICAL)
    try:
        yield TestClient(app)
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture
def signed_in():
    app.dependency_overrides[get_current_user] = lambda: dict(_USER)
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_a_hit_returns_the_row_for_the_caller(client, signed_in, monkeypatch):
    svc, _ = _service(rows=[dict(_ROW)])
    stub = _install(monkeypatch, _Stub(result=svc._to_response(dict(_ROW))))
    resp = client.get(_PATH, params={"dedup_key": _KEY, "user_id": "victim"})
    assert resp.status_code == 200, resp.text
    assert stub.calls == [("u1", _KEY)], (
        "the id must be the credential's — a `user_id` in the URL is ignored, not honoured"
    )
    body = resp.json()
    NotificationLookupResponse.model_validate(body)
    assert body["item"]["body"] == _ROW["body"]
    assert body["item"]["created_at"], "the detail screen's 'Received' row needs the stamp"


def test_a_miss_is_a_200_with_a_null_item(client, signed_in, monkeypatch):
    _install(monkeypatch, _Stub(result=None))
    resp = client.get(_PATH, params={"dedup_key": _KEY})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"item": None}


def test_an_outage_is_a_503_on_the_error_contract(client, signed_in, monkeypatch):
    _install(monkeypatch, _Stub(raises=NotificationInboxUnavailable("db down")))
    resp = client.get(_PATH, params={"dedup_key": _KEY})
    assert resp.status_code == 503, resp.text
    assert resp.json()["error_code"] == ErrorCode.NOTIFICATIONS_UNAVAILABLE.value


@pytest.mark.parametrize("params", [{}, {"dedup_key": ""}, {"dedup_key": "k" * 513}],
                         ids=["missing", "empty", "overlong"])
def test_a_bad_key_is_refused_before_the_service(client, signed_in, monkeypatch, params):
    stub = _install(monkeypatch, _Stub(result=None))
    resp = client.get(_PATH, params=params)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error_code"] == ErrorCode.INVALID_INPUT.value
    assert stub.calls == [], "the service ran on a key the route should have refused"


def test_no_credential_is_refused_before_the_service(client, monkeypatch):
    """Strict `get_current_user`: a push never reaches a guest, and an inbox row is the
    caller's own data (auth.md §1a)."""
    stub = _install(monkeypatch, _Stub(result=None))
    resp = client.get(_PATH, params={"dedup_key": _KEY})
    assert resp.status_code == 401, resp.text
    assert stub.calls == []


@pytest.mark.parametrize("key", [
    "pa:22222222-2222-2222-2222-222222222222:2026-09-14",
    # The insider sender embeds the filer's NAME — spaces, punctuation, and no length bound
    # upstream. A long legitimate key must not be refused as "overlong".
    "insider:BRK.B:2026-09-10:BERKSHIRE HATHAWAY INC /DE/ & AFFILIATES, L.P. (Class A + B):S",
    "insider:ORCL:2026-09-10:" + "X" * 300 + ":S",
], ids=["price_alert", "insider_punctuation", "insider_long_name"])
def test_a_realistic_key_survives_the_query_string(client, signed_in, monkeypatch, key):
    """Colons, hyphens, spaces, slashes, `&`, `+` and `,` must all arrive intact."""
    stub = _install(monkeypatch, _Stub(result=None))
    resp = client.get(_PATH, params={"dedup_key": key})
    assert resp.status_code == 200, resp.text
    assert stub.calls == [("u1", key)]


# ── the iOS half ─────────────────────────────────────────────────────────────


def test_the_ios_wrapper_decodes_item_as_optional():
    """`item: null` is a normal answer. A non-optional `item` would turn every miss into a
    decode failure — survivable here (the pushed copy stays) but logged as a fault forever."""
    src = _MODELS.read_text(encoding="utf-8")
    block = src[src.index("struct NotificationLookupDTO"):]
    block = block[: block.index("\n}")]
    assert re.search(r"let item: NotificationEventDTO\?", block), (
        "NotificationLookupDTO.item is no longer Optional"
    )
    assert set(NotificationLookupResponse.model_fields) == {"item"}, (
        "the lookup response grew a field the iOS wrapper does not decode"
    )
