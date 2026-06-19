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
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                // Header: leading icon/audio slot + completion mark (centered on the badge).
                HStack(alignment: .center) {
                    if showIcon {
                        ZStack {
                            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                .fill(moneyMove.iconBackgroundColor)
                                .frame(width: 40, height: 40)

                            Image(systemName: moneyMove.iconName)
                                .font(AppTypography.iconMedium).fontWeight(.semibold)
                                .foregroundColor(.white)
                        }
                    } else {
                        // Compact headphones badge on narrated moves (See-All). Reserve the slot so
                        // audio / non-audio cards stay aligned.
                        ZStack {
                            if moneyMove.hasAudio {
                                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                    .fill(AppColors.cardBackgroundLight)

                                Image(systemName: "headphones")
                                    .font(AppTypography.iconSmall).fontWeight(.semibold)
                                    .foregroundColor(AppColors.textSecondary)
                            }
                        }
                        .frame(width: 28, height: 28)
                    }

                    Spacer()

                    // Completion status (read-only): filled green check once done, else a muted
                    // hollow circle. The Complete action lives at the end of the article, not here.
                    Image(systemName: progress.isCompleted(slug: moneyMove.slug) ? "checkmark.circle.fill" : "circle")
                        .font(AppTypography.iconSmall).fontWeight(.semibold)
                        .foregroundColor(progress.isCompleted(slug: moneyMove.slug) ? AppColors.bullish : AppColors.textMuted)
                }

                // Title
                Text(moneyMove.title)
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)

                // Subtitle
                Text(moneyMove.subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)

                Spacer(minLength: 0)

                // Meta info
                HStack(spacing: AppSpacing.lg) {
                    ReadTimeLabel(minutes: moneyMove.estimatedMinutes)
                    LearnerCountBadge(count: moneyMove.learnerCount)
                }
            }
            .padding(AppSpacing.lg)
            .frame(width: 200)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.extraLarge)
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
    .preferredColorScheme(.dark)
}
