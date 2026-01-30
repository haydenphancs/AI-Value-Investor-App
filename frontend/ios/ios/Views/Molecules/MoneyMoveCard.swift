//
//  MoneyMoveCard.swift
//  ios
//
//  Molecule: Card showing a money move with bookmark functionality
//

import SwiftUI

struct MoneyMoveCard: View {
    let moneyMove: MoneyMove
    var showIcon: Bool = true
    var onTap: (() -> Void)?
    var onBookmark: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header with optional icon and bookmark
            HStack(alignment: .top) {
                // Icon (conditional)
                if showIcon {
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(moneyMove.iconBackgroundColor)
                            .frame(width: 40, height: 40)

                        Image(systemName: moneyMove.iconName)
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundColor(.white)
                    }
                }

                Spacer()

                // Bookmark button
                BookmarkButton(isBookmarked: moneyMove.isBookmarked) {
                    onBookmark?()
                }
            }

            // Title
            Text(moneyMove.title)
                .font(AppTypography.headline)
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
        .onTapGesture {
            onTap?()
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
