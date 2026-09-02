"""The Settings password row must stay provider-aware, and the client must not guess.

An Apple/Google account has no password at all — Supabase provisions it through
`sign_in_with_id_token` and never writes one. The row used to be gated on nothing but
`auth.status == .authenticated`, so those users were pushed into a form demanding a current
password they could never supply, and the server told them it was "incorrect". Reported from
TestFlight.

Three properties, each of which has a distinct broken-again shape:

1. The row branches on `hasPassword`. Reverting it to an unconditional "Change Password" puts
   the original bug straight back.
2. `nil` is treated as "has a password". `hasPassword` is Optional because an older backend and
   a failed probe both report nothing; reading `!= true` instead of `== false` would offer "Set
   a Password" to every user the moment `/users/me` blipped, stranding them in a flow the server
   refuses with AUTH_PASSWORD_ALREADY_SET.
3. Both new backend codes have an explicit `AppError` branch. Falling through to the `default:`
   arm yields `.authUnavailable`, which is `isRetryable` — offering "Try Again" forever for a
   request that is refused identically until the account state changes.

Source-scan, so it needs no app build. Per `.claude/rules/testing.md` these are brace-bounded
and comment-stripped — the prose above and the (extensive) comments at each site contain every
token asserted below, so an un-stripped scan would pass on the explanation after the code was
reverted. Mutation-tested by hand on 2026-09-01: each assertion was watched to fail against a
deliberately broken source before being committed.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend/ios/ios"
_SETTINGS = _IOS / "Views/Screens/AppSettingsView.swift"
_SCREEN = _IOS / "Views/Screens/ChangePasswordView.swift"
_APP_ERROR = _IOS / "Core/Utilities/AppError.swift"
_PROFILE = _IOS / "Core/State/AppState.swift"


def _strip_comments(src: str) -> str:
    """Remove // and /* */ comments so a guard cannot be satisfied by prose."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"^\s*//.*$", "", src, flags=re.M)


def _src(path: Path) -> str:
    assert path.exists(), f"{path} moved — re-point this guard, do not delete it"
    return _strip_comments(path.read_text())


def _brace_block(src: str, header: str) -> str:
    """The braced body following `header`, matched by depth.

    Scanning a whole FILE is how a guard ends up proving something about a different type in it
    — this pins the assertion to the declaration actually meant.
    """
    i = src.index(header)
    start = src.index("{", i)
    depth, j = 0, start
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces after {header!r}")


# ── 1 & 2: the settings row ──────────────────────────────────────────────────

def test_the_password_row_branches_on_has_password():
    code = _src(_SETTINGS)
    assert "hasPassword" in code, (
        "the Settings password row no longer consults `hasPassword`. An Apple/Google account "
        "has none, so an unconditional Change Password row sends those users to a form that "
        "demands a current password that has never existed."
    )
    assert "ChangePasswordView(mode:" in code, (
        "the row pushes ChangePasswordView without a mode — `.set` is the only branch that "
        "drops the Current-password field."
    )
    assert '"Set a Password"' in code, "the set-password wording is gone from the row"


def test_unknown_password_state_is_treated_as_having_one():
    """`nil` means an older backend or a failed probe, NOT "no password"."""
    code = _src(_SETTINGS)
    assert "hasPassword == false" in code, (
        "the row must test `hasPassword == false`, not `!= true` or `?? false`. `hasPassword` "
        "is Optional and nil means UNKNOWN; reading nil as 'no password' would offer Set a "
        "Password to every user during a probe outage."
    )
    for wrong in ("hasPassword != true", "hasPassword ?? false"):
        assert wrong not in code, (
            f"`{wrong}` treats an unknown password state as 'no password' — see the docstring."
        )


def test_the_profile_dto_carries_the_password_state():
    """A `let hasPassword: Bool` (non-Optional) would crash the decode against a backend that
    predates the field, which is every deploy until the migration is applied."""
    body = _brace_block(_src(_PROFILE), "struct UserProfile")
    assert "let hasPassword: Bool?" in body, (
        "UserProfile.hasPassword must stay Optional — `/users/me` omits it on an older backend "
        "and reports it absent when the account_auth_methods probe fails."
    )
    assert 'case hasPassword = "has_password"' in body, "the CodingKey mapping is missing"


# ── 3: the error branches ────────────────────────────────────────────────────

@pytest.mark.parametrize("code_name", ["AUTH_PASSWORD_NOT_SET", "AUTH_PASSWORD_ALREADY_SET"])
def test_the_new_auth_codes_have_an_explicit_apperror_branch(code_name):
    body = _brace_block(_src(_APP_ERROR), "private static func mapAPIError")
    assert f'"{code_name}"' in body, (
        f"{code_name} has no branch in mapAPIError, so it falls to the `default:` arm and "
        f"becomes .authUnavailable — which is isRetryable, offering 'Try Again' for a request "
        f"that is refused identically forever."
    )


def test_the_new_auth_codes_are_not_treated_as_session_failures():
    """They must not clear a credential. `.validationFailed` is excluded from `isAuthError`;
    any `.sessionEnded` / `.tokenExpired` / `.unauthorized` here would sign the user out for
    tapping the wrong password row."""
    body = _brace_block(_src(_APP_ERROR), "private static func mapAPIError")
    m = re.search(
        r'case "AUTH_PASSWORD_NOT_SET",\s*"AUTH_PASSWORD_ALREADY_SET":(.*?)case ',
        body, re.S,
    )
    assert m, "the two codes are no longer a single arm — re-point this guard"
    arm = m.group(1)
    assert "validationFailed" in arm, f"expected .validationFailed, got: {arm.strip()!r}"
    for destructive in ("sessionEnded", "tokenExpired", "unauthorized", "authUnavailable"):
        assert destructive not in arm, (
            f"`.{destructive}` for an account-state error: the caller's session is fine."
        )


# ── The set flow itself ──────────────────────────────────────────────────────

def test_the_set_mode_does_not_ask_for_a_current_password():
    """The whole point. A Current-password field in the `.set` path is the original bug."""
    code = _src(_SCREEN)
    form = _brace_block(code, "private var enterCodeStep")
    assert "Current password" not in form, (
        "the set-password step asks for a current password, which by definition does not exist"
    )
    assert "oneTimeCode" in form, (
        "the emailed code field is gone. The OTP is what replaces the current password as proof "
        "— without it, a stolen access token alone becomes permanent account ownership."
    )


def test_the_set_flow_goes_through_authservice_so_the_rotated_tokens_are_adopted():
    """Setting a password stamps `password_changed_at`, which kills the token that made the
    request. Calling APIClient directly and decoding a bare message drops the replacements and
    signs the user out seconds after telling them it worked — the exact bug that already shipped
    once on change-password."""
    body = _brace_block(_src(_SCREEN), "private func submitSet")
    assert "authService.setPassword" in body, (
        "submitSet must call AuthService.setPassword, which adopts the rotated tokens"
    )
