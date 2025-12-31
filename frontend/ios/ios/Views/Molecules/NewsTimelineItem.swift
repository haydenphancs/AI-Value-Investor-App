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
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(3)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)

                    // Footer: Source and Sentiment
                    HStack(spacing: AppSpacing.md) {
                        SourceLabel(source: article.source)

                        NewsSentimentBadge(sentiment: article.sentiment)
                    }
                }

                Spacer()

                // Thumbnail
                NewsThumbnail(imageName: article.thumbnailName)
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
