"""GET /users/me/credits/history — the handler that nothing was pinning.

WHY THIS FILE EXISTS
--------------------
The service behind this route is well covered: `test_credit_history_schema_parity.py`
proves `CreditHistoryService.list_for_user` scopes, pages and degrades correctly, and
`test_credit_history_labels.py` proves every ledger reason renders. The HANDLER —
`list_my_credit_history`, the dozen lines in `endpoints/users.py` that hand a caller's
identity and paging to that service and hand the answer back — had **zero** tests. Nothing
in `tests/` names it and nothing requests its path on purpose, so every one of these would
have left the whole suite green:

    * `user["id"]`                  → any other id     every caller reads ONE account's statement
    * `limit=limit, before=before`  → dropped          page 2 is page 1, forever
    * `except CreditHistoryUnavailable:` → `return CreditHistoryResponse()`
                                                       an outage renders as "No credit activity yet"
    * `asyncio.to_thread(...)`      → a direct call    one slow Postgres read parks every request

The statement is the screen a user opens when they already believe a refund did not land,
so the handler asking the right question — for the right user — and reporting the answer,
or the failure, in the shape iOS decodes IS the feature from the user's side.

Three layers, deliberately:
  1. The handler invoked DIRECTLY with a recording stub service (the suite's standard
     idiom, as in `test_users_endpoint_guards.py`): pins what it passes and what it returns.
  2. The REAL `CreditHistoryService` over a fake Supabase client, still through the
     handler, so the caller's id can be seen reaching the `.eq("user_id", …)` filter that is
     the IDOR wall — a stub proves the argument was PASSED, not that it was USED.
  3. A `TestClient` round trip through `app.main`, for what only HTTP exercises: query
     parsing, the 422 on junk, the 401 with no credential, the 409 body on the wire, and
     the 500 that a programming error must stay as.

Hermetic: no Supabase, no network. The `TestClient` is built WITHOUT the context manager,
so the lifespan (whose startup jobs reach Supabase) never runs.
"""
from __future__ import annotations

import ast
import inspect
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import fastapi.params
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from app.api.error_response import ErrorCode
from app.api.v1.endpoints import users as users_endpoint
from app.api.v1.endpoints.users import list_my_credit_history
from app.dependencies import get_current_user
from app.main import app
from app.schemas.credit_history import CreditHistoryResponse
from app.services import credit_history_service as chs
from app.services.credit_history_service import (
    DEFAULT_PAGE,
    MAX_PAGE,
    CreditHistoryService,
    CreditHistoryUnavailable,
)

_PATH = "/api/v1/users/me/credits/history"  # the literal `APIEndpoint.swift` requests
_USER = {"id": "u1", "email": "u1@example.com", "tier": "free"}

# Keys the iOS decoder reads (`CreditTransactionDTO` / `CreditHistoryDTO`). The parity test
# pins them on the MODEL; here they pin that the handler returns that model, whole.
_ITEM_KEYS = {
    "id", "created_at", "delta", "kind", "title", "subtitle",
    "pool_note", "is_reversed", "reason",
}
_ENVELOPE_KEYS = {"items", "next_cursor"}
_ERROR_KEYS = {"error_code", "message", "user_message", "action", "details"}


# ── fakes ────────────────────────────────────────────────────────────────────


class _RecordingService:
    """Stands in for `CreditHistoryService`. Records every call and the thread it ran on.

    `list_for_user`'s signature mirrors the real one — `limit` and `before` KEYWORD-ONLY —
    so a handler that started passing them positionally fails here with a TypeError
    instead of silently binding `limit` to the cursor.
    """

    def __init__(self, page=None, raises=None):
        self.page = page if page is not None else CreditHistoryResponse()
        self.raises = raises
        self.calls: list[dict] = []

    def list_for_user(self, user_id, *, limit=DEFAULT_PAGE, before=None):
        self.calls.append({
            "user_id": user_id, "limit": limit, "before": before,
            "thread": threading.get_ident(),
        })
        if self.raises is not None:
            raise self.raises
        return self.page


def _install(monkeypatch, service):
    """Patched on `users_endpoint`, NOT on `credit_history_service`: `users.py` binds
    `get_credit_history_service` with a MODULE-LEVEL import, so the endpoint module's own
    name is the one live at call time (.claude/rules/testing.md, patch-target trap #1)."""
    monkeypatch.setattr(users_endpoint, "get_credit_history_service", lambda: service)
    return service


