"""The iOS half of the Apple / Google sign-in hardening (sign-in audit, 2026-11-26).

There is no XCTest target, so these are SOURCE SCANS over the Swift tree (testing.md §3):
comments stripped, every check brace-bound to its declaration, and every guard mutation-tested
by `test_each_mutation_is_killed` (the Swift files are patched IN MEMORY — other sessions read
them concurrently).

What is pinned, each against the regression that would reopen it:

  SOC-07 Google nonce — native Google sign-in gives GIDSignIn the SHA-256 HEX of a fresh raw
         nonce and sends the RAW value to `/auth/oauth`. GoTrue hashes what it is sent and
         compares it with the ID token's claim, which GIDSignIn copies from what IT was given:
         raw-to-Google or hash-to-backend fails the moment "Skip nonce check" goes OFF.
  SOC-04 Apple revocation — the authorization code travels from the Apple credential through
         AppState and AuthService into the `/auth/oauth` body (`authorization_code`), for Apple
         only. The session's Apple ID is recorded as an ACCOUNT LINK on an Apple sign-in, cleared
         on every other sign-in and with the session tokens, and honoured only for the account
         signed in now. `getCredentialState` is asked on foreground, on iOS's revocation
         notification and when a session is established; only `.revoked` signs out, through
         `signOut()` (auth.md §8), and only if nothing moved the session while Apple answered.
         GoogleSignIn is signed out on every sign-out and DISCONNECTED (grant revoked) after an
         account deletion — disconnect before the sign-out, from both deletion screens.
  SOC-05 Apple's first-time name — kept in the Keychain from the moment Apple hands it over,
         resent until an `/auth/oauth` succeeds (only for the same Apple ID), cleared after that
         success and when a session ends; capped at the backend's 64-scalar limit, because a
         longer name 422'd the whole sign-in.
"""

from __future__ import annotations

import pathlib
import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios" / "ios"
_SOCIAL = _IOS / "Core/Services/SocialSignInService.swift"
_STORE = _IOS / "Core/Services/AppleSignInStore.swift"
_AUTH = _IOS / "Core/Services/AuthService.swift"
_APP = _IOS / "Core/State/AppState.swift"
_ENDPOINT = _IOS / "Core/Services/APIEndpoint.swift"
_CLIENT = _IOS / "Core/Services/APIClient.swift"
_SETTINGS = _IOS / "Views/Screens/AppSettingsView.swift"
_PROFILE_VM = _IOS / "ViewModels/ProfileViewModel.swift"
_USER_SCHEMA = _REPO / "backend" / "app" / "schemas" / "user.py"

_GOOGLE_NATIVE = "fileprivate func signInWithGoogleNative(clientID: String) async throws -> SocialSignInResult"
_APPLE_HANDLE = "func handleAppleCompletion("
_END_GOOGLE = "static func endGoogleSessionNative()"
_DISCONNECT_GOOGLE = "static func disconnectGoogleGrantNative()"
_AUTH_PROVIDER = "func signInWithProvider("
_AUTH_SIGN_IN = "func signIn(email: String, password: String) async throws -> UserProfile"
_AUTH_CONFIRM = "func confirmEmail(email: String, code: String, password: String) async throws -> UserProfile"
_AUTH_SIGN_UP = "func signUp("
_AUTH_EXCHANGE = "func exchangeSupabaseSession(accessToken supabaseToken: String) async throws -> UserProfile"
_AUTH_CLEAR = "func clearToken()"
_AUTH_FINISH = "func finishSignOut(_ handoff: SignOutHandoff) async"
_AUTH_RECORD = "private func recordAppleIdentity(_ appleUserID: String?, accountId: String?)"
_APP_SOCIAL = "func completeSocialSignIn(_ result: SocialSignInResult) async throws"
_APP_CHECK = "func checkAppleCredentialState(trigger: String) async"
_APP_CONFIGURE = "func configure(apiClient: APIClient, authService: AuthService)"
_APP_ESTABLISH = "private func establishAuthenticatedSession(userId: String) async"
_APP_AFTER_DELETE = "func signOutAfterAccountDeletion()"
_APP_DISCARD = "private func discardDataForEndedSession()"
_STORE_NAME = "static func nameForSignIn("
_STORE_LINKED = "static func appleUserID(linkedTo accountId: String, keychain: KeychainService = .shared) -> String?"
_STORE_NORMALISE = "static func normalisedName(_ raw: String?) -> String?"
_ENDPOINT_BODY_CASE = "case .oauthSignIn(let provider, let idToken, let nonce, let displayName, let authorizationCode):"
_ENDPOINT_STRUCT = "nonisolated struct OAuthSignInRequest: Encodable, Sendable"
# Since migration 214 (server-side sessions) deletion takes the re-authentication proof.
_SETTINGS_DELETE = "private func deleteAccount(reauthToken: String) {"
_VM_DELETE = "func deleteAccount() {"


