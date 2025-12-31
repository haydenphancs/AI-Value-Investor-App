//
//  RatingBadge.swift
//  ios
//
//  Atom: Displays rating score with color coding
//

import SwiftUI

struct RatingBadge: View {
    let rating: Double
    let maxRating: Double

    init(rating: Double, maxRating: Double = 5.0) {
        self.rating = rating
        self.maxRating = maxRating
    }

    private var backgroundColor: Color {
        let ratio = rating / maxRating
        if ratio >= 0.8 {
            return AppColors.bullish
        } else if ratio >= 0.6 {
            return AppColors.primaryBlue
        } else if ratio >= 0.4 {
            return AppColors.neutral
        } else {
            return AppColors.bearish
        }
    }

    var body: some View {
        Text(String(format: "%.1f/%.0f", rating, maxRating))
            .font(AppTypography.captionBold)
            .foregroundColor(.white)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(backgroundColor)
            .cornerRadius(AppCornerRadius.small)
    }
}

#Preview {
    HStack(spacing: 10) {
        RatingBadge(rating: 4.6)
        RatingBadge(rating: 4.2)
        RatingBadge(rating: 3.3)
        RatingBadge(rating: 2.0)
    }
    .padding()
    .background(AppColors.background)
}
