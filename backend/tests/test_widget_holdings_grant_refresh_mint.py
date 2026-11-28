"""The Holdings widget grant is minted ONLY inside `POST /auth/refresh` (migration 216).

Owner 2026-11-27: "Yes, issue it in refresh (Recommended)". The grant reads the account's holdings
for 30 days, so it must never be obtainable with a stolen ACCESS token (≤ 60 min): minting needs
the session's single-use REFRESH token, proven by a 214 rotation answering `rotated`. A thief who
rotates the victim's refresh token to mint makes the victim's next refresh REUSE — which revokes
the session, and 216's trigger kills the thief's grant in the same transaction.

And the mint must never cost the refresh anything: no failure, no delay past its 3 s bound, no
change of status. The refresh is the app's lifeline; the grant is a convenience.

Hermetic: the route coroutine is called directly over `_refresh_session_fake.SupabaseFake` (the
214 + 216 model) — the same idiom as `test_auth_session_revocation.py`.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import re
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from jose import jwt as jose_jwt

from _refresh_session_fake import SessionStoreFake, SupabaseFake
from app.api.v1.endpoints import auth as auth_ep
from app.config import settings
from app.core.security import create_access_token, create_refresh_token, decode_token, rate_limiter
from app.schemas.auth import RefreshTokenRequest, TokenResponse
from app.services import auth_session_service as sessions
from app.services import widget_holdings_grant_service as grants

UID = "11111111-2222-4333-8444-555555555555"
EMAIL = "owner@example.com"
_BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _state(monkeypatch):
    rate_limiter.clear()
    monkeypatch.setattr(settings, "WIDGET_HOLDINGS_GRANTS_ENABLED", True)
    monkeypatch.setattr(grants, "_last_schema_missing_error", 0.0)
    monkeypatch.setattr(sessions, "_last_schema_missing_error", 0.0)
    yield
    rate_limiter.clear()


class _Req:
    def __init__(self, ip: str = "203.0.113.77") -> None:
        self.client = SimpleNamespace(host=ip)
        self.headers = {}


@pytest.fixture
def db() -> SupabaseFake:
    return SupabaseFake(users={UID: {"password_changed_at": None, "sessions_revoked_at": None}})


def _start(db) -> sessions.IssuedSession:
    issued = sessions.start_session(db, UID, EMAIL)
    assert issued.sid
    return issued


async def _refresh(db, token: str, *, flag: bool = True) -> TokenResponse:
    return await auth_ep.refresh_token(
        RefreshTokenRequest(refresh_token=token, widget_holdings_grant=flag), _Req(), db,
    )


def _advance(db, seconds: float) -> None:
    base = db.store.now()
    db.store._clock = lambda: base + timedelta(seconds=seconds)


def _mint_calls(db) -> list:
    return [c for c in db.store.calls if c[0] == "mint_widget_holdings_grant"]


# ── the happy path ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_flag_plus_a_real_rotation_mints_a_grant_bound_to_that_session(db):
    s = _start(db)
    answer = await _refresh(db, s.refresh_token)
    g = answer.widget_holdings_grant
    assert g is not None
    assert grants.is_well_formed(g.grant_token)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", g.expires_at)
    access = decode_token(answer.access_token)
    assert g.session_id == access["sid"] == s.sid
    row = db.store.grant_by_hash(grants.key_hash(g.grant_token))
    assert row is not None and row["grant_id"] == g.grant_id
    assert row["minted_iat"] == access["iat"], "eviction compares the NEW access token's iat"
    assert grants.check(db, grants.key_hash(g.grant_token)).status == "live"
    # The refresh itself is untouched.
    assert decode_token(answer.refresh_token)["type"] == "refresh"


@pytest.mark.asyncio
async def test_no_flag_no_grant_and_no_mint_call(db):
    s = _start(db)
    answer = await _refresh(db, s.refresh_token, flag=False)
    assert answer.widget_holdings_grant is None
    assert _mint_calls(db) == []


def test_the_flag_defaults_to_false_and_old_bodies_still_parse():
    assert RefreshTokenRequest(refresh_token="x").widget_holdings_grant is False
    # 1.02-1.04 send only refresh_token; a future field is ignored, never a 422.
    assert RefreshTokenRequest.model_validate({"refresh_token": "x", "later": 1})


# ── never from a bare access token ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_an_access_token_alone_can_never_mint(db):
    """The thief's position: a live ACCESS token, no refresh token. The refresh route refuses
    the wrong token type before any session or grant call."""
    s = _start(db)
    calls = len(db.store.calls)
    with pytest.raises(HTTPException) as e:
        await _refresh(db, s.access_token)
    assert e.value.detail["error_code"] == "AUTH_TOKEN_INVALID"
    assert len(db.store.calls) == calls
    assert not db.store.grants


_SERVICE_MODULE = "widget_holdings_grant_service"
_MINT_RPC = "mint_widget_holdings_grant"


def _mint_hits(source: str) -> list[str]:
    """Every way one module can reach the grant mint, ALIAS-RESOLVED, as the name of the enclosing
    function (`<module>` at top level): the service module bound under ANY name (`import … as X`,
    `from app.services import … as X`, a relative import, the full dotted path) and then `X.mint`
    / `getattr(X, "mint")`; `from …widget_holdings_grant_service import mint` (or `*`); and the
    RPC's own name as a string (a direct `.rpc("mint_widget_holdings_grant", …)`)."""
    tree = ast.parse(source)
    parent: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parent[child] = node

    def owner(node: ast.AST) -> str:
        while node in parent:
            node = parent[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return "<module>"

    aliases: set[str] = set()
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[-1] == _SERVICE_MODULE and a.asname:
                    aliases.add(a.asname)
        elif isinstance(node, ast.ImportFrom):
            from_service = (node.module or "").split(".")[-1] == _SERVICE_MODULE
            for a in node.names:
                if from_service and a.name in ("mint", "*"):
                    hits.append(owner(node))
                elif a.name == _SERVICE_MODULE:
                    aliases.add(a.asname or a.name)

    def is_service(expr: ast.AST) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id in aliases
        return isinstance(expr, ast.Attribute) and expr.attr == _SERVICE_MODULE

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "mint" and is_service(node.value):
            hits.append(owner(node))
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
              and node.func.id == "getattr" and len(node.args) >= 2
              and is_service(node.args[0]) and isinstance(node.args[1], ast.Constant)
              and node.args[1].value == "mint"):
            hits.append(owner(node))
        elif isinstance(node, ast.Constant) and node.value == _MINT_RPC:
            hits.append(owner(node))
    return hits


