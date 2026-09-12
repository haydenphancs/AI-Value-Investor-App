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
            //
            // THE `known` FLAG HAS TO BE PART OF THIS GATE. `socialDataAvailable` is
            // computed backend-side as `count_24h > 0 or count_7d > 0`, and a lookup that
            // FAILED also reports 0 — so on its own it sends every failure down the else
            // branch, which states "Not tracked on Reddit" as a measured fact about a
            // question that was never answered. That is precisely the incident the flag
            // was added for (the 42501 that answered every ticker "0 mentions this week"
            // for months). When the window is unknown, take the accessor branch: its three
            // readers already render "—" / "Reddit data unavailable" / muted.
            if sentimentData.socialDataAvailable
                || !sentimentData.socialKnown(for: selectedTimeframe) {
                SentimentMetricCard(
                    iconName: "bubble.left.and.bubble.right.fill",
                    title: "Social Mentions",
                    value: sentimentData.formattedSocialMentions(for: selectedTimeframe),
                    change: sentimentData.formattedSocialChange(for: selectedTimeframe),
                    changeColor: sentimentData.socialChangeColor(for: selectedTimeframe),
                    isDimmed: !sentimentData.socialKnown(for: selectedTimeframe),
                    // Only the UNKNOWN string ("Reddit data unavailable") is long enough
                    // to need the smaller size; a measured "+12% today" keeps the card's
                    // default `caption` exactly as it shipped.
                    changeFont: sentimentData.socialKnown(for: selectedTimeframe)
                        ? AppTypography.caption : AppTypography.captionSmall
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
        // No `.overlay` stroke: `cardBackgroundLight` and `cardBackgroundNested` share the
        // #252B3B dark arm, so it drew nothing there, and duplicated `cardEdge` in light.
        // `.cardSurface` already draws the edge in the mode that needs one.
        .cardSurface(AppColors.cardBackgroundNested, cornerRadius: AppCornerRadius.medium)
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
