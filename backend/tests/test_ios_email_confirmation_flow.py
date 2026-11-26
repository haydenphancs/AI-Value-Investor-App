"""The iOS half of email sign-up confirmation, and the sign-in hardening that shipped with it.

THE INCIDENT (2026-11-26, owner's iPhone, production). Every new email/password account was
stuck. Supabase's "Confirm signup" email carried an 8-digit code and no link; the app told the
user to click a link, had no screen and no route that could take a sign-up code, and
`AuthService.signIn` silently re-sent the email on EVERY attempt — appending "We've just sent you
a fresh confirmation link" to the backend's own sentence (the doubled red text), rotating the
code in the inbox each time, and spending the 3-an-hour resend budget before the user tapped
anything. The whole auth suite was green throughout (TG-1, TG-4): nothing pinned the iOS path.

What this file pins, each against the regression that would reopen it:

  * sign-in rethrows EMAIL_NOT_CONFIRMED unchanged — no resend, no `try?` (IOS-AUTH-02, TG-4);
  * SignInView routes EMAIL_NOT_CONFIRMED, and a sign-up that needs confirming, to the code
    screen (IOS-AUTH-01);
  * ConfirmEmailView takes a `.oneTimeCode` and confirms with it; its resend is explicit,
    in-flight guarded, cooled down and never claims a send (IOS-AUTH-03);
  * `AuthService.confirmEmail` adopts the session EXACTLY like `signIn`, and `AppState`'s twin
    mirrors `AppState.signIn` (TG-4 d);
  * a sign-in whose profile read fails after the tokens were stored is settled — `.restoring`
    or undone, generation-guarded — never left half signed in (IOS-AUTH-09, SOC-03);
  * AUTH_CODE_INVALID and the three 400 password codes map on the arm their status reaches
    (IOS-AUTH-08); 429 copy is in human units (IOS-AUTH-04); friendlyError goes through
    `AppError.from` (IOS-AUTH-07);
  * Apple's nonce: SHA-256 to Apple, the raw value to the backend, consumed by a `defer`
    (TG-10); social failures are logged and shown as fixed copy (SOC-06);
  * a fresh install purges only the session tokens (SEC-8); the DEBUG logger redacts codes,
    nonces and every `*_token` (SEC-9) — including a value holding a JSON-escaped quote
    (IOS-CONF-3); device proofs evict oldest-issued first (CC-08);
  * VoiceOver names and announcements on both screens (CC-05).

Second round (same day, review findings IOS-CONF-1/2, COMPAT-2, PW-6):

  * a code the server ACCEPTED is spent: AUTH_CONFIRMED_SIGN_IN_FAILED (and, on this route,
    AUTH_PASSWORD_REJECTED) locks the code field, Confirm and "Send a new code" and makes Sign in
    the primary action — never on AUTH_UNAVAILABLE, which also comes back BEFORE the code is
    checked, while it is still good; AppError gives both post-verify codes a typed,
    non-retryable branch on the arm their 503 / 400 reaches (IOS-CONF-1, C2);
  * the sign-up response's sentence ("Already have an account? Sign in…") reaches the code
    screen, unbranched on, and the sign-in link reads "Already confirmed, or have an account?
    Sign in" (IOS-CONF-2, C5);
  * an unstructured 404 (an app newer than its server) gets honest copy and a contract-drift
    WARNING with no address or code in it (COMPAT-2);
  * the sign-up show / hide eye is a 44-pt target that keeps the keyboard up (PW-6).

Source scans (there is no XCTest target), per testing.md §3: comments stripped first (the fixes'
own comments quote every token these scans look for), every check brace-bound to its
declaration, and every guard mutation-tested in memory by `_MUTATIONS` below — patched through
`Path.read_text`, never on disk, because other sessions read these Swift files concurrently.
"""

from __future__ import annotations

import pathlib
import re
import time
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_AUTH = _IOS / "Core/Services/AuthService.swift"
_APP = _IOS / "Core/State/AppState.swift"
_SIGN_IN = _IOS / "Views/Screens/SignInView.swift"
_CONFIRM = _IOS / "Views/Screens/ConfirmEmailView.swift"
_ENDPOINT = _IOS / "Core/Services/APIEndpoint.swift"
_APP_ERROR = _IOS / "Core/Utilities/AppError.swift"
_SOCIAL = _IOS / "Core/Services/SocialSignInService.swift"
_CLIENT = _IOS / "Core/Services/APIClient.swift"
_PROOFS = _IOS / "Core/Services/DeviceProofStore.swift"

# Declaration headers (each must occur exactly once in its comment-stripped file).
_AUTH_SIGN_IN = "func signIn(email: String, password: String) async throws -> UserProfile"
_AUTH_CONFIRM = "func confirmEmail(email: String, code: String, password: String) async throws -> UserProfile"
_AUTH_ADOPT = "private func adoptedSessionProfile(epoch: Int) async throws -> UserProfile"
_AUTH_DISCARD = "func discardAdoptedSession(epoch: Int) -> Bool"
_AUTH_PURGE = "func purgeSessionLeftByAnEarlierInstall() async"
_APP_SIGN_IN = "func signIn(email: String, password: String) async throws"
_APP_SIGN_UP = "func signUp("
_APP_CONFIRM = "func confirmEmail("
_APP_SOCIAL = "func completeSocialSignIn(_ result: SocialSignInResult) async throws"
_APP_SETTLE = "private func settleSessionWithoutProfile("
_APP_CONFIGURE = "func configure(apiClient: APIClient, authService: AuthService)"
_SHOW_CONFIRMATION = "private func showConfirmation(for address: String, codeJustSent: Bool, signUpMessage: String? = nil)"
_CONFIRM_CATCH_CODES = '"AUTH_CONFIRMED_SIGN_IN_FAILED", "AUTH_PASSWORD_REJECTED"'


# ── Scanning helpers (no shared conftest — testing.md) ────────────────────────────────

def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the comments beside these fixes quote `resendConfirmation(`, `try?`,
    `fetchCurrentUser()`, `EMAIL_NOT_CONFIRMED` and the old copy while explaining the bug, so an
    un-stripped scan passes on prose after the code is reverted. A tail needs leading
    whitespace, so the `//` of a `https://` literal survives.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _code(path: Path) -> str:
    assert path.exists(), f"expected to exist: {path}"
    return _strip_swift_comments(path.read_text(encoding="utf-8"))


def _block(src: str, header: str) -> str:
    """The brace-balanced body after the ONLY occurrence of `header`."""
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    start = src.index("{", src.index(header) + len(header) - 1)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"unbalanced braces after `{header}`")


def _between(src: str, start: str, end: str) -> str:
    i = src.index(start)
    return src[i : src.index(end, i + len(start))]


def _in_order(body: str, steps: tuple[str, ...]) -> bool:
    at = [body.find(s) for s in steps]
    return all(i != -1 for i in at) and at == sorted(at)


# ── 1. Sign-in never re-sends, and rethrows unchanged ─────────────────────────────────

def test_sign_in_rethrows_email_not_confirmed_without_resending():
    body = _block(_code(_AUTH), _AUTH_SIGN_IN)
    assert "endpoint: .signIn(email: email, password: password)" in body, (
        "not AuthService.signIn — this scan is bound to the wrong declaration")
    assert "resendConfirmation(" not in body, (
        "AuthService.signIn re-sends the confirmation email — every attempt rotates the code the "
        "user is about to type and spends the hourly resend budget")
    assert "try?" not in body, "AuthService.signIn swallows an error with `try?` (auth.md §6)"
    assert "EMAIL_NOT_CONFIRMED" not in body and "APIError.businessError(" not in body, (
        "AuthService.signIn rewrites the EMAIL_NOT_CONFIRMED error instead of rethrowing it")


# ── 2. SignInView routes to the code screen ───────────────────────────────────────────

