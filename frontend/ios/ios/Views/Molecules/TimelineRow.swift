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
    /// Open the article in the in-app browser. Fires from the expanded card's
    /// open-link icon, and from a tap on a card that has no summary to expand.
    var onOpenLink: (() -> Void)?

    private let railWidth: CGFloat = 20
    private let dotSize: CGFloat = 8
    /// Dot centre, measured from the top of the row — level with the timestamp
    /// caption line above the card.
    private let dotCenterY: CGFloat = 9
    private var lineColor: Color { AppColors.textMuted.opacity(0.3) }

    var body: some View {
        // The rail is drawn as an OVERLAY over the content's reserved left
        // gutter, not as a fixed-height sibling column. The old design used
        // `TimelineConnector(height: 120)`, a guess that left a gap the moment a
        // card expanded. Here the outgoing segment uses `maxHeight: .infinity`,
        // so the line always spans the row's ACTUAL height and meets the next
        // row's dot with no gap — expanded or not.
        contentColumn
            .padding(.leading, railWidth + AppSpacing.md)
            .overlay(alignment: .topLeading) { rail }
    }

    private var contentColumn: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Timestamp — the timeline's own time. The card below therefore
            // passes `timeAgo: nil` so the time is not printed twice.
            Text(article.formattedTime)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            // Same card as the detail News tabs, EXPANDABLE: tap toggles the
            // inline AI summary + footer icons; the open-link icon (and a tap on
            // a not-yet-summarized card) opens the article in the in-app browser
            // — no separate News Detail screen. Sentiment is optional here (no
            // badge until enriched).
            NewsCardView(
                headline: article.headline,
                sourceName: article.source.displayName,
                sentiment: article.sentiment,
                timeAgo: nil,
                thumbnailName: article.thumbnailName,
                imageURL: article.imageURL,
                relatedTickers: article.relatedTickers,
                bullets: article.summaryBullets,
                style: .expandable,
                onTap: onOpenLink,
                onExternalLinkTap: onOpenLink
            )
        }
    }

    private var rail: some View {
        VStack(spacing: 0) {
            // Incoming segment: row top → dot. Hidden on the first row.
            Rectangle()
                .fill(isFirst ? Color.clear : lineColor)
                .frame(width: 1, height: max(0, dotCenterY - dotSize / 2))

            TimelineDot()

            // Outgoing segment: dot → row bottom. `maxHeight: .infinity` fills
            // the remaining height (the overlay is bounded by the content), so
            // it reaches the next row regardless of expand state.
            Rectangle()
                .fill(isLast ? Color.clear : lineColor)
                .frame(width: 1)
                .frame(maxHeight: .infinity)
        }
        .frame(width: railWidth)
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
