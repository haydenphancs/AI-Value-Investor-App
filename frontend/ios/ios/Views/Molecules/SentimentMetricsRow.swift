//
//  SentimentMetricsRow.swift
//  ios
//
//  Row displaying social mentions and news articles metrics
//

import SwiftUI

struct SentimentMetricsRow: View {
    let sentimentData: SentimentAnalysisData
    let selectedTimeframe: SentimentTimeframe

    var body: some View {
        HStack(spacing: AppSpacing.lg) {
            // Social Mentions
            if sentimentData.socialDataAvailable {
                SentimentMetricCard(
                    iconName: "bubble.left.and.bubble.right.fill",
                    title: "Social Mentions",
                    value: sentimentData.formattedSocialMentions(for: selectedTimeframe),
                    change: sentimentData.formattedSocialChange(for: selectedTimeframe),
                    changeColor: sentimentData.socialChangeColor(for: selectedTimeframe)
                )
            } else {
                SentimentMetricCard(
                    iconName: "bubble.left.and.bubble.right.fill",
                    title: "Social Mentions",
                    value: "N/A",
                    change: "Not tracked on Reddit",
                    changeColor: AppColors.textMuted,
                    isDimmed: true,
                    valueFont: AppTypography.bodyEmphasis,
                    changeFont: AppTypography.captionSmall
                )
            }

            // News Sentiment
            SentimentMetricCard(
                iconName: "newspaper.fill",
                title: "News Sentiment",
                value: sentimentData.formattedNewsArticles(for: selectedTimeframe),
                change: sentimentData.formattedNewsChange(for: selectedTimeframe),
                changeColor: sentimentData.newsChangeColor(for: selectedTimeframe),
                valueFont: AppTypography.bodyEmphasis,
                changeFont: AppTypography.captionSmall
            )
        }
    }
}

// MARK: - Single Metric Card
struct SentimentMetricCard: View {
    let iconName: String
    let title: String
    let value: String
    let change: String
    let changeColor: Color
    var isDimmed: Bool = false
    var valueFont: Font = AppTypography.titleCompact
    var changeFont: Font = AppTypography.caption

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: iconName)
                    .font(AppTypography.iconXS)
                    .foregroundColor(AppColors.textSecondary)

                Text(title)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            Text(value)
                .font(valueFont)
                .fontWeight(.bold)
                .foregroundColor(isDimmed ? AppColors.textMuted : AppColors.textPrimary)

            Text(change)
                .font(changeFont)
                .foregroundColor(changeColor)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity)
        .padding(AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .stroke(AppColors.cardBackgroundLight, lineWidth: 1)
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        SentimentMetricsRow(sentimentData: SentimentAnalysisData.sampleData, selectedTimeframe: .last24h)
            .padding()
    }
}