def test_sign_in_view_routes_email_not_confirmed_to_the_code_screen():
    view = _code(_SIGN_IN)
    submit = _block(view, "private func submit()")
    catch = submit[submit.index("} catch {"):]
    m = re.search(r"if case \.emailNotConfirmed = AppError\.from\(error\) \{(.*?)\n\s*\}", catch, re.S)
    assert m and "showConfirmation(for: address, codeJustSent: false)" in m.group(1) \
        and re.search(r"\breturn\b", m.group(1)), (
        "SignInView no longer routes EMAIL_NOT_CONFIRMED to the code screen — the user gets a "
        "red line and no way to enter the code")
    assert catch.index(".emailNotConfirmed") < catch.index("errorMessage = friendlyError(error)"), (
        "SignInView no longer routes EMAIL_NOT_CONFIRMED to the code screen before the error line")
    assert re.search(r"if case \.needsEmailConfirmation\(let message\) = outcome \{\s*"
                     r"showConfirmation\(for: address, codeJustSent: true, signUpMessage: message\)", submit), (
        "a sign-up that needs confirming no longer opens the code screen")
    show = _block(view, _SHOW_CONFIRMATION)
    assert "pendingConfirmation = PendingConfirmation(" in show and "errorMessage = nil" in show, (
        "showConfirmation no longer opens the code screen with a clean error line")
    body = _block(view, "var body: some View")
    overlay = body[body.index(".overlay {"):]
    assert re.search(r"if let pending = pendingConfirmation \{\s*ConfirmEmailView\(", overlay), (
        "SignInView no longer presents ConfirmEmailView for a pending confirmation")
    call = overlay[overlay.index("ConfirmEmailView("):]
    assert "email: pending.email" in call[:400] and "password: password" in call[:400], (
        "the code screen is not handed the captured address and the in-memory password")
    for gone in ("confirmationPrompt", "pendingConfirmationMessage", "didResendConfirmation"):
        assert gone not in view, f"the old link-only overlay is back (`{gone}`)"


# ── 3. The code screen ────────────────────────────────────────────────────────────────

def test_the_code_screen_takes_a_one_time_code_and_confirms_with_it():
    cv = _code(_CONFIRM)
    field = _block(cv, "private var codeField: some View")
    assert ".textContentType(.oneTimeCode)" in field and ".keyboardType(.numberPad)" in field, (
        "the code field lost `.oneTimeCode` / `.numberPad` — AutoFill cannot offer the emailed code")
    confirm = _block(cv, "private func confirm()")
    assert "appState.confirmEmail(email: email, code: digits, password: password)" in confirm, (
        "ConfirmEmailView no longer confirms through AppState.confirmEmail with the typed code")
    assert "Self.codeLengths.contains(digits.count)" in confirm and "activity == nil" in confirm, (
        "ConfirmEmailView's confirm lost its length or in-flight guard")
    assert "static let codeLengths = 6...10" in cv, "the accepted code length drifted from 6...10"
    on_change = field[field.index(".onChange(of: code)"):]
    assert re.search(r"if Self\.digits\(in: oldValue, limit: Self\.fieldLimit\) != cleaned \{\s*errorMessage = nil",
                     on_change), (
        "typing a new code no longer clears the previous code's error — the Simulator showed "
        "'That code is invalid…' under a freshly typed, correct code")


def test_resend_is_explicit_guarded_cooled_down_and_never_claims_a_send():
    cv = _code(_CONFIRM)
    resend = _block(cv, "private func resend()")
    assert "appState.resendConfirmation(email: email)" in resend, "resend no longer sends — re-derive"
    assert re.search(r"guard activity == nil,[^\n]*secondsUntilResend\(at: Date\(\)\) == 0 else", resend) \
        and "activity = .resending" in resend, (
        "resend lost its in-flight / cooldown guard — a double tap spends two of the 3-an-hour sends")
    assert "resendAvailableAt = Date().addingTimeInterval(Self.resendCooldown)" in resend \
        and "static let resendCooldown: TimeInterval = 60" in cv, (
        "resend no longer starts its 60 s cooldown")
    assert 'static let resendNote = "If your email still needs confirming, a new code is on its way."' in cv \
        and "note = Self.resendNote" in resend, (
        "the resend note changed — it must stay hedged (the backend never says whether a code went out)")
    for claim in ("just sent", "We've sent", "we've sent", "Code sent", "code sent", "Email sent", "email sent"):
        assert claim not in cv, f"the code screen claims a send it cannot know about (`{claim}`)"


# ── 3b. A code the server accepted is spent (IOS-CONF-1, C2) ──────────────────────────

def test_a_spent_code_locks_the_code_step_and_promotes_sign_in():
    """AUTH_CONFIRMED_SIGN_IN_FAILED means the code WORKED: the address is confirmed and the code
    used up. Before, the screen only showed the message and left the code, Confirm and "Send a
    new code" live — a retry got AUTH_CODE_INVALID, and the resend sent nothing to an address
    that was already confirmed, so the user waited for an email that never came."""
    cv = _code(_CONFIRM)
    confirm = _block(cv, "private func confirm()")
    catch = confirm[confirm.index("} catch {"):]
    assert "switch Self.backendCode(of: error) {" in catch, "confirm() no longer branches on the backend code"
    # AUTH_UNAVAILABLE also comes back BEFORE the code is checked, while it is still good.
    assert "AUTH_UNAVAILABLE" not in catch, (
        "confirm() treats AUTH_UNAVAILABLE as a spent code — it also comes back before the code is "
        "checked, and locking then strands a user whose code still works")
    arm = re.search(rf"case {re.escape(_CONFIRM_CATCH_CODES)}:(.*?)\n\s*case \"", catch, re.S)
    assert arm and "lockAfterConfirmation(notice: Self.message(for: appError))" in arm.group(1), (
        "a code the server ACCEPTED (AUTH_CONFIRMED_SIGN_IN_FAILED) no longer locks the code step — "
        "the user re-sends a spent code, then waits for a new one that never comes")
    assert "lockAfterConfirmation" not in catch[catch.index("default:"):], (
        "confirm() locks the code step on an unknown failure — the code may still be good")
    lock = _block(cv, "private func lockAfterConfirmation(notice: String)")
    for step in ('code = ""', "confirmedNeedsSignIn = true", "confirmedNotice = notice"):
        assert step in lock, f"lockAfterConfirmation lost `{step}`"
    assert "private var codeSpent: Bool { confirmedAwaitingConnection || confirmedNeedsSignIn }" in cv, (
        "codeSpent no longer covers both accepted outcomes")
    # Every way to send the dead code again is locked by it.
    assert "guard Self.codeLengths.contains(digits.count), activity == nil, !codeSpent else" in confirm, (
        "confirm() can send a spent code")
    assert re.search(r"guard activity == nil, !codeSpent, secondsUntilResend", _block(cv, "private func resend()")), (
        "resend() can fire after the code was accepted")
    assert ".disabled(codeSpent)" in _block(cv, "private var codeField: some View"), (
        "the code field stays editable after the code was accepted")
    assert ".disabled(wait > 0 || activity != nil || codeSpent)" in _block(cv, "private var resendButton: some View"), (
        "Send a new code stays enabled after the code was accepted")
    assert "&& !codeSpent" in _block(cv, "private var canConfirm: Bool"), "Confirm stays enabled after the code was accepted"
    # Sign in becomes THE action; Forgot password stays.
    primary = _block(cv, "private var primaryAction: some View")
    assert re.search(r'if confirmedNeedsSignIn \{\s*primaryButton\(\s*title: "Sign in",.*?action: signInWithPassword\s*\)',
                     primary, re.S), "Sign in is no longer the primary action once the code was accepted"
    others = _block(cv, "private var otherWays: some View")
    assert re.search(r"if !confirmedNeedsSignIn \{\s*linkButton\(Self\.signInLinkTitle", others) \
        and 'linkButton("Forgot password?"' in others, (
        "the confirmed state lost Forgot password, or shows two Sign in buttons")
    assert ".onChange(of: confirmedNotice) { _, message in announce(message) }" in cv, (
        "the confirmed notice appears silently to VoiceOver")
    # The server's later word wins: a sign-in that says "not confirmed" reopens the code step.
    sign_in = _block(cv, "private func signInWithPassword()")
    assert re.search(r"if case \.emailNotConfirmed = appError \{.*?confirmedNeedsSignIn = false", sign_in, re.S), (
        "a sign-in refused as unconfirmed leaves the code step locked — nothing on the screen can help")


def test_a_code_refused_after_an_unanswered_try_says_it_may_have_gone_through():
    """The one loop with no server copy saying "confirmed": the confirm's answer was lost (it may
    have spent the code), and the retry is told the code is invalid."""
    cv = _code(_CONFIRM)
    confirm = _block(cv, "private func confirm()")
    assert "let unanswered = lastConfirmUnanswered" in confirm \
        and "lastConfirmUnanswered = Self.mayHaveReachedServer(appError)" in confirm, (
        "confirm() no longer remembers a try that went unanswered")
    arm = re.search(r'case "AUTH_CODE_INVALID":(.*?)\n\s*default:', confirm, re.S)
    assert arm and "if unanswered { note = Self.maybeConfirmedNote }" in arm.group(1), (
        "AUTH_CODE_INVALID after an unanswered try no longer says the address may be confirmed")
    reach = _block(cv, "static func mayHaveReachedServer(_ appError: AppError) -> Bool")
    for case in (".timeout", ".noConnection", ".serverError", ".unknown"):
        assert case in reach, f"mayHaveReachedServer no longer counts `{case}`"
    for answered in ("authUnavailable", "validationFailed", "rateLimited"):
        assert answered not in reach, f"mayHaveReachedServer counts an ANSWERED failure (`{answered}`)"


