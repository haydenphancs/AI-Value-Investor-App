//
//  NewsTimelineItem.swift
//  ios
//
//  Molecule: Individual news item in the timeline feed
//

import SwiftUI

struct NewsTimelineItem: View {
    let article: NewsArticle
    var onTapped: (() -> Void)?

    var body: some View {
        Button(action: { onTapped?() }) {
            HStack(alignment: .top, spacing: AppSpacing.md) {
                // Content
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    // Headline
                    Text(article.headline)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(3)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)

                    // Footer: Source (leading) and Sentiment (trailing).
                    //
                    // The badge is pinned to the TRAILING edge of the text
                    // column, not packed after the source name. The column has
                    // a fixed width (card − padding − thumbnail − gutter), so
                    // the badge lands on the same x in every row instead of
                    // sliding left and right with the length of "CNBC" vs
                    // "MarketWatch".
                    HStack(spacing: AppSpacing.sm) {
                        SourceLabel(source: article.source)

                        Spacer(minLength: AppSpacing.sm)

                        // Only shown once AI enrichment has actually produced a
                        // sentiment. Rendering a "Neutral" badge for every
                        // un-analysed article states a judgement no model made.
                        if let sentiment = article.sentiment {
                            NewsSentimentBadge(sentiment: sentiment)
                                // The badge is fixed-width content; a long
                                // source name must truncate rather than squeeze
                                // "Neutral" into "Neut…".
                                .layoutPriority(1)
                        }
                    }
                }
                // Claim the column explicitly. The `Spacer()` that used to sit
                // here competed with the headline for the row's free width, so
                // SwiftUI handed it a slice and the gap before the thumbnail
                // came out wider than the card's own 12pt padding. Without it
                // the gutter is exactly the HStack spacing — 12pt, matching the
                // inset on the left of the headline.
                .frame(maxWidth: .infinity, alignment: .leading)

                // Thumbnail — prefers the publisher's remote image; falls back
                // to the legacy local asset name, then to a placeholder.
                NewsThumbnail(
                    imageName: article.thumbnailName,
                    imageURL: article.imageURL
                )
            }
            .padding(AppSpacing.md)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.medium)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    VStack(spacing: 16) {
        NewsTimelineItem(
            article: NewsArticle(
                headline: "Oil prices stabilize as OPEC + members agreed to maintain current production levels.",
                summary: nil,
                source: NewsSource(name: "Reuters", iconName: nil),
                sentiment: .neutral,
                publishedAt: Date(),
                thumbnailName: nil,
                relatedTickers: ["XOM"]
            )
        )

        NewsTimelineItem(
            article: NewsArticle(
                headline: "NVIDIA Announces Record Q4 Earnings, Missed Expectations and CEO step down",
                summary: nil,
                source: NewsSource(name: "CNBC", iconName: nil),
                sentiment: .negative,
                publishedAt: Date(),
                thumbnailName: nil,
                relatedTickers: ["NVDA"]
            )
        )
    }
    .padding()
    .background(AppColors.background)
}
