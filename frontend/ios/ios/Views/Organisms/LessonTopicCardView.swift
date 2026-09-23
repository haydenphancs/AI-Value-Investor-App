//
//  LessonTopicCardView.swift
//  ios
//
//  Organism: Full-screen lesson story card view with swipeable cards,
//  AI voice orb, and progress tracking (Instagram Stories style)
//  Includes text-to-speech with word-by-word highlighting
//

import SwiftUI
import Combine

struct LessonTopicCardView: View {
    let storyContent: LessonStoryContent
    var onDismiss: (() -> Void)?
    var onCTATapped: ((LessonCTADestination) -> Void)?
    /// Fired once when the learner reaches the lesson's completion card.
    var onLessonCompleted: (() -> Void)?
    /// Ask Cay AI about THIS lesson. Offered on the completion card only — mid-lesson
    /// it would compete with the read-along, and at the end the learner has the whole
    /// concept in mind, which is when a follow-up question is actually worth asking.
    var onAskAI: (() -> Void)?

    @StateObject private var voiceManager = AIVoiceManager.shared
    @State private var currentIndex: Int = 0
    @State private var cardProgress: CGFloat = 0
    @State private var dragOffset: CGFloat = 0
    @State private var didMarkCompleted = false

    /// Sticky pause. Set by the learner's Pause (or a headphone unplug), cleared by their Play.
    /// While set, a card change starts NO narration and NO auto-advance — the learner reads at
    /// their own pace. TestFlight 1.0(8): Pause used to hold for one card only. Per
    /// presentation on purpose: the next lesson starts voiced (developer decision 2026-09-22).
    /// The rules live in `LessonNarrationPolicy` so they can be executed in CI.
    @State private var narrationMuted = false

    // Timer for auto-advance after voice finishes
    @State private var autoAdvanceTimer: Timer?
    /// An auto-advance is armed. Shown as "pause" so the learner can stop the lesson moving on.
    @State private var advancePending = false
    /// Identifies the armed timer. A Timer that has ALREADY fired can't be cancelled by
    /// `invalidate()` (its callback is queued), so the callback checks it is still the current
    /// one before touching `advancePending` or advancing.
    @State private var autoAdvanceToken = 0

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                // Background
                AppColors.background
                    .ignoresSafeArea()

                VStack(spacing: 0) {
                    // Header with lesson label and close button
                    headerView
                        .padding(.top, AppSpacing.sm)
                        .zIndex(100) // Ensure header stays on top

                    // Progress bar
                    LessonStoryProgressBar(
                        currentIndex: currentIndex,
                        totalCount: storyContent.totalCards,
                        currentProgress: cardProgress
                    )
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.md)
                    .zIndex(99)

                    // The card AND the narration controls share one layer, so the prev/next strips
                    // run the full height below the progress bar. TestFlight 1.0(6): the strips
                    // used to cover the text only, and taps beside the orb / pause button did
                    // nothing. The pause button stays reachable because the centre 40% is a
                    // Spacer, which is not hit-testable.
                    ZStack {
                        VStack(spacing: 0) {
                            cardContentView
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                                // VoiceOver can't use invisible strips; it gets named actions.
                                // "Next card" only when there IS one — on the last card
                                // `goToNext()` is a guarded no-op, the same reason the strips
                                // are gone from the completion card.
                                .accessibilityActions {
                                    Button("Previous card") { goToPrevious() }
                                    if currentIndex < storyContent.totalCards - 1 {
                                        Button("Next card") { goToNext() }
                                    }
                                }

                            // Bottom section with orb and controls (not on completion card)
                            if !isCompletionCard {
                                bottomControlsView
                                    .padding(.bottom, AppSpacing.xxxl)
                            }
                        }

                        // No strips on the completion card: they sat ON TOP of its full-width
                        // "Ask Cay AI about this" button, so its left third went back a card and
                        // its right third did nothing. Swipe-back still works there.
                        if !isCompletionCard {
                            tapZones(width: geometry.size.width)
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .gesture(
                DragGesture()
                    .onChanged { value in
                        dragOffset = value.translation.width
                    }
                    .onEnded { value in
                        handleDragEnd(value: value)
                    }
            )
        }
        .onAppear {
            startReadingCurrentCard()
        }
        .onDisappear {
            voiceManager.stop()
            stopAutoAdvanceTimer()
        }
        .onChange(of: currentIndex) { _, _ in
            startReadingCurrentCard()
        }
        .onChange(of: voiceManager.progress) { _, newProgress in
            // Sync card progress with voice progress. Not while muted: a silent card owns its
            // segment, and the `stop()` of the card change zeroes `progress` — which would
            // otherwise land after `startReadingCurrentCard` filled it.
            if !isCompletionCard && !narrationMuted {
                cardProgress = newProgress
            }
        }
        .onChange(of: voiceManager.routeLossPauseCount) { _, _ in
            // Headphones pulled: the engine already paused. Make it the learner's pause, so the
            // next card doesn't start talking out of the speaker.
            narrationMuted = true
            stopAutoAdvanceTimer()
        }
        // No `.learnAudioPaywall()` here. Journey narration is free on every tier, so
        // nothing inside this cover can raise `upgradeRequested` any more. The modifier is
        // still attached on the Money Moves and book screens, whose narration is Pro/Max.
    }

