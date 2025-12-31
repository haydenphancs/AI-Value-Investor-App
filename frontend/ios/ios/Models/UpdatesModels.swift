//
//  UpdatesModels.swift
//  ios
//
//  Data models for the Updates/News screen
//

import Foundation

// MARK: - News Sentiment
enum NewsSentiment: String, CaseIterable {
    case positive = "Positive"
    case negative = "Negative"
    case neutral = "Neutral"

    var displayName: String {
        rawValue
    }
}

// MARK: - News Filter Tab
struct NewsFilterTab: Identifiable, Equatable {
    let id = UUID()
    let title: String
    let ticker: String?
    let changePercent: Double?
    let isMarketTab: Bool

    var isPositive: Bool {
        (changePercent ?? 0) >= 0
    }

    var formattedChange: String? {
        guard let change = changePercent else { return nil }
        let sign = change >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", change))%"
    }

    static func == (lhs: NewsFilterTab, rhs: NewsFilterTab) -> Bool {
        lhs.id == rhs.id
    }
}

// MARK: - News Source
struct NewsSource: Identifiable {
    let id = UUID()
    let name: String
    let iconName: String?

    var systemIconName: String {
        "newspaper.fill"
    }
}

// MARK: - News Article
struct NewsArticle: Identifiable {
    let id = UUID()
    let headline: String
    let summary: String?
    let source: NewsSource
    let sentiment: NewsSentiment
    let publishedAt: Date
    let thumbnailName: String?
    let relatedTickers: [String]

    var formattedTime: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        return formatter.string(from: publishedAt)
    }

    var isToday: Bool {
        Calendar.current.isDateInToday(publishedAt)
    }

    var isYesterday: Bool {
        Calendar.current.isDateInYesterday(publishedAt)
    }

    var sectionTitle: String {
        if isToday {
            return "TODAY"
        } else if isYesterday {
            return "YESTERDAY"
        } else {
            let formatter = DateFormatter()
            formatter.dateFormat = "MMM d, yyyy"
            return formatter.string(from: publishedAt)
        }
    }
}

// MARK: - News Insight Summary
struct NewsInsightSummary: Identifiable {
    let id = UUID()
    let headline: String
    let bulletPoints: [String]
    let sentiment: MarketSentiment
    let updatedAt: Date
    let summaryType: String

    var timeAgo: String {
        let interval = Date().timeIntervalSince(updatedAt)
        let hours = Int(interval / 3600)
        if hours < 1 {
            return "Updated just now"
        } else if hours == 1 {
            return "Updated 1 hours ago"
        } else {
            return "Updated \(hours) hours ago"
        }
    }

    var summaryBadgeText: String {
        summaryType
    }
}

// MARK: - Grouped News
struct GroupedNews: Identifiable {
    let id = UUID()
    let sectionTitle: String
    let articles: [NewsArticle]
}

// MARK: - News Filter Options
struct NewsFilterOptions {
    var sources: [String]
    var sectors: [String]
    var sentiments: [NewsSentiment]

    static var `default`: NewsFilterOptions {
        NewsFilterOptions(sources: [], sectors: [], sentiments: [])
    }

    var hasActiveFilters: Bool {
        !sources.isEmpty || !sectors.isEmpty || !sentiments.isEmpty
    }
}
