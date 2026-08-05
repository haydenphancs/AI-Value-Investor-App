"""The wire shape of an auth rejection, and the handler that emits it.

Auth errors are raised from inside a DEPENDENCY, before any handler exists to
`return make_error_response(...)`. So they travel as `HTTPException(detail=<dict>)` and the
`StarletteHTTPException` handler in `app/main.py` emits that dict verbatim. Two things have to
hold for that to be safe, and both are pinned here:

  1. the body matches the iOS `APIErrorResponse` decoder exactly (CLAUDE.md invariant #3), and
  2. a plain-STRING detail is still rendered `{"detail": ...}` — byte-identical to FastAPI's
     own handler — because ~100 existing raise sites pass a string and `APIClient` already has
     per-status behaviour keyed off those shapes.
"""

import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.error_response import (
    ErrorCode,
    _DEFAULT_ACTIONS,
    _DEFAULT_STATUS,
    _USER_MESSAGES,
    auth_error,
)
from app.main import http_exception_handler

_AUTH_CODES = [
    ErrorCode.AUTH_REQUIRED,
    ErrorCode.AUTH_TOKEN_INVALID,
    ErrorCode.AUTH_SESSION_EXPIRED,
    ErrorCode.AUTH_ACCOUNT_NOT_FOUND,
    ErrorCode.AUTH_FORBIDDEN,
    ErrorCode.AUTH_UNAVAILABLE,
]

# Exactly what `APIErrorResponse.init(from:)` reads (Core/Services/APIClient.swift).
_REQUIRED_KEYS = {"error_code", "message", "user_message"}
_OPTIONAL_KEYS = {"action", "details"}


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/x", "headers": []})


async def _render(exc: HTTPException):
    response = await http_exception_handler(_request(), exc)
    return response.status_code, json.loads(bytes(response.body))


# ── registry completeness ────────────────────────────────────────────────────

def test_every_error_code_has_copy_and_a_status():
    """A code with no `_USER_MESSAGES` entry silently falls back to "Something went wrong",
    which defeats the point of having a specific code at all."""
    for code in ErrorCode:
        assert code in _DEFAULT_STATUS, f"{code.value} has no default status"
    for code in _AUTH_CODES:
        assert code in _USER_MESSAGES, f"{code.value} has no user-facing copy"
        assert code in _DEFAULT_ACTIONS, f"{code.value} has no suggested action"


def test_every_ErrorCode_reference_in_the_app_resolves():
    """`billing.py` referenced `ErrorCode.UNAUTHORIZED`, which does not exist — so that line
    raised AttributeError at request time and the global Exception handler turned a guest's
    purchase attempt into a 500. A typo'd code name is invisible until the branch is hit, so
    scan for it instead of hoping."""
    import re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    valid = {c.name for c in ErrorCode}
    bad: list[str] = []
    for py in app_dir.rglob("*.py"):
        for lineno, line in enumerate(py.read_text().splitlines(), 1):
            # Comments only NAME codes (including retired ones, to explain a fix); they cannot
            # raise AttributeError. Strip them so prose doesn't fail the scan.
            code_part = line.split("#", 1)[0]
            for m in re.finditer(r"\bErrorCode\.([A-Z_][A-Z0-9_]*)\b", code_part):
                if m.group(1) not in valid:
                    bad.append(f"{py.relative_to(app_dir)}:{lineno} ErrorCode.{m.group(1)}")
    assert not bad, "references to non-existent ErrorCode members: " + "; ".join(bad)


# ── the contract body ────────────────────────────────────────────────────────

@pytest.mark.parametrize("code", _AUTH_CODES, ids=lambda c: c.value)
@pytest.mark.asyncio
async def test_auth_error_body_matches_the_ios_decoder(code):
    status, body = await _render(auth_error(code, message="technical detail"))

    assert status == _DEFAULT_STATUS[code]
    assert _REQUIRED_KEYS <= body.keys()
    assert set(body.keys()) <= (_REQUIRED_KEYS | _OPTIONAL_KEYS), body.keys()
    assert body["error_code"] == code.value
    assert body["user_message"], "user_message drives what the user actually reads"


