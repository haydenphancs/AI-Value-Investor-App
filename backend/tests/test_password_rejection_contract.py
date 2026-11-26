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

THE CLASSIFIER IS THE FRAGILE PART, so most of this file is about it.

⚠️ THE SDK THAT RUNS IN PRODUCTION IS `gotrue` (supabase 2.16 builds its auth client from it),
NOT `supabase_auth` — a stray local install no requirement pins, absent on Railway (AUTH-BE-13).
This file used to import `supabase_auth.errors` at module level, so it exercised a class
production never raises and would not even collect in a clean environment. The exceptions below
are now built THROUGH gotrue's own `helpers.handle_exception` from a fake HTTP response, exactly
the path a real GoTrue answer takes — and that path builds the typed `AuthWeakPasswordError` for
EVERY `weak_password` body, user calls and the admin call alike. The `.code`-only shape is kept
as its own layer for a future SDK that stops building the subclass.
"""

from __future__ import annotations

import warnings

import httpx
import pytest

with warnings.catch_warnings():
    warnings.simplefilter("ignore", DeprecationWarning)  # gotrue announces its own rename
    from gotrue.errors import AuthApiError, AuthWeakPasswordError
    from gotrue.helpers import handle_exception

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


def _gotrue_error(status: int, body: dict) -> Exception:
    """What gotrue raises for this GoTrue answer — built by the SDK's OWN error mapper."""
    req = httpx.Request("PUT", "https://example.supabase.co/auth/v1/admin/users/x")
    resp = httpx.Response(
        status, json=body, request=req, headers={"X-Supabase-Api-Version": "2024-01-01"},
    )
    return handle_exception(httpx.HTTPStatusError("http error", request=req, response=resp))


_WEAK_BODY = {
    "code": "weak_password",
    "msg": "Password is known to be weak and easy to guess, please choose a different one.",
    "weak_password": {"reasons": ["pwned"]},
}


# ── The classifier, layer by layer ────────────────────────────────────────────


def test_layer1_the_real_sdk_path_builds_the_typed_error():
    """A GoTrue 422 `weak_password` — from `sign_up` OR `admin.update_user_by_id`, both go
    through the same `handle_exception` — becomes gotrue's typed class, carrying `.reasons`."""
    exc = _gotrue_error(422, _WEAK_BODY)
    assert type(exc) is AuthWeakPasswordError, "premise changed — re-read gotrue.helpers"
    assert exc.reasons == ["pwned"]
    assert _is_password_rejected(exc)


def test_layer1_alone_survives_an_sdk_that_drops_the_code():
    """AUTH-BE-13's scenario: the typed class arrives WITHOUT `.code` and with neutral wording.
    Only the isinstance layer can still classify it — this isolates that layer."""
    exc = _gotrue_error(422, _WEAK_BODY)
    exc.code = None
    exc.args = ("rejected",)
    exc.message = "rejected"
    assert _is_password_rejected(exc)


def test_layer1_is_production_s_class_not_a_stray_sdk_s():
    """The typed layer must include the class the RUNTIME SDK raises (AUTH-BE-13)."""
    from app.api.v1.endpoints.auth import _WEAK_PASSWORD_ERROR_TYPES

    assert AuthWeakPasswordError in _WEAK_PASSWORD_ERROR_TYPES


def test_layer2_a_code_only_shape_still_classifies():
    """An AuthError carrying `code="weak_password"` WITHOUT the subclass — the shape a future SDK
    could produce. Without layer 2 the change/reset/set-password halves would go vacuously
    unhandled the day that happens, while signup looked fine."""
    exc = AuthApiError("Password is too weak", 422, "weak_password")
    assert not isinstance(exc, AuthWeakPasswordError), "premise changed — re-read the SDK"
    assert _is_password_rejected(exc)


def test_layer3_wording_fallback():
    assert _is_password_rejected(Exception("Password is known to be weak and easy to guess"))


