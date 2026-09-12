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


@pytest.fixture(autouse=True)
def _clear_catalog_cache():
    """The storefront catalogues are memoised process-wide for 120 s (they are public,
    unauthenticated routes that used to run a blocking SELECT per request). Tests install
    different fake tables, so the cache must not carry one test's rows into the next."""
    from app.services.subscription_service import reset_catalog_cache

    reset_catalog_cache()
    yield
    reset_catalog_cache()



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


# ── the two paths that never reach the middleware ───────────────────────────────────


def test_the_headers_are_defined_in_one_shared_helper():
    """`_security_headers` is a middleware, and TWO response paths never reach it:

      * `cap_json_body` returns its 413 WITHOUT calling `call_next`, so nothing downstream
        of it runs;
      * Starlette hoists `@app.exception_handler(Exception)` into `ServerErrorMiddleware`,
        which sits OUTSIDE every user middleware — so an unhandled 500 had no security
        headers, no CORS header and no `X-Request-ID`.

    That second one is exactly where a future HTML error page would live, i.e. the case the
    middleware's own docstring says it exists to cover. Both now call the shared helper, and
    this pins that they keep doing so.
    """
    import ast
    import inspect
    import re

    import app.main as main_mod

    src = inspect.getsource(main_mod)
    tree = ast.parse(src)

    def _code(name):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name)
        body = ast.get_source_segment(src, fn)
        return "\n".join(re.sub(r"#.*$", "", line) for line in body.splitlines())

    assert callable(getattr(main_mod, "_apply_security_headers", None))
    for fn_name in ("_security_headers", "cap_json_body", "general_handler"):
        assert "_apply_security_headers(" in _code(fn_name), (
            f"{fn_name} builds a response without the baseline security headers"
        )


def test_the_helper_sets_all_four_and_honours_the_scheme():
    import app.main as main_mod

    class _R:
        def __init__(self): self.headers = {}

    over_https = _R()
    main_mod._apply_security_headers(over_https, https=True)
    assert over_https.headers["X-Content-Type-Options"] == "nosniff"
    assert over_https.headers["X-Frame-Options"] == "DENY"
    assert over_https.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "max-age=" in over_https.headers["Strict-Transport-Security"]

    over_http = _R()
    main_mod._apply_security_headers(over_http, https=False)
    assert "Strict-Transport-Security" not in over_http.headers, (
        "HSTS over plain http is meaningless and can strand a local dev client"
    )


def test_an_unhandled_500_is_logged_with_the_method_path_and_request_id():
    """`add_process_time`'s request log never runs for a hoisted 500, so this handler is
    the ONLY record that the request happened. `Unhandled: {exc}` alone left no way to tell
    which request died (CLAUDE.md: errors carry context + identifiers)."""
    import ast
    import inspect
    import re

    import app.main as main_mod

    src = inspect.getsource(main_mod)
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
              and n.name == "general_handler")
    code = "\n".join(re.sub(r"#.*$", "", l) for l in ast.get_source_segment(src, fn).splitlines())
    for token in ("request.method", "request.url.path", "request_id", "exc_info=True"):
        assert token in code, f"the unhandled-500 log does not carry {token}"

