"""The guest-chat IP ceiling — the ONE anti-rotation control on the ONE AI surface a
signed-out caller can reach, and nothing was exercising it.

WHY THIS FILE EXISTS
--------------------
`_ip_budget_bucket`, the IP half of `_claim_chat_turn_or_error`, and `_record_chat_tokens`
in `app/api/v1/endpoints/chat.py` had **zero test coverage**. `CHAT_DAILY_TURN_LIMIT_PER_IP`
had no test reference at all, and `tests/test_chat_credits.py` monkeypatches
`_claim_chat_turn_or_error` OUT entirely, so the IP branch has never run under the suite.
Found by mutation: deleting the whole `if req is not None and user.get("is_guest")` block —
handing every guest unlimited chat for the price of a fresh header per request — leaves the
suite green.

The defect is the one `_claim_chat_turn_or_error`'s own docstring describes: the per-install
bucket keys on `guest_user_id_for(X-Guest-Id)`, a uuid5 of a header the CLIENT picks, so a
fresh header minted a fresh 60-turn allowance on every request. Report generation went
account-only; chat did not. The IP bucket is the only thing that makes rotation buy nothing,
and it keys on `trusted_client_ip` — the RIGHTMOST `X-Forwarded-For` entry, the one part of
that header the caller cannot write (Railway's edge appends it). Under
`--forwarded-allow-ips='*'` uvicorn rewrites `request.client.host` to the LEFTMOST entry, so
`client.host` is attacker-controlled too. `test_auth_rate_limit_keys.py` proves that helper;
this file proves chat USES it, that the ceiling actually trips, and that the telemetry write
beside it can never break a turn.

Pure module: a fake budget service, a fake Request, no network, no Supabase.
"""
from __future__ import annotations

import ast
import inspect
import json
import logging
import uuid
from pathlib import Path

import pytest

import app.api.v1.endpoints.chat as chat
import app.services.chat_budget_service as cbs
from app.api.error_response import ErrorCode
from app.config import settings
from app.dependencies import GUEST_USER_ID, chat_identity_key, guest_user_id_for
from app.services.chat_budget_service import ChatBudgetUnavailable

_EDGE_IP = "203.0.113.5"          # what OUR edge appended — the only unforgeable part
_OTHER_EDGE_IP = "198.51.100.77"  # a different network entirely

GUEST = {"id": guest_user_id_for("install-1"), "is_guest": True}
AUTHED = {"id": "authed-user-1", "is_guest": False}


# ── fakes ────────────────────────────────────────────────────────────────────

class _Headers:
    def __init__(self, mapping):
        self._m = {k.lower(): v for k, v in mapping.items()}

    def get(self, key, default=None):
        return self._m.get(key.lower(), default)


class _Client:
    def __init__(self, host):
        self.host = host


class _Req:
    """What uvicorn hands the handler under `--forwarded-allow-ips='*'`: `client.host` has
    ALREADY been rewritten to the leftmost (attacker-written) XFF entry, so the only part of
    the request the caller did not author is the rightmost entry our edge appended."""

    def __init__(self, xff=None, client_host="10.0.0.9"):
        self.headers = _Headers({"x-forwarded-for": xff} if xff is not None else {})
        self.client = _Client(client_host) if client_host else None


def _guest_req(spoof="198.51.100.1", edge=_EDGE_IP) -> _Req:
    return _Req(xff=f"{spoof}, {edge}", client_host=spoof)


def _rotated(i: int) -> tuple[dict, str]:
    """A guest who sent a brand-new `X-Guest-Id` — the rotation attack, one turn of it."""
    header = f"rot-{i}"
    return {"id": guest_user_id_for(header), "is_guest": True}, header