def test_the_sign_up_message_reaches_the_code_screen_and_the_link_is_neutral():
    """IOS-CONF-2: an existing user who taps Create Account gets no email. The backend's sentence
    ("…Already have an account? Sign in or reset your password.") is the only way out it names,
    and the screen used to drop it. It is worded the same for every address, so it may be shown;
    the screen must never branch on it (that would reveal which addresses have accounts)."""
    view = _code(_SIGN_IN)
    assert "let signUpMessage: String?" in _block(view, "private struct PendingConfirmation"), (
        "PendingConfirmation has nowhere to carry the sign-up message")
    assert "signUpMessage: signUpMessage" in _block(view, _SHOW_CONFIRMATION), (
        "showConfirmation drops the sign-up message")
    body = _block(view, "var body: some View")
    assert "signUpMessage: pending.signUpMessage" in body[body.index("ConfirmEmailView("):][:500], (
        "the code screen is not handed the sign-up message")
    submit = _block(view, "private func submit()")
    catch = submit[submit.index("} catch {"):]
    assert "showConfirmation(for: address, codeJustSent: false)" in catch and "signUpMessage" not in catch, (
        "the sign-in path invents a sign-up message")
    cv = _code(_CONFIRM)
    for name in ("signUpMessage", "signUpNote"):
        hit = re.search(rf"\b{name}\s*(?:==|!=|\?*\.(?:contains|hasPrefix|hasSuffix|range|lowercased|starts))", cv)
        assert not hit, f"ConfirmEmailView inspects the sign-up message ({hit.group(0)!r}) — enumeration"
    header = _block(cv, "private var header: some View")
    assert re.search(r"if let signUpNote = Self\.displayable\(signUpMessage\) \{\s*Text\(signUpNote\)", header), (
        "the sign-up message is not shown on the code screen")
    shown = _block(cv, "static func displayable(_ message: String?) -> String?")
    for step in ("trimmingCharacters(in: .whitespacesAndNewlines)", "!trimmed.isEmpty", "prefix(signUpMessageLimit)"):
        assert step in shown, f"displayable() lost `{step}`"
    assert 'static let signInLinkTitle = "Already confirmed, or have an account? Sign in"' in cv, (
        "the sign-in link's label drifted from the C5 copy")
    assert "linkButton(Self.signInLinkTitle" in _block(cv, "private var otherWays: some View")
    assert "Confirmed with the link? Sign in" not in cv, "the old link-only label is back"


def test_an_unstructured_404_gets_honest_copy_and_a_drift_warning():
    """COMPAT-2: against a server without the confirm route, the screen said "The requested item
    could not be found." A structured 404 decodes to a business error, so `.notFound` here can
    only mean the route is missing."""
    cv = _code(_CONFIRM)
    msg = _block(cv, "static func message(for appError: AppError) -> String")
    assert re.search(r"case \.notFound:\s*return routeMissingMessage", msg), (
        "an unstructured 404 shows AppError's \"could not be found\" copy on the code screen")
    assert "static let routeMissingMessage = \"We can't confirm with a code right now. Please try again later.\"" in cv
    report = _block(cv, "private func report(_ error: Error, during step: String)")
    m = re.search(r"if case \.notFound = appError \{(.*?)\n\s*return\s*\n", report, re.S)
    assert m and "Self.log.warning(" in m.group(1) and "contract drift" in m.group(1), (
        "an unstructured 404 is no longer logged as a contract-drift WARNING")
    for leak in ("\\(email", "\\(code", "\\(password", "\\(digits", "\\(self.email", "\\(error"):
        assert leak not in report, f"the screen's failure log interpolates `{leak}` — codes only"


# ── 4. Token adoption: confirmEmail ≡ signIn ──────────────────────────────────────────

_ADOPTION_STEPS = (
    "saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)",
    "sessionEpoch += 1",
    "let epoch = sessionEpoch",
    "await apiClient.setAuthToken(response.accessToken)",
    "return try await adoptedSessionProfile(epoch: epoch)",
)


def test_confirm_email_adopts_tokens_exactly_like_sign_in():
    auth = _code(_AUTH)
    for header, name in ((_AUTH_SIGN_IN, "signIn"), (_AUTH_CONFIRM, "confirmEmail")):
        body = _block(auth, header)
        assert _in_order(body, _ADOPTION_STEPS), (
            f"AuthService.{name} no longer adopts the session in signIn's order (persist, bump the "
            "epoch, arm the client, read the profile)")
    confirm = _block(auth, _AUTH_CONFIRM)
    assert "endpoint: .confirmEmail(email: email, code: code, password: password)" in confirm \
        and "responseType: AuthResponse.self" in confirm, "confirmEmail no longer calls its route"
    assert "retryCount: 0" in confirm, "confirmEmail may auto-retry a single-use code"
    assert "DeviceProofStore" not in confirm, (
        "confirmEmail remembers a device proof — the server never mints one on this route")
    ep = _code(_ENDPOINT)
    assert "return ConfirmEmailRequest(email: email, code: code, password: password)" in ep, (
        "the confirm-email body is not `{email, code, password}`")


def test_app_state_confirm_email_mirrors_sign_in():
    app = _code(_APP)
    body = _block(app, _APP_CONFIRM)
    steps = ("credentialGeneration &+= 1", "let generation = credentialGeneration", "cancelRestoreBackoff()",
             "authService.confirmEmail(email: email, code: code, password: password)",
             "applyProfile(profile)", "await establishAuthenticatedSession(userId: profile.id)",
             "catch let adopted as SignedInProfileUnavailable",
             "settleSessionWithoutProfile(adopted, generation: generation)")
    assert _in_order(body, steps), (
        "AppState.confirmEmail no longer mirrors AppState.signIn (fence the restore, adopt the "
        "profile, settle a lost profile read)")
    assert "return .confirmedPendingConnection" in body and "return .signedIn" in body, (
        "AppState.confirmEmail no longer reports a confirmed-but-reconnecting outcome — the screen "
        "would invite a retry of a spent code")


# ── 5. No half-signed-in session ──────────────────────────────────────────────────────

def test_a_sign_in_that_loses_its_profile_read_is_settled_not_half_signed_in():
    auth = _code(_AUTH)
    for header in (_AUTH_SIGN_IN, _AUTH_CONFIRM, "func signUp(",
                   "func signInWithProvider(", "func exchangeSupabaseSession("):
        body = _block(auth, header)
        assert "adoptedSessionProfile(epoch: epoch)" in body and "fetchCurrentUser()" not in body, (
            f"AuthService `{header}` reads the profile without wrapping its failure — the caller "
            "cannot tell a session now exists, and the user is left half signed in")
    adopt = _block(auth, _AUTH_ADOPT)
    assert "try await fetchCurrentUser()" in adopt \
        and "throw SignedInProfileUnavailable(underlying: error, epoch: epoch)" in adopt, (
        "AuthService reads the profile without wrapping its failure")
    discard = _block(auth, _AUTH_DISCARD)
    assert _in_order(discard, ("guard epoch == sessionEpoch else { return false }", "clearToken()",
                               "apiClient.disarmAuthToken()")), (
        "discardAdoptedSession undoes a session without checking the epoch — a newer sign-in is wiped")
    assert "DeviceProofStore" not in discard, "discardAdoptedSession clears the device proof (auth.md §3a)"

    app = _code(_APP)
    for header in (_APP_SIGN_IN, _APP_SIGN_UP, _APP_SOCIAL, _APP_CONFIRM):
        body = _block(app, header)
        assert _in_order(body, ("credentialGeneration &+= 1", "let generation = credentialGeneration",
                                "catch let adopted as SignedInProfileUnavailable",
                                "settleSessionWithoutProfile(adopted, generation: generation)")), (
            f"AppState `{header}` does not settle a sign-in whose profile read failed — the user "
            "is left half signed in")
    settle = _block(app, _APP_SETTLE)
    guard = re.search(r"guard generation == credentialGeneration else \{\s*return false\s*\}", settle)
    assert guard and guard.start() < settle.index("enterRestoringWindow(generation: generation)") \
        and guard.start() < settle.index("discardAdoptedSession(epoch: failure.epoch)"), (
        "settleSessionWithoutProfile acts without checking the generation — a sign-out or newer "
        "sign-in that landed during the profile read is undone")
    assert re.search(r"if AppError\.from\(failure\.underlying\)\.isAuthError \{", settle), (
        "settleSessionWithoutProfile no longer tells a refused credential from a network blip")
    social = _block(app, _APP_SOCIAL)
    generic = social[social.rindex("} catch {"):]
    assert re.search(r"if !authService\.hasStoredToken \{\s*auth\.status = \.unauthenticated\s*\}", generic), (
        "completeSocialSignIn forces `.unauthenticated` while a credential is still held — a "
        "`.restoring` user's good session is forgotten")


