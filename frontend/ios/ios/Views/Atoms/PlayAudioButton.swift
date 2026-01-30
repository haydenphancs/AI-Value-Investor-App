//
//  PlayAudioButton.swift
//  ios
//
//  Atom: Play audio button that integrates with AudioManager
//  Displays play/pause state and triggers audio playback
//

import SwiftUI

struct PlayAudioButton: View {
    @EnvironmentObject private var audioManager: AudioManager
    let episode: AudioEpisode
    var style: Style = .primary
    var size: Size = .medium

    enum Style {
        case primary   // Filled blue button
        case secondary // Outlined button
        case minimal   // Just icon
    }

    enum Size {
        case small
        case medium
        case large

        var iconSize: CGFloat {
            switch self {
            case .small: return 12
            case .medium: return 14
            case .large: return 18
            }
        }

        var padding: (h: CGFloat, v: CGFloat) {
            switch self {
            case .small: return (AppSpacing.md, AppSpacing.sm)
            case .medium: return (AppSpacing.lg, AppSpacing.sm)
            case .large: return (AppSpacing.xl, AppSpacing.md)
            }
        }
    }

    private var isCurrentEpisode: Bool {
        audioManager.currentEpisode?.id == episode.id
    }

    private var isPlaying: Bool {
        isCurrentEpisode && audioManager.isPlaying
    }

    var body: some View {
        Button(action: handleTap) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                    .font(.system(size: size.iconSize, weight: .semibold))
                    .offset(x: isPlaying ? 0 : 1)

                if style != .minimal {
                    Text(buttonLabel)
                        .font(AppTypography.calloutBold)
                }
            }
            .foregroundColor(foregroundColor)
            .padding(.horizontal, size.padding.h)
            .padding(.vertical, size.padding.v)
            .background(background)
            .clipShape(Capsule())
        }
        .buttonStyle(PlainButtonStyle())
    }

    private var buttonLabel: String {
        if isPlaying {
            return "Pause"
        } else if isCurrentEpisode && audioManager.playbackState == .paused {
            return "Resume"
        } else {
            return "Listen"
        }
    }

    private var foregroundColor: Color {
        switch style {
        case .primary:
            return .white
        case .secondary:
            return AppColors.primaryBlue
        case .minimal:
            return AppColors.textPrimary
        }
    }

    @ViewBuilder
    private var background: some View {
        switch style {
        case .primary:
            LinearGradient(
                colors: [AppColors.primaryBlue, Color(hex: "6366F1")],
                startPoint: .leading,
                endPoint: .trailing
            )
        case .secondary:
            Capsule()
                .strokeBorder(AppColors.primaryBlue, lineWidth: 1.5)
        case .minimal:
            Color.clear
        }
    }

    private func handleTap() {
        if isCurrentEpisode {
            audioManager.togglePlayPause()
        } else {
            audioManager.play(episode)
        }
    }
}

// MARK: - Large Play Button (for hero cards)
struct LargePlayButton: View {
    @EnvironmentObject private var audioManager: AudioManager
    let episode: AudioEpisode
    var showLabel: Bool = true

    private var isCurrentEpisode: Bool {
        audioManager.currentEpisode?.id == episode.id
    }

    private var isPlaying: Bool {
        isCurrentEpisode && audioManager.isPlaying
    }

    var body: some View {
        Button(action: handleTap) {
            HStack(spacing: AppSpacing.md) {
                ZStack {
                    Circle()
                        .fill(.white)
                        .frame(width: 48, height: 48)

                    Image(systemName: isPlaying ? "pause.fill" : "play.fill")
                        .font(.system(size: 18, weight: .bold))
                        .foregroundColor(AppColors.background)
                        .offset(x: isPlaying ? 0 : 2)
                }

                if showLabel {
                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text(isPlaying ? "Now Playing" : "Listen Now")
                            .font(AppTypography.bodyBold)
                            .foregroundColor(.white)

                        Text(episode.formattedDuration)
                            .font(AppTypography.caption)
                            .foregroundColor(.white.opacity(0.8))
                    }
                }
            }
        }
        .buttonStyle(PlainButtonStyle())
    }

    private func handleTap() {
        if isCurrentEpisode {
            audioManager.togglePlayPause()
        } else {
            audioManager.play(episode)
        }
    }
}

// MARK: - Preview
#Preview {
    VStack(spacing: AppSpacing.xl) {
        // Primary styles
        HStack(spacing: AppSpacing.lg) {
            PlayAudioButton(episode: .sampleMoneyMoves, style: .primary, size: .small)
            PlayAudioButton(episode: .sampleMoneyMoves, style: .primary, size: .medium)
            PlayAudioButton(episode: .sampleMoneyMoves, style: .primary, size: .large)
        }

        // Secondary style
        PlayAudioButton(episode: .sampleMoneyMoves, style: .secondary)

        // Minimal style
        PlayAudioButton(episode: .sampleMoneyMoves, style: .minimal)

        // Large play button
        ZStack {
            LinearGradient(
                colors: [Color(hex: "1E3A5F"), Color(hex: "0D1B2A")],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
            .frame(height: 120)
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))

            LargePlayButton(episode: .sampleMoneyMoves)
        }
    }
    .padding()
    .background(AppColors.background)
    .environmentObject(AudioManager.shared)
    .preferredColorScheme(.dark)
}
