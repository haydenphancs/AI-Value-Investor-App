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

Backend tests cover (1)'s server half directly; the iOS halves are pinned by source parity,
which is the only mechanism available (there is no iOS test target).
"""
from __future__ import annotations

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


def test_signout_detaches_the_device_before_the_session_ends():
    src = _read("Core/Services/AuthService.swift")
    assert "unregisterCurrentDevice()" in src, (
        "sign-out never detaches the APNs token — the device keeps getting the old account's alerts"
    )
    body = src[src.index("func signOut()"):]
    body = body[: body.index("\n    }")]
    # The boundary that matters is `setAuthToken(nil)`, NOT `clearToken()`: clearToken only
    # deletes the Keychain copy, while the request is authenticated by the in-memory APIClient
    # token. De-registration must therefore precede the setAuthToken(nil) disarm — running
    # after it would send an unauthenticated DELETE and 401.
    assert body.index("unregisterCurrentDevice") < body.index("setAuthToken(nil)"), (
        "de-registration runs after the APIClient token is disarmed — it would 401"
    )


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
    """A force-quit during the ~30s logout timeout must not leave the tokens on disk."""
    src = _read("Core/Services/AuthService.swift")
    body = src[src.index("func signOut()"):]
    body = body[: body.index("\n    }")]
    assert body.index("clearToken()") < body.index("endpoint: .signOut"), (
        "the durable token clear still sits after the network round-trip"
    )


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
