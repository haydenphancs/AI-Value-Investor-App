"""App Lock must sit in its own `UIWindow`, not in the SwiftUI view tree.

It used to be `.overlay { if appLock.isLocked { AppLockView().zIndex(1000) } }` on the root view
in `iosApp.swift`. That cannot work, and `zIndex` cannot rescue it: a `.fullScreenCover` /
`.sheet` is a separate presented `UIViewController` drawn ABOVE the entire root hierarchy, while
`zIndex` only orders siblings WITHIN one hierarchy.

So the lock rendered BEHIND every modal in the app — Account, ticker detail, Cay AI chat, Buy
Credits, Sign In. Background the app with any of them open, come back, and the content was
sitting there fully readable: an account screen with an email address and a credit balance, or a
chat transcript with the user's holdings pasted into it. A privacy control that fails open in
the ordinary case.

A window at `.alert + 1` is above every modal presentation unconditionally, needs no cooperation
from any screen, and covers presentations that do not exist yet.

⚠️ Guard discipline (`project_source_scan_guard_vacuity`): comments blanked, windows brace-
bounded. Mutation-tested at the bottom.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_IOS = Path(__file__).resolve().parents[2] / "frontend" / "ios" / "ios"
_MANAGER = _IOS / "Core" / "Services" / "AppLockManager.swift"
_APP = _IOS / "iosApp.swift"


def _code_only(src: str) -> str:
    return "\n".join(
        "" if line.strip().startswith("//") else line for line in src.splitlines()
    )


def _read(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"{path} not present")
    return _code_only(path.read_text(encoding="utf-8"))


def test_the_lock_is_hosted_in_its_own_window():
    src = _read(_MANAGER)
    assert "UIWindow(windowScene:" in src, (
        "AppLockManager does not create its own window; an in-tree overlay is drawn behind "
        "every sheet and fullScreenCover in the app"
    )
    assert "windowLevel" in src, "the lock window never sets a level, so it is not above modals"
    assert ".alert" in src, (
        "the lock window is not raised above `.alert` level — a system alert or any modal "
        "would cover it"
    )
    assert "AppLockView()" in src, "the window hosts no lock view"


def test_the_root_view_no_longer_renders_the_lock():
    """Both halves matter: a leftover in-tree copy would render behind modals AND double up
    with the window."""
    src = _read(_APP)
    assert "AppLockView()" not in src, (
        "iosApp.swift still instantiates AppLockView in the view tree. That copy is drawn "
        "behind every modal — the exact defect the window exists to fix."
    )


def test_the_window_is_reconciled_from_the_app_lifecycle():
    """`AppLockManager.init` runs before any scene exists, so it CANNOT raise the window. If
    nothing calls the reconciler from the lifecycle, a cold launch into a locked app shows the
    content unlocked."""
    app = _read(_APP)
    assert app.count("AppLockManager.shared.syncLockWindow()") >= 2, (
        "syncLockWindow() must run on BOTH launch and didBecomeActive — launch covers a cold "
        "start into a locked app, didBecomeActive covers a scene that reconnected while "
        "suspended (when didEnterBackground has already set isLocked)"
    )


def test_locking_state_has_a_single_writer():
    """The flag and the window must not be able to disagree."""
    src = _read(_MANAGER)
    assert "private(set) var isLocked" in src, (
        "isLocked is publicly settable, so a caller can lock the app without the window "
        "following — the flag would say locked while the content stays on screen"
    )
    assert "private func setLocked(" in src, "no single writer that also reconciles the window"


def test_unlocking_hands_key_status_back():
    """Dropping the window without handing `key` back leaves some scene configurations with no
    key window, which silently breaks keyboard input on the screen the user returns to."""
    src = _read(_MANAGER)
    assert "makeKey()" in src, "the lock window never returns key status on dismiss"


# ── anti-vacuity ─────────────────────────────────────────────────────────────

def test_the_guards_would_fail_on_the_old_code():
    manager = _read(_MANAGER)
    app = _read(_APP)
    # If AppLockView appeared in NEITHER file the "not in iosApp" assertion would be
    # vacuously true, so pin that it lives in exactly one place — the manager.
    assert "AppLockView()" in manager, (
        "AppLockView is instantiated nowhere, so `not in iosApp.swift` proves nothing"
    )
    assert "syncLockWindow" in manager and "syncLockWindow" in app, (
        "the reconciler must be defined in the manager and called from the app"
    )
