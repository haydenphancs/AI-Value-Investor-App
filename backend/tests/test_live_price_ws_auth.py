"""The live-price WebSocket must refuse an unauthenticated connection.

WHY THIS FILE EXISTS
--------------------
`WEBSOCKET /ws/price/{ticker}` accepted anonymous connections in production — `token` was
optional and an absent one produced `user_id = None`, which the handler treated as a guest
session. Verified live against caydexinvest.com: a client with no token connected fine.

It survived the whole account-only redesign because **no existing guard can see it**:

  * `tests/test_ios_auth_policy_parity.py` scans for `@router.get/post/put/patch/delete`
    and never matches `@router.websocket`.
  * `APIEndpoint.authPolicy` governs the REST path through `APIClient`; the socket is
    opened directly by `LivePriceWebSocketManager` and never passes through it.
  * The account-only work gated the nine market-data modules with
    `APIRouter(dependencies=[...])`; `live_price.py` has no such router dependency, and a
    router-level `Depends` does not apply to a websocket route anyway.

It matters because the payload is FMP-derived price data and the signed Order Form grants
End-User Display Rights only — their data may be shown solely "through the Licensee's
authenticated platform". The crypto carve-out that justified the exception is moot: FMP
answers 402 for every `…USD` pair now.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import app.api.v1.endpoints.live_price as lp

SOURCE = pathlib.Path(lp.__file__)


class _FakeWS:
    """Records what the handler did, so 'refused BEFORE accept' is observable."""

    def __init__(self):
        self.accepted = False
        self.closed_with: tuple | None = None
        self.sent: list = []

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=""):
        self.closed_with = (code, reason)

    async def send_json(self, payload):
        self.sent.append(payload)

    async def receive_text(self):
        raise AssertionError("must never reach the receive loop")


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "", "not-a-jwt", "eyJhbGciOiJIUzI1NiJ9.garbage.sig"])
async def test_a_connection_without_a_usable_token_is_refused(monkeypatch, token):
    monkeypatch.setattr(lp, "_validate_ws_token", lambda t: None)
    ws = _FakeWS()
    await lp.live_price_ws(ws, "AAPL", token=token)

    assert ws.closed_with is not None, "the socket must be closed"
    assert ws.closed_with[0] == 1008, "policy-violation close code"
    assert not ws.accepted, (
        "the refusal must happen BEFORE accept() — accepting first would hold a "
        "connection slot for an unauthenticated caller and let the per-key cap be "
        "exhausted by anyone"
    )
    assert ws.sent == [], "nothing may be streamed to an unauthenticated caller"


@pytest.mark.asyncio
async def test_the_refusal_does_not_reveal_whether_the_token_parsed(monkeypatch):
    """Same close reason for absent and malformed — otherwise it is an oracle."""
    monkeypatch.setattr(lp, "_validate_ws_token", lambda t: None)
    absent, malformed = _FakeWS(), _FakeWS()
    await lp.live_price_ws(absent, "AAPL", token=None)
    await lp.live_price_ws(malformed, "AAPL", token="garbage")
    assert absent.closed_with == malformed.closed_with


@pytest.mark.asyncio
async def test_a_valid_token_is_accepted(monkeypatch):
    """The gate must not be so tight it refuses a real user."""
    monkeypatch.setattr(lp, "_validate_ws_token", lambda t: "user-123")
    monkeypatch.setattr(lp, "is_market_active", lambda: False)  # short-circuit early
    lp._active_connections.clear()

    ws = _FakeWS()
    await lp.live_price_ws(ws, "AAPL", token="good-token")
    assert ws.accepted, "an authenticated caller must get through"
    assert ws.sent and ws.sent[0]["type"] == "market_closed"


@pytest.mark.asyncio
async def test_a_crypto_pair_is_no_longer_a_public_stream(monkeypatch):
    """The carve-out that justified anonymous access is gone.

    It was 'crypto is 24/7 public data'. FMP now answers 402 for every `…USD` pair, so
    there is no public stream left to justify the hole.
    """
    monkeypatch.setattr(lp, "_validate_ws_token", lambda t: None)
    ws = _FakeWS()
    await lp.live_price_ws(ws, "BTCUSD", token=None)
    assert ws.closed_with[0] == 1008
    assert not ws.accepted


def test_every_websocket_route_authenticates_before_accept():
    """Anti-regression for the CLASS of bug, not just this one route.

    A websocket route is invisible to every other auth guard in the suite, so a new one
    could reintroduce exactly this hole with nothing failing. Requires each
    `@router.websocket` handler to reference the token validator and to close on the
    failure path before any `accept()`.
    """
    tree = ast.parse(SOURCE.read_text())
    ws_handlers = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute)
            and d.func.attr == "websocket"
            for d in n.decorator_list
        )
    ]
    assert ws_handlers, "extractor found no websocket routes — it is broken"

    for fn in ws_handlers:
        names = {
            n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_validate_ws_token" in names, (
            f"{fn.name} does not validate a token. A websocket route is seen by NO other "
            "auth guard in this suite — not the iOS policy parity scan, not APIClient, not "
            "a router-level Depends. It has to gate itself."
        )
        # The validator must be consulted before the socket is accepted.
        accepts = [
            n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "accept"
        ]
        validates = [
            n.lineno for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_validate_ws_token"
        ]
        if accepts and validates:
            assert min(validates) < min(accepts), (
                f"{fn.name} accepts the socket before validating the token — an "
                "unauthenticated caller would hold a connection slot."
            )
