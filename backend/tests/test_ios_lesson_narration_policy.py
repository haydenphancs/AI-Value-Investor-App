"""Journey lesson narration: sticky pause (TestFlight 1.0(8), wiser_learn E1).

Tester: *"When touches on Pause, it should paused at the all even if i change the page, so some
users want to read only without voice."* Pause used to hold for ONE card: every card change ran
`startReadingCurrentCard()`, which started the next clip and its auto-advance unconditionally.
Developer decision (2026-09-22): the pause lasts the rest of the LESSON; the next lesson starts
voiced; no auto-advance while paused.

Two halves:

1. `LessonNarrationPolicy` is EXECUTED — piped into `xcrun swift -` (no XCTest target exists;
   precedent `test_money_moves_date_label.py`). The expectations below are the SPEC, written
   out case by case from the user stories, not derived from the Swift.
2. Source scans (comment-stripped, brace-bounded) pin that `LessonTopicCardView` and
   `AIVoiceManager` actually consult it, including the three traps the fix had to respect:
   `resume()` with nothing loaded replays the PREVIOUS card; the clip-failure refresh Task
   restarted audio after a pause; and the post-clip auto-advance could not be stopped.

Category 1 (pure) — no network, no Supabase. The Swift half skips when `xcrun` is unavailable.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
IOS = REPO / "frontend/ios/ios"
POLICY = IOS / "Core/Utilities/LessonNarrationPolicy.swift"
VIEW = IOS / "Views/Organisms/LessonTopicCardView.swift"
ENGINE = IOS / "Services/AIVoiceManager.swift"


def _strip_comments(src: str) -> str:
    """Drop `/* */` blocks, whole-line `//` comments and trailing `//` tails.

    Load-bearing: the explanatory comments next to each fix name every token these tests look
    for, so an un-stripped scan would pass on prose after the code was reverted.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    out = []
    for line in src.splitlines():
        if line.lstrip().startswith("//"):
            continue
        # A trailing comment: `//` preceded by whitespace, outside a string literal.
        m = re.search(r"\s//", line)
        if m and line[: m.start()].count('"') % 2 == 0:
            line = line[: m.start()]
        out.append(line)
    return "\n".join(out)


def _code(path: Path) -> str:
    assert path.exists(), f"{path} is missing — every assertion below would be vacuous"
    return _strip_comments(path.read_text())


