"""The Holdings widget grant as a CREDENTIAL (migration 216): the key dependency and its router.

`get_holdings_grant_caller` is the second dependency in the app that accepts a non-session
credential (the market widget token's `get_widget_caller` is the first). It reads ONE header,
`X-Caydex-Holdings-Token`, and lets the grant row alone decide the user. Every branch below maps
to an exact code, because the extension's "key refused" allow-list is exactly
{AUTH_TOKEN_INVALID, AUTH_SESSION_EXPIRED, AUTH_ACCOUNT_NOT_FOUND} — anything else keeps the tile:

    no header                         → 401 AUTH_REQUIRED (no database read)
    malformed                         → 401 AUTH_TOKEN_INVALID (no database read)
    garbage flood from one address    → 429 before any database read
    unknown                           → 401 AUTH_TOKEN_INVALID
    revoked / expired / evicted       → 401 AUTH_SESSION_EXPIRED
    account missing                   → 401 AUTH_ACCOUNT_NOT_FOUND
    store error / schema missing / ?  → 503 AUTH_UNAVAILABLE

Three things this dependency must NEVER do, pinned by comment-stripped scans with in-file
mutations (testing.md §3): read `WIDGET_HOLDINGS_GRANTS_ENABLED` (the switch sits on the data
route only, so a device can always revoke), call `_decode_access_token`, or call the fail-open
`_reject_if_password_changed_since_issue` (eviction is the SQL predicate's job).

Hermetic: TestClient over the real app with `get_supabase` overridden by the 214 + 216 fake;
the holdings build itself is stubbed at the endpoint module's bindings.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path
from typing import Any, List, Optional

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from _refresh_session_fake import SupabaseFake
from app.api.v1.endpoints import widget
from app.config import settings
from app.core.security import create_access_token, create_widget_token, rate_limiter
from app.database import get_supabase
from app.dependencies import (
    HOLDINGS_GRANT_HEADER,
    HOLDINGS_GRANT_IP_LIMIT,
    WIDGET_TOKEN_HEADER,
    get_holdings_grant_caller,
    require_holdings_grants_enabled,
)
from app.main import app
from app.schemas.widget import (
    WidgetCauseResponse,
    WidgetMoveContextResponse,
    WidgetMoverPayload,
    WidgetMoverResponse,
)
from app.services import auth_session_service as sessions
from app.services import widget_holdings_grant_service as grants
from app.services.active_group_service import ActiveGroup

UID = "11111111-2222-4333-8444-555555555555"
OTHER = "99999999-8888-4777-8666-555555555555"
EMAIL = "owner@example.com"
MOVER = "/api/v1/widget/holdings-mover"
REVOKE = "/api/v1/widget/holdings-grant/revoke"
_DEPS_PY = Path(__file__).resolve().parents[1] / "app" / "dependencies.py"


# ── fixtures ────────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _state(monkeypatch):
    rate_limiter.clear()
    widget._head_memo.clear()
    monkeypatch.setattr(settings, "WIDGET_HOLDINGS_GRANTS_ENABLED", True)
    monkeypatch.setattr(grants, "_last_schema_missing_error", 0.0)
    yield
    rate_limiter.clear()
    widget._head_memo.clear()


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


class _Movers:
    def __init__(self):
        self.calls: List[dict] = []

    async def get_portfolio_mover(self, user_id, tickers, **kw):
        self.calls.append({"user_id": user_id, "tickers": list(tickers), **kw})
        return WidgetMoverPayload(
            mode="portfolio", as_of="2026-11-27T21:00:00Z", market_session="closed",
            session_date="2026-11-27", session_label="Fri close", scope_label="Your holdings",
            holdings_count=len(tickers),
            headline_mover=WidgetMoverResponse(
                ticker=tickers[0],
                cause=WidgetCauseResponse(kind="none", detail=""),
                context=WidgetMoveContextResponse(change_percent=3.1),
            ),
        )


@pytest.fixture
def movers(monkeypatch) -> _Movers:
    fake = _Movers()

    async def _group(user_id):
        return ActiveGroup(id="g", name="Core", tickers=["NVDA", "AAPL"] if user_id == UID
                           else ["TSLA"])

    monkeypatch.setattr(widget, "get_active_group", _group)
    monkeypatch.setattr(widget, "get_widget_movers_service", lambda: fake)
    return fake


def _grant(db, uid: str = UID) -> grants.MintedGrant:
    issued = sessions.start_session(db, uid, EMAIL)
    outcome = grants.mint(db, uid, issued.sid, int(db.store.now().timestamp()))
    assert outcome.status == "minted"
    return outcome.grant


def _h(token: str) -> dict:
    return {HOLDINGS_GRANT_HEADER: token}


def _checks(db) -> int:
    return sum(1 for c in db.store.calls if c[0] == "check_widget_holdings_grant")


# ── 1. the dependency, branch by branch ─────────────────────────────────────────────────────

class _Req:
    def __init__(self, ip="203.0.113.5"):
        self.headers = {"x-forwarded-for": f"198.51.100.1, {ip}"}
        self.url = type("U", (), {"path": MOVER})()


async def _resolve(db, token: Optional[str], ip="203.0.113.5"):
    return await get_holdings_grant_caller(request=_Req(ip), x_holdings_token=token, supabase=db)


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "   "])
async def test_no_key_is_AUTH_REQUIRED_with_no_read(db, token):
    with pytest.raises(HTTPException) as e:
        await _resolve(db, token)
    assert e.value.status_code == 401 and e.value.detail["error_code"] == "AUTH_REQUIRED"
    assert db.store.calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [
    "nonsense", "cwh1_short", "cwh1_" + "A" * 42 + "+", "Bearer cwh1_" + "A" * 43,
    "cwh1_" + "A" * 300, create_access_token({"sub": UID}), create_widget_token(UID),
])
async def test_a_malformed_key_is_TOKEN_INVALID_with_no_read(db, token):
    with pytest.raises(HTTPException) as e:
        await _resolve(db, token)
    assert e.value.status_code == 401 and e.value.detail["error_code"] == "AUTH_TOKEN_INVALID"
    assert db.store.calls == []


@pytest.mark.asyncio
async def test_surrounding_whitespace_is_tolerated(db):
    g = _grant(db)
    caller = await _resolve(db, f"  {g.grant_token}\t")
    assert caller.user_id == UID and caller.grant_id == g.grant_id


@pytest.mark.asyncio
async def test_an_unknown_well_formed_key_is_TOKEN_INVALID(db):
    with pytest.raises(HTTPException) as e:
        await _resolve(db, grants.new_token())
    assert e.value.detail["error_code"] == "AUTH_TOKEN_INVALID"


@pytest.mark.asyncio
async def test_a_revoked_key_is_SESSION_EXPIRED(db):
    g = _grant(db)
    grants.revoke(db, g.grant_id, UID, "app_revoked")
    with pytest.raises(HTTPException) as e:
        await _resolve(db, g.grant_token)
    assert e.value.detail["error_code"] == "AUTH_SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_an_evicted_key_is_SESSION_EXPIRED(db):
    from datetime import timedelta

    g = _grant(db)
    db.store.users[UID]["sessions_revoked_at"] = (db.store.now() + timedelta(seconds=2)).isoformat()
    with pytest.raises(HTTPException) as e:
        await _resolve(db, g.grant_token)
    assert e.value.detail["error_code"] == "AUTH_SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_an_expired_key_is_SESSION_EXPIRED(db):
    from datetime import timedelta

    g = _grant(db)
    base = db.store.now()
    db.store._clock = lambda: base + timedelta(days=31)
    with pytest.raises(HTTPException) as e:
        await _resolve(db, g.grant_token)
    assert e.value.detail["error_code"] == "AUTH_SESSION_EXPIRED"


@pytest.mark.asyncio
async def test_a_missing_account_is_ACCOUNT_NOT_FOUND(db, monkeypatch):
    monkeypatch.setattr(grants, "check", lambda *_a, **_k: grants.GrantCheck("account_missing"))
    with pytest.raises(HTTPException) as e:
        await _resolve(db, grants.new_token())
    assert e.value.detail["error_code"] == "AUTH_ACCOUNT_NOT_FOUND"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["store", "schema", "status", "live-without-ids"])
async def test_every_store_failure_is_503_never_allowed(db, monkeypatch, failure):
    if failure == "store":
        db.store.fail = {"check_widget_holdings_grant"}
    elif failure == "schema":
        db.store.grants_missing = {"*"}
    elif failure == "status":
        monkeypatch.setattr(grants, "check", lambda *_a, **_k: grants.GrantCheck("weird"))
    else:
        monkeypatch.setattr(grants, "check", lambda *_a, **_k: grants.GrantCheck("live"))
    with pytest.raises(HTTPException) as e:
        await _resolve(db, grants.new_token())
    assert e.value.status_code == 503 and e.value.detail["error_code"] == "AUTH_UNAVAILABLE"


@pytest.mark.asyncio
async def test_the_per_ip_bound_refuses_before_any_read(db):
    for _ in range(HOLDINGS_GRANT_IP_LIMIT):
        with pytest.raises(HTTPException):
            await _resolve(db, grants.new_token(), ip="203.0.113.99")
    before = _checks(db)
    with pytest.raises(HTTPException) as e:
        await _resolve(db, grants.new_token(), ip="203.0.113.99")
    assert e.value.status_code == 429
    assert _checks(db) == before, "the 429 must come before the database read"
    # Another address is untouched.
    with pytest.raises(HTTPException) as e2:
        await _resolve(db, grants.new_token(), ip="203.0.113.100")
    assert e2.value.status_code == 401


def test_the_ip_bound_was_raised_for_carrier_nat():
    assert HOLDINGS_GRANT_IP_LIMIT == 300


def test_the_dependency_reads_no_bearer():
    params = inspect.signature(get_holdings_grant_caller).parameters
    assert "credentials" not in params, "a bearer parameter would let a session reach this route"
    assert set(params) == {"request", "x_holdings_token", "supabase"}


# ── 2. through the real app ────────────────────────────────────────────────────────────────

def test_the_mover_serves_the_grants_account_with_no_store(client, db, movers):
    g = _grant(db)
    r = client.get(MOVER, headers=_h(g.grant_token))
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "portfolio" and r.json()["holdings_count"] == 2
    assert r.headers["cache-control"] == "no-store"
    assert movers.calls[-1]["user_id"] == UID
    assert movers.calls[-1]["for_extension"] is True, "the extension's budget path"


def test_a_session_bearer_alone_is_AUTH_REQUIRED(client, db, movers):
    r = client.get(MOVER, headers={"Authorization": f"Bearer {create_access_token({'sub': UID})}"})
    assert r.status_code == 401 and r.json()["error_code"] == "AUTH_REQUIRED"
    assert movers.calls == []


def test_the_grant_row_decides_the_user_not_the_bearer(client, db, movers):
    g = _grant(db, UID)
    r = client.get(MOVER, headers={
        **_h(g.grant_token),
        "Authorization": f"Bearer {create_access_token({'sub': OTHER})}",
    })
    assert r.status_code == 200
    assert movers.calls[-1]["user_id"] == UID


def test_a_garbage_bearer_beside_a_good_key_changes_nothing(client, db, movers):
    g = _grant(db)
    r = client.get(MOVER, headers={**_h(g.grant_token), "Authorization": "Bearer garbage"})
    assert r.status_code == 200 and movers.calls[-1]["user_id"] == UID


def test_the_market_token_in_the_holdings_header_is_refused_with_no_read(client, db, movers):
    r = client.get(MOVER, headers=_h(create_widget_token(UID)))
    assert r.status_code == 401 and r.json()["error_code"] == "AUTH_TOKEN_INVALID"
    assert _checks(db) == 0 and movers.calls == []


def test_the_market_token_in_its_own_header_is_refused_here(client, db, movers):
    r = client.get(MOVER, headers={WIDGET_TOKEN_HEADER: create_widget_token(UID)})
    assert r.status_code == 401 and r.json()["error_code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize("path", [
    "/api/v1/widget/portfolio-mover", "/api/v1/widget/token", "/api/v1/users/me/credits",
    "/api/v1/widget/market-mover",
])
def test_a_grant_key_opens_no_other_route(client, db, path):
    g = _grant(db)
    for headers in ({"Authorization": f"Bearer {g.grant_token}"}, _h(g.grant_token),
                    {WIDGET_TOKEN_HEADER: g.grant_token}):
        r = client.get(path, headers=headers)
        assert r.status_code == 401, f"{path} accepted a holdings grant via {list(headers)}"


def test_a_grant_key_is_never_a_session_credential():
    from jose import JWTError

    from app.dependencies import _decode_access_token

    with pytest.raises(JWTError):
        _decode_access_token(grants.new_token())


def test_the_switch_off_503s_the_mover_but_never_the_revoke(client, db, movers, monkeypatch):
    g = _grant(db)
    monkeypatch.setattr(settings, "WIDGET_HOLDINGS_GRANTS_ENABLED", False)
    r = client.get(MOVER, headers=_h(g.grant_token))
    assert r.status_code == 503 and r.json()["error_code"] == "AUTH_UNAVAILABLE"
    assert movers.calls == []
    r = client.post(REVOKE, headers=_h(g.grant_token), json={"reason": "app_revoked"})
    assert r.status_code == 200 and r.json() == {"revoked": True}
    assert grants.check(db, grants.key_hash(g.grant_token)).status == "revoked"


def test_the_switch_off_still_answers_401_to_no_key(client, db, monkeypatch):
    """The licence gate's contract comes first: an anonymous caller is 401, never 503."""
    monkeypatch.setattr(settings, "WIDGET_HOLDINGS_GRANTS_ENABLED", False)
    r = client.get(MOVER)
    assert r.status_code == 401 and r.json()["error_code"] == "AUTH_REQUIRED"


