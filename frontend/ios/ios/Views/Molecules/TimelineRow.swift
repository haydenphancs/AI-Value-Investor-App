//
//  TimelineRow.swift
//  ios
//
//  Molecule: Combines timeline indicator with timestamp and news item
//

import SwiftUI

struct TimelineRow: View {
    let article: NewsArticle
    let isFirst: Bool
    let isLast: Bool
    var onTapped: (() -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Timeline Column
            VStack(spacing: 0) {
                // Top connector (hidden for first item)
                if !isFirst {
                    TimelineConnector(height: 8)
                } else {
                    Spacer().frame(height: 8)
                }

                // Dot
                TimelineDot()

                // Bottom connector (extends to content)
                if !isLast {
                    TimelineConnector(height: 120)
                }
            }
            .frame(width: 20)

            // Content Column
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Timestamp
                Text(article.formattedTime)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                // News Item
                NewsTimelineItem(article: article, onTapped: onTapped)
            }
        }
    }
}

#Preview {
    VStack(spacing: 0) {
        TimelineRow(
            article: NewsArticle(
                headline: "Oil prices stabilize as OPEC + members agreed to maintain current production levels.",
                summary: nil,
                source: NewsSource(name: "Reuters", iconName: nil),
                sentiment: .neutral,
                publishedAt: Date(),
                thumbnailName: nil,
                relatedTickers: []
            ),
            isFirst: true,
            isLast: false
        )

        TimelineRow(
            article: NewsArticle(
                headline: "NVIDIA Announces Record Q4 Earnings",
                summary: nil,
                source: NewsSource(name: "CNBC", iconName: nil),
                sentiment: .negative,
                publishedAt: Date(),
                thumbnailName: nil,
                relatedTickers: []
            ),
            isFirst: false,
            isLast: true
        )
    }
    .padding()
    .background(AppColors.background)
}
