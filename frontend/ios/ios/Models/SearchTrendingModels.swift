//
//  SearchTrendingModels.swift
//  ios
//
//  The chips every search screen shows before the user types: "Trending searches",
//  "Most added" and the curated "Popular" fallback (GET /api/v1/search/trending).
//
//  Wire types first, UI types second, co-located per the Models convention. The backend
//  half is `backend/app/schemas/search_trending.py`; `tests/test_search_trending_schema_parity.py`
//  pins the two together.
//
//  Tolerant on purpose: every field defaults, an unknown section `kind` or item `type` is
//  DROPPED rather than failing the decode, and one malformed element never takes the rest
//  of its array with it. A newer server must never blank an older app's chips.
//

import Foundation
import OSLog

private let trendingDecodeLog = Logger(subsystem: "com.phan.caydex", category: "search-trending")

/// A never-throwing array element: a bad element decodes to `nil` and is LOGGED (silent
/// leniency would make "a chip never showed up" undiagnosable).
private struct LossyTrendingElement<Wrapped: Decodable>: Decodable {
    let value: Wrapped?

    init(from decoder: Decoder) throws {
        do {
            value = try Wrapped(from: decoder)
        } catch {
            value = nil
            trendingDecodeLog.warning("dropped \(String(describing: Wrapped.self), privacy: .public) at \(decoder.codingPath.map(\.stringValue).joined(separator: "."), privacy: .public): \(String(describing: error), privacy: .public)")
        }
    }
}

// MARK: - Wire

struct SearchTrendingItemDTO: Decodable, Equatable {
    let symbol: String
    let name: String
    let type: String

    enum CodingKeys: String, CodingKey {
        case symbol
        case name
        case type
    }

    init(symbol: String, name: String = "", type: String = "stock") {
        self.symbol = symbol
        self.name = name
        self.type = type
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        symbol = try c.decode(String.self, forKey: .symbol)
        name = (try? c.decode(String.self, forKey: .name)) ?? ""
        type = (try? c.decode(String.self, forKey: .type)) ?? "stock"
    }
}

struct SearchTrendingSectionDTO: Decodable, Equatable {
    let kind: String
    let items: [SearchTrendingItemDTO]

    enum CodingKeys: String, CodingKey {
        case kind
        case items
    }

    init(kind: String, items: [SearchTrendingItemDTO]) {
        self.kind = kind
        self.items = items
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try c.decode(String.self, forKey: .kind)
        items = ((try? c.decode([LossyTrendingElement<SearchTrendingItemDTO>].self, forKey: .items)) ?? [])
            .compactMap(\.value)
    }
}

struct SearchTrendingDTO: Decodable, Equatable {
    let windowDays: Int
    let computedAt: String
    /// Every asset type — Home search, the ticker-search sheet, the Tracking add sheet.
    let sections: [SearchTrendingSectionDTO]
    /// Stocks only, thresholded on the server AFTER filtering — the company picker and the
    /// Updates "Add Ticker" sheet.
    let stockSections: [SearchTrendingSectionDTO]

    enum CodingKeys: String, CodingKey {
        case windowDays = "window_days"
        case computedAt = "computed_at"
        case sections
        case stockSections = "stock_sections"
    }

    init(windowDays: Int = 7, computedAt: String = "",
         sections: [SearchTrendingSectionDTO], stockSections: [SearchTrendingSectionDTO]) {
        self.windowDays = windowDays
        self.computedAt = computedAt
        self.sections = sections
        self.stockSections = stockSections
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        windowDays = (try? c.decode(Int.self, forKey: .windowDays)) ?? 7
        computedAt = (try? c.decode(String.self, forKey: .computedAt)) ?? ""
        sections = ((try? c.decode([LossyTrendingElement<SearchTrendingSectionDTO>].self, forKey: .sections)) ?? [])
            .compactMap(\.value)
        stockSections = ((try? c.decode([LossyTrendingElement<SearchTrendingSectionDTO>].self, forKey: .stockSections)) ?? [])
            .compactMap(\.value)
    }
}

