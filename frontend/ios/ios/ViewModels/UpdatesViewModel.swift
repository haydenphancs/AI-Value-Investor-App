//
//  UpdatesViewModel.swift
//  ios
//
//  ViewModel for Updates/News screen - MVVM Architecture
//

import Foundation
import Combine

@MainActor
class UpdatesViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var filterTabs: [NewsFilterTab] = []
    @Published var selectedTab: NewsFilterTab?
    @Published var insightSummary: NewsInsightSummary?
    @Published var newsArticles: [NewsArticle] = []
    @Published var groupedNews: [GroupedNews] = []
    @Published var filterOptions: NewsFilterOptions = .default
    @Published var isLoading: Bool = false
    @Published var isRefreshing: Bool = false
    @Published var error: String?
    @Published var showFilterSheet: Bool = false

    // MARK: - Initialization
    init() {
        loadMockData()
    }

    // MARK: - Data Loading
    func loadMockData() {
        isLoading = true

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.loadFilterTabs()
            self?.loadInsightSummary()
            self?.loadNewsArticles()
            self?.isLoading = false
        }
    }

    func refresh() async {
        isRefreshing = true
        try? await Task.sleep(nanoseconds: 1_000_000_000)
        loadMockData()
        isRefreshing = false
    }

    func selectTab(_ tab: NewsFilterTab) {
        selectedTab = tab
        loadNewsForTab(tab)
    }

    func addNewTicker() {
        // Navigate to ticker search
        print("Add new ticker tapped")
    }

    func openFilterOptions() {
        showFilterSheet = true
    }

    // MARK: - Private Methods
    private func loadFilterTabs() {
        filterTabs = [
            NewsFilterTab(
                title: "Market",
                ticker: nil,
                changePercent: nil,
                isMarketTab: true
            ),
            NewsFilterTab(
                title: "AAPL",
                ticker: "AAPL",
                changePercent: 2.4,
                isMarketTab: false
            ),
            NewsFilterTab(
                title: "TSLA",
                ticker: "TSLA",
                changePercent: -1.2,
                isMarketTab: false
            )
        ]
        selectedTab = filterTabs.first
    }

    private func loadInsightSummary() {
        insightSummary = NewsInsightSummary(
            headline: "Tech Stocks Rally on Strong AI Earnings",
            bulletPoints: [
                "Major technology companies posted impressive Q4 results driven by AI infrastructure investments.",
                "Cloud computing revenue exceeded expectations, with Microsoft and Google leading the charge. Market sentiment remains bullish heading into 2024."
            ],
            sentiment: .bullish,
            updatedAt: Date().addingTimeInterval(-3600),
            summaryType: "24h - AI Summary"
        )
    }

    private func loadNewsArticles() {
        let calendar = Calendar.current
        let now = Date()

        // Today's articles
        let today1 = calendar.date(bySettingHour: 14, minute: 45, second: 0, of: now)!
        let today2 = calendar.date(bySettingHour: 14, minute: 1, second: 0, of: now)!
        let today3 = calendar.date(bySettingHour: 9, minute: 45, second: 0, of: now)!

        newsArticles = [
            NewsArticle(
                headline: "Oil prices stabilize as OPEC + members agreed to maintain current production levels.",
                summary: nil,
                source: NewsSource(name: "Reuters", iconName: "icon_reuters"),
                sentiment: .neutral,
                publishedAt: today1,
                thumbnailName: "news_oil_opec",
                relatedTickers: ["XOM", "CVX", "BP"]
            ),
            NewsArticle(
                headline: "NVIDIA Announces Record Q4 Earnings, Missed Expectations and CEO step down",
                summary: nil,
                source: NewsSource(name: "CNBC", iconName: "icon_cnbc"),
                sentiment: .negative,
                publishedAt: today2,
                thumbnailName: "news_nvidia",
                relatedTickers: ["NVDA"]
            ),
            NewsArticle(
                headline: "Apple Unveils Revolutionary AI Features in iOS 18 Beta and increase 20% profit for the next year.",
                summary: nil,
                source: NewsSource(name: "Zacks", iconName: "icon_zacks"),
                sentiment: .positive,
                publishedAt: today3,
                thumbnailName: "news_apple",
                relatedTickers: ["AAPL"]
            )
        ]

        groupNewsArticles()
    }

    private func loadNewsForTab(_ tab: NewsFilterTab) {
        // In a real app, this would fetch news filtered by the selected tab
        // For now, we just reload the same data
        isLoading = true
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.isLoading = false
        }
    }

    private func groupNewsArticles() {
        var groups: [String: [NewsArticle]] = [:]

        for article in newsArticles {
            let sectionTitle = article.sectionTitle
            if groups[sectionTitle] == nil {
                groups[sectionTitle] = []
            }
            groups[sectionTitle]?.append(article)
        }

        // Sort groups by date (TODAY first)
        let sortedKeys = groups.keys.sorted { key1, key2 in
            if key1 == "TODAY" { return true }
            if key2 == "TODAY" { return false }
            if key1 == "YESTERDAY" { return true }
            if key2 == "YESTERDAY" { return false }
            return key1 > key2
        }

        groupedNews = sortedKeys.map { key in
            GroupedNews(
                sectionTitle: key,
                articles: groups[key]?.sorted { $0.publishedAt > $1.publishedAt } ?? []
            )
        }
    }
}