@pytest.mark.parametrize("exc", [
    AuthApiError("Invalid login credentials", 400, "invalid_credentials"),
    _gotrue_error(400, {"code": "invalid_credentials", "msg": "Invalid login credentials"}),
    _gotrue_error(429, {"code": "over_email_send_rate_limit", "msg": "email rate limit exceeded"}),
    _gotrue_error(503, {"msg": "upstream down"}),
    handle_exception(httpx.ConnectError("connection reset by peer")),
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
    wrong = _gotrue_error(400, {"code": "invalid_credentials", "msg": "Invalid login credentials"})
    refused = _gotrue_error(422, _WEAK_BODY)

    assert _is_rejected_credential(wrong) and not _is_password_rejected(wrong)
    assert _is_password_rejected(refused) and not _is_rejected_credential(refused)


# ── Reasons must survive as a FLAT scalar ─────────────────────────────────────


def test_reasons_are_flattened_to_a_string():
    """iOS `AnyCodable` decodes String/Int/Double/Bool only and silently yields "" for a list,
    so a raw list in `details` arrives as garbage (auth.md §3)."""
    exc = _gotrue_error(422, {**_WEAK_BODY, "weak_password": {"reasons": ["length", "pwned"]}})
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
                              ("change_password", "We couldn't change your password"),
                              # The single-use code is SPENT before this handler runs (AUTH-BE-03,
                              # SB-5, PW-4): its fallback (AUTH_CODE_USED) must never be reached
                              # for a refused password, which is AUTH_PASSWORD_REJECTED.
                              ("reset_password", "_code_used("),
                              ("set_password", "_code_used("),
                              ("confirm_email", "_CONFIRMED_FINISH_FAILED")):
        src = inspect.getsource(getattr(auth_mod, fn_name))
        # Comment-stripped, trailing comments too: the prose next to each branch names both
        # helpers, which would satisfy this scan after the code was deleted.
        code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())

        # Scope to the `except Exception` HANDLER, not the whole function. `sign_up` raises
        # `detail="Registration failed"` twice — once inside the `try` for a missing user
        # object, which legitimately precedes the classifier — so a whole-function search
        # matches the wrong one and reports a bug that is not there. (It did, when this guard
        # was first written.)
        handler_at = code.rfind("except Exception")
        assert handler_at != -1, f"{fn_name} has no `except Exception` handler any more"
        handler = code[handler_at:]

        # `_is_password_refused` = weak/pwned OR GoTrue's password-shaped `validation_failed`
        # (the 72-byte cap); reset/set keep `_is_password_rejected` (C2 files validation_failed
        # there as AUTH_CODE_USED). Either one counts as classifying.
        hits = [i for i in (handler.find("_is_password_refused("),
                            handler.find("_is_password_rejected(")) if i != -1]
        check_at = min(hits) if hits else -1
        fall_at = handler.find(fallback)
        assert check_at != -1, f"{fn_name}'s handler no longer classifies a refused password"
        assert fall_at != -1, f"{fn_name}'s generic fallback moved — re-read this guard"
        assert check_at < fall_at, (
            f"{fn_name}: the generic fallback runs BEFORE the rejection check, so "
            "AUTH_PASSWORD_REJECTED can never be raised"
        )


# ── After a SPENT code, the copy must ask for a NEW one ─────────────────────────


@pytest.mark.parametrize("fn_name", ["reset_password", "set_password"])
def test_a_refused_password_after_a_spent_code_says_request_a_new_code(fn_name):
    """verify_otp consumed the code before the password write ran, and the form still holds
    it — the stock "choose a different one" copy sends the user to resubmit a dead code and
    read "That code is invalid". Scoped to the admin-update HANDLER (the last `except
    Exception`), comment-stripped."""
    import inspect

    from app.api.v1.endpoints import auth as auth_mod

    src = inspect.getsource(getattr(auth_mod, fn_name))
    code = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    handler = code[code.rfind("except Exception"):]
    branch = handler[handler.find("_is_password_rejected("):]
    assert "_PASSWORD_REJECTED_NEEDS_NEW_CODE" in branch.split("logger.error", 1)[0]
    # The words on both screens' button (C2): "Send a new code".
    assert auth_mod._PASSWORD_REJECTED_NEEDS_NEW_CODE == (
        "That password can't be used. Send a new code, then choose a different password."
    )


# ── GoTrue's OTHER password refusal: `validation_failed` (the 72-byte cap) ──────


_TOO_LONG_BODY = {"code": "validation_failed", "msg": "Password cannot be longer than 72 characters"}


def test_a_too_long_refusal_is_a_password_refusal_but_not_a_weak_one():
    """GoTrue answers > 72 bytes with 400 `validation_failed` (verified live), never
    `weak_password`. Sign-up / change / confirm-email treat it as the user's password problem
    (`_is_password_refused`); reset / set-password deliberately do not (`_is_password_rejected`
    stays weak-only), so there it is AUTH_CODE_USED per the C2 contract."""
    from app.api.v1.endpoints.auth import (
        _is_password_refused, _is_password_validation_failure, _password_refused_copy,
    )
    from app.schemas.auth import PASSWORD_TOO_LONG_MESSAGE

    e = _gotrue_error(400, _TOO_LONG_BODY)
    assert _is_password_validation_failure(e) and _is_password_refused(e)
    assert not _is_password_rejected(e)
    assert _password_refused_copy(e) == PASSWORD_TOO_LONG_MESSAGE


@pytest.mark.parametrize("body", [
    # The same code about ANOTHER field, or about a one-time code — never a password refusal.
    {"code": "validation_failed", "msg": "Unable to validate email address: invalid format"},
    {"code": "validation_failed", "msg": "Token has expired or is invalid"},
    # The password word without the code: not this classifier's business.
    {"code": "same_password", "msg": "New password should be different from the old password."},
])
def test_validation_failed_about_something_else_is_not_a_password_refusal(body):
    from app.api.v1.endpoints.auth import _is_password_validation_failure, _password_refused_copy

    e = _gotrue_error(422 if body["code"] == "same_password" else 400, body)
    assert not _is_password_validation_failure(e)
    assert _password_refused_copy(e) is None


def test_a_weak_refusal_keeps_the_stock_copy():
    from app.api.v1.endpoints.auth import _is_password_refused, _password_refused_copy

    e = _gotrue_error(422, {"code": "weak_password", "msg": "Password is known to be weak",
                            "weak_password": {"reasons": ["characters"]}})
    assert _is_password_refused(e) and _password_refused_copy(e) is None
