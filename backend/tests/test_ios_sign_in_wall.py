"""The sign-in wall, and the four ways it silently fails to be one.

WHY THIS FILE EXISTS. The backend half of account-only is proven by
`test_account_only_licence_gate.py`, which issues real requests and asserts 401. The CLIENT
half has no equivalent: there is no XCTest target, and every failure mode below leaves an app
that still compiles, still launches, and still looks right in the simulator run you happen to
try.

The four:

  1. **`.restoring` routed to the wall.** It means "we hold a credential we could not yet
     validate" — a cold launch on a flaky network. Send it to `SignInView` and a signed-in
     user is asked to sign in again while their session is mid-restore. You would only see it
     on a bad network, which is exactly where nobody tests.
  2. **Onboarding running before sign-in.** It PUTs the investor profile, and that route is
     `.signInRequired` now, so `APIClient` refuses before the request leaves the device. The
     user fills in five pages and nothing is saved — no error, no log, nothing on screen.
  3. **The wall arm deleted.** The app falls back to rendering the tab bar for a signed-out
     user. Every screen then 401s, which reads as "the app is broken", not "please sign in".
  4. **The widget keeping FMP prices after sign-out.** The App Group snapshot outlives the
     session, so a signed-out device keeps showing licensed market data on its Home Screen —
     the same licence problem as an open route, one process further out.

Per `.claude/rules/testing.md` §3 every scan is comment-stripped and brace-bounded, and the
file was mutation-tested by hand — see MUTATION_LOG.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"

_APP = _IOS / "iosApp.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_WIDGET_REFRESH = _IOS / "Core" / "Services" / "WidgetRefreshService.swift"
_WIDGET_STORE = _REPO / "frontend" / "ios" / "Shared" / "WidgetSnapshotStore.swift"
_SIGNIN_SHEET = _IOS / "Views" / "Organisms" / "SignInRequiredSheet.swift"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    r"""Drop `//` lines and trailing `//` tails, blanking rather than deleting.

    Testing.md rule 1, and acute here: the wall's own comment block explains at length what
    `.restoring` must NOT do, naming `SignInView` and `.unauthenticated` repeatedly. An
    un-stripped scan would be satisfied by that prose with the arm deleted.

    `\s//` not `//`: a bare `//` mangles the `https://` in nearby literals. Blanking keeps line
    numbers intact so failures stay quotable.
    """
    out = []
    for line in src.splitlines():
        out.append("" if line.strip().startswith("//") else re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of the declaration starting at `header`."""
    start = src.find(header)
    assert start != -1, f"{header!r} not found — this scan has drifted"
    open_brace = src.index("{", start)
    depth = 0
    for i in range(open_brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_brace : i + 1]
    pytest.fail(f"unbalanced braces after {header!r}")


def _root_body() -> str:
    return _decl_block(_strip_comments(_read(_APP)), "var body: some View")


# ── 1. The wall exists, and only `.unauthenticated` reaches it ───────────────────────

def test_the_unauthenticated_arm_renders_the_sign_in_screen():
    body = _root_body()
    m = re.search(
        r"else if appState\.auth\.status == \.unauthenticated \{(.*?)\} else \{", body, re.S
    )
    assert m, (
        "RootView has no `.unauthenticated` arm — a signed-out user falls through to the tab "
        "bar, where every screen 401s and the app reads as broken rather than as gated"
    )
    assert "SignInView()" in m.group(1), m.group(1)


def test_restoring_is_not_routed_to_the_wall():
    """`.restoring` must render the container, NOT the sign-in screen.

    Asserted on the arm CONDITION rather than by looking for the word `.restoring` anywhere:
    the condition is what decides, and `.restoring` appears in this file's prose either way.
    """
    body = _root_body()
    m = re.search(r"else if (appState\.auth\.status == \.[a-zA-Z]+)", body)
    assert m, "no `else if` arm found between the splash and the container"
    condition = m.group(1)
    assert condition == "appState.auth.status == .unauthenticated", (
        f"the wall arm tests {condition!r}. It must be `.unauthenticated` ONLY — `.restoring` "
        "means we hold a credential we could not yet validate, and showing a login screen "
        "there asks an already-signed-in user to sign in again on every flaky launch."
    )


def test_the_container_still_serves_two_states_from_one_arm():
    """Identity: `.authenticated` and `.restoring` share an arm, so a reconnect does not tear
    the view tree down and reset the selected tab and every ViewModel."""
    body = _root_body()
    assert body.count("RootContainerView()") == 1, (
        "RootContainerView is rendered from more than one branch — each ViewBuilder branch has "
        "its own SwiftUI identity, so the tree is destroyed and rebuilt when the session heals"
    )


# ── 2. Onboarding runs AFTER sign-in ─────────────────────────────────────────────────

def test_onboarding_is_gated_on_being_signed_in():
    """`OnboardingView` PUTs the investor profile, which is `.signInRequired`."""
    src = _strip_comments(_read(_APP))
    m = re.search(r"if hasAcknowledgedDisclaimers, !hasCompletedOnboarding,(.*?)\{", src, re.S)
    assert m, "the onboarding overlay condition has moved — this scan has drifted"
    condition = m.group(1)
    assert "appState.auth.isAuthenticated" in condition, (
        "onboarding is not gated on being signed in. It runs over the wall, its profile PUT is "
        "refused by APIClient before leaving the device, and every answer is dropped silently."
    )


