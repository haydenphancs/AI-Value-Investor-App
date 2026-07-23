//
//  LiveNewsTimeline.swift
//  ios
//
//  Organism: Live news timeline content with sticky section headers
//

import SwiftUI

struct LiveNewsTimeline: View {
    let groupedNews: [GroupedNews]
    /// Open the article's publisher page in the in-app browser. Fires from the
    /// expanded card's open-link icon and the "Read the full story" fallback.
    var onOpenArticle: ((NewsArticle) -> Void)?
    /// Fired as each row scrolls in. Drives paging and visible-window AI
    /// enrichment — the reader's position is the signal for what to summarise.
    var onArticleAppear: ((NewsArticle) -> Void)?
    /// Summarise an un-enriched article in-app on tap (instead of opening the link).
    var onRequestSummary: ((NewsArticle) -> Void)?
    /// Article ids whose on-tap summary is in flight (drives the per-card spinner).
    var summarizingIDs: Set<String> = []

    var body: some View {
        LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
            ForEach(groupedNews) { group in
                Section {
                    NewsGroupContent(
                        articles: group.articles,
                        onOpenArticle: onOpenArticle,
                        onArticleAppear: onArticleAppear,
                        onRequestSummary: onRequestSummary,
                        summarizingIDs: summarizingIDs
                    )
                } header: {
                    NewsSectionHeader(title: group.sectionTitle)
                }
            }
        }
    }
}

// MARK: - News Group Content (without header)
struct NewsGroupContent: View {
    let articles: [NewsArticle]
    var onOpenArticle: ((NewsArticle) -> Void)?
    var onArticleAppear: ((NewsArticle) -> Void)?
    var onRequestSummary: ((NewsArticle) -> Void)?
    var summarizingIDs: Set<String> = []

    var body: some View {
        LazyVStack(spacing: 0) {
            ForEach(Array(articles.enumerated()), id: \.element.id) { index, article in
                TimelineRow(
                    article: article,
                    isFirst: index == 0,
                    isLast: index == articles.count - 1,
                    onOpenLink: {
                        onOpenArticle?(article)
                    },
                    onRequestSummary: {
                        onRequestSummary?(article)
                    },
                    isSummarizing: summarizingIDs.contains(article.apiId)
                )
                .padding(.horizontal, AppSpacing.lg)
                // LazyVStack, so this fires only when the row is actually
                // realised — which is exactly the "reader got here" signal.
                .onAppear { onArticleAppear?(article) }
            }
        }
    }
}

#Preview {
    ScrollView {
        LiveNewsTimeline(
            groupedNews: [
                GroupedNews(
                    sectionTitle: "TODAY",
                    articles: [
                        NewsArticle(
                            headline: "Oil prices stabilize as OPEC + members agreed to maintain current production levels.",
                            summary: nil,
                            source: NewsSource(name: "Reuters", iconName: nil),
                            sentiment: .neutral,
                            publishedAt: Date(),
                            thumbnailName: nil,
                            relatedTickers: []
                        ),
                        NewsArticle(
                            headline: "NVIDIA Announces Record Q4 Earnings, Missed Expectations and CEO step down",
                            summary: nil,
                            source: NewsSource(name: "CNBC", iconName: nil),
                            sentiment: .negative,
                            publishedAt: Date(),
                            thumbnailName: nil,
                            relatedTickers: []
                        )
                    ]
                ),
                GroupedNews(
                    sectionTitle: "YESTERDAY",
                    articles: [
                        NewsArticle(
                            headline: "Apple Unveils Revolutionary AI Features in iOS 18 Beta",
                            summary: nil,
                            source: NewsSource(name: "Zacks", iconName: nil),
                            sentiment: .positive,
                            publishedAt: Calendar.current.date(byAdding: .day, value: -1, to: Date())!,
                            thumbnailName: nil,
                            relatedTickers: []
                        )
                    ]
                )
            ]
        )
    }
    .background(AppColors.background)
}
