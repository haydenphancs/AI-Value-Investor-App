"""iOS half of server-side sessions (migration 214) and of re-authentication before deletion
(SEC-11), pinned from the Swift source — there is no XCTest target.

What must not regress:
  * "Sign out of all devices" signs out locally ONLY after the server confirmed, and marks the
    sign-out as server-ended in the same main-actor turn (no `await` between), so no other
    sign-out can consume the mark and `finishSignOut` skips two guaranteed 401s.
  * Settings' "Delete Forever" opens the "confirm it's you" sheet; the deletion itself sends the
    proof (`.deleteAccount(reauthToken:)`), and `APIClient` puts it in `X-Caydex-Reauth`.
  * The confirm step sends the provider token to `/auth/reauthenticate`, never to
    `/auth/oauth` / `/auth/session-exchange` (which would mint and adopt a new session).
  * 403 AUTH_REAUTH_REQUIRED is a typed, NON-auth error: it must never clear the session.
  * `.signOut` carries the refresh token in its body; `.signOutAllDevices` / `.reauthenticate`
    are `.signInRequired` (the policy parity test pins those against the backend).

Every guard strips comments first and is brace-bound to its declaration, and each one is
mutation-tested below (testing.md §3).
"""

from __future__ import annotations

import pathlib
import re
from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_AUTH_SERVICE = _IOS / "Core" / "Services" / "AuthService.swift"
_CLIENT = _IOS / "Core" / "Services" / "APIClient.swift"
_ENDPOINT = _IOS / "Core" / "Services" / "APIEndpoint.swift"
_APP_ERROR = _IOS / "Core" / "Utilities" / "AppError.swift"
_SETTINGS = _IOS / "Views" / "Screens" / "AppSettingsView.swift"
_CONFIRM_VM = _IOS / "ViewModels" / "ConfirmIdentityViewModel.swift"