def test_no_route_but_the_refresh_mints_a_grant():
    """Structural: the grant mint is reached from exactly one function in app/ (outside the
    service), and that function only from `refresh_token`. A standalone mint route (the rejected
    design) would let a stolen access token become a 30-day credential."""
    callers = []
    for path in sorted((_BACKEND / "app").rglob("*.py")):
        if path.name == f"{_SERVICE_MODULE}.py":
            continue
        for fn in _mint_hits(path.read_text(encoding="utf-8")):
            callers.append((path.relative_to(_BACKEND).as_posix(), fn))
    assert callers == [("app/api/v1/endpoints/auth.py", "_mint_widget_holdings_grant")], callers

    src = (_BACKEND / "app/api/v1/endpoints/auth.py").read_text()
    tree = ast.parse(src)
    users = {
        fn.name for fn in ast.walk(tree)
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(isinstance(n, ast.Name) and n.id == "_mint_widget_holdings_grant"
                for n in ast.walk(fn) if fn.name != "_mint_widget_holdings_grant")
    }
    assert users == {"refresh_token"}, users


_MINT_SCAN_MUTATIONS = [
    # (label, source, expected hits)
    ("from-import of mint", "from app.services.widget_holdings_grant_service import mint\n"
     "def grab(sb):\n    return mint(sb, 'u', 's', 1)\n", ["<module>"]),
    ("star import", "from app.services.widget_holdings_grant_service import *\n", ["<module>"]),
    ("a relative from-import of mint", "from ...services.widget_holdings_grant_service import mint\n",
     ["<module>"]),
    ("a direct RPC call", "def grab(sb):\n    return sb.rpc('mint_widget_holdings_grant', "
     "{'p_user_id': 'u'}).execute()\n", ["grab"]),
    ("another alias", "from app.services import widget_holdings_grant_service as wh\n"
     "async def issue(sb):\n    return wh.mint(sb, 'u', 's', 1)\n", ["issue"]),
    ("import … as", "import app.services.widget_holdings_grant_service as svc\n"
     "def issue(sb):\n    return svc.mint(sb, 'u', 's', 1)\n", ["issue"]),
    ("the full dotted path", "import app.services.widget_holdings_grant_service\n"
     "def issue(sb):\n    return app.services.widget_holdings_grant_service.mint(sb)\n", ["issue"]),
    ("no alias at all", "from app.services import widget_holdings_grant_service\n"
     "def issue(sb):\n    return widget_holdings_grant_service.mint(sb)\n", ["issue"]),
    ("getattr", "from app.services import widget_holdings_grant_service as wh\n"
     "def issue(sb):\n    return getattr(wh, 'mint')(sb)\n", ["issue"]),
    ("CONTROL: the check and an unrelated mint", "from app.services import "
     "widget_holdings_grant_service as wh\nfrom app.core import tokens\n"
     "def ok(sb):\n    wh.check(sb, 'h')\n    return tokens.mint('x')\n", []),
]


