import Foundation

// MARK: - Widget Models

struct WidgetUpdate: Codable, Identifiable {
    let id: String
    let headline: String
    let sentiment: SentimentType
    let emoji: String
    let dailyTrend: String
    let marketSummary: String?
    let publishedAt: Date
    let deepLinkUrl: String?
    let linkedReportId: String?
    let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, headline, sentiment, emoji
        case dailyTrend = "daily_trend"
        case marketSummary = "market_summary"
        case publishedAt = "published_at"
        case deepLinkUrl = "deep_link_url"
        case linkedReportId = "linked_report_id"
        case createdAt = "created_at"
    }

    var isStale: Bool {
        let daysSincePublished = Calendar.current.dateComponents([.day], from: publishedAt, to: Date()).day ?? 0
        return daysSincePublished > 1
    }
}

struct WidgetTimeline: Codable {
    let pastUpdates: [WidgetUpdate]
    let futureUpdates: [WidgetUpdate]

    enum CodingKeys: String, CodingKey {
        case pastUpdates = "past_updates"
        case futureUpdates = "future_updates"
    }
}
