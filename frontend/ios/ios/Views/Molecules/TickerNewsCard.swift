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

    @State private var isExpanded: Bool = false

    private var hasExpandableContent: Bool {
        article.hasSummary
    }

    var body: some View {
        Button(action: {
            if hasExpandableContent {
                withAnimation(.easeInOut(duration: 0.25)) {
                    isExpanded.toggle()
                }
            } else {
                onCardTap?()
            }
        }) {
            VStack(alignment: .leading, spacing: AppSpacing.md) {
                // Header row: Sentiment + Time + Source
                TickerNewsCardHeader(
                    sentiment: article.sentiment,
                    timeAgo: article.timeAgo,
                    sourceName: article.source.name
                )

                // Main content: Headline + Thumbnail
                HStack(alignment: .top, spacing: AppSpacing.md) {
                    // Headline
                    Text(article.headline)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(isExpanded ? nil : 3)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)

                    Spacer(minLength: 0)

                    // Thumbnail
                    NewsThumbnail(
                        imageName: article.thumbnailName,
                        width: 88,
                        height: 64
                    )
                }

                // Related tickers
                if !article.relatedTickers.isEmpty {
                    TickerNewsRelatedTickers(
                        tickers: article.relatedTickers,
                        currentTicker: currentTicker,
                        onTickerTap: onTickerTap
                    )
                }

                // Expanded content (bullet points)
                if isExpanded && hasExpandableContent {
                    TickerNewsExpandedContent(bullets: article.summaryBullets)
                        .padding(.top, AppSpacing.sm)
                        .transition(.opacity.combined(with: .move(edge: .top)))
                }

                // Footer: External link + Expand toggle
                TickerNewsCardFooter(
                    hasExpandableContent: hasExpandableContent,
                    isExpanded: isExpanded,
                    onExternalLinkTap: onExternalLinkTap,
                    onExpandToggle: {
                        withAnimation(.easeInOut(duration: 0.25)) {
                            isExpanded.toggle()
                        }
                    }
                )
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(PlainButtonStyle())
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