class _FakeQuery:
    """Order-INDEPENDENT, like PostgREST: the limit is deferred to `execute()` so an early
    `.limit()` cannot truncate before a later `.lt()` filter and fake a paging bug."""

    def __init__(self, rows, log):
        self._rows = rows
        self._log = log
        self._limit = None

    def select(self, *a, **k):
        self._log.append(("select", a))
        return self

    def eq(self, col, val):
        self._log.append(("eq", col, val))
        self._rows = [r for r in self._rows if str(r.get(col)) == str(val)]
        return self

    def in_(self, col, vals):
        self._log.append(("in_", col, list(vals)))
        wanted = {str(v) for v in vals}
        self._rows = [r for r in self._rows if str(r.get(col)) in wanted]
        return self

    def lt(self, col, val):
        self._log.append(("lt", col, val))
        self._rows = [r for r in self._rows if int(r.get(col)) < int(val)]
        return self

    def order(self, col, desc=False):
        self._log.append(("order", col, desc))
        self._rows = sorted(self._rows, key=lambda r: r.get(col) or 0, reverse=desc)
        return self

    def limit(self, n):
        self._log.append(("limit", n))
        self._limit = n
        return self

    def execute(self):
        rows = self._rows if self._limit is None else self._rows[: self._limit]
        return type("R", (), {"data": list(rows)})()


class _FakeSupabase:
    def __init__(self, tables):
        self.tables = tables
        self.log = []

    def table(self, name):
        self.log.append(("table", name))
        return _FakeQuery(list(self.tables.get(name, [])), self.log)


def _real_service(tables):
    """The production class, minus `__init__` (which would call `get_supabase()`)."""
    svc = CreditHistoryService.__new__(CreditHistoryService)
    svc.supabase = _FakeSupabase(tables)
    return svc


def _ledger_row(row_id, user_id="u1", **over):
    row = {
        "id": row_id,
        "user_id": user_id,
        "delta": -1,
        "reason": "chat_charge",
        "ref_id": f"sess-{row_id}:abcd",
        "created_at": datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc),
        "granted_delta": -1,
        "purchased_delta": 0,
    }
    row.update(over)
    return row


@pytest.fixture(autouse=True)
def _no_real_service(monkeypatch):
    """`get_credit_history_service` memoizes a real `CreditHistoryService` — whose
    `__init__` calls `get_supabase()` — into a module global. Cleared before every test so
    an instance left by something else in the run cannot be what the handler reaches, and
    CHECKED after: a populated memo means a patch in this file missed the live binding and
    the handler went looking for a database."""
    monkeypatch.setattr(chs, "_service", None)
    yield
    assert chs._service is None, (
        "the real CreditHistoryService was constructed — a patch missed "
        "`users_endpoint.get_credit_history_service`"
    )


# ── 1. The caller, and only the caller ───────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("user_id", ["user-aaa", "user-bbb"])
async def test_the_statement_is_the_callers(monkeypatch, user_id):
    """Two ids, so a hard-coded id — the one mutation a single call cannot see — fails
    one of them."""
    svc = _install(monkeypatch, _RecordingService())
    await list_my_credit_history(user={"id": user_id, "email": "x@y", "tier": "free"})
    assert [c["user_id"] for c in svc.calls] == [user_id]


@pytest.mark.asyncio
async def test_the_callers_id_reaches_the_ledger_filter(monkeypatch):
    """The stub above proves the id was PASSED. This proves it was USED: the real service
    over a fake client, with another account's row sitting in the same table. The backend
    holds the service-role key, so this in-code filter is the wall, not RLS."""
    rows = [_ledger_row(1), _ledger_row(2), _ledger_row(3, user_id="someone-else")]
    svc = _install(monkeypatch, _real_service({"credit_transactions": rows}))
    page = await list_my_credit_history(user=_USER)
    # The other account's row (id 3) is absent. THIS is the guard — mutation-tested: with
    # the main query's `.eq("user_id", …)` deleted from the service it reads ['3','2','1'].
    assert [i.id for i in page.items] == ["2", "1"]
    # And the id reached the MAIN select's builder chain, not merely SOME query. The
    # `_reversed_ids` enrichment probe also scopes on user_id, so a bare
    # `("eq", "user_id", "u1") in log` is satisfied even with the main filter gone.
    # Only the main query calls `.limit()`, so the chain is the log up to the first one.
    log = svc.supabase.log
    main_chain = log[: next(i for i, e in enumerate(log) if e[0] == "limit")]
    assert ("eq", "user_id", "u1") in main_chain, main_chain