# ── Scanning helpers (no shared conftest — testing.md) ────────────────────────────────

def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails (a tail needs
    leading whitespace, so a `https://` literal survives). Load-bearing: the comments beside
    these fixes name `signOut()`, `nonce`, `clearToken` and `.notFound` while explaining them."""
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
                return src[start: i + 1]
    raise AssertionError(f"unbalanced braces after `{header}`")


def _in_order(body: str, steps: tuple[str, ...]) -> bool:
    at = [body.find(s) for s in steps]
    return all(i != -1 for i in at) and at == sorted(at)


def _squash(s: str) -> str:
    return re.sub(r"\s+", " ", s)


# ── SOC-07: the Google nonce ─────────────────────────────────────────────────────────

def _t_google_nonce() -> None:
    body = _squash(_block(_code(_SOCIAL), _GOOGLE_NATIVE))
    assert "GIDSignIn.sharedInstance.signIn(" in body, (
        "not the native Google sign-in — this scan is bound to the wrong declaration")
    assert "let rawNonce = Self.randomNonceString()" in body, (
        "native Google sign-in no longer generates a fresh raw nonce per attempt")
    call = body[body.index("GIDSignIn.sharedInstance.signIn("):]
    call = call[: call.index(")", call.index("nonce:")) + 1] if "nonce:" in call else call[:200]
    assert "nonce: Self.sha256(rawNonce)" in call, (
        "Google is no longer given the SHA-256 of the raw nonce — GIDSignIn puts what it is given "
        "into the ID token's claim, and GoTrue compares that claim with sha256(raw)")
    ret = body[body.index('.identityToken( provider: "google"'):]
    ret = ret[: ret.index(")") + 1]
    assert "nonce: rawNonce," in ret, (
        "native Google sign-in no longer sends the RAW nonce to the backend (hash to Google, raw "
        "to /auth/oauth)")
    assert "sha256" not in ret and "nonce: nil" not in ret, (
        "native Google sign-in sends the backend a hashed or no nonce")


def test_google_gets_the_hash_and_the_backend_gets_the_raw_nonce():
    _t_google_nonce()


# ── SOC-04: the Apple authorization code reaches /auth/oauth ─────────────────────────

def _t_apple_code() -> None:
    handle = _squash(_block(_code(_SOCIAL), _APPLE_HANDLE))
    assert "credential.authorizationCode" in handle, (
        "handleAppleCompletion no longer reads Apple's authorization code")
    assert "authorizationCode: authorizationCode," in handle and "appleUserID: credential.user" in handle, (
        "handleAppleCompletion no longer returns the authorization code and Apple user id")
    social = _squash(_block(_code(_APP), _APP_SOCIAL))
    assert "authorizationCode: authorizationCode, appleUserID: appleUserID" in social, (
        "completeSocialSignIn no longer passes the authorization code and Apple user id to AuthService")
    provider = _squash(_block(_code(_AUTH), _AUTH_PROVIDER))
    assert "authorizationCode: isApple ? authorizationCode : nil" in provider, (
        "signInWithProvider no longer sends the authorization code for Apple only")
    endpoint = _code(_ENDPOINT)
    at = endpoint.index(_ENDPOINT_BODY_CASE)
    case_body = _squash(endpoint[at: endpoint.index(")", endpoint.index("OAuthSignInRequest(", at)) + 1])
    assert "authorizationCode: authorizationCode" in case_body, (
        "the oauthSignIn body no longer carries the authorization code")
    assert "let authorizationCode: String?" in _block(endpoint, _ENDPOINT_STRUCT), (
        "OAuthSignInRequest lost its optional authorizationCode (→ authorization_code)")


def test_the_apple_authorization_code_reaches_the_oauth_body():
    _t_apple_code()


def test_the_wire_name_matches_the_backend_schema():
    """`APIClient` encodes with `.convertToSnakeCase`: `authorizationCode` → `authorization_code`,
    which must be the Pydantic field, or the backend silently ignores it (extra keys are allowed)."""
    from app.schemas.auth import OAuthSignInRequest

    assert "authorization_code" in OAuthSignInRequest.model_fields
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", "authorizationCode").lower()
    assert snake == "authorization_code"
    assert not OAuthSignInRequest.model_fields["authorization_code"].is_required(), (
        "builds 1.0–1.03 send no code: the field must stay optional")


# ── SOC-04: the account link and the credential-state check ──────────────────────────

def _t_account_link() -> None:
    auth = _code(_AUTH)
    assert "AppleSignInStore.clearAccountLink(keychain: keychain)" in _block(auth, _AUTH_CLEAR), (
        "clearToken no longer drops the Apple account link with the session tokens")
    for header, expect in ((_AUTH_SIGN_IN, "recordAppleIdentity(nil,"),
                           (_AUTH_CONFIRM, "recordAppleIdentity(nil,"),
                           (_AUTH_SIGN_UP, "recordAppleIdentity(nil,"),
                           (_AUTH_EXCHANGE, "recordAppleIdentity(nil,"),
                           (_AUTH_PROVIDER, "recordAppleIdentity(isApple ? appleUserID : nil,")):
        body = _block(auth, header)
        assert _in_order(body, (expect, "saveTokens(")), (
            f"AuthService `{header}` no longer records the session's Apple identity before saving "
            f"its tokens (`{expect}` …) — a link from an earlier session could sign this one out")
    record = _block(auth, _AUTH_RECORD)
    assert "AppleSignInStore.clearAccountLink(keychain: keychain)" in record \
        and "AppleSignInStore.setAccountLink(" in record, "recordAppleIdentity no longer sets / clears the link"
    linked = _block(_code(_STORE), _STORE_LINKED)
    assert "link.accountId == accountId" in linked, (
        "the account link is honoured for an account it does not belong to")


def test_the_account_link_lives_exactly_as_long_as_its_apple_session():
    _t_account_link()


def _t_credential_check() -> None:
    app = _code(_APP)
    check = _block(app, _APP_CHECK)
    assert "guard auth.status == .authenticated" in check, (
        "the Apple credential check runs without a signed-in session")
    assert re.search(r"await\s+Task\.detached\([^)]*\)\s*\{\s*AppleSignInStore\.appleUserID\(linkedTo: accountId\)",
                     check), "the account link is no longer read OFF the main actor through Task.detached"
    ask = check.find("credentialState(forUserID: appleUserID)")
    guard = re.search(r"guard\s+generation\s*==\s*credentialGeneration,\s*auth\.status\s*==\s*\.authenticated,"
                      r"\s*user\.profile\?\.id\s*==\s*accountId\s+else\s*\{\s*return\s*\}", check)
    assert ask != -1 and guard and guard.start() > ask, (
        "the Apple credential check acts on Apple's answer without re-checking that the session did "
        "not move while Apple answered — it could sign out a NEWER session")
    revoked = check[check.index("case .revoked:"):]
    revoked = revoked[: revoked.index("case .", len("case .revoked:"))]
    assert "signOut()" in revoked, "a revoked Apple credential no longer signs the session out"
    assert "clearToken" not in revoked and "disarm" not in revoked, (
        "a revoked Apple credential ends the session around signOut() — auth.md §8 ordering")
    rest = check[check.index("case .revoked:") + len(revoked):]
    assert "signOut()" not in rest, (
        "the Apple credential check signs out on an answer other than .revoked (notFound / "
        "transferred / unknown) — the worse failure for an answer we cannot explain")
    configure = _squash(_block(app, _APP_CONFIGURE))
    for note, trigger in (("UIApplication.didBecomeActiveNotification", '"foreground"'),
                          ("ASAuthorizationAppleIDProvider.credentialRevokedNotification", '"revoked-notification"')):
        at = configure.find(note)
        assert at != -1 and f"checkAppleCredentialState(trigger: {trigger})" in configure[at: at + 300], (
            f"configure no longer re-checks the Apple credential on `{note}`")
    assert "checkAppleCredentialState(trigger:" in _block(app, _APP_ESTABLISH), (
        "a cold launch no longer checks the Apple credential once the session is established")


def test_a_revoked_apple_credential_signs_out_through_sign_out():
    _t_credential_check()


# ── SOC-04: GoogleSignIn ends with the session; deletion revokes its grant ───────────

def _t_google_session_end() -> None:
    social = _code(_SOCIAL)
    assert "GIDSignIn.sharedInstance.signOut()" in _block(social, _END_GOOGLE)
    assert "GIDSignIn.sharedInstance.disconnect" in _block(social, _DISCONNECT_GOOGLE)
    finish = _block(_code(_AUTH), _AUTH_FINISH)
    assert _in_order(finish, ("SocialSignInService.endGoogleSession()", "if let endedToken")), (
        "finishSignOut no longer signs GoogleSignIn out (first, before the round trips a kill can cut)")
    # Review 2026-11-27: a session the SERVER ended (dead credential, undone adoption, the
    # fresh-install purge) reaches only clearToken, so the Google SDK must end there too.
    assert "SocialSignInService.endGoogleSession()" in _block(_code(_AUTH), _AUTH_CLEAR), (
        "clearToken no longer signs GoogleSignIn out, so a server-ended session leaves it signed in")
    after = _block(_code(_APP), _APP_AFTER_DELETE)
    assert _in_order(after, ("SocialSignInService.disconnectGoogleGrant()", "signOut()")), (
        "account deletion no longer disconnects Google BEFORE signing out — the sign-out clears "
        "the token the disconnect needs")
    for path, header, request in ((_SETTINGS, _SETTINGS_DELETE, "request(endpoint: .deleteAccount("),
                                  (_PROFILE_VM, _VM_DELETE, "request(endpoint: .deleteAccount(")):
        body = _block(_code(path), header)
        tail = body[body.index(request):]
        assert "signOutAfterAccountDeletion()" in tail and not re.search(r"\.signOut\(\)", tail), (
            f"{path.name} signs out after a deletion without revoking the Google grant")


def test_google_sign_in_ends_with_the_session_and_deletion_revokes_it():
    _t_google_session_end()


# ── SOC-05: the pending name ─────────────────────────────────────────────────────────

def _t_pending_name() -> None:
    provider = _squash(_block(_code(_AUTH), _AUTH_PROVIDER))
    assert _in_order(provider, ("appleNameForSignIn(received: displayName, appleUserID: appleUserID",
                                "apiClient.request(",
                                "AppleSignInStore.clearPendingName(keychain: keychain)")), (
        "signInWithProvider no longer resolves the pending Apple name before the request and "
        "clears it only after the request succeeded")
    assert "displayName: name," in provider, "signInWithProvider sends Apple's raw name, not the pending one"
    name = _block(_code(_STORE), _STORE_NAME)
    assert "pending.appleUserID == appleUserID" in name, (
        "a pending name is resent for an Apple ID it was not kept for")
    assert "keychain.set(raw, forKey: pendingNameKey)" in name, (
        "a name Apple sent is no longer kept until a sign-in succeeds")
    assert "AppleSignInStore.clearForEndedSession()" in _block(_code(_APP), _APP_DISCARD), (
        "a session end no longer clears the pending Apple name and the account link (auth.md §7)")


def test_apples_first_time_name_survives_a_failed_first_sign_in():
    _t_pending_name()


def _t_name_cap() -> None:
    store = _code(_STORE)
    m = re.search(r"static let maxNameScalars = (\d+)", store)
    backend = re.search(r"^DISPLAY_NAME_MAX_LENGTH = (\d+)", _USER_SCHEMA.read_text(), re.M)
    assert m and backend and m.group(1) == backend.group(1), (
        "maxNameScalars no longer equals the backend's DISPLAY_NAME_MAX_LENGTH — a longer name "
        "422s the whole /auth/oauth request")
    norm = _block(store, _STORE_NORMALISE)
    assert "unicodeScalars.count > maxNameScalars" in norm and "unicodeScalars.prefix(maxNameScalars)" in norm, (
        "the name cap no longer counts Unicode scalars (Python's len), so a name of combining "
        "characters can still pass the cap and fail the backend's")
    provider = _squash(_block(_code(_AUTH), _AUTH_PROVIDER))
    assert ": AppleSignInStore.normalisedName(displayName)" in provider, (
        "a Google name is sent uncapped — over 64 characters an older backend 422s the sign-in")


def test_the_name_is_capped_where_the_backend_caps_it():
    _t_name_cap()


def _t_redaction() -> None:
    keys = _code(_CLIENT)
    keys = keys[keys.index("private static let redactedKeys"):]
    keys = keys[: keys.index("]")]
    assert '"authorization_code"' in keys, (
        "the DEBUG request logger no longer redacts Apple's authorization code")


def test_the_debug_logger_redacts_the_authorization_code():
    _t_redaction()


# ── Mutations: each guard must fail on the bug it names ─────────────────────────────

_I8, _I12, _I16 = " " * 8, " " * 12, " " * 16

_MUTATIONS = [
    ("google-raw-to-google", _SOCIAL,
     ((f"{_I16}nonce: Self.sha256(rawNonce)\n", f"{_I16}nonce: rawNonce\n"),),
     _t_google_nonce, "Google is no longer given the SHA-256 of the raw nonce"),
    ("google-hash-to-backend", _SOCIAL,
     ((f"{_I12}nonce: rawNonce,\n", f"{_I12}nonce: Self.sha256(rawNonce),\n"),),
     _t_google_nonce, "native Google sign-in no longer sends the RAW nonce to the backend"),
    ("google-no-nonce", _SOCIAL,
     ((f"{_I12}nonce: rawNonce,\n", f"{_I12}nonce: nil,\n"),),
     _t_google_nonce, "native Google sign-in no longer sends the RAW nonce to the backend"),
    ("google-old-call", _SOCIAL,
     ((f"result = try await GIDSignIn.sharedInstance.signIn(\n{_I16}withPresenting: presenter,\n"
       f"{_I16}hint: nil,\n{_I16}additionalScopes: nil,\n{_I16}nonce: Self.sha256(rawNonce)\n{_I12})",
       "result = try await GIDSignIn.sharedInstance.signIn(withPresenting: presenter)"),),
     _t_google_nonce, "Google is no longer given the SHA-256 of the raw nonce"),
    ("apple-code-dropped-in-appstate", _APP,
     (("authorizationCode: authorizationCode, appleUserID: appleUserID", "authorizationCode: nil, appleUserID: appleUserID"),),
     _t_apple_code, "completeSocialSignIn no longer passes the authorization code"),
    ("apple-code-dropped-in-body", _ENDPOINT,
     (("                authorizationCode: authorizationCode\n", "                authorizationCode: nil\n"),),
     _t_apple_code, "the oauthSignIn body no longer carries the authorization code"),
    ("code-sent-for-google", _AUTH,
     (("authorizationCode: isApple ? authorizationCode : nil", "authorizationCode: authorizationCode"),),
     _t_apple_code, "signInWithProvider no longer sends the authorization code for Apple only"),
    ("google-outlives-server-ended-session", _AUTH,
     (("        SocialSignInService.endGoogleSession()\n        // The Apple account link", "        // The Apple account link"),),
     _t_google_session_end, "clearToken no longer signs GoogleSignIn out"),
    ("link-outlives-session", _AUTH,
     (("        AppleSignInStore.clearAccountLink(keychain: keychain)\n    }\n}", "    }\n}"),),
     _t_account_link, "clearToken no longer drops the Apple account link"),
    ("email-sign-in-keeps-old-link", _AUTH,
     (("        recordAppleIdentity(nil, accountId: response.userId)\n        // Store tokens\n",
       "        // Store tokens\n"),),
     _t_account_link, "no longer records the session's Apple identity before saving"),
    ("google-records-an-apple-link", _AUTH,
     (("recordAppleIdentity(isApple ? appleUserID : nil,", "recordAppleIdentity(appleUserID,"),),
     _t_account_link, "no longer records the session's Apple identity before saving"),
    ("link-for-any-account", _STORE,
     (("              link.accountId == accountId, !link.appleUserID.isEmpty else {",
       "              !link.appleUserID.isEmpty else {"),),
     _t_account_link, "the account link is honoured for an account it does not belong to"),
    ("check-no-generation-guard", _APP,
     (("        guard generation == credentialGeneration, auth.status == .authenticated,\n"
       "              user.profile?.id == accountId else { return }\n", ""),),
     _t_credential_check, "without re-checking that the session did not move"),
    ("check-signs-out-on-not-found", _APP,
     (("        case .notFound:\n", "        case .notFound:\n            signOut()\n"),),
     _t_credential_check, "signs out on an answer other than .revoked"),
    ("check-bare-keychain-clear", _APP,
     (("            signOut()\n            currentError = .sessionEnded(",
       "            authService.clearToken()\n            currentError = .sessionEnded("),),
     _t_credential_check, "a revoked Apple credential no longer signs the session out"),
    ("check-no-foreground", _APP,
     (('await self?.checkAppleCredentialState(trigger: "foreground")', "_ = self"),),
     _t_credential_check, "configure no longer re-checks the Apple credential"),
    ("check-not-at-launch", _APP,
     (('Task { [weak self] in await self?.checkAppleCredentialState(trigger: "session-established") }', ""),),
     _t_credential_check, "a cold launch no longer checks the Apple credential"),
    ("signout-keeps-google", _AUTH,
     (("        SocialSignInService.endGoogleSession()\n\n        if handoff.endedByServer {",
       "\n        if handoff.endedByServer {"),),
     _t_google_session_end, "finishSignOut no longer signs GoogleSignIn out"),
    ("delete-signs-out-first", _APP,
     (("        SocialSignInService.disconnectGoogleGrant()\n        signOut()\n",
       "        signOut()\n        SocialSignInService.disconnectGoogleGrant()\n"),),
     _t_google_session_end, "account deletion no longer disconnects Google BEFORE signing out"),
    ("settings-delete-plain-signout", _SETTINGS,
     (("                appState.signOutAfterAccountDeletion()\n", "                appState.signOut()\n"),),
     _t_google_session_end, "AppSettingsView.swift signs out after a deletion without revoking"),
    ("profile-delete-plain-signout", _PROFILE_VM,
     (("            self?.appState?.signOutAfterAccountDeletion()\n", "            self?.appState?.signOut()\n"),),
     _t_google_session_end, "ProfileViewModel.swift signs out after a deletion without revoking"),
    ("name-cleared-before-success", _AUTH,
     (("        if isApple { AppleSignInStore.clearPendingName(keychain: keychain) }\n", ""),
      ("        let isApple = provider == \"apple\"\n",
       "        let isApple = provider == \"apple\"\n        if isApple { AppleSignInStore.clearPendingName(keychain: keychain) }\n")),
     _t_pending_name, "signInWithProvider no longer resolves the pending Apple name before the request"),
    ("name-raw-sent", _AUTH,
     (("provider: provider, idToken: idToken, nonce: nonce, displayName: name,",
       "provider: provider, idToken: idToken, nonce: nonce, displayName: displayName,"),),
     _t_pending_name, "signInWithProvider sends Apple's raw name"),
    ("name-any-apple-id", _STORE,
     (("              pending.appleUserID == appleUserID else {", "              true else {"),),
     _t_pending_name, "a pending name is resent for an Apple ID it was not kept for"),
    ("name-survives-session-end", _APP,
     (("        AppleSignInStore.clearForEndedSession()\n", ""),),
     _t_pending_name, "a session end no longer clears the pending Apple name"),
    ("cap-too-high", _STORE,
     (("static let maxNameScalars = 64", "static let maxNameScalars = 100"),),
     _t_name_cap, "maxNameScalars no longer equals the backend's DISPLAY_NAME_MAX_LENGTH"),
    ("cap-counts-graphemes", _STORE,
     (("guard trimmed.unicodeScalars.count > maxNameScalars", "guard trimmed.count > maxNameScalars"),),
     _t_name_cap, "the name cap no longer counts Unicode scalars"),
    ("google-name-uncapped", _AUTH,
     (("            : AppleSignInStore.normalisedName(displayName)\n", "            : displayName\n"),),
     _t_name_cap, "a Google name is sent uncapped"),
    ("code-logged-in-debug", _CLIENT,
     (('        "authorization_code",\n', ""),),
     _t_redaction, "the DEBUG request logger no longer redacts Apple's authorization code"),
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
    assert mutated != original, f"{name}: the mutation changed nothing"

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == path:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    with pytest.raises(AssertionError, match=re.escape(message)):
        guard()


def test_the_mutation_table_covers_every_guard():
    covered = {m[3] for m in _MUTATIONS}
    for guard in (_t_google_nonce, _t_apple_code, _t_account_link, _t_credential_check,
                  _t_google_session_end, _t_pending_name, _t_name_cap, _t_redaction):
        assert guard in covered, f"{guard.__name__} has no mutation in _MUTATIONS"


def test_comment_stripping_is_load_bearing():
    """The comment above `checkAppleCredentialState` names `signOut()` and `.notFound`, and the one
    in `signInWithGoogleNative` describes the raw/hash split: un-stripped, prose would satisfy
    (or trip) these scans."""
    raw = _APP.read_text(encoding="utf-8")
    region = raw[raw.index("    /// End a session whose Apple ID no longer authorises Caydex."):raw.index(_APP_CHECK)]
    assert "signOut()" in region and ".notFound" in region, (
        "the explanatory comment no longer quotes these tokens — then this probe needs a new sample")
    assert _strip_swift_comments("let x = 1 // signOut()\n/// .notFound") == "let x = 1"
    assert "https://appleid.apple.com" in _strip_swift_comments('let u = "https://appleid.apple.com"')
