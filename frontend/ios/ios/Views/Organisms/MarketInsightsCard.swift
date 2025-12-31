//
//  MarketInsightsCard.swift
//  ios
//
//  Organism: Market insights card with headline and bullet points
//

import SwiftUI

struct MarketInsightsCard: View {
    let insight: MarketInsight
    var onSeeAllTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            HStack {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "bolt.fill")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(AppColors.neutral)

                    Text("Market Insights")
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()

                Button(action: {
                    onSeeAllTapped?()
                }) {
                    Text("See All")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }

            // Headline
            Text(insight.headline)
                .font(AppTypography.title2)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)

            // Bullet Points
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                ForEach(insight.bulletPoints, id: \.self) { point in
                    HStack(alignment: .top, spacing: AppSpacing.sm) {
                        Circle()
                            .fill(AppColors.textSecondary)
                            .frame(width: 5, height: 5)
                            .padding(.top, 6)

                        Text(point)
                            .font(AppTypography.callout)
                            .foregroundColor(AppColors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }

            // Footer
            HStack {
                SentimentBadge(sentiment: insight.sentiment)

                Spacer()

                Text("Updated \(insight.timeAgo)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    MarketInsightsCard(insight: MarketInsight(
        headline: "Tech Stocks Rally on Strong AI Earnings",
        bulletPoints: [
            "Major technology companies posted impressive Q4 results driven by AI infrastructure investments.",
            "Cloud computing revenue exceeded expectations, with Microsoft and Google leading the charge. Market sentiment remains bullish heading into 2024."
        ],
        sentiment: .bullish,
        updatedAt: Date().addingTimeInterval(-3600)
    ))
    .padding()
    .background(AppColors.background)
}
