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

    /// Gap left below the capsule. Defaults to the spacing that suits the player floating above a
    /// tab bar; screens that stack it directly on top of the Ask Cay AI bar pass a tighter value,
    /// since the chat bar supplies its own bottom inset and the two otherwise read as far apart.
    var bottomSpacing: CGFloat = AppSpacing.md

    // Layout constants
    private let playerWidthRatio: CGFloat = 0.88 // 88% of screen width
    static let playerHeight: CGFloat = 60
    private let capsuleCornerRadius: CGFloat = 36 // Full capsule effect

    /// What the parked capsule occupies above the bar it floats on: its height plus the default
    /// `bottomSpacing` below it (72pt). Anything pinned to the bottom of a screen the player floats
    /// over has to sit above this — the Reports "Delete" button sat entirely underneath it.
    static let footprint: CGFloat = playerHeight + AppSpacing.md

    /// Room a scroll view under the floating player reserves so its last row can scroll clear of
    /// it: the footprint plus breathing room (88pt). One number for `miniPlayerSafeArea()` and
    /// `GlobalAudioOverlay`, which used to carry 104 and 88 respectively for the same capsule.
    static let contentClearance: CGFloat = footprint + AppSpacing.lg

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
            .frame(width: playerWidth, height: Self.playerHeight)
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
        .shadow(color: AppColors.shadowKey, radius: 24, y: 12)
        // The hit shape is the CAPSULE, set before the gap below it. It was a Rectangle applied
        // after `.padding(.bottom, bottomSpacing)`, which made the transparent strip under the
        // capsule live: a tap aimed at the top edge of the tab bar or the Ask Cay AI field opened
        // the full-screen player instead, and the drag-to-dismiss could start there too.
        .contentShape(RoundedRectangle(cornerRadius: capsuleCornerRadius))
        .onTapGesture {
            audioManager.expandPlayer()
        }
        .padding(.bottom, bottomSpacing)
    }

    // MARK: - Close Button
    private var closeButton: some View {
        Button(action: {
            withAnimation(.spring(response: 0.3)) {
                audioManager.stop()
            }
        }) {
            Image(systemName: "xmark")
                .font(AppTypography.iconSmall).fontWeight(.bold)
                .foregroundColor(AppColors.textPrimary)
                .frame(width: 28, height: 28)
                .hitSlop(reaching: 28)
        }
        .buttonStyle(PlainButtonStyle())
    }

    // MARK: - Waveform Icon
    private var waveformIcon: some View {
        Image(systemName: "chart.bar.fill")
            .font(AppTypography.iconMedium).fontWeight(.medium)
            .foregroundColor(AppColors.textSecondary)
            .frame(width: 24, height: 24)
    }

    // MARK: - Title and Time Section
    private var titleAndTimeSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            if let episode = audioManager.currentEpisode {
                // Episode title (truncated)
                Text(episode.title)
                    .font(AppTypography.bodyEmphasis)
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
                .font(AppTypography.iconMedium).fontWeight(.semibold)
                .foregroundColor(AppColors.textPrimary)
                .frame(width: 32, height: 32)
                .hitSlop(reaching: 32)
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
            PlayPauseCircle(isPlaying: audioManager.isPlaying)
        }
        .buttonStyle(PlainButtonStyle())
    }

    // MARK: - Speed Indicator
    private var speedIndicator: some View {
        Button(action: {
            cyclePlaybackSpeed()
        }) {
            Text(audioManager.playbackSpeed.label)
                .font(AppTypography.labelSmallEmphasis)
                .foregroundColor(AppColors.textPrimary)
                .frame(width: 36, height: 28)
                .hitSlop(reaching: 28)
        }
        .buttonStyle(PlainButtonStyle())
    }

    // MARK: - Capsule Background
    private var capsuleBackground: some View {
        // Was a frozen dark navy (#1A1F2E). Its labels use `textPrimary`, which
        // resolves to near-black in light mode — so the whole player went
        // black-on-navy. It reads as a floating card, so it should BE one: an
        // adaptive surface, with the border carrying the edge in light.
        // Just the surface. `.overlay(AppColors.border)` was NOT a hairline — overlaying a
        // Color fills the WHOLE area, so the capsule rendered as a flat grey wash (#101828 at
        // 14% over #FFFFFF ≈ #DEDFE1 in light) rather than a white card with an edge. The
        // visible edge users actually see comes from the primaryBlue strokeBorder applied at
        // the call site, which is unaffected.
        AppColors.cardBackground
    }

    // MARK: - Progress Bar
    private func progressBar(width: CGFloat) -> some View {
        let barWidth = width - (AppSpacing.lg * 2)
        
        return ZStack(alignment: .leading) {
            // Background track (subtle)
            Rectangle()
                .fill(AppColors.borderStrong)
                .frame(width: barWidth, height: 3)

            // Progress fill
            Rectangle()
                .fill(AppColors.primaryBlue)
                .frame(width: max(0, barWidth * audioManager.progress), height: 3)
                .animation(.linear(duration: 0.1), value: audioManager.progress)
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
            // The user path (persist + sync) — a bare assignment would be reverted by the
            // next launch's hydrate. See `AudioManager.setPlaybackSpeedFromUser`.
            audioManager.setPlaybackSpeedFromUser(speeds[nextIndex])
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
}
