//
//  AudioStatusIsland.swift
//  ios
//
//  The AI chat screen's audio player: a small pill centred in the chat's top bar, between the
//  history (☰) and close (✕) buttons, while an episode is active.
//
//  It used to float at the top of every screen that hid the mini player for an Ask Cay AI bar,
//  on iPhones without a Dynamic Island only — over the stock header's Search and bell buttons, and
//  on the assumption that a Dynamic Island shows Now Playing while the app is open, which it does
//  not. Those screens carry `ChatBarAudioControl` beside their bar now; the chat screen, whose top
//  bar has the room, carries this.
//

import SwiftUI

struct AudioStatusIsland: View {
    /// Observed directly rather than via `@EnvironmentObject`, like `ChatBarAudioControl`: a
    /// missing environment object is a crash rather than a fallback. Observing HERE (not in the
    /// chat screen) also keeps the playback clock from re-rendering the whole conversation.
    @ObservedObject private var audioManager = AudioManager.shared

    // Layout constants
    private let pillHeight: CGFloat = 36
    /// The play/pause disc, concentric with the pill: 36pt pill, 28pt disc, 4pt margin.
    private let discDiameter: CGFloat = 28

    var body: some View {
        // A container that persists, so the pill's arrival and departure animate.
        ZStack {
            if audioManager.hasActiveEpisode {
                pill
                    .transition(.opacity.combined(with: .scale(scale: 0.95)))
            }
        }
        .animation(.spring(response: 0.3, dampingFraction: 0.85), value: audioManager.hasActiveEpisode)
    }

    private var pill: some View {
        HStack(spacing: AppSpacing.sm) {
            // Tiny animated waveform
            WaveformIndicator(isPlaying: audioManager.isPlaying)
                .frame(width: 20, height: 16)

            // Episode title (truncated)
            if let episode = audioManager.currentEpisode {
                Text(episode.title)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: 120)
            }

            // The mini player's disc, scaled to the pill. A drawing, not a Button — the whole pill
            // is the control, so the target is the pill rather than a 20pt glyph.
            PlayPauseCircle(
                isPlaying: audioManager.isPlaying,
                diameter: discDiameter,
                glyphFont: AppTypography.iconXS
            )
        }
        .padding(.leading, AppSpacing.md)
        .padding(.trailing, (pillHeight - discDiameter) / 2)
        .frame(height: pillHeight)
        .background(
            Capsule()
                .fill(AppColors.cardBackground)
                .shadow(color: AppColors.shadowKey, radius: 8, y: 4)
        )
        .overlay(
            Capsule()
                .strokeBorder(AppColors.border, lineWidth: 0.5)
        )
        // Hit area to Apple's 44pt minimum; the drawing stays 36pt.
        .padding(.vertical, (HitSlop.minimumTarget - pillHeight) / 2)
        .contentShape(Rectangle())
        // Tap = play/pause, press and hold = the full-screen player — the same two gestures as
        // `ChatBarAudioControl`. Not a Button, which would also fire on the hold's touch-up.
        .onTapGesture {
            withAnimation(.spring(response: 0.2)) {
                audioManager.togglePlayPause()
            }
        }
        .onLongPressGesture(minimumDuration: 0.4) {
            Haptics.impact(.light)
            audioManager.expandPlayer()
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spokenLabel)
        .accessibilityAddTraits(.isButton)
        .accessibilityAction {
            audioManager.togglePlayPause()
        }
        .accessibilityAction(named: "Open player") {
            audioManager.expandPlayer()
        }
    }

    private var spokenLabel: String {
        let action = audioManager.isPlaying ? "Pause" : "Play"
        guard let title = audioManager.currentEpisode?.title else { return "\(action) narration" }
        return "\(action) \(title)"
    }
}

// MARK: - Tiny Waveform Indicator
private struct WaveformIndicator: View {
    let isPlaying: Bool

    @State private var animationPhase: CGFloat = 0

    private let barCount = 3
    private let barWidth: CGFloat = 2
    private let barSpacing: CGFloat = 2

    var body: some View {
        HStack(spacing: barSpacing) {
            ForEach(0..<barCount, id: \.self) { index in
                WaveformBar(
                    isPlaying: isPlaying,
                    delay: Double(index) * 0.15,
                    phase: animationPhase
                )
                .frame(width: barWidth)
            }
        }
        .onAppear {
            if isPlaying {
                startAnimation()
            }
        }
        .onChange(of: isPlaying) { oldValue, newValue in
            if newValue {
                startAnimation()
            }
        }
    }

    private func startAnimation() {
        withAnimation(.linear(duration: 0.8).repeatForever(autoreverses: false)) {
            animationPhase = 1
        }
    }
}

// MARK: - Individual Waveform Bar
private struct WaveformBar: View {
    let isPlaying: Bool
    let delay: Double
    let phase: CGFloat

    @State private var height: CGFloat = 4

    var body: some View {
        RoundedRectangle(cornerRadius: 1)
            .fill(AppColors.primaryBlue)
            .frame(height: height)
            .onAppear {
                if isPlaying {
                    animateBar()
                }
            }
            .onChange(of: isPlaying) { oldValue, newValue in
                if newValue {
                    animateBar()
                } else {
                    withAnimation(.easeOut(duration: 0.2)) {
                        height = 4
                    }
                }
            }
    }

    private func animateBar() {
        let minHeight: CGFloat = 4
        let maxHeight: CGFloat = 14

        withAnimation(
            .easeInOut(duration: 0.4)
            .repeatForever(autoreverses: true)
            .delay(delay)
        ) {
            height = CGFloat.random(in: minHeight...maxHeight)
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            // As the chat screen's top bar lays it out: centred between ☰ and ✕.
            HStack {
                Image(systemName: "line.3.horizontal")
                Spacer()
                Image(systemName: "xmark")
            }
            .foregroundColor(AppColors.textSecondary)
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .overlay { AudioStatusIsland() }

            Spacer()
        }
    }
    .onAppear {
        AudioManager.shared.load(.sampleMoneyMoves)
        AudioManager.shared.resume()
    }
}

#Preview("Paused State") {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            AudioStatusIsland()
                .padding(.top, 60)

            Spacer()
        }
    }
    .onAppear {
        // Start then pause: `load` alone leaves the episode merely PREPARED, which no longer mounts
        // the player (see AudioManager.hasActiveEpisode). "Paused" here means paused BY THE USER.
        AudioManager.shared.load(.sampleMoneyMoves)
        AudioManager.shared.resume()
        AudioManager.shared.pause()
    }
}
