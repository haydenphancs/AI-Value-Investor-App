//
//  InsightsSummaryCard.swift
//  ios
//
//  Organism: AI-generated insights summary card for Updates screen
//

import SwiftUI

struct InsightsSummaryCard: View {
    let summary: NewsInsightSummary

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            HStack {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "bolt.fill")
                        .font(.system(size: 14, weight: .semibold))
                        .foregroundColor(AppColors.neutral)

                    Text("Insights")
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()

                AIBadge(text: summary.summaryBadgeText)
            }

            // Headline
            Text(summary.headline)
                .font(AppTypography.title2)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)

            // Bullet Points
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                ForEach(summary.bulletPoints, id: \.self) { point in
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
                SentimentBadge(sentiment: summary.sentiment)

                Spacer()

                Text(summary.timeAgo)
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
    InsightsSummaryCard(
        summary: NewsInsightSummary(
            headline: "Tech Stocks Rally on Strong AI Earnings",
            bulletPoints: [
                "Major technology companies posted impressive Q4 results driven by AI infrastructure investments.",
                "Cloud computing revenue exceeded expectations, with Microsoft and Google leading the charge. Market sentiment remains bullish heading into 2024."
            ],
            sentiment: .bullish,
            updatedAt: Date().addingTimeInterval(-3600),
            summaryType: "24h - AI Summary"
        )
    )
    .padding()
    .background(AppColors.background)
}
