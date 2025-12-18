import Foundation

enum NewsServiceError: Error { case failed }

struct NewsService {
  var fetchFeed: @Sendable (_ page: Int, _ sentiment: Sentiment?) async throws -> Paginated<NewsItem>
  var fetchDetail: @Sendable (_ id: String) async throws -> NewsDetail
  var markRead: @Sendable (_ id: String) async throws -> Void
}

extension NewsService {
  static var mock: NewsService {
    .init(
      fetchFeed: { page, sentiment in
        try? await Task.sleep(nanoseconds: 350_000_000)
        let baseTitles = [
          "Fed signals steady rates into Q1",
          "Apple unveils new AI silicon",
          "Oil dips as supply concerns ease",
          "Tesla beats deliveries, margins compress",
          "Chipmakers rally on data center demand"
        ]
        let sentiments: [Sentiment] = [.bullish, .bearish, .neutral]
        let items: [NewsItem] = (0..<20).map { idx in
          let id = "n_\(page)_\(idx)"
          let s = sentiment ?? sentiments[idx % sentiments.count]
          return NewsItem(
            id: id,
            title: "\(baseTitles[idx % baseTitles.count]) — page \(page) item \(idx+1)",
            ai_summary_bullets: [
              "Key point one for \(id)",
              "Key point two",
              "Key point three"
            ],
            sentiment: s,
            sentiment_emoji: s == .bullish ? "📈" : (s == .bearish ? "📉" : "🤝"),
            published_at: Date().addingTimeInterval(Double(-idx * 3600)),
            source_name: ["Bloomberg","Reuters","WSJ","CNBC"][idx % 4],
            image_url: nil,
            stock_ticker: ["AAPL","TSLA","NVDA","MSFT"][idx % 4],
            impact_score: Double(Int.random(in: 50...95))
          )
        }
        let next = page < 3 ? "cursor_\(page+1)" : nil
        return Paginated(items: items, nextCursor: next)
      },
      fetchDetail: { id in
        try? await Task.sleep(nanoseconds: 250_000_000)
        return NewsDetail(
          id: id,
          title: "Full detail for \(id)",
          image_url: nil,
          published_at: Date(),
          source_name: "Reuters",
          ai_summary: "Concise plain-English summary for \(id).",
          ai_summary_bullets: ["Bullet one","Bullet two","Bullet three"],
          sentiment: .neutral,
          sentiment_emoji: "🤝",
          related_stocks: [RelatedStock(ticker: "AAPL", company_name: "Apple Inc.", logo_url: nil)],
          content: "Full article content for demo purposes."
        )
      },
      markRead: { _ in }
    )
  }
}
