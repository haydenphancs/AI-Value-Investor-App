//
//  SnapshotRatingIndicator.swift
//  ios
//
//  Atom: Rating indicator with icon and optional stroke for Snapshots
//

import SwiftUI

struct SnapshotRatingIndicator: View {
    let category: SnapshotCategory
    let rating: SnapshotRatingLevel
    var iconSize: CGFloat = 18

    private var backgroundColor: Color {
        rating.color.opacity(0.15)
    }

    private var iconColor: Color {
        rating.color
    }

    var body: some View {
        ZStack {
            // Background circle
            Circle()
                .fill(backgroundColor)
                .frame(width: iconSize + 12, height: iconSize + 12)

            // Stroke for excellent (5-star) or poor (1-star) ratings
            if rating.hasStroke {
                Circle()
                    .stroke(rating.color, lineWidth: 2)
                    .frame(width: iconSize + 12, height: iconSize + 12)
            }

            // Icon
            Image(systemName: category.iconName)
                .font(.system(size: iconSize * 0.7, weight: .semibold))
                .foregroundColor(iconColor)
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        HStack(spacing: AppSpacing.lg) {
            SnapshotRatingIndicator(category: .profitability, rating: .excellent)
            SnapshotRatingIndicator(category: .growth, rating: .average)
            SnapshotRatingIndicator(category: .price, rating: .strong)
        }
        HStack(spacing: AppSpacing.lg) {
            SnapshotRatingIndicator(category: .financialHealth, rating: .poor)
            SnapshotRatingIndicator(category: .insidersOwnership, rating: .weak)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
