//
//  LessonNarrationPolicy.swift
//  ios
//
//  The decisions behind the Investor Journey lesson player's play/pause button and its
//  auto-advance, as pure functions over plain values.
//
//  ⚠️ FOUNDATION ONLY — no `import SwiftUI`. There is no XCTest target in this project, so
//  the only way to EXECUTE this branching logic in CI is to pipe this file into
//  `xcrun swift -` from pytest (`backend/tests/test_ios_lesson_narration_policy.py`, same
//  mechanism as `MoneyMoveDateFormatting`). A SwiftUI import makes it unrunnable there.
//
//  WHY IT EXISTS (TestFlight 1.0(8)): Pause used to hold for ONE card. Every card change
//  started the next clip unconditionally, so a learner who wanted to read without the voice
//  had to press Pause on every card. Pause is now STICKY for the rest of the lesson
//  ("muted"): no narration and no auto-advance until the learner presses Play; the next
//  lesson starts voiced again (developer decision 2026-09-22).
//
//  Three traps the rules below encode:
//   1. `AIVoiceManager.resume()` with nothing loaded REPLAYS its `lastRequest` — which, after
//      a card change, is the PREVIOUS card's clip. So "Play" must restart the CURRENT card
//      unless the engine still holds this card's paused audio (`canResumeInPlace`). Every
//      card change calls `stop()` first, so a held item always belongs to the current card.
//   2. After a clip finishes, the button used to show "play" during the 1.5 s before the
//      auto-advance fired, so the learner could not stop it — and a tap replayed the card
//      while the timer still advanced mid-replay. An armed auto-advance now shows "pause".
//   3. The icon must never claim silence while audio plays: `isPlaying` alone shows pause.
//

import Foundation

enum LessonNarrationPolicy {

    /// What a card does when it becomes current.
    enum CardStart: Equatable {
        case narrate
        case silent   // muted: no clip, no speech, no auto-advance — the learner reads
    }

    /// What a tap on the play/pause button does.
    enum Tap: Equatable {
        case pause          // mute the rest of the lesson; stop audio and any armed advance
        case resumeInPlace  // unmute; continue this card's paused audio where it stopped
        case restartCard    // unmute; narrate the current card from its start
    }

    static func cardStart(muted: Bool) -> CardStart {
        muted ? .silent : .narrate
    }

    /// Whether the button shows the pause glyph. True while audio plays, and while an
    /// auto-advance is armed (so the learner can still stop the lesson moving on).
    static func showsPause(muted: Bool, isPlaying: Bool, advancePending: Bool) -> Bool {
        isPlaying || (advancePending && !muted)
    }

    /// The action for a button tap. Always the inverse of what the glyph shows.
    static func tap(muted: Bool, isPlaying: Bool, advancePending: Bool,
                    canResumeInPlace: Bool) -> Tap {
        if showsPause(muted: muted, isPlaying: isPlaying, advancePending: advancePending) {
            return .pause
        }
        return canResumeInPlace ? .resumeInPlace : .restartCard
    }

    /// Whether a firing auto-advance timer may move the lesson on. `sourceIndex` is the card
    /// that armed it: a timer that outlived a manual swipe must not skip a second card.
    static func shouldAutoAdvance(muted: Bool, currentIndex: Int, sourceIndex: Int) -> Bool {
        !muted && currentIndex == sourceIndex
    }

    /// Whether a narration-finished callback may arm the auto-advance. The engine's completion
    /// can arrive late (a clip-load failure falls back to speech asynchronously), so it is
    /// honoured only for the card that started it, and never while muted.
    static func shouldHonorCompletion(muted: Bool, currentIndex: Int, narratedIndex: Int) -> Bool {
        !muted && currentIndex == narratedIndex
    }
}