def test_identity_comes_from_the_strict_dependency_not_the_request():
    """No `user_id` parameter of any kind, and `Depends(get_current_user)` — never
    `get_current_user_or_guest`, which resolves a signed-out caller to the SHARED
    `GUEST_USER_ID` sentinel and would serve every install one global ledger (the
    handler's own docstring makes this argument; this is what holds it). The HTTP 401
    itself is swept suite-wide by `test_account_only_licence_gate.py`."""
    params = inspect.signature(list_my_credit_history).parameters
    assert set(params) == {"limit", "before", "user"}
    dep = params["user"].default
    assert isinstance(dep, fastapi.params.Depends)
    assert dep.dependency is get_current_user


@pytest.mark.asyncio
async def test_a_degraded_identity_never_reaches_the_ledger(monkeypatch):
    """A dict with no `id` must raise, not query. `.eq("user_id", None)` is a scope the
    handler invented, and `user.get("id")` is the one-token edit that would produce it."""
    svc = _install(monkeypatch, _RecordingService())
    with pytest.raises((KeyError, HTTPException)):
        await list_my_credit_history(user={"email": "x@y"})
    assert svc.calls == []


# ── 2. Paging is forwarded, not reinterpreted ────────────────────────────────


@pytest.mark.asyncio
async def test_paging_is_forwarded_untouched(monkeypatch):
    svc = _install(monkeypatch, _RecordingService())
    await list_my_credit_history(limit=7, before="4242", user=_USER)
    assert len(svc.calls) == 1
    assert svc.calls[0]["limit"] == 7
    assert svc.calls[0]["before"] == "4242"


@pytest.mark.asyncio
async def test_the_first_page_asks_for_the_default_and_no_cursor(monkeypatch):
    """iOS omits `before` on page 1 so the request line stays byte-identical. That must
    arrive as `None`, not `""`, or the service logs a junk-cursor warning on every first
    page a user ever opens."""
    svc = _install(monkeypatch, _RecordingService())
    await list_my_credit_history(user=_USER)
    assert len(svc.calls) == 1
    assert svc.calls[0]["limit"] == DEFAULT_PAGE
    assert svc.calls[0]["before"] is None


def test_the_page_size_is_the_ledgers_knob_not_the_inboxs():
    """`users.py` imports TWO constants named `DEFAULT_PAGE` — the inbox's and the
    ledger's — and both happen to be 30, so no value check can tell them apart. The
    ledger's is aliased `CREDIT_HISTORY_DEFAULT_PAGE`; this pins that the handler's default
    is that alias, AST-bounded to the handler's own signature so a mention elsewhere in the
    file cannot satisfy it."""
    tree = ast.parse(Path(inspect.getfile(users_endpoint)).read_text())
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "list_my_credit_history"
    )
    names = [a.arg for a in fn.args.args]
    defaults = dict(zip(names[len(names) - len(fn.args.defaults):], fn.args.defaults))
    limit_default = defaults["limit"]
    assert isinstance(limit_default, ast.Name), ast.dump(limit_default)
    assert limit_default.id == "CREDIT_HISTORY_DEFAULT_PAGE"

    aliases = {
        (alias.name, alias.asname)
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "app.services.credit_history_service"
        for alias in node.names
    }
    assert ("DEFAULT_PAGE", "CREDIT_HISTORY_DEFAULT_PAGE") in aliases


@pytest.mark.asyncio
async def test_the_cursor_and_the_ceiling_reach_the_query(monkeypatch):
    """Through the handler AND the real service: `before` becomes the keyset `lt`, and an
    oversized `limit` is still clamped to `MAX_PAGE` (+1 is the next-page probe). A handler
    that sliced or paged on its own would bypass both."""
    rows = [_ledger_row(i) for i in range(1, 6)]
    svc = _install(monkeypatch, _real_service({"credit_transactions": rows}))
    page = await list_my_credit_history(limit=10_000, before="4", user=_USER)
    assert [i.id for i in page.items] == ["3", "2", "1"]
    assert ("lt", "id", 4) in svc.supabase.log
    assert ("limit", MAX_PAGE + 1) in svc.supabase.log


