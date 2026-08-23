"""Tracking -> Alerts is the ONE surface for everything alert-shaped.

Four features used the word "Alerts" across two tabs, and one of them —
`Profile -> Notification History` — was a straight duplicate of the Alerts tab: same organism,
same view-model type, while the tab-bar badge pointed at Tracking. So it was the one surface
the badge could not lead you to, and two sets of read semantics to keep correct for no gain.

These guards pin the consolidation. They are source scans over the Swift tree because there is
no XCTest target; per `.claude/rules/testing.md` they are comment-stripped and brace-bounded,
and each was mutation-tested by hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_ALERTS_TAB = _IOS / "Views" / "Organisms" / "AlertsTabContent.swift"
_PROFILE = _IOS / "Views" / "Screens" / "ProfileView.swift"
_TRACKING = _IOS / "Views" / "Screens" / "TrackingView.swift"
_HOME = _IOS / "Views" / "Screens" / "HomeDashboardView.swift"
_CONTENT = _IOS / "ContentView.swift"
_ROUTER = _IOS / "Core" / "Services" / "NotificationRouter.swift"
_PUSH_MGR = _IOS / "Core" / "Services" / "PushNotificationManager.swift"
_INBOX_VM = _IOS / "ViewModels" / "NotificationInboxViewModel.swift"
_RULES_VM = _IOS / "ViewModels" / "PriceAlertRulesViewModel.swift"


def _code_only(src: str) -> str:
    """Strip whole-line comments.

    Load-bearing: every one of these fixes is documented in a comment sitting next to it, and
    those comments contain the exact tokens the assertions grep for. An un-stripped scan would
    keep passing on the prose after the code was reverted.
    """
    return "\n".join(
        "" if line.strip().startswith("//") else line for line in src.splitlines()
    )


def _read(path: Path) -> str:
    # Deliberately NOT pytest.skip: a guard whose subject vanished must fail, not go quiet.
    assert path.exists(), f"{path} is missing — this guard would otherwise pass vacuously"
    return _code_only(path.read_text(encoding="utf-8"))


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


# --------------------------------------------------------------------------- the duplicate is gone


def test_the_standalone_notification_history_screen_is_deleted():
    stale = _IOS / "Views" / "Screens" / "NotificationInboxView.swift"
    assert not stale.exists(), (
        "NotificationInboxView is back. It renders the same list as Tracking -> Alerts through "
        "the same view-model type, while the tab-bar badge points at Tracking — so it is a "
        "second surface with different read semantics that the badge cannot reach."
    )


def test_the_account_screen_has_no_notification_history_row():
    # Brace-bounded to the settings section: "Notification History" also appears in this file's
    # explanatory comment, and `NotificationsSettingsView` (push PREFERENCES, a different
    # feature that stays) is linked from the very next row.
    section = _balanced(_read(_PROFILE), "private var settingsSection: some View {")
    assert "Notification History" not in section, (
        "the Account screen links a second copy of the notification list again"
    )
    # Anti-vacuity: prove the window really is the settings section, and that the neighbouring
    # PREFERENCES row — which must NOT be deleted along with it — is still there.
    assert "NotificationsSettingsView" in section, (
        "brace-bounded window is not the settings section, or the notification PREFERENCES row "
        "was deleted along with the history row"
    )


# --------------------------------------------------------------------------- the tab owns everything


def test_the_alerts_tab_carries_all_three_sections():
    src = _read(_ALERTS_TAB)
    for section in ("AlertsEventsSection", "PriceAlertRuleRow", "NotificationInboxSection.rows"):
        assert section in src, f"the Alerts tab no longer renders {section}"


def test_the_upcoming_digest_left_the_assets_tab():
    assets = _balanced(_read(_TRACKING), "struct AssetsTabContent: View {")
    assert "AlertsEventsSection" not in assets, (
        "the 'Upcoming & Events' digest is back in the Assets tab, so two tabs show things "
        "called Alerts again"
    )


def test_the_alerts_tab_still_marks_read_on_sight():
    """The badge fix, not a preference.

    A badge the user cannot clear by looking at the thing it counts IS the bug — that is why
    there is no 'Mark all read' button here. And the mark must follow an AWAITED first page:
    there is nothing to mark before it lands.
    """
    body = _balanced(_read(_ALERTS_TAB), "private func loadAll() async {")
    assert "markAllReadOnView()" in body, (
        "the Alerts tab no longer marks notifications read on sight, so the tab-bar badge has "
        "nothing that clears it"
    )
    assert body.index("loadAndWait()") < body.index("markAllReadOnView()"), (
        "mark-all-read runs before the first page is awaited, so it marks an empty list"
    )


def test_the_alerts_tab_has_exactly_one_lazy_stack():
    """Nested LazyVStacks render EAGERLY — only the outermost one virtualizes.

    Three stacked sections make a second one easy to add by reflex. On Home the same mistake
    was a permanent 100%-CPU main-thread hang with nothing in the simulator log.
    """
    src = _read(_ALERTS_TAB)
    assert src.count("LazyVStack") == 1, (
        f"expected exactly one LazyVStack in the Alerts tab, found {src.count('LazyVStack')} — "
        "a nested lazy stack materializes every notification row at once"
    )


def test_the_notification_rows_are_not_wrapped_in_a_child_view_struct():
    """`NotificationInboxSection` must stay a namespace of @ViewBuilder funcs.

    A child `View` struct is an opaque boundary to the enclosing lazy stack: it re-eager-renders
    and breaks pinning. Splicing a bare ForEach in keeps the rows real children of the one stack.
    """
    section = _IOS / "Views" / "Organisms" / "NotificationInboxSection.swift"
    src = _read(section)
    assert "enum NotificationInboxSection {" in src, (
        "NotificationInboxSection became a View again, so the notification rows are behind a "
        "struct boundary and the Alerts tab's LazyVStack can no longer virtualize them"
    )
    assert "ScrollView" not in src, (
        "the notification section owns a ScrollView again — the Alerts tab already has one, "
        "and nesting them breaks scrolling and virtualization"
    )


# --------------------------------------------------------------------------- the push fallback


def test_an_unroutable_push_has_exactly_one_owner():
    """`ContentView` and `HomeDashboardView` both observe `pendingPushRoute`, and Home CLEARS it.

    If Home consumed a fallback route, the clear would land before ContentView read it and the
    tap would go nowhere. They partition the route space instead — this pins both halves.
    """
    assert "needsAlertsFallback" in _read(_ROUTER), (
        "the shared predicate is gone; a forked copy in each file drifts into 'some unroutable "
        "taps land nowhere', which is silent"
    )

    content = _read(_CONTENT)
    assert "route.needsAlertsFallback" in content and "pendingTrackingTab = .alerts" in content, (
        "ContentView no longer routes an unroutable push to the Alerts tab"
    )

    home = _read(_HOME)
    assert "guard !route.needsAlertsFallback else { return }" in home, (
        "HomeDashboardView consumes fallback routes again and its `defer` clears them, racing "
        "ContentView's handler and dropping the tap"
    )
    assert "showNotificationInbox" not in home, (
        "Home presents a standalone notification inbox again"
    )


def test_the_cold_launch_tap_still_survives():
    """`.onChange` does not fire for a value set BEFORE first render, and a cold-launch tap sets
    the route before any view exists. Warm taps work either way, so this never fails by hand."""
    tracking = _read(_TRACKING)
    consume = _balanced(
        tracking, ".onChange(of: appState.pendingTrackingTab, initial: true) {"
    )
    assert "viewModel.selectedTab = pending" in consume, (
        "the Tracking screen no longer selects the parked segment"
    )
    assert "appState.pendingTrackingTab = nil" in consume, (
        "the parked segment is never cleared, so it re-fires on every later render"
    )


# --------------------------------------------------------- defects found by driving the app


def test_a_tap_that_arrives_before_appstate_is_parked_not_dropped():
    """Cold launch FROM a tap: `didReceive` fires before `iosApp`'s `.task` has run
    `configure(appState:)` — that task also `await`s `ServerEnvironmentManager.resolve()` first.
    `appState?.pendingPushRoute = route` then wrote to nil and the app opened on Home with
    nothing logged. Observed live: the banner tap cold-launched the app and went nowhere."""
    src = _read(_PUSH_MGR)
    handle = _balanced(src, "func handleTap(route: NotificationRoute) {")
    assert "pendingRoute = route" in handle, (
        "handleTap drops the route when AppState is not wired yet — the cold-launch path"
    )
    configure = _balanced(src, "func configure(appState: AppState) {")
    assert "pendingRoute" in configure, (
        "configure() never flushes a parked tap, so parking it just loses it later instead"
    )


def test_the_alerts_tab_heals_a_load_that_raced_session_restore():
    """`.reloadOnIdentityChange` deliberately does NOT fire on `.restoring -> .authenticated`
    at launch (`identityGeneration` does not move), and `.task(id: isActiveTab)` only re-runs
    on a tab switch. Landing here directly from a push tap hits both blind spots: observed
    live sitting on "Reconnecting your account..." until a manual pull."""
    src = _read(_ALERTS_TAB)
    healer = _balanced(src, ".onChange(of: appState.auth.status) {")
    assert "loadAll()" in healer, "the auth-status trigger does not reload"
    assert "isAuthBlocked" in healer, (
        "the heal is ungated, so a genuine sign-in double-fetches on top of "
        "reloadOnIdentityChange"
    )


def test_neither_list_can_render_an_empty_error_message():
    """`AppError.message` passes some backend strings through verbatim (`.apiError`,
    `.validationFailed`). An empty one rendered as a bare warning triangle over "Try Again"
    with no sentence at all — seen live on the Price Rules section."""
    for path, label in ((_INBOX_VM, "notifications"), (_RULES_VM, "price rules")):
        src = _read(path)
        assert "text.isEmpty ?" in src, (
            f"the {label} view model can put an empty string in `.error`, which renders as a "
            "warning icon with no explanation"
        )
        assert "log.error(" in src, (
            f"the {label} load failure is not logged — it would be diagnosed from nothing"
        )
