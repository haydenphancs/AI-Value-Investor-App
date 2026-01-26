//
//  LessonTopicCardView.swift
//  ios
//
//  Organism: Full-screen lesson story card view with swipeable cards,
//  AI voice orb, and progress tracking (Instagram Stories style)
//

import SwiftUI

struct LessonTopicCardView: View {
    let storyContent: LessonStoryContent
    var onDismiss: (() -> Void)?
    var onCTATapped: ((LessonCTADestination) -> Void)?

    @State private var currentIndex: Int = 0
    @State private var isPlaying: Bool = true
    @State private var cardProgress: CGFloat = 0
    @State private var dragOffset: CGFloat = 0

    // Timer for auto-advance
    @State private var timer: Timer?
    private let cardDuration: TimeInterval = 8.0  // seconds per card

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
            startTimer()
        }
        .onDisappear {
            stopTimer()
        }
    }

    // MARK: - Computed Properties

    private var currentCard: LessonTopicCard {
        storyContent.cards[currentIndex]
    }

    private var isCompletionCard: Bool {
        currentCard.cardType == .completion
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
                subtitleSegments: currentCard.subtitleSegments ?? []
            )
            .transition(.opacity.combined(with: .scale(scale: 0.95)))

        case .content:
            LessonContentCard(
                imageName: currentCard.imageName,
                contentSegments: currentCard.contentSegments ?? []
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
            // AI Voice Orb
            AIVoiceOrb(isAnimating: isPlaying, size: 100)

            // Play/Pause button
            Button(action: {
                togglePlayPause()
            }) {
                ZStack {
                    Circle()
                        .fill(Color.white.opacity(0.1))
                        .frame(width: 44, height: 44)

                    Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(AppColors.textPrimary)
                }
            }
        }
    }

    // MARK: - Navigation

    private func goToNext() {
        guard currentIndex < storyContent.totalCards - 1 else {
            // Already at last card
            return
        }

        withAnimation(.easeInOut(duration: 0.3)) {
            currentIndex += 1
            cardProgress = 0
        }

        restartTimer()
    }

    private func goToPrevious() {
        guard currentIndex > 0 else {
            // Reset current card progress
            cardProgress = 0
            restartTimer()
            return
        }

        withAnimation(.easeInOut(duration: 0.3)) {
            currentIndex -= 1
            cardProgress = 0
        }

        restartTimer()
    }

    private func handleDragEnd(value: DragGesture.Value) {
        let threshold: CGFloat = 50

        if value.translation.width < -threshold {
            // Swipe left - go forward
            goToNext()
        } else if value.translation.width > threshold {
            // Swipe right - go back
            goToPrevious()
        }

        dragOffset = 0
    }

    // MARK: - Timer Management

    private func startTimer() {
        guard !isCompletionCard else { return }

        stopTimer()
        cardProgress = 0

        timer = Timer.scheduledTimer(withTimeInterval: 0.05, repeats: true) { _ in
            if isPlaying {
                let increment = 0.05 / cardDuration
                cardProgress += increment

                if cardProgress >= 1.0 {
                    goToNext()
                }
            }
        }
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    private func restartTimer() {
        if !isCompletionCard {
            startTimer()
        } else {
            stopTimer()
        }
    }

    private func togglePlayPause() {
        isPlaying.toggle()
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
