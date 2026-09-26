"""
`GET /api/v1/search/trending` and `POST /api/v1/search/picks`.

  • Both are account-only (auth.md §1a): no credential → 401 AUTH_REQUIRED, never a list.
  • GET returns whatever the impersonal service computed and cannot error.
  • POST answers 204 in EVERY case — counted, invalid, or a failing service — so the app
    can fire and forget, and the answer reveals nothing about what the server knows.
  • The account comes from the token only; a `user_id` in the body is ignored.

Hermetic: the services are stubbed; the lifespan never runs.
"""

from __future__ import annotations

import logging
from typing import Any, List, Tuple

import pytest
from fastapi.testclient import TestClient

import app.api.v1.endpoints.search as search_ep
from app.dependencies import get_current_user_id
from app.main import app
from app.schemas.search_trending import (
    SearchTrendingItemResponse,
    SearchTrendingResponse,
    SearchTrendingSectionResponse,
)

_TRENDING = "/api/v1/search/trending"
_PICKS = "/api/v1/search/picks"


@pytest.fixture
def client():
    logging.disable(logging.CRITICAL)
    try:
        yield TestClient(app)
    finally:
        logging.disable(logging.NOTSET)


@pytest.fixture
def signed_in():
    app.dependency_overrides[get_current_user_id] = lambda: "acct-from-token"
    yield
    app.dependency_overrides.pop(get_current_user_id, None)


class _Picks:
    def __init__(self, raises: bool = False):
        self.calls: List[Tuple[Any, Any, Any]] = []
        self.raises = raises

    async def record_pick(self, account, symbol, asset_type):
        self.calls.append((account, symbol, asset_type))
        if self.raises:
            raise RuntimeError("service bug")
        return "counted"


@pytest.mark.parametrize("method,path", [("GET", _TRENDING), ("POST", _PICKS)])
def test_no_credential_is_refused_with_auth_required(client, method, path):
    response = client.request(method, path, json={"symbol": "AAPL"} if method == "POST" else None)
    assert response.status_code == 401, response.text
    assert response.json().get("error_code") == "AUTH_REQUIRED"


def test_the_router_gates_every_route_by_default():
    names = {d.dependency.__name__ for d in search_ep.router.dependencies}
    assert "get_current_user_id" in names


def test_get_returns_the_service_answer(client, signed_in, monkeypatch):
    expected = SearchTrendingResponse(
        computed_at="2026-09-26T14:00:00+00:00",
        sections=[SearchTrendingSectionResponse(
            kind="trending_searches",
            items=[SearchTrendingItemResponse(symbol="NVDA", name="NVIDIA", type="stock")],
        )],
    )

    class _Svc:
        async def get_trending(self):
            return expected

    monkeypatch.setattr(search_ep, "get_search_trending_service", lambda: _Svc())
    response = client.get(_TRENDING)
    assert response.status_code == 200, response.text
    assert response.json() == expected.model_dump()


@pytest.mark.parametrize("body", [
    {"symbol": "AAPL", "type": "stock"},
    {"symbol": "AVGOP", "type": "stock"},
    {"symbol": "zzz", "type": "bogus"},
])
def test_post_is_always_204(client, signed_in, monkeypatch, body):
    picks = _Picks()
    monkeypatch.setattr(search_ep, "get_search_pick_service", lambda: picks)
    response = client.post(_PICKS, json=body)
    assert response.status_code == 204 and response.content == b""
    assert picks.calls == [("acct-from-token", body["symbol"], body["type"])]


def test_post_is_204_even_when_the_service_fails(client, signed_in, monkeypatch):
    """`record_pick` never raises by contract; if it ever did, the tap must still not error
    — a 500 here would surface as a failure on a search screen for a pure side effect."""
    picks = _Picks(raises=True)
    monkeypatch.setattr(search_ep, "get_search_pick_service", lambda: picks)
    response = client.post(_PICKS, json={"symbol": "AAPL"})
    assert response.status_code == 204 and picks.calls


def test_the_account_comes_from_the_token_not_the_body(client, signed_in, monkeypatch):
    picks = _Picks()
    monkeypatch.setattr(search_ep, "get_search_pick_service", lambda: picks)
    client.post(_PICKS, json={"symbol": "AAPL", "user_id": "someone-else", "account": "x"})
    assert picks.calls[0][0] == "acct-from-token"


@pytest.mark.parametrize("body", [{}, {"symbol": ""}, {"symbol": "A" * 17},
                                  {"symbol": "AAPL", "type": "x" * 17}])
def test_malformed_bodies_are_422(client, signed_in, monkeypatch, body):
    picks = _Picks()
    monkeypatch.setattr(search_ep, "get_search_pick_service", lambda: picks)
    assert client.post(_PICKS, json=body).status_code == 422
    assert picks.calls == []