@pytest.mark.asyncio
async def test_the_switch_dependency_itself(monkeypatch):
    monkeypatch.setattr(settings, "WIDGET_HOLDINGS_GRANTS_ENABLED", False)
    with pytest.raises(HTTPException) as e:
        await require_holdings_grants_enabled()
    assert e.value.status_code == 503
    monkeypatch.setattr(settings, "WIDGET_HOLDINGS_GRANTS_ENABLED", True)
    assert await require_holdings_grants_enabled() is None


def test_the_per_grant_limiter_answers_429_after_30(client, db, movers):
    g = _grant(db)
    codes = [client.get(MOVER, headers=_h(g.grant_token)).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429
    other = _grant(db, OTHER)
    assert client.get(MOVER, headers=_h(other.grant_token)).status_code == 200


@pytest.mark.parametrize("version,expected", [("1.05", True), ("1.04", True), (None, False),
                                              ("1.0 (10)", False)])
def test_x_app_version_gates_the_web_only_cause(client, db, movers, version, expected):
    g = _grant(db)
    headers = _h(g.grant_token)
    if version:
        headers["X-App-Version"] = version
    assert client.get(MOVER, headers=headers).status_code == 200
    assert movers.calls[-1]["links_web_only"] is expected


def test_the_app_route_never_passes_for_extension(client, db, movers):
    db.store.users[UID]["email"] = EMAIL
    r = client.get("/api/v1/widget/portfolio-mover",
                   headers={"Authorization": f"Bearer {create_access_token({'sub': UID})}"})
    assert r.status_code == 200, r.text
    assert "for_extension" not in movers.calls[-1], "the app's build must stay byte-identical"


def test_a_degraded_build_is_served_as_degraded_not_an_error(client, db, monkeypatch):
    class _Boom:
        async def get_portfolio_mover(self, *_a, **_k):
            raise RuntimeError("upstream down")

    async def _group(_uid):
        return ActiveGroup(id="g", name=None, tickers=["NVDA"])

    monkeypatch.setattr(widget, "get_active_group", _group)
    monkeypatch.setattr(widget, "get_widget_movers_service", lambda: _Boom())
    g = _grant(db)
    db.store.grants[g.grant_id]["last_head_ticker"] = "NVDA"
    r = client.get(MOVER, headers=_h(g.grant_token))
    assert r.status_code == 200 and r.json()["holdings_count"] is None
    # A degraded build says nothing about the head: the last noted one stays (push targeting).
    assert not [c for c in db.store.calls if c[0] == "note_widget_holdings_head"]
    assert db.store.grants[g.grant_id]["last_head_ticker"] == "NVDA"


# ── 3. note_head: best-effort, memoised, bounded ────────────────────────────────────────────

def test_the_head_ticker_is_noted_once_per_change(client, db, movers):
    g = _grant(db)
    for _ in range(3):
        client.get(MOVER, headers=_h(g.grant_token))
    notes = [c for c in db.store.calls if c[0] == "note_widget_holdings_head"]
    assert len(notes) == 1 and notes[0][1]["p_ticker"] == "NVDA"
    assert db.store.grants[g.grant_id]["last_head_ticker"] == "NVDA"


def test_a_note_head_failure_never_fails_the_payload_and_is_retried(client, db, movers):
    g = _grant(db)
    db.store.fail = {"note_widget_holdings_head"}
    assert client.get(MOVER, headers=_h(g.grant_token)).status_code == 200
    db.store.fail = set()
    assert client.get(MOVER, headers=_h(g.grant_token)).status_code == 200
    assert db.store.grants[g.grant_id]["last_head_ticker"] == "NVDA"


def test_the_head_memo_is_bounded_lru():
    widget._head_memo.clear()
    for i in range(widget._HEAD_MEMO_MAX + 50):
        assert widget._remember_head(f"g{i}", "NVDA") is True
    assert len(widget._head_memo) == widget._HEAD_MEMO_MAX == 4096
    assert "g0" not in widget._head_memo, "least-recently-built first out"
    assert widget._remember_head(f"g{widget._HEAD_MEMO_MAX + 49}", "NVDA") is False
    assert widget._remember_head(f"g{widget._HEAD_MEMO_MAX + 49}", "AAPL") is True


# ── 4. revoke-by-key ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("body,reason", [
    ({"reason": "app_revoked"}, "app_revoked"),
    ({"reason": "widget_removed"}, "widget_removed"),
    (None, "app_revoked"),
])
def test_revoke_by_key(client, db, body, reason):
    g = _grant(db)
    r = client.post(REVOKE, headers=_h(g.grant_token), json=body)
    assert r.status_code == 200 and r.json() == {"revoked": True}
    assert r.headers["cache-control"] == "no-store"
    assert db.store.grants[g.grant_id]["revoke_reason"] == reason
    again = client.post(REVOKE, headers=_h(g.grant_token), json=body)
    assert again.status_code == 401 and again.json()["error_code"] == "AUTH_SESSION_EXPIRED", (
        "a dead key 401s at the dependency — the client reads that as 'already dead'"
    )