# ── 6. AppError: the arm each code actually reaches ───────────────────────────────────

_PASSWORD_400S = ("AUTH_PASSWORD_REJECTED", "AUTH_PASSWORD_NOT_SET", "AUTH_PASSWORD_ALREADY_SET")


def test_app_error_maps_the_code_and_password_codes_on_the_business_arm():
    mapper = _block(_code(_APP_ERROR), "private static func mapAPIError(_ error: APIError) -> AppError")
    biz = _between(mapper, "case .businessError(let code, let message):", "case .decodingError:")
    m = re.search(r'if code == "AUTH_CODE_INVALID" \{(.*?)\n {12}\}', biz, re.S)
    assert m and "return .validationFailed(" in m.group(1), (
        "AUTH_CODE_INVALID has no explicit branch on the businessError arm (its 400 lands there) — it "
        "falls to `.apiError`, whose Retry resends a dead code")
    for code in _PASSWORD_400S:
        branch = re.search(rf'code == "{code}"(.*?)\{{(.*?)\n {{12}}\}}', biz, re.S)
        assert branch and "return .validationFailed(message: message)" in branch.group(2), (
            f"{code} is not mapped on the businessError arm — its 400 falls to `.apiError` (IOS-AUTH-08)")
    auth_arm = _between(mapper, "case .authError(let code, let message):", "case .notFound:")
    assert '"AUTH_PASSWORD_REJECTED"' in auth_arm, (
        "the authError branches were removed — an older backend's 401 would fall to `.authUnavailable`")


_POST_VERIFY_CODES = ("AUTH_CONFIRMED_SIGN_IN_FAILED", "AUTH_CODE_USED")


def test_app_error_maps_the_post_verify_codes_on_the_business_arm():
    """Both arrive as 503 (400 when the password was refused) — the businessError arm, never
    `.authError` (401 only). Both mean "the code is spent": never `.authUnavailable`, whose Try
    Again re-sends a dead code (C2, IOS-CONF-1, PW-4)."""
    err = _code(_APP_ERROR)
    mapper = _block(err, "private static func mapAPIError(_ error: APIError) -> AppError")
    biz = _between(mapper, "case .businessError(let code, let message):", "case .decodingError:")
    assert re.search(r'if code == "AUTH_CONFIRMED_SIGN_IN_FAILED" \{\s*return \.emailConfirmedSignInFailed\(message: message\)\s*\}', biz), (
        "AUTH_CONFIRMED_SIGN_IN_FAILED is not mapped to `.emailConfirmedSignInFailed` on the businessError arm")
    used = re.search(r'if code == "AUTH_CODE_USED" \{(.*?)\n {12}\}', biz, re.S)
    assert used and "return .validationFailed(" in used.group(1), (
        "AUTH_CODE_USED is not mapped to `.validationFailed` on the businessError arm — its 503 falls "
        "to `.apiError`, whose Retry re-sends a spent code")
    assert "case emailConfirmedSignInFailed(message: String)" in err, "AppError lost `.emailConfirmedSignInFailed`"
    message = _block(err, "var message: String")
    arm = message[message.index("case .emailConfirmedSignInFailed(let message):"):]
    arm = arm[: arm.index("case .", len("case .emailConfirmedSignInFailed"))]
    assert "? \"Your email is confirmed, but" in arm and ": message" in arm, (
        "`.emailConfirmedSignInFailed` no longer shows the server's sentence (with a 'confirmed' fallback)")
    assert re.search(r"case \.emailConfirmedSignInFailed:\s*return \.signIn",
                     _block(err, "var suggestedAction: ErrorAction")), (
        "`.emailConfirmedSignInFailed` no longer suggests Sign In — the code is spent, so Retry cannot help")
    for decl in ("var isRetryable: Bool", "var isAuthError: Bool"):
        assert "emailConfirmedSignInFailed" not in _block(err, decl), (
            f"`.emailConfirmedSignInFailed` is listed in {decl} — it is neither retryable nor a reason to clear a credential")
    assert 'case .emailConfirmedSignInFailed: return "email_confirmed_sign_in_failed"' in _block(err, "var analyticsCode: String")
    # An iOS branch for a code nobody sends is dead: the backend must emit both.
    from app.api.error_response import ErrorCode
    for code in _POST_VERIFY_CODES:
        assert code in ErrorCode.__members__, f"the backend has no ErrorCode.{code} — the iOS branch is dead"


def test_rate_limited_copy_is_in_human_units():
    message = _block(_code(_APP_ERROR), "var message: String")
    arm = message[message.index("case .rateLimited(let seconds):"):]
    arm = arm[: arm.index("case .", len("case .rateLimited"))]
    assert "Self.waitPhrase(seconds: seconds)" in arm and "seconds) seconds" not in arm, (
        "the 429 copy is back to raw seconds (\"Please wait 3600 seconds\")")
    phrase = _block(_code(_APP_ERROR), "static func waitPhrase(seconds: Int) -> String")
    for words in ('"a minute"', '"about an hour"', '"about \\(minutes) minutes"'):
        assert words in phrase, f"waitPhrase lost {words} — re-derive"


def test_sign_in_errors_go_through_app_error_with_a_mode_aware_fallback():
    view = _code(_SIGN_IN)
    fe = _block(view, "private func friendlyError(_ error: Error) -> String")
    social_at, app_at = fe.find("as? SocialSignInError"), fe.find("AppError.from(error)")
    assert -1 < social_at < app_at, (
        "friendlyError no longer goes through AppError.from after the social arm")
    assert "wait a minute" not in fe, "friendlyError hard-codes 'wait a minute' for every 429 (IOS-AUTH-04)"
    gf = _block(view, "private var genericFailure: String")
    assert "mode == .signIn" in gf and "Couldn't create your account" in gf, (
        "the fallback no longer depends on the mode — a failed Create Account reads 'Sign in failed'")


# ── 7. Social sign-in: Apple's nonce, and failures that leave a trace ─────────────────

def test_apple_gets_the_hash_and_the_backend_gets_the_raw_nonce():
    social = _code(_SOCIAL)
    make = _block(social, "func makeAppleRequest() -> ASAuthorizationAppleIDRequest")
    assert _in_order(make, ("let nonce = Self.randomNonceString()", "currentAppleNonce = nonce",
                            "request.nonce = Self.sha256(nonce)")), (
        "makeAppleRequest no longer keeps the raw nonce and sends Apple its SHA-256")
    handle = _block(social, "func handleAppleCompletion(")
    assert handle.lstrip("{").lstrip().startswith("defer { currentAppleNonce = nil }"), (
        "handleAppleCompletion no longer consumes the nonce on EVERY outcome (`defer` first)")
    assert "let nonce = currentAppleNonce" in handle and re.search(
        r'\.identityToken\(\s*provider: "apple",\s*token: identityToken,\s*nonce: nonce,', handle), (
        "handleAppleCompletion no longer sends the RAW nonce to the backend")
    assert "sha256" not in handle, "handleAppleCompletion hashes the nonce it sends to the backend"


def test_social_failures_are_logged_and_shown_as_fixed_copy():
    view = _code(_SIGN_IN)
    for header, provider in (("private func handleApple(", "apple"), ("private func signInWithGoogle()", "google")):
        body = _block(view, header)
        generic = body[body.rindex("} catch {"):]
        assert _in_order(generic, (f'SocialSignInService.logFailure(error, provider: "{provider}")',
                                   "errorMessage = friendlyError(error)")), (
            f"a {provider} sign-in failure is no longer logged before it is shown")
    social = _code(_SOCIAL)
    described = _block(social, "var errorDescription: String?")
    assert "\\(reason)" not in described and "\\(what)" not in described, (
        "SocialSignInError shows the provider's raw text on screen (SOC-06)")
    log = _block(social, "static func logFailure(_ error: Error, provider: String)")
    assert "MonitoringConfig.redact(reason)" in log, "logFailure writes the provider's reason unscrubbed"
    for banned in ("identityToken", "currentAppleNonce", "callbackURL", "fragment", ".email"):
        assert banned not in log, f"logFailure logs `{banned}` — never a token, nonce, callback or email"


# ── 8. Fresh install, DEBUG logs, device proofs ───────────────────────────────────────

