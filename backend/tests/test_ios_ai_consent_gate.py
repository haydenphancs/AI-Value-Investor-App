"""The third-party-AI consent gate must survive (App Review 5.1.2(i)/5.1.2(ii)).

WHY THIS FILE EXISTS. Guideline 5.1.2(i) (added 13 November 2025) requires an app to "clearly
disclose where personal data will be shared with third parties, including with third-party AI,
and obtain explicit permission before doing so." Cay AI chat sends the user's typed message to
an external provider. The entire enforcement of that permission is TWO call sites to one
private helper in `ChatViewModel`, and until this file nothing pinned any of it — a grep for
`AIConsentStore` / `needsAIConsent` / `holdForConsent` across `backend/tests/` returned zero
hits, while `documents/legal/privacy.html` §3 makes three affirmative promises about it to
every reader. `tests/test_legal_pages.py` pins the promises; this pins the code that has to be
true for them.

The gate is easy to delete by accident and impossible to notice: removing it does not break
chat, it makes chat work MORE smoothly. Nobody files that bug.

Four things must hold:
  1. Both send paths hold the send BEFORE any state mutation or network call — and there are
     exactly two of them. `sendMessage` is not a funnel: `startNewConversation(firstMessage:)`
     seeds and sends directly, and it is the primary path (Deep Research, AI Analyst, report
     chat, suggestion chips).
  2. `holdForConsent` itself still gates. Stubbing it to `return false` leaves both call-site
     assertions green and the gate dead — so it is asserted separately.
  3. `withdraw()` and `resetForEndedSession()` clear BOTH UserDefaults keys and both published
     values. The keys are device-global with no user id in them, so a missed reset hands the
     next account on the phone a consent it never gave — the same bug class as the stores in
     `AppState.discardDataForEndedSession()` (`.claude/rules/auth.md` §7).
  4. The withdrawal affordance exists, in the place the privacy policy tells the user to look:
     Settings → General → "AI Chat Data Permission". 5.1.2(ii) requires withdrawal to be
     accessible, and `privacy.html` names that row by its exact label.

Per `.claude/rules/testing.md` §3: every scan is comment-stripped, brace-bounded to the
declaration it means to check, and mutation-tested by hand — see MUTATION_LOG at the bottom.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"

_CHAT_VM = _IOS / "ViewModels" / "ChatViewModel.swift"
_CONSENT_STORE = _IOS / "Core" / "Services" / "AIConsentStore.swift"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_SETTINGS = _IOS / "Views" / "Screens" / "AppSettingsView.swift"
_CHAT_SCREEN = _IOS / "Views" / "Screens" / "AIChatScreen.swift"
_SERVED_PRIVACY = _REPO / "backend" / "app" / "templates" / "legal" / "privacy.html"

# The row label is a THREE-way contract: the Settings row, the privacy policy that tells the
# user where to find it, and this test. Renaming the row without amending the policy points a
# 5.1.2(ii) promise at a control that no longer exists under that name.
_SETTINGS_ROW_TITLE = "AI Chat Data Permission"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_comments(src: str) -> str:
    r"""Drop `//` lines and trailing `//` tails, blanking rather than deleting.

    Testing.md rule 1, and it is acute here rather than hypothetical: every token this file
    greps for also appears in a nearby comment. `AIConsentStore`'s header names 5.1.2(i) and
    `resetForEndedSession`; `ChatViewModel` says "Third-party AI consent gate (App Review
    5.1.2(i))" IMMEDIATELY above each of the two calls; `AIChatScreen` names
    `AIDataConsentView` in prose. An un-stripped scan would stay green with all four gates
    deleted, satisfied by the changelog explaining why they used to exist.

    `\s//` not `//` in the tail pattern: a bare `//` mangles every `"https://…"` literal.
    Blanking preserves line numbers so failure messages stay quotable.
    """
    out = []
    for line in src.splitlines():
        out.append("" if line.strip().startswith("//") else re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _code(path: Path) -> str:
    """Comment-stripped source.

    Strip BEFORE brace-matching: a stray brace inside a comment would otherwise unbalance the
    match and silently widen every bounded scan below.
    """
    return _strip_comments(_read(path))


def _decl_block(src: str, header: str) -> str:
    """The brace-balanced body of the declaration starting at `header`.

    Testing.md rule 2. Asserting against a whole FILE passes when the token lives in a
    different declaration — `AppSettingsView.swift` has many sections, and a consent row moved
    out of `generalSection` would still satisfy a file-wide scan while making the privacy
    policy's stated location ("Settings → General") false.

    Deliberately NOT `src[start:src.index(token, start)]`. Bounding a window with the very
    token you are asserting is present is circular: delete the token and the window grows until
    it finds one somewhere else.
    """
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


# ── 1. Both send paths hold the send before anything leaves the device ────────

_SEND_PATHS = {
    # header → (network token, state-mutation token)
    "func startNewConversation(": ("createChatSession", "messages = [userMessage]"),
    "func sendMessage(_ text: String)": ("respond(sessionId:", "messages.append(userMessage)"),
}


@pytest.mark.parametrize("header", sorted(_SEND_PATHS))
def test_both_send_paths_hold_for_consent_before_transmitting(header):
    """`sendMessage` is NOT a funnel.

    `startNewConversation(firstMessage:)` creates the session and sends directly without
    re-entering it, and it is the path Deep Research, AI Analyst, report chat and the
    suggestion chips all take — gating only `sendMessage` would leave the gate bypassed on
    almost every real entry point. Both, or neither is worth anything.
    """
    network, mutation = _SEND_PATHS[header]
    body = _decl_block(_code(_CHAT_VM), header)

    hold = body.find("holdForConsent(")
    assert hold != -1, (
        f"{header} no longer calls holdForConsent. User-typed text now reaches a third-party "
        f"AI provider with no explicit permission — App Review 5.1.2(i), and privacy.html §3 "
        f"promises the opposite in three separate sentences."
    )

    # The return value must be HONOURED. `holdForConsent(...)` as a bare statement records the
    # pending send, shows the sheet, and then sends anyway.
    assert re.search(r"if holdForConsent\(.*?\)\)\s*\{\s*return", body, re.S), (
        f"{header} calls holdForConsent but does not return on it — the send proceeds while "
        f"the consent sheet is being presented"
    )

    for token, why in ((network, "network call"), (mutation, "conversation state mutation")):
        at = body.find(token)
        assert at != -1, f"scan drifted — {header} no longer contains {token!r}"
        assert hold < at, (
            f"the consent gate in {header} runs AFTER the {why} ({token!r}). A held send must "
            f"leave the conversation exactly as it was and must transmit nothing."
        )
    # NOT asserted: precedence over `Analytics.shared.track(.chatSent, …)` in `sendMessage`,
    # which deliberately precedes the gate. It is a COUNT — the message body is never a prop —
    # and it goes to our own analytics, not to the AI provider.


def test_there_are_exactly_two_gated_send_paths():
    """A tripwire, on purpose.

    Three occurrences: the declaration and the two call sites. A new send path that forgets the
    gate leaves this at 3 while the parametrized test above still passes on the two paths it
    knows about — this is the only assertion that notices.
    """
    assert _code(_CHAT_VM).count("holdForConsent(") == 3, (
        "the number of holdForConsent references changed. If you added a send path, gate it "
        "and bump this number; if you removed one, confirm it did not transmit user text."
    )


def test_hold_for_consent_actually_gates():
    """THE assertion the others depend on.

    Stubbing this body to `return false` leaves every call-site assertion above green and the
    gate completely dead. It is asserted separately for exactly that reason.
    """
    body = _decl_block(_code(_CHAT_VM), "private func holdForConsent(")
    assert "AIConsentStore.shared.hasConsented" in body, (
        "holdForConsent no longer reads the persisted consent — it cannot be gating on anything"
    )
    assert "return false" in body and "return true" in body, (
        "holdForConsent has lost one of its two outcomes; a helper that always returns false "
        "never holds a send, and one that always returns true bricks chat"
    )
    assert "needsAIConsent = true" in body, (
        "holdForConsent no longer raises needsAIConsent, so AIChatScreen never presents the "
        "consent sheet — the send is silently swallowed and chat looks broken"
    )


def test_the_chat_screen_presents_the_consent_sheet():
    """The other end of `needsAIConsent`.

    Without this layer a held send is a dead end: nothing is transmitted (good) and nothing is
    shown (a bug that reads as chat being broken).
    """
    block = _decl_block(_code(_CHAT_SCREEN), "if viewModel.needsAIConsent")
    assert "AIDataConsentView(" in block, "the consent layer no longer renders AIDataConsentView"
    assert "grantAIConsentAndResume()" in block, "Allow no longer resumes the held send"
    assert "declineAIConsent()" in block, "Decline no longer discards the held send"


# ── 2. Withdrawal and session-end clear both device-global keys ───────────────

def test_the_consent_keys_are_still_the_two_this_guard_knows_about():
    """Anti-vacuity for this section.

    Every assertion below is bounded to a function that references `Keys.granted` /
    `Keys.grantedAt`; if those constants were renamed the scans would still pass while
    checking nothing.
    """
    keys = _decl_block(_code(_CONSENT_STORE), "private enum Keys")
    assert 'static let granted = "ai_processing_consent_granted"' in keys
    assert 'static let grantedAt = "ai_processing_consent_granted_at"' in keys


@pytest.mark.parametrize("header", ["func withdraw()", "func resetForEndedSession()"])
def test_dropping_consent_clears_both_keys_and_both_published_values(header):
    """Both keys, both times.

    Clearing only `granted` leaves a stale `grantedAt`, so Settings would report an
    "Allowed <date>" audit trail for a consent that is not held — and `grant()` writes both, so
    a partial clear is a genuine divergence rather than a tidiness one. Clearing the defaults
    without flipping `hasConsented` is worse: `holdForConsent` reads the @Published value, so
    the gate would not re-arm until the next launch.

    `withdraw()` writes `false` while `resetForEndedSession()` removes the key; both are
    correct, so this asserts on the key REFERENCE rather than on either spelling.
    """
    body = _decl_block(_code(_CONSENT_STORE), header)
    assert "Keys.granted" in body, f"{header} no longer touches the consent key"
    assert "Keys.grantedAt" in body, (
        f"{header} leaves the grant TIMESTAMP behind; Settings would keep showing an "
        f"'Allowed <date>' audit trail for a consent that is no longer held"
    )
    assert "hasConsented = false" in body, (
        f"{header} does not flip the published flag, so ChatViewModel.holdForConsent keeps "
        f"reading a stale true and the gate never re-arms this session"
    )
    assert "grantedAt = nil" in body, f"{header} leaves the published timestamp set"


def test_grant_writes_the_same_two_keys():
    """The inverse, so the pair above cannot be satisfied by a store that writes nothing."""
    body = _decl_block(_code(_CONSENT_STORE), "func grant()")
    assert "defaults.set(true, forKey: Keys.granted)" in body
    assert "Keys.grantedAt" in body
    assert "hasConsented = true" in body


def test_consent_is_dropped_when_a_session_ends():
    """`.claude/rules/auth.md` §7, one more time.

    Both keys are device-global with NO user id, so without this the next account to sign in on
    this phone inherits the previous user's "Allow" — `holdForConsent` sees
    `hasConsented == true`, the sheet never presents, and that person's first message is sent
    for AI processing having never been asked.
    """
    block = _decl_block(_code(_APP_STATE), "private func discardDataForEndedSession()")

    # Anti-vacuity: prove this is the real funnel and not an empty or renamed block.
    assert "LearnIdentityEpoch.bump()" in block, "scan drifted — this is not the session-end funnel"
    assert "WhaleService.shared" in block, "scan drifted — this is not the session-end funnel"

    assert "AIConsentStore.shared.resetForEndedSession()" in block, (
        "AI consent is not cleared when a session ends. Consent is per person and cannot be "
        "inherited from whoever held the phone before."
    )



def test_sign_out_routes_through_the_session_end_funnel():
    """The consent reset is only as good as the funnel it lives in.

    `signOut()` clearing stores directly instead of delegating would reintroduce exactly the
    divergence the funnel exists to prevent — four session-end paths drifting apart again.
    """
    assert "discardDataForEndedSession()" in _decl_block(_code(_APP_STATE), "func signOut()"), (
        "signOut() no longer routes through discardDataForEndedSession() — the other "
        "session-end paths and this one would drift apart again"
    )

def test_no_surface_writes_the_consent_keys_directly():
    """`AIConsentStore` must stay the only writer.

    A `UserDefaults.standard.set(true, forKey: "ai_processing_consent_granted")` anywhere else
    bypasses the published flag and every guard in this file at once.
    """
    for path in sorted(_IOS.rglob("*.swift")):
        if path.name == "AIConsentStore.swift":
            continue
        assert "ai_processing_consent" not in _strip_comments(path.read_text(encoding="utf-8")), (
            f"{path.relative_to(_IOS)} touches the consent UserDefaults key directly. Route it "
            f"through AIConsentStore so the @Published flag and the audit timestamp stay in sync."
        )


# ── 3. The withdrawal affordance exists, where the policy says it does ────────

def test_the_settings_row_offers_withdrawal_from_the_general_section():
    """5.1.2(ii)/5.1.1(ii) require an accessible way to withdraw.

    Brace-bounded to `generalSection` rather than the file: the privacy policy names the
    LOCATION, not just the control, so a row that survives in a different section makes a
    published promise false while a file-wide scan stays green.
    """
    block = _decl_block(_code(_SETTINGS), "private var generalSection: some View")

    # Anti-vacuity: this must still be the real General section, not an empty stub.
    assert "AppSpacing" in block and len(block) > 1500, (
        "scan drifted — generalSection is not the real section"
    )

    assert f'title: "{_SETTINGS_ROW_TITLE}"' in block, (
        f"the {_SETTINGS_ROW_TITLE!r} row is gone from Settings → General. privacy.html §3 "
        f"tells every reader to withdraw permission there, and 5.1.2(ii) requires the control "
        f"to be accessible."
    )
    assert "aiConsent.hasConsented" in block, "the row no longer reflects the live consent state"
    assert "showWithdrawAIConsentConfirmation = true" in block, (
        "the row no longer opens the withdrawal confirmation — it is now decorative"
    )


def test_the_withdraw_confirmation_actually_withdraws():
    """Bounded to the alert's ACTION closure, not the file.

    The `message:` closure immediately below it describes withdrawal in prose; a file-wide scan
    would be satisfied by the description of the thing instead of the thing.
    """
    actions = _decl_block(
        _code(_SETTINGS),
        '.alert("Withdraw AI Chat Permission", isPresented: $showWithdrawAIConsentConfirmation)',
    )
    assert "aiConsent.withdraw()" in actions, (
        "the Withdraw button no longer calls AIConsentStore.withdraw() — Settings shows a "
        "withdrawal control that does nothing"
    )
    assert "role: .cancel" in actions, "the destructive alert lost its Cancel"


def test_the_privacy_policy_names_the_row_that_actually_exists():
    """The three-way contract, closed.

    `tests/test_legal_pages.py` asserts the policy STATES a withdrawal location; this asserts
    the location it states is REAL. Reads the served template from disk — no client, no network.
    """
    policy = _read(_SERVED_PRIVACY)
    assert _SETTINGS_ROW_TITLE in policy, (
        f"privacy.html no longer names {_SETTINGS_ROW_TITLE!r}. Either the policy or the "
        f"Settings row was renamed without the other; the policy directs users to a control "
        f"by name and 5.1.2(ii) requires that control to be findable."
    )


# MUTATION_LOG — hand-verified 2026-09-03 (.claude/rules/testing.md §3). Every line below was
# actually observed RED before being written down; the suite restored green after each.
#
#   ChatViewModel: delete the `if holdForConsent(…) { return true }` at :338
#                                       => RED  (startNewConversation param + the count tripwire)
#   ChatViewModel: delete the `if holdForConsent(…) { return }` at :436
#                                       => RED  (sendMessage param + the count tripwire)
#   ChatViewModel: move the :436 gate BELOW `messages.append(userMessage)`
#                                       => RED  (ordering, not mere presence)
#   ChatViewModel: `)) { return }` -> `))`, so the return is not honoured
#                                       => RED  (the return-honoured regex)
#   ChatViewModel: holdForConsent's guard -> `if true { return false }`
#                                       => RED  (hold_for_consent_actually_gates) — and BOTH
#                                               call-site tests stayed GREEN, which is exactly
#                                               why that assertion is separate
#   ChatViewModel: add a third, ungated `holdForConsent(` call
#                                       => RED  (the count tripwire; nothing else notices)
#   AIChatScreen: `AIDataConsentView(` -> `EmptyView(`
#                                       => RED  (presents_the_consent_sheet)
#   AIConsentStore: withdraw() loses `defaults.removeObject(forKey: Keys.grantedAt)`
#                                       => RED  (withdraw param only; reset param stayed green)
#   AIConsentStore: Keys.granted literal -> "ai_consent_v2"
#                                       => RED  (the keys anti-vacuity test)
#   AppState: delete `AIConsentStore.shared.resetForEndedSession()` at :1119
#                                       => RED  (session_end)
#   AppSettingsView: PHYSICALLY MOVE the whole consent row out of `generalSection` and into
#                    `securitySection` — the row still exists, and a file-wide scan would still
#                    find its title
#                                       => RED  (settings_row). This is the brace-bounding proof:
#                                               testing.md rule 2, and the privacy policy names
#                                               the LOCATION, not just the control.
#   AppSettingsView: alert's `{ aiConsent.withdraw() }` -> `{}`
#                                       => RED  (withdraw_confirmation) — bounded to the ACTION
#                                               closure, so the `message:` prose below it cannot
#                                               satisfy the scan
#   CRITICAL — neutralise `_strip_comments` to the identity function, THEN delete the :338 gate
#                                       => still RED. testing.md rule 1: if this had gone green,
#                                          the "Third-party AI consent gate (App Review
#                                          5.1.2(i))" comment sitting directly above each call
#                                          would have been satisfying the scan on its own, and
#                                          this whole file would prove nothing.
