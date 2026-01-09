//
//  SentimentMetricsRow.swift
//  ios
//
//  Row displaying social mentions and news articles metrics
//

import SwiftUI

struct SentimentMetricsRow: View {
    let sentimentData: SentimentAnalysisData

    var body: some View {
        HStack(spacing: AppSpacing.lg) {
            // Social Mentions
            SentimentMetricCard(
                iconName: "bubble.left.and.bubble.right.fill",
                title: "Social Mentions",
                value: sentimentData.formattedSocialMentions,
                change: sentimentData.formattedSocialChange,
                changeColor: sentimentData.socialChangeColor
            )

            // News Articles
            SentimentMetricCard(
                iconName: "newspaper.fill",
                title: "News Articles",
                value: sentimentData.formattedNewsArticles,
                change: sentimentData.formattedNewsChange,
                changeColor: sentimentData.newsChangeColor
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

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: iconName)
                    .font(.system(size: 12))
                    .foregroundColor(AppColors.textSecondary)

                Text(title)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            Text(value)
                .font(AppTypography.title2)
                .fontWeight(.bold)
                .foregroundColor(AppColors.textPrimary)

            Text(change)
                .font(AppTypography.caption)
                .foregroundColor(changeColor)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
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

        SentimentMetricsRow(sentimentData: SentimentAnalysisData.sampleData)
            .padding()
    }
}