def test_a_fresh_install_purges_only_the_session_tokens():
    purge = _block(_code(_AUTH), _AUTH_PURGE)
    assert "self.clearToken()" in purge, "the fresh-install purge no longer clears the session tokens"
    for banned in ("deleteAll", "DeviceProofStore", "GuestIdentity", "keychain.delete("):
        assert banned not in purge, (
            f"the fresh-install purge touches `{banned}` — only the two session tokens may go "
            "(the device proof and the install id are meant to survive a reinstall)")
    assert "await Task.detached(" in purge and \
        "group.string(forKey: WidgetAPIConfig.baseURLOverrideKey) == nil ? .fresh : .updated" in purge, (
        "the purge no longer tells an update from a fresh install — every update would sign "
        "every user out")
    assert purge.index("self.clearToken()") < purge.index(
        "UserDefaults.standard.set(true, forKey: Self.installMarkerKey)"), (
        "the install marker is written before the clear — a kill between the two skips the purge")
    configure = _block(_code(_APP), _APP_CONFIGURE)
    at = [configure.find(t) for t in ("await authService.purgeSessionLeftByAnEarlierInstall()",
                                      "await authService.loadStoredToken()",
                                      "WidgetAPIConfig.publishBaseURL(")]
    assert -1 < at[0] < at[1] < at[2], (
        "the launch no longer purges before its one Keychain read and before the widget publish it "
        "uses as evidence of an earlier launch")


def test_the_debug_logger_redacts_codes_nonces_and_every_token_key():
    client = _code(_CLIENT)
    keys = _between(client, "private static let redactedKeys", "]")
    for key in ("code", "otp", "nonce", "password", "new_password", "current_password", "token"):
        assert f'"{key}"' in keys, f"`{key}` is no longer redacted from DEBUG request/response logs"
    redact = _block(client, "private static func redact(_ raw: String) -> String")
    assert 'hasSuffix("_token")' in redact and "redactedKeys.contains(" in redact, (
        "redact() no longer masks every `*_token` key — device_token and supabase_access_token leak")


def _swift_unescape(body: str) -> str:
    """A Swift string literal's body as the string it denotes — ONE pass, so `\\\\"` (an
    escaped backslash, then an escaped quote) cannot be misread the way chained `.replace`
    calls misread it. Only `\\` + one character occurs in these literals; an interpolation is
    refused."""
    assert "\\(" not in body, f"interpolation in a literal this test compiles: {body!r}"
    return re.sub(r"\\(.)", r"\1", body)


def _swift_pair_pattern() -> str:
    """The Swift `jsonStringPair` literal, unescaped into the regex it compiles to."""
    client = _code(_CLIENT)
    decl = _between(client, "private static let jsonStringPair", ")\n")
    literal = re.search(r'pattern: "((?:[^"\\]|\\.)*)"', decl)
    assert literal, "the redaction pattern literal moved — re-derive"
    return _swift_unescape(literal.group(1))


_REDACTED_KEYS = {"password", "token", "code", "otp", "nonce", "email"}


def _redact(pair: re.Pattern, body: str) -> str:
    """`redact()`'s JSON pass in Python: each matched pair whose key is redacted (the key rule is
    pinned above) is replaced by `"key":"***"`, everything between matches kept as-is."""
    out, cursor = [], 0
    for m in pair.finditer(body):
        key = m.group(1).lower()
        if key not in _REDACTED_KEYS and not key.endswith("_token"):
            continue
        out.append(body[cursor:m.start()])
        out.append(f'"{m.group(1)}":"***"')
        cursor = m.end()
    out.append(body[cursor:])
    return "".join(out)


def test_the_swift_unescaper_is_one_pass():
    assert _swift_unescape('\\"a\\\\\\"b') == '"a\\"b'   # \" a \\ \" b
    assert _swift_unescape("[^\\\"\\\\\\\\]") == '[^"\\\\]'
    with pytest.raises(AssertionError, match="interpolation"):
        _swift_unescape("x\\(y)")


def test_the_redaction_pattern_masks_the_right_keys_in_linear_time():
    """The pattern is the Swift literal itself, compiled here; the key decision mirrors
    `redact()`'s one line (`redactedKeys.contains(lowered) || lowered.hasSuffix("_token")`),
    which the scan above pins."""
    pair = re.compile(_swift_pair_pattern())

    def masked(body: str) -> set[str]:
        return {m.group(1) for m in pair.finditer(body)
                if m.group(1).lower() in _REDACTED_KEYS or m.group(1).lower().endswith("_token")}

    body = ('{"email":"a@b.co","code":"12345678","password":"Pw1!aaaa","device_token":"v1.1.d.s",'
            '"supabase_access_token":"eyJ.x.y","nonce":"raw","error_code":"AUTH_CODE_INVALID",'
            '"currency_code":"USD","user_message":"That code is invalid"}')
    assert masked(body) == {"email", "code", "password", "device_token", "supabase_access_token", "nonce"}
    # IOS-CONF-3: a password holding a quote. JSONEncoder writes it as `\"`; the old `[^"]*`
    # value class stopped there and printed the rest of the secret.
    escaped = '{"email":"a@b.co","code":"12345678","password":"Abc\\"Secret9!"}'
    assert _redact(pair, escaped) == '{"email":"***","code":"***","password":"***"}', (
        f"the DEBUG redaction leaks the tail of a secret holding a JSON-escaped quote: "
        f"{_redact(pair, escaped)!r}")
    # A value ENDING in an escaped backslash still closes on its own quote.
    assert _redact(pair, '{"password":"Abc\\\\","user_message":"ok"}') == '{"password":"***","user_message":"ok"}'
    assert _redact(pair, '{"password":"a\\\\\\"b\\\\","n":1}') == '{"password":"***","n":1}'
    # A hostile body must not make the logger backtrack (it runs on the APIClient actor, in
    # every DEBUG request). Bounded key class, branches that never share a first character,
    # possessive run: far under a second here, escapes and unterminated values included.
    for hostile in ('"' + "a_" * 60_000 + '": "' + "x" * 80_000 + '"a' * 40_000,
                    '"k":"' + '\\"' * 100_000,
                    '"k":"' + "\\" * 200_001,
                    '"a":"\\' * 50_000,
                    '"' + "a" * 64 + '":"' * 50_000):
        started = time.perf_counter()
        list(pair.finditer(hostile))
        assert time.perf_counter() - started < 1.0, "the redaction pattern backtracks on a hostile body"


def test_device_proofs_evict_the_oldest_issued_first_and_stay_bounded():
    store = _code(_PROOFS)
    remember = _block(store, "static func remember(_ proof: String?, for email: String?)")
    assert "save(bounded(proofs, keeping: normalised))" in remember, (
        "remember() no longer bounds the map AFTER inserting — it can sit over its limit")
    assert "keys.sorted()" not in store, "proofs are evicted alphabetically again (CC-08)"
    bounded = _block(store, "static func bounded(_ proofs: [String: String], keeping kept: String) -> [String: String]")
    assert "issuedAt(of:" in bounded and ".filter { $0.key != kept }" in bounded \
        and "prefix(overflow)" in bounded, (
        "eviction is no longer oldest-issued-first, or can evict the proof just remembered")


# ── 9. Accessibility ──────────────────────────────────────────────────────────────────

def test_both_screens_name_their_fields_and_announce_their_errors():
    view = _code(_SIGN_IN)
    body = _block(view, "var body: some View")
    assert re.search(r"\.onChange\(of: errorMessage\)[^\n]*\n[^\n]*\n\s*AccessibilityNotification\.Announcement\(", body), (
        "SignInView's errors appear silently to VoiceOver (CC-05)")
    for label in ('.accessibilityLabel("Email")', '.accessibilityLabel("Password")'):
        assert label in view, f"SignInView lost {label}"
    assert re.search(r'ProgressView\(\)\s*\.tint\(AppColors\.textOnAccent\)\s*\.accessibilityLabel\(', body), (
        "SignInView's spinner has no VoiceOver label")
    cv = _code(_CONFIRM)
    assert ".onChange(of: errorMessage) { _, message in announce(message) }" in cv \
        and "AccessibilityNotification.Announcement(message).post()" in _block(cv, "private func announce(_ message: String?)"), (
        "ConfirmEmailView's errors appear silently to VoiceOver (CC-05)")
    assert '.accessibilityLabel("Code from email")' in cv, "ConfirmEmailView's code field has no name"


def test_the_sign_up_eye_is_a_44pt_target_that_keeps_focus():
    """PW-6: the show / hide eye was a bare 14-pt glyph, and toggling it swapped the focused
    SecureField for a TextField, dropping the keyboard."""
    view = _code(_SIGN_IN)
    field = _block(view, "private var passwordField: some View")
    eye = field[field.index("if mode == .signUp {"):]
    frame = ".frame(minWidth: Self.minTapTarget, minHeight: Self.minTapTarget)"
    assert frame in eye and ".contentShape(Rectangle())" in eye, (
        "the show / hide eye is no longer a 44-pt target with a hit shape")
    assert eye.index(frame) < eye.index(".contentShape(Rectangle())") < eye.index(".buttonStyle("), (
        "the 44-pt frame / hit shape is not on the button's label (a button hit-tests what its label draws)")
    assert "private static let minTapTarget: CGFloat = 44" in view, "the eye's target is no longer 44 pt"
    assert re.search(r"let hadFocus = focusedField == \.password\s*showPassword\.toggle\(\)\s*"
                     r"if hadFocus \{\s*Task \{ @MainActor in focusedField = \.password \}", eye), (
        "toggling the eye no longer re-asserts focus on the next turn — the keyboard drops on every tap")
    # The target cancels the field's own inset rather than growing the field.
    assert ".padding(.vertical, -Self.fieldVerticalPadding)" in eye \
        and ".padding(.vertical, Self.fieldVerticalPadding)" in _block(view, "private func labeled("), (
        "the eye's 44-pt target no longer cancels the field inset it sits in")


