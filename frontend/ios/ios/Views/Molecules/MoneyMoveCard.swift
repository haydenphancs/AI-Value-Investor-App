//
//  MoneyMoveCard.swift
//  ios
//
//  Molecule: Card showing a money move with completion + audio indicators
//

import SwiftUI

struct MoneyMoveCard: View {
    let moneyMove: MoneyMove
    var showIcon: Bool = true
    var onTap: (() -> Void)?
    @ObservedObject private var progress = MoneyMovesProgressStore.shared

    var body: some View {
        Button {
            onTap?()
        } label: {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Cover plate, when the article has one.
                //
                // ⚠️ Deliberately NOT gated on `showIcon`. The See-All grid passes
                // `showIcon: false` (MoneyMovesDetailView) to suppress the category badge, so
                // gating artwork on the same flag would leave that entire screen — the one
                // built for browsing — the only place with no pictures.
                if let imageUrl = moneyMove.imageUrl {
                    MoneyMoveCoverImage(
                        url: imageUrl,
                        gradientColors: moneyMove.category.gradientColors,
                        cornerRadius: AppCornerRadius.medium,
                        aspectRatio: 16 / 9
                    )
                } else if showIcon {
                    // The category badge is the fallback identity, not a companion to the
                    // artwork: once a plate is present it says the same thing the plate
                    // already says, in a block that costs 40pt of height on every card.
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(moneyMove.iconFillColor)
                            .frame(width: 36, height: 36)

                        Image(systemName: moneyMove.iconName)
                            .font(AppTypography.iconMedium).fontWeight(.semibold)
                            .foregroundColor(moneyMove.iconFillInk)
                    }
                }

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
                // entirely — narration is on all thirteen articles, so a badge that is always
                // present distinguishes nothing, and the article's own Listen button is the
                // real affordance.
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