def _block_after(src: str, anchor: str) -> str:
    """The brace-balanced block that opens at the first `{` after `anchor`."""
    at = src.find(anchor)
    assert at >= 0, f"`{anchor}` not found"
    open_at = src.index("{", at)
    depth = 0
    for i in range(open_at, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[open_at : i + 1]
    pytest.fail(f"unbalanced braces after `{anchor}`")


# ---------------------------------------------------------------------------------------------
# 1. The policy, executed
# ---------------------------------------------------------------------------------------------

HARNESS = r"""
var failures = 0
func check<T: Equatable>(_ name: String, _ got: T, _ expect: T) {
    if got == expect { print("ok|\(name)") }
    else { failures += 1; print("FAIL|\(name)|got=\(got)|expect=\(expect)") }
}
typealias P = LessonNarrationPolicy

// --- The button, scenario by scenario ------------------------------------------------------
// Narration playing mid-card: the button pauses.
check("playing_shows_pause", P.showsPause(muted: false, isPlaying: true, advancePending: false), true)
check("playing_tap_pauses",
      P.tap(muted: false, isPlaying: true, advancePending: false, canResumeInPlace: true), .pause)
// Paused mid-card, still on it: Play continues THIS card where it stopped.
check("paused_same_card_shows_play", P.showsPause(muted: true, isPlaying: false, advancePending: false), false)
check("paused_same_card_resumes",
      P.tap(muted: true, isPlaying: false, advancePending: false, canResumeInPlace: true), .resumeInPlace)
// Paused, then swiped: the engine was stopped by the card change, so nothing of THIS card is
// held. `resume()` would replay the PREVIOUS card's clip — Play must restart the current card.
check("paused_then_navigated_restarts",
      P.tap(muted: true, isPlaying: false, advancePending: false, canResumeInPlace: false), .restartCard)
// A clip just finished and the 1.5 s auto-advance is armed. The button used to show "play"
// here, so the learner could not stop the lesson moving on.
check("advance_armed_shows_pause", P.showsPause(muted: false, isPlaying: false, advancePending: true), true)
check("advance_armed_tap_pauses",
      P.tap(muted: false, isPlaying: false, advancePending: true, canResumeInPlace: false), .pause)
// Last content card finished (no advance to arm): Play narrates the card again from its start.
check("finished_last_card_restarts",
      P.tap(muted: false, isPlaying: false, advancePending: false, canResumeInPlace: false), .restartCard)
check("finished_last_card_shows_play", P.showsPause(muted: false, isPlaying: false, advancePending: false), false)
// A system interruption paused the engine without the learner: in place if held.
check("system_paused_resumes_in_place",
      P.tap(muted: false, isPlaying: false, advancePending: false, canResumeInPlace: true), .resumeInPlace)
// The icon never claims silence while audio plays — even if the mute flag is somehow set.
check("muted_but_playing_shows_pause", P.showsPause(muted: true, isPlaying: true, advancePending: false), true)
check("muted_but_playing_tap_pauses",
      P.tap(muted: true, isPlaying: true, advancePending: false, canResumeInPlace: true), .pause)
// A muted lesson never shows an armed advance as "pause" (none should be armed; defensive).
check("muted_pending_shows_play", P.showsPause(muted: true, isPlaying: false, advancePending: true), false)

// --- Exhaustive: the tap is ALWAYS the inverse of the glyph ---------------------------------
var combos = 0
for muted in [false, true] { for playing in [false, true] { for pending in [false, true] {
    for held in [false, true] {
        combos += 1
        let shows = P.showsPause(muted: muted, isPlaying: playing, advancePending: pending)
        let tap = P.tap(muted: muted, isPlaying: playing, advancePending: pending, canResumeInPlace: held)
        let name = "inverse_m\(muted)_p\(playing)_a\(pending)_h\(held)"
        if shows { check(name, tap, .pause) }
        else { check(name, tap, held ? .resumeInPlace : .restartCard) }
        // Playing audio is never offered as "play".
        if playing { check("playing_never_play_\(name)", shows, true) }
    }
}}}
check("combos_enumerated", combos, 16)

// --- Card start, auto-advance, late completion ------------------------------------------
check("muted_card_is_silent", P.cardStart(muted: true), .silent)
check("unmuted_card_narrates", P.cardStart(muted: false), .narrate)
check("advance_same_card", P.shouldAutoAdvance(muted: false, currentIndex: 3, sourceIndex: 3), true)
check("advance_blocked_when_muted", P.shouldAutoAdvance(muted: true, currentIndex: 3, sourceIndex: 3), false)
check("advance_blocked_after_manual_nav", P.shouldAutoAdvance(muted: false, currentIndex: 4, sourceIndex: 3), false)
check("advance_blocked_after_back_nav", P.shouldAutoAdvance(muted: false, currentIndex: 2, sourceIndex: 3), false)
check("completion_same_card", P.shouldHonorCompletion(muted: false, currentIndex: 5, narratedIndex: 5), true)
check("completion_ignored_when_muted", P.shouldHonorCompletion(muted: true, currentIndex: 5, narratedIndex: 5), false)
check("stale_completion_ignored", P.shouldHonorCompletion(muted: false, currentIndex: 6, narratedIndex: 5), false)
check("index_zero_completion", P.shouldHonorCompletion(muted: false, currentIndex: 0, narratedIndex: 0), true)

print("DONE|\(failures)")
"""


def _run_swift() -> str:
    if not shutil.which("xcrun"):
        pytest.skip("xcrun unavailable — Swift cannot be executed on this host")
    src = POLICY.read_text() + "\n" + HARNESS
    try:
        proc = subprocess.run(["xcrun", "swift", "-"], input=src, text=True,
                              capture_output=True, timeout=180)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        pytest.skip(f"could not run swift: {type(exc).__name__}: {exc}")
    if "DONE|" not in proc.stdout:
        pytest.fail(
            "the Swift harness did not run to completion — the policy probably stopped compiling "
            f"standalone (a SwiftUI import will do it).\nstdout:\n{proc.stdout}\n"
            f"stderr:\n{proc.stderr[-4000:]}")
    return proc.stdout


@pytest.fixture(scope="module")
def swift_output() -> str:
    return _run_swift()


def test_policy_is_foundation_only():
    raw = POLICY.read_text()
    src = _strip_comments(raw)
    assert "import Foundation" in src
    assert "import SwiftUI" not in src and "import UIKit" not in src, (
        "LessonNarrationPolicy must stay Foundation-only — otherwise it cannot run under "
        "`xcrun swift -` and every case below silently stops being tested")
    assert "import SwiftUI" in raw, "the header comment explaining the rule is gone (anti-vacuity)"


def test_every_policy_case(swift_output: str):
    failures = [l for l in swift_output.splitlines() if l.startswith("FAIL|")]
    assert not failures, "policy mismatches:\n  " + "\n  ".join(failures)


def test_the_harness_actually_asserted_something(swift_output: str):
    oks = {l.split("|", 1)[1] for l in swift_output.splitlines() if l.startswith("ok|")}
    assert len(oks) >= 45, f"only {len(oks)} checks ran — the harness was truncated"
    for required in ("paused_then_navigated_restarts", "advance_armed_tap_pauses",
                     "muted_but_playing_shows_pause", "stale_completion_ignored",
                     "combos_enumerated"):
        assert required in oks, f"the {required} case did not run"


# ---------------------------------------------------------------------------------------------
# 2. The view consults the policy
# ---------------------------------------------------------------------------------------------

def test_card_start_checks_the_mute_before_any_audio_and_after_completion():
    body = _block_after(_code(VIEW), "private func startReadingCurrentCard()")
    completion = body.find("markLessonCompletedOnce()")
    gate = body.find("LessonNarrationPolicy.cardStart(muted: narrationMuted)")
    clip = body.find("voiceManager.playClip(")
    speak = body.find("voiceManager.speak(")
    assert completion >= 0 and gate >= 0 and clip >= 0 and speak >= 0, body
    assert completion < gate, (
        "the mute gate must come AFTER the completion bookkeeping — a learner reading silently "
        "must still complete the lesson")
    assert gate < clip and gate < speak, "a muted card must be gated before any audio starts"


def test_a_muted_card_never_schedules_the_auto_advance():
    body = _block_after(_code(VIEW), "private func startReadingCurrentCard()")
    at = body.find("LessonNarrationPolicy.cardStart(muted: narrationMuted)")
    silent = _block_after(body[at:], "else")
    assert "return" in silent
    for banned in ("scheduleAutoAdvance", "playClip", "speak(", "resume("):
        assert banned not in silent, f"the muted branch calls {banned}"


def test_the_narration_completion_is_honoured_only_for_its_own_unmuted_card():
    body = _block_after(_code(VIEW), "private func startReadingCurrentCard()")
    closure = _block_after(body, "let onFinished")
    guard = closure.find("LessonNarrationPolicy.shouldHonorCompletion(")
    arm = closure.find("scheduleAutoAdvance(delay: 1.5)")
    assert 0 <= guard < arm, closure
    assert "narratedIndex: narratedIndex" in closure and "muted: narrationMuted" in closure
    assert "let narratedIndex = currentIndex" in body
    # Both engine paths get the guarded closure, not an inline unguarded one.
    assert body.count("onComplete: onFinished") == 2, body


def test_every_play_branch_unmutes_and_restart_never_resumes():
    body = _block_after(_code(VIEW), "private func togglePlayPause()")
    assert "LessonNarrationPolicy.tap(" in body and "canResumeInPlace: voiceManager.canResumeInPlace" in body
    cases = re.split(r"\bcase\s+\.", body)
    by_name = {c.split(":", 1)[0].strip(): c for c in cases[1:]}
    assert set(by_name) == {"pause", "resumeInPlace", "restartCard"}, by_name.keys()
    assert "narrationMuted = true" in by_name["pause"] and "stopAutoAdvanceTimer()" in by_name["pause"]
    for name in ("resumeInPlace", "restartCard"):
        assert "narrationMuted = false" in by_name[name], (
            f"`{name}` must un-mute — otherwise progress sync, the completion's auto-advance and "
            "every later card stay silenced while this one plays")
    assert "voiceManager.resume()" in by_name["resumeInPlace"]
    assert "stopAutoAdvanceTimer()" in by_name["resumeInPlace"], (
        "resuming must cancel an armed advance, or the card moves on mid-replay")
    assert "resume(" not in by_name["restartCard"], (
        "restartCard must not call resume(): with nothing loaded it replays the PREVIOUS card")
    assert "startReadingCurrentCard()" in by_name["restartCard"]


def test_the_timer_callback_is_token_and_policy_guarded():
    code = _code(VIEW)
    sched = _block_after(code, "private func scheduleAutoAdvance(")
    assert "advancePending = true" in sched and "autoAdvanceToken &+= 1" in sched
    cb = _block_after(sched, "Task { @MainActor in")
    token = cb.find("guard token == autoAdvanceToken")
    policy = cb.find("LessonNarrationPolicy.shouldAutoAdvance(")
    advance = cb.find("goToNext()")
    assert 0 <= token < policy < advance, cb
    stop = _block_after(code, "private func stopAutoAdvanceTimer()")
    assert "autoAdvanceToken &+= 1" in stop and "advancePending = false" in stop, (
        "cancelling must invalidate an already-queued callback and clear the armed state")


def test_progress_sync_and_route_loss_respect_the_mute():
    code = _code(VIEW)
    sync = _block_after(code, ".onChange(of: voiceManager.progress)")
    assert "!narrationMuted" in sync, "stop() zeroes progress and would overwrite a silent card"
    loss = _block_after(code, ".onChange(of: voiceManager.routeLossPauseCount)")
    assert "narrationMuted = true" in loss and "stopAutoAdvanceTimer()" in loss


def test_the_button_glyph_and_label_follow_the_policy():
    code = _code(VIEW)
    assert "LessonNarrationPolicy.showsPause(" in _block_after(code, "private var showsPause: Bool")
    controls = _block_after(code, "private var bottomControlsView: some View")
    assert 'showsPause ? "pause.fill" : "play.fill"' in controls
    assert "voiceManager.isPlaying ? \"pause.fill\"" not in controls
    assert 'accessibilityLabel(showsPause ? "Pause narration" : "Play narration")' in controls


def test_mute_is_per_presentation_state():
    """Per lesson (developer decision): view @State, never persisted."""
    code = _code(VIEW)
    assert "@State private var narrationMuted = false" in code
    assert "AppStorage" not in code and "UserDefaults" not in code


# ---------------------------------------------------------------------------------------------
# 3. The engine can no longer start audio behind a pause
# ---------------------------------------------------------------------------------------------

def test_a_paused_clip_failure_has_no_fallback():
    body = _block_after(_code(ENGINE), "private func handleClipLoadFailed()")
    guard = body.find("guard isPlaying else")
    assert guard >= 0, body
    first_restart = min(i for i in (body.find("speak("), body.find("playClip(")) if i >= 0)
    assert guard < first_restart, "the paused check must precede every fallback"
    paused = _block_after(body[guard:], "else")
    assert "teardownPlayer()" in paused and "return" in paused
    assert "speak(" not in paused and "playClip(" not in paused and "Task" not in paused


def test_the_refresh_task_bails_after_a_pause_or_stop():
    body = _block_after(_code(ENGINE), "private func handleClipLoadFailed()")
    assert "let generation = playbackGeneration" in body
    task = _block_after(body, "Task { @MainActor in")
    check = task.find("guard self.playbackGeneration == generation, self.isPlaying else")
    assert check >= 0, task
    for call in ("self.playClip(", "self.speak("):
        assert 0 <= check < task.find(call), f"{call} can run before the generation check"
    bail = _block_after(task[check:], "else")
    assert "self.didRetryClipRefresh = false" in bail and "return" in bail, (
        "a bailed refresh must give the one-shot budget back — nothing reached .readyToPlay to "
        "clear it, and the lesson still holds the expired URLs, so every later card would drop "
        "straight to the system voice")


def test_every_start_stop_and_pause_bumps_the_generation():
    code = _code(ENGINE)
    for anchor in ("func speak(", "func playClip(", "func stop()", "private func pauseEngine()"):
        assert "playbackGeneration &+= 1" in _block_after(code, anchor), anchor


def test_user_pause_drops_the_interruption_latch_and_the_system_pause_keeps_it():
    code = _code(ENGINE)
    user = _block_after(code, "func pause()")
    assert "wasPlayingBeforeInterruption = false" in user and "pauseEngine()" in user
    began = _block_after(code, "private func handleInterruption(")
    began = began[began.find("case .began"): began.find("case .ended")]
    assert "pauseEngine()" in began and "pause()" not in began.replace("pauseEngine()", ""), (
        "the interruption must use the ENGINE pause — pause() clears the latch it just set")


def test_stop_drops_the_interruption_latch():
    """A lesson closed during a call must not start talking when the call ends."""
    assert "wasPlayingBeforeInterruption = false" in _block_after(_code(ENGINE), "func stop()")


def test_route_loss_is_published_and_resume_in_place_is_exposed():
    code = _code(ENGINE)
    route = _block_after(code, "private func handleRouteChange(")
    assert "routeLossPauseCount &+= 1" in route and "pause()" in route
    playing = _block_after(route, "if isPlaying")
    assert "routeLossPauseCount" not in playing, (
        "publish the route loss even between clips — an armed auto-advance would otherwise "
        "start the next card on the speaker")
    assert "@Published private(set) var routeLossPauseCount" in code
    held = _block_after(code, "var canResumeInPlace: Bool")
    assert "player != nil" in held and "isPaused" in held
