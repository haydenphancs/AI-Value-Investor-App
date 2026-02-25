//
//  HomeViewModel.swift
//  ios
//
//  ViewModel for Home screen - MVVM Architecture
//

import Foundation
import Combine

@MainActor
class HomeViewModel: ObservableObject {
    // MARK: - Published Properties
    @Published var marketTickers: [MarketTicker] = []
    @Published var marketInsight: MarketInsight?
    @Published var dailyBriefings: [DailyBriefingItem] = []
    @Published var recentResearch: [ResearchReport] = []
    @Published var selectedTab: HomeTab = .home
    @Published var isLoading: Bool = false
    @Published var error: String?

    // Pre-computed sparkline data so it isn't regenerated on every load
    private lazy var cachedSparklines: (pos1: [Double], pos2: [Double], neg1: [Double], neg2: [Double]) = {
        (
            pos1: Self.generateSparklineData(positive: true),
            pos2: Self.generateSparklineData(positive: true),
            neg1: Self.generateSparklineData(positive: false),
            neg2: Self.generateSparklineData(positive: false)
        )
    }()

    // MARK: - Initialization
    init() {
        // Defer data loading to onAppear via Task to avoid blocking view init
        Task { [weak self] in
            await self?.loadInitialData()
        }
    }

    // MARK: - Data Loading
    private func loadInitialData() async {
        isLoading = true
        // Build mock data off the main actor, then assign on main
        let tickers = buildMarketTickers()
        let insight = buildMarketInsight()
        let briefings = buildDailyBriefings()
        let research = buildRecentResearch()

        marketTickers = tickers
        marketInsight = insight
        dailyBriefings = briefings
        recentResearch = research
        isLoading = false
    }

    func refresh() async {
        isLoading = true
        try? await Task.sleep(nanoseconds: 1_000_000_000)
        let tickers = buildMarketTickers()
        let insight = buildMarketInsight()
        let briefings = buildDailyBriefings()
        let research = buildRecentResearch()

        marketTickers = tickers
        marketInsight = insight
        dailyBriefings = briefings
        recentResearch = research
        isLoading = false
    }

    // MARK: - Data Builders (pure functions that return values)
    private func buildMarketTickers() -> [MarketTicker] {
        [
            MarketTicker(
                name: "S&P 500",
                symbol: "^GSPC",
                type: .index,
                price: 6783.45,
                changePercent: 0.85,
                sparklineData: cachedSparklines.pos1
            ),
            MarketTicker(
                name: "Nasdaq",
                symbol: "^IXIC",
                type: .index,
                price: 23293.23,
                changePercent: 0.85,
                sparklineData: cachedSparklines.pos2
            ),
            MarketTicker(
                name: "Bitcoin",
                symbol: "BTCUSD",
                type: .crypto,
                price: 89394.43,
                changePercent: -2.34,
                sparklineData: cachedSparklines.neg1
            ),
            MarketTicker(
                name: "Gold",
                symbol: "GCUSD",
                type: .commodity,
                price: 4322.43,
                changePercent: -1.34,
                sparklineData: cachedSparklines.neg2
            )
        ]
    }

    private func buildMarketInsight() -> MarketInsight {
        MarketInsight(
            headline: "Tech Stocks Rally on Strong AI Earnings",
            bulletPoints: [
                "Major technology companies posted impressive Q4 results driven by AI infrastructure investments.",
                "Cloud computing revenue exceeded expectations, with Microsoft and Google leading the charge. Market sentiment remains bullish heading into 2024."
            ],
            sentiment: .bullish,
            updatedAt: Date().addingTimeInterval(-3600)
        )
    }

    private func buildDailyBriefings() -> [DailyBriefingItem] {
        let calendar = Calendar.current
        var dateComponents = DateComponents()
        dateComponents.month = 2
        dateComponents.day = 24
        dateComponents.year = 2025
        let earningsDate = calendar.date(from: dateComponents)

        return [
            DailyBriefingItem(
                type: .whalesAlert,
                title: "Whales Alert",
                subtitle: "Large crypto whale just moved $50M into COIN stock",
                date: nil,
                badgeText: nil
            ),
            DailyBriefingItem(
                type: .earningsAlert,
                title: "Earnings Alert",
                subtitle: "NVDA reports earnings tomorrow after market close.",
                date: earningsDate,
                badgeText: "24\nFEB"
            ),
            DailyBriefingItem(
                type: .whalesFollowing,
                title: "Whales Your Following",
                subtitle: "3 hedge funds you follow bought GOOGL this week. Avg. position size: $1.2B",
                date: nil,
                badgeText: nil
            ),
            DailyBriefingItem(
                type: .wiserTrending,
                title: "Wiser: Trending",
                subtitle: "How can I invest in OpenAI even though the company is not yet listed on the stock exchange?",
                date: nil,
                badgeText: nil
            )
        ]
    }

    private func buildRecentResearch() -> [ResearchReport] {
        [
            ResearchReport(
                stockTicker: "ORCL",
                stockName: "Oracle Corporation",
                companyLogoName: "icon_oracle",
                persona: .warrenBuffett,
                headline: "Oracle: Strong Quality",
                summary: "Enterprise software giant with deep moat in cloud infrastructure and database services. Consistent earnings growth and long-term competitive advantages.",
                rating: 82,
                fairValue: 190,
                createdAt: Calendar.current.date(from: DateComponents(year: 2025, month: 2, day: 7)) ?? Date(),
                gradientColors: ["C74634", "F80000"]
            ),
            ResearchReport(
                stockTicker: "AAPL",
                stockName: "Apple Inc.",
                companyLogoName: "icon_apple",
                persona: .warrenBuffett,
                headline: "Apple: Excellent Quality",
                summary: "Unmatched ecosystem and brand loyalty create a powerful moat. Services revenue continues to grow, driving recurring income and higher margins.",
                rating: 90,
                fairValue: 213,
                createdAt: Calendar.current.date(from: DateComponents(year: 2024, month: 12, day: 24)) ?? Date(),
                gradientColors: ["A2AAAD", "555555"]
            ),
            ResearchReport(
                stockTicker: "NVDA",
                stockName: "NVIDIA Corp.",
                companyLogoName: "icon_nvidia",
                persona: .peterLynch,
                headline: "NVIDIA: Excellent Quality",
                summary: "Dominant position in AI accelerators with data center revenue surging. GPU demand from AI training and inference workloads continues to outpace supply.",
                rating: 95,
                fairValue: 220,
                createdAt: Calendar.current.date(from: DateComponents(year: 2024, month: 12, day: 23)) ?? Date(),
                gradientColors: ["76B900", "1A1A1A"]
            )
        ]
    }

    // MARK: - Helpers
    private static func generateSparklineData(positive: Bool) -> [Double] {
        var data: [Double] = []
        var value = Double.random(in: 90...110)

        for _ in 0..<20 {
            let change = Double.random(in: -3...3)
            let trend = positive ? 0.5 : -0.5
            value += change + trend
            value = max(80, min(120, value))
            data.append(value)
        }

        return data
    }
}
