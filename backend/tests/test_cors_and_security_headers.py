"""The browser-reachable half of the API: no credentialed wildcard, and baseline headers.

Verified against production on 2026-09-12: a preflight carrying `Origin: https://evil.example`
came back `access-control-allow-origin: https://evil.example` + `access-control-allow-credentials:
true`, because `ALLOWED_ORIGINS` defaults to `["*"]` (config.py) and Starlette resolves the
spec-illegal wildcard+credentials pair by echoing the caller's origin. And `GET /privacy` (a real
HTML page on the same host) carried no HSTS, no nosniff, no frame policy.
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from starlette.middleware.cors import CORSMiddleware

import app.main as main_mod
from app.config import settings


@pytest.fixture()
def client():
    # `lifespan` spawns the background jobs; TestClient without a context manager never
    # enters it, so no job runs here.
    return TestClient(main_mod.app)


def _cors_options(app):
    for m in app.user_middleware:
        if m.cls is CORSMiddleware:
            return m.kwargs
    raise AssertionError("CORSMiddleware is not installed")


def test_a_wildcard_origin_never_promises_credentials():
    opts = _cors_options(main_mod.app)
    if "*" in opts["allow_origins"]:
        assert opts["allow_credentials"] is False, (
            "wildcard + credentials makes Starlette echo ANY caller's Origin back with "
            "access-control-allow-credentials: true — every website becomes a trusted origin"
        )
    else:
        assert opts["allow_credentials"] is True, "an explicit origin list keeps credentials"


def test_the_clamp_is_computed_from_the_setting_not_hardcoded(monkeypatch):
    """Anti-vacuity + the real contract: an explicit list must restore credentials."""
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", ["https://caydexinvest.com"])
    reloaded = importlib.reload(main_mod)
    try:
        opts = _cors_options(reloaded.app)
        assert opts["allow_origins"] == ["https://caydexinvest.com"]
        assert opts["allow_credentials"] is True
    finally:
        monkeypatch.undo()
        importlib.reload(main_mod)


def test_a_cross_origin_preflight_is_not_answered_with_credentials(client):
    r = client.options(
        "/api/v1/billing/plans",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"},
    )
    assert r.headers.get("access-control-allow-credentials") != "true", dict(r.headers)


@pytest.mark.parametrize("path", ["/health/live", "/privacy"])
def test_every_response_carries_the_baseline_headers(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"


def test_hsts_is_sent_only_over_https(client):
    plain = client.get("/health/live")
    assert "strict-transport-security" not in {k.lower() for k in plain.headers}, \
        "HSTS over plain http is meaningless and would be ignored"
    # Railway terminates TLS and uvicorn runs with --proxy-headers, so in production
    # `request.url.scheme` is https. TestClient has no proxy layer; drive the scheme
    # directly through its base_url.
    secure_client = TestClient(main_mod.app, base_url="https://testserver")
    secure = secure_client.get("/health/live")
    assert "max-age=31536000" in secure.headers.get("strict-transport-security", "")
    assert "includeSubDomains" in secure.headers["strict-transport-security"]