def _strip(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        out.append(re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _code(path: Path) -> str:
    assert path.exists(), f"expected to exist: {path}"
    return _strip(path.read_text(encoding="utf-8"))


def _block(src: str, header: str) -> str:
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    start = src.index("{", src.index(header) + len(header) - 1)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start:i + 1]
    raise AssertionError(f"unbalanced braces after `{header}`")


# ── 1. Sign out of all devices ────────────────────────────────────────────────

def test_sign_out_everywhere_signs_out_locally_only_after_the_server_confirmed():
    body = _block(_code(_APP_STATE), "func signOutEverywhere() async throws")
    server = body.find("try await authService.signOutAllDevices()")
    note = body.find("authService.noteSessionsEndedByServer()")
    local = body.find("signOut()", note if note >= 0 else 0)
    assert 0 <= server < note < local, (
        "AppState.signOutEverywhere no longer calls the server FIRST, then marks the sign-out as "
        "server-ended, then signs out — a failure would sign out only this device, or the mark "
        "would precede a server answer that never came")
    assert "await" not in body[note:local], (
        "AppState.signOutEverywhere suspends between the server-ended mark and signOut() — another "
        "sign-out could consume the mark")


def test_the_server_ended_mark_is_consumed_by_the_next_begin_only():
    code = _code(_AUTH_SERVICE)
    begin = _block(code, "func beginSignOut() -> SignOutHandoff")
    assert re.search(r"let\s+endedByServer\s*=\s*nextSignOutEndedByServer", begin) and \
        "nextSignOutEndedByServer = false" in begin, (
        "AuthService.beginSignOut no longer consumes (reads AND resets) the server-ended mark — a "
        "stale mark would silently skip a later sign-out's logout")
    note = _block(code, "func noteSessionsEndedByServer()")
    assert "nextSignOutEndedByServer = true" in note


def test_the_refresh_token_mirror_follows_every_keychain_write():
    """`beginSignOut` hands `/auth/logout` the mirrored refresh token. A mirror a write forgot to
    update would send ANOTHER session's token (or a cleared one) — and it must never be filled by
    a synchronous Keychain read (`test_ios_launch_no_main_thread_keychain.py`)."""
    code = _code(_AUTH_SERVICE)
    save = _block(code, "private func saveTokens(accessToken: String, refreshToken: String)")
    clear = _block(code, "func clearToken()")
    load = _block(code, "private func loadStoredRefreshToken() async -> String?")
    assert "refreshTokenMirror = refreshToken" in save, (
        "saveTokens no longer updates the refresh-token mirror — sign-out would name a stale session")
    assert "refreshTokenMirror = nil" in clear, (
        "clearToken no longer drops the refresh-token mirror — a later sign-out could send a dead token")
    m = re.search(r"if\s+writes\s*==\s*keychainWrites\s*\{\s*refreshTokenMirror\s*=\s*token\s*\}", load)
    assert m, ("loadStoredRefreshToken no longer fills the mirror only when no write overtook its "
               "off-main read — a stale read could replace a newer session's token")


def test_sign_out_all_devices_uses_the_armed_client():
    body = _block(_code(_AUTH_SERVICE), "func signOutAllDevices() async throws")
    assert re.search(r"apiClient\.request\(endpoint:\s*\.signOutAllDevices\)", body), (
        "AuthService.signOutAllDevices no longer sends POST /auth/sign-out-all through the armed "
        "client (the session is still live; an expired token must be refreshed first)")


# ── 2. Account deletion re-authenticates ──────────────────────────────────────

def test_delete_forever_opens_the_confirm_step_not_the_deletion():
    code = _code(_SETTINGS)
    alert = code[code.index('.alert("Delete Account"'):]
    alert = alert[: alert.index("} message:")]
    assert re.search(r'Button\("Delete Forever",\s*role:\s*\.destructive\)\s*\{\s*showConfirmIdentity\s*=\s*true\s*\}', alert), (
        "Settings' Delete Forever no longer opens the confirm-it's-you step (SEC-11)")
    assert "deleteAccount(" not in alert, "Delete Forever deletes without re-authentication"


def test_the_confirm_sheet_hands_its_proof_to_the_deletion():
    code = _code(_SETTINGS)
    sheet = code[code.index(".sheet(isPresented: $showConfirmIdentity)"):]
    sheet = sheet[: sheet.index(".alert(")]
    assert re.search(r"onConfirmed:\s*\{\s*proof\s+in\s+deleteAccount\(reauthToken:\s*proof\)\s*\}", sheet), (
        "the confirm sheet no longer hands its proof to deleteAccount(reauthToken:)")
    assert re.search(r"\.environment\(\\\.appState,\s*appState\)", sheet), (
        "the confirm sheet lost its `\\.appState` re-injection (ios-swiftui.md: a sheet does not "
        "inherit it and silently gets a throwaway AppState)")


def test_the_deletion_sends_the_proof():
    body = _block(_code(_SETTINGS), "private func deleteAccount(reauthToken: String)")
    assert re.search(r"\.deleteAccount\(reauthToken:\s*reauthToken\)", body), (
        "AppSettingsView.deleteAccount no longer sends the re-authentication proof")


def test_the_client_sends_the_proof_header():
    build = _block(_code(_CLIENT), "private func buildRequest(for endpoint: APIEndpoint")
    assert re.search(
        r"if\s+let\s+(\w+)\s*=\s*endpoint\.reauthProof[^{]*\{\s*request\.setValue\(\1,\s*forHTTPHeaderField:\s*\"X-Caydex-Reauth\"\)",
        build), "APIClient.buildRequest no longer sends the deletion proof as X-Caydex-Reauth"
    prop = _block(_code(_ENDPOINT), "nonisolated var reauthProof: String?")
    assert re.search(r"case\s+\.deleteAccount\(let\s+(\w+)\)\s*=\s*self\s*\{\s*return\s+\1", prop), (
        "APIEndpoint.reauthProof no longer reads the token from .deleteAccount")


def test_the_confirm_step_never_signs_in():
    code = _code(_CONFIRM_VM)
    assert "authService.reauthenticate(" in code, (
        "ConfirmIdentityViewModel no longer sends the credential to /auth/reauthenticate")
    for forbidden in ("completeSocialSignIn", "signInWithProvider", "exchangeSupabaseSession",
                      ".oauthSignIn", ".sessionExchange", ".signIn("):
        assert forbidden not in code, (
            f"ConfirmIdentityViewModel uses `{forbidden}` — the re-authentication would mint and "
            "adopt a NEW session (and for an unknown Apple/Google subject, a new account)")


# ── 3. The error and the endpoint shapes ──────────────────────────────────────

def test_reauth_required_is_a_typed_non_auth_error():
    code = _code(_APP_ERROR)
    m = re.search(r'if\s+code\s*==\s*"AUTH_REAUTH_REQUIRED"\s*\{\s*return\s+\.(\w+)', code)
    assert m and m.group(1) == "validationFailed", (
        "AUTH_REAUTH_REQUIRED no longer maps to .validationFailed — it would fall through to "
        ".apiError (Retry) or, worse, an auth case that clears the session")
    auth_arm = code[code.index("case .authError(let code, let message):"):]
    auth_arm = auth_arm[: auth_arm.index("case .notFound:")]
    assert "AUTH_REAUTH_REQUIRED" not in auth_arm, (
        "AUTH_REAUTH_REQUIRED is on the 401 arm — it is a 403 and must not be read as a session "
        "failure")


def test_sign_out_carries_the_refresh_token_and_the_new_cases_are_sign_in_required():
    code = _code(_ENDPOINT)
    assert re.search(r"case\s+signOut\(refreshToken:\s*String\?\)", code)
    body = _block(code, "nonisolated var body: (any Encodable & Sendable)?")
    assert re.search(r"case\s+\.signOut\(let\s+(\w+)\):\s*[^\n]*\n?\s*return\s+\1\.map\s*\{\s*LogoutRequest\(refreshToken:\s*\$0\)\s*\}",
                     body), "`.signOut` no longer sends its refresh token in the logout body"
    policy = _block(code, "nonisolated var authPolicy: AuthPolicy")
    assert re.search(r"case\s+\.signOutAllDevices,\s*\.reauthenticate:\s*return\s+\.signInRequired", policy), (
        "`.signOutAllDevices` / `.reauthenticate` are no longer .signInRequired")


# ── 4. Mutation table ─────────────────────────────────────────────────────────

_I8 = " " * 8
_I12 = " " * 12
_I16 = " " * 16

_MUTATIONS = [
    ("local-sign-out-first", _APP_STATE,
     ((f"{_I8}try await authService.signOutAllDevices()\n{_I8}authService.noteSessionsEndedByServer()\n{_I8}signOut()\n",
       f"{_I8}signOut()\n{_I8}try await authService.signOutAllDevices()\n{_I8}authService.noteSessionsEndedByServer()\n"),),
     test_sign_out_everywhere_signs_out_locally_only_after_the_server_confirmed,
     "no longer calls the server FIRST"),
    ("await-between-mark-and-signout", _APP_STATE,
     ((f"{_I8}authService.noteSessionsEndedByServer()\n{_I8}signOut()\n",
       f"{_I8}authService.noteSessionsEndedByServer()\n{_I8}await Task.yield()\n{_I8}signOut()\n"),),
     test_sign_out_everywhere_signs_out_locally_only_after_the_server_confirmed,
     "suspends between the server-ended mark"),
    ("mark-never-reset", _AUTH_SERVICE,
     ((f"{_I8}nextSignOutEndedByServer = false\n", ""),),
     test_the_server_ended_mark_is_consumed_by_the_next_begin_only, "no longer consumes"),
    ("mirror-not-saved", _AUTH_SERVICE,
     ((f"{_I8}refreshTokenMirror = refreshToken\n", ""),),
     test_the_refresh_token_mirror_follows_every_keychain_write, "saveTokens no longer updates"),
    ("mirror-not-cleared", _AUTH_SERVICE,
     ((f"{_I8}refreshTokenMirror = nil\n", ""),),
     test_the_refresh_token_mirror_follows_every_keychain_write, "clearToken no longer drops"),
    ("mirror-stale-read", _AUTH_SERVICE,
     ((f"{_I8}if writes == keychainWrites {{\n{_I12}refreshTokenMirror = token\n{_I8}}}\n",
       f"{_I8}refreshTokenMirror = token\n"),),
     test_the_refresh_token_mirror_follows_every_keychain_write, "only when no write overtook"),
    ("delete-forever-deletes", _SETTINGS,
     (('Button("Delete Forever", role: .destructive) { showConfirmIdentity = true }',
       'Button("Delete Forever", role: .destructive) { deleteAccount(reauthToken: "") }'),),
     test_delete_forever_opens_the_confirm_step_not_the_deletion, "no longer opens the confirm"),
    ("proof-dropped", _SETTINGS,
     (("endpoint: .deleteAccount(reauthToken: reauthToken)", "endpoint: .deleteAccount(reauthToken: nil)"),),
     test_the_deletion_sends_the_proof, "no longer sends the re-authentication proof"),
    ("sheet-ignores-proof", _SETTINGS,
     (("onConfirmed: { proof in deleteAccount(reauthToken: proof) }", "onConfirmed: { _ in }"),),
     test_the_confirm_sheet_hands_its_proof_to_the_deletion, "no longer hands its proof"),
    ("header-not-sent", _CLIENT,
     (('request.setValue(proof, forHTTPHeaderField: "X-Caydex-Reauth")', "_ = proof"),),
     test_the_client_sends_the_proof_header, "no longer sends the deletion proof"),
    ("confirm-signs-in", _CONFIRM_VM,
     (("return try await authService.reauthenticate(body)",
       "_ = try? await authService.signInWithProvider(provider: \"apple\", idToken: \"\", nonce: nil, displayName: nil)\n"
       "            return try await authService.reauthenticate(body)"),),
     test_the_confirm_step_never_signs_in, "signInWithProvider"),
    ("reauth-error-untyped", _APP_ERROR,
     (('if code == "AUTH_REAUTH_REQUIRED" {', 'if code == "AUTH_REAUTH_REQUIRED_X" {'),),
     test_reauth_required_is_a_typed_non_auth_error, "no longer maps to .validationFailed"),
    ("logout-body-dropped", _ENDPOINT,
     (("return refreshToken.map { LogoutRequest(refreshToken: $0) }", "return nil as LogoutRequest?"),),
     test_sign_out_carries_the_refresh_token_and_the_new_cases_are_sign_in_required,
     "no longer sends its refresh token"),
]


@pytest.mark.parametrize("name,path,edits,guard,message", _MUTATIONS, ids=[m[0] for m in _MUTATIONS])
def test_each_mutation_is_killed(monkeypatch, name, path, edits, guard, message):
    """Patched in memory only — other sessions read these Swift files concurrently."""
    real_read_text = pathlib.Path.read_text
    original = real_read_text(path, encoding="utf-8")
    mutated = original
    for old, new in edits:
        assert mutated.count(old) == 1, (
            f"{name}: mutation anchor {old[:70]!r} occurs {mutated.count(old)}x in {path.name} — "
            "re-derive this mutation against the new source rather than deleting it")
        mutated = mutated.replace(old, new, 1)

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == path:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    with pytest.raises(AssertionError, match=re.escape(message)):
        guard()


def test_every_guard_has_a_mutation():
    covered = {m[3] for m in _MUTATIONS}
    guards = [v for k, v in globals().items() if k.startswith("test_") and callable(v)
              and k not in ("test_each_mutation_is_killed", "test_every_guard_has_a_mutation",
                            "test_comments_cannot_satisfy_a_guard")]
    missing = [g.__name__ for g in guards if g not in covered]
    # Two guards are pinned by an ENDPOINT-shape assertion inside another guard's mutation set.
    allowed_without = {"test_sign_out_all_devices_uses_the_armed_client"}
    assert set(missing) <= allowed_without, f"guards with no mutation: {missing}"


def test_comments_cannot_satisfy_a_guard():
    """The comment beside the Delete Forever button quotes the old flow; a scan that read prose
    would pass after the code was reverted."""
    raw = _SETTINGS.read_text(encoding="utf-8")
    assert "Not the deletion itself" in raw  # the explanatory comment exists...
    assert "Not the deletion itself" not in _code(_SETTINGS)  # ...and is stripped before scanning