@pytest.mark.parametrize("label,source,expected", _MINT_SCAN_MUTATIONS,
                         ids=[m[0] for m in _MINT_SCAN_MUTATIONS])
def test_the_mint_scan_resolves_every_alias(label, source, expected):
    """Mutation rows (testing.md §3): each plausible second mint path turns the scan red."""
    assert _mint_hits(source) == expected, label


def test_no_holdings_grant_mint_route_exists():
    from app.main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/v1/widget/holdings-grant" not in paths
    client = TestClient(app)
    bearer = {"Authorization": f"Bearer {create_access_token({'sub': UID})}"}
    assert client.post("/api/v1/widget/holdings-grant", headers=bearer).status_code in (404, 405)


# ── statuses that never mint ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_grace_reissue_never_mints(db):
    s = _start(db)
    first = await _refresh(db, s.refresh_token)
    assert first.widget_holdings_grant is not None
    again = await _refresh(db, s.refresh_token)          # same token, inside the grace window
    assert again.widget_holdings_grant is None
    assert len(_mint_calls(db)) == 1


@pytest.mark.asyncio
async def test_a_degraded_stateless_refresh_never_mints(db):
    """Migration 214 missing (a legacy token degrades to stateless): no rotation proof, no grant."""
    db.store.missing = True
    legacy = create_refresh_token({"sub": UID, "email": EMAIL})
    answer = await _refresh(db, legacy)
    assert answer.widget_holdings_grant is None
    assert _mint_calls(db) == []


@pytest.mark.asyncio
async def test_the_switch_off_answers_the_refresh_without_a_grant(db, monkeypatch):
    monkeypatch.setattr(settings, "WIDGET_HOLDINGS_GRANTS_ENABLED", False)
    s = _start(db)
    answer = await _refresh(db, s.refresh_token)
    assert answer.access_token and answer.widget_holdings_grant is None
    assert _mint_calls(db) == []


# ── the mint can never cost the refresh ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_store_error_answers_the_refresh_without_a_grant(db, caplog):
    caplog.set_level(logging.ERROR)
    db.store.fail = {"mint_widget_holdings_grant"}
    s = _start(db)
    answer = await _refresh(db, s.refresh_token)
    assert answer.access_token and answer.widget_holdings_grant is None
    assert any("widget grant: mint failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_a_missing_216_answers_the_refresh_and_throttles_its_error(db, caplog):
    caplog.set_level(logging.WARNING)
    db.store.grants_missing = {"*"}
    issued = _start(db)
    token = issued.refresh_token
    for _ in range(3):
        answer = await _refresh(db, token)
        assert answer.widget_holdings_grant is None
        token = answer.refresh_token
    errors = [r for r in caplog.records
              if r.levelno == logging.ERROR and "216" in r.getMessage()]
    assert len(errors) == 1, [r.getMessage() for r in errors]


@pytest.mark.asyncio
async def test_a_mint_slower_than_its_bound_never_delays_the_refresh(db, monkeypatch):
    monkeypatch.setattr(auth_ep, "WIDGET_GRANT_MINT_TIMEOUT_SECONDS", 0.1)
    real_mint = grants.mint

    def _slow(*a, **k):
        time.sleep(0.6)
        return real_mint(*a, **k)

    monkeypatch.setattr(grants, "mint", _slow)
    s = _start(db)
    t0 = time.monotonic()
    answer = await _refresh(db, s.refresh_token)
    assert time.monotonic() - t0 < 0.5
    assert answer.access_token and answer.widget_holdings_grant is None
    await asyncio.sleep(0.7)   # let the abandoned thread finish: its late row has no holder
    assert len(db.store.grants) == 1, "the late row committed, with nobody holding its key"


def test_the_bound_is_three_seconds():
    assert auth_ep.WIDGET_GRANT_MINT_TIMEOUT_SECONDS == 3.0


@pytest.mark.asyncio
async def test_the_per_user_mint_limiter_skips_the_grant_not_the_refresh(db):
    s = _start(db)
    token = s.refresh_token
    got = []
    for _ in range(auth_ep.WIDGET_GRANT_MINT_PER_HOUR + 2):
        answer = await _refresh(db, token)
        got.append(answer.widget_holdings_grant is not None)
        token = answer.refresh_token
    assert got == [True] * auth_ep.WIDGET_GRANT_MINT_PER_HOUR + [False, False]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["evicted", "session_revoked", "account_missing",
                                    "session_unknown", "surprise"])
