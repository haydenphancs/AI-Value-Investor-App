//
//  MoneyMoveArticleHeroHeader.swift
//  ios
//
//  Organism: Hero header section for a Money Moves article.
//  Integrates with AudioManager for audio playback.
//
//  THE HEADLINE SITS BELOW THE ARTWORK, NOT ON IT. This used to be a full-bleed 380pt
//  gradient with the category, title, subtitle and Listen button drawn over it in
//  `AppColors.textOnAccent` (white). Two things forced the change:
//
//   1. It was broken in LIGHT mode, and had been since light mode shipped. The bottom 100pt
//      faded to `AppColors.background` — #F4F5F8 in light — while the meta row sat 20pt from
//      the bottom, i.e. white type dissolving into a near-white fade. A saturated gradient
//      masked how bad it was; a photograph would not have.
//   2. Cover artwork cannot be constrained to stay dark. Eight of the thirteen plates have a
//      near-white ground (a specimen plate, seamless white, aged ledger paper), and any rule
//      that kept them dark enough for white type would have thrown away the whole editorial
//      range. Moving the type off the picture removes the constraint entirely rather than
//      negotiating with it.
//
//  So the art is a contained rounded card and the type is ordinary content on the page
//  background, inheriting `textPrimary`/`textSecondary` and therefore correct in both
//  appearances by construction rather than by measurement.
//

import SwiftUI

struct MoneyMoveArticleHeroHeader: View {
    @EnvironmentObject private var audioManager: AudioManager

    let article: MoneyMoveArticle
    var audioEpisode: AudioEpisode?
    var onBackTapped: (() -> Void)?
    var onShareTapped: (() -> Void)?

    private var isCurrentlyPlaying: Bool {
        guard let episode = audioEpisode else { return false }
        return audioManager.currentEpisode?.id == episode.id && audioManager.isPlaying
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            navigationBar

            // The artwork. 16:9 and contained, so a light-ground plate keeps its hairline
            // and a dark one keeps its own edge — see MoneyMoveCoverImage.
            MoneyMoveCoverImage(
                url: article.imageUrl,
                gradientColors: article.heroGradientColors,
                cornerRadius: AppCornerRadius.extraLarge,
                aspectRatio: 16 / 9
            )
            .padding(.horizontal, AppSpacing.lg)
            .padding(.top, AppSpacing.md)

            headline
        }
    }

    // MARK: - Navigation

    private var navigationBar: some View {
        HStack {
            Button(action: { onBackTapped?() }) {
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "chevron.left")
                        .font(AppTypography.iconDefault).fontWeight(.semibold)
                    Text("Back")
                        .font(AppTypography.body)
                }
                // Ordinary page chrome now that it sits on the page background rather than
                // on a saturated gradient — so it takes the page's own ink, not textOnAccent.
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(Capsule().fill(AppColors.cardBackground))
            }
            .buttonStyle(PlainButtonStyle())

            Spacer()

            Button(action: { onShareTapped?() }) {
                Image(systemName: "square.and.arrow.up")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
                    .frame(width: 36, height: 36)
                    .background(Circle().fill(AppColors.cardBackground))
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.md)
    }

    // MARK: - Headline block

    private var headline: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text(article.isFeatured ? "FEATURED DEEP DIVE" : article.category.rawValue.uppercased())
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.primaryBlue)
                .tracking(1.2)

            Text(article.title)
                .font(AppTypography.titleLarge)
                .foregroundColor(AppColors.textPrimary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)

            Text(article.subtitle)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)

            HStack(spacing: AppSpacing.lg) {
                if article.hasAudioVersion, let episode = audioEpisode {
                    LargePlayButton(episode: episode, showLabel: true)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: AppSpacing.xs) {
                    HStack(spacing: AppSpacing.md) {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "clock")
                                .font(AppTypography.captionEmphasis).fontWeight(.medium)
                            Text(article.formattedReadTime)
                                .font(AppTypography.caption)
                        }

                        // Views — hidden when we have no measured count.
                        if !article.viewCount.isEmpty {
                            HStack(spacing: AppSpacing.xs) {
                                Image(systemName: "eye")
                                    .font(AppTypography.captionEmphasis).fontWeight(.medium)
                                Text(article.viewCount)
                                    .font(AppTypography.caption)
                            }
                        }
                    }
                    .foregroundColor(AppColors.textSecondary)

                    Text(article.formattedDate)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            if isCurrentlyPlaying {
                HStack(spacing: AppSpacing.sm) {
                    NowPlayingBars()
                    Text("Now Playing")
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                }
                .padding(.top, AppSpacing.xs)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
    }
}

// MARK: - Now Playing Animation Bars
/// The four bouncing bars beside "Now Playing".
///
/// Filled with `textPrimary`, not `textOnAccent`: this sits on the page background now that
/// the headline moved off the artwork, and `textOnAccent` is white in both appearances —
/// invisible on the #F4F5F8 light page. Its only caller is the hero header directly above.
struct NowPlayingBars: View {
    @State private var isAnimating = false

    var body: some View {
        HStack(spacing: 2) {
            ForEach(0..<4) { index in
                RoundedRectangle(cornerRadius: 1)
                    .fill(AppColors.textPrimary)
                    .frame(width: 3, height: isAnimating ? CGFloat.random(in: 8...16) : 4)
                    .animation(
                        .easeInOut(duration: 0.4)
                            .repeatForever()
                            .delay(Double(index) * 0.1),
                        value: isAnimating
                    )
            }
        }
        .frame(height: 16)
        .onAppear {
            isAnimating = true
        }
    }
}

#Preview {
    ScrollView {
        MoneyMoveArticleHeroHeader(
            article: MoneyMoveArticle.sampleDigitalFinance,
            audioEpisode: .sampleMoneyMoves
        )

        Text("Content goes here")
            .foregroundColor(AppColors.textPrimary)
            .padding()
    }
    .background(AppColors.background)
    .ignoresSafeArea()
    .environmentObject(AudioManager.shared)
}
