"""The Confirm-signup email's LINK page: `GET/POST /auth/confirm` (app/api/email_confirm_page.py).

Why it exists, in one breath: the email carries a code for this app AND a link for 1.02/1.03
installs (no code screen); Supabase's own `{{ .ConfirmationURL }}` would park a live Supabase
session in the browser's history (CC-02), so the link comes HERE with only `token_hash`, and the
backend redeems it server-side.

What is pinned:
  * GET NEVER calls GoTrue — mail scanners prefetch every link, and a consuming GET would spend
    the single-use token before the person tapped anything. It does not even resolve the auth
    client dependency.
  * `token_hash` is drawn only after it matched the hex pattern, only in the hidden field; any
    other value (including markup) gets the failure page and is never echoed. `type` must be
    exactly "signup".
  * POST outcomes: success / refused / outage pages, the session GoTrue returns never drawn.
  * Headers: no-store, no-referrer, landing CSP with form-action 'self', noindex.
  * The page stays inside marketing.md §8: no script, no external resource, code-owned copy.

Driven through the REAL app with FastAPI's TestClient (no lifespan: nothing here needs
startup state), the auth client overridden with a fake. No network.
"""
from __future__ import annotations

import html
import logging
import re
import warnings

import httpx
import pytest
from fastapi.testclient import TestClient

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)  # gotrue announces its own rename
    from gotrue.helpers import handle_exception

from app.api import email_confirm_page as page
from app.core.security import rate_limiter
from app.database import get_auth_client
from app.main import app

_HASH = "a1" * 28                       # 56 hex characters: a sha224 digest, GoTrue's shape
_SESSION_SECRET = "SB-SESSION-ACCESS-TOKEN-must-never-be-drawn"


def _gotrue_error(status: int, body: dict) -> Exception:
    req = httpx.Request("POST", "https://example.supabase.co/auth/v1/verify")
    resp = httpx.Response(status, json=body, request=req,
                          headers={"X-Supabase-Api-Version": "2024-01-01"})
    return handle_exception(httpx.HTTPStatusError("http error", request=req, response=resp))


class _User:
    def __init__(self, confirmed=True):
        self.id = "11112222-3333-4444-8555-666677778888"
        self.email = "new.user@example.com"
        self.email_confirmed_at = "2026-11-26T00:00:00Z" if confirmed else None


class _FakeAuthClient:
    """Stands in for `get_auth_client()`; `.auth` is itself so `auth_client.auth.verify_otp`
    resolves. `run_gotrue` finds no `_headers` on it and simply runs the verb."""

    def __init__(self, behaviour: str = "ok"):
        self.behaviour = behaviour
        self.calls: list[dict] = []
        self.auth = self

    def verify_otp(self, params):
        self.calls.append(dict(params))
        if self.behaviour == "expired":
            raise _gotrue_error(403, {"code": "otp_expired", "msg": "Email link is invalid or has expired"})
        if self.behaviour == "outage":
            raise handle_exception(httpx.ConnectError("connection refused"))
        if self.behaviour == "429":
            raise _gotrue_error(429, {"code": "over_request_rate_limit", "msg": "rate limited"})
        if self.behaviour == "5xx":
            raise _gotrue_error(500, {"code": "unexpected_failure", "msg": "db down"})
        if self.behaviour == "nouser":
            return type("R", (), {"user": None, "session": None})()
        if self.behaviour == "unconfirmed":
            return type("R", (), {"user": _User(confirmed=False), "session": None})()
        session = type("S", (), {"access_token": _SESSION_SECRET, "refresh_token": "SB-REFRESH"})()
        return type("R", (), {"user": _User(), "session": session})()


@pytest.fixture(autouse=True)
def _reset():
    rate_limiter.clear()
    yield
    app.dependency_overrides.pop(get_auth_client, None)
    rate_limiter.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _use(fake: _FakeAuthClient) -> _FakeAuthClient:
    app.dependency_overrides[get_auth_client] = lambda: fake
    return fake


