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
    case index = "Index"
    case commodity = "Commodity"

    var iconName: String {
        switch self {
        case .stock: return "chart.line.uptrend.xyaxis"
        case .person: return "person.fill"
        case .etf: return "chart.pie.fill"
        case .crypto: return "bitcoinsign.circle.fill"
        case .index: return "chart.bar.fill"
        case .commodity: return "cube.fill"
        }
    }
}

// MARK: - Search Result Item
struct SearchResultItem: Identifiable {
    let id = UUID()
    let type: SearchResultType
    let rawType: String
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

// MARK: - Search History Entry

/// One row of the user's own search history: a ticker they opened, or a question they asked
/// Cay AI from this screen.
///
/// WHY THIS TYPE EXISTS. "Recent Searches" used to render `SearchViewModel.recentSearches`,
/// which was the LIVE results array — reassigned on every keystroke and emptied the moment the
/// field cleared. So the section could only ever say "No recent searches" at rest, and nothing
/// anywhere recorded a ticker the user opened or a question they asked. This is the record that
/// was missing; `SearchHistoryStore` owns it.
struct SearchHistoryEntry: Identifiable, Codable, Equatable {
    enum Kind: String, Codable {
        case ticker
        /// ⚠️ **Do not delete this case, even though nothing writes it any more.**
        ///
        /// Search stopped being able to ask Cay AI, so `record(question:)` is gone and
        /// `SearchHistoryStore.load` filters these out — but installs upgrading from an earlier
        /// build still have `"kind":"question"` rows sitting in `UserDefaults`. `load` decodes
        /// the array in ONE shot and its `catch` deletes the entire blob, so removing this case
        /// would throw on those rows and wipe the user's TICKER history along with them.
        ///
        /// Pinned by `tests/test_ios_search_history_guards.py`.
        case question
    }

    let id: UUID
    let kind: Kind
    /// The ticker symbol, or the question exactly as the user typed it.
    let text: String
    /// Company name for a ticker ("Apple Inc."), or the asset label ("Crypto" / "ETF"). Nil for
    /// a question — the text is the whole content.
    let subtitle: String?
    /// `"stock"` / `"crypto"` / `"etf"` / `"fund"`, carried so a tapped ticker rebuilds the same
    /// `SearchSelection` the live result would have. WITHOUT IT every replayed tap opens
    /// `TickerDetailView`, so a crypto or an ETF silently reopens as a stock.
    let rawType: String?
    let createdAt: Date

    init(
        id: UUID = UUID(),
        kind: Kind,
        text: String,
        subtitle: String? = nil,
        rawType: String? = nil,
        createdAt: Date = Date()
    ) {
        self.id = id
        self.kind = kind
        self.text = text
        self.subtitle = subtitle
        self.rawType = rawType
        self.createdAt = createdAt
    }

    /// The icon for the row. A question is visually an AI prompt, not a symbol lookup.
    var iconName: String {
        switch kind {
        case .ticker: return "magnifyingglass"
        case .question: return AppSymbols.ai
        }
    }
}

// MARK: - Search Book Item
struct SearchBookItem: Identifiable {
    let id = UUID()
    let title: String
    let author: String
    let description: String
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

extension SearchResultItem {
    static let sampleData: [SearchResultItem] = [
        SearchResultItem(
            type: .stock,
            rawType: "stock",
            ticker: "AAPL",
            name: "Apple Inc.",
            subtitle: "Technology",
            imageName: nil,
            isFollowable: false,
            isFollowing: false
        ),
        SearchResultItem(
            type: .stock,
            rawType: "stock",
            ticker: "TSLA",
            name: "Tesla Inc.",
            subtitle: "Automotive",
            imageName: nil,
            isFollowable: false,
            isFollowing: false
        ),
        SearchResultItem(
            type: .person,
            rawType: "person",
            ticker: nil,
            name: "Nancy Pelosi",
            subtitle: "U.S. Representative",
            imageName: "avatar_nancy_pelosi",
            isFollowable: true,
            isFollowing: false
        ),
        SearchResultItem(
            type: .stock,
            rawType: "stock",
            ticker: "MSFT",
            name: "Microsoft Corp.",
            subtitle: "Technology",
            imageName: nil,
            isFollowable: false,
            isFollowing: false
        ),
        SearchResultItem(
            type: .person,
            rawType: "person",
            ticker: nil,
            name: "Michael Burry",
            subtitle: "Scion Asset Management",
            imageName: "avatar_michael_burry",
            isFollowable: true,
            isFollowing: false
        )
    ]
}

extension SearchBookItem {
    static let sampleData: [SearchBookItem] = [
        SearchBookItem(
            title: "The Intelligent Investor",
            author: "Benjamin Graham",
            description: "The Bible of Value Investing. Warren Buffett's #1 recommended book.",
            pageCount: 623,
            publishedYear: 1949,
            rating: 4.9
        ),
        SearchBookItem(
            title: "One Up On Wall Street",
            author: "Peter Lynch",
            description: "How to use what you already know to make money in the market.",
            pageCount: 304,
            publishedYear: 1989,
            rating: 4.8
        )
    ]
}
