//
//  TickerNewsContent.swift
//  ios
//
//  Organism: News tab content for Ticker Detail screen
//

import SwiftUI

struct TickerNewsContent: View {
    let articles: [TickerNewsArticle]
    let currentTicker: String
    var onArticleTap: ((TickerNewsArticle) -> Void)?
    var onExternalLinkTap: ((TickerNewsArticle) -> Void)?
    var onRelatedTickerTap: ((String) -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            if articles.isEmpty {
                emptyStateView
            } else {
                // News cards list
                ForEach(articles) { article in
                    TickerNewsCard(
                        article: article,
                        currentTicker: currentTicker,
                        onCardTap: {
                            onArticleTap?(article)
                        },
                        onExternalLinkTap: {
                            onExternalLinkTap?(article)
                        },
                        onTickerTap: { ticker in
                            onRelatedTickerTap?(ticker)
                        }
                    )
                }

                // Bottom spacing for AI bar
                Spacer()
                    .frame(height: 120)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
    }

    private var emptyStateView: some View {
        VStack(spacing: AppSpacing.lg) {
            Image(systemName: "newspaper")
                .font(.system(size: 48))
                .foregroundColor(AppColors.textMuted)

            Text("No News Available")
                .font(AppTypography.title2)
                .foregroundColor(AppColors.textPrimary)

            Text("News articles for \(currentTicker) will appear here when available.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)

            Spacer()
                .frame(height: 150)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.xxxl)
    }
}

#Preview {
    ScrollView {
        TickerNewsContent(
            articles: TickerNewsArticle.sampleDataForTicker("AAPL"),
            currentTicker: "AAPL"
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
