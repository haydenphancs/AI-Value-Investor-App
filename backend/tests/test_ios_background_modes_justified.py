"""Every `UIBackgroundModes` value the app declares must have a live feature behind it.

WHY THIS FILE EXISTS. Caydex 1.0 (9) was REJECTED on 2026-09-24 under Guideline 2.5.4:

    "The app declares support for audio in the UIBackgroundModes key in the Info.plist but
     we are unable to locate any features that require persistent audio."

The audio feature is real (Learn narration keeps playing on the Lock Screen), so that one was
answered with a recording rather than a code change. But the same audit found the OTHER
declared mode, `remote-notification`, genuinely had nothing behind it: no
`application(_:didReceiveRemoteNotification:fetchCompletionHandler:)` handler and a backend
that sends only `apns-push-type: alert` (never `content-available`). The Info.plist comment
beside it claimed a badge-reconciling wake that no code implemented. Alert pushes, `aps.badge`
and background notification ACTIONS need no background mode at all, so the declaration was
pure review liability.

The rule, enforced structurally so it holds for modes nobody has thought of yet:

  * a declared mode with no rule in `_RULES` fails — add a rule when you add a mode;
  * a declared mode whose rule finds the implementation missing fails.

Each rule is ALSO exercised against synthetic source (the `test_rule_*` tests), so a rule
cannot go vacuous just because its mode is currently not declared.

Per `.claude/rules/testing.md` §3 the Swift is comment-stripped and brace-bound to the
declaration that must hold the code, and the file was mutation-tested by hand — see
MUTATION_LOG at the bottom.
"""
from __future__ import annotations

import plistlib
import re
from pathlib import Path
from typing import Callable, Dict, List

import pytest

_REPO = Path(__file__).resolve().parents[2]
_IOS = _REPO / "frontend" / "ios"
_INFO_PLIST = _IOS / "ios" / "Info.plist"
_PBXPROJ = _IOS / "ios.xcodeproj" / "project.pbxproj"
_AUDIO_MANAGER = _IOS / "ios" / "Services" / "AudioManager.swift"
_APP_DELEGATE = _IOS / "ios" / "Core" / "AppDelegate.swift"
_BACKEND_APP = _REPO / "backend" / "app"


def _read(path: Path) -> str:
    if not path.exists():
        pytest.fail(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _strip_swift_comments(src: str) -> str:
    """Blank `/* */` blocks, `//` lines and trailing `//` tails (line numbers preserved).

    Rule 1: the fix for each mode left prose NAMING the API it is about (`MPRemoteCommandCenter`,
    `didReceiveRemoteNotification`), so an unstripped scan would pass on the comment after the
    code is gone. `\\s//` rather than `//` so a `"https://…"` literal survives.
    """
    src = re.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), src, flags=re.DOTALL)
    out = []
    for line in src.splitlines():
        out.append("" if line.strip().startswith("//") else re.sub(r"\s//.*$", "", line))
    return "\n".join(out)


def _decl_block(src: str, header: str) -> str:
    """Brace-balanced body of the declaration starting at `header` (rule 2)."""
    start = src.find(header)
    if start == -1:
        return ""
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


def _is_called(block: str, func: str) -> bool:
    """`func()` appears as a CALL, not only as its own `func func()` declaration."""
    return re.search(rf"(?<!func )\b{re.escape(func)}\(\)", block) is not None


# ── the declared set ────────────────────────────────────────────────────────────────────


def _declared_modes() -> List[str]:
    """Info.plist ∪ any `INFOPLIST_KEY_UIBackgroundModes` build setting (the two MERGE)."""
    with _INFO_PLIST.open("rb") as fh:
        modes = list(plistlib.load(fh).get("UIBackgroundModes", []))
    pbx = re.sub(r"/\*.*?\*/", "", _read(_PBXPROJ), flags=re.DOTALL)
    for raw in re.findall(r"INFOPLIST_KEY_UIBackgroundModes = (.+?);", pbx):
        modes.extend(v for v in re.split(r"[\s\"(),]+", raw) if v)
    return sorted(set(modes))


# ── one rule per mode: returns the list of missing pieces (empty = justified) ───────────


def _audio_problems(audio_manager_src: str) -> List[str]:
    """Persistent audio = a `.playback` session plus Lock Screen transport, wired at init."""
    block = _decl_block(_strip_swift_comments(audio_manager_src), "final class AudioManager")
    if not block:
        return ["`final class AudioManager` not found"]
    problems = []
    if "setCategory(.playback" not in block:
        problems.append("no `.playback` audio-session category (audio would stop on lock)")
    if "MPRemoteCommandCenter.shared()" not in block:
        problems.append("no MPRemoteCommandCenter wiring (no Lock Screen / Control Center controls)")
    if "MPNowPlayingInfoCenter.default().nowPlayingInfo =" not in block:
        problems.append("never publishes MPNowPlayingInfoCenter.nowPlayingInfo")
    for func in ("configureAudioSession", "setupRemoteCommands"):
        if not _is_called(block, func):
            problems.append(f"`{func}()` is declared but never called")
    return problems


def _remote_notification_problems(app_delegate_src: str, backend_sources: List[str]) -> List[str]:
    """A background wake needs BOTH a handler for it and a server that asks for it."""
    block = _decl_block(_strip_swift_comments(app_delegate_src), "final class AppDelegate")
    problems = []
    if "didReceiveRemoteNotification" not in block:
        problems.append(
            "AppDelegate implements no `application(_:didReceiveRemoteNotification:"
            "fetchCompletionHandler:)` — nothing handles a background wake"
        )
    if not any("content-available" in s for s in backend_sources):
        problems.append(
            "the backend never sends `content-available` (every push is apns-push-type "
            "alert), so iOS is never asked to wake the app"
        )
    return problems


