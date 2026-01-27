//
//  MoneyMoveCard.swift
//  ios
//
//  Molecule: Card showing a money move with bookmark functionality
//

import SwiftUI

struct MoneyMoveCard: View {
    let moneyMove: MoneyMove
    var onTap: (() -> Void)?
    var onBookmark: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                // Header with icon and bookmark
                HStack(alignment: .top) {
                    // Icon
                    ZStack {
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(moneyMove.iconBackgroundColor)
                            .frame(width: 40, height: 40)

                        Image(systemName: moneyMove.iconName)
                            .font(.system(size: 18, weight: .semibold))
                            .foregroundColor(.white)
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
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                // Subtitle
                Text(moneyMove.subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)

                Spacer(minLength: 0)

                // Meta info
                HStack(spacing: 30) {
                    ReadTimeLabel(minutes: moneyMove.estimatedMinutes)
                    LearnerCountBadge(count: moneyMove.learnerCount)
                }
            }
            .padding(AppSpacing.lg)
            .frame(width: 200, height: 200)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.extraLarge)
        }
        .buttonStyle(PlainButtonStyle())
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
