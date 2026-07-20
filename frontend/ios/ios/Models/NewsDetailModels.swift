//
//  NewsDetailModels.swift
//  ios
//
//  Data models for the News Detail screen
//

import Foundation

// MARK: - Key Takeaway
struct KeyTakeaway: Identifiable {
    let id = UUID()
    let index: Int
    let text: String
}

// MARK: - News Article Detail
struct NewsArticleDetail: Identifiable {
    let id = UUID()
    let headline: String
    let source: NewsSource
    /// Optional: an article that has not been AI-enriched has no sentiment.
    let sentiment: NewsSentiment?
    let publishedAt: Date
    /// Nil when the article body is unavailable — the chip is hidden rather
    /// than showing an invented figure (it used to be a hardcoded `4`).
    let readTimeMinutes: Int?
    let heroImageName: String?
    /// Remote hero image from the publisher.
    let heroImageURL: URL?
    let relatedTickers: [String]
    let keyTakeaways: [KeyTakeaway]
    let articleURL: URL?

    var formattedDate: String {
        Self.dateFormatter.string(from: publishedAt)
    }

    /// One shared formatter. Constructing a `DateFormatter` per access is a
    /// known hot-path cost, and the fixed locale keeps the format stable
    /// regardless of the device's region settings.
    private static let dateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM d, yyyy"
        f.locale = Locale(identifier: "en_US_POSIX")
        return f
    }()

    var formattedReadTime: String? {
        readTimeMinutes.map { "\($0) min read" }
    }

    // Convert from NewsArticle
    init(
        from article: NewsArticle,
        keyTakeaways: [KeyTakeaway],
        heroImageName: String? = nil,
        readTimeMinutes: Int? = nil,
        articleURL: URL? = nil
    ) {
        self.headline = article.headline
        self.source = article.source
        self.sentiment = article.sentiment
        self.publishedAt = article.publishedAt
        self.readTimeMinutes = readTimeMinutes
        self.heroImageName = heroImageName ?? article.thumbnailName
        self.heroImageURL = article.imageURL
        self.relatedTickers = article.relatedTickers
        self.keyTakeaways = keyTakeaways
        self.articleURL = articleURL ?? article.articleURL
    }

    init(
        headline: String,
        source: NewsSource,
        sentiment: NewsSentiment?,
        publishedAt: Date,
        readTimeMinutes: Int?,
        heroImageName: String?,
        heroImageURL: URL? = nil,
        relatedTickers: [String],
        keyTakeaways: [KeyTakeaway],
        articleURL: URL?
    ) {
        self.headline = headline
        self.source = source
        self.sentiment = sentiment
        self.publishedAt = publishedAt
        self.readTimeMinutes = readTimeMinutes
        self.heroImageName = heroImageName
        self.heroImageURL = heroImageURL
        self.relatedTickers = relatedTickers
        self.keyTakeaways = keyTakeaways
        self.articleURL = articleURL
    }
}

// MARK: - News Source Extension
extension NewsSource {
    /// Brand colour for the source tile.
    ///
    /// Matches on `displayName` (domain suffix stripped), not the raw `name`:
    /// the backend supplies values like "cnbc.com", which never matched the raw
    /// switch and silently fell through to the default blue for every publisher.
    var brandColor: String {
        switch displayName.lowercased() {
        case "cnbc": return "6366F1"   // Indigo/purple
        case "reuters": return "FF6600"  // Orange
        case "bloomberg": return "2800D7" // Purple
        case "wsj", "wall street journal": return "000000" // Black
        case "zacks": return "0066CC" // Blue
        case "marketwatch": return "00AC4E" // Green
        default: return "3B82F6" // Default blue
        }
    }
}