def _backend_python_sources() -> List[str]:
    return [p.read_text(encoding="utf-8") for p in _BACKEND_APP.rglob("*.py")]


_RULES: Dict[str, Callable[[], List[str]]] = {
    "audio": lambda: _audio_problems(_read(_AUDIO_MANAGER)),
    "remote-notification": lambda: _remote_notification_problems(
        _read(_APP_DELEGATE), _backend_python_sources()
    ),
}


# ── the live assertions ─────────────────────────────────────────────────────────────────


def test_the_declared_modes_are_readable():
    """Drift check: if the plist moves or stops parsing, the tests below would pass on `[]`."""
    assert _INFO_PLIST.exists(), f"{_INFO_PLIST} moved — update this file"
    assert "audio" in _declared_modes(), (
        "`audio` is no longer declared. If narration was deliberately made foreground-only, "
        "delete this assertion AND update the App Review notes' background-audio paragraph."
    )


@pytest.mark.parametrize("mode", _declared_modes())
def test_every_declared_background_mode_has_a_live_feature(mode: str):
    rule = _RULES.get(mode)
    assert rule is not None, (
        f"UIBackgroundModes declares {mode!r} but this file has no rule for it. Add one that "
        "proves the feature exists — Guideline 2.5.4 rejects a declared mode App Review "
        "cannot find a use for (Caydex 1.0 (9) was rejected on exactly this)."
    )
    problems = rule()
    assert not problems, (
        f"UIBackgroundModes declares {mode!r} but its implementation is missing:\n  - "
        + "\n  - ".join(problems)
        + "\nEither restore the feature or remove the mode from ios/Info.plist."
    )


# ── the rules themselves, on synthetic source (so an undeclared mode's rule stays honest) ──

_GOOD_AUDIO = """
@MainActor
final class AudioManager: ObservableObject {
    private init() {
        configureAudioSession()
        setupRemoteCommands()
    }
    private func configureAudioSession() {
        try? audioSession.setCategory(.playback, mode: .spokenAudio)
    }
    private func setupRemoteCommands() {
        let center = MPRemoteCommandCenter.shared()
    }
    private func publish() {
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }
}
"""

_GOOD_DELEGATE = """
final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(_ application: UIApplication,
                     didReceiveRemoteNotification userInfo: [AnyHashable: Any]) async -> UIBackgroundFetchResult {
        return .newData
    }
}
"""


def test_rule_audio_accepts_a_complete_implementation():
    assert _audio_problems(_GOOD_AUDIO) == []


@pytest.mark.parametrize(
    "mutation, expected",
    [
        (("setCategory(.playback", "setCategory(.ambient"), "`.playback`"),
        (("MPRemoteCommandCenter.shared()", "nil"), "MPRemoteCommandCenter"),
        (("MPNowPlayingInfoCenter.default().nowPlayingInfo = info", "_ = info"), "nowPlayingInfo"),
        (("        setupRemoteCommands()\n    }", "    }"), "`setupRemoteCommands()` is declared but never called"),
    ],
)
def test_rule_audio_rejects_each_missing_piece(mutation, expected):
    old, new = mutation
    assert old in _GOOD_AUDIO, "mutation no longer applies — fix the fixture"
    problems = _audio_problems(_GOOD_AUDIO.replace(old, new, 1))
    assert any(expected in p for p in problems), problems


def test_rule_audio_is_not_satisfied_by_a_comment():
    stripped = _GOOD_AUDIO.replace(
        "try? audioSession.setCategory(.playback, mode: .spokenAudio)",
        "// was: audioSession.setCategory(.playback, mode: .spokenAudio)",
    )
    assert any("`.playback`" in p for p in _audio_problems(stripped))


def test_rule_remote_notification_accepts_a_handler_plus_a_background_sender():
    assert _remote_notification_problems(_GOOD_DELEGATE, ['aps["content-available"] = 1']) == []


def test_rule_remote_notification_rejects_a_missing_handler():
    no_handler = "final class AppDelegate: NSObject { func applicationDidBecomeActive() {} }"
    problems = _remote_notification_problems(no_handler, ['aps["content-available"] = 1'])
    assert any("nothing handles a background wake" in p for p in problems)


def test_rule_remote_notification_rejects_a_handler_nobody_triggers():
    problems = _remote_notification_problems(_GOOD_DELEGATE, ['"apns-push-type": "alert"'])
    assert any("never sends `content-available`" in p for p in problems)


def test_rule_remote_notification_is_not_satisfied_by_a_comment():
    commented = "final class AppDelegate: NSObject {\n    // didReceiveRemoteNotification was removed\n}"
    assert _remote_notification_problems(commented, ["content-available"])


# ── MUTATION_LOG ─────────────────────────────────────────────────────────────────────
#
# Hand-run 2026-09-24 against the real tree (each applied, suite run, reverted):
#
#  1. Before the fix, the live tree declared `remote-notification` with no handler and an
#     alert-only backend
#       -> test_every_declared_background_mode_has_a_live_feature[remote-notification] FAILED ✅
#  2. AudioManager.swift: `setCategory(.playback` -> `setCategory(.ambient` (both call sites)
#       -> test_every_declared_background_mode_has_a_live_feature[audio] FAILED ✅
#  3. AudioManager.swift: deleted the `setupRemoteCommands()` call in init (declaration kept)
#       -> [audio] FAILED with "declared but never called" ✅
#  4. Info.plist: added `fetch` to UIBackgroundModes
#       -> [fetch] FAILED with "has no rule for it" ✅