# ── 3. The shape iOS decodes ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_page_is_the_shape_ios_decodes(monkeypatch):
    """Anti-vacuity for the whole file: the handler gutted to `return
    CreditHistoryResponse()` fails here on the ids, and gutted to a dict fails on the
    type. Swift's decoder is strict, so a subset or a rename is a crash on a money screen."""
    rows = [_ledger_row(i) for i in range(1, 4)]
    _install(monkeypatch, _real_service({"credit_transactions": rows}))
    page = await list_my_credit_history(limit=2, user=_USER)
    assert isinstance(page, CreditHistoryResponse)
    body = page.model_dump()
    assert set(body) == _ENVELOPE_KEYS
    assert [i["id"] for i in body["items"]] == ["3", "2"]
    assert body["next_cursor"] == "2"
    assert all(set(item) == _ITEM_KEYS for item in body["items"])


def test_the_route_is_registered_as_ios_expects():
    """GET, on this handler, with `response_model=CreditHistoryResponse` — the declaration
    that makes FastAPI serialize the model rather than whatever the handler happens to
    return — and mounted at the literal path iOS requests."""
    route = next(
        r for r in users_endpoint.router.routes
        if getattr(r, "path", None) == "/me/credits/history"
    )
    assert route.methods == {"GET"}
    assert route.endpoint is list_my_credit_history
    assert route.response_model is CreditHistoryResponse

    mounted = [r for r in app.routes if getattr(r, "path", None) == _PATH]
    assert len(mounted) == 1, f"expected exactly one route at {_PATH}, found {len(mounted)}"
    assert mounted[0].endpoint is list_my_credit_history


# ── 4. Both halves of the degrade path ───────────────────────────────────────


@pytest.mark.asyncio
async def test_a_read_failure_is_system_busy_not_an_empty_page(monkeypatch):
    """🔴 The money assertion. An empty statement and a broken statement look identical,
    and this is the screen someone opens because they think a refund did not land. The
    body is the `APIErrorResponse` contract (CLAUDE.md invariant #3), `details` flat."""
    _install(monkeypatch, _RecordingService(raises=CreditHistoryUnavailable("db down")))
    resp = await list_my_credit_history(user=_USER)
    assert isinstance(resp, JSONResponse), "an outage was served as a page"
    assert resp.status_code == 409
    body = json.loads(resp.body)
    assert body["error_code"] == ErrorCode.SYSTEM_BUSY.value
    assert set(body) == _ERROR_KEYS
    assert body["action"] == "retry_later"
    assert body["details"] == {}
    # The handler overrides the code's generic copy ("Our analysis engine is at
    # capacity…"), which would be nonsense on a credits screen.
    assert "credit history" in body["user_message"].lower()


@pytest.mark.asyncio
async def test_an_empty_ledger_is_a_page_not_an_error(monkeypatch):
    """The other half: nothing to show is a 200 with an empty list and no cursor. A
    brand-new account must not be told to try again."""
    _install(monkeypatch, _real_service({"credit_transactions": []}))
    page = await list_my_credit_history(user=_USER)
    assert isinstance(page, CreditHistoryResponse)
    assert page.items == []
    assert page.next_cursor is None


@pytest.mark.asyncio
async def test_only_the_typed_failure_is_translated(monkeypatch):
    """The `except` is narrow on purpose. The service already wraps every database failure
    in `CreditHistoryUnavailable`; anything else escaping it is a programming error, and
    relabelling that "try again later" would hide a bug behind a retry the user makes
    forever. It propagates to `main.general_handler` (logged with a stack, 500)."""
    _install(monkeypatch, _RecordingService(raises=RuntimeError("a bug, not an outage")))
    with pytest.raises(RuntimeError):
        await list_my_credit_history(user=_USER)


# ── 5. The event loop is not the place for a Postgres round trip ─────────────


@pytest.mark.asyncio
async def test_the_read_is_offloaded_from_the_event_loop(monkeypatch):
    """`list_for_user` is a SYNC Supabase read (CLAUDE.md invariant #6). Called inline it
    parks the loop for a Postgres round trip — every other request in the process waits on
    one user's statement. `asyncio.to_thread` is the only thing preventing that, and
    dropping it changes no output, so nothing else here would notice."""
    svc = _install(monkeypatch, _RecordingService())
    await list_my_credit_history(user=_USER)
    assert len(svc.calls) == 1
    assert svc.calls[0]["thread"] != threading.get_ident()


