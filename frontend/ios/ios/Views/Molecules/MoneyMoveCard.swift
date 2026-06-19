//
//  MoneyMoveCard.swift
//  ios
//
//  Molecule: Card showing a money move with completion + audio indicators
//

import SwiftUI
import UIKit

struct MoneyMoveCard: View {
    let moneyMove: MoneyMove
    var showIcon: Bool = true
    var onTap: (() -> Void)?
    @ObservedObject private var progress = MoneyMovesProgressStore.shared

    private var isCompleted: Bool {
        progress.isCompleted(slug: moneyMove.slug)
    }

    // The card itself uses onTapGesture (not a Button) so the nested "Complete" button below
    // reliably receives its own taps — the proven LibraryBookCard pattern.
    var body: some View {
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

                // Completion mark: filled green check once read/heard, else a muted hollow circle.
                Image(systemName: isCompleted ? "checkmark.circle.fill" : "circle")
                    .font(AppTypography.iconSmall).fontWeight(.semibold)
                    .foregroundColor(isCompleted ? AppColors.bullish : AppColors.textMuted)
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

            // Mark-complete affordance. Reading or finishing the narration completes it too;
            // a completed move sorts to the end of the row.
            completionButton
        }
        .padding(AppSpacing.lg)
        .frame(width: 200)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.extraLarge)
        .contentShape(RoundedRectangle(cornerRadius: AppCornerRadius.extraLarge))
        .onTapGesture { onTap?() }
    }

    // MARK: - Complete affordance
    @ViewBuilder
    private var completionButton: some View {
        if moneyMove.slug.isEmpty {
            // Placeholder card with no backend slug — completion can't be tracked, so no button.
            EmptyView()
        } else if isCompleted {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "checkmark.circle.fill")
                    .font(AppTypography.captionEmphasis)
                Text("Completed")
                    .font(AppTypography.bodySmallEmphasis)
            }
            .foregroundColor(AppColors.bullish)
            .frame(maxWidth: .infinity)
            .padding(.vertical, AppSpacing.sm)
            .background(AppColors.bullish.opacity(0.12))
            .cornerRadius(AppCornerRadius.medium)
        } else {
            Button {
                MoneyMovesProgressStore.shared.markCompleted(slug: moneyMove.slug)
                UINotificationFeedbackGenerator().notificationOccurred(.success)
            } label: {
                Text("Complete")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.bullish)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.sm)
                    .background(AppColors.bullish.opacity(0.15))
                    .cornerRadius(AppCornerRadius.medium)
            }
            .buttonStyle(.plain)
        }
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