# ── 10. Mutations — every guard above goes red on the regression it names ─────────────

_I8, _I12, _I16, _I20, _I24 = " " * 8, " " * 12, " " * 16, " " * 20, " " * 24

_T_RESEND = test_sign_in_rethrows_email_not_confirmed_without_resending
_T_ROUTE = test_sign_in_view_routes_email_not_confirmed_to_the_code_screen
_T_CODE = test_the_code_screen_takes_a_one_time_code_and_confirms_with_it
_T_RESEND_UI = test_resend_is_explicit_guarded_cooled_down_and_never_claims_a_send
_T_ADOPT = test_confirm_email_adopts_tokens_exactly_like_sign_in
_T_APP_CONFIRM = test_app_state_confirm_email_mirrors_sign_in
_T_HALF = test_a_sign_in_that_loses_its_profile_read_is_settled_not_half_signed_in
_T_APPERR = test_app_error_maps_the_code_and_password_codes_on_the_business_arm
_T_429 = test_rate_limited_copy_is_in_human_units
_T_FRIENDLY = test_sign_in_errors_go_through_app_error_with_a_mode_aware_fallback
_T_NONCE = test_apple_gets_the_hash_and_the_backend_gets_the_raw_nonce
_T_SOCIAL = test_social_failures_are_logged_and_shown_as_fixed_copy
_T_PURGE = test_a_fresh_install_purges_only_the_session_tokens
_T_REDACT = test_the_debug_logger_redacts_codes_nonces_and_every_token_key
_T_PROOFS = test_device_proofs_evict_the_oldest_issued_first_and_stay_bounded
_T_A11Y = test_both_screens_name_their_fields_and_announce_their_errors
_T_SPENT = test_a_spent_code_locks_the_code_step_and_promotes_sign_in
_T_UNANSWERED = test_a_code_refused_after_an_unanswered_try_says_it_may_have_gone_through
_T_SIGNUP_MSG = test_the_sign_up_message_reaches_the_code_screen_and_the_link_is_neutral
_T_404 = test_an_unstructured_404_gets_honest_copy_and_a_drift_warning
_T_POST_VERIFY = test_app_error_maps_the_post_verify_codes_on_the_business_arm
_T_PAIR = test_the_redaction_pattern_masks_the_right_keys_in_linear_time
_T_EYE = test_the_sign_up_eye_is_a_44pt_target_that_keeps_focus

_SIGN_IN_ADOPT = (f"{_I8}let response = try await apiClient.request(\n"
                  f"{_I12}endpoint: .signIn(email: email, password: password),\n"
                  f"{_I12}responseType: AuthResponse.self\n{_I8})\n")
_OLD_RESEND = (f"{_I8}let response: AuthResponse\n{_I8}do {{\n{_I12}response = try await apiClient.request(\n"
               f"{_I16}endpoint: .signIn(email: email, password: password),\n"
               f"{_I16}responseType: AuthResponse.self\n{_I12})\n"
               f"{_I8}}} catch APIError.businessError(let code, let message) where code == \"EMAIL_NOT_CONFIRMED\" {{\n"
               f"{_I12}let resent = (try? await resendConfirmation(email: email)) != nil\n"
               f"{_I12}throw APIError.businessError(code: code, message: resent ? \"\\(message) We've just sent you a fresh confirmation link.\" : message)\n"
               f"{_I8}}}\n")
_ROUTE = (f"{_I16}if case .emailNotConfirmed = AppError.from(error) {{\n"
          f"{_I16}    showConfirmation(for: address, codeJustSent: false)\n"
          f"{_I16}    return\n{_I16}}}\n")

