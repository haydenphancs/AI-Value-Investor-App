//
//  ArticleActionBar.swift
//  ios
//
//  Molecule: Action bar with article interaction buttons
//  Integrates with AudioManager for audio playback
//

import SwiftUI

struct ArticleActionBar: View {
    @EnvironmentObject private var audioManager: AudioManager

    var audioEpisode: AudioEpisode?
    var hasAudioVersion: Bool = true
    var isBookmarked: Bool = false
    var onShareTapped: (() -> Void)?
    var onBookmarkTapped: (() -> Void)?
    var onMoreTapped: (() -> Void)?

    private var isCurrentEpisode: Bool {
        guard let episode = audioEpisode else { return false }
        return audioManager.currentEpisode?.id == episode.id
    }

    private var isPlaying: Bool {
        isCurrentEpisode && audioManager.isPlaying
    }

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Listen button (if audio available)
            if hasAudioVersion, let episode = audioEpisode {
                PlayAudioButton(episode: episode, style: .primary, size: .medium)
            }

            Spacer()

            // Right side actions
            HStack(spacing: AppSpacing.lg) {
                // Add to queue (if audio available and not currently playing)
                if hasAudioVersion, let episode = audioEpisode, !isCurrentEpisode {
                    Button(action: {
                        audioManager.addToQueue(episode)
                    }) {
                        Image(systemName: "text.badge.plus")
                            .font(.system(size: 18, weight: .medium))
                            .foregroundColor(AppColors.textSecondary)
                    }
                    .buttonStyle(PlainButtonStyle())
                }

                // Share
                Button(action: { onShareTapped?() }) {
                    Image(systemName: "square.and.arrow.up")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())

                // Bookmark
                Button(action: { onBookmarkTapped?() }) {
                    Image(systemName: isBookmarked ? "bookmark.fill" : "bookmark")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(isBookmarked ? AppColors.primaryBlue : AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())

                // More options
                Button(action: { onMoreTapped?() }) {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundColor(AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(
            ZStack {
                // Glassmorphism background
                VisualEffectBlur(blurStyle: .systemThinMaterialDark)

                // Gradient overlay
                LinearGradient(
                    colors: [
                        Color.white.opacity(0.05),
                        Color.white.opacity(0.02)
                    ],
                    startPoint: .top,
                    endPoint: .bottom
                )
            }
            .shadow(color: Color.black.opacity(0.3), radius: 8, y: -4)
        )
    }
}

// MARK: - Preview
#Preview {
    VStack {
        Spacer()
        ArticleActionBar(
            audioEpisode: .sampleMoneyMoves,
            hasAudioVersion: true,
            isBookmarked: false
        )
        ArticleActionBar(
            audioEpisode: nil,
            hasAudioVersion: false,
            isBookmarked: true
        )
    }
    .background(AppColors.background)
    .environmentObject(AudioManager.shared)
    .preferredColorScheme(.dark)
}