    // MARK: - Computed Properties

    private var currentCard: LessonTopicCard {
        storyContent.cards[currentIndex]
    }

    private var isCompletionCard: Bool {
        currentCard.cardType == .completion
    }

    /// Get the text to read for the current card
    private var currentAudioText: String {
        if let audioText = currentCard.audioText {
            return audioText
        }

        // Fall back to constructing from segments
        switch currentCard.cardType {
        case .title:
            return currentCard.subtitleSegments?.map { $0.text }.joined() ?? ""
        case .content:
            return currentCard.contentSegments?.map { $0.text }.joined() ?? ""
        case .completion:
            return ""
        }
    }

    // MARK: - Subviews

    private var headerView: some View {
        HStack {
            Text(storyContent.lessonLabel)
                .font(AppTypography.labelEmphasis)
                .foregroundColor(AppColors.textSecondary)
                .tracking(0.5)

            Spacer()

            Button(action: {
                handleClose()
            }) {
                Image(systemName: "xmark")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(AppColors.textSecondary)
                    .frame(width: 32, height: 32)
                    .hitSlop(reaching: 32)
            }
            .buttonStyle(.plain) // Ensure no interference from default button style
        }
        .padding(.horizontal, AppSpacing.lg)
        .allowsHitTesting(true)
    }

    @ViewBuilder
    private var cardContentView: some View {
        switch currentCard.cardType {
        case .title:
            LessonTitleCard(
                title: currentCard.title ?? "",
                subtitleSegments: currentCard.subtitleSegments ?? [],
                currentWordRange: voiceManager.currentWordRange,
                isReading: voiceManager.isPlaying,
                imageName: currentCard.imageName
            )
            .transition(.opacity.combined(with: .scale(scale: 0.95)))

        case .content:
            LessonContentCard(
                imageName: currentCard.imageName,
                contentSegments: currentCard.contentSegments ?? [],
                currentWordRange: voiceManager.currentWordRange,
                isReading: voiceManager.isPlaying
            )
            .transition(.opacity.combined(with: .scale(scale: 0.95)))

        case .completion:
            // "Ask Cay AI about this" is the card's PRIMARY button now — it used to float as a
            // capsule in an `.overlay(alignment: .bottom)` under the card while "Analyze a Stock"
            // held the primary slot. `onCTATapped` / `currentCard.ctaDestination` are therefore no
            // longer reachable from here; the content schema still carries a `cta` key and
            // `JourneyContentStore.cta(_:)` still parses it, so that plumbing is left intact
            // rather than torn out of the content layer.
            LessonCompletionCard(
                title: currentCard.completionTitle ?? "You're ready.",
                subtitle: currentCard.completionSubtitle ?? "",
                imageName: currentCard.imageName,
                onAskAITapped: onAskAI == nil ? nil : {
                    // Same teardown the floating button did: the narration must not keep talking
                    // over the chat, and the auto-advance timer must not fire behind the cover.
                    voiceManager.stop()
                    stopAutoAdvanceTimer()
                    onAskAI?()
                },
                onCloseTapped: {
                    onDismiss?()
                }
            )
            .transition(.opacity.combined(with: .move(edge: .bottom)))
        }
    }

