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

    // MARK: - Private Properties
    private var allNewsArticles: [NewsArticle] = []
    private var stockSummaries: [String: NewsInsightSummary] = [:]

    // MARK: - Initialization
    init() {
        loadMockData()
    }

    // MARK: - Data Loading
    func loadMockData() {
        isLoading = true

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.loadFilterTabs()
            self?.loadAllStockSummaries()
            self?.loadAllNewsArticles()
            self?.updateContentForSelectedTab()
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

    private func loadAllStockSummaries() {
        // Market Summary (default)
        stockSummaries["Market"] = NewsInsightSummary(
            headline: "Tech Stocks Rally on Strong AI Earnings",
            bulletPoints: [
                "Major technology companies posted impressive Q4 results driven by AI infrastructure investments.",
                "Cloud computing revenue exceeded expectations, with Microsoft and Google leading the charge. Market sentiment remains bullish heading into 2024."
            ],
            sentiment: .bullish,
            updatedAt: Date().addingTimeInterval(-3600),
            summaryType: "24h - AI Summary"
        )

        // AAPL Summary
        stockSummaries["AAPL"] = NewsInsightSummary(
            headline: "Apple's AI Strategy Drives Strong Momentum",
            bulletPoints: [
                "Apple Intelligence features in iOS 18 are seeing strong adoption with 78% of new iPhone users enabling AI capabilities.",
                "Services revenue hit $25B quarterly record, with App Store and Apple Music leading growth. Analysts raised price targets citing AI monetization potential."
            ],
            sentiment: .bullish,
            updatedAt: Date().addingTimeInterval(-1800),
            summaryType: "AAPL - AI Summary"
        )

        // TSLA Summary
        stockSummaries["TSLA"] = NewsInsightSummary(
            headline: "Tesla Delivery Numbers Beat Expectations",
            bulletPoints: [
                "Q4 deliveries reached 484,000 units, surpassing analyst estimates of 473,000. Model Y remains the best-selling EV globally.",
                "FSD v12 rollout accelerating with 2M+ miles driven. Energy storage deployments grew 125% YoY, diversifying revenue streams."
            ],
            sentiment: .bullish,
            updatedAt: Date().addingTimeInterval(-2700),
            summaryType: "TSLA - AI Summary"
        )
    }

    private func loadAllNewsArticles() {
        let calendar = Calendar.current
        let now = Date()

        // Today's timestamps
        let today1 = calendar.date(bySettingHour: 14, minute: 45, second: 0, of: now)!
        let today2 = calendar.date(bySettingHour: 14, minute: 1, second: 0, of: now)!
        let today3 = calendar.date(bySettingHour: 9, minute: 45, second: 0, of: now)!
        let today4 = calendar.date(bySettingHour: 11, minute: 30, second: 0, of: now)!
        let today5 = calendar.date(bySettingHour: 10, minute: 15, second: 0, of: now)!

        // Yesterday's timestamps
        let yesterday = calendar.date(byAdding: .day, value: -1, to: now)!
        let yesterday1 = calendar.date(bySettingHour: 16, minute: 30, second: 0, of: yesterday)!
        let yesterday2 = calendar.date(bySettingHour: 11, minute: 15, second: 0, of: yesterday)!
        let yesterday3 = calendar.date(bySettingHour: 14, minute: 20, second: 0, of: yesterday)!
        let yesterday4 = calendar.date(bySettingHour: 9, minute: 45, second: 0, of: yesterday)!

        // Older articles (2 days ago)
        let twoDaysAgo = calendar.date(byAdding: .day, value: -2, to: now)!
        let older1 = calendar.date(bySettingHour: 10, minute: 0, second: 0, of: twoDaysAgo)!
        let older2 = calendar.date(bySettingHour: 15, minute: 30, second: 0, of: twoDaysAgo)!
        let older3 = calendar.date(bySettingHour: 13, minute: 45, second: 0, of: twoDaysAgo)!

        allNewsArticles = [
            // ===== MARKET NEWS =====
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
                headline: "Fed signals potential rate cuts in early 2025 amid cooling inflation data.",
                summary: nil,
                source: NewsSource(name: "WSJ", iconName: "icon_wsj"),
                sentiment: .positive,
                publishedAt: yesterday2,
                thumbnailName: "news_fed",
                relatedTickers: ["SPY", "QQQ"]
            ),
            NewsArticle(
                headline: "Microsoft Azure revenue grows 29% YoY driven by AI workloads and enterprise adoption.",
                summary: nil,
                source: NewsSource(name: "MarketWatch", iconName: "icon_marketwatch"),
                sentiment: .positive,
                publishedAt: older1,
                thumbnailName: "news_microsoft",
                relatedTickers: ["MSFT"]
            ),

            // ===== APPLE (AAPL) NEWS =====
            NewsArticle(
                headline: "Apple Unveils Revolutionary AI Features in iOS 18 Beta and increase 20% profit for the next year.",
                summary: nil,
                source: NewsSource(name: "Zacks", iconName: "icon_zacks"),
                sentiment: .positive,
                publishedAt: today3,
                thumbnailName: "news_apple",
                relatedTickers: ["AAPL"]
            ),
            NewsArticle(
                headline: "Apple's Services Revenue Hits Record $25 Billion in Q4, App Store Growth Accelerates",
                summary: nil,
                source: NewsSource(name: "Bloomberg", iconName: "icon_bloomberg"),
                sentiment: .positive,
                publishedAt: today4,
                thumbnailName: "news_apple",
                relatedTickers: ["AAPL"]
            ),
            NewsArticle(
                headline: "iPhone 16 Pro Max Demand Exceeds Supply, Apple Increases Production Orders by 10%",
                summary: nil,
                source: NewsSource(name: "CNBC", iconName: "icon_cnbc"),
                sentiment: .positive,
                publishedAt: yesterday3,
                thumbnailName: "news_apple",
                relatedTickers: ["AAPL"]
            ),
            NewsArticle(
                headline: "Apple Intelligence Features See 78% Adoption Rate Among New iPhone Users",
                summary: nil,
                source: NewsSource(name: "Reuters", iconName: "icon_reuters"),
                sentiment: .positive,
                publishedAt: yesterday4,
                thumbnailName: "news_apple",
                relatedTickers: ["AAPL"]
            ),
            NewsArticle(
                headline: "Apple Expands Partnership with TSMC for 2nm Chip Production in 2025",
                summary: nil,
                source: NewsSource(name: "WSJ", iconName: "icon_wsj"),
                sentiment: .positive,
                publishedAt: older2,
                thumbnailName: "news_apple",
                relatedTickers: ["AAPL", "TSM"]
            ),

            // ===== TESLA (TSLA) NEWS =====
            NewsArticle(
                headline: "Tesla reports strong Q4 deliveries beating Wall Street estimates by 15%.",
                summary: nil,
                source: NewsSource(name: "Bloomberg", iconName: "icon_bloomberg"),
                sentiment: .positive,
                publishedAt: yesterday1,
                thumbnailName: "news_tesla",
                relatedTickers: ["TSLA"]
            ),
            NewsArticle(
                headline: "Tesla's Full Self-Driving V12 Reaches 2 Million Miles Driven Without Intervention",
                summary: nil,
                source: NewsSource(name: "CNBC", iconName: "icon_cnbc"),
                sentiment: .positive,
                publishedAt: today5,
                thumbnailName: "news_tesla",
                relatedTickers: ["TSLA"]
            ),
            NewsArticle(
                headline: "Tesla Energy Storage Deployments Surge 125% YoY, Megapack Demand Exceeds Supply",
                summary: nil,
                source: NewsSource(name: "Reuters", iconName: "icon_reuters"),
                sentiment: .positive,
                publishedAt: yesterday4,
                thumbnailName: "news_tesla",
                relatedTickers: ["TSLA"]
            ),
            NewsArticle(
                headline: "Tesla Cybertruck Production Ramps Up, Deliveries Begin in European Markets",
                summary: nil,
                source: NewsSource(name: "MarketWatch", iconName: "icon_marketwatch"),
                sentiment: .positive,
                publishedAt: older3,
                thumbnailName: "news_tesla",
                relatedTickers: ["TSLA"]
            ),
            NewsArticle(
                headline: "Elon Musk Confirms Tesla Robotaxi Event Scheduled for Q2 2025",
                summary: nil,
                source: NewsSource(name: "Bloomberg", iconName: "icon_bloomberg"),
                sentiment: .positive,
                publishedAt: older1,
                thumbnailName: "news_tesla",
                relatedTickers: ["TSLA"]
            )
        ]
    }

    private func loadNewsForTab(_ tab: NewsFilterTab) {
        isLoading = true

        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { [weak self] in
            self?.updateContentForSelectedTab()
            self?.isLoading = false
        }
    }

    private func updateContentForSelectedTab() {
        guard let tab = selectedTab else { return }

        // Update AI Summary based on selected tab
        let summaryKey = tab.isMarketTab ? "Market" : (tab.ticker ?? "Market")
        insightSummary = stockSummaries[summaryKey]
        
        print("🔄 UpdatesViewModel: Loading content for tab '\(tab.title)' with ticker '\(tab.ticker ?? "nil")'")

        // Filter news articles based on selected tab
        if tab.isMarketTab {
            // Market tab shows all general market news (excluding stock-specific)
            newsArticles = allNewsArticles.filter { article in
                // Show articles that are general market news
                let generalMarketTickers = ["XOM", "CVX", "BP", "NVDA", "SPY", "QQQ", "MSFT"]
                return article.relatedTickers.contains { generalMarketTickers.contains($0) }
            }
        } else if let ticker = tab.ticker {
            // Stock-specific tab shows only news for that ticker
            newsArticles = allNewsArticles.filter { article in
                article.relatedTickers.contains(ticker)
            }
        } else {
            newsArticles = allNewsArticles
        }
        
        print("📰 Found \(newsArticles.count) articles for '\(tab.title)'")

        groupNewsArticles()
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
