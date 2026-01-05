//
//  SearchModels.swift
//  ios
//
//  Data models for the Search screen
//

import Foundation
import SwiftUI

// MARK: - Search Result Type
enum SearchResultType: String, CaseIterable {
    case stock = "Stock"
    case person = "Person"
    case etf = "ETF"
    case crypto = "Crypto"

    var iconName: String {
        switch self {
        case .stock: return "chart.line.uptrend.xyaxis"
        case .person: return "person.fill"
        case .etf: return "chart.pie.fill"
        case .crypto: return "bitcoinsign.circle.fill"
        }
    }
}

// MARK: - Search Result Item
struct SearchResultItem: Identifiable {
    let id = UUID()
    let type: SearchResultType
    let ticker: String?
    let name: String
    let subtitle: String
    let imageName: String?
    let isFollowable: Bool
    let isFollowing: Bool

    var displayTicker: String? {
        ticker
    }

    var hasProfileImage: Bool {
        type == .person
    }
}

// MARK: - Search Query Suggestion
struct SearchQuerySuggestion: Identifiable {
    let id = UUID()
    let text: String
    let iconName: String?

    var hasIcon: Bool {
        iconName != nil
    }
}

// MARK: - Search News Item
struct SearchNewsItem: Identifiable {
    let id = UUID()
    let source: String
    let timeAgo: String
    let headline: String
    let summary: String
    let imageName: String
    let readMoreAction: String

    var formattedMeta: String {
        "\(source)  \(timeAgo)"
    }
}

// MARK: - Search Book Item
struct SearchBookItem: Identifiable {
    let id = UUID()
    let title: String
    let author: String
    let description: String
    let coverImageName: String
    let pageCount: Int
    let publishedYear: Int
    let rating: Double

    var formattedRating: String {
        String(format: "%.1f", rating)
    }

    var formattedPages: String {
        "\(pageCount) pages"
    }

    var formattedPublished: String {
        "Published \(publishedYear)"
    }
}

// MARK: - Sample Data Extensions
extension SearchQuerySuggestion {
    static let sampleData: [SearchQuerySuggestion] = [
        SearchQuerySuggestion(text: "What is P/E ratio?", iconName: nil),
        SearchQuerySuggestion(text: "Best tech stocks", iconName: nil),
        SearchQuerySuggestion(text: "Market trends", iconName: nil),
        SearchQuerySuggestion(text: "Why APPLE moved today?", iconName: nil)
    ]
}

extension SearchResultItem {
    static let sampleData: [SearchResultItem] = [
        SearchResultItem(
            type: .stock,
            ticker: "AAPL",
            name: "Apple Inc.",
            subtitle: "Technology",
            imageName: nil,
            isFollowable: false,
            isFollowing: false
        ),
        SearchResultItem(
            type: .stock,
            ticker: "TSLA",
            name: "Tesla Inc.",
            subtitle: "Automotive",
            imageName: nil,
            isFollowable: false,
            isFollowing: false
        ),
        SearchResultItem(
            type: .person,
            ticker: nil,
            name: "Nancy Pelosi",
            subtitle: "U.S. Representative",
            imageName: "avatar_nancy_pelosi",
            isFollowable: true,
            isFollowing: false
        ),
        SearchResultItem(
            type: .stock,
            ticker: "MSFT",
            name: "Microsoft Corp.",
            subtitle: "Technology",
            imageName: nil,
            isFollowable: false,
            isFollowing: false
        ),
        SearchResultItem(
            type: .person,
            ticker: nil,
            name: "Michael Burry",
            subtitle: "Scion Asset Management",
            imageName: "avatar_michael_burry",
            isFollowable: true,
            isFollowing: false
        )
    ]
}

extension SearchNewsItem {
    static let sampleData: [SearchNewsItem] = [
        SearchNewsItem(
            source: "TechCrunch",
            timeAgo: "8 hours ago",
            headline: "Fed Signals Potential Rate Cuts in 2024",
            summary: "Federal Reserve hints at monetary policy shifts that could impact market dynamics...",
            imageName: "news_fed_rates",
            readMoreAction: "Read More"
        ),
        SearchNewsItem(
            source: "CNBC",
            timeAgo: "8 hours ago",
            headline: "Apple Announces Revolutionary AI Features Coming to iPhone 16 Pro",
            summary: "Tech giant unveils groundbreaking artificial intelligence capabilities that could reshape the smartphone industry and boost stock performance...",
            imageName: "news_apple_ai",
            readMoreAction: "Read More"
        ),
        SearchNewsItem(
            source: "Reuters",
            timeAgo: "10 hours ago",
            headline: "Bitcoin Reaches New All-Time High Above $68K",
            summary: "Cryptocurrency markets rally as institutional adoption continues to grow worldwide...",
            imageName: "news_bitcoin",
            readMoreAction: "Read More"
        )
    ]
}

extension SearchBookItem {
    static let sampleData: [SearchBookItem] = [
        SearchBookItem(
            title: "The Intelligent Investor",
            author: "Benjamin Graham",
            description: "The Bible of Value Investing. Warren Buffett's #1 recommended book.",
            coverImageName: "book_intelligent_investor",
            pageCount: 623,
            publishedYear: 1949,
            rating: 4.9
        ),
        SearchBookItem(
            title: "One Up On Wall Street",
            author: "Peter Lynch",
            description: "How to use what you already know to make money in the market.",
            coverImageName: "book_one_up_wall_street",
            pageCount: 304,
            publishedYear: 1989,
            rating: 4.8
        )
    ]
}
