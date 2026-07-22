//
//  TickerNewsCard.swift
//  ios
//
//  Molecule: Complete news card for ticker detail news tab
//

import SwiftUI

struct TickerNewsCard: View {
    let article: TickerNewsArticle
    var currentTicker: String?
    var onCardTap: (() -> Void)?
    var onExternalLinkTap: (() -> Void)?
    var onTickerTap: ((String) -> Void)?

    var body: some View {
        // Thin adapter over the shared NewsCardView. The detail News tabs use
        // the EXPANDABLE style with the relative time shown (no timeline here).
        // `article.sentiment` is OPTIONAL — nil hides the badge until the article
        // is AI-enriched, matching the Updates screen for the same shared row.
        NewsCardView(
            headline: article.headline,
            sourceName: article.source.displayName,
            sentiment: article.sentiment,
            timeAgo: article.timeAgo,
            thumbnailName: article.thumbnailName,
            imageURL: article.imageURL,
            relatedTickers: article.relatedTickers,
            currentTicker: currentTicker,
            bullets: article.summaryBullets,
            style: .expandable,
            onTap: onCardTap,
            onExternalLinkTap: onExternalLinkTap,
            onTickerTap: onTickerTap
        )
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.md) {
            // Sample cards
            ForEach(TickerNewsArticle.sampleDataForTicker("AAPL")) { article in
                TickerNewsCard(
                    article: article,
                    currentTicker: "AAPL"
                )
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
