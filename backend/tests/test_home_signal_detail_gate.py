"""The Pro/Max gate on the Home signals DRILL-DOWN — the second door to whale position detail.

WHY THIS FILE EXISTS
--------------------
`GET /home/signals/{kind}/{ticker}` returns WHO holds a ticker, WHEN they bought it and HOW
MUCH — the same 13F/congress position-level detail the whale profile withholds from Free.
The handler's own docstring records the regression its inline gate fixes:

    "This route used to take no auth dependency at all, which made the signals redaction
     on `/dashboard` a curtain rather than a gate: the masked ticker was the only thing
     standing between a free caller and the full holder list, and a guessed symbol walked
     straight past it."

That gate is three lines at the top of the handler — `if not whale_detail_unlocked(...)` —
and NOTHING pinned them. `test_signals_detail.py` exercises `signals_service` and the
schemas only; `test_signals_entitlement.py` drives `/dashboard` and never this route. Found
by mutation: deleting the `if` left the whole suite green, and every Free account got the
full holder list back for any symbol it could guess. Same shape as
`test_whale_trade_group_gate.py`: one door closed, one left open.

The handler is awaited directly with a fake identity dict and a fake `SignalsService`, so
this is the HTTP layer's own `user.get("tier")` → gate → service wiring under test, not the
pure predicate (that lives in `test_entitlements.py`). No FastAPI app, no network, no Supabase.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
from pathlib import Path

import pytest

from app.api.error_response import ErrorCode
from app.api.v1.endpoints import home as home_endpoint
from app.schemas.signals_detail import SignalHolderResponse, SignalTickerDetailResponse
from app.services import signals_service as signals_service_module
from app.services.entitlements import TIER_FREE, TIER_MAX, TIER_PRO

_KIND = "whale"
_TICKER = "AAPL"

# The shape a per-install guest identity carried while this route was still guest-reachable:
# a synthetic id and a HARDCODED "free" tier (`dependencies.py` builds every guest that way),
# plus the flag. The gate must read the TIER, not the flag — a real Free account has
# `is_guest: False` and must lock identically, and a guest with the flag missing must too.
_GUEST_IDENTITY = {"id": "guest-uuid", "email": "guest@local", "tier": TIER_FREE, "is_guest": True}


def _holder(name: str = "Berkshire Hathaway") -> SignalHolderResponse:
    return SignalHolderResponse(
        whale_id="11111111-1111-1111-1111-111111111111",
        name=name,
        subtitle="13F fund",
        allocation_percent=12.5,
        is_new_position=True,
        amount_est=1_000_000.0,
    )


class _FakeSignalsService:
    """Stands in for `SignalsService` behind the module-level `get_signals_service` binding.

    Records every `(kind, ticker)` it is asked for, so a test can assert the refused path
    never reaches the data layer at all — a gate that fetched and THEN refused would still
    warm a shared cache on behalf of a caller who is not entitled to the answer.

    `holders=[]` models the service's own documented degradation (an honest empty state,
    never an error); `raises=` models a transport fault escaping it.
    """

    def __init__(self, *, holders=None, raises: BaseException | None = None):
        self.calls: list[tuple[str, str]] = []
        self._holders = holders if holders is not None else [_holder()]
        self._raises = raises

    async def get_ticker_detail(self, kind: str, ticker: str) -> SignalTickerDetailResponse:
        self.calls.append((kind, ticker))
        if self._raises is not None:
            raise self._raises
        return SignalTickerDetailResponse(symbol=ticker, kind=kind, holders=list(self._holders))


@pytest.fixture(autouse=True)
def _the_seam_is_what_we_patch():
    """Seam sanity, before AND after every test.

    `home.py` binds `get_signals_service` with a MODULE-LEVEL import, so the patch below
    lands on `home_endpoint`, not on `app.services.signals_service`. If that import ever
    becomes function-scoped, the attribute disappears and this fails by name instead of
    the patch silently missing the live binding (testing.md, hermeticity trap 1). The
    post-check proves `monkeypatch` handed the real factory back, so no test here can
    leak a fake into a neighbouring file. Nothing else is memoized on this path: the
    real singleton factory is never invoked, and `entitlements` is pure.
    """
    real = signals_service_module.get_signals_service
    assert home_endpoint.get_signals_service is real, (
        "home.py no longer binds `get_signals_service` at module level — re-point the "
        "patch in `_call` at the binding the handler actually resolves"
    )
    yield
    assert home_endpoint.get_signals_service is real, "a fake service leaked past monkeypatch"


def _call(monkeypatch, identity: dict, *, kind: str = _KIND, ticker: str = _TICKER, svc=None):
    """Await the handler the way FastAPI would, with `Depends` already resolved.

    Run under `asyncio.run` in a SYNC test on purpose: pytest-asyncio is in strict mode,
    where a forgotten `@pytest.mark.asyncio` skips an async test silently — and a silently
    skipped paywall assertion is worse than none. `asyncio.run` builds its own loop, so it
    does not read the process-wide policy the way `get_event_loop()` does.
    """
    svc = svc if svc is not None else _FakeSignalsService()
    monkeypatch.setattr(home_endpoint, "get_signals_service", lambda: svc)
    resp = asyncio.run(
        home_endpoint.get_signal_ticker_detail(kind=kind, ticker=ticker, user=identity)
    )
    return resp, svc


def _body(resp) -> dict:
    """The error contract is a `JSONResponse`; a success is the Pydantic model itself."""
    assert not isinstance(resp, SignalTickerDetailResponse), (
        "expected an error response but the handler served the holder list"
    )
    return json.loads(resp.body.decode())


# ── 1. Permissive: a paid tier gets the holder list ──────────────────────────

@pytest.mark.parametrize("tier", [TIER_PRO, TIER_MAX, " Pro "])
def test_a_paid_tier_gets_the_holder_list(monkeypatch, tier):
    """The permissive case, including the whitespace/case slop `normalize_tier` folds.
    Anti-vacuity: the fake must have been asked for exactly this (kind, ticker) — a gate
    gutted to a canned success that never consults the service would also "pass" on the
    type check alone."""
    resp, svc = _call(monkeypatch, {"id": "u1", "email": "a@b.c", "tier": tier, "is_guest": False})

    assert isinstance(resp, SignalTickerDetailResponse)
    assert resp.symbol == _TICKER and resp.kind == _KIND
    assert [h.name for h in resp.holders] == ["Berkshire Hathaway"]
    assert svc.calls == [(_KIND, _TICKER)]


def test_a_paid_caller_still_gets_input_validation(monkeypatch):
    """The gate must not short-circuit the validators for the callers it lets through:
    an unsupported kind is INVALID_INPUT (400), not a service call with garbage — and the
    `valid` hint is a JOINED string, because iOS `AnyCodable` yields "" for a list."""
    resp, svc = _call(monkeypatch, {"tier": TIER_PRO}, kind="insider")

    body = _body(resp)
    assert resp.status_code == 400
    assert body["error_code"] == ErrorCode.INVALID_INPUT.value
    assert body["details"]["valid"] == "congress, whale"
    assert svc.calls == []


def test_a_paid_caller_with_an_empty_ticker_is_rejected_before_the_service(monkeypatch):
    """The second validator: an empty symbol never reaches `get_ticker_detail`."""
    resp, svc = _call(monkeypatch, {"tier": TIER_PRO}, ticker="")

    body = _body(resp)
    assert resp.status_code == 400
    assert body["error_code"] == ErrorCode.INVALID_INPUT.value
    assert svc.calls == []


# ── 2. Refused: Free is answered with the typed paywall error ────────────────

def test_free_is_refused_with_the_typed_error(monkeypatch):
    """🔴 The money assertion. Deleting the `if` in the handler passed the whole suite.

    Pins the contract iOS decodes (invariant #3): `error_code` + `user_message`, the
    registered 403 / "upgrade" pair, and `details.tier_required` naming the cheapest
    unlocking plan so the paywall the client renders agrees with the server's ladder."""
    resp, _ = _call(monkeypatch, {"id": "u9", "email": "a@b.c", "tier": TIER_FREE, "is_guest": False})

    body = _body(resp)
    assert resp.status_code == 403
    assert body["error_code"] == ErrorCode.WHALE_FOLLOW_LOCKED.value
    assert body["user_message"], "iOS renders `user_message`; an empty one is a blank alert"
    assert body["action"] == "upgrade"
    assert body["details"]["tier_required"] == TIER_PRO
    assert body["details"]["kind"] == _KIND


def test_a_refused_call_never_reaches_the_service(monkeypatch):
    """The gate runs BEFORE the fetch. Otherwise a locked caller could still warm the
    shared detail cache — and a later refactor that fetched, then redacted, would leave the
    holder list one log line away from a Free account.

    Both halves are asserted: `calls == []` alone also holds for a handler gutted to a
    canned SUCCESS that never consults the service (mutation-tested: it passed), so the
    test must first prove the caller was actually refused."""
    resp, svc = _call(monkeypatch, {"tier": TIER_FREE})

    assert _body(resp)["error_code"] == ErrorCode.WHALE_FOLLOW_LOCKED.value
    assert svc.calls == []


@pytest.mark.parametrize(
    "kwargs",
    [{"kind": "insider"}, {"ticker": ""}, {"ticker": "X" * 13}],
    ids=["bogus-kind", "empty-ticker", "overlong-ticker"],
)
def test_the_paywall_is_the_first_thing_a_locked_caller_sees(monkeypatch, kwargs):
    """Gate BEFORE validation: a locked caller with garbage input gets WHALE_FOLLOW_LOCKED,
    not INVALID_INPUT. The validators must not double as an oracle — the valid-kind list
    and the ticker rules are behind the paywall too. Pins the statement order in the
    handler, which a `_body`-only check could not."""
    resp, svc = _call(monkeypatch, {"tier": TIER_FREE}, **kwargs)

    body = _body(resp)
    assert body["error_code"] == ErrorCode.WHALE_FOLLOW_LOCKED.value
    assert resp.status_code == 403
    assert svc.calls == []


def test_locked_body_has_exactly_the_contract_keys_and_flat_details(monkeypatch):
    """Invariant #3 shape, pinned exactly: iOS `APIErrorResponse` decodes these five keys,
    and its `AnyCodable` decodes String/Int/Double/Bool only — a nested dict or list in
    `details` arrives as "" (auth.md §3)."""
    resp, _ = _call(monkeypatch, {"tier": TIER_FREE})

    body = _body(resp)
    assert set(body) == {"error_code", "message", "user_message", "action", "details"}
    details = body["details"]
    assert set(details) == {"tier_required", "kind"}
    for key, value in details.items():
        assert isinstance(value, (str, int, float, bool)), f"details[{key!r}] is {type(value).__name__}"


# ── 3. Unknown / missing / garbage tier falls CLOSED ─────────────────────────

@pytest.mark.parametrize(
    "identity",
    [
        _GUEST_IDENTITY,
        {"id": "u3", "email": "a@b.c"},          # degraded identity: no tier key at all
        {"tier": None},
        {"tier": ""},
        {"tier": "guest"},
        {"tier": "nonsense"},
        {"tier": "max"},                         # Max is spelled "premium"; a plausible misspelling must not unlock
        {"tier": "FREE_TRIAL"},
        {"tier": 42},                            # wrong type must not raise its way to a 500 either
        {"tier": ["pro"]},                       # a list containing a paid key is not a paid key
    ],
    ids=lambda i: repr(i.get("tier", "<missing>")),
)
def test_an_unrecognised_or_missing_tier_falls_closed(monkeypatch, identity):
    """`user.get("tier")` is None for a degraded identity dict and "free" for every guest.
    Anything the ladder does not recognise must land ON the paid surface, not through it —
    and must be refused before the service is consulted."""
    resp, svc = _call(monkeypatch, identity)

    body = _body(resp)
    assert resp.status_code == 403
    assert body["error_code"] == ErrorCode.WHALE_FOLLOW_LOCKED.value
    assert body["details"]["tier_required"] == TIER_PRO
    assert svc.calls == []


def test_the_guest_flag_alone_does_not_unlock_or_lock(monkeypatch):
    """The gate keys on TIER. `is_guest` is fail-safe-defaulted elsewhere (`whales.py` reads
    `user.get("is_guest", True)`), so a Pro identity that happens to carry a stale
    `is_guest: True` must still pass, and a Free one with the flag absent must still lock —
    the flag is not a second entitlement channel."""
    resp, svc = _call(monkeypatch, {"tier": TIER_PRO, "is_guest": True})
    assert isinstance(resp, SignalTickerDetailResponse)
    assert svc.calls == [(_KIND, _TICKER)]

    resp, svc = _call(monkeypatch, {"tier": TIER_FREE})
    assert _body(resp)["error_code"] == ErrorCode.WHALE_FOLLOW_LOCKED.value
    assert svc.calls == []


# ── 4. Both branches of the degrade path, for an ENTITLED caller ─────────────

def test_an_empty_holder_list_passes_through_as_an_honest_empty_state(monkeypatch):
    """The service's documented degradation is an empty `holders`, which iOS renders as an
    empty state. The handler must not "helpfully" turn that into an error — and must not
    confuse it with the paywall."""
    resp, svc = _call(monkeypatch, {"tier": TIER_PRO}, svc=_FakeSignalsService(holders=[]))

    assert isinstance(resp, SignalTickerDetailResponse)
    assert resp.holders == []
    assert svc.calls == [(_KIND, _TICKER)]


def test_a_transport_fault_surfaces_as_a_structured_error_not_a_paywall(monkeypatch):
    """A fault escaping the service is wrapped by `error_response_from_exception` with the
    step + ticker for the logs. It must NOT be reported as WHALE_FOLLOW_LOCKED — a Pro user
    told to upgrade because Supabase blinked is the wrong message on the wrong plan — and
    it must not escape as a raw exception (a bare 500 breaks invariant #3)."""
    resp, svc = _call(
        monkeypatch, {"tier": TIER_PRO}, svc=_FakeSignalsService(raises=RuntimeError("supabase down"))
    )

    body = _body(resp)
    assert svc.calls == [(_KIND, _TICKER)]
    assert body["error_code"] != ErrorCode.WHALE_FOLLOW_LOCKED.value
    assert resp.status_code != 403
    assert body["error_code"] and body["user_message"]
    assert body["details"]["step"] == "signal_detail"
    assert body["details"]["ticker"] == _TICKER


# ── 5. The signals-detail route actually consults the gate ───────────────────
#
# Sections 1-4 prove the handler behaves when awaited directly. This pins WHERE the gate
# lives — inside the route function's own body, as the test of an `if` — so that moving it
# somewhere the direct call cannot see (or calling the predicate and discarding the result)
# is caught by name. Bounded to the route's AST node, not a whole-file grep: the handler's
# docstring, the import line, and the router comment ALL contain every token, so a regex
# over the file passes on prose after the gate is deleted (testing.md rules 1 and 2).

def _signal_detail_route_functions() -> dict[str, ast.AST]:
    """Every `async def` in home.py whose route decorator path contains '/signals/'."""
    src = Path(inspect.getfile(home_endpoint)).read_text()
    tree = ast.parse(src)
    out: dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        for dec in node.decorator_list:
            # @router.get("/signals/{kind}/{ticker}", ...) — first positional arg is the path
            if not isinstance(dec, ast.Call) or not dec.args:
                continue
            first = dec.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                if "/signals/" in first.value:
                    out[node.name] = node
    return out


def _gate_calls_in_if_tests(fn: ast.AST) -> set[str]:
    """Names called inside the `test` expression of any `if` in the function body."""
    names: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        for n in ast.walk(node.test):
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                names.add(n.func.id)
    return names


def test_there_is_at_least_one_signal_detail_route():
    """Anti-vacuity: if the route is renamed or moved, the guard below would pass by
    finding nothing to check."""
    assert _signal_detail_route_functions(), (
        "no '/signals/' route found in home.py — this guard has gone vacuous, "
        "point it at wherever that route lives now"
    )


def test_every_signal_detail_route_branches_on_the_gate():
    """The predicate must be the TEST of an `if` in the route's own body. A bare
    `whale_detail_unlocked(...)` expression statement — evaluated and thrown away — would
    satisfy a name-only walk and gate nothing."""
    routes = _signal_detail_route_functions()
    assert routes, "no '/signals/' route found — nothing to check (see the test above)"
    offenders = []
    for name, node in routes.items():
        if "whale_detail_unlocked" not in _gate_calls_in_if_tests(node):
            offenders.append(name)

    assert not offenders, (
        f"signal-detail route(s) {offenders} do not branch on `whale_detail_unlocked` — the "
        "13F/congress holder list is served unguarded, which is the exact bypass the inline "
        "gate was added to close (see the handler's docstring in home.py). If the gate moved "
        "into a `Depends(...)`, update sections 1-4 to exercise it there and re-point this."
    )


def test_the_gate_is_the_first_statement_after_the_docstring():
    """Statement ORDER, pinned structurally: the gate precedes both validators, so a locked
    caller learns nothing from the error it gets. The behavioural version is
    `test_the_paywall_is_the_first_thing_a_locked_caller_sees`; this one names the line to
    move back if someone reorders the handler for readability."""
    routes = _signal_detail_route_functions()
    assert routes, "no '/signals/' route found — nothing to check (see the test above)"
    for name, node in routes.items():
        stmts = list(node.body)
        # Drop a leading docstring expression, if any.
        if stmts and isinstance(stmts[0], ast.Expr) and isinstance(getattr(stmts[0], "value", None), ast.Constant):
            stmts = stmts[1:]
        assert stmts, f"{name} has an empty body"
        first = stmts[0]
        assert isinstance(first, ast.If), f"{name}: first statement is {type(first).__name__}, not the tier gate"
        assert "whale_detail_unlocked" in {
            n.func.id
            for n in ast.walk(first.test)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }, f"{name}: the first `if` does not test `whale_detail_unlocked` — the gate has been reordered"