class _FakeBudget:
    """Stands in for `ChatBudgetService`.

    Records every claim as `(bucket, effective_limit)` — the limit the RPC would actually
    be handed, i.e. `CHAT_DAILY_TURN_LIMIT` when the caller passes none, exactly as the real
    `try_claim_turn` defaults it. Recording the raw argument instead would pin the call's
    SPELLING (`limit=None` vs omitted vs `limit=settings.CHAT_DAILY_TURN_LIMIT`) rather
    than its meaning.

    Answers come from `answers`, keyed on bucket: an int count, -1 for cap-reached, or an
    exception instance to raise. Unscripted buckets are granted (1) — unless
    `counting=True`, which emulates the `claim_chat_turn` RPC's own rule (grant while
    `count < limit`, else -1) so a test can drive the ceiling to the limit for real rather
    than scripting the answer it wants.
    """

    def __init__(self, answers=None, *, counting=False):
        self.claims: list[tuple[str, int | None]] = []
        self.recorded: list[tuple[str, int]] = []
        self.record_raises: Exception | None = None
        self._answers = dict(answers or {})
        self._counting = counting
        self._counts: dict[str, int] = {}

    def try_claim_turn(self, bucket_key, limit=None):
        effective = settings.CHAT_DAILY_TURN_LIMIT if limit is None else limit
        self.claims.append((bucket_key, effective))
        scripted = self._answers.get(bucket_key)
        if isinstance(scripted, Exception):
            raise scripted
        if scripted is not None:
            return scripted
        if self._counting:
            if self._counts.get(bucket_key, 0) >= effective:
                return -1
            self._counts[bucket_key] = self._counts.get(bucket_key, 0) + 1
            return self._counts[bucket_key]
        return 1

    def record_tokens(self, bucket_key, tokens):
        if self.record_raises is not None:
            raise self.record_raises
        self.recorded.append((bucket_key, tokens))


@pytest.fixture(autouse=True)
def _no_real_budget_service(monkeypatch):
    """Two things, both fail-closed.

    `get_chat_budget_service` memoizes a process-wide singleton whose constructor calls
    `get_supabase()`; reset it so nothing here inherits a client another test built. And
    `chat.py` binds that getter with a MODULE-LEVEL import, so the live name is the endpoint
    module's own — patch it to REFUSE, so a test that forgets `_install` fails instead of
    reaching the real service (and the conftest network guard). Outside the
    `_record_chat_tokens` catch-all that failure is this AssertionError verbatim; inside it,
    `AssertionError` IS an `Exception`, so it surfaces as an empty `recorded` list."""
    monkeypatch.setattr(cbs, "_chat_budget_service", None)

    def _refuse():
        raise AssertionError("test reached the real ChatBudgetService — call _install() first")

    monkeypatch.setattr(chat, "get_chat_budget_service", _refuse)


def _install(monkeypatch, fake: _FakeBudget) -> _FakeBudget:
    monkeypatch.setattr(chat, "get_chat_budget_service", lambda: fake)
    return fake


# ── 1. The bucket key ────────────────────────────────────────────────────────

def test_bucket_keys_on_the_edge_appended_address_not_the_spoofed_prefix():
    """THE property. The caller rewrites the XFF prefix — and, under
    `--forwarded-allow-ips='*'`, thereby `client.host` too — on every request. All of
    those must collapse onto the ONE bucket our edge's appended address identifies.
    Keying on `client.host` instead (the obvious refactor) fails this."""
    rotated = {chat._ip_budget_bucket(_guest_req(spoof=f"198.51.100.{i}")) for i in range(1, 60)}
    assert rotated == {chat._ip_budget_bucket(_Req(xff=_EDGE_IP, client_host=_EDGE_IP))}, (
        "rotating the forgeable X-Forwarded-For prefix minted distinct IP buckets"
    )


def test_two_addresses_get_two_buckets():
    """The permissive half: a ceiling per NETWORK, not one global one — a different
    address must not inherit a neighbour's exhausted allowance. A constant key passes
    every other test in this section and fails here."""
    assert chat._ip_budget_bucket(_guest_req(edge=_EDGE_IP)) != chat._ip_budget_bucket(
        _guest_req(edge=_OTHER_EDGE_IP)
    )


