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
    var isLoading: Bool = false
    var hasMoreNews: Bool = false
    var onArticleTap: ((TickerNewsArticle) -> Void)?
    var onExternalLinkTap: ((TickerNewsArticle) -> Void)?
    var onRelatedTickerTap: ((String) -> Void)?
    var onLoadMore: (() -> Void)?

    var body: some View {
        LazyVStack(spacing: AppSpacing.md) {
            if isLoading && articles.isEmpty {
                // Shimmer loading state (cache miss — AI is summarizing)
                loadingStateView
            } else if articles.isEmpty {
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

                // Load more trigger
                if hasMoreNews {
                    Color.clear
                        .frame(height: 1)
                        .onAppear {
                            onLoadMore?()
                        }
                }

                // Bottom spacing for AI bar
                Spacer()
                    .frame(height: 120)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
    }

    private var loadingStateView: some View {
        VStack(spacing: AppSpacing.md) {
            // "AI is summarizing" label
            HStack(spacing: AppSpacing.sm) {
                ProgressView()
                    .tint(AppColors.accentCyan)
                    .scaleEffect(0.8)

                Text("Caydex AI is summarizing...")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
            }
            .padding(.bottom, AppSpacing.xs)

            // Shimmer skeleton cards
            ForEach(0..<3, id: \.self) { _ in
                TickerNewsShimmerCard()
            }

            // Bottom spacing for AI bar
            Spacer()
                .frame(height: 120)
        }
    }

    private var emptyStateView: some View {
        VStack(spacing: AppSpacing.lg) {
            Image(systemName: "newspaper")
                .font(AppTypography.iconHero)
                .foregroundColor(AppColors.textMuted)

            Text("No News Available")
                .font(AppTypography.titleCompact)
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