def test_the_disclaimer_still_comes_first():
    """Legal acknowledgement precedes both the wall and onboarding. Onboarding's own condition
    requires `hasAcknowledgedDisclaimers`, which is what orders the two overlays."""
    src = _strip_comments(_read(_APP))
    assert "if !hasAcknowledgedDisclaimers" in src
    assert "if hasAcknowledgedDisclaimers, !hasCompletedOnboarding" in src


# ── 3. No FMP prices survive on the Home Screen after sign-out ───────────────────────

def test_the_widget_snapshot_is_wiped_when_a_session_ends():
    """The App Group snapshot holds FMP prices and is read by a separate process.

    If it outlives the session, a signed-out device keeps rendering licensed market data on its
    Home Screen — the same breach as an open route, one process further out, and invisible from
    inside the app.
    """
    clear = _decl_block(_strip_comments(_read(_WIDGET_REFRESH)), "func clearForEndedSession()")

    # ⚠️ `clearAll()`, and this assertion was VACUOUS until 2026-09-07. It used to accept
    # `WidgetSnapshotStore.clear()`, which nils the PORTFOLIO snapshot and deliberately KEEPS a
    # non-empty market one — so the guard passed while the exact thing it names in its own
    # docstring (FMP prices on a signed-out Home Screen) went on happening. Keeping the market
    # blob was correct while `/widget/market-mover` was public; End-User Display Rights ended
    # that. A test that asserts a call by NAME proves nothing about what the call does — see
    # `.claude/rules/testing.md` §3.
    assert "WidgetSnapshotStore.clearAll()" in clear, (
        "clearForEndedSession no longer wipes BOTH snapshots — `clear()` keeps the market one, "
        "so FMP prices stay on the Home Screen after sign-out"
    )
    # The other half of the same breach: with the token left behind, the extension keeps
    # SUCCESSFULLY refreshing licensed market data onto a device with no session.
    assert "WidgetAPIConfig.clearWidgetToken()" in clear, (
        "the widget token outlives the session — the extension can still fetch FMP data"
    )
    assert "inFlight?.cancel()" in clear, (
        "an in-flight refresh is not cancelled, so it can re-publish the ended session's data "
        "immediately after the wipe"
    )

    # And prove `clearAll` is not itself a synonym for the half-wipe. Reading the store's own
    # source is what closes the loop: the assertion above is a name, this is the behaviour.
    store = _strip_comments(_read(_WIDGET_STORE))
    clear_all = _decl_block(store, "static func clearAll()")
    assert "removeObject(forKey: WidgetSharedConfig.snapshotKey)" in clear_all, (
        "clearAll no longer removes the whole envelope"
    )
    plain_clear = _decl_block(store, "static func clear()")
    assert "envelope.portfolio = nil" in plain_clear, (
        "`clear()` changed shape — re-check that `clearForEndedSession` still needs clearAll; "
        "this test's whole premise is that the two differ"
    )
    discard = _decl_block(
        _strip_comments(_read(_APP_STATE)), "func discardDataForEndedSession()"
    )
    assert "WidgetRefreshService.shared.clearForEndedSession()" in discard, (
        "sign-out does not reach the widget — auth.md §7: every device-global store must be "
        "reset when a session ends"
    )


# ── 4. The sheet no longer promises a signed-out app ─────────────────────────────────

def test_the_sign_in_sheet_does_not_claim_the_app_works_signed_out():
    """It used to read "Everything else in the app stays available without one." """
    code = _strip_comments(_read(_SIGNIN_SHEET))
    for claim in ("stays available without one", "without an account"):
        assert claim not in code, (
            f"SignInRequiredSheet still tells the user {claim!r} while the app is account-only"
        )


# ── MUTATION_LOG ─────────────────────────────────────────────────────────────────────
#
# Hand-run 2026-09-07 (testing.md §3 rule 3). Each applied, the file run, then reverted.
#
#  1. Wall arm condition changed to `.restoring` — the plausible "handle both signed-out
#     states" edit. -> 2 FAILED (test_restoring_is_not_routed_to_the_wall AND
#     test_the_unauthenticated_arm_renders_the_sign_in_screen)  ✅
#  2. Wall arm deleted entirely, falling back to RootContainerView.
#       -> 2 FAILED (the arm test AND test_the_container_still_serves_two_states_from_one_arm,
#          which is the identity guard noticing there is now only one branch)  ✅
#  3. `appState.auth.isAuthenticated` removed from the onboarding condition — the revert that
#     restores the old first-run order. -> test_onboarding_is_gated_on_being_signed_in FAILED ✅
#  4. `WidgetSnapshotStore.clear()` removed from clearForEndedSession.
#       -> test_the_widget_snapshot_is_wiped_when_a_session_ends FAILED  ✅
#  5. Anti-vacuity: the banned sentence written back into a COMMENT in SignInRequiredSheet with
#     the user-visible string left correct. -> still passed ✅ (the scan reads code, not prose)
