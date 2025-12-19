import Foundation

// MARK: - News Models

struct NewsArticle: Codable, Identifiable {
    let id: String
    let sourceName: String
    let sourceUrl: String
    let externalId: String?
    let title: String
    let summary: String?
    let content: String?
    let imageUrl: String?
    let author: String?
    let publishedAt: Date
    let sentiment: SentimentType?
    let sentimentEmoji: String?
    let relevanceScore: Double?
    let impactScore: Double?
    let aiSummary: String?
    let aiSummaryBullets: [String]?
    let relatedStocks: [RelatedStock]?
    let createdAt: Date
    let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, title, summary, content, author, sentiment
        case sourceName = "source_name"
        case sourceUrl = "source_url"
        case externalId = "external_id"
        case imageUrl = "image_url"
        case publishedAt = "published_at"
        case sentimentEmoji = "sentiment_emoji"
        case relevanceScore = "relevance_score"
        case impactScore = "impact_score"
        case aiSummary = "ai_summary"
        case aiSummaryBullets = "ai_summary_bullets"
        case relatedStocks = "related_stocks"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    var displaySentimentEmoji: String {
        sentimentEmoji ?? sentiment?.emoji ?? "•"
    }
}

struct RelatedStock: Codable, Identifiable {
    let id: String
    let ticker: String
    let companyName: String
    let logoUrl: String?

    enum CodingKeys: String, CodingKey {
        case id, ticker
        case companyName = "company_name"
        case logoUrl = "logo_url"
    }
}

struct BreakingNews: Codable, Identifiable {
    let id: String
    let newsId: String
    let headline: String
    let sentiment: SentimentType
    let sentimentEmoji: String
    let ticker: String?
    let companyName: String?
    let logoUrl: String?
    let impactScore: Double
    let publishedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, headline, sentiment, ticker
        case newsId = "news_id"
        case sentimentEmoji = "sentiment_emoji"
        case companyName = "company_name"
        case logoUrl = "logo_url"
        case impactScore = "impact_score"
        case publishedAt = "published_at"
    }
}

struct NewsFeedItem: Codable, Identifiable {
    let id: String
    let title: String
    let aiSummaryBullets: [String]?
    let sentiment: SentimentType?
    let sentimentEmoji: String?
    let publishedAt: Date
    let sourceName: String
    let imageUrl: String?

    enum CodingKeys: String, CodingKey {
        case id, title, sentiment
        case aiSummaryBullets = "ai_summary_bullets"
        case sentimentEmoji = "sentiment_emoji"
        case publishedAt = "published_at"
        case sourceName = "source_name"
        case imageUrl = "image_url"
    }
}
