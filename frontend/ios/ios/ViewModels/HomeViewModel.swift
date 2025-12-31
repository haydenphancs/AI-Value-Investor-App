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

    // MARK: - Initialization
    init() {
        loadMockData()
    }

    // MARK: - Data Loading
    func loadMockData() {
        isLoading = true

        // Simulate network delay
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            self?.loadMarketTickers()
            self?.loadMarketInsight()
            self?.loadDailyBriefings()
            self?.loadRecentResearch()
            self?.isLoading = false
        }
    }

    func refresh() async {
        isLoading = true
        // Simulate API call
        try? await Task.sleep(nanoseconds: 1_000_000_000)
        loadMockData()
    }

    // MARK: - Mock Data Loaders
    private func loadMarketTickers() {
        marketTickers = [
            MarketTicker(
                name: "S&P 500",
                price: 6783.45,
                changePercent: 0.85,
                sparklineData: generateSparklineData(positive: true)
            ),
            MarketTicker(
                name: "Nasdaq",
                price: 23293.23,
                changePercent: 0.85,
                sparklineData: generateSparklineData(positive: true)
            ),
            MarketTicker(
                name: "Bitcoin",
                price: 89394.43,
                changePercent: -2.34,
                sparklineData: generateSparklineData(positive: false)
            ),
            MarketTicker(
                name: "Gold",
                price: 4322.43,
                changePercent: -1.34,
                sparklineData: generateSparklineData(positive: false)
            )
        ]
    }

    private func loadMarketInsight() {
        marketInsight = MarketInsight(
            headline: "Tech Stocks Rally on Strong AI Earnings",
            bulletPoints: [
                "Major technology companies posted impressive Q4 results driven by AI infrastructure investments.",
                "Cloud computing revenue exceeded expectations, with Microsoft and Google leading the charge. Market sentiment remains bullish heading into 2024."
            ],
            sentiment: .bullish,
            updatedAt: Date().addingTimeInterval(-3600)
        )
    }

    private func loadDailyBriefings() {
        let calendar = Calendar.current
        var dateComponents = DateComponents()
        dateComponents.month = 2
        dateComponents.day = 24
        dateComponents.year = 2025
        let earningsDate = calendar.date(from: dateComponents)

        dailyBriefings = [
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

    private func loadRecentResearch() {
        recentResearch = [
            ResearchReport(
                stockTicker: "MSFT",
                stockName: "Microsoft",
                companyLogoName: "icon_microsoft",
                persona: .warrenBuffett,
                headline: "Microsoft: The AI Moat Deepens",
                summary: "Azure's AI services and UX Pilot AI partnership position MSFT as a dominant force in enterprise AI. Q4 cloud growth of 28% YoY signals strong market demand.",
                rating: 4.6,
                targetPrice: 425,
                createdAt: Date().addingTimeInterval(-10800),
                gradientColors: ["0078D4", "00BCF2"]
            ),
            ResearchReport(
                stockTicker: "GOOGL",
                stockName: "Google",
                companyLogoName: "icon_google",
                persona: .peterLynch,
                headline: "Google: Gemini's Market Impact",
                summary: "Gemini AI integration across products shows promise. Search market share stable while cloud business accelerates with 26% growth.",
                rating: 4.2,
                targetPrice: 155,
                createdAt: Date().addingTimeInterval(-345600),
                gradientColors: ["4285F4", "34A853"]
            ),
            ResearchReport(
                stockTicker: "AMD",
                stockName: "AMD",
                companyLogoName: "icon_amd",
                persona: .cathieWood,
                headline: "AMD: AI Chip Wars Heat Up",
                summary: "MI300 series gaining traction in data centers. While trailing NVIDIA, AMD's competitive pricing and supply availability create opportunities.",
                rating: 3.3,
                targetPrice: 23,
                createdAt: Date().addingTimeInterval(-432000),
                gradientColors: ["ED1C24", "FF6B6B"]
            )
        ]
    }

    // MARK: - Helpers
    private func generateSparklineData(positive: Bool) -> [Double] {
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
