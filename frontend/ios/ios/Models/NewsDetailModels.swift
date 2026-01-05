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
    let sentiment: NewsSentiment
    let publishedAt: Date
    let readTimeMinutes: Int
    let heroImageName: String?
    let relatedTickers: [String]
    let keyTakeaways: [KeyTakeaway]
    let articleURL: URL?

    var formattedDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, yyyy"
        return formatter.string(from: publishedAt)
    }

    var formattedReadTime: String {
        "\(readTimeMinutes) min read"
    }

    // Convert from NewsArticle
    init(from article: NewsArticle, keyTakeaways: [KeyTakeaway], heroImageName: String? = nil, readTimeMinutes: Int = 4, articleURL: URL? = nil) {
        self.headline = article.headline
        self.source = article.source
        self.sentiment = article.sentiment
        self.publishedAt = article.publishedAt
        self.readTimeMinutes = readTimeMinutes
        self.heroImageName = heroImageName ?? article.thumbnailName
        self.relatedTickers = article.relatedTickers
        self.keyTakeaways = keyTakeaways
        self.articleURL = articleURL
    }

    init(headline: String, source: NewsSource, sentiment: NewsSentiment, publishedAt: Date, readTimeMinutes: Int, heroImageName: String?, relatedTickers: [String], keyTakeaways: [KeyTakeaway], articleURL: URL?) {
        self.headline = headline
        self.source = source
        self.sentiment = sentiment
        self.publishedAt = publishedAt
        self.readTimeMinutes = readTimeMinutes
        self.heroImageName = heroImageName
        self.relatedTickers = relatedTickers
        self.keyTakeaways = keyTakeaways
        self.articleURL = articleURL
    }
}

// MARK: - News Source Extension
extension NewsSource {
    // Source brand colors
    var brandColor: String {
        switch name.lowercased() {
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
