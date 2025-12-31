//
//  LiveNewsTimeline.swift
//  ios
//
//  Organism: Live news timeline with grouped sections
//

import SwiftUI

struct LiveNewsTimeline: View {
    let groupedNews: [GroupedNews]
    var onArticleTapped: ((NewsArticle) -> Void)?
    var onFilterTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section Header
            HStack {
                HStack(spacing: AppSpacing.sm) {
                    LiveIndicator()

                    Text("Live News")
                        .font(AppTypography.title3)
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()

                Button(action: { onFilterTapped?() }) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "line.3.horizontal.decrease")
                            .font(.system(size: 12, weight: .medium))

                        Text("All")
                            .font(AppTypography.callout)
                    }
                    .foregroundColor(AppColors.textSecondary)
                }
                .buttonStyle(PlainButtonStyle())
            }
            .padding(.horizontal, AppSpacing.lg)

            // News Groups
            ForEach(groupedNews) { group in
                NewsGroupSection(
                    group: group,
                    onArticleTapped: onArticleTapped
                )
            }
        }
    }
}

// MARK: - News Group Section
struct NewsGroupSection: View {
    let group: GroupedNews
    var onArticleTapped: ((NewsArticle) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section Title
            Text(group.sectionTitle)
                .font(AppTypography.footnoteBold)
                .foregroundColor(AppColors.primaryBlue)
                .padding(.horizontal, AppSpacing.lg)

            // Timeline Items
            VStack(spacing: 0) {
                ForEach(Array(group.articles.enumerated()), id: \.element.id) { index, article in
                    TimelineRow(
                        article: article,
                        isFirst: index == 0,
                        isLast: index == group.articles.count - 1
                    ) {
                        onArticleTapped?(article)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                }
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
                        ),
                        NewsArticle(
                            headline: "Apple Unveils Revolutionary AI Features in iOS 18 Beta",
                            summary: nil,
                            source: NewsSource(name: "Zacks", iconName: nil),
                            sentiment: .positive,
                            publishedAt: Date(),
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