def test_bucket_is_deterministic_and_pseudonymous():
    """Same address → same row across requests (or the ceiling never accumulates), and the
    row must NOT carry the raw address: it outlives the request in `chat_usage_budget`."""
    a = chat._ip_budget_bucket(_guest_req())
    b = chat._ip_budget_bucket(_guest_req())
    assert a == b
    assert _EDGE_IP not in a
    parsed = uuid.UUID(a)
    assert parsed.version == 5
    assert parsed == uuid.uuid5(chat._IP_BUDGET_NAMESPACE, _EDGE_IP)


def test_the_namespace_is_pinned():
    """"Random once, constant forever — changing it hands every caller a fresh allowance."
    Pin the literal so a well-meant regeneration is a test failure, not a silent reset."""
    assert chat._IP_BUDGET_NAMESPACE == uuid.UUID("2b7f4e91-0c3d-4a86-9f52-8d1e6a04b7c3")


@pytest.mark.parametrize("garbage", [" , ", ",,,", "   "])
def test_garbage_forwarding_header_cannot_mint_a_fresh_bucket(garbage):
    """An XFF of only separators/whitespace is not an address. It must fall through to the
    peer and land on the SAME bucket a header-less request from that peer gets — never on
    a bucket of its own."""
    from_garbage = chat._ip_budget_bucket(_Req(xff=garbage, client_host="10.0.0.9"))
    assert from_garbage == chat._ip_budget_bucket(_Req(xff=None, client_host="10.0.0.9"))
    # …and "the same bucket" means the PEER's, not two callers both landing on a sentinel
    assert from_garbage == str(uuid.uuid5(chat._IP_BUDGET_NAMESPACE, "10.0.0.9"))


def test_no_address_at_all_shares_one_bucket_rather_than_none():
    """No header and no peer (a unix socket, a test client) resolves to the `"unknown"`
    sentinel. Fail CLOSED: every such caller shares ONE ceiling — a stable, well-formed key
    the uuid column accepts, never a fresh one per request."""
    a = chat._ip_budget_bucket(_Req(xff=None, client_host=None))
    b = chat._ip_budget_bucket(_Req(xff=None, client_host=None))
    assert a == b
    assert a == str(uuid.uuid5(chat._IP_BUDGET_NAMESPACE, "unknown"))


# ── 2. The IP half of _claim_chat_turn_or_error ──────────────────────────────

def test_a_guest_claims_the_install_bucket_then_the_ip_bucket_with_its_own_limit(monkeypatch):
    """Order and limits are the contract: install bucket at the default cap, then the IP
    bucket at `CHAT_DAILY_TURN_LIMIT_PER_IP` — not the 60-turn install cap, which would
    wall a shared office network after one afternoon."""
    fake = _install(monkeypatch, _FakeBudget())
    req = _guest_req()
    assert chat._claim_chat_turn_or_error(GUEST, "install-1", req) is None
    assert fake.claims == [
        (chat_identity_key(GUEST, "install-1"), settings.CHAT_DAILY_TURN_LIMIT),
        (chat._ip_budget_bucket(req), settings.CHAT_DAILY_TURN_LIMIT_PER_IP),
    ]
    assert settings.CHAT_DAILY_TURN_LIMIT_PER_IP > settings.CHAT_DAILY_TURN_LIMIT, (
        "the IP ceiling must sit ABOVE the per-install cap or a shared network is walled first"
    )


def test_rotating_the_guest_header_lands_every_turn_on_the_same_ip_bucket(monkeypatch):
    """🔴 The money assertion. A fresh header DOES mint a fresh install bucket each time —
    that is the rotation attack, and by design it is not stopped there — and must buy
    nothing at all on the IP side."""
    fake = _install(monkeypatch, _FakeBudget())
    req = _guest_req()
    for i in range(25):
        user, header = _rotated(i)
        assert chat._claim_chat_turn_or_error(user, header, req) is None

    ip_key = chat._ip_budget_bucket(req)
    ip_claims = [c for c in fake.claims if c[0] == ip_key]
    install_claims = [c for c in fake.claims if c[0] != ip_key]
    assert len({b for b, _ in install_claims}) == 25   # rotation minted 25 install buckets …
    assert len(ip_claims) == 25                        # … and every turn hit the ONE IP bucket
    assert {lim for _, lim in ip_claims} == {settings.CHAT_DAILY_TURN_LIMIT_PER_IP}