def _shows(copy: str, body: str) -> bool:
    """Copy is drawn HTML-escaped (an apostrophe becomes &#x27;)."""
    return html.escape(copy) in body


def _assert_page_headers(r):
    assert r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["referrer-policy"] == "no-referrer", "the URL carries the token hash"
    csp = r.headers["content-security-policy"]
    assert "form-action 'self'" in csp and "default-src 'none'" in csp
    assert "script-src" not in csp
    assert "noindex" in r.headers["x-robots-tag"]


def _assert_static(body: str):
    """marketing.md §8 for the passkey domain: no script, no external resource, no leftovers."""
    assert "<script" not in body.lower()
    assert not re.search(r'(src|href|action)\s*=\s*"(https?:)?//', body), "external resource"
    assert "{{" not in body, "an unfilled template slot"


# ── GET: a static button, never a consumption ─────────────────────────────────

@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_get_never_calls_gotrue_nor_resolves_the_auth_client(client, method):
    resolved = []

    def _tripwire():
        resolved.append(True)
        return _FakeAuthClient()

    app.dependency_overrides[get_auth_client] = _tripwire
    r = client.request(method, f"/auth/confirm?token_hash={_HASH}&type=signup")
    assert r.status_code == 200
    assert resolved == [], "the GET must not even resolve a client that could redeem the token"
    _assert_page_headers(r)


def test_get_draws_the_confirm_form_with_the_validated_hash(client):
    r = client.get(f"/auth/confirm?token_hash={_HASH}&type=signup")
    body = r.text
    assert r.status_code == 200
    assert '<form method="post" action="/auth/confirm">' in body
    assert f'name="token_hash" value="{_HASH}"' in body
    assert 'name="type" value="signup"' in body
    assert page.BUTTON_LABEL in body
    assert _shows(page.COPY_PROMPT_NOTE, body)
    _assert_static(body)


@pytest.mark.parametrize("query", [
    "",                                                    # no parameters
    f"token_hash={_HASH}",                                 # no type
    "type=signup",                                         # no hash
    f"token_hash={_HASH}&type=email",                      # "email" also matches a RECOVERY token
    f"token_hash={_HASH}&type=recovery",
    f"token_hash={_HASH}&type=SIGNUP",
    "token_hash=abc123&type=signup",                       # too short
    f"token_hash={'a' * 129}&type=signup",                 # too long
    "token_hash=zzzzzzzzzzzzzzzzzzzzzzzzzz&type=signup",   # not hex
    'token_hash=%22%3E%3Cscript%3Ealert(1)%3C%2Fscript%3E&type=signup',
    f"token_hash={_HASH}&token_hash={'b2' * 28}&type=signup",   # repeated: never "pick one"
    f"token_hash={_HASH}&type=signup&type=signup",
])
def test_get_with_a_bad_link_shows_the_failure_page_and_never_echoes(client, query):
    r = client.get(f"/auth/confirm?{query}")
    assert r.status_code == 400
    assert _shows(page.COPY_FAILURE, r.text)
    assert "<form" not in r.text
    assert "alert(1)" not in r.text and "zzzzzzzz" not in r.text
    _assert_page_headers(r)
    _assert_static(r.text)


# ── POST: redeem server-side, discard the session ─────────────────────────────

def test_post_redeems_a_signup_token_hash_and_draws_success(client, caplog):
    fake = _use(_FakeAuthClient())
    with caplog.at_level(logging.INFO):
        r = client.post("/auth/confirm", data={"token_hash": _HASH, "type": "signup"})
    assert r.status_code == 200
    assert _shows(page.COPY_SUCCESS, r.text)
    assert fake.calls == [{"token_hash": _HASH, "type": "signup"}]
    assert _SESSION_SECRET not in r.text and "SB-REFRESH" not in r.text, "session must be discarded"
    _assert_page_headers(r)
    _assert_static(r.text)
    for rec in caplog.records:
        assert _HASH not in rec.getMessage() and _SESSION_SECRET not in rec.getMessage()