/// `POST /api/v1/search/picks` — one tap on a search RESULT row. The account comes from
/// the token; nothing identifying travels in the body.
nonisolated struct SearchPickRequest: Encodable, Sendable {
    let symbol: String
    let type: String
}

// MARK: - UI

/// Section kinds the app knows. Raw values equal the backend's `SECTION_KINDS`
/// (test-pinned). An unknown kind maps to nil and its section is dropped.
enum SearchTrendingKind: String {
    case trendingSearches = "trending_searches"
    case mostAdded = "most_added"
    case popular
}

struct SearchTrendingItem: Identifiable, Hashable {
    let symbol: String
    let name: String
    let type: String

    /// Byte-equal to `SearchSelection.id` and `StockSearchResult.id`: BTC the coin and BTC
    /// the ETF are two chips, and two `ForEach` children with one id is SwiftUI's
    /// "undefined results" (the BTC row once opened the ETF screen).
    var id: String { "\(symbol)_\(type)" }

    var selection: SearchSelection { SearchSelection(symbol: symbol, type: type) }

    /// For the surfaces whose selection path takes a search row (the company picker, the
    /// Tracking add sheet). The name falls back to the symbol so no row ever reads blank.
    var stockSearchResult: StockSearchResult {
        StockSearchResult(
            ticker: symbol,
            companyName: name.isEmpty ? symbol : name,
            exchange: nil,
            sector: nil,
            logoUrl: nil,
            type: type
        )
    }

    /// The chip draws only the symbol; VoiceOver reads the company too.
    var accessibilityText: String { name.isEmpty ? symbol : "\(symbol), \(name)" }
}

struct SearchTrendingSection: Identifiable, Equatable {
    static let maxItems = 8
    static let supportedTypes: Set<String> = ["stock", "etf", "fund", "crypto"]

    let kind: SearchTrendingKind
    let items: [SearchTrendingItem]
    let windowDays: Int

    var id: String { kind.rawValue }

    /// Neutral wording on purpose — no counts, no rank marks, no "hot"/"top". The lists are
    /// general and impersonal, never a recommendation (test-pinned word list).
    var title: String {
        switch kind {
        case .trendingSearches: return "Trending searches · \(windowDays) days"
        case .mostAdded: return "Most added · \(windowDays) days"
        case .popular: return "Popular"
        }
    }

    /// Built from what people in Caydex actually did — as opposed to the curated list.
    var isLive: Bool { kind != .popular }

    init(kind: SearchTrendingKind, items: [SearchTrendingItem], windowDays: Int) {
        self.kind = kind
        self.items = items
        self.windowDays = windowDays
    }

    /// nil for an unknown kind or a section with nothing usable left.
    init?(dto: SearchTrendingSectionDTO, windowDays: Int, stocksOnly: Bool) {
        guard let kind = SearchTrendingKind(rawValue: dto.kind) else { return nil }
        var seen = Set<String>()
        let items: [SearchTrendingItem] = dto.items.compactMap { raw in
            let symbol = raw.symbol.trimmingCharacters(in: .whitespacesAndNewlines).uppercased()
            let type = raw.type.lowercased()
            guard !symbol.isEmpty, Self.supportedTypes.contains(type) else { return nil }
            // Belt and braces: the server already filtered stock_sections, but these
            // surfaces act on equities only (the company pipeline, the news feed).
            if stocksOnly && type != "stock" { return nil }
            let item = SearchTrendingItem(
                symbol: symbol,
                name: raw.name.trimmingCharacters(in: .whitespacesAndNewlines),
                type: type
            )
            return seen.insert(item.id).inserted ? item : nil
        }
        guard !items.isEmpty else { return nil }
        self.init(kind: kind, items: Array(items.prefix(Self.maxItems)), windowDays: windowDays)
    }