    /// The glyph follows `LessonNarrationPolicy.showsPause`: pause while audio plays AND while
    /// an auto-advance is armed, so the learner can always stop the lesson moving on.
    private var showsPause: Bool {
        LessonNarrationPolicy.showsPause(muted: narrationMuted,
                                         isPlaying: voiceManager.isPlaying,
                                         advancePending: advancePending)
    }

    private var bottomControlsView: some View {
        VStack(spacing: AppSpacing.xl) {
            // AI Voice Orb - animated when speaking
            AIVoiceOrb(isAnimating: voiceManager.isPlaying, size: 100)

            // Play/Pause button
            Button(action: {
                togglePlayPause()
            }) {
                ZStack {
                    Circle()
                        .fill(AppColors.textPrimary.opacity(0.1))
                        .frame(width: 44, height: 44)

                    Image(systemName: showsPause ? "pause.fill" : "play.fill")
                        .font(AppTypography.iconDefault).fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                }
            }
            .accessibilityLabel(showsPause ? "Pause narration" : "Play narration")
        }
    }

    /// Full-height prev/next strips (30% each side; the centre stays neutral for the pause
    /// button). They reach the bottom screen edge — the tester's marks ran to it.
    private func tapZones(width: CGFloat) -> some View {
        HStack(spacing: 0) {
            // Left tap zone - go back
            Color.clear
                .contentShape(Rectangle())
                .onTapGesture {
                    goToPrevious()
                }
                .frame(width: width * 0.3)

            Spacer()

            // Right tap zone - go forward
            Color.clear
                .contentShape(Rectangle())
                .onTapGesture {
                    goToNext()
                }
                .frame(width: width * 0.3)
        }
        .ignoresSafeArea(edges: .bottom)
        // Invisible strips over the text would steal VoiceOver's touch exploration; the card
        // carries named "Previous card" / "Next card" actions instead.
        .accessibilityHidden(true)
    }

    // MARK: - Voice Reading

    private func handleClose() {
        // Stop all ongoing activities
        voiceManager.stop()
        stopAutoAdvanceTimer()
        
        // Dismiss the view
        onDismiss?()
    }

    private func startReadingCurrentCard() {
        stopAutoAdvanceTimer()

        // Reaching the final card finishes the lesson — whether it's an explicit completion card
        // OR (for remote content authored without one) just the last content card. Keying completion
        // solely on cardType == .completion would leave such a lesson permanently "incomplete": the
        // learner could read every card yet never get the local UserDefaults write / progress POST.
        if isCompletionCard || currentIndex >= storyContent.totalCards - 1 {
            markLessonCompletedOnce()
        }

        guard !isCompletionCard else {
            // No voice for the completion card; show its progress segment full.
            cardProgress = 1.0
            return
        }

        // No entitlement guard: Journey narration is free on every tier, so this method
        // narrates from `.onAppear` and on every card change for everyone, including
        // signed-out guests. `markLessonCompletedOnce()` above is untouched — completion is
        // driven by reaching the last card, not by audio.

        // Sticky pause: a muted lesson stays silent and never advances on its own. Placed AFTER
        // the completion bookkeeping, so a learner reading silently still completes the lesson.
        guard LessonNarrationPolicy.cardStart(muted: narrationMuted) == .narrate else {
            cardProgress = 1.0
            return
        }

        let textToRead = currentAudioText
        guard !textToRead.isEmpty else {
            cardProgress = 1.0
            scheduleAutoAdvance(delay: 2.0)
            return
        }

        cardProgress = 0

        // The engine's completion can arrive LATE (a failed clip load falls back to speech
        // asynchronously), so it is honoured only for the card that started it and not while
        // muted — otherwise a stale finish would advance the card the learner is reading.
        let narratedIndex = currentIndex
        let onFinished: () -> Void = { [self] in
            guard LessonNarrationPolicy.shouldHonorCompletion(
                muted: narrationMuted, currentIndex: currentIndex, narratedIndex: narratedIndex
            ) else { return }
            // Voice finished, wait a moment then auto-advance
            scheduleAutoAdvance(delay: 1.5)
        }

        // Prefer pre-recorded AI narration (Achird) when this card has a bundled clip;
        // otherwise fall back to on-device speech synthesis.
        if let clip = currentCard.audioClip, !clip.isEmpty {
            voiceManager.playClip(named: clip, text: textToRead, readAlong: currentCard.readAlongWords,
                                  onComplete: onFinished)
        } else {
            voiceManager.speak(textToRead, onComplete: onFinished)
        }
    }

