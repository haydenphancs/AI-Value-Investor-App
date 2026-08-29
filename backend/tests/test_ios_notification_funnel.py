"""The client half of "notifications work": permission, registration, and honest state.

WHY THIS FILE EXISTS. Push delivery was broken for a month by a Sandbox-only APNs key. Fixing
it exposed the funnel behind it: only **1 of 16** production users had any row in
`device_tokens`. Not because registration was failing — because most users are never asked.

The onboarding prompt is gated on `!skipped && !selected.isEmpty`, which is a defensible
choice (iOS asks once, so a wasted prompt is permanent) and is deliberately left alone. What
was missing is a second chance: nothing else in the app asked, `NotificationPermissionBanner`
lives on one screen nothing points at, and every pre-existing install already has
`has_completed_onboarding = true` and never sees onboarding at all.

Source scans over the Swift tree — there is no XCTest target. Per `.claude/rules/testing.md`
each is comment-stripped, brace-bounded, and was mutation-tested by hand.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"

_PRICE_SHEET = _IOS / "Views" / "Screens" / "PriceAlertsSheet.swift"
_PUSH_MGR = _IOS / "Core" / "Services" / "PushNotificationManager.swift"
_APP_DELEGATE = _IOS / "Core" / "AppDelegate.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_SETTINGS_VM = _IOS / "ViewModels" / "NotificationSettingsViewModel.swift"
_SETTINGS_VIEW = _IOS / "Views" / "Screens" / "NotificationsSettingsView.swift"


def _read(path: Path) -> str:
    assert path.exists(), f"{path} is missing — this guard would otherwise pass vacuously"
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("//"):
            out.append("")
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


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


# ── the ask ──────────────────────────────────────────────────────────────────


def test_creating_a_price_alert_asks_for_permission():
    """The highest-intent moment in the product, and it used to ask nothing.

    A user types "tell me when ORCL hits $147". Before this, `PriceAlertsSheet` contained no
    reference to permission at all — it neither asked nor admitted it could not deliver.
    """
    src = _read(_PRICE_SHEET)
    # The CALL, not the name. Asserting the bare identifier passed with the call site
    # deleted, because the function's own declaration still matched it — caught by mutation.
    assert "await requestPermissionIfNeeded()" in src, (
        "PriceAlertsSheet no longer CALLS the permission request. For a user who skipped "
        "onboarding this is the only place the app ever asks."
    )
    body = _balanced(src, "private func requestPermissionIfNeeded() async {")
    assert "permission == .notDetermined" in body, (
        "the request is not gated on `.notDetermined`. iOS prompts once; calling it while "
        "denied is a silent no-op that leaves the screen looking identical to the granted case."
    )
    # AWAIT the answer, never sleep a guess.
    #
    # The first cut slept 500ms and re-read `authorizationStatus`. The prompt is MODAL and a
    # person takes seconds to read it, so the re-read observed `.notDetermined` and the denial
    # warning never appeared — verified on the simulator, where tapping "Don't Allow" produced
    # no warning at all. A fixed sleep here is a race against a human.
    assert "await PushNotificationManager.shared.requestAuthorizationResult()" in body, (
        "the sheet no longer awaits the user's answer before re-reading the permission"
    )
    assert "Task.sleep" not in body, (
        "a fixed sleep is back in the permission path — it races the modal prompt and leaves "
        "a denied user with no warning"
    )


def test_the_ask_happens_only_after_a_rule_actually_exists():
    """Spending the one-shot prompt on a failed create is how a permanent denial happens."""
    src = _read(_PRICE_SHEET)
    assert "if await viewModel.create() {" in src, (
        "the permission request is no longer conditional on a SUCCESSFUL create"
    )
    vm = _read(_IOS / "ViewModels" / "PriceAlertsViewModel.swift")
    assert "func create() async -> Bool" in vm, (
        "`create()` stopped reporting success, so the caller cannot tell whether a rule "
        "exists before asking"
    )


def test_a_denied_user_is_told_the_alert_cannot_buzz():
    """Silently accepting a price alert you cannot deliver is the worst of the three states."""
    src = _read(_PRICE_SHEET)
    assert "permission == .denied" in src and "NotificationPermissionBanner" in src, (
        "PriceAlertsSheet no longer warns a denied user; the rule is saved and nothing says "
        "their phone will never ring"
    )


# ── registration ─────────────────────────────────────────────────────────────


def test_registration_is_re_asserted_on_every_foreground():
    """iOS hands over a token ONLY in response to a registration call.

    Without this, the recovery flow the permission banner exists to drive — denied → Open
    Settings → enable → return — ends with the banner gone, every toggle live, and no token
    on the backend until the process is killed and relaunched.
    """
    mgr = _read(_PUSH_MGR)
    assert "func registerIfAuthorized() async" in mgr, (
        "the shared registration helper is gone"
    )
    delegate = _read(_APP_DELEGATE)
    body = _balanced(
        delegate, "func applicationDidBecomeActive(_ application: UIApplication) {"
    )
    assert "registerIfAuthorized" in body, (
        "APNs registration is no longer re-asserted on foreground — enabling notifications "
        "in iOS Settings will not register a device until the next cold launch"
    )


def test_a_stranded_token_is_retried_on_a_healed_session_not_only_a_cold_launch():
    """`onAuthenticated` early-returns on the same identity, and restore re-runs constantly.

    A token re-stashed by a transient 5xx sat behind that return, so the retry only ever
    happened when the process was killed. `SettingsSyncManager` has a durable ladder for the
    identical problem one line above; the push token had none.
    """
    src = _read(_APP_STATE)
    body = _balanced(src, "private func onAuthenticated(userId: String? = nil) async {")
    healed = body.index("resumeSyncIfNeeded")
    ret = body.index("return", healed)
    assert "flushPendingToken" in body[healed:ret], (
        "flushPendingToken is back below the identity early-return, so a stranded device "
        "token is only retried on a cold launch"
    )


# ── honest state ─────────────────────────────────────────────────────────────


def test_authorized_but_unregistered_is_not_shown_as_healthy():
    """A reachable state that rendered as a perfectly working screen.

    `didFailToRegister` records nothing and `systemNotificationsBlocked` only tests `.denied`,
    so a user whose APNs registration failed saw full-opacity rows and no banner — every
    control on the screen inert, presented as though it worked.
    """
    vm = _read(_SETTINGS_VM)
    assert "deviceUnregistered" in vm, (
        "the settings screen can no longer distinguish 'granted but not registered' from "
        "'working'"
    )
    body = _balanced(vm, "func refreshPermission() async {")
    assert "hasRegisteredToken" in body, (
        "`deviceUnregistered` is not derived from whether a token was actually confirmed"
    )
    view = _read(_SETTINGS_VIEW)
    assert "viewModel.deviceUnregistered" in view, (
        "the notice is computed but never rendered"
    )


def test_a_registration_failure_keeps_the_real_error():
    """`reason: "apns"` was a hardcoded literal, so in production a missing entitlement, a
    network failure and a provisioning problem were indistinguishable — on the one failure
    that makes push unreachable for that device entirely."""
    body = _balanced(
        _read(_APP_DELEGATE),
        "didFailToRegisterForRemoteNotificationsWithError error: Error\n    ) {",
    )
    assert '"reason": "apns"' not in body, (
        "the hardcoded analytics reason is back; the real error is discarded again"
    )
    assert "NSError" in body and "code" in body, (
        "nothing about the actual error survives into telemetry"
    )


# ── round 2: the client half of "the notification actually does something" ───

_INBOX_VM = _IOS / "ViewModels" / "NotificationInboxViewModel.swift"
_BANNER = _IOS / "Views" / "Organisms" / "NotificationPermissionBanner.swift"


def test_a_foreground_notification_is_added_to_notification_center():
    """Without `.list` a notification that arrives while the app is OPEN shows its banner
    and is then gone — never added to Notification Center, so pulling down five minutes
    later shows nothing and the alert has no second chance to be read."""
    body = _balanced(
        _read(_APP_DELEGATE), "willPresent notification: UNNotification\n    ) async -> UNNotificationPresentationOptions {"
    )
    assert ".list" in body, (
        "foreground notifications no longer land in Notification Center"
    )


def test_the_icon_badge_has_exactly_one_writer():
    """`setBadgeCount` lived in `AppDelegate.syncBadge` and NOWHERE else, so the app icon
    was written when a push ARRIVED and never again. Three pushes lit up a "3" that
    survived opening the app, reading every one of them and marking them read on the
    server; only the next push could change it.

    Both the arriving-push path and the inbox path already call
    `AppState.notificationUnreadDidChange`, so putting the write there makes the icon
    agree with the inbox by construction.
    """
    writers = []
    for path in _IOS.rglob("*.swift"):
        if "setBadgeCount" in _read(path):
            writers.append(path.name)
    assert writers == ["AppState.swift"], (
        f"the app-icon badge is written from {writers} — it must have exactly one writer, "
        f"reached by both the push path and the read path"
    )
    body = _balanced(
        _read(_APP_STATE), "static func notificationUnreadDidChange(_ count: Int) {"
    )
    assert "setBadgeCount" in body, (
        "the badge write is not in the broadcast every path already calls, so reading the "
        "inbox cannot clear the icon"
    )


def test_clearing_the_badge_is_not_guarded_away_by_falsiness():
    """`count == 0` is THE case that matters — it is what clears the badge. A truthiness
    guard would leave the icon showing the last non-zero number forever."""
    body = _balanced(
        _read(_APP_STATE), "static func notificationUnreadDidChange(_ count: Int) {"
    )
    assert "guard count > 0" not in body and "if count > 0" not in body, (
        "a falsiness guard is back around the badge write — zero never reaches the icon"
    )


def test_mark_as_read_actually_marks_something_read():
    """The action is registered on all SIX categories, so every notification the app sends
    offers it — and it fired one analytics event and returned. It could not have worked:
    the payload identified no row."""
    body = _balanced(
        _read(_APP_DELEGATE), "if response.actionIdentifier == Action.markRead {"
    )
    assert "markReadFromNotificationAction" in body, (
        "the Mark as Read branch is a no-op again — a button on every notification that "
        "does nothing"
    )
    assert 'info["dedup_key"]' in body, (
        "the branch no longer reads the dedup key, which is the only thing in the payload "
        "that identifies the row"
    )


def test_mark_as_read_never_fires_a_request_without_a_token_in_memory():
    """This runs from `didReceive` for a NON-foreground action, which iOS may service by
    launching the app in the BACKGROUND with no scene — before `restoreSession` has put a
    token on `APIClient`. Firing anyway 401s, and `.unauthorized` sets
    `triggersTokenRefresh`, so a button tap would kick off a refresh from a process about
    to be suspended.

    Skipping costs nothing durable: Alerts marks everything read on sight.
    """
    body = _balanced(
        _read(_INBOX_VM), "func markReadFromNotificationAction(dedupKey: String?) async {"
    )
    guard = body.find("currentAuthToken()")
    call = body.find("repository.markRead(")
    assert guard != -1, (
        "the token check is gone — this can now 401 from a background launch and trigger "
        "a token refresh the process will not survive"
    )
    assert guard < call, (
        "the request is issued BEFORE the token is checked, which is the same thing as "
        "not checking it"
    )
    # auth.md §8: one token source. The Keychain and the client deliberately diverge
    # during `.restoring`, so a Keychain reader authenticates as an account the UI is
    # currently rendering as a guest.
    assert "Keychain" not in body


def test_the_notifications_screen_tells_a_guest_why_nothing_can_arrive():
    """Every control on that screen is inert for a guest — `device_tokens` is FK-bound to
    `public.users` so they cannot hold a push token, and `/me/settings` is
    `.signInRequired` so their toggles never reach the server. It rendered identically to
    a signed-in user's.

    NOT gated: auth.md §1a — a login wall in front of browsing risks a 5.1.1(v) rejection,
    and the choices genuinely are kept until sign-in.
    """
    banner = _read(_BANNER)
    assert "needsAccount" in banner and "Sign In" in banner
    view = _read(_SETTINGS_VIEW)
    assert "needsAccount: needsAccount" in view, (
        "the settings screen no longer passes the guest state to the banner"
    )
    body = _balanced(view, "private var needsAccount: Bool {")
    # auth.md §5: `.restoring` renders like a guest but holds a real credential. Telling
    # that user to sign in is the exact defect that rule names, and the restore backoff
    # runs indefinitely — the window is not brief.
    assert "hasUnusedStoredCredential" in body, (
        "a session that is merely RECONNECTING is now told to sign in"
    )


def test_the_guest_notice_replaces_the_unregistered_device_notice():
    """A guest who granted permission during onboarding satisfies "authorized but no
    token" BY CONSTRUCTION — registration deliberately stashes the token until sign-in.
    Showing both would stack a second notice offering a Retry that cannot ever succeed."""
    view = _read(_SETTINGS_VIEW)
    assert "viewModel.deviceUnregistered && !needsAccount" in view, (
        "a guest now sees the 'this device isn't registered' notice with a Retry button "
        "that can never work, on top of the banner explaining the real reason"
    )


def test_mark_as_read_never_decrements_a_count_it_has_not_loaded():
    """The bug that hid behind an empty inbox.

    iOS services a non-foreground action by launching the app in the BACKGROUND with no
    scene, so `NotificationInboxViewModel.shared` is constructed fresh with
    `unreadCount == 0` and nothing has loaded the inbox. An optimistic
    `unreadCount = max(0, unreadCount - 1)` is therefore `max(0, -1) == 0`, and a user
    with five unread alerts watched ALL FIVE vanish from the app icon for marking one
    read — the badge wiped rather than decremented.

    It survived a simulator run because that account's server-side unread count was 0:
    the wrong answer and the right answer were the same number. There is no public getter
    for the current icon badge, so a correct local decrement is not available — the
    server's count is the only trustworthy number, and when the call is skipped or fails
    the badge must be left alone.
    """
    body = _balanced(
        _read(_INBOX_VM), "func markReadFromNotificationAction(dedupKey: String?) async {"
    )
    assert "unreadCount - 1" not in body and "unreadCount-1" not in body, (
        "the optimistic decrement is back — on a background launch it reads 0 and zeroes "
        "the badge instead of decrementing it"
    )
    # The ONLY badge write on this path is the server's authoritative count.
    assert body.count("notificationUnreadDidChange") == 1, (
        f"expected exactly one badge write (the server's), found "
        f"{body.count('notificationUnreadDidChange')}"
    )
    server_write = body.index("notificationUnreadDidChange")
    assert body.index("result.unreadCount") < server_write, (
        "the badge is written from something other than the server's response"
    )


def test_the_guest_banners_sign_in_button_is_not_swallowed_by_the_cover():
    """A button that does nothing is the defect this whole pass exists to remove.

    `NotificationsSettingsView` lives inside the Account `fullScreenCover`, and
    `appState.signInPrompt` is bound to a `.sheet(item:)` on the ROOT — which cannot
    present while a cover is up. Routing through `AppActions.requestSignIn(for:)` here is
    therefore silently inert; verified on the simulator, where tapping it did nothing at
    all. `ErrorPresentationHost` hit the same wall and documents it; its fix is a
    cover-local sheet presenting `SignInView` directly, which is what this mirrors.
    """
    view = _read(_SETTINGS_VIEW)
    assert "AppActions.shared.requestSignIn" not in view, (
        "the sign-in request routes through the root's prompt again — from inside the "
        "Account fullScreenCover that presentation is dropped and the button is a no-op"
    )
    assert "showSignIn = true" in view and "SignInView()" in view, (
        "the cover-local SignInView presentation is gone"
    )
    assert ".sheet(isPresented: $showSignIn)" in view


def test_sign_in_closes_itself_so_no_presenter_can_forget():
    """`SignInView.submit()` succeeds and does nothing but stop the spinner.

    A plain `.sheet(isPresented:)` never closes on its own, so FOUR of the five presenters
    left the user staring at a filled-in "Welcome back" form after authentication had
    already SUCCEEDED — indistinguishable from a silent failure, escapable only by swiping.
    Reachable from an error toast's "Sign In" action, from Profile, from the Notifications
    guest banner, and from the root's own error path. Only `SignInRequiredSheet` closed,
    via its own `.onChange`.

    Fixed in `SignInView` itself rather than at each call site, so a fifth presenter cannot
    reintroduce it — which is why this guard reads that file and not the callers.
    """
    src = _read(_IOS / "Views" / "Screens" / "SignInView.swift")
    assert "@Environment(\\.dismiss)" in src, "SignInView cannot dismiss itself"
    body = _balanced(src, "var body: some View {")
    assert "onChange(of: appState.auth.isAuthenticated)" in body and "dismiss()" in body, (
        "SignInView no longer closes when the session arrives, so every presenter using a "
        "plain `.sheet(isPresented:)` strands the user on a form they have already submitted"
    )
    # `.onChange`, never `.task`/`.onAppear`: it must fire on the TRANSITION, or a view
    # presented while already authenticated dismisses itself instantly.
    dismiss_at = body.index("onChange(of: appState.auth.isAuthenticated)")
    window = body[dismiss_at - 200:dismiss_at]
    assert ".task {" not in window and ".onAppear" not in window


def test_a_session_ending_clears_the_app_icon_badge_too():
    """auth.md §7: a store keyed without a user id must be reset when a session ends.

    The icon badge is the most durable such store in the app — iOS owns it, it survives
    the process, and it is visible without launching anything. Zeroing
    `unreadNotificationCount` clears the TAB badge only, so the next account to sign in on
    this phone opened to a home screen still showing the previous user's count, behind
    rows that no longer exist for them.
    """
    body = _balanced(_read(_APP_STATE), "func discardDataForEndedSession() {")
    assert "unreadNotificationCount = 0" in body
    assert "AppState.notificationUnreadDidChange(0)" in body, (
        "the app-icon badge survives sign-out — the tab clears, the icon keeps the "
        "previous account's number"
    )