def test_a_guest_without_an_install_header_still_meets_the_ip_ceiling(monkeypatch):
    """Missing input falls CLOSED. A shipped app version that sends no `X-Guest-Id` resolves
    to the shared sentinel bucket; the IP half keys on `is_guest`, not on the header's
    presence, so omitting the header is not an exit from the ceiling."""
    fake = _install(monkeypatch, _FakeBudget())
    legacy = {"id": GUEST_USER_ID, "is_guest": True}
    req = _guest_req()
    assert chat._claim_chat_turn_or_error(legacy, None, req) is None
    assert fake.claims == [
        (GUEST_USER_ID, settings.CHAT_DAILY_TURN_LIMIT),
        (chat._ip_budget_bucket(req), settings.CHAT_DAILY_TURN_LIMIT_PER_IP),
    ]


def test_the_ip_ceiling_refuses_with_the_typed_409(monkeypatch, caplog):
    """A `-1` from the IP bucket is the same user-facing refusal as the install cap — and
    it must be logged as the rotation signal it almost certainly is."""
    req = _guest_req()
    _install(monkeypatch, _FakeBudget({chat._ip_budget_bucket(req): -1}))
    with caplog.at_level(logging.WARNING, logger=chat.logger.name):
        resp = chat._claim_chat_turn_or_error(GUEST, "install-1", req)

    assert resp is not None, "the IP ceiling answered -1 and the guest was let through"
    assert resp.status_code == 409
    body = json.loads(bytes(resp.body))
    assert body["error_code"] == ErrorCode.CHAT_DAILY_LIMIT_REACHED.value
    # invariant #3: the iOS decoder needs these keys or it cannot render an actionable error
    assert body["user_message"] and "action" in body
    assert "Chat IP ceiling reached" in caplog.text


def test_the_ceiling_trips_at_exactly_the_limit_under_rotation(monkeypatch):
    """End-to-end over the RPC's own rule (grant while count < limit). With a fresh header
    per turn no install bucket ever exceeds 1, so ONLY the IP ceiling can stop this: turn
    N is granted and turn N+1 is refused. A constant `return None` fails here."""
    monkeypatch.setattr(settings, "CHAT_DAILY_TURN_LIMIT_PER_IP", 5)
    _install(monkeypatch, _FakeBudget(counting=True))
    req = _guest_req()
    outcomes = [chat._claim_chat_turn_or_error(*_rotated(i), req) for i in range(6)]

    assert outcomes[:5] == [None] * 5
    assert outcomes[5] is not None and outcomes[5].status_code == 409


def test_an_exhausted_network_does_not_wall_a_different_one(monkeypatch):
    """The ceiling is per-network. One exhausted address must not become a global outage
    for every other guest — the failure a single shared bucket would produce."""
    monkeypatch.setattr(settings, "CHAT_DAILY_TURN_LIMIT_PER_IP", 2)
    _install(monkeypatch, _FakeBudget(counting=True))
    exhausted, other = _guest_req(edge=_EDGE_IP), _guest_req(edge=_OTHER_EDGE_IP)
    for i in range(2):
        assert chat._claim_chat_turn_or_error(*_rotated(i), exhausted) is None
    assert chat._claim_chat_turn_or_error(*_rotated(9), exhausted) is not None
    assert chat._claim_chat_turn_or_error(*_rotated(10), other) is None


def test_a_signed_in_caller_gets_exactly_one_bucket(monkeypatch):
    """A real account id is not rotatable, and an IP ceiling there would throttle a
    household or office sharing an address — the docstring's stated reason."""
    fake = _install(monkeypatch, _FakeBudget())
    assert chat._claim_chat_turn_or_error(AUTHED, None, _guest_req()) is None
    assert fake.claims == [("authed-user-1", settings.CHAT_DAILY_TURN_LIMIT)]