# (name, file, ((old, new), ...), guard, message the guard must fail with)
_MUTATIONS = [
    # The shipped bug, verbatim in shape: catch the code, `try?` a resend, append a sentence.
    ("signin-auto-resend", _AUTH, ((_SIGN_IN_ADOPT, _OLD_RESEND),), _T_RESEND,
     "AuthService.signIn re-sends the confirmation email"),
    ("signin-swallows", _AUTH,
     ((f"{_I8}return try await adoptedSessionProfile(epoch: epoch)\n    }}\n\n    /// Confirm",
       f"{_I8}_ = try? await fetchCurrentUser()\n"
       f"{_I8}return try await adoptedSessionProfile(epoch: epoch)\n    }}\n\n    /// Confirm"),),
     _T_RESEND, "AuthService.signIn swallows an error with `try?`"),
    ("route-to-red-line", _SIGN_IN, ((_ROUTE, ""),), _T_ROUTE,
     "SignInView no longer routes EMAIL_NOT_CONFIRMED to the code screen"),
    ("signup-not-routed", _SIGN_IN,
     ((f"{_I24}showConfirmation(for: address, codeJustSent: true, signUpMessage: message)\n",
       f"{_I24}errorMessage = nil\n"),),
     _T_ROUTE, "a sign-up that needs confirming no longer opens the code screen"),
    # IOS-CONF-2, verbatim in shape: the outcome matched without binding its message.
    ("signup-message-unbound", _SIGN_IN,
     (("if case .needsEmailConfirmation(let message) = outcome {", "if case .needsEmailConfirmation = outcome {"),
      ("showConfirmation(for: address, codeJustSent: true, signUpMessage: message)",
       "showConfirmation(for: address, codeJustSent: true)")),
     _T_ROUTE, "a sign-up that needs confirming no longer opens the code screen"),
    ("signup-message-not-handed-over", _SIGN_IN,
     ((f"{_I20}signUpMessage: pending.signUpMessage,\n", ""),),
     _T_SIGNUP_MSG, "the code screen is not handed the sign-up message"),
    ("signup-message-branched-on", _CONFIRM,
     (("if let signUpNote = Self.displayable(signUpMessage) {",
       'if let signUpNote = Self.displayable(signUpMessage), signUpMessage?.contains("Already") == true {'),),
     _T_SIGNUP_MSG, "ConfirmEmailView inspects the sign-up message"),
    ("signup-message-not-shown", _CONFIRM,
     ((f"{_I12}if let signUpNote = Self.displayable(signUpMessage) {{\n{_I16}Text(signUpNote)\n",
       f"{_I12}if let signUpNote = Self.displayable(nil) {{\n{_I16}Text(signUpNote)\n"),),
     _T_SIGNUP_MSG, "the sign-up message is not shown on the code screen"),
    ("old-link-label", _CONFIRM,
     (('static let signInLinkTitle = "Already confirmed, or have an account? Sign in"',
       'static let signInLinkTitle = "Confirmed with the link? Sign in"'),),
     _T_SIGNUP_MSG, "the sign-in link's label drifted"),
    # IOS-CONF-1: the accepted code only shows a message, the form stays live.
    ("spent-code-not-locked", _CONFIRM,
     (("lockAfterConfirmation(notice: Self.message(for: appError))", "errorMessage = Self.message(for: appError)"),),
     _T_SPENT, "no longer locks the code step"),
    ("locks-on-auth-unavailable", _CONFIRM,
     (('case "AUTH_CONFIRMED_SIGN_IN_FAILED", "AUTH_PASSWORD_REJECTED":',
       'case "AUTH_CONFIRMED_SIGN_IN_FAILED", "AUTH_PASSWORD_REJECTED", "AUTH_UNAVAILABLE":'),),
     _T_SPENT, "treats AUTH_UNAVAILABLE as a spent code"),
    ("code-spent-misses-a-state", _CONFIRM,
     (("{ confirmedAwaitingConnection || confirmedNeedsSignIn }", "{ confirmedAwaitingConnection }"),),
     _T_SPENT, "codeSpent no longer covers both accepted outcomes"),
    ("resend-live-after-accept", _CONFIRM,
     ((".disabled(wait > 0 || activity != nil || codeSpent)", ".disabled(wait > 0 || activity != nil)"),),
     _T_SPENT, "Send a new code stays enabled after the code was accepted"),
    ("code-field-live-after-accept", _CONFIRM, ((".disabled(codeSpent)", ".disabled(false)"),),
     _T_SPENT, "the code field stays editable after the code was accepted"),
    ("sign-in-not-promoted", _CONFIRM,
     (('title: "Sign in", busyLabel: "Signing in",', 'title: "Confirm", busyLabel: "Signing in",'),),
     _T_SPENT, "Sign in is no longer the primary action"),
    ("never-unlocks", _CONFIRM,
     ((f"{_I24}confirmedNeedsSignIn = false\n", ""),),
     _T_SPENT, "a sign-in refused as unconfirmed leaves the code step locked"),
    ("unanswered-hint-gone", _CONFIRM,
     (("if unanswered { note = Self.maybeConfirmedNote }", "_ = unanswered"),),
     _T_UNANSWERED, "no longer says the address may be confirmed"),
    ("unanswered-counts-an-answer", _CONFIRM,
     (("case .timeout, .noConnection, .serverError, .unknown:",
       "case .timeout, .noConnection, .serverError, .unknown, .authUnavailable:"),),
     _T_UNANSWERED, "mayHaveReachedServer counts an ANSWERED failure"),
    # COMPAT-2.
    ("not-found-generic-copy", _CONFIRM,
     ((f"{_I8}case .notFound:\n{_I12}return routeMissingMessage\n", ""),),
     _T_404, "an unstructured 404 shows AppError's"),
    ("not-found-unlogged", _CONFIRM,
     (('Self.log.warning("confirm-email screen', 'Self.log.notice("confirm-email screen'),),
     _T_404, "no longer logged as a contract-drift WARNING"),
    ("log-leaks-the-address", _CONFIRM,
     (('failed: \\(appError.analyticsCode, privacy: .public)', 'failed: \\(email, privacy: .public)'),),
     _T_404, "interpolates `\\(email`"),
    ("overlay-gone", _SIGN_IN,
     ((f"{_I12}if let pending = pendingConfirmation {{\n{_I16}ConfirmEmailView(",
       f"{_I12}if let pending = pendingConfirmation, false {{\n{_I16}ConfirmEmailView("),),
     _T_ROUTE, "SignInView no longer presents ConfirmEmailView for a pending confirmation"),
    ("no-one-time-code", _CONFIRM, ((".textContentType(.oneTimeCode)", ".textContentType(.username)"),),
     _T_CODE, "the code field lost `.oneTimeCode`"),
    ("confirm-not-wired", _CONFIRM,
     (("appState.confirmEmail(email: email, code: digits, password: password)",
       "appState.signIn(email: email, password: password)"),),
     _T_CODE, "ConfirmEmailView no longer confirms through AppState.confirmEmail"),
    ("resend-unguarded", _CONFIRM,
     (("guard activity == nil, !codeSpent, secondsUntilResend(at: Date()) == 0 else {",
       "guard !codeSpent else {"),),
     _T_RESEND_UI, "resend lost its in-flight / cooldown guard"),
    ("resend-claims-a-send", _CONFIRM,
     (('"If your email still needs confirming, a new code is on its way."',
       '"We\'ve sent you a new code."'),),
     _T_RESEND_UI, "the resend note changed"),
    ("confirm-arms-before-save", _AUTH,
     ((f"{_I8}saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)\n"
       f"{_I8}sessionEpoch += 1   // new session — invalidate any in-flight older-session refresh\n"
       f"{_I8}let epoch = sessionEpoch\n\n{_I8}await apiClient.setAuthToken(response.accessToken)\n",
       f"{_I8}await apiClient.setAuthToken(response.accessToken)\n"
       f"{_I8}saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)\n"
       f"{_I8}sessionEpoch += 1\n{_I8}let epoch = sessionEpoch\n"),),
     _T_ADOPT, "AuthService.confirmEmail no longer adopts the session in signIn's order"),
    ("confirm-remembers-a-proof", _AUTH,
     ((f"{_I8}await apiClient.setAuthToken(response.accessToken)\n\n{_I8}return try await adoptedSessionProfile(epoch: epoch)\n    }}\n\n    /// Sign up",
       f"{_I8}await apiClient.setAuthToken(response.accessToken)\n{_I8}DeviceProofStore.remember(response.deviceToken, for: email)\n\n"
       f"{_I8}return try await adoptedSessionProfile(epoch: epoch)\n    }}\n\n    /// Sign up"),),
     _T_ADOPT, "confirmEmail remembers a device proof"),
    ("app-confirm-skips-fence", _APP,
     ((f"    ) async throws -> EmailConfirmationOutcome {{\n{_I8}credentialGeneration &+= 1\n",
       f"    ) async throws -> EmailConfirmationOutcome {{\n"),),
     _T_APP_CONFIRM, "AppState.confirmEmail no longer mirrors AppState.signIn"),
    ("app-confirm-always-throws", _APP,
     ((f"{_I16}return .confirmedPendingConnection\n", f"{_I16}throw adopted.underlying\n"),),
     _T_APP_CONFIRM, "no longer reports a confirmed-but-reconnecting outcome"),
    ("provider-bare-profile-read", _AUTH,
     ((f"{_I8}let profile = try await adoptedSessionProfile(epoch: epoch)\n"
       f"{_I8}// The address is only known once the profile is back; the proof is bound to it.\n",
       f"{_I8}let profile = try await fetchCurrentUser()\n"
       f"{_I8}// The address is only known once the profile is back; the proof is bound to it.\n"),),
     _T_HALF, "reads the profile without wrapping its failure"),
    ("app-signin-does-not-settle", _APP,
     ((f"{_I12}// The password was accepted and the tokens stored; only the profile read failed.\n"
       f"{_I12}await settleSessionWithoutProfile(adopted, generation: generation)\n",
       f"{_I12}_ = adopted\n"),),
     _T_HALF, "does not settle a sign-in whose profile read failed"),
    ("settle-unguarded", _APP,
     ((f"{_I8}guard generation == credentialGeneration else {{ return false }}\n"
       f"{_I8}if AppError.from(failure.underlying).isAuthError {{\n",
       f"{_I8}if AppError.from(failure.underlying).isAuthError {{\n"),),
     _T_HALF, "settleSessionWithoutProfile acts without checking the generation"),
    ("discard-unguarded", _AUTH,
     ((f"{_I8}guard epoch == sessionEpoch else {{ return false }}\n{_I8}sessionEpoch += 1\n{_I8}self.clearToken()\n",
       f"{_I8}sessionEpoch += 1\n{_I8}self.clearToken()\n"),),
     _T_HALF, "discardAdoptedSession undoes a session without checking the epoch"),
    ("social-forces-signed-out", _APP,
     ((f"{_I12}if !authService.hasStoredToken {{\n{_I16}auth.status = .unauthenticated\n{_I12}}}\n",
       f"{_I12}auth.status = .unauthenticated\n"),),
     _T_HALF, "completeSocialSignIn forces `.unauthenticated` while a credential is still held"),
    ("code-invalid-unmapped", _APP_ERROR, (('if code == "AUTH_CODE_INVALID" {', 'if code == "AUTH_CODE_INVALID_" {'),),
     _T_APPERR, "AUTH_CODE_INVALID has no explicit branch on the businessError arm"),
    ("password-400-unmapped", _APP_ERROR,
     ((f'{_I16}|| code == "AUTH_PASSWORD_NOT_SET"\n', ""),),
     _T_APPERR, "AUTH_PASSWORD_NOT_SET is not mapped on the businessError arm"),
    ("confirmed-code-unmapped", _APP_ERROR,
     (('if code == "AUTH_CONFIRMED_SIGN_IN_FAILED" {', 'if code == "AUTH_CONFIRMED_SIGN_IN_FAILED_" {'),),
     _T_POST_VERIFY, "AUTH_CONFIRMED_SIGN_IN_FAILED is not mapped"),
    ("code-used-unmapped", _APP_ERROR,
     (('if code == "AUTH_CODE_USED" {', 'if code == "AUTH_CODE_USED_" {'),),
     _T_POST_VERIFY, "AUTH_CODE_USED is not mapped"),
    ("confirmed-case-retryable", _APP_ERROR,
     (("case .timeout, .serverError, .rateLimited, .authUnavailable:",
       "case .timeout, .serverError, .rateLimited, .authUnavailable, .emailConfirmedSignInFailed:"),),
     _T_POST_VERIFY, "is listed in var isRetryable: Bool"),
    ("confirmed-case-offers-retry", _APP_ERROR,
     ((f"{_I8}case .emailConfirmedSignInFailed:\n{_I12}return .signIn\n",
       f"{_I8}case .emailConfirmedSignInFailed:\n{_I12}return .retry\n"),),
     _T_POST_VERIFY, "no longer suggests Sign In"),
    ("raw-seconds", _APP_ERROR,
     (('return "Please wait \\(Self.waitPhrase(seconds: seconds)) before trying again."',
       'return "Please wait \\(seconds) seconds before trying again."'),),
     _T_429, "the 429 copy is back to raw seconds"),
    ("friendly-bypasses-app-error", _SIGN_IN,
     ((f"{_I8}let appError = AppError.from(error)\n{_I8}switch appError {{\n",
       f"{_I8}let appError = AppError.unknown(message: \"\")\n{_I8}switch appError {{\n"),),
     _T_FRIENDLY, "friendlyError no longer goes through AppError.from"),
    ("fallback-not-mode-aware", _SIGN_IN,
     (('            : "Couldn\'t create your account. Please try again."\n    }',
       '            : "Sign in failed. Please try again."\n    }'),),
     _T_FRIENDLY, "the fallback no longer depends on the mode"),
    ("backend-gets-hashed-nonce", _SOCIAL,
     ((f"{_I12}let nonce = currentAppleNonce\n", f"{_I12}let nonce = currentAppleNonce.map(Self.sha256)\n"),),
     _T_NONCE, "handleAppleCompletion hashes the nonce it sends to the backend"),
    ("nonce-not-consumed", _SOCIAL,
     ((f"{_I8}defer {{ currentAppleNonce = nil }}\n", ""),),
     _T_NONCE, "handleAppleCompletion no longer consumes the nonce on EVERY outcome"),
    ("apple-gets-raw-nonce", _SOCIAL,
     (("request.nonce = Self.sha256(nonce)", "request.nonce = nonce"),),
     _T_NONCE, "makeAppleRequest no longer keeps the raw nonce and sends Apple its SHA-256"),
    ("social-unlogged", _SIGN_IN,
     ((f'{_I16}SocialSignInService.logFailure(error, provider: "google")\n', ""),),
     _T_SOCIAL, "a google sign-in failure is no longer logged before it is shown"),
    ("raw-provider-copy", _SOCIAL,
     ((f'{_I8}case .malformedCallback, .provider:\n{_I12}return "That sign-in didn\'t complete. Please try again."\n',
       f'{_I8}case .malformedCallback:\n{_I12}return "That sign-in didn\'t complete. Please try again."\n'
       f'{_I8}case .provider(let reason):\n{_I12}return "That sign-in didn\'t complete: \\(reason)"\n'),),
     _T_SOCIAL, "SocialSignInError shows the provider's raw text on screen"),
    ("purge-wipes-the-proof", _AUTH,
     ((f"{_I12}self.clearToken()\n{_I8}case .updated:",
       f"{_I12}self.clearToken()\n{_I12}DeviceProofStore.remember(nil, for: nil)\n{_I8}case .updated:"),),
     _T_PURGE, "the fresh-install purge touches `DeviceProofStore`"),
    ("purge-on-every-update", _AUTH,
     (("group.string(forKey: WidgetAPIConfig.baseURLOverrideKey) == nil ? .fresh : .updated", ".fresh"),),
     _T_PURGE, "the purge no longer tells an update from a fresh install"),
    ("purge-after-launch-read", _APP,
     ((f"{_I12}await authService.purgeSessionLeftByAnEarlierInstall()\n{_I12}let launchGeneration = credentialGeneration\n",
       f"{_I12}let launchGeneration = credentialGeneration\n"),),
     _T_PURGE, "the launch no longer purges before its one Keychain read"),
    ("code-key-logged", _CLIENT, (('"code", "otp", "nonce",\n', '"otp", "nonce",\n'),),
     _T_REDACT, "`code` is no longer redacted"),
    ("token-suffix-dropped", _CLIENT,
     (('redactedKeys.contains(lowered) || lowered.hasSuffix("_token")', "redactedKeys.contains(lowered)"),),
     _T_REDACT, "redact() no longer masks every `*_token` key"),
    # IOS-CONF-3, verbatim: the pre-fix value class.
    ("value-class-stops-at-escaped-quote", _CLIENT,
     ((r'\"(?:[^\"\\\\]|\\\\.)*+\""', r'\"[^\"]*\""'),),
     _T_PAIR, "leaks the tail of a secret holding a JSON-escaped quote"),
    # The same alternation WITHOUT the possessive: still correct here (no catastrophic case),
    # so this mutation must be killed by the escape cases, not slip through on timing.
    ("value-class-ignores-escapes", _CLIENT,
     ((r'\"(?:[^\"\\\\]|\\\\.)*+\""', r'\"(?:[^\"])*+\""'),),
     _T_PAIR, "leaks the tail of a secret holding a JSON-escaped quote"),
    # PW-6.
    ("eye-tiny", _SIGN_IN,
     ((f"{_I24}.frame(minWidth: Self.minTapTarget, minHeight: Self.minTapTarget)\n", ""),),
     _T_EYE, "no longer a 44-pt target"),
    ("eye-drops-focus", _SIGN_IN,
     (("Task { @MainActor in focusedField = .password }", "focusedField = .password"),),
     _T_EYE, "no longer re-asserts focus"),
    ("proofs-alphabetical", _PROOFS,
     ((f"{_I8}let oldestFirst = proofs\n{_I12}.filter {{ $0.key != kept }}\n"
       f"{_I12}.map {{ (issuedAt: issuedAt(of: $0.value), email: $0.key) }}\n"
       f"{_I12}.sorted {{ ($0.issuedAt, $0.email) < ($1.issuedAt, $1.email) }}\n",
       f"{_I8}let oldestFirst = proofs.keys.sorted().map {{ (issuedAt: 0, email: $0) }}\n"),),
     _T_PROOFS, "proofs are evicted alphabetically again"),
    ("proofs-bounded-before-insert", _PROOFS,
     ((f"{_I8}save(bounded(proofs, keeping: normalised))\n", f"{_I8}save(proofs)\n"),),
     _T_PROOFS, "remember() no longer bounds the map AFTER inserting"),
    ("signin-errors-silent", _SIGN_IN,
     ((f"{_I16}AccessibilityNotification.Announcement(message).post()\n", ""),),
     _T_A11Y, "SignInView's errors appear silently to VoiceOver"),
    ("code-field-unnamed", _CONFIRM, (('.accessibilityLabel("Code from email")', ""),),
     _T_A11Y, "ConfirmEmailView's code field has no name"),
    ("stale-code-error", _CONFIRM,
     (("                        errorMessage = nil\n                    }\n                }", "                    }\n                }"),),
     _T_CODE, "typing a new code no longer clears the previous code's error"),
]


