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
import re
from pathlib import Path

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

# DERIVED, not hand-listed. This used to be six literals, which meant a newly added AUTH_* code
# was silently exempt from every test in this file — the wire-shape check, the flat-scalar
# check, the WWW-Authenticate check and the action check all iterate this list. Deriving it
# means a new code is covered the moment it exists.
_AUTH_CODES = [code for code in ErrorCode if code.value.startswith("AUTH_")]

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


# The button each auth code puts in front of the user. Spelled out per code rather than
# derived from the status, because "401" does not imply "sign in": AUTH_CREDENTIALS_INVALID is
# a 401 raised while the user is looking at the very form they just submitted, and
# AUTH_PROVIDER_FAILED is a 401 for a handshake that may simply succeed on retry. Offering
# "Sign In" for either is the same circle the 403 rule below was written to prevent.
_EXPECTED_ACTIONS = {
    # The session is over; re-authenticating is the only way forward.
    ErrorCode.AUTH_REQUIRED: "sign_in",
    ErrorCode.AUTH_TOKEN_INVALID: "sign_in",
    ErrorCode.AUTH_SESSION_EXPIRED: "sign_in",
    ErrorCode.AUTH_ACCOUNT_NOT_FOUND: "sign_in",
    # Authenticated but not permitted — signing in again cannot help.
    ErrorCode.AUTH_FORBIDDEN: "contact_support",
    # Our outage, not their credential.
    ErrorCode.AUTH_UNAVAILABLE: "retry_later",
    # What they typed was wrong; the fix is in the field, not in a new session.
    ErrorCode.AUTH_CREDENTIALS_INVALID: "fix_input",
    ErrorCode.AUTH_PROVIDER_FAILED: "retry_later",
}


def test_every_auth_code_declares_an_expected_action():
    """Forces a deliberate decision when an AUTH_* code is added, instead of inheriting one."""
    missing = [c.value for c in _AUTH_CODES if c not in _EXPECTED_ACTIONS]
    assert not missing, f"add these to _EXPECTED_ACTIONS with a reason: {missing}"


@pytest.mark.parametrize("code", _AUTH_CODES, ids=lambda c: c.value)
def test_auth_action_matches_the_client_affordance(code):
    """`action` drives the iOS button, so a wrong one is a dead end for the user."""
    assert auth_error(code, message="m").detail["action"] == _EXPECTED_ACTIONS[code]


def test_only_session_failures_offer_sign_in():
    """The original rule, kept: "Sign In" must appear ONLY where a new session actually helps.

    Offering it on a 403 sends an already-signed-in user in a circle, on a transient 503 it
    blames them for our outage, and on a wrong-password 401 it points at the screen they are
    already on.
    """
    sign_in_codes = {c for c, a in _EXPECTED_ACTIONS.items() if a == "sign_in"}
    for code in sign_in_codes:
        assert _DEFAULT_STATUS[code] == 401, f"{code.value} offers sign_in but is not a 401"
    assert ErrorCode.AUTH_CREDENTIALS_INVALID not in sign_in_codes
    assert ErrorCode.AUTH_PROVIDER_FAILED not in sign_in_codes


def test_auth_py_raises_no_bare_string_401s():
    """`auth.py` must go through `auth_error(...)`, never `HTTPException(401, detail="...")`.

    A string detail renders as `{"detail": ...}`, which iOS cannot decode against
    `APIErrorResponse` — it falls back to `APIError.unauthorized`, whose copy is hardcoded to
    "Your session has expired." Nine sites shipped that way and told users things that were
    simply untrue: a wrong current password read as an expired session, and a failed Apple
    sign-in read as "Email or password is incorrect" for a flow with no password in it.

    Scoped to 401s on purpose — the 400/429/500 raises in this file are fine as strings, and
    the `HTTPException` handler stays deliberately narrow for the ~100 such raises elsewhere.
    """
    src = (Path(__file__).resolve().parents[1] / "app" / "api" / "v1" / "endpoints" / "auth.py")
    offenders = []
    lines = src.read_text().splitlines()
    for i, raw in enumerate(lines, 1):
        # Strip trailing comments — the prose above names the retired pattern, and this suite
        # has twice failed on its own explanation of a bug.
        line = raw.split("#", 1)[0]
        if "HTTPException" not in line:
            continue
        # The status may sit on the same line or the next one (black wraps these).
        window = " ".join(l.split("#", 1)[0] for l in lines[i - 1:i + 2])
        if re.search(r"status_code\s*=\s*401|HTTPException\(\s*401", window):
            offenders.append(f"auth.py:{i}: {raw.strip()}")

    assert not offenders, (
        "use auth_error(ErrorCode.AUTH_*, ...) so iOS can decode the reason:\n  "
        + "\n  ".join(offenders)
    )


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