@pytest.mark.parametrize("body", [{"reason": "logout"}, {"reason": "cap"}, {"reason": ""},
                                  {"reason": "app_revoked", "user_id": OTHER}])
def test_revoke_refuses_a_server_reason_or_an_extra_field(client, db, body):
    g = _grant(db)
    r = client.post(REVOKE, headers=_h(g.grant_token), json=body)
    assert r.status_code == 422
    assert db.store.grants[g.grant_id]["revoked_at"] is None


def test_revoke_without_a_key_is_401(client, db):
    r = client.post(REVOKE, json={"reason": "app_revoked"})
    assert r.status_code == 401 and r.json()["error_code"] == "AUTH_REQUIRED"


def test_a_revoke_store_failure_is_a_retryable_503(client, db):
    g = _grant(db)
    db.store.fail = {"revoke_widget_holdings_grant"}
    r = client.post(REVOKE, headers=_h(g.grant_token), json={"reason": "app_revoked"})
    assert r.status_code == 503 and r.json()["error_code"] == "AUTH_UNAVAILABLE"


# ── 5. source scans: what the dependency must never do ──────────────────────────────────────

_FORBIDDEN_IN_DEPENDENCY = (
    "WIDGET_HOLDINGS_GRANTS_ENABLED",
    "_reject_if_password_changed_since_issue",
    "_decode_access_token",
    "HTTPAuthorizationCredentials",
    "security",
)


