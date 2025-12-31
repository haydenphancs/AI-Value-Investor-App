//
//  StarRatingView.swift
//  ios
//
//  Atom: Star rating display (0-5 scale)
//

import SwiftUI

struct StarRatingView: View {
    let rating: Double
    let maxRating: Int = 5
    var starSize: CGFloat = 14
    var showValue: Bool = true

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            // Stars
            HStack(spacing: 2) {
                ForEach(0..<maxRating, id: \.self) { index in
                    starImage(for: index)
                        .font(.system(size: starSize))
                        .foregroundColor(starColor(for: index))
                }
            }

            // Rating value
            if showValue {
                Text(String(format: "%.1f", rating))
                    .font(AppTypography.footnote)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary)
            }
        }
    }

    private func starImage(for index: Int) -> Image {
        let threshold = Double(index) + 0.5
        if rating >= Double(index + 1) {
            return Image(systemName: "star.fill")
        } else if rating >= threshold {
            return Image(systemName: "star.leadinghalf.filled")
        } else {
            return Image(systemName: "star")
        }
    }

    private func starColor(for index: Int) -> Color {
        if rating > Double(index) {
            return Color(hex: "F59E0B") // Gold/Amber
        } else {
            return AppColors.textMuted
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        StarRatingView(rating: 5.0)
        StarRatingView(rating: 4.5)
        StarRatingView(rating: 3.0)
        StarRatingView(rating: 2.5)
        StarRatingView(rating: 0.0)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