def test_no_request_means_no_ip_bucket(monkeypatch):
    """`req=None` is the documented non-HTTP contract: install bucket only. Section 4 is
    what guarantees the real handlers never take this path by accident."""
    fake = _install(monkeypatch, _FakeBudget())
    assert chat._claim_chat_turn_or_error(GUEST, "install-1", None) is None
    assert fake.claims == [(chat_identity_key(GUEST, "install-1"), settings.CHAT_DAILY_TURN_LIMIT)]


def test_an_ip_budget_transport_fault_fails_open_but_loudly(monkeypatch, caplog):
    """Documented: a DB blip must never wall a user out of chat. But this is the anti-abuse
    ceiling, so the open door must be LOGGED — a persistent fault here means rotation is
    buying allowance again, and the warning is the only way anyone finds out."""
    req = _guest_req()
    _install(monkeypatch, _FakeBudget({chat._ip_budget_bucket(req): ChatBudgetUnavailable("rpc down")}))
    with caplog.at_level(logging.WARNING, logger=chat.logger.name):
        assert chat._claim_chat_turn_or_error(GUEST, "install-1", req) is None
    assert "Chat IP budget unavailable" in caplog.text


def test_an_install_cap_short_circuits_before_the_ip_bucket_is_touched(monkeypatch):
    """A capped install must not ALSO burn a turn off its network's shared ceiling."""
    fake = _install(monkeypatch, _FakeBudget({chat_identity_key(GUEST, "install-1"): -1}))
    resp = chat._claim_chat_turn_or_error(GUEST, "install-1", _guest_req())
    assert resp is not None and resp.status_code == 409
    assert len(fake.claims) == 1


def test_an_install_budget_fault_fails_open_without_touching_the_ip_bucket(monkeypatch):
    """The other branch of the install degrade path: a transport fault on the first bucket
    returns early (open), and the IP bucket is never consulted for that turn."""
    fake = _install(
        monkeypatch,
        _FakeBudget({chat_identity_key(GUEST, "install-1"): ChatBudgetUnavailable("rpc down")}),
    )
    assert chat._claim_chat_turn_or_error(GUEST, "install-1", _guest_req()) is None
    assert len(fake.claims) == 1


# ── 3. _record_chat_tokens ───────────────────────────────────────────────────

def test_tokens_are_forwarded_to_the_callers_bucket(monkeypatch):
    """Anti-vacuity for this helper: a `pass` body leaves `recorded` empty."""
    fake = _install(monkeypatch, _FakeBudget())
    chat._record_chat_tokens(GUEST, "install-1", 123)
    chat._record_chat_tokens(AUTHED, None, 7)
    assert fake.recorded == [
        (chat_identity_key(GUEST, "install-1"), 123),
        ("authed-user-1", 7),
    ]


@pytest.mark.parametrize("raw, expected", [(None, 0), (12.9, 12), ("40", 40)])
def test_tokens_are_coerced_to_int_before_the_rpc(monkeypatch, raw, expected):
    """The stream path passes `tokens_used or len(content) // 4`; a SDK usage object can
    hand back None or a float, and the RPC's `p_tokens` is an int column."""
    fake = _install(monkeypatch, _FakeBudget())
    chat._record_chat_tokens(GUEST, "install-1", raw)
    assert fake.recorded == [(chat_identity_key(GUEST, "install-1"), expected)]
    assert type(fake.recorded[0][1]) is int


def test_a_failing_record_never_raises(monkeypatch, caplog):
    """A telemetry write must never break a paid, already-answered turn — and it must
    say so with the exception TYPE, since that is all a log-only diagnosis has."""
    fake = _install(monkeypatch, _FakeBudget())
    fake.record_raises = RuntimeError("supabase down")
    with caplog.at_level(logging.WARNING, logger=chat.logger.name):
        chat._record_chat_tokens(GUEST, "install-1", 50)
    assert "Chat token record failed" in caplog.text and "RuntimeError" in caplog.text