    /// The OFFLINE floor, shown until the first fetch lands (or when it cannot). A subset of
    /// `backend/data/search_trending_popular.json` (test-pinned); the server's copy is the
    /// one that changes without an app update.
    static func bundledPopular(stocksOnly: Bool) -> SearchTrendingSection {
        let items = stocksOnly ? bundledStocks : bundledAll
        return SearchTrendingSection(kind: .popular, items: items, windowDays: 7)
    }

    static let bundledAll: [SearchTrendingItem] = [
        SearchTrendingItem(symbol: "AAPL", name: "Apple Inc.", type: "stock"),
        SearchTrendingItem(symbol: "NVDA", name: "NVIDIA Corporation", type: "stock"),
        SearchTrendingItem(symbol: "MSFT", name: "Microsoft Corporation", type: "stock"),
        SearchTrendingItem(symbol: "SPY", name: "SPDR S&P 500 ETF Trust", type: "etf"),
        SearchTrendingItem(symbol: "TSLA", name: "Tesla, Inc.", type: "stock"),
        SearchTrendingItem(symbol: "AMZN", name: "Amazon.com, Inc.", type: "stock"),
        SearchTrendingItem(symbol: "BTC", name: "Bitcoin", type: "crypto"),
        SearchTrendingItem(symbol: "GOOGL", name: "Alphabet Inc.", type: "stock"),
    ]

    static let bundledStocks: [SearchTrendingItem] = [
        SearchTrendingItem(symbol: "AAPL", name: "Apple Inc.", type: "stock"),
        SearchTrendingItem(symbol: "MSFT", name: "Microsoft Corporation", type: "stock"),
        SearchTrendingItem(symbol: "NVDA", name: "NVIDIA Corporation", type: "stock"),
        SearchTrendingItem(symbol: "AMZN", name: "Amazon.com, Inc.", type: "stock"),
        SearchTrendingItem(symbol: "GOOGL", name: "Alphabet Inc.", type: "stock"),
        SearchTrendingItem(symbol: "META", name: "Meta Platforms, Inc.", type: "stock"),
        SearchTrendingItem(symbol: "TSLA", name: "Tesla, Inc.", type: "stock"),
        SearchTrendingItem(symbol: "BRK-B", name: "Berkshire Hathaway Inc.", type: "stock"),
    ]
}

// MARK: - Preview data

extension SearchTrendingSection {
    static let previewLive: [SearchTrendingSection] = [
        SearchTrendingSection(kind: .trendingSearches, items: [
            SearchTrendingItem(symbol: "NVDA", name: "NVIDIA Corporation", type: "stock"),
            SearchTrendingItem(symbol: "TSLA", name: "Tesla, Inc.", type: "stock"),
            SearchTrendingItem(symbol: "PLTR", name: "Palantir Technologies Inc.", type: "stock"),
            SearchTrendingItem(symbol: "AMD", name: "Advanced Micro Devices, Inc.", type: "stock"),
            SearchTrendingItem(symbol: "SOFI", name: "SoFi Technologies, Inc.", type: "stock"),
            SearchTrendingItem(symbol: "BTC", name: "Bitcoin", type: "crypto"),
        ], windowDays: 7),
        SearchTrendingSection(kind: .mostAdded, items: [
            SearchTrendingItem(symbol: "AVGO", name: "Broadcom Inc.", type: "stock"),
            SearchTrendingItem(symbol: "HOOD", name: "Robinhood Markets, Inc.", type: "stock"),
            SearchTrendingItem(symbol: "RKLB", name: "Rocket Lab Corporation", type: "stock"),
            SearchTrendingItem(symbol: "MSFT", name: "Microsoft Corporation", type: "stock"),
            SearchTrendingItem(symbol: "ETH", name: "Ethereum", type: "crypto"),
        ], windowDays: 7),
    ]

    static let previewPopular: [SearchTrendingSection] = [bundledPopular(stocksOnly: false)]
}