@pytest.mark.parametrize("code", _AUTH_CODES, ids=lambda c: c.value)
def test_auth_details_are_flat_scalars(code):
    """iOS `AnyCodable` decodes String/Int/Double/Bool only, and silently yields "" for
    anything else — so a nested dict or list in `details` reaches the client as garbage rather
    than as an error. Keep the auth payloads flat."""
    exc = auth_error(code, message="m", details={"reason": "expired", "attempt": 1})
    for key, value in exc.detail["details"].items():
        assert isinstance(value, (str, int, float, bool)), f"{key} is {type(value).__name__}"


def test_every_details_literal_in_the_app_is_flat_scalars():
    """Static scan of every `details={...}` in backend/app.

    iOS `AnyCodable` (APIClient.swift) tries String → Int → Double → Bool and falls through to
    `value = ""` for anything else, WITHOUT throwing. So a list in `details` does not surface as
    a decode error — the hint simply arrives empty and nobody finds out. Two live sites shipped
    that way: `ticker_report.py` and `home.py` both passed `sorted(...)`.

    `test_auth_details_are_flat_scalars` above cannot catch this — it builds its own flat dict
    and asserts those literals are scalars, and `auth_error` never transforms `details`, so it
    is a tautology. This reads the real call sites instead.
    """
    import re
    from pathlib import Path

    app_dir = Path(__file__).resolve().parents[1] / "app"
    offenders: list[str] = []
    pattern = re.compile(r"details=\{([^{}]*)\}")

    for path in sorted(app_dir.rglob("*.py")):
        for lineno, raw in enumerate(path.read_text().splitlines(), 1):
            # Strip trailing comments — prose naming the retired pattern must not fail its own
            # test. That has bitten the source-scan tests in this suite before.
            line = raw.split("#", 1)[0]
            for match in pattern.finditer(line):
                body = match.group(1)
                if re.search(r":\s*(\[|sorted\(|list\(|set\(|tuple\()", body):
                    offenders.append(
                        f"{path.relative_to(app_dir.parent)}:{lineno}: {body.strip()}"
                    )

    assert not offenders, (
        "`details` values must be flat scalars — iOS AnyCodable silently renders anything "
        "else as an empty string:\n  " + "\n  ".join(offenders)
    )


def test_401s_carry_WWW_Authenticate():
    for code in _AUTH_CODES:
        exc = auth_error(code, message="m")
        if _DEFAULT_STATUS[code] == 401:
            assert exc.headers.get("WWW-Authenticate") == "Bearer", code.value
        else:
            assert not (exc.headers or {}).get("WWW-Authenticate"), code.value


def test_sign_in_action_only_on_the_401s():
    """`action` drives the iOS button. Offering "Sign In" on a 403 sends an already-signed-in
    user in a circle, and on a transient 503 it blames the user for our outage."""
    for code in _AUTH_CODES:
        action = auth_error(code, message="m").detail["action"]
        if _DEFAULT_STATUS[code] == 401:
            assert action == "sign_in", f"{code.value} -> {action}"
        else:
            assert action != "sign_in", f"{code.value} -> {action}"


# ── the handler must not disturb existing raises ─────────────────────────────

@pytest.mark.asyncio
async def test_string_detail_is_rendered_exactly_as_fastapi_did():
    """The narrow rule that makes this change additive. ~100 raise sites pass a plain string,
    and `APIClient.validateResponse` already keys per-status behaviour off those shapes — a
    blanket reshape would have silently altered every one of them."""
    status, body = await _render(HTTPException(status_code=404, detail="Ticker not found"))
    assert status == 404
    assert body == {"detail": "Ticker not found"}


@pytest.mark.asyncio
async def test_dict_detail_passes_through_verbatim():
    payload = {
        "error_code": "AUTH_REQUIRED",
        "message": "m",
        "user_message": "u",
        "action": "sign_in",
        "details": {},
    }
    status, body = await _render(HTTPException(status_code=401, detail=payload))
    assert status == 401
    assert body == payload


@pytest.mark.asyncio
async def test_headers_survive_the_handler():
    """`Retry-After` on a 429 and `WWW-Authenticate` on a 401 are part of the contract; a
    handler that rebuilt the response without them would drop both."""
    exc = HTTPException(status_code=429, detail="slow down", headers={"Retry-After": "60"})
    response = await http_exception_handler(_request(), exc)
    assert response.headers.get("Retry-After") == "60"
