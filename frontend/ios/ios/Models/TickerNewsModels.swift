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
    let apiId: String          // Backend article ID (for enrichment requests)
    let headline: String
    let source: NewsSource
    var sentiment: NewsSentiment
    let publishedAt: Date
    let thumbnailName: String?
    let imageURL: URL?
    let relatedTickers: [String]
    var summaryBullets: [String]
    let articleURL: URL?
    var aiProcessed: Bool

    var timeAgo: String {
        let interval = Date().timeIntervalSince(publishedAt)
        guard interval > 0 else { return "Just now" }
        let minutes = Int(interval / 60)
        let hours = Int(interval / 3600)
        let days = Int(interval / 86400)

        if minutes < 1 {
            return "Just now"
        } else if minutes < 60 {
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
                apiId: "sample_1",
                headline: "Apple announces record-breaking Q4 earnings, exceeds analyst...",
                source: NewsSource(name: "Bloomberg", iconName: "icon_bloomberg"),
                sentiment: .positive,
                publishedAt: now.addingTimeInterval(-2 * 3600), // 2h ago
                thumbnailName: nil,
                imageURL: nil,
                relatedTickers: [symbol, "MSFT"],
                summaryBullets: [],
                articleURL: URL(string: "https://bloomberg.com/news/apple-q4"),
                aiProcessed: true
            ),
            TickerNewsArticle(
                apiId: "sample_2",
                headline: "Apple releases iOS 18.2 beta with new AI features for developers",
                source: NewsSource(name: "TechCrunch", iconName: "icon_techcrunch"),
                sentiment: .neutral,
                publishedAt: now.addingTimeInterval(-4 * 3600), // 4h ago
                thumbnailName: nil,
                imageURL: nil,
                relatedTickers: [symbol],
                summaryBullets: [],
                articleURL: URL(string: "https://techcrunch.com/apple-ios-18-2"),
                aiProcessed: true
            ),
            TickerNewsArticle(
                apiId: "sample_3",
                headline: "Apple Vision Pro demand surges in international markets ahead of launch",
                source: NewsSource(name: "Reuters", iconName: "icon_reuters"),
                sentiment: .positive,
                publishedAt: now.addingTimeInterval(-6 * 3600), // 6h ago
                thumbnailName: nil,
                imageURL: nil,
                relatedTickers: [symbol, "META"],
                summaryBullets: [],
                articleURL: URL(string: "https://reuters.com/apple-vision-pro"),
                aiProcessed: true
            ),
            TickerNewsArticle(
                apiId: "sample_4",
                headline: "Apple faces regulatory scrutiny over App Store policies in European Union",
                source: NewsSource(name: "Financial Times", iconName: "icon_ft"),
                sentiment: .negative,
                publishedAt: now.addingTimeInterval(-8 * 3600), // 8h ago
                thumbnailName: nil,
                imageURL: nil,
                relatedTickers: [symbol, "GOOGL"],
                summaryBullets: [
                    "High Pre-Orders Abroad: Apple is seeing unusually strong pre-order numbers in Europe and Asia, indicating strong international interest before the official launch.",
                    "Supply Chain Scaling: Apple is ramping up production and logistics overseas to meet anticipated demand and prevent stock shortages.",
                    "Premium Market Appeal: Early excitement suggests that Apple's Vision Pro is resonating with tech enthusiasts and luxury consumers globally."
                ],
                articleURL: URL(string: "https://ft.com/apple-eu-regulation"),
                aiProcessed: true
            ),
            TickerNewsArticle(
                apiId: "sample_5",
                headline: "Apple suppliers report strong orders for upcoming iPhone 16 production",
                source: NewsSource(name: "The Wall Street Journal", iconName: "icon_wsj"),
                sentiment: .positive,
                publishedAt: now.addingTimeInterval(-24 * 3600), // Yesterday
                thumbnailName: nil,
                imageURL: nil,
                relatedTickers: [symbol],
                summaryBullets: [],
                articleURL: URL(string: "https://wsj.com/apple-iphone-16"),
                aiProcessed: true
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