async def test_a_refused_mint_answers_the_refresh_without_a_grant(db, monkeypatch, status):
    monkeypatch.setattr(grants, "mint", lambda *a, **k: grants.MintOutcome(status=status))
    s = _start(db)
    answer = await _refresh(db, s.refresh_token)
    assert answer.access_token and answer.widget_holdings_grant is None


@pytest.mark.asyncio
async def test_an_unexpected_mint_exception_never_fails_the_refresh(db, monkeypatch):
    def _boom(*_a, **_k):
        raise KeyError("surprise")

    monkeypatch.setattr(grants, "mint", _boom)
    s = _start(db)
    answer = await _refresh(db, s.refresh_token)
    assert answer.access_token and answer.widget_holdings_grant is None


# ── the security property: a thief's mint dies with the victim's next refresh ───────────────

@pytest.mark.asyncio
async def test_a_thief_who_rotates_to_mint_loses_the_grant_on_the_victims_next_refresh(db):
    victim = _start(db)
    stolen = victim.refresh_token
    thief = await _refresh(db, stolen)                   # the thief rotates first …
    g = thief.widget_holdings_grant
    assert g is not None
    assert grants.check(db, grants.key_hash(g.grant_token)).status == "live"
    _advance(db, 600)                                    # … the victim refreshes later with R
    with pytest.raises(HTTPException) as e:
        await _refresh(db, stolen)
    assert e.value.detail["error_code"] == "AUTH_SESSION_EXPIRED"     # reuse
    check = grants.check(db, grants.key_hash(g.grant_token))
    assert check.status == "revoked"
    assert db.store.grant_by_hash(grants.key_hash(g.grant_token))["revoke_reason"] == "reuse"


@pytest.mark.asyncio
async def test_a_grant_minted_after_the_session_was_revoked_is_refused(db, monkeypatch):
    """The rotation committed, then a logout landed before the mint (separate transactions):
    the mint re-checks the family under the 214 lock and refuses."""
    s = _start(db)
    real_mint = grants.mint

    def _logout_first(dbx, user_id, sid, iat):
        sessions.revoke_session(dbx, user_id, sid)
        return real_mint(dbx, user_id, sid, iat)

    monkeypatch.setattr(grants, "mint", _logout_first)
    answer = await _refresh(db, s.refresh_token)
    assert answer.widget_holdings_grant is None
    assert not db.store.grants


# ── the key never leaks ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_the_key_never_reaches_a_log_line_or_a_repr(db, caplog):
    caplog.set_level(logging.DEBUG)
    s = _start(db)
    answer = await _refresh(db, s.refresh_token)
    key = answer.widget_holdings_grant.grant_token
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert key not in text and grants.key_hash(key) not in text
    assert key not in repr(answer) and key not in repr(answer.widget_holdings_grant)


def test_the_answer_serialises_the_grant_with_the_ios_field_names():
    out = TokenResponse(access_token="a", refresh_token="r", user_id=UID)
    assert out.model_dump()["widget_holdings_grant"] is None
    from app.schemas.auth import WidgetHoldingsGrantOut

    filled = out.model_copy(update={"widget_holdings_grant": WidgetHoldingsGrantOut(
        grant_token="cwh1_" + "A" * 43, grant_id="g", expires_at="2026-12-27T00:00:00Z",
        session_id="s",
    )})
    body = filled.model_dump(mode="json")["widget_holdings_grant"]
    assert set(body) == {"grant_token", "grant_id", "expires_at", "session_id"}


def test_the_minted_iat_is_the_new_access_tokens():
    access = create_access_token({"sub": UID})
    assert auth_ep._access_token_iat(access) == jose_jwt.get_unverified_claims(access)["iat"]
    assert auth_ep._access_token_iat("garbage") is None
