//
//  LessonTopicCardView.swift
//  ios
//
//  Organism: Full-screen lesson story card view with swipeable cards,
//  AI voice orb, and progress tracking (Instagram Stories style)
//  Includes text-to-speech with word-by-word highlighting
//

import SwiftUI

struct LessonTopicCardView: View {
    let storyContent: LessonStoryContent
    var onDismiss: (() -> Void)?
    var onCTATapped: ((LessonCTADestination) -> Void)?
    /// Fired once when the learner reaches the lesson's completion card.
    var onLessonCompleted: (() -> Void)?

    @StateObject private var voiceManager = AIVoiceManager.shared
    @State private var currentIndex: Int = 0
    @State private var cardProgress: CGFloat = 0
    @State private var dragOffset: CGFloat = 0
    @State private var didMarkCompleted = false

    // Timer for auto-advance after voice finishes
    @State private var autoAdvanceTimer: Timer?

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

                    // Card content with tap zones overlay
                    ZStack {
                        cardContentView
                        
                        // Tap zones for navigation (only over content area)
                        HStack(spacing: 0) {
                            // Left tap zone - go back
                            Color.clear
                                .contentShape(Rectangle())
                                .onTapGesture {
                                    goToPrevious()
                                }
                                .frame(width: geometry.size.width * 0.3)

                            Spacer()

                            // Right tap zone - go forward
                            Color.clear
                                .contentShape(Rectangle())
                                .onTapGesture {
                                    goToNext()
                                }
                                .frame(width: geometry.size.width * 0.3)
                        }
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                    // Bottom section with orb and controls (not on completion card)
                    if !isCompletionCard {
                        bottomControlsView
                            .padding(.bottom, AppSpacing.xxxl)
                            .zIndex(98)
                    }
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
            // Sync card progress with voice progress
            if !isCompletionCard {
                cardProgress = newProgress
            }
        }
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
                    .contentShape(Rectangle())
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
            LessonCompletionCard(
                title: currentCard.completionTitle ?? "You're ready.",
                subtitle: currentCard.completionSubtitle ?? "",
                lessonNumber: storyContent.lessonNumber,
                totalLessons: storyContent.totalLessonsInLevel,
                estimatedMinutes: storyContent.estimatedMinutes,
                ctaButtonTitle: currentCard.ctaButtonTitle ?? "Continue",
                imageName: currentCard.imageName,
                onCTATapped: {
                    if let destination = currentCard.ctaDestination {
                        onCTATapped?(destination)
                    }
                },
                onCloseTapped: {
                    onDismiss?()
                }
            )
            .transition(.opacity.combined(with: .move(edge: .bottom)))
        }
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
                        .fill(Color.white.opacity(0.1))
                        .frame(width: 44, height: 44)

                    Image(systemName: voiceManager.isPlaying ? "pause.fill" : "play.fill")
                        .font(AppTypography.iconDefault).fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                }
            }
        }
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

        let textToRead = currentAudioText
        guard !textToRead.isEmpty else {
            cardProgress = 1.0
            scheduleAutoAdvance(delay: 2.0)
            return
        }

        cardProgress = 0

        // Prefer pre-recorded AI narration (Achird) when this card has a bundled clip;
        // otherwise fall back to on-device speech synthesis.
        if let clip = currentCard.audioClip, !clip.isEmpty {
            voiceManager.playClip(named: clip, text: textToRead, readAlong: currentCard.readAlongWords) { [self] in
                scheduleAutoAdvance(delay: 1.5)
            }
        } else {
            voiceManager.speak(textToRead) { [self] in
                // Voice finished, wait a moment then auto-advance
                scheduleAutoAdvance(delay: 1.5)
            }
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
        // advance if we're still on it.
        let sourceIndex = currentIndex
        autoAdvanceTimer = Timer.scheduledTimer(withTimeInterval: delay, repeats: false) { _ in
            Task { @MainActor in
                guard currentIndex == sourceIndex else { return }   // manual nav already moved on
                if currentIndex < storyContent.totalCards - 1 {
                    goToNext()
                }
            }
        }
    }

    private func stopAutoAdvanceTimer() {
        autoAdvanceTimer?.invalidate()
        autoAdvanceTimer = nil
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
        if voiceManager.isPlaying {
            voiceManager.pause()
            stopAutoAdvanceTimer()
        } else {
            voiceManager.resume()
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
    .preferredColorScheme(.dark)
}
