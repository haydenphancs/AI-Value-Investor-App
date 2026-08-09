"""Failures raised inside the Account screen have to be VISIBLE.

`ProfileView` is a `.fullScreenCover` (presented from seven sites), and UIKit draws a modal
presentation ABOVE the whole root hierarchy. `AppState.currentError` and `AppState.toastMessage`
are rendered by `.overlay`s on the ROOT view in `iosApp.swift`, so anything raised from inside
the cover is drawn behind it and is never seen — not delayed, not clipped: invisible.

`AppSettingsView` already documents this ("Verified on the Simulator") and works around it with
local alerts, but three live paths still went through the root surfaces:

  * `AppSettingsView.deleteAccount()` set `appState.currentError`, so a failed account deletion
    — a destructive, irreversible action the user just confirmed — told them NOTHING.
  * `ProfileView` never read `viewModel.errorMessage`, so a failed display-name rename reverted
    in silence (`.claude/rules/auth.md` §6 bans exactly that on a user-initiated mutation).
  * `AppActions.reportMutationFailure` routes to the same toast, so it was not a fix either.

`GlobalAudioOverlay` exists for this identical reason, and `ErrorPresentationHost` is its mirror
for errors.

⚠️ Guard discipline (`project_source_scan_guard_vacuity`): comments blanked, windows brace-
bounded, never bounded by the token asserted. Mutation-tested at the bottom.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_PROFILE = _IOS / "Views" / "Screens" / "ProfileView.swift"
_SETTINGS = _IOS / "Views" / "Screens" / "AppSettingsView.swift"
_HOST = _IOS / "Views" / "Modifiers" / "ErrorPresentationHost.swift"


def _code_only(src: str) -> str:
    return "\n".join(
        "" if line.strip().startswith("//") else line for line in src.splitlines()
    )


def _braced_block(src: str, opener: str) -> str:
    start = src.index(opener) + len(opener) - 1
    assert src[start] == "{", f"{opener!r} must end at its opening brace"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after {opener!r}")


def _read(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return _code_only(path.read_text(encoding="utf-8"))


# ── the host exists and is applied ───────────────────────────────────────────

def test_the_error_host_exists_and_renders_both_root_surfaces():
    src = _read(_HOST)
    assert "struct ErrorPresentationHost: ViewModifier" in src
    assert "appState.currentError" in src, "the host does not render the error toast"
    assert "appState.toastMessage" in src, "the host does not render the toast"
    # Its own sheets, not the root's: an Upgrade/Sign In tap on a toast rendered inside the
    # cover must open something the user can actually see.
    assert "BuyCreditsView()" in src and "SignInView()" in src, (
        "the host must own its action destinations; the root's are behind the cover too"
    )


def test_profile_applies_the_error_host():
    assert ".errorPresentationHost()" in _read(_PROFILE), (
        "ProfileView is a fullScreenCover and does not host the error surfaces, so every "
        "failure raised on the Account screen is drawn behind it"
    )


# ── the three call sites ─────────────────────────────────────────────────────

def test_delete_account_does_not_report_through_the_root_error_surface():
    body = _braced_block(_read(_SETTINGS), "private func deleteAccount() {")
    assert "appState.currentError" not in body, (
        "AppSettingsView.deleteAccount() reports through appState.currentError, which renders "
        "on the ROOT view — behind the Account fullScreenCover this screen lives in. A failed "
        "account deletion shows the user nothing."
    )
    assert "deleteError" in body, "the failure must be surfaced locally instead"


def test_delete_account_cannot_be_fired_twice():
    body = _braced_block(_read(_SETTINGS), "private func deleteAccount() {")
    assert "guard !isDeleting" in body, (
        "no in-flight guard: a double tap on 'Delete Forever' fires two DELETEs, and the "
        "second 401s because the first removed the auth row — telling the user the deletion "
        "failed at the moment it actually succeeded"
    )


def test_profile_renders_its_view_models_error():
    src = _read(_PROFILE)
    assert "viewModel.errorMessage" in src, (
        "ProfileView never reads viewModel.errorMessage, so a failed rename or credit load "
        "is silent — the alert just closes and the old value stays"
    )


def test_the_danger_zone_is_hidden_from_guests():
    src = _read(_SETTINGS)
    assert "if appState.auth.status == .authenticated {" in src, (
        "the Danger Zone is not gated on a real account. `.deleteAccount` is `.signInRequired`, "
        "so APIClient refuses it pre-flight and a guest's tap is a guaranteed silent no-op "
        "behind a confirmation alert promising it is permanent."
    )


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_guards_would_fail_on_the_old_code():
    """Each assertion above must be sensitive to the shipped defect, not merely true."""
    settings = _read(_SETTINGS)
    body = _braced_block(settings, "private func deleteAccount() {")
    # The window must actually be the function, not the whole file — otherwise
    # "appState.currentError not in body" is satisfied by any file that never mentions it.
    assert "apiClient.request(endpoint: .deleteAccount)" in body, (
        "the brace-bounded window does not contain the delete call, so it is not the "
        "function body and every assertion scoped to it is meaningless"
    )
    assert len(body) < len(settings), "the window is the whole file"
