"""A password the provider REFUSES TO SET must reach the user as an actionable message.

WHY THIS FILE EXISTS
--------------------
Supabase's "leaked password protection" is a dashboard toggle. Flipping it makes GoTrue reject
any password found in a breach corpus with a 422 `weak_password`. Before this change, that
landed as:

  * sign_up          -> bare HTTPException(400, "Registration failed")
  * change_password  -> bare-string HTTPException(500, "We couldn't change your password.")

Both are dead ends. The 500 is worse than it looks: `APIClient.validateResponse`'s 5xx arm has
no `detail` fallback (only the 4xx arm does, see auth.md §3), so the wording never reached iOS
at all — the user saw a generic transient server error and was invited to retry the one password
that can never be accepted.

THE CLASSIFIER IS THE FRAGILE PART, so most of this file is about it. GoTrue signals the
rejection three different ways depending on which call was used, and the ADMIN path — the one
`change_password` actually uses — is NOT the typed exception. Testing only the typed case would
leave the change-password half vacuously unhandled while looking green.
"""

from __future__ import annotations

import pytest
from supabase_auth.errors import AuthApiError, AuthWeakPasswordError

from app.api.error_response import (
    _DEFAULT_ACTIONS,
    _DEFAULT_STATUS,
    _USER_MESSAGES,
    ErrorCode,
    auth_error,
)
from app.api.v1.endpoints.auth import (
    _is_password_rejected,
    _is_rejected_credential,
    _password_rejection_reasons,
)


# ── The classifier, layer by layer ────────────────────────────────────────────


def test_layer1_the_typed_weak_password_error():
    """`sign_up` / `update_user` raise the real class, carrying `.reasons`."""
    exc = AuthWeakPasswordError("Password is known to be weak", 422, ["pwned"])
    assert _is_password_rejected(exc)


def test_layer2_the_admin_path_which_is_NOT_the_typed_error():
    """`admin.update_user_by_id` — the call `change_password` uses — surfaces a plain
    `AuthApiError` whose `.code` carries the string instead of the typed subclass.

    This is the half that would silently not work if the classifier only did isinstance().
    """
    exc = AuthApiError("Password is too weak", 422, "weak_password")
    assert not isinstance(exc, AuthWeakPasswordError), "premise changed — re-read the SDK"
    assert _is_password_rejected(exc)


def test_layer3_wording_fallback():
    assert _is_password_rejected(Exception("Password is known to be weak and easy to guess"))


@pytest.mark.parametrize("exc", [
    AuthApiError("Invalid login credentials", 400, "invalid_credentials"),
    Exception("connection reset by peer"),
    # An exception whose str() is EMPTY defeats any marker-only classifier — the exact trap a
    # Gemini timeout classifier hit in this repo. Must not be mistaken for a weak password.
    TimeoutError(),
    ValueError(),
])
def test_unrelated_failures_are_not_misread_as_a_weak_password(exc):
    assert not _is_password_rejected(exc)


def test_a_wrong_password_and_a_refused_password_are_different_things():
    """Collapsing them would tell a user setting a NEW password that their EXISTING one is
    wrong — and would send them to a reset flow they do not need."""
    wrong = AuthApiError("Invalid login credentials", 400, "invalid_credentials")
    refused = AuthWeakPasswordError("Password is known to be weak", 422, ["pwned"])

    assert _is_rejected_credential(wrong) and not _is_password_rejected(wrong)
    assert _is_password_rejected(refused) and not _is_rejected_credential(refused)


# ── Reasons must survive as a FLAT scalar ─────────────────────────────────────


def test_reasons_are_flattened_to_a_string():
    """iOS `AnyCodable` decodes String/Int/Double/Bool only and silently yields "" for a list,
    so a raw list in `details` arrives as garbage (auth.md §3)."""
    exc = AuthWeakPasswordError("weak", 422, ["length", "pwned"])
    reasons = _password_rejection_reasons(exc)
    assert isinstance(reasons, str) and reasons == "length, pwned"


def test_absent_reasons_yield_none_rather_than_an_empty_string():
    assert _password_rejection_reasons(AuthApiError("x", 422, "weak_password")) is None
    assert _password_rejection_reasons(AuthWeakPasswordError("x", 422, [])) is None


# ── The response contract ─────────────────────────────────────────────────────


def test_the_code_is_registered_in_all_four_maps():
    """`_DEFAULT_ACTIONS` is the one that fails SILENTLY when forgotten — `make_error_response`
    simply emits `action: null` and no existing test covers non-auth codes there."""
    code = ErrorCode.AUTH_PASSWORD_REJECTED
    assert code in _USER_MESSAGES and _USER_MESSAGES[code]
    assert _DEFAULT_ACTIONS.get(code) == "fix_input"
    assert _DEFAULT_STATUS.get(code) == 400


def test_it_is_a_400_so_ios_does_not_clear_a_valid_token():
    """A 401 would make the client treat this as a session failure and drop a working
    credential (auth.md §3) — for a user whose only mistake was choosing a bad new password."""
    assert _DEFAULT_STATUS[ErrorCode.AUTH_PASSWORD_REJECTED] == 400
    body = auth_error(ErrorCode.AUTH_PASSWORD_REJECTED, message="m").detail
    assert body["action"] == "fix_input", "must not offer sign_in — they are already on the form"
    assert body["error_code"] == "AUTH_PASSWORD_REJECTED"
    assert body["user_message"]


def test_both_handlers_classify_before_falling_through():
    """Source guard: the bare 400/500 fallbacks must come AFTER the rejection check, or the
    typed code is unreachable. An order bug here restores the exact dead end this fixes."""
    import inspect

    from app.api.v1.endpoints import auth as auth_mod

    for fn_name, fallback in (("sign_up", 'detail="Registration failed"'),
                              ("change_password", "We couldn't change your password")):
        src = inspect.getsource(getattr(auth_mod, fn_name))
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))

        # Scope to the `except Exception` HANDLER, not the whole function. `sign_up` raises
        # `detail="Registration failed"` twice — once inside the `try` for a missing user
        # object, which legitimately precedes the classifier — so a whole-function search
        # matches the wrong one and reports a bug that is not there. (It did, when this guard
        # was first written.)
        handler_at = code.rfind("except Exception")
        assert handler_at != -1, f"{fn_name} has no `except Exception` handler any more"
        handler = code[handler_at:]

        check_at = handler.find("_is_password_rejected")
        fall_at = handler.find(fallback)
        assert check_at != -1, f"{fn_name}'s handler no longer classifies a refused password"
        assert fall_at != -1, f"{fn_name}'s generic fallback moved — re-read this guard"
        assert check_at < fall_at, (
            f"{fn_name}: the generic fallback runs BEFORE the rejection check, so "
            "AUTH_PASSWORD_REJECTED can never be raised"
        )
