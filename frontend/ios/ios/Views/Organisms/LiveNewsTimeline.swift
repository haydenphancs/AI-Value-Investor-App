//
//  LiveNewsTimeline.swift
//  ios
//
//  Organism: Live news timeline content with sticky section headers
//

import SwiftUI

struct LiveNewsTimeline: View {
    let groupedNews: [GroupedNews]
    var onArticleTapped: ((NewsArticle) -> Void)?

    var body: some View {
        LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
            ForEach(groupedNews) { group in
                Section {
                    NewsGroupContent(
                        articles: group.articles,
                        onArticleTapped: onArticleTapped
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
    var onArticleTapped: ((NewsArticle) -> Void)?

    var body: some View {
        VStack(spacing: 0) {
            ForEach(Array(articles.enumerated()), id: \.element.id) { index, article in
                TimelineRow(
                    article: article,
                    isFirst: index == 0,
                    isLast: index == articles.count - 1,
                    onTapped: {
                        onArticleTapped?(article)
                    }
                )
                .padding(.horizontal, AppSpacing.lg)
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
