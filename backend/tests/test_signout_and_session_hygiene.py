"""Sign-out actually ends the session, on every surface it touched.

Four confirmed defects, all of which let one account's state or notifications reach the NEXT
person to use the device:

1. **Device token stayed bound.** `device_tokens.token` is UNIQUE and the binding only moves
   when a NEW registration re-binds it — but a signed-out client has no session, so it never
   registers again. The row kept pointing at the previous account, and the insight sweeper kept
   delivering ITS watchlist alerts to this phone: a device showing the signed-out guest UI,
   buzzing with someone else's tickers, which also discloses what they follow.

2. **Synced preferences survived.** The keys in `SettingsSyncManager` carry no user id and
   `hydrate()` only overwrites keys the server returns, so user B inherited A's default persona,
   playback speed, appearance, and notification opt-ins — then `push()` wrote them up as B's own.

3. **The Keychain was cleared only AFTER the logout round-trip.** On a dead connection that
   awaits ~30s; a force-quit in that window left both tokens on disk, and the next launch
   silently signed the "signed-out" user back in.

4. **A transient launch failure left the client token armed** while the UI rendered a guest —
   so the app showed "Sign In" while every request still authenticated as the real account.

5. **Sign-out disarmed the client LAST** (2026-10-13). `AppState.signOut()` shows the signed-out
   UI and bumps the identity generation synchronously, so every screen reloads at once — but the
   client token was cleared only inside a Task, after awaiting the device detach and
   `POST /auth/logout`. Those reloads went out with the ENDED account's token, so its reports
   list landed in the signed-out UI, and `/auth/logout` only logs, so the token stayed valid
   server-side. Now `AppState.signOut()` first calls `AuthService.beginSignOut()` — synchronous:
   epoch, Keychain, and `APIClient.disarmAuthToken()`, a nonisolated swap under the token's
   `Mutex` — and only then moves the identity; `finishSignOut(_:)` sends the two calls with the
   captured token passed explicitly (`APIClient.requestAuthenticated(as:)`), never re-arming.

Backend tests cover (1)'s server half directly; the iOS halves are pinned by source parity,
which is the only mechanism available (there is no iOS test target). The (5) guards strip
comments, brace-bound every scan, and are mutation-tested in memory on every pass
(``pathlib.Path.read_text`` patched for one file — the Swift files are never rewritten).
"""
from __future__ import annotations

import pathlib
import re
from pathlib import Path

import pytest

from app.services.user_settings_service import UserSettingsService

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_USER_A = "aaaa1111-2222-4333-8444-555555555555"
_USER_B = "bbbb2222-3333-4444-8555-666666666666"


def _read(rel: str) -> str:
    p = _IOS / rel
    assert p.exists(), f"expected to exist: {p}"
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Server-side de-registration
# ---------------------------------------------------------------------------


class _Table:
    def __init__(self, store, log):
        self.store, self.log = store, log
        self._op = None
        self._filters = {}

    def delete(self):
        self._op = "delete"
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def execute(self):
        if self._op == "delete":
            before = len(self.store)
            self.store[:] = [
                r for r in self.store
                if not all(r.get(k) == v for k, v in self._filters.items())
            ]
            self.log.append(("delete", before - len(self.store), dict(self._filters)))
        return type("R", (), {"data": []})()


class _SB:
    def __init__(self, store):
        self.store, self.log = store, []

    def table(self, _name):
        return _Table(self.store, self.log)


def _svc(store):
    svc = UserSettingsService.__new__(UserSettingsService)
    svc.supabase = _SB(store)
    return svc


def test_unregister_removes_this_users_binding():
    store = [{"token": "TOKEN-A", "user_id": _USER_A}]
    svc = _svc(store)
    assert svc.unregister_device(user_id=_USER_A, token="TOKEN-A") is True
    assert store == [], "the token→user binding survived sign-out"


def test_unregister_cannot_detach_a_token_that_rebound_to_someone_else():
    """A stale client must not be able to unbind a token now owned by another account —
    that would silently disable push for whoever is currently signed in."""
    store = [{"token": "TOKEN-A", "user_id": _USER_B}]
    svc = _svc(store)
    svc.unregister_device(user_id=_USER_A, token="TOKEN-A")
    assert store == [{"token": "TOKEN-A", "user_id": _USER_B}], "detached another user's token"


