//
//  LearnAudioEntitlement.swift
//  ios
//
//  Wiser (Learn) narration gate — read free, listen with Pro. MONEY MOVES + BOOKS ONLY.
//
//  ⚠️ THE INVESTOR JOURNEY IS NOT GATED BY THIS OBJECT.
//  Journey narration is free on every tier including signed-out guests (backend:
//  entitlements.JOURNEY_AUDIO_UNLOCKED_TIERS). `AIVoiceManager`, the Journey engine, no
//  longer consults this class at all, and `LessonTopicCardView` references it zero times.
//  Two source-scan tests fail the build if either starts again.
//
//  WHY THIS EXISTS AS A SERVICE RATHER THAN A VIEW CHECK
//  -----------------------------------------------------
//  The gate has to sit at the AUDIO ENGINES, not at the buttons, for two reasons the
//  backend redaction alone cannot cover:
//
//  1. `AudioManager` treats a nil `audioUrl` as a NON-AUDIO episode and falls into a
//     simulated-playback branch: a 1-second-tick timer that drives the mini player and the
//     progress bar and then fires `playbackDidComplete` — which marks Money Moves articles
//     and book cores COMPLETE for a user who heard nothing. So stripping the URL server-side
//     is defence-in-depth on the wire; it is emphatically NOT the mechanism. The engine must
//     refuse before that branch is reachable.
//  2. Journey AUTO-PLAYS. `LessonTopicCardView` narrates on `.onAppear` and on every card
//     change, with zero user intent, and falls through to on-device TTS when a clip is
//     missing. There is no button to gate there.
//
//  Book audio is additionally client-only: `BookAudioContent.swift` compiles the public
//  Storage URLs into the binary, so there is no server request to refuse. This object is the
//  only gate books have until those URLs move behind a signed-URL endpoint.
//
//  ONE engine reads it now — `AudioManager` (Money Moves + Books). `AIVoiceManager` (the
//  Journey's own AVPlayer + AVSpeechSynthesizer) used to as well; reason 2 above is now the
//  reason the Journey needs no gate rather than the reason it needed a special one.
//

import Foundation
import Combine

@MainActor
final class LearnAudioEntitlement: ObservableObject {
    static let shared = LearnAudioEntitlement()

    /// Whether this account may hear MONEY MOVES / BOOK narration. Mirrors the backend's
    /// `entitlements.learn_audio_unlocked` (Pro/Max). Says nothing about the Journey, which
    /// is free on every tier.
    ///
    /// Defaults to LOCKED. An unknown tier must fall closed onto the paid surface, and on a
    /// cold launch this is false until `AppState.applyProfile` runs — a moment of silence is
    /// the right failure, where a moment of free narration is not.
    @Published private(set) var isUnlocked: Bool = false

    /// Fired when a locked caller tried to play something. Screens turn this into a paywall.
    /// A subject rather than a `@Published Bool` because two different taps in a row must
    /// each present, and a bool would swallow the second.
    let upgradeRequested = PassthroughSubject<Void, Never>()

    private init() {}

    /// Called from `AppState.applyProfile` — the single place `user.tier` is assigned.
    func update(tier: UserTier) {
        let unlocked = (tier == .pro || tier == .premium)
        guard unlocked != isUnlocked else { return }
        isUnlocked = unlocked
        if unlocked {
            // ⚠️ THE UPGRADE MUST INVALIDATE THE MONEY MOVES STORE, or it buys nothing this run.
            //
            // The store latches a once-per-session prefetch (`didPrefetch`) that never ages
            // out. A caller who was locked at launch holds a payload the backend REDACTED:
            // text intact, `audioUrl` and the read-along spans gone. Flipping this flag changes
            // the labels — `hasAudioVersion` is deliberately left true so the offer stays
            // visible — but the URLs are still missing, so "Listen Now" hits
            // `AudioManager.isMissingNarration` and dies in a `print`: no audio, no error, no
            // paywall. Just a dead button. It does not recover without a relaunch.
            //
            // JourneyContentStore is deliberately NOT refreshed: its payload is complete on
            // the first fetch for every tier, so re-fetching 27 lessons and 6,435 word timings
            // on upgrade would buy nothing.
            Task { await MoneyMovesContentStore.shared.forceRefresh() }
        } else {
            // A downgrade mid-session must not leave PAID narration playing.
            // `stopForLostEntitlement` self-filters to Money Moves / book episodes.
            //
            // ⚠️ Do NOT add `AIVoiceManager.shared.stop()` back here. `AppState` funnels
            // EVERY auth transition through `update(tier: .free)`, so that call would cut a
            // Journey lesson off mid-sentence the moment a user signed out — narration they
            // are still entitled to, because the Journey is free on every tier.
            AudioManager.shared.stopForLostEntitlement()
        }
    }

    /// `true` when this episode is gated Learn narration. Non-Learn audio (Daily Brief,
    /// podcasts) is unaffected — the gate is on the produced library, not on the player.
    func isLocked(_ episode: AudioEpisode) -> Bool {
        guard !isUnlocked else { return false }
        return episode.category == .moneyMoves
            || episode.category == .books
            || episode.bookCurriculumOrder != nil
    }

    /// Refuse and ask for an upgrade. Returns `true` when the caller should stop.
    @discardableResult
    func refuseIfLocked(_ episode: AudioEpisode) -> Bool {
        guard isLocked(episode) else { return false }
        upgradeRequested.send()
        return true
    }
}