# ── 6. Over the wire ─────────────────────────────────────────────────────────


@pytest.fixture
def client():
    # NOT `with TestClient(app)` — the context manager runs the lifespan, whose startup
    # jobs reach Supabase, and conftest blocks that. Logging is silenced for the same
    # reason `test_chat_starters_endpoint.py` does it: the 500 test below deliberately
    # trips `general_handler`, which logs a full stack.
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


def test_over_the_wire_query_params_arrive_typed(client, signed_in, monkeypatch):
    """`?limit=7&before=4242` → `limit=7` (an int: FastAPI parsed it) and `before="4242"`
    (a str: the keyset cursor is a bigint id but travels as text; the service coerces).
    The id is the credential's — a `user_id` in the URL is ignored, not honoured."""
    svc = _install(monkeypatch, _RecordingService())
    resp = client.get(_PATH, params={"limit": 7, "before": "4242", "user_id": "victim"})
    assert resp.status_code == 200, resp.text
    assert len(svc.calls) == 1
    call = svc.calls[0]
    assert (call["user_id"], call["limit"], call["before"]) == ("u1", 7, "4242")
    assert resp.json() == {"items": [], "next_cursor": None}


def test_over_the_wire_a_junk_limit_is_refused_not_served(client, signed_in, monkeypatch):
    """`limit: int` — FastAPI answers 422 on the error contract (`main.validation_handler`)
    and the handler is never entered. The service's `_coerce_int` is a second net for a
    direct caller, not the reason the route is safe."""
    svc = _install(monkeypatch, _RecordingService())
    resp = client.get(_PATH, params={"limit": "lots"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error_code"] == ErrorCode.INVALID_INPUT.value
    assert svc.calls == []


def test_over_the_wire_an_outage_is_a_409_body_ios_can_decode(client, signed_in, monkeypatch):
    """A `JSONResponse` returned from a handler with a `response_model` must pass through
    FastAPI untouched — this is the wire form of the direct test above."""
    _install(monkeypatch, _RecordingService(raises=CreditHistoryUnavailable("db down")))
    resp = client.get(_PATH)
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["error_code"] == ErrorCode.SYSTEM_BUSY.value
    assert set(body) == _ERROR_KEYS


def test_over_the_wire_a_bug_stays_a_500_not_a_retry_prompt(signed_in, monkeypatch):
    """Wire form of `test_only_the_typed_failure_is_translated`, pinning what the route
    ACTUALLY does today: an exception the service did not classify reaches
    `main.general_handler` and is a 500 — never SYSTEM_BUSY's "try again later", which
    would send the user back into the same bug on a loop with no Sentry issue behind it."""
    logging.disable(logging.CRITICAL)
    try:
        svc = _install(monkeypatch, _RecordingService(raises=RuntimeError("a bug, not an outage")))
        resp = TestClient(app, raise_server_exceptions=False).get(_PATH)
    finally:
        logging.disable(logging.NOTSET)
    # `raise_server_exceptions=False` turns ANY unhandled error into a 500, so pin that
    # the 500 is the stub's RuntimeError and not, say, a missed patch blowing up in the
    # real service's constructor. (A missed patch today actually lands as a 409 — the
    # real read trips conftest's network guard and is classified as an outage — but
    # that is conftest's doing, not this test's, so it is not what this relies on.)
    assert len(svc.calls) == 1, "the stub was never reached — the 500 came from elsewhere"
    assert resp.status_code == 500, resp.text
    assert resp.json().get("error_code") != ErrorCode.SYSTEM_BUSY.value


def test_over_the_wire_no_credential_is_refused_before_the_service(client, monkeypatch):
    """The refused case. Swept suite-wide by `test_account_only_licence_gate.py`; here so
    this file's permissive/refused pair is complete, and to pin that the SERVICE is never
    reached — the wall stands in front of the handler, not inside it."""
    svc = _install(monkeypatch, _RecordingService())
    resp = client.get(_PATH)
    assert resp.status_code == 401, resp.text
    assert resp.json()["error_code"] == ErrorCode.AUTH_REQUIRED.value
    assert svc.calls == []