@pytest.mark.parametrize("name,path,edits,guard,message", _MUTATIONS, ids=[m[0] for m in _MUTATIONS])
def test_each_mutation_is_killed(monkeypatch, name, path, edits, guard, message):
    """Patched in memory only — other sessions read these Swift files concurrently."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(path, encoding="utf-8")
    mutated = original
    for old, new in edits:
        assert old and mutated.count(old) == 1, (
            f"{name}: mutation anchor {old[:70]!r} occurs {mutated.count(old)}x in {path.name} — "
            "re-derive this mutation against the new source rather than deleting it")
        mutated = mutated.replace(old, new, 1)
    assert mutated != original

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == path:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    with pytest.raises(AssertionError, match=re.escape(message)):
        guard()


def test_the_mutation_table_covers_every_guard():
    covered = {m[3] for m in _MUTATIONS}
    for guard in (_T_RESEND, _T_ROUTE, _T_CODE, _T_RESEND_UI, _T_ADOPT, _T_APP_CONFIRM, _T_HALF,
                  _T_APPERR, _T_429, _T_FRIENDLY, _T_NONCE, _T_SOCIAL, _T_PURGE, _T_REDACT,
                  _T_PROOFS, _T_A11Y, _T_SPENT, _T_UNANSWERED, _T_SIGNUP_MSG, _T_404,
                  _T_POST_VERIFY, _T_PAIR, _T_EYE):
        assert guard in covered, f"{guard.__name__} has no mutation in _MUTATIONS"


def test_comment_stripping_is_load_bearing():
    """The comment above `AuthService.signIn` quotes the old `try?` resend and "fresh
    confirmation link" while explaining the bug; an un-stripped scan of that region would read
    the prose as code."""
    raw = _AUTH.read_text(encoding="utf-8")
    region = raw[raw.index("    /// Sign in with email and password."):raw.index(_AUTH_SIGN_IN)]
    assert "try?" in region and "fresh confirmation" in region, (
        "the explanatory comment no longer quotes the old code — fine, but this anti-vacuity "
        "probe then needs a new raw sample")
    stripped = _strip_swift_comments(raw)
    s_region = stripped[: stripped.index(_AUTH_SIGN_IN)]
    assert "fresh confirmation" not in s_region
    assert _strip_swift_comments("let x = 1 // try? resendConfirmation(\n// try?") == "let x = 1"
    assert "https://caydexinvest.com" in _strip_swift_comments('let u = "https://caydexinvest.com"')
