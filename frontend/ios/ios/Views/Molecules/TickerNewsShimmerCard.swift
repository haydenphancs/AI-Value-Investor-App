//
//  TickerNewsShimmerCard.swift
//  ios
//
//  Molecule: Skeleton loading card for news tab (shown during cache miss / AI summarization)
//

import SwiftUI

struct TickerNewsShimmerCard: View {
    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            // Header row: pill + time + source
            HStack(spacing: AppSpacing.sm) {
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 60, height: 20)

                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 40, height: 14)

                Spacer()

                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 70, height: 14)
            }

            // Main content: headline lines + thumbnail
            HStack(alignment: .top, spacing: AppSpacing.xs) {
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    RoundedRectangle(cornerRadius: AppCornerRadius.small)
                        .fill(AppColors.cardBackgroundLight)
                        .frame(height: 16)

                    RoundedRectangle(cornerRadius: AppCornerRadius.small)
                        .fill(AppColors.cardBackgroundLight)
                        .frame(width: 180, height: 16)
                }

                Spacer(minLength: 0)

                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 72, height: 40)
            }

            // Ticker pills
            HStack(spacing: AppSpacing.xs) {
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 50, height: 22)

                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 50, height: 22)
            }

            // Footer placeholder
            HStack {
                Spacer()
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 20, height: 20)
            }
        }
        .padding(AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
        .shimmer()
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.md) {
            ForEach(0..<3, id: \.self) { _ in
                TickerNewsShimmerCard()
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