def test_an_unconstructible_service_never_raises(monkeypatch, caplog):
    """The getter itself sits inside the try: a cold singleton whose constructor fails
    (Supabase env missing) must degrade the same way, not take the turn down with it —
    and, like every other branch of that catch-all, say so. Without the log assertion this
    test also passes a bare `except: pass`, which is the silent degradation the rulebook
    bans."""
    def _boom():
        raise RuntimeError("no supabase client")

    monkeypatch.setattr(chat, "get_chat_budget_service", _boom)
    with caplog.at_level(logging.WARNING, logger=chat.logger.name):
        chat._record_chat_tokens(GUEST, "install-1", 50)
    assert "Chat token record failed" in caplog.text and "RuntimeError" in caplog.text


def test_garbage_tokens_are_dropped_not_raised(monkeypatch, caplog):
    """An un-int-able value is a bug upstream, not a reason to fail the turn: the RPC is
    not called, nothing propagates — and the upstream bug is logged with its type, since a
    warning is the only trace it will ever leave."""
    fake = _install(monkeypatch, _FakeBudget())
    with caplog.at_level(logging.WARNING, logger=chat.logger.name):
        chat._record_chat_tokens(GUEST, "install-1", "not-a-number")
    assert fake.recorded == []
    assert "Chat token record failed" in caplog.text and "ValueError" in caplog.text


# ── 4. The ceiling is actually REACHED from the HTTP handlers ─────────────────
#
# `_claim_chat_turn_or_error(user, x_guest_id)` without `req` is a perfectly legal call that
# silently drops the ceiling — `req=None` skips the IP half by contract (pinned above). So
# the one production call must pass it, and both HTTP handlers must hand `_claim_chat_quota`
# their Request. A correct gate nothing reaches is exactly the shape of the original bug.
#
# AST-bounded to call nodes, so neither a docstring nor a comment can satisfy it. And it is
# not enough that SOMETHING is passed: a positional `None` is as legal as `req=None`, the
# handlers also carry a Pydantic body named `request`, and a stale `req = None` local would
# shadow the parameter — so the value must be the enclosing function's OWN parameter, never
# rebound in its body, and for the HTTP handlers that parameter must be annotated `Request`.

_SCOPE_BOUNDARIES = (
    ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
    ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
)


def _chat_ast() -> tuple[ast.Module, dict[ast.AST, ast.AST]]:
    tree = ast.parse(Path(inspect.getfile(chat)).read_text())
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    return tree, parents


def _calls_to(tree: ast.Module, name: str) -> list[ast.Call]:
    return [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == name
    ]


def _is_none(node) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _req_argument(call: ast.Call, *, positional_index: int | None):
    """The expression passed as `req`, or None when it is absent or a literal `None` —
    by keyword OR by position, since either spelling silently disables the ceiling."""
    for kw in call.keywords:
        if kw.arg == "req":
            return None if _is_none(kw.value) else kw.value
    if positional_index is not None and len(call.args) > positional_index:
        arg = call.args[positional_index]
        return None if _is_none(arg) else arg
    return None


def _enclosing_function(parents, node):
    cur = parents.get(node)
    while cur is not None and not isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
        cur = parents.get(cur)
    return cur


def _params(fn) -> dict[str, ast.expr | None]:
    a = fn.args
    return {p.arg: p.annotation for p in (*a.posonlyargs, *a.args, *a.kwonlyargs)}


def _is_request_annotation(ann) -> bool:
    return (isinstance(ann, ast.Name) and ann.id == "Request") or (
        isinstance(ann, ast.Attribute) and ann.attr == "Request"
    )


def _rebound_in(fn, name: str) -> bool:
    """True if `fn`'s OWN body stores to `name` (assignment, for/with/except target, walrus).
    Nested scopes are skipped: a comprehension or inner def binding the same name does not
    shadow the parameter at the call site."""
    stack = list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, _SCOPE_BOUNDARIES):
            continue
        if isinstance(n, ast.Name) and n.id == name and isinstance(n.ctx, ast.Store):
            return True
        stack.extend(ast.iter_child_nodes(n))
    return False


