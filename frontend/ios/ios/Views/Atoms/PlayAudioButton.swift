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
    @ObservedObject private var entitlement = LearnAudioEntitlement.shared
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
                // Narration is Pro/Max. The engine refuses a locked episode and raises the
                // paywall (see LearnAudioEntitlement), so the control stays tappable — it just
                // has to LOOK like an offer rather than a broken play button.
                Image(systemName: isLocked ? "lock.fill" : (isPlaying ? "pause.fill" : "play.fill"))
                    .font(.system(size: size.iconSize, weight: .semibold))
                    .offset(x: (isPlaying || isLocked) ? 0 : 1)

                if style != .minimal {
                    Text(buttonLabel)
                        .font(AppTypography.bodySmallEmphasis)
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

    /// Whether this episode's narration is behind the caller's plan.
    private var isLocked: Bool { entitlement.isLocked(episode) }

    private var buttonLabel: String {
        if isLocked {
            return "Listen"          // the offer, not an error — the tap opens the plan sheet
        } else if isPlaying {
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
            return AppColors.textOnAccent
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
                colors: [AppColors.primaryBlue, AppColors.aiRampStart],
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
    @ObservedObject private var entitlement = LearnAudioEntitlement.shared
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
                        .fill(AppColors.mediaSurface)
                        .frame(width: 48, height: 48)

                    Image(systemName: entitlement.isLocked(episode)
                          ? "lock.fill" : (isPlaying ? "pause.fill" : "play.fill"))
                        .font(AppTypography.iconMedium).fontWeight(.bold)
                        // NOT `AppColors.background`: that is #F4F5F8 in light, so this
                        // play triangle measured 1.09:1 on its own white circle and was
                        // simply invisible on every Money Moves hero in light mode.
                        .foregroundColor(AppColors.textOnMediaSurface)
                        .offset(x: isPlaying ? 0 : 2)
                }

                if showLabel {
                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        // Sits on the article hero gradient, not on the page — so this is
                        // on-accent ink, constant in both appearances.
                        Text(entitlement.isLocked(episode)
                             ? "Listen with Pro" : (isPlaying ? "Now Playing" : "Listen Now"))
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textOnAccent)

                        Text(episode.formattedDuration)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textOnAccent.opacity(0.8))
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
}
