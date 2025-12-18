import Foundation

struct WidgetHeadline: Decodable, Identifiable { let id = UUID(); let headline: String; let sentiment: Sentiment; let emoji: String; let daily_trend: String; let market_summary: String? }

struct NewsItem: Decodable, Identifiable { let id: String; let title: String; let ai_summary_bullets: [String]?; let sentiment: Sentiment; let sentiment_emoji: String; let published_at: Date; let source_name: String; let image_url: URL?; let stock_ticker: String?; let impact_score: Double? }

struct NewsDetail: Decodable { let id: String; let title: String; let image_url: URL?; let published_at: Date; let source_name: String; let ai_summary: String; let ai_summary_bullets: [String]; let sentiment: Sentiment; let sentiment_emoji: String; let related_stocks: [RelatedStock]; let content: String? }

struct RelatedStock: Decodable, Identifiable { let id = UUID(); let ticker: String; let company_name: String; let logo_url: URL? }

struct StockSearchResult: Decodable, Identifiable { let id = UUID(); let ticker: String; let company_name: String; let sector: String?; let market_cap: Double?; let logo_url: URL? }

struct StockDetail: Decodable { let ticker: String; let company_name: String; let logo_url: URL?; let sector: String?; let industry: String?; let exchange: String?; let market_cap: Double?; let description: String?; let website: URL? }

struct FundamentalsPoint: Decodable, Identifiable { let id = UUID(); let fiscal_year: Int; let fiscal_quarter: Int; let revenue: Double?; let net_income: Double?; let eps: Double? }

struct Earnings: Decodable { let earnings_date: Date; let eps_estimate: Double?; let revenue_estimate: Double? }

struct WatchlistItem: Decodable, Identifiable { let id: String; let stock: StockSummary; let alert_on_news: Bool; let custom_notes: String?; let has_breaking_news: Bool; let added_at: Date }

struct StockSummary: Decodable { let ticker: String; let company_name: String; let logo_url: URL? }

struct ResearchReport: Decodable, Identifiable { let id: String; let title: String; let executive_summary: String; let investor_persona: Persona; let persona_emoji: String; let status: ReportStatus; let stock: StockSummary; let created_at: Date; let user_rating: Int? }

struct ChatSession: Decodable, Identifiable { let id: String; let title: String; let session_type: ChatType; let session_emoji: String; let message_count: Int; let last_message_at: Date; let preview_message: String?; let content_title: String?; let stock_ticker: String? }

struct ChatMessage: Decodable, Identifiable { let id: String; let role: String; let content: String; let created_at: Date; let citations: [String]? }

struct EducationContent: Decodable, Identifiable { let id: String; let type: String; let title: String; let author: String?; let publication_year: Int?; let summary: String; let cover_image_url: URL?; let chunk_count: Int; let topics: [String] }

struct UserProfile: Decodable { let email: String; let full_name: String?; let tier: UserTier }

struct Usage: Decodable { let deep_research: UsageBucket; let reset_at: Date }
struct UsageBucket: Decodable { let used: Int; let limit: Int?; let remaining: String }

struct UserStats: Decodable { let watchlist_count: Int; let reports_generated: Int; let chat_sessions: Int; let last_activity: Date? }

enum Sentiment: String, Decodable { case bullish, bearish, neutral }

enum Persona: String, Decodable, CaseIterable { case buffett, ackman, munger, lynch, graham }

enum ReportStatus: String, Decodable { case pending, processing, completed, failed }

enum ChatType: String, Decodable { case education, stock_analysis, general }

enum UserTier: String, Decodable { case free, pro, premium }

struct Paginated<T: Decodable>: Decodable { let items: [T]; let nextCursor: String? }