def _forwarded_parameter(parents, call: ast.Call, *, positional_index: int | None):
    """The (enclosing function, parameter name) a call forwards as `req`, asserting the
    value is that function's own, never-rebound parameter."""
    fn = _enclosing_function(parents, call)
    assert fn is not None, f"line {call.lineno}: claim call outside any function"
    arg = _req_argument(call, positional_index=positional_index)
    assert arg is not None, (
        f"{fn.name} (line {call.lineno}) calls {call.func.id} without a real `req` — the IP "
        "ceiling is skipped on that path and X-Guest-Id rotation buys unlimited chat again"
    )
    assert isinstance(arg, ast.Name) and arg.id in _params(fn), (
        f"{fn.name} (line {call.lineno}) passes req={ast.unparse(arg)}, which is not one of "
        f"its own parameters {sorted(_params(fn))}"
    )
    assert not _rebound_in(fn, arg.id), (
        f"{fn.name} rebinds `{arg.id}` in its body before forwarding it as `req`"
    )
    return fn, arg.id


def test_every_claim_site_threads_the_request_through():
    """Both lists must be non-empty (anti-vacuity: a rename would otherwise pass by finding
    nothing to check), and every call must carry the real Request."""
    tree, parents = _chat_ast()

    inner = _calls_to(tree, "_claim_chat_turn_or_error")
    assert inner, "no call to _claim_chat_turn_or_error in chat.py — guard has gone vacuous"
    for call in inner:
        _forwarded_parameter(parents, call, positional_index=2)

    outer = _calls_to(tree, "_claim_chat_quota")
    assert len(outer) >= 2, "expected the send AND stream handlers to claim quota"
    for call in outer:
        fn, name = _forwarded_parameter(parents, call, positional_index=None)
        assert _is_request_annotation(_params(fn)[name]), (
            f"{fn.name} passes req={name}, which is not annotated `Request` — the ceiling "
            "would key on whatever that object's `.headers`/`.client` happen to be"
        )


def test_the_wiring_guard_rejects_each_way_of_dropping_the_request():
    """Mutation-test the guard itself, in-process, so it cannot rot into a vacuous pass.
    Each snippet is a call-site shape that DOES compile and DOES disable the ceiling; the
    guard must refuse every one and accept the correct spelling."""
    def verdict(src: str, *, positional_index) -> bool:
        tree = ast.parse(src)
        parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
        (call,) = _calls_to(tree, "_claim_chat_quota")
        try:
            fn, name = _forwarded_parameter(parents, call, positional_index=positional_index)
            return _is_request_annotation(_params(fn)[name])
        except AssertionError:
            return False

    handler = "async def h(session_id, request: SendChatMessageRequest, req: Request):\n    {}\n"
    ok = "q = _claim_chat_quota(user, g, session_id=s, req=req)"
    assert verdict(handler.format(ok), positional_index=None) is True

    bad = {
        "omitted":            "q = _claim_chat_quota(user, g, session_id=s)",
        "keyword None":       "q = _claim_chat_quota(user, g, session_id=s, req=None)",
        "the body model":     "q = _claim_chat_quota(user, g, session_id=s, req=request)",
        "a local":            "x = 1\n    q = _claim_chat_quota(user, g, session_id=s, req=x)",
        "shadowed parameter": "req = None\n    q = _claim_chat_quota(user, g, session_id=s, req=req)",
        "for-target shadow":  "for req in ():\n        pass\n    q = _claim_chat_quota(user, g, session_id=s, req=req)",
    }
    leaks = [label for label, body in bad.items()
             if verdict(handler.format(body), positional_index=None)]
    assert not leaks, f"the wiring guard accepted a call shape that drops the ceiling: {leaks}"

    # positional spelling, as `_claim_chat_quota` itself forwards to the inner claim
    pos = "def _claim_chat_quota(user, g, *, session_id, req=None):\n    {}\n"
    assert verdict(pos.format("q = _claim_chat_quota(user, g, req)"), positional_index=2) is False, (
        "an unannotated forwarder must not satisfy the Request-annotation check"
    )
    tree = ast.parse(pos.format("q = _claim_chat_quota(user, g, None)"))
    parents = {c: p for p in ast.walk(tree) for c in ast.iter_child_nodes(p)}
    (call,) = _calls_to(tree, "_claim_chat_quota")
    with pytest.raises(AssertionError, match="without a real `req`"):
        _forwarded_parameter(parents, call, positional_index=2)
