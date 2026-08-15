//
//  MoneyMoveCard.swift
//  ios
//
//  Molecule: Card showing a money move with completion + audio indicators
//

import SwiftUI

struct MoneyMoveCard: View {
    let moneyMove: MoneyMove
    var onTap: (() -> Void)?
    @ObservedObject private var progress = MoneyMovesProgressStore.shared

    var body: some View {
        Button {
            onTap?()
        } label: {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // ALWAYS a cover plate — never a conditional, never a category badge.
                //
                // MoneyMoveCoverImage already degrades to the category gradient + grain when
                // the url is nil, so "no artwork yet", "still loading" and "artwork arrived"
                // all render the same 16:9 block. That is the whole point: the BUNDLED
                // money_moves.json carries no image urls at all, so until the backend prefetch
                // lands every card on the See-All screen used to start straight at its title
                // — no picture, no badge, because that screen also passed `showIcon: false` —
                // and then REFLOW when the art arrived. A card that changes height under the
                // reader's thumb is worse than a plain plate.
                //
                // The `showIcon` parameter died here: with the badge branch gone it had no
                // reader, and a parameter that is accepted and ignored lies to its callers.
                MoneyMoveCoverImage(
                    url: moneyMove.imageUrl,
                    gradientColors: moneyMove.category.gradientColors,
                    cornerRadius: AppCornerRadius.medium,
                    aspectRatio: 16 / 9
                )

                // Title
                Text(moneyMove.title)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                // Subtitle
                Text(moneyMove.subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                Spacer(minLength: 0)

                // One meta row carrying everything: read time, optional learner count, and the
                // completion mark pushed to the trailing edge.
                //
                // The completion mark used to sit in its own header row opposite a headphones
                // badge, which cost a whole 28-40pt band for two glyphs. The headphones went
                // entirely — narration is on every article, so a badge that is always present
                // distinguishes nothing, and the article's own Listen button is the real
                // affordance. (`MoneyMove.hasAudio` went with it — nothing read it after.)
                HStack(spacing: AppSpacing.md) {
                    ReadTimeLabel(minutes: moneyMove.estimatedMinutes)
                    // Only when there is a real count to show.
                    if !moneyMove.learnerCount.isEmpty {
                        LearnerCountBadge(count: moneyMove.learnerCount)
                    }

                    Spacer(minLength: 0)

                    // Completion status (read-only): filled green check once done, else a muted
                    // hollow circle. The Complete action lives at the end of the article.
                    Image(systemName: progress.isCompleted(slug: moneyMove.slug)
                          ? "checkmark.circle.fill" : "circle")
                        .font(AppTypography.iconSmall).fontWeight(.semibold)
                        .foregroundColor(progress.isCompleted(slug: moneyMove.slug)
                                         ? AppColors.bullish : AppColors.textMuted)
                }
            }
            .padding(AppSpacing.md)
            .frame(width: 200)
            .cardSurface(cornerRadius: AppCornerRadius.extraLarge)
            .contentShape(RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge))
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: AppSpacing.lg) {
            ForEach(MoneyMove.sampleData) { moneyMove in
                MoneyMoveCard(moneyMove: moneyMove)
            }
        }
        .padding()
    }
    .background(AppColors.background)
}
