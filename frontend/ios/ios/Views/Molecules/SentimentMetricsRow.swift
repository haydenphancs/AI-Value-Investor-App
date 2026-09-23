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
        // `.top` + a definite row height (`fixedSize`) + each card's `maxHeight: .infinity`:
        // only the Social tile carries a source line, so without this trio the two tiles
        // are different heights and the shorter one floats to the middle of the taller.
        HStack(alignment: .top, spacing: AppSpacing.lg) {
            // Social Mentions
            //
            // THE `known` FLAG HAS TO BE PART OF THIS GATE. `socialDataAvailable` is
            // computed backend-side as `count_24h > 0 or count_7d > 0`, and a lookup that
            // FAILED also reports 0 — so on its own it sends every failure down the else
            // branch, which states "Not tracked on Reddit" as a measured fact about a
            // question that was never answered. That is precisely the incident the flag
            // was added for (the 42501 that answered every ticker "0 mentions this week"
            // for months). When the window is unknown, take the accessor branch: its three
            // readers already render "—" / "Reddit data unavailable" / muted. And "Not
            // tracked" is a claim about BOTH windows, so it needs both measured — a
            // known-zero 24 h beside an unknown 7 d stays on the accessor branch, which
            // renders the measured window honestly and dashes the other.
            if sentimentData.socialDataAvailable
                || !sentimentData.socialKnown(for: selectedTimeframe)
                || !sentimentData.socialBothWindowsKnown {
                SentimentMetricCard(
                    iconName: "bubble.left.and.bubble.right.fill",
                    title: "Social Mentions",
                    source: Self.socialSource,
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
                    source: Self.socialSource,
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
                source: Self.newsSource,
                value: sentimentData.formattedNewsArticles(for: selectedTimeframe),
                change: sentimentData.formattedNewsChange(for: selectedTimeframe),
                changeColor: sentimentData.newsChangeColor(for: selectedTimeframe),
                valueFont: AppTypography.bodyEmphasis,
                changeFont: AppTypography.captionSmall
            )
        }
        .fixedSize(horizontal: false, vertical: true)
    }

    /// Where each number comes from, named on the card — and the reason BOTH tiles carry
    /// a line even though only one was asked for: with a source on one tile only, its
    /// value sits a line lower than its neighbour's and the two stop reading as a pair.
    ///
    /// Social is Reddit mentions (r/wallstreetbets, r/stocks, r/investing and friends),
    /// not X/StockTwits — worth stating, because "Social Mentions 69" otherwise reads as
    /// all of social media.
    private static let socialSource = "on Reddit"
    /// News is the asset's own coverage across many publishers. Named by WHAT it is, never
    /// by the data provider: the market-data licence does not permit naming them as a
    /// source (`.claude/rules/marketing.md` §1).
    private static let newsSource = "across news outlets"
}

// MARK: - Single Metric Card
struct SentimentMetricCard: View {
    let iconName: String
    let title: String
    /// Optional provenance line under the title, e.g. "on Reddit". Omitted where the
    /// source may not be named.
    var source: String? = nil
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

            if let source {
                Text(source)
                    .font(AppTypography.captionSmall)
                    .foregroundColor(AppColors.textMuted)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                    .padding(.top, -AppSpacing.xs)
                    .accessibilityLabel("Source: \(source)")
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
        // Stretches to the taller tile (the row is `.top`-aligned with a definite height),
        // so a source line on one card does not leave the other floating mid-row.
        .frame(maxHeight: .infinity, alignment: .top)
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
