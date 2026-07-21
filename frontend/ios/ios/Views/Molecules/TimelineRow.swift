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
                // Timestamp — the timeline's own time. The card below therefore
                // passes `timeAgo: nil` so the time is not printed twice.
                Text(article.formattedTime)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                // Same card as the detail News tabs, in TAPPABLE style: the whole
                // card navigates to the News Detail screen (Read-full-story +
                // share). Sentiment is optional here — no badge until enriched.
                NewsCardView(
                    headline: article.headline,
                    sourceName: article.source.displayName,
                    sentiment: article.sentiment,
                    timeAgo: nil,
                    thumbnailName: article.thumbnailName,
                    imageURL: article.imageURL,
                    relatedTickers: article.relatedTickers,
                    bullets: article.summaryBullets,
                    style: .tappable,
                    onTap: onTapped
                )
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