def test_unregister_is_scoped_by_both_token_and_user():
    store = [
        {"token": "TOKEN-A", "user_id": _USER_A},
        {"token": "TOKEN-B", "user_id": _USER_A},
    ]
    svc = _svc(store)
    svc.unregister_device(user_id=_USER_A, token="TOKEN-A")
    assert store == [{"token": "TOKEN-B", "user_id": _USER_A}], "detached the wrong device"


@pytest.mark.parametrize("token", ["", "   ", None])
def test_empty_token_is_refused_rather_than_deleting_everything(token):
    """A blank token with only `user_id` in the filter would delete every device the user owns."""
    store = [{"token": "TOKEN-A", "user_id": _USER_A}]
    svc = _svc(store)
    assert svc.unregister_device(user_id=_USER_A, token=token) is False
    assert store, "a blank token wiped the user's device rows"


def test_a_delete_failure_is_reported_not_raised():
    """Sign-out must not fail because de-registration did."""
    class _Boom:
        def table(self, _n):
            raise RuntimeError("supabase down")

    svc = UserSettingsService.__new__(UserSettingsService)
    svc.supabase = _Boom()
    assert svc.unregister_device(user_id=_USER_A, token="TOKEN-A") is False


# ---------------------------------------------------------------------------
# 2-4. The iOS halves (source parity — no iOS test target exists)
# ---------------------------------------------------------------------------


_APP_STATE = _IOS / "Core" / "State" / "AppState.swift"
_AUTH_SERVICE = _IOS / "Core" / "Services" / "AuthService.swift"
_CLIENT = _IOS / "Core" / "Services" / "APIClient.swift"
_PUSH = _IOS / "Core" / "Services" / "PushNotificationManager.swift"
_REPO_SWIFT = _IOS / "Core" / "Repositories" / "AccountRepository.swift"

_APP_SIGN_OUT = "func signOut() {"
_BEGIN = "func beginSignOut() -> SignOutHandoff"
_FINISH = "func finishSignOut(_ handoff: SignOutHandoff) async"
_DISARM_DECL = "nonisolated func disarmAuthToken() -> String?"
_SLOT_DECL = "nonisolated private let armedToken = Mutex<String?>(nil)"
_TOKEN_PROP = "private var authToken: String? {"
_SEND_DECL = "private func sendAuthenticated(as token: String, endpoint: APIEndpoint) async throws -> Data"
_BUILD_DECL = "private func buildRequest(for endpoint: APIEndpoint"
_UNREGISTER_DECL = "func unregisterCurrentDevice(authenticatedAs sessionToken: String) async"
_REPO_UNREGISTER_DECL = "func unregisterDevice(token: String, authenticatedAs sessionToken: String) async throws -> Bool {"

_BEGIN_CALL = re.compile(r"let\s+(\w+)\s*=\s*authService\??\.beginSignOut\(\)")
_AWAIT = re.compile(r"\bawait\b")
# Every ARMED transport: anything sent through one of these carries whatever the client holds.
_ARMED_TRANSPORT = re.compile(
    r"\bapiClient\.(?:request|requestReturningBody|downloadData|stream)\s*[(<]")
# What `AppState.signOut()` changes. Every one must come AFTER the synchronous disarm: each
# moves the identity (or the state screens reload from) while the client still holds the token.
_IDENTITY_MOVES = ("invalidateIdentity(", "resolveIdentity(", "auth.status", "credentialGeneration",
                   "user = ", "watchlist = ", "research = ", "discardDataForEndedSession(")