def test_post_with_a_spent_or_expired_token_draws_the_neutral_failure(client, caplog):
    """A scanner or a second tap may have spent it already — the copy says 'if you already
    confirmed, just sign in', and the refusal is INFO, not an incident."""
    _use(_FakeAuthClient("expired"))
    with caplog.at_level(logging.INFO):
        r = client.post("/auth/confirm", data={"token_hash": _HASH, "type": "signup"})
    assert r.status_code == 400
    assert "If you already confirmed, just sign in" in _visible_text(r.text)
    assert "<form" not in r.text
    assert not [x for x in caplog.records if x.levelno >= logging.ERROR]


def _visible_text(body: str) -> str:
    """What a person reads: tags stripped, entities decoded, whitespace folded."""
    no_style = re.sub(r"<style.*?</style>|<!--.*?-->", " ", body, flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", no_style)).split())


def test_the_failure_page_points_old_builds_at_the_newest_email(client):
    """COMPAT-1: this link exists for 1.0–1.03, which re-send a confirmation email on every
    unconfirmed sign-in — so the dead link is usually an OLDER email's. The page names the
    newest email, keeps "already confirmed → sign in", and names no control those builds lack
    (they have no code screen: "send a new code" was a dead end). Pinned on the RENDERED page."""
    _use(_FakeAuthClient("expired"))
    text = _visible_text(client.post("/auth/confirm", data={"token_hash": _HASH, "type": "signup"}).text)
    assert "Each new email from Caydex replaces the earlier ones" in text
    assert "open the newest one and tap its button" in text
    assert "If you already confirmed, just sign in to the Caydex app" in text
    assert "sign in to get a new confirmation email" in text
    assert "send a new code" not in text.lower() and "code" not in text.lower()


def test_the_success_page_names_forgot_password(client):
    """BE-AUTH-02: the link confirms on the token alone and keeps the FIRST registrant's password,
    so someone who re-registered with a newer one (CC-03) is told the way out."""
    _use(_FakeAuthClient())
    text = _visible_text(client.post("/auth/confirm", data={"token_hash": _HASH, "type": "signup"}).text)
    assert "Your email is confirmed. Open the Caydex app and sign in." in text
    assert "If your password doesn't work, use Forgot password in the app." in text


def test_the_module_no_longer_claims_nothing_is_logged():
    """The URL carries the hash and our edge may keep it: the docstring must not promise more."""
    doc = page.__doc__ or ""
    assert "nothing about the token, the session or the address is logged" not in doc
    assert "Railway" in doc


@pytest.mark.parametrize("behaviour, marker", [("outage", None), ("5xx", None), ("429", "GOTRUE_RATE_LIMITED")])
def test_post_upstream_failure_offers_the_button_again(client, caplog, behaviour, marker):
    """Ours, not theirs: the token is very likely still good, so the button comes back."""
    _use(_FakeAuthClient(behaviour))
    with caplog.at_level(logging.INFO):
        r = client.post("/auth/confirm", data={"token_hash": _HASH, "type": "signup"})
    assert r.status_code == 503
    assert _shows(page.COPY_UNAVAILABLE, r.text)
    assert f'name="token_hash" value="{_HASH}"' in r.text
    errors = [x.getMessage() for x in caplog.records if x.levelno >= logging.ERROR]
    assert errors
    if marker:
        assert any(marker in m for m in errors)


@pytest.mark.parametrize("behaviour, status", [("nouser", 400), ("unconfirmed", 503)])
def test_post_anomalous_verify_answers_never_claim_success(client, behaviour, status):
    _use(_FakeAuthClient(behaviour))
    r = client.post("/auth/confirm", data={"token_hash": _HASH, "type": "signup"})
    assert r.status_code == status
    assert not _shows(page.COPY_SUCCESS, r.text)


@pytest.mark.parametrize("kwargs", [
    {"data": {"token_hash": _HASH, "type": "email"}},
    {"data": {"token_hash": _HASH, "type": "recovery"}},
    {"data": {"token_hash": "not-hex!", "type": "signup"}},
    {"data": {"type": "signup"}},
    {"data": {"token_hash": _HASH}},
    {"json": {"token_hash": _HASH, "type": "signup"}},                       # not our form
    {"content": f"token_hash={_HASH}&type=signup",
     "headers": {"content-type": "text/plain"}},                            # right bytes, wrong type
    {"files": {"token_hash": ("x", _HASH.encode())}, "data": {"type": "signup"}},   # multipart
    {"content": f"token_hash={_HASH}&type=signup&pad={'x' * 4000}",
     "headers": {"content-type": "application/x-www-form-urlencoded"}},     # over the byte cap
    {"content": f"token_hash={_HASH}&token_hash={_HASH}&type=signup",
     "headers": {"content-type": "application/x-www-form-urlencoded"}},     # repeated field
    {"content": b"token_hash=\xff\xfe&type=signup",
     "headers": {"content-type": "application/x-www-form-urlencoded"}},     # not ASCII
])
def test_post_refuses_anything_but_our_exact_form_without_calling_gotrue(client, kwargs):
    fake = _use(_FakeAuthClient())
    r = client.post("/auth/confirm", **kwargs)
    assert r.status_code == 400
    assert fake.calls == [], "GoTrue must never see an unvalidated value"
    assert not _shows(page.COPY_SUCCESS, r.text)


def test_post_is_rate_limited_per_ip_before_gotrue(client):
    fake = _use(_FakeAuthClient("expired"))
    for _ in range(page.POST_LIMIT_MAX):
        assert client.post("/auth/confirm", data={"token_hash": _HASH, "type": "signup"}).status_code == 400
    calls = len(fake.calls)
    r = client.post("/auth/confirm", data={"token_hash": _HASH, "type": "signup"})
    assert r.status_code == 429
    assert r.headers["retry-after"] == str(page.POST_LIMIT_WINDOW_SECONDS)
    assert _shows(page.COPY_RATE_LIMITED, r.text)
    assert len(fake.calls) == calls
    _assert_page_headers(r)


# ── The template ──────────────────────────────────────────────────────────────

def test_a_missing_template_still_serves_a_working_page(client, monkeypatch, tmp_path, caplog):
    """Without this page nobody on 1.02/1.03 can confirm: a packaging fault degrades, loudly."""
    monkeypatch.setattr(page, "_TEMPLATE_PATH", tmp_path / "missing.html")
    with caplog.at_level(logging.ERROR, logger="app.api.email_confirm_page"):
        r = client.get(f"/auth/confirm?token_hash={_HASH}&type=signup")
    assert r.status_code == 200 and _shows(page.BUTTON_LABEL, r.text)
    assert "{{" not in r.text
    assert [x for x in caplog.records if "template unreadable" in x.getMessage()]


def test_the_shipped_template_has_every_slot_and_no_script():
    text = page._TEMPLATE_PATH.read_text(encoding="utf-8")
    for slot in page._SLOTS:
        assert slot in text
    assert "<script" not in text.lower()
    assert not re.search(r'(src|href)\s*=\s*"(https?:)?//', text)


def test_the_link_validator_is_strict():
    assert page.is_valid_link(_HASH, "signup")
    assert page.is_valid_link("A" * 20, "signup") and not page.is_valid_link("A" * 19, "signup")
    assert page.is_valid_link("f" * 128, "signup") and not page.is_valid_link("f" * 129, "signup")
    assert not page.is_valid_link(_HASH + "\n", "signup"), "fullmatch, not match: no trailing newline"
    assert not page.is_valid_link(None, "signup") and not page.is_valid_link(_HASH, None)


def test_the_post_forwards_the_callers_address_to_gotrue_and_the_get_does_not_need_to():
    """SEC-7: the POST is the only verb here that reaches GoTrue; it carries the dependency that
    binds the trusted client address for `run_gotrue`."""
    from app.dependencies import forward_client_ip_to_gotrue

    deps = {}
    for r in app.routes:
        if getattr(r, "path", None) == "/auth/confirm":
            deps[frozenset(r.methods)] = [d.call for d in r.dependant.dependencies]
    post = next(v for k, v in deps.items() if "POST" in k)
    assert forward_client_ip_to_gotrue in post