def _function_body(source: str, name: str) -> str:
    """The comment- and docstring-free source of one top-level function, via the AST."""
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            body = list(node.body)
            if body and isinstance(body[0], ast.Expr) and isinstance(
                    getattr(body[0], "value", None), ast.Constant):
                body = body[1:]                      # the docstring narrates the rule
            node.body = body or [ast.Pass()]
            return ast.unparse(node)                 # unparse drops comments
    raise AssertionError(f"{name} not found")


def _violations(source: str) -> list[str]:
    body = _function_body(source, "get_holdings_grant_caller")
    names = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", body))
    return [tok for tok in _FORBIDDEN_IN_DEPENDENCY if tok in names]


def test_the_dependency_never_reads_the_switch_or_the_session_helpers():
    assert _violations(_DEPS_PY.read_text()) == []


# Each row: a realistic regression, applied to an in-memory copy of dependencies.py. The scan
# must turn red on every one; the CONTROL (prose only) must stay green.
_MUTATIONS = [
    ("switch read in the dependency",
     "    route = _route_of(request)\n    presented_token",
     "    route = _route_of(request)\n    if not settings.WIDGET_HOLDINGS_GRANTS_ENABLED:\n"
     "        raise HTTPException(503)\n    presented_token", True),
    ("fail-open eviction helper",
     "    hashed = holdings_grants.key_hash(presented_token)",
     "    _reject_if_password_changed_since_issue(presented_token, {})\n"
     "    hashed = holdings_grants.key_hash(presented_token)", True),
    ("access-token decode",
     "    hashed = holdings_grants.key_hash(presented_token)",
     "    _decode_access_token(presented_token)\n"
     "    hashed = holdings_grants.key_hash(presented_token)", True),
    ("CONTROL: the names in a comment",
     "    route = _route_of(request)\n    presented_token",
     "    route = _route_of(request)\n    # WIDGET_HOLDINGS_GRANTS_ENABLED _decode_access_token\n"
     "    presented_token", False),
]


@pytest.mark.parametrize("label,anchor,replacement,must_fail", _MUTATIONS,
                         ids=[m[0] for m in _MUTATIONS])
def test_the_scan_catches_each_mutation(label, anchor, replacement, must_fail):
    src = _DEPS_PY.read_text()
    assert anchor in src, f"mutation anchor drifted: {label}"
    mutated = src.replace(anchor, replacement, 1)
    assert bool(_violations(mutated)) is must_fail, label


def test_the_holdings_router_has_no_switch_dependency():
    """A router-level switch would 503 the revoke too (spec D5)."""
    deps = [d.dependency.__name__ for d in widget.widget_holdings_router.dependencies]
    assert deps == ["get_holdings_grant_caller"]
    route = next(r for r in widget.widget_holdings_router.routes if r.path == "/holdings-mover")
    names = {d.call.__name__ for d in route.dependant.dependencies if hasattr(d.call, "__name__")}
    assert "require_holdings_grants_enabled" in names
    revoke = next(r for r in widget.widget_holdings_router.routes
                  if r.path == "/holdings-grant/revoke")
    rnames = {getattr(d.call, "__name__", "") for d in revoke.dependant.dependencies}
    assert "require_holdings_grants_enabled" not in rnames