def _strip_swift_comments(src: str) -> str:
    """Drop block comments, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the sign-out comments name `setAuthToken(nil)`, `apiClient.request(.signOut)` and
    the old ordering while explaining the bug, so an un-stripped scan passes on prose after the
    code is reverted. A tail needs leading whitespace, so a `https://` in a string survives.
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


def _span(src: str, header: str) -> tuple[int, int]:
    """`[start, end)` of the brace-balanced body after the ONLY occurrence of `header`."""
    assert src.count(header) == 1, f"expected exactly one `{header}`, found {src.count(header)}"
    start = src.index("{", src.index(header) + len(header) - 1)
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return start, i + 1
    raise AssertionError(f"unbalanced braces after `{header}`")


def _block(src: str, header: str) -> str:
    start, end = _span(src, header)
    return src[start:end]


def _line_of(src: str, at: int) -> str:
    return src[src.rfind("\n", 0, at) + 1: src.find("\n", at)].strip()


def _app_sign_out() -> str:
    body = _block(_code(_APP_STATE), _APP_SIGN_OUT)
    assert "invalidateIdentity(nil)" in body and "auth.status = .unauthenticated" in body, (
        "not AppState.signOut — this scan is bound to the wrong declaration")
    return body


def _begin() -> str:
    code = _code(_AUTH_SERVICE)
    assert re.search(re.escape(_BEGIN) + r"\s*\{", code), (
        "AuthService.beginSignOut is gone or became async — sign-out's local half can no longer "
        "run before AppState.signOut changes the identity")
    return _block(code, _BEGIN)


def _finish() -> str:
    body = _block(_code(_AUTH_SERVICE), _FINISH)
    assert "PushNotificationManager" in body, "not AuthService.finishSignOut — re-derive this scan"
    return body


def test_app_signout_disarms_synchronously_before_the_identity_moves():
    body = _app_sign_out()
    m = _BEGIN_CALL.search(body)
    assert m, (
        "AppState.signOut no longer disarms synchronously (`let <h> = authService.beginSignOut()`) "
        "— the reloads its identity change sets off are built with the ended account's token")
    before = body[: m.start()]
    for token in ("Task", "await", "async let") + _IDENTITY_MOVES:
        assert token not in before, (
            f"AppState.signOut reaches `{token}` before `beginSignOut()` — the identity moves, or "
            "work starts, while the client still holds the ended account's token")
    assert re.search(rf"Task\s*\{{\s*await\s+authService\??\.finishSignOut\(\s*{m.group(1)}\s*\)\s*\}}",
                     body[m.end():]), (
        "AppState.signOut no longer hands the sign-out handoff to finishSignOut — the device "
        "detach and the backend logout never run, or run without the ended token")


def test_begin_signout_is_synchronous_and_disarms_the_client():
    body = _begin()
    for token in ("await", "Task"):
        assert token not in body, (
            f"AuthService.beginSignOut uses `{token}` — sign-out's local half suspends, so the "
            "identity can move while the client still holds the ended token")
    assert "sessionEpoch += 1" in body, (
        "AuthService.beginSignOut no longer ends the session epoch — a refresh in flight keeps "
        "its result and re-arms the ended session")
    m = re.search(r"let\s+(\w+)\s*=\s*apiClient\.disarmAuthToken\(\)", body)
    assert m, "AuthService.beginSignOut no longer disarms the client"
    assert re.search(rf"return\s+SignOutHandoff\(endedToken:\s*{m.group(1)},\s*epoch:\s*sessionEpoch\)",
                     body), (
        "AuthService.beginSignOut no longer hands the ended token and the epoch to finishSignOut")


def test_disarm_swaps_the_token_out_synchronously():
    client = _code(_CLIENT)
    assert re.search(re.escape(_DISARM_DECL) + r"\s*\{", client), (
        "APIClient.disarmAuthToken is no longer nonisolated and synchronous — sign-out cannot "
        "disarm before AppState.signOut changes the identity")
    body = _block(client, _DISARM_DECL)
    assert not _AWAIT.search(body), (
        "APIClient.disarmAuthToken is no longer nonisolated and synchronous — sign-out cannot "
        "disarm before AppState.signOut changes the identity")
    assert re.search(r"armedToken\.withLock\s*\{\s*(\w+)\s+in\s*let\s+(\w+)\s*=\s*\1\s*\n"
                     r"\s*\1\s*=\s*nil\s*\n\s*return\s+\2\s*\}", body), (
        "APIClient.disarmAuthToken no longer swaps the token out under the lock (read it, clear "
        "it to nil, return what it read — in one withLock)")


def test_the_token_lives_behind_one_lock_only_the_accessors_and_disarm_touch():
    client = _code(_CLIENT)
    lock_msg = ("authToken is no longer kept behind the armedToken lock — sign-out's synchronous "
                "disarm would not reach the token the transports read")
    assert client.count(_SLOT_DECL) == 1, lock_msg
    prop = _block(client, _TOKEN_PROP) if _TOKEN_PROP in client else ""
    assert re.search(r"get\s*\{\s*armedToken\.withLock\s*\{\s*\$0\s*\}\s*\}", prop), lock_msg
    assert re.search(r"set\s*\{\s*armedToken\.withLock\s*\{\s*\$0\s*=\s*newValue\s*\}\s*\}", prop), lock_msg
    allowed = [_span(client, _TOKEN_PROP), _span(client, _DISARM_DECL)]
    decl_at = client.index(_SLOT_DECL) + len("nonisolated private let ")
    stray = [_line_of(client, m.start()) for m in re.finditer(r"\barmedToken\b", client)
             if m.start() != decl_at and not any(a <= m.start() < b for a, b in allowed)]
    assert not stray, (
        f"armedToken is touched outside the authToken accessors and disarmAuthToken: {stray} — "
        "a second path writes the token without the actor's or sign-out's rules")


def test_finish_signout_never_rearms_the_client_or_uses_an_armed_transport():
    body = _finish()
    rearm = [c for c in re.findall(r"setAuthToken\(([^)]*)\)", body) if c.strip() != "nil"]
    assert not rearm, (
        f"AuthService.finishSignOut re-arms the client (`setAuthToken({rearm[0] if rearm else ''})`) "
        "— the reloads the identity change set off go out as the ended account again")
    hit = _ARMED_TRANSPORT.search(body)
    assert not hit, (
        f"AuthService.finishSignOut sends through an armed transport (`{hit.group(0) if hit else ''}`) "
        "— the client is disarmed by then, so it is refused, or carries a newer session's token")


def test_finish_signout_sends_the_detach_and_logout_with_the_ended_token():
    body = _finish()
    m = re.search(r"if\s+let\s+(\w+)\s*=\s*handoff\.endedToken\s*\{", body)
    assert m, (
        "AuthService.finishSignOut no longer takes the ended token from the handoff — the detach "
        "and the logout cannot present it")
    token, after = m.group(1), body[m.end():]
    assert re.search(
        rf"PushNotificationManager\.shared\.unregisterCurrentDevice\(authenticatedAs:\s*{token}\)", after), (
        "sign-out no longer detaches the APNs token with the ended session's token — the device "
        "keeps getting the old account's alerts (or the DELETE goes out unauthenticated and 401s)")
    assert re.search(rf"apiClient\.requestAuthenticated\(as:\s*{token},\s*endpoint:\s*\.signOut\)", after), (
        "sign-out no longer sends POST /auth/logout with the ended session's token")

    push = _block(_code(_PUSH), _UNREGISTER_DECL)
    assert re.search(r"repository\.unregisterDevice\(token:\s*\w+,\s*authenticatedAs:\s*sessionToken\)", push), (
        "PushNotificationManager.unregisterCurrentDevice no longer hands the ended session's token "
        "to the repository")
    repo = _block(_code(_REPO_SWIFT), _REPO_UNREGISTER_DECL)
    assert re.search(r"apiClient\.requestAuthenticated\(\s*as:\s*sessionToken,\s*endpoint:\s*\.unregisterDevice", repo), (
        "AccountRepository.unregisterDevice no longer sends the DELETE with the explicit token — "
        "through the armed client it is refused after sign-out's disarm")
    assert not _ARMED_TRANSPORT.search(repo), (
        "AccountRepository.unregisterDevice no longer sends the DELETE with the explicit token — "
        "through the armed client it is refused after sign-out's disarm")


def test_the_explicit_token_transport_never_touches_the_shared_session():
    client = _code(_CLIENT)
    send = _block(client, _SEND_DECL)
    assert "buildRequest(for: endpoint, credential: .explicit(token))" in send, (
        "sendAuthenticated no longer builds through buildRequest with its explicit credential")
    for banned, what in (
        ("refreshArmedTokenIfExpired", "runs the pre-flight refresh (re-arms the client)"),
        ("refreshTokenSingleFlight", "refreshes the shared session (re-arms the client)"),
        ("handleUnrecoverableAuthFailure", "disarms whatever is armed — a newer session's token"),
        ("authFailureHandler", "reports an auth failure for a session that already ended"),
        ("setAuthToken", "arms the client"),
        ("authToken", "reads or writes the armed token"),
        ("attemptFailover", "re-sends through the failover"),
        ("Task.sleep", "retries"),
    ):
        assert banned not in send, f"the explicit-token transport {what} (`{banned}`)"

    build = _block(client, _BUILD_DECL)
    switch = _block(build, "switch credential")
    explicit = switch[switch.index("case .explicit(let token):"):]
    assert "authToken" not in explicit, (
        "buildRequest's .explicit credential falls back to the armed token — sign-out's last calls "
        "would carry whatever the client holds, a newer session's token included")
    assert re.search(r"if\s+let\s+token\s*=\s*bearer\s*\{", build), (
        "buildRequest no longer sets the Authorization header from the chosen bearer")


def test_finish_signout_keeps_the_rearm_backstop():
    body = _finish()
    after = body[body.index("requestAuthenticated(as:"):] if "requestAuthenticated(as:" in body else ""
    assert re.search(r"guard\s+handoff\.epoch\s*==\s*sessionEpoch\s+else\s*\{\s*return\s*\}\s*"
                     r"await\s+apiClient\.setAuthToken\(nil\)", after), (
        "sign-out lost its trailing epoch-guarded setAuthToken(nil) backstop — a token refresh in "
        "flight when sign-out began can land after the disarm and re-arm the ended session")


def test_the_registered_token_is_remembered_so_it_can_be_detached():
    src = _read("Core/Services/PushNotificationManager.swift")
    assert "registeredToken" in src, (
        "nothing records the CONFIRMED token; pendingToken is cleared on success, so at "
        "sign-out there is no token to unregister"
    )


def test_signout_clears_the_synced_preferences():
    assert "clearLocalForEndedSession" in _read("Core/State/AppState.swift"), (
        "sign-out leaves the previous account's persona / speed / appearance / notification "
        "opt-ins in UserDefaults for the next user to inherit"
    )
    sync = _read("Core/Services/SettingsSyncManager.swift")
    assert "func clearLocalForEndedSession" in sync
    for key_list in ("boolKeys", "stringKeys", "doubleKeys"):
        assert key_list in sync.split("func clearLocalForEndedSession")[1][:600], (
            f"clearLocalForEndedSession does not clear {key_list}"
        )


def test_keychain_is_cleared_before_the_awaited_logout_call():
    """A force-quit during the ~30s logout timeout must not leave the tokens on disk. The clear is
    in the synchronous half, which `AppState.signOut()` runs before it creates the Task that sends
    the logout (the ordering half is pinned by the AppState guard above)."""
    begin, finish = _begin(), _finish()
    assert "endpoint: .signOut" in finish, "sign-out no longer sends the backend logout — re-derive"
    assert "clearToken()" in begin, "the durable token clear still sits after the network round-trip"


def test_transient_launch_failure_disarms_the_client_token():
    """The UI renders a guest; the wire identity must agree, or writes land on the real account."""
    src = _read("Core/State/AppState.swift")
    # `restoreAuthState()` became `performRestore(trigger:)` when restore stopped being a
    # once-per-launch call and became re-runnable (foreground / network-restore / backoff).
    # The invariant under test is unchanged.
    restore = src[src.index("private func performRestore("):]
    restore = restore[: restore.index("\n    /// Fetch")]
    assert restore.count("setAuthToken(nil)") >= 2, (
        "a transient profile-fetch failure still leaves the Bearer token armed while the app "
        "presents the signed-out UI"
    )
    # And it must now RETRY rather than strand the session for the rest of the run — the
    # original complaint: a valid stored credential sitting unused for a whole app run.
    assert "scheduleRestoreBackoff()" in restore, (
        "a transient failure no longer schedules a retry, so a token-holding user is stranded "
        "as a guest until the next cold launch"
    )


# ---------------------------------------------------------------------------
# 5. Mutations — each (5) guard must go red on the regression it names, with its message
# ---------------------------------------------------------------------------

_I8 = " " * 8
_I12 = " " * 12
_I16 = " " * 16
# AppState.signOut
_A_BLOCK = (f"{_I8}if let authService {{\n"
            f"{_I12}let handoff = authService.beginSignOut()\n"
            f"{_I12}Task {{ await authService.finishSignOut(handoff) }}\n"
            f"{_I8}}}\n")
# AuthService
_B_HEAD = "    func beginSignOut() -> SignOutHandoff {\n"
_B_EPOCH = f"{_B_HEAD}{_I8}sessionEpoch += 1\n"
_B_CLEAR = f"{_I8}clearToken()\n"
_B_DISARM = f"{_I8}let endedToken = apiClient.disarmAuthToken()\n"
_B_RETURN = "return SignOutHandoff(endedToken: endedToken, epoch: sessionEpoch)"
_F_TAKE = f"{_I8}if let endedToken = handoff.endedToken {{\n"
_F_DETACH = f"{_I12}await PushNotificationManager.shared.unregisterCurrentDevice(authenticatedAs: endedToken)\n"
_F_LOGOUT = f"{_I16}try await apiClient.requestAuthenticated(as: endedToken, endpoint: .signOut)\n"
_F_GUARD = f"{_I8}guard handoff.epoch == sessionEpoch else {{ return }}\n"
_F_BACKSTOP = f"{_F_GUARD}{_I8}await apiClient.setAuthToken(nil)\n"
# APIClient
_C_DISARM_HEAD = "    nonisolated func disarmAuthToken() -> String? {\n"
_C_DISARM_CLEAR = f"{_I12}token = nil\n"
_C_PROP = ("    private var authToken: String? {\n"
           f"{_I8}get {{ armedToken.withLock {{ $0 }} }}\n"
           f"{_I8}set {{ armedToken.withLock {{ $0 = newValue }} }}\n"
           "    }\n")
_C_SETTER = f"{_I8}self.authToken = token\n"
_C_SEND_BUILD = f"{_I8}let request = try buildRequest(for: endpoint, credential: .explicit(token))\n"
_C_EXPLICIT_ARM = f"{_I12}bearer = token.isEmpty ? nil : token\n"
# PushNotificationManager / AccountRepository
_P_CALL = "repository.unregisterDevice(token: token, authenticatedAs: sessionToken)"
_R_CALL = (f"try await apiClient.requestAuthenticated(\n{_I12}as: sessionToken,\n"
           f"{_I12}endpoint: .unregisterDevice(token: token),\n")

_T_APP = test_app_signout_disarms_synchronously_before_the_identity_moves
_T_BEGIN = test_begin_signout_is_synchronous_and_disarms_the_client
_T_DISARM = test_disarm_swaps_the_token_out_synchronously
_T_SLOT = test_the_token_lives_behind_one_lock_only_the_accessors_and_disarm_touch
_T_REARM = test_finish_signout_never_rearms_the_client_or_uses_an_armed_transport
_T_EXPLICIT = test_finish_signout_sends_the_detach_and_logout_with_the_ended_token
_T_TRANSPORT = test_the_explicit_token_transport_never_touches_the_shared_session
_T_BACKSTOP = test_finish_signout_keeps_the_rearm_backstop
_T_KEYCHAIN = test_keychain_is_cleared_before_the_awaited_logout_call

# (name, file, ((old, new), ...), guard, message the guard must fail with)
_MUTATIONS = [
    # The reported shape: the whole sign-out deferred into a Task, behind the identity change.
    ("begin-inside-task", _APP_STATE,
     ((_A_BLOCK, f"{_I8}Task {{ [authService] in\n{_I12}guard let authService else {{ return }}\n"
                 f"{_I12}let handoff = authService.beginSignOut()\n"
                 f"{_I12}await authService.finishSignOut(handoff)\n{_I8}}}\n"),),
     _T_APP, "AppState.signOut reaches `Task` before `beginSignOut()`"),
    ("identity-before-begin", _APP_STATE, ((_A_BLOCK, f"{_I8}invalidateIdentity(nil)\n{_A_BLOCK}"),),
     _T_APP, "AppState.signOut reaches `invalidateIdentity(` before `beginSignOut()`"),
    ("status-before-begin", _APP_STATE, ((_A_BLOCK, f"{_I8}auth.status = .unauthenticated\n{_A_BLOCK}"),),
     _T_APP, "AppState.signOut reaches `auth.status` before `beginSignOut()`"),
    ("no-begin", _APP_STATE,
     ((f"{_I12}let handoff = authService.beginSignOut()\n",
       f"{_I12}let handoff = AuthService.SignOutHandoff(endedToken: nil, epoch: 0)\n"),),
     _T_APP, "AppState.signOut no longer disarms synchronously"),
    ("handoff-dropped", _APP_STATE,
     ((f"{_I12}Task {{ await authService.finishSignOut(handoff) }}\n", f"{_I12}_ = handoff\n"),),
     _T_APP, "AppState.signOut no longer hands the sign-out handoff to finishSignOut"),
    ("begin-becomes-async", _AUTH_SERVICE,
     ((_B_HEAD, "    func beginSignOut() async -> SignOutHandoff {\n"),
      (_B_DISARM, f"{_I8}let endedToken = await apiClient.currentAuthToken()\n")),
     _T_BEGIN, "AuthService.beginSignOut is gone or became async"),
    ("begin-suspends", _AUTH_SERVICE, ((_B_DISARM, f"{_I8}await Task.yield()\n{_B_DISARM}"),),
     _T_BEGIN, "AuthService.beginSignOut uses `await`"),
    ("begin-epoch-not-bumped", _AUTH_SERVICE, ((_B_EPOCH, _B_HEAD),),
     _T_BEGIN, "AuthService.beginSignOut no longer ends the session epoch"),
    ("begin-skips-disarm", _AUTH_SERVICE, ((_B_DISARM, f"{_I8}let endedToken: String? = nil\n"),),
     _T_BEGIN, "AuthService.beginSignOut no longer disarms the client"),
    ("begin-drops-the-token", _AUTH_SERVICE,
     ((_B_RETURN, "return SignOutHandoff(endedToken: nil, epoch: sessionEpoch)"),),
     _T_BEGIN, "no longer hands the ended token and the epoch to finishSignOut"),
    ("disarm-not-nonisolated", _CLIENT, ((_C_DISARM_HEAD, "    func disarmAuthToken() -> String? {\n"),),
     _T_DISARM, "APIClient.disarmAuthToken is no longer nonisolated and synchronous"),
    ("disarm-keeps-token", _CLIENT, ((_C_DISARM_CLEAR, f"{_I12}token = endedToken\n"),),
     _T_DISARM, "no longer swaps the token out under the lock"),
    ("token-off-the-lock", _CLIENT, ((_C_PROP, "    private var authToken: String?\n"),),
     _T_SLOT, "authToken is no longer kept behind the armedToken lock"),
    ("second-slot-writer", _CLIENT, ((_C_SETTER, f"{_I8}armedToken.withLock {{ $0 = token }}\n"),),
     _T_SLOT, "armedToken is touched outside the authToken accessors and disarmAuthToken"),
    ("rearm-to-send-logout", _AUTH_SERVICE,
     ((_F_LOGOUT, f"{_I16}await apiClient.setAuthToken(endedToken)\n{_F_LOGOUT}"),),
     _T_REARM, "AuthService.finishSignOut re-arms the client"),
    ("logout-through-armed-transport", _AUTH_SERVICE,
     ((_F_LOGOUT, f"{_I16}try await apiClient.request(endpoint: .signOut)\n"),),
     _T_REARM, "AuthService.finishSignOut sends through an armed transport"),
    ("finish-ignores-the-handoff", _AUTH_SERVICE,
     ((_F_TAKE, f"{_I8}if let endedToken = await apiClient.currentAuthToken() {{\n"),),
     _T_EXPLICIT, "AuthService.finishSignOut no longer takes the ended token from the handoff"),
    ("detach-without-ended-token", _AUTH_SERVICE,
     ((_F_DETACH, f"{_I12}await PushNotificationManager.shared.unregisterCurrentDevice(authenticatedAs: \"\")\n"),),
     _T_EXPLICIT, "sign-out no longer detaches the APNs token with the ended session's token"),
    ("logout-dropped", _AUTH_SERVICE, ((_F_LOGOUT, f"{_I16}_ = endedToken\n"),),
     _T_EXPLICIT, "sign-out no longer sends POST /auth/logout with the ended session's token"),
    ("push-drops-the-token", _PUSH,
     ((_P_CALL, "repository.unregisterDevice(token: token, authenticatedAs: \"\")"),),
     _T_EXPLICIT, "no longer hands the ended session's token to the repository"),
    ("repo-through-armed-client", _REPO_SWIFT,
     ((_R_CALL, f"try await apiClient.request(\n{_I12}endpoint: .unregisterDevice(token: token),\n"),),
     _T_EXPLICIT, "AccountRepository.unregisterDevice no longer sends the DELETE with the explicit token"),
    ("explicit-transport-preflights", _CLIENT,
     ((_C_SEND_BUILD, f"{_I8}await refreshArmedTokenIfExpired(for: endpoint)\n{_C_SEND_BUILD}"),),
     _T_TRANSPORT, "runs the pre-flight refresh"),
    ("explicit-transport-ends-session-on-401", _CLIENT,
     ((_C_SEND_BUILD, f"{_C_SEND_BUILD}{_I8}_ = await handleUnrecoverableAuthFailure(.unauthorized, endpoint: endpoint)\n"),),
     _T_TRANSPORT, "disarms whatever is armed"),
    ("explicit-falls-back-to-armed", _CLIENT,
     ((_C_EXPLICIT_ARM, f"{_I12}bearer = token.isEmpty ? authToken : token\n"),),
     _T_TRANSPORT, "falls back to the armed token"),
    ("backstop-removed", _AUTH_SERVICE, ((_F_BACKSTOP, ""),),
     _T_BACKSTOP, "sign-out lost its trailing epoch-guarded setAuthToken(nil) backstop"),
    ("backstop-unguarded", _AUTH_SERVICE, ((_F_GUARD, ""),),
     _T_BACKSTOP, "sign-out lost its trailing epoch-guarded setAuthToken(nil) backstop"),
    ("keychain-after-logout", _AUTH_SERVICE,
     ((_B_CLEAR, ""), (_F_BACKSTOP, f"{_I8}clearToken()\n{_F_BACKSTOP}")),
     _T_KEYCHAIN, "the durable token clear still sits after the network round-trip"),
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
    assert mutated != original

    def fake_read_text(self, *args, **kwargs):
        if pathlib.Path(self) == path:
            return mutated
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", fake_read_text)
    with pytest.raises(AssertionError, match=re.escape(message)):
        guard()


def test_the_mutation_table_covers_every_signout_guard():
    covered = {m[3] for m in _MUTATIONS}
    for guard in (_T_APP, _T_BEGIN, _T_DISARM, _T_SLOT, _T_REARM, _T_EXPLICIT, _T_TRANSPORT,
                  _T_BACKSTOP, _T_KEYCHAIN):
        assert guard in covered, f"{guard.__name__} has no mutation in _MUTATIONS"


def test_comment_stripping_is_load_bearing_for_the_signout_scan():
    """The sign-out comments describe the OLD order in prose (`await apiClient.request(.signOut)`
    before the Keychain clear); an unstripped scan would read them as code."""
    raw = _AUTH_SERVICE.read_text(encoding="utf-8")
    raw_body = raw[raw.index(_BEGIN):]
    raw_body = raw_body[: raw_body.index("\n    }\n")]
    assert "apiClient.request(.signOut)" in raw_body, (
        "the sign-out comment no longer quotes the old call — fine, but this anti-vacuity probe "
        "then needs a new raw sample")
    assert "apiClient.request(.signOut)" not in _begin()
