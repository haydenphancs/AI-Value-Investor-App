//
//  GlobalMiniPlayer.swift
//  ios
//
//  Molecule: Floating capsule mini audio player
//  Appears above tab bar when audio is playing, expandable to full screen
//  Optimized for global use with 85-90% screen width
//

import SwiftUI

struct GlobalMiniPlayer: View {
    @EnvironmentObject private var audioManager: AudioManager
    @State private var dragOffset: CGFloat = 0

    // Layout constants
    private let playerWidthRatio: CGFloat = 0.88 // 88% of screen width
    private let playerHeight: CGFloat = 72
    private let capsuleCornerRadius: CGFloat = 36 // Full capsule effect

    // Animation constants
    private let dismissThreshold: CGFloat = 100
    private let expandThreshold: CGFloat = -50

    var body: some View {
        GeometryReader { geometry in
            VStack(spacing: 0) {
                Spacer()

                // Floating capsule mini player
                floatingCapsulePlayer(screenWidth: geometry.size.width)
                    .offset(y: dragOffset)
                    .gesture(
                        DragGesture()
                            .onChanged { value in
                                dragOffset = value.translation.height
                            }
                            .onEnded { value in
                                handleDragEnd(value)
                            }
                    )
                    .animation(.spring(response: 0.3, dampingFraction: 0.8), value: dragOffset)

                // Bottom spacing for tab bar
                Color.clear
                    .frame(height: 0)
            }
            .ignoresSafeArea(.container, edges: .bottom)
        }
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }

    // MARK: - Floating Capsule Player
    private func floatingCapsulePlayer(screenWidth: CGFloat) -> some View {
        let playerWidth = screenWidth * playerWidthRatio

        return ZStack(alignment: .bottom) {
            // Main capsule content
            HStack(spacing: AppSpacing.md) {
                // Close button (X)
                closeButton

                // Waveform icon
                waveformIcon

                // Title and remaining time
                titleAndTimeSection

                // Skip back button
                skipBackButton

                // Play/Pause button
                playPauseButton

                // Speed indicator
                speedIndicator
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .frame(width: playerWidth, height: playerHeight)
            .background(capsuleBackground)
            .overlay(
                // Blue border stroke
                RoundedRectangle(cornerRadius: capsuleCornerRadius)
                    .strokeBorder(AppColors.primaryBlue.opacity(0.5), lineWidth: 1.5)
            )
            .clipShape(RoundedRectangle(cornerRadius: capsuleCornerRadius))

            // Progress bar at bottom (inside capsule)
            progressBar(width: playerWidth)
        }
        .shadow(color: Color.black.opacity(0.4), radius: 24, y: 12)
        .padding(.bottom, AppSpacing.md)
        .contentShape(Rectangle())
        .onTapGesture {
            audioManager.expandPlayer()
        }
    }

    // MARK: - Close Button
    private var closeButton: some View {
        Button(action: {
            withAnimation(.spring(response: 0.3)) {
                audioManager.stop()
            }
        }) {
            Image(systemName: "xmark")
                .font(.system(size: 14, weight: .bold))
                .foregroundColor(AppColors.textPrimary)
                .frame(width: 28, height: 28)
        }
        .buttonStyle(PlainButtonStyle())
    }

    // MARK: - Waveform Icon
    private var waveformIcon: some View {
        Image(systemName: "chart.bar.fill")
            .font(.system(size: 18, weight: .medium))
            .foregroundColor(AppColors.textSecondary)
            .frame(width: 24, height: 24)
    }

    // MARK: - Title and Time Section
    private var titleAndTimeSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            if let episode = audioManager.currentEpisode {
                // Episode title (truncated)
                Text(episode.title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                    .truncationMode(.tail)

                // Remaining time
                Text(formatRemainingTime())
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Skip Back Button
    private var skipBackButton: some View {
        Button(action: {
            withAnimation(.spring(response: 0.2)) {
                audioManager.skipBackward()
            }
        }) {
            Image(systemName: "gobackward.15")
                .font(.system(size: 18, weight: .semibold))
                .foregroundColor(AppColors.textPrimary)
                .frame(width: 32, height: 32)
        }
        .buttonStyle(PlainButtonStyle())
    }

    // MARK: - Play/Pause Button
    private var playPauseButton: some View {
        Button(action: {
            withAnimation(.spring(response: 0.2)) {
                audioManager.togglePlayPause()
            }
        }) {
            ZStack {
                Circle()
                    .fill(AppColors.primaryBlue)
                    .frame(width: 44, height: 44)

                Image(systemName: audioManager.isPlaying ? "pause.fill" : "play.fill")
                    .font(.system(size: 16, weight: .bold))
                    .foregroundColor(.white)
                    .offset(x: audioManager.isPlaying ? 0 : 1)
            }
        }
        .buttonStyle(PlainButtonStyle())
    }

    // MARK: - Speed Indicator
    private var speedIndicator: some View {
        Button(action: {
            cyclePlaybackSpeed()
        }) {
            Text(audioManager.playbackSpeed.label)
                .font(AppTypography.footnoteBold)
                .foregroundColor(AppColors.textPrimary)
                .frame(width: 36, height: 28)
        }
        .buttonStyle(PlainButtonStyle())
    }

    // MARK: - Capsule Background
    private var capsuleBackground: some View {
        Color(hex: "1A1F2E") // Dark navy/charcoal for solid capsule look
    }

    // MARK: - Progress Bar
    private func progressBar(width: CGFloat) -> some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                // Background track (subtle)
                Rectangle()
                    .fill(Color.white.opacity(0.1))
                    .frame(height: 3)

                // Progress fill
                Rectangle()
                    .fill(AppColors.primaryBlue)
                    .frame(width: max(0, (width - AppSpacing.lg * 2) * audioManager.progress), height: 3)
                    .animation(.linear(duration: 0.1), value: audioManager.progress)
            }
        }
        .frame(height: 3)
        .padding(.horizontal, AppSpacing.lg)
        .offset(y: -6) // Position at bottom inside capsule
    }

    // MARK: - Drag Handling
    private func handleDragEnd(_ value: DragGesture.Value) {
        let velocity = value.predictedEndTranslation.height - value.translation.height

        // Swipe up to expand
        if value.translation.height < expandThreshold || velocity < -300 {
            audioManager.expandPlayer()
        }
        // Swipe down to dismiss
        else if value.translation.height > dismissThreshold || velocity > 300 {
            audioManager.stop()
        }

        // Reset offset
        withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
            dragOffset = 0
        }
    }

    // MARK: - Helpers

    private func formatRemainingTime() -> String {
        let remaining = audioManager.remainingTime
        let minutes = Int(remaining) / 60
        let seconds = Int(remaining) % 60
        return String(format: "%d:%02d Remaining...", minutes, seconds)
    }

    private func cyclePlaybackSpeed() {
        let speeds = PlaybackSpeed.allCases
        if let currentIndex = speeds.firstIndex(of: audioManager.playbackSpeed) {
            let nextIndex = (currentIndex + 1) % speeds.count
            audioManager.playbackSpeed = speeds[nextIndex]
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            Text("Main Content")
                .foregroundColor(AppColors.textPrimary)
            Spacer()
        }

        GlobalMiniPlayer()
            .environmentObject(AudioManager.shared)
    }
    .onAppear {
        AudioManager.shared.play(.sampleMoneyMoves)
    }
    .preferredColorScheme(.dark)
}
