//
//  NewsDetailViewModel.swift
//  ios
//
//  ViewModel for the News Detail screen
//

import SwiftUI
import Combine

@MainActor
class NewsDetailViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var articleDetail: NewsArticleDetail?
    @Published var isLoading: Bool = false
    @Published var showShareSheet: Bool = false

    // MARK: - Private Properties
    private let article: NewsArticle

    // MARK: - Initialization
    init(article: NewsArticle) {
        self.article = article
        loadArticleDetail()
    }

    // MARK: - Public Methods
    func loadArticleDetail() {
        isLoading = true

        // Simulate loading article details
        // In production, this would fetch from an API
        let mockTakeaways = generateMockTakeaways(for: article)

        articleDetail = NewsArticleDetail(
            from: article,
            keyTakeaways: mockTakeaways,
            heroImageName: article.thumbnailName ?? "news_hero_placeholder",
            readTimeMinutes: 4,
            articleURL: URL(string: "https://example.com/article")
        )

        isLoading = false
    }

    func refresh() async {
        isLoading = true
        // Simulate network delay
        try? await Task.sleep(nanoseconds: 500_000_000)
        loadArticleDetail()
        isLoading = false
    }

    func openFullStory() {
        guard let url = articleDetail?.articleURL else { return }
        UIApplication.shared.open(url)
    }

    func shareArticle() {
        showShareSheet = true
    }

    // MARK: - Private Methods
    private func generateMockTakeaways(for article: NewsArticle) -> [KeyTakeaway] {
        // Generate contextual takeaways based on sentiment
        let takeaways: [String]

        switch article.sentiment {
        case .negative:
            takeaways = [
                "Despite record Q4 results, missing expectations signals slowing growth and weaker-than-hoped execution.",
                "A miss in a flagship quarter raises doubts about forward demand and near-term visibility.",
                "Leadership transition at this scale introduces strategic and execution risk during a critical AI cycle.",
                "With expectations priced for perfection, even a small miss could trigger outsized market pressure."
            ]
        case .positive:
            takeaways = [
                "Strong quarterly performance exceeded analyst expectations across all key metrics.",
                "Revenue growth acceleration signals robust demand and effective market positioning.",
                "Management's forward guidance suggests continued momentum in upcoming quarters.",
                "Institutional investors have increased positions, reflecting growing confidence."
            ]
        case .neutral:
            takeaways = [
                "Results came in line with market expectations, maintaining steady performance.",
                "No significant surprises in the earnings report, suggesting stability.",
                "Management guidance remains consistent with prior quarters.",
                "Market reaction has been muted as investors await more clarity."
            ]
        }

        return takeaways.enumerated().map { index, text in
            KeyTakeaway(index: index + 1, text: text)
        }
    }
}
