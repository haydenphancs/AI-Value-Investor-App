"""`PUT /api/v1/widget/push-token` — the APP uploads the widget's WidgetKit push token (iOS 26+).

Owner 2026-11-27: "Yes, add push too". The token rides on the device's Holdings grant row
(migration 216), so every way the grant dies stops the pushes with no extra hook. The route is on
the STRICT widget router — a real session on a live account, `get_current_user` — never on either
widget router: it carries no market data and must never accept a widget or grant credential.

Its answers are a contract the app branches on, with no new ErrorCode (DEGRADE, never error):

    stored                              → {stored: true,  grant_usable: true}
    unknown / foreign / dead / superseded grant
                                        → {stored: false, grant_usable: false}  (app re-mints)
    a database error                    → {stored: false, grant_usable: true}   (retry later)
    a malformed body                    → 422 in the APIErrorResponse shape

Hermetic: TestClient over the real app, `get_supabase` overridden by the 214 + 216 fake.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt

from _refresh_session_fake import SupabaseFake
from app.api.v1.endpoints import widget
from app.config import settings
from app.core.security import create_access_token, create_widget_token, rate_limiter
from app.database import get_supabase
from app.dependencies import HOLDINGS_GRANT_HEADER, WIDGET_TOKEN_HEADER
from app.main import app
from app.services import auth_session_service as sessions
from app.services import widget_holdings_grant_service as grants

UID = "11111111-2222-4333-8444-555555555555"
OTHER = "99999999-8888-4777-8666-555555555555"
URL = "/api/v1/widget/push-token"
TOKEN = "ab" * 32


@pytest.fixture(autouse=True)
def _state():
    rate_limiter.clear()
    yield
    rate_limiter.clear()


@pytest.fixture
def db():
    fake = SupabaseFake(users={
        UID: {"password_changed_at": None, "sessions_revoked_at": None},
        OTHER: {"password_changed_at": None, "sessions_revoked_at": None},
    })
    previous = app.dependency_overrides.get(get_supabase)
    app.dependency_overrides[get_supabase] = lambda: fake
    try:
        yield fake
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_supabase, None)
        else:
            app.dependency_overrides[get_supabase] = previous


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app)


def _bearer(sub: str = UID, issued_ago: timedelta = timedelta(0)) -> dict:
    iat = datetime.now(timezone.utc) - issued_ago
    token = jwt.encode({"sub": sub, "type": "access", "iat": iat, "exp": iat + timedelta(hours=1)},
                       settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return {"Authorization": f"Bearer {token}"}


def _grant(db, uid: str = UID) -> grants.MintedGrant:
    issued = sessions.start_session(db, uid, "x@example.com")
    out = grants.mint(db, uid, issued.sid, int(db.store.now().timestamp()))
    assert out.status == "minted"
    return out.grant


def _body(grant_id: str, token=TOKEN, env="production") -> dict:
    return {"grant_id": grant_id, "token": token, "environment": env}


# ── the happy path and the contract ─────────────────────────────────────────────────────────

def test_a_live_grant_stores_the_token(client, db):
    g = _grant(db)
    r = client.put(URL, headers=_bearer(), json=_body(g.grant_id))
    assert r.status_code == 200, r.text
    assert r.json() == {"stored": True, "grant_usable": True}
    assert db.store.grants[g.grant_id]["push_token"] == TOKEN
    assert db.store.grants[g.grant_id]["push_environment"] == "production"


def test_an_upper_case_token_is_normalised(client, db):
    g = _grant(db)
    r = client.put(URL, headers=_bearer(), json=_body(g.grant_id, token=TOKEN.upper()))
    assert r.status_code == 200 and db.store.grants[g.grant_id]["push_token"] == TOKEN


@pytest.mark.parametrize("token", [None, "__absent__"])
def test_a_null_or_absent_token_clears_it(client, db, token):
    g = _grant(db)
    client.put(URL, headers=_bearer(), json=_body(g.grant_id))
    body = {"grant_id": g.grant_id, "environment": "sandbox"}
    if token is None:
        body["token"] = None
    r = client.put(URL, headers=_bearer(), json=body)
    assert r.json() == {"stored": True, "grant_usable": True}
    assert db.store.grants[g.grant_id]["push_token"] is None


def test_another_accounts_grant_is_refused_without_enumeration(client, db):
    theirs = _grant(db, OTHER)
    unknown = client.put(URL, headers=_bearer(),
                         json=_body("00000000-0000-4000-8000-000000000000"))
    foreign = client.put(URL, headers=_bearer(), json=_body(theirs.grant_id))
    assert unknown.json() == foreign.json() == {"stored": False, "grant_usable": False}
    assert db.store.grants[theirs.grant_id]["push_token"] is None


def test_a_dead_or_superseded_grant_is_not_usable(client, db):
    issued = sessions.start_session(db, UID, "x@example.com")
    old = grants.mint(db, UID, issued.sid, int(db.store.now().timestamp())).grant
    grants.mint(db, UID, issued.sid, int(db.store.now().timestamp()))
    assert client.put(URL, headers=_bearer(), json=_body(old.grant_id)).json() == {
        "stored": False, "grant_usable": False}
    dead = _grant(db)
    grants.revoke(db, dead.grant_id, UID, "app_revoked")
    assert client.put(URL, headers=_bearer(), json=_body(dead.grant_id)).json() == {
        "stored": False, "grant_usable": False}


def test_a_database_error_degrades_to_retry_later(client, db, caplog):
    caplog.set_level(logging.ERROR)
    g = _grant(db)
    db.store.fail = {"widget_push_register"}
    r = client.put(URL, headers=_bearer(), json=_body(g.grant_id))
    assert r.status_code == 200
    assert r.json() == {"stored": False, "grant_usable": True}
    assert any("widget push: token registration failed" in rec.getMessage()
               for rec in caplog.records)


def test_the_token_is_never_logged(client, db, caplog):
    caplog.set_level(logging.DEBUG)
    g = _grant(db)
    client.put(URL, headers=_bearer(), json=_body(g.grant_id))
    db.store.fail = {"widget_push_register"}
    client.put(URL, headers=_bearer(), json=_body(g.grant_id))
    assert TOKEN not in "\n".join(r.getMessage() for r in caplog.records)


# ── 422 shapes ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body", [
    {"grant_id": "not-a-uuid", "token": TOKEN, "environment": "production"},
    {"token": TOKEN, "environment": "production"},
    {"grant_id": "00000000-0000-4000-8000-000000000000", "token": TOKEN, "environment": "prod"},
    {"grant_id": "00000000-0000-4000-8000-000000000000", "token": TOKEN},
    {"grant_id": "00000000-0000-4000-8000-000000000000", "token": "ab" * 15,
     "environment": "sandbox"},
    {"grant_id": "00000000-0000-4000-8000-000000000000", "token": "a" * 33,
     "environment": "sandbox"},
    {"grant_id": "00000000-0000-4000-8000-000000000000", "token": "zz" * 16,
     "environment": "sandbox"},
    {"grant_id": "00000000-0000-4000-8000-000000000000", "token": "ab" * 201,
     "environment": "sandbox"},
    {"grant_id": "00000000-0000-4000-8000-000000000000", "token": 12345,
     "environment": "sandbox"},
    {"grant_id": "00000000-0000-4000-8000-000000000000", "token": TOKEN,
     "environment": "sandbox", "user_id": OTHER},
])
def test_a_malformed_body_is_a_422_in_the_error_contract(client, db, body):
    calls = len(db.store.calls)
    r = client.put(URL, headers=_bearer(), json=body)
    assert r.status_code == 422, r.text
    assert "error_code" in r.json()
    assert not any(c[0] == "widget_push_register" for c in db.store.calls[calls:])


@pytest.mark.parametrize("n", [32, 64, 160, 400])
def test_every_legal_token_length_is_accepted(client, db, n):
    g = _grant(db)
    r = client.put(URL, headers=_bearer(), json=_body(g.grant_id, token="a" * n))
    assert r.status_code == 200 and r.json()["stored"] is True


# ── auth: a real session on a live account, nothing else ────────────────────────────────────

def test_no_credential_is_AUTH_REQUIRED(client, db):
    r = client.put(URL, json=_body("00000000-0000-4000-8000-000000000000"))
    assert r.status_code == 401 and r.json()["error_code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("headers", [
    lambda g: {WIDGET_TOKEN_HEADER: create_widget_token(UID)},
    lambda g: {HOLDINGS_GRANT_HEADER: g.grant_token},
    lambda g: {"Authorization": f"Bearer {create_widget_token(UID)}"},
    lambda g: {"Authorization": f"Bearer {g.grant_token}"},
])
def test_a_widget_or_grant_credential_never_registers(client, db, headers):
    g = _grant(db)
    r = client.put(URL, headers=headers(g), json=_body(g.grant_id))
    assert r.status_code == 401
    assert db.store.grants[g.grant_id]["push_token"] is None


def test_an_evicted_session_cannot_bind_a_token(client, db):
    g = _grant(db)
    db.store.users[UID]["password_changed_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    r = client.put(URL, headers=_bearer(issued_ago=timedelta(minutes=30)), json=_body(g.grant_id))
    assert r.status_code == 401 and r.json()["error_code"] == "AUTH_SESSION_EXPIRED"


def test_a_deleted_account_cannot_bind_a_token(client, db):
    g = _grant(db)
    db.store.delete_user(UID)
    r = client.put(URL, headers=_bearer(), json=_body(g.grant_id))
    assert r.status_code == 401 and r.json()["error_code"] == "AUTH_ACCOUNT_NOT_FOUND"


def test_the_route_is_rate_limited_per_account(client, db):
    g = _grant(db)
    codes = [client.put(URL, headers=_bearer(), json=_body(g.grant_id)).status_code
             for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429
    limiter = widget.WidgetPushTokenRateLimit.dependency
    assert limiter.bucket == "widget_push_token"


def test_the_route_sits_on_the_strict_router_only():
    on = {name: [r.path for r in getattr(widget, name).routes]
          for name in ("router", "widget_client_router", "widget_holdings_router")}
    assert "/push-token" in on["router"]
    assert "/push-token" not in on["widget_client_router"] + on["widget_holdings_router"]