    /// Fire the lesson-completed callback exactly once for this presentation.
    private func markLessonCompletedOnce() {
        guard !didMarkCompleted else { return }
        didMarkCompleted = true
        onLessonCompleted?()
    }

    private func scheduleAutoAdvance(delay: TimeInterval) {
        stopAutoAdvanceTimer()

        // A Timer that has ALREADY fired can't be cancelled by a later invalidate(): its callback is
        // queued. If the learner manually navigates in that window, the stale callback would advance
        // a SECOND time and skip a card (its narration never plays). Pin the source index and only
        // advance if we're still on it. The token does the same for a pause / re-arm in that window.
        let sourceIndex = currentIndex
        autoAdvanceToken &+= 1
        let token = autoAdvanceToken
        advancePending = true
        autoAdvanceTimer = Timer.scheduledTimer(withTimeInterval: delay, repeats: false) { _ in
            Task { @MainActor in
                guard token == autoAdvanceToken else { return }   // cancelled or superseded
                advancePending = false
                autoAdvanceTimer = nil
                guard LessonNarrationPolicy.shouldAutoAdvance(
                    muted: narrationMuted, currentIndex: currentIndex, sourceIndex: sourceIndex
                ) else { return }
                if currentIndex < storyContent.totalCards - 1 {
                    goToNext()
                }
            }
        }
    }

    private func stopAutoAdvanceTimer() {
        autoAdvanceTimer?.invalidate()
        autoAdvanceTimer = nil
        autoAdvanceToken &+= 1   // a callback already queued is now stale
        advancePending = false
    }

    // MARK: - Navigation

    private func goToNext() {
        guard currentIndex < storyContent.totalCards - 1 else {
            return
        }

        voiceManager.stop()
        stopAutoAdvanceTimer()

        withAnimation(.easeInOut(duration: 0.3)) {
            currentIndex += 1
            cardProgress = 0
        }
    }

    private func goToPrevious() {
        voiceManager.stop()
        stopAutoAdvanceTimer()

        guard currentIndex > 0 else {
            // Restart current card
            cardProgress = 0
            startReadingCurrentCard()
            return
        }

        withAnimation(.easeInOut(duration: 0.3)) {
            currentIndex -= 1
            cardProgress = 0
        }
    }

    private func handleDragEnd(value: DragGesture.Value) {
        let threshold: CGFloat = 50

        if value.translation.width < -threshold {
            goToNext()
        } else if value.translation.width > threshold {
            goToPrevious()
        }

        dragOffset = 0
    }

    private func togglePlayPause() {
        // No paywall branch — Journey narration is free on every tier.
        switch LessonNarrationPolicy.tap(muted: narrationMuted,
                                         isPlaying: voiceManager.isPlaying,
                                         advancePending: advancePending,
                                         canResumeInPlace: voiceManager.canResumeInPlace) {
        case .pause:
            // Sticky for the rest of the lesson — card changes stay silent until Play.
            narrationMuted = true
            stopAutoAdvanceTimer()
            if voiceManager.isPlaying {
                voiceManager.pause()
            }
        case .resumeInPlace:
            // Every Play un-mutes FIRST — otherwise progress sync, the completion's auto-advance
            // and every later card would stay silenced while this one plays.
            narrationMuted = false
            stopAutoAdvanceTimer()
            voiceManager.resume()
        case .restartCard:
            // Nothing of THIS card is held (a card change, a finished clip, a silent card):
            // narrate it from the start. Never `resume()` here — with nothing loaded it replays
            // the engine's last request, which can be the PREVIOUS card.
            narrationMuted = false
            startReadingCurrentCard()
        }
    }
}

#Preview {
    LessonTopicCardView(
        storyContent: .buffettWaySample,
        onDismiss: {
            print("Dismissed")
        },
        onCTATapped: { destination in
            print("CTA tapped: \(destination)")
        }
    )
}
