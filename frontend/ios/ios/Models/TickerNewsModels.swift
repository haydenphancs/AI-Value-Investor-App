//
//  TickerNewsModels.swift
//  ios
//
//  Data models for the Ticker Detail News tab
//

import Foundation
import SwiftUI

// MARK: - Ticker News Article
struct TickerNewsArticle: Identifiable {
    let id = UUID()
    let headline: String
    let source: NewsSource
    let sentiment: NewsSentiment
    let publishedAt: Date
    let thumbnailName: String?
    let relatedTickers: [String]
    let summaryBullets: [String]
    let articleURL: URL?

    var timeAgo: String {
        let interval = Date().timeIntervalSince(publishedAt)
        let minutes = Int(interval / 60)
        let hours = Int(interval / 3600)
        let days = Int(interval / 86400)

        if minutes < 60 {
            return "\(minutes)m ago"
        } else if hours < 24 {
            return "\(hours)h ago"
        } else if days == 1 {
            return "Yesterday"
        } else if days < 7 {
            return "\(days)d ago"
        } else {
            let formatter = DateFormatter()
            formatter.dateFormat = "MMM d"
            return formatter.string(from: publishedAt)
        }
    }

    var hasSummary: Bool {
        !summaryBullets.isEmpty
    }
}

// MARK: - Sample Data
extension TickerNewsArticle {
    static func sampleDataForTicker(_ symbol: String) -> [TickerNewsArticle] {
        let now = Date()

        return [
            TickerNewsArticle(
                headline: "Apple announces record-breaking Q4 earnings, exceeds analyst...",
                source: NewsSource(name: "Bloomberg", iconName: "icon_bloomberg"),
                sentiment: .positive,
                publishedAt: now.addingTimeInterval(-2 * 3600), // 2h ago
                thumbnailName: "news_thumbnail_earnings",
                relatedTickers: [symbol, "MSFT"],
                summaryBullets: [],
                articleURL: URL(string: "https://bloomberg.com/news/apple-q4")
            ),
            TickerNewsArticle(
                headline: "Apple releases iOS 18.2 beta with new AI features for developers",
                source: NewsSource(name: "TechCrunch", iconName: "icon_techcrunch"),
                sentiment: .neutral,
                publishedAt: now.addingTimeInterval(-4 * 3600), // 4h ago
                thumbnailName: "news_thumbnail_ios",
                relatedTickers: [symbol],
                summaryBullets: [],
                articleURL: URL(string: "https://techcrunch.com/apple-ios-18-2")
            ),
            TickerNewsArticle(
                headline: "Apple Vision Pro demand surges in international markets ahead of launch",
                source: NewsSource(name: "Reuters", iconName: "icon_reuters"),
                sentiment: .positive,
                publishedAt: now.addingTimeInterval(-6 * 3600), // 6h ago
                thumbnailName: "news_thumbnail_vision_pro",
                relatedTickers: [symbol, "META"],
                summaryBullets: [],
                articleURL: URL(string: "https://reuters.com/apple-vision-pro")
            ),
            TickerNewsArticle(
                headline: "Apple faces regulatory scrutiny over App Store policies in European Union",
                source: NewsSource(name: "Financial Times", iconName: "icon_ft"),
                sentiment: .negative,
                publishedAt: now.addingTimeInterval(-8 * 3600), // 8h ago
                thumbnailName: "news_thumbnail_eu",
                relatedTickers: [symbol, "GOOGL"],
                summaryBullets: [
                    "High Pre-Orders Abroad: Apple is seeing unusually strong pre-order numbers in Europe and Asia, indicating strong international interest before the official launch.",
                    "Supply Chain Scaling: Apple is ramping up production and logistics overseas to meet anticipated demand and prevent stock shortages.",
                    "Premium Market Appeal: Early excitement suggests that Apple's Vision Pro is resonating with tech enthusiasts and luxury consumers globally."
                ],
                articleURL: URL(string: "https://ft.com/apple-eu-regulation")
            ),
            TickerNewsArticle(
                headline: "Apple suppliers report strong orders for upcoming iPhone 16 production",
                source: NewsSource(name: "The Wall Street Journal", iconName: "icon_wsj"),
                sentiment: .positive,
                publishedAt: now.addingTimeInterval(-24 * 3600), // Yesterday
                thumbnailName: "news_thumbnail_iphone",
                relatedTickers: [symbol],
                summaryBullets: [],
                articleURL: URL(string: "https://wsj.com/apple-iphone-16")
            )
        ]
    }
}

// MARK: - News Source Brand Color Extension
extension NewsSource {
    var brandColorHex: String {
        switch name.lowercased() {
        case "bloomberg":
            return "2800D7" // Purple
        case "techcrunch":
            return "0A9E25" // Green
        case "reuters":
            return "FF6600" // Orange
        case "financial times", "ft":
            return "FCD0A1" // Salmon/Peach
        case "the wall street journal", "wsj", "wall street journal":
            return "0274B6" // Blue
        case "cnbc":
            return "6366F1" // Indigo
        case "marketwatch":
            return "00AC4E" // Green
        case "zacks":
            return "0066CC" // Blue
        default:
            return "3B82F6" // Default blue
        }
    }
}
