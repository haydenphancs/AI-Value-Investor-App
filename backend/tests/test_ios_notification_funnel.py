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
