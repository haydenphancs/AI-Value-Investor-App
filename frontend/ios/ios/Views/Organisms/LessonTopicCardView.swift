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

    @StateObject private var voiceManager = AIVoiceManager.shared
    @State private var currentIndex: Int = 0
    @State private var cardProgress: CGFloat = 0
    @State private var dragOffset: CGFloat = 0

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

                    // Progress bar
                    LessonStoryProgressBar(
                        currentIndex: currentIndex,
                        totalCount: storyContent.totalCards,
                        currentProgress: cardProgress
                    )
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.top, AppSpacing.md)

                    // Card content
                    ZStack {
                        cardContentView
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)

                    // Bottom section with orb and controls (not on completion card)
                    if !isCompletionCard {
                        bottomControlsView
                            .padding(.bottom, AppSpacing.xxxl)
                    }
                }

                // Tap zones for navigation
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
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(AppColors.textSecondary)
                .tracking(0.5)

            Spacer()

            Button(action: {
                voiceManager.stop()
                onDismiss?()
            }) {
                Image(systemName: "xmark")
                    .font(.system(size: 16, weight: .semibold))
                    .foregroundColor(AppColors.textSecondary)
                    .frame(width: 32, height: 32)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    @ViewBuilder
    private var cardContentView: some View {
        switch currentCard.cardType {
        case .title:
            LessonTitleCard(
                title: currentCard.title ?? "",
                subtitleSegments: currentCard.subtitleSegments ?? [],
                currentWordRange: voiceManager.currentWordRange,
                isReading: voiceManager.isPlaying
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
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                }
            }
        }
    }

    // MARK: - Voice Reading

    private func startReadingCurrentCard() {
        stopAutoAdvanceTimer()

        guard !isCompletionCard else {
            // No voice for completion card
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

        // Start speaking with completion handler
        voiceManager.speak(textToRead) { [self] in
            // Voice finished, wait a moment then auto-advance
            scheduleAutoAdvance(delay: 1.5)
        }
    }

    private func scheduleAutoAdvance(delay: TimeInterval) {
        stopAutoAdvanceTimer()

        autoAdvanceTimer = Timer.scheduledTimer(withTimeInterval: delay, repeats: false) { _ in
            Task { @MainActor in
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
