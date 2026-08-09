"""Account screen — accessibility and the `.restoring` state.

Three defects, all of which look fine to a sighted user on a healthy connection:

  * The Appearance chips carried selection ONLY in fill colour and ink shade, so VoiceOver read
    three identical plain buttons and never announced which mode was active.
  * Their tappable area was ~22pt — an 11pt caption plus a 4pt pad — against the 44pt HIG
    minimum and WCAG 2.2 SC 2.5.8's 24pt floor.
  * `ProfileView` branched on `isAuthenticated`, which is `.authenticated` ONLY. During
    `.restoring` — entered on every transient network failure and every launch on a poor
    connection — it told a signed-in user they were a "Guest" and invited them to sign in to
    unlock credits for the account they already have. `.claude/rules/auth.md` §5 requires
    "Reconnecting" and forbids collapsing `.restoring` into `.unauthenticated`.

And the quiet-hours pickers, which stayed live and undimmed when iOS notifications are denied —
the only interactive controls on a screen where every Toggle is dimmed for exactly that reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_PROFILE = _IOS / "Views" / "Screens" / "ProfileView.swift"
_NOTIFS = _IOS / "Views" / "Screens" / "NotificationsSettingsView.swift"
_INBOX = _IOS / "Views" / "Screens" / "NotificationInboxView.swift"
_VM = _IOS / "ViewModels" / "ProfileViewModel.swift"


def _code_only(src: str) -> str:
    return "\n".join(
        "" if line.strip().startswith("//") else line for line in src.splitlines()
    )


def _balanced(src: str, opener: str, o: str = "{", c: str = "}") -> str:
    start = src.index(opener) + len(opener) - 1
    assert src[start] == o, f"{opener!r} must end at its opening {o!r}"
    depth = 0
    for i in range(start, len(src)):
        if src[i] == o:
            depth += 1
        elif src[i] == c:
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced {o!r} after {opener!r}")


def _read(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return _code_only(path.read_text(encoding="utf-8"))


@pytest.fixture
def appearance_chips() -> str:
    src = _read(_PROFILE)
    return _balanced(src, "ForEach(AppearanceMode.allCases) {")


def test_the_window_is_really_the_chip_loop(appearance_chips):
    """Anti-vacuity for everything scoped to it."""
    assert "viewModel.appearanceMode = mode" in appearance_chips, (
        "the brace-bounded window is not the chip loop"
    )


def test_the_selected_appearance_chip_announces_itself(appearance_chips):
    assert ".isSelected" in appearance_chips, (
        "the appearance chips carry selection only in colour, so VoiceOver reads three "
        "identical buttons and never says which mode is active"
    )


def test_the_appearance_chips_meet_the_minimum_hit_target(appearance_chips):
    assert "minHeight: 44" in appearance_chips, (
        "the chip's tappable area is the caption's intrinsic height plus a 4pt pad (~22pt), "
        "half the 44pt HIG minimum and below WCAG 2.2 SC 2.5.8"
    )


def test_the_appearance_control_uses_the_audited_toggle_tokens(appearance_chips):
    """A hand-composited `textMuted.opacity(0.3)` cannot be proven by `ThemeContrastAudit`,
    which resolves DECLARED tokens against DECLARED surfaces. These two are in the manifest."""
    assert "toggleSelectedBackground" in appearance_chips, (
        "the selected chip no longer uses the audited segmented-control token"
    )


def test_the_restoring_state_is_not_rendered_as_a_guest():
    src = _read(_PROFILE)
    # Scoped to the IDENTITY section, not the whole file: `isRestoring` is also read by the
    # sign-out gate, so a file-wide `in src` is satisfied by that unrelated use — verified by
    # mutation, where breaking the identity branch left the file-wide assertion passing.
    identity = _balanced(src, "private var userIdentitySection: some View {")
    assert "viewModel.isRestoring" in identity, (
        "the identity section does not distinguish `.restoring`, so a signed-in user on a "
        "flaky connection is shown 'Guest' and asked to sign in to the account they have"
    )
    assert "Reconnecting" in identity, "the restoring branch does not say what is happening"
    # And it must be a branch of its own, not folded into the guest arm.
    assert identity.index("viewModel.isRestoring") < identity.index('Text("Guest")'), (
        "the restoring check must come BEFORE the guest arm or it can never be reached"
    )
    vm = _read(_VM)
    assert "var isRestoring: Bool" in vm and ".restoring" in vm


def test_sign_out_stays_reachable_while_restoring():
    """This is the app's ONLY sign-out control, and a stuck session is exactly when a user
    wants it. `signOut()` resets local state first, so it works offline."""
    src = _read(_PROFILE)
    assert "viewModel.isAuthenticated || viewModel.isRestoring" in src, (
        "the sign-out section is hidden during `.restoring`, stranding the user"
    )


def test_a_transient_credit_load_failure_does_not_wipe_the_balance():
    vm = _read(_VM)
    body = _balanced(vm, "func loadCredits() {")
    assert "if isRestoring { return }" in body, (
        "loadCredits() nils the balance during `.restoring`, blanking a figure the user can "
        "currently see for a session that is merely healing"
    )


def test_the_quiet_hours_pickers_are_dimmed_when_notifications_are_denied():
    src = _read(_NOTIFS)
    # The signature spans lines, so anchor on the brace that opens the BODY.
    body = _balanced(src, "selection: Binding<Date>\n    ) -> some View {")
    assert "blocked" in body, "timeRow does not take the permission state"
    assert ".disabled(blocked)" in body, (
        "the quiet-hours DatePickers stay interactive when iOS notifications are denied — the "
        "only live controls on a screen where every Toggle is dimmed for that reason"
    )


def test_the_inbox_badge_is_not_published_before_the_first_load():
    src = _read(_INBOX)
    assert "initial: true" not in src, (
        "the inbox publishes its placeholder unread count (0) on appear, before any page has "
        "loaded — so opening the inbox clears the badge, and a failed load leaves it cleared "
        "while the notifications are still unread"
    )
