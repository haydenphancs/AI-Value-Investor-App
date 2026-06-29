//
//  ChatConversationModels.swift
//  ios
//
//  Data models for rich chat conversation content
//

import Foundation
import SwiftUI

// MARK: - Chat Message Role
enum ChatMessageRole {
    case user
    case assistant
}

// MARK: - Rich Content Type
enum RichContentType {
    case text(String)
    case sentimentAnalysis(SentimentAnalysis)
    case stockPerformance(StockPerformance)
    case stockChart(StockChartWidgetData)
    case marketOverview(MarketOverviewWidgetData)
    case riskFactors(RiskFactorsData)
    case tip(TipData)
    case bulletPoints([ChatBulletPoint])
}

// MARK: - Rich Chat Message
struct RichChatMessage: Identifiable {
    let id = UUID()
    let role: ChatMessageRole
    let content: [RichContentType]
    let timestamp: Date

    var formattedTime: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        return formatter.string(from: timestamp)
    }
}

// MARK: - Stock Chart Widget (Codable — from backend)

/// Matches the backend ``StockChartWidget`` Pydantic model.
/// Uses explicit CodingKeys with snake_case raw values so the
/// default JSONDecoder (no keyDecodingStrategy) works correctly.
struct StockChartWidgetData: Codable, Identifiable {
    let id: UUID = UUID()

    let widgetType: String
    let ticker: String
    let companyName: String
    let currentPrice: Double
    let change: Double
    let changePercent: Double
    let dayHigh: Double
    let dayLow: Double
    let volume: Int
    let avgVolume: Int
    let marketCap: Double?
    let peRatio: Double?
    let yearHigh: Double?
    let yearLow: Double?
    let historicalData: [HistoricalDataPointDTO]

    enum CodingKeys: String, CodingKey {
        case widgetType = "widget_type"
        case ticker
        case companyName = "company_name"
        case currentPrice = "current_price"
        case change
        case changePercent = "change_percent"
        case dayHigh = "day_high"
        case dayLow = "day_low"
        case volume
        case avgVolume = "avg_volume"
        case marketCap = "market_cap"
        case peRatio = "pe_ratio"
        case yearHigh = "year_high"
        case yearLow = "year_low"
        case historicalData = "historical_data"
    }

    // Computed helpers for the UI
    var isPositive: Bool { changePercent >= 0 }

    var formattedPrice: String {
        String(format: "$%.2f", currentPrice)
    }

    var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", changePercent))%"
    }

    var formattedAbsChange: String {
        let sign = change >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.2f", change))"
    }

    var formattedDayHigh: String { String(format: "$%.2f", dayHigh) }
    var formattedDayLow: String { String(format: "$%.2f", dayLow) }

    var formattedVolume: String { Self.abbreviate(Double(volume)) }
    var formattedAvgVolume: String { Self.abbreviate(Double(avgVolume)) }
    var formattedMarketCap: String? {
        guard let mc = marketCap else { return nil }
        return Self.abbreviate(mc)
    }

    /// Close prices for the chart line
    var chartCloses: [Double] {
        historicalData.map(\.close)
    }

    private static func abbreviate(_ value: Double) -> String {
        switch abs(value) {
        case 1_000_000_000_000...:
            return String(format: "%.2fT", value / 1_000_000_000_000)
        case 1_000_000_000...:
            return String(format: "%.2fB", value / 1_000_000_000)
        case 1_000_000...:
            return String(format: "%.1fM", value / 1_000_000)
        case 1_000...:
            return String(format: "%.1fK", value / 1_000)
        default:
            return String(format: "%.0f", value)
        }
    }
}

struct HistoricalDataPointDTO: Codable, Identifiable {
    var id: String { date }

    let date: String
    let open: Double
    let high: Double
    let low: Double
    let close: Double
    let volume: Int
}

// MARK: - Market Overview Widget Data

struct MarketOverviewSectorEntry: Codable, Identifiable, Sendable {
    var id: String { sector }
    let sector: String
    let changePercent: Double

    var isPositive: Bool { changePercent >= 0 }
    var formattedChange: String {
        String(format: "%@%.1f%%", changePercent >= 0 ? "+" : "", changePercent)
    }

    enum CodingKeys: String, CodingKey {
        case sector
        case changePercent = "change_percent"
    }
}

struct MarketOverviewMacroEntry: Codable, Identifiable, Sendable {
    var id: String { title }
    let title: String
    let signal: String  // "positive", "neutral", "cautious"

    enum CodingKeys: String, CodingKey {
        case title, signal
    }
}

struct MarketOverviewWidgetData: Codable, Identifiable, Sendable {
    var id: String { "market_overview_\(peRatio)" }

    let widgetType: String
    let peRatio: Double
    let forwardPe: Double
    let valuationLevel: String
    let earningsYield: Double
    let historicalAvgPe: Double
    let sectors: [MarketOverviewSectorEntry]
    let advancing: Int
    let declining: Int
    let macroIndicators: [MarketOverviewMacroEntry]

    enum CodingKeys: String, CodingKey {
        case widgetType = "widget_type"
        case peRatio = "pe_ratio"
        case forwardPe = "forward_pe"
        case valuationLevel = "valuation_level"
        case earningsYield = "earnings_yield"
        case historicalAvgPe = "historical_avg_pe"
        case sectors, advancing, declining
        case macroIndicators = "macro_indicators"
    }
}

// MARK: - Polymorphic Widget Decoding

enum ChatWidgetData: Codable, Sendable {
    case stockChart(StockChartWidgetData)
    case marketOverview(MarketOverviewWidgetData)

    private enum TypeKey: String, CodingKey {
        case widgetType = "widget_type"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: TypeKey.self)

        // Try to read widget_type — if missing, fall back to stock_chart
        let type = try container.decodeIfPresent(String.self, forKey: .widgetType) ?? "stock_chart"

        switch type {
        case "market_overview":
            self = .marketOverview(try MarketOverviewWidgetData(from: decoder))
        default:
            // Default to stock chart for backward compatibility
            self = .stockChart(try StockChartWidgetData(from: decoder))
        }
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .stockChart(let data):
            try data.encode(to: encoder)
        case .marketOverview(let data):
            try data.encode(to: encoder)
        }
    }
}

// MARK: - Sentiment Analysis
struct SentimentAnalysis: Identifiable {
    let id = UUID()
    let overallSentiment: SentimentType
    let percentage: Int
    let bulletPoints: [ChatBulletPoint]
    let dataUpdatedText: String

    enum SentimentType: String {
        case bullish = "Bullish"
        case bearish = "Bearish"
        case neutral = "Neutral"

        var color: Color {
            switch self {
            case .bullish: return AppColors.bullish
            case .bearish: return AppColors.bearish
            case .neutral: return AppColors.neutral
            }
        }
    }
}

// MARK: - Chat Bullet Point
struct ChatBulletPoint: Identifiable {
    let id = UUID()
    let text: String
    let indicatorType: IndicatorType

    enum IndicatorType {
        case success  // Green checkmark
        case warning  // Yellow/amber triangle
        case info     // Blue info circle

        var color: Color {
            switch self {
            case .success: return AppColors.bullish
            case .warning: return AppColors.neutral
            case .info: return AppColors.primaryBlue
            }
        }

        var iconName: String {
            switch self {
            case .success: return "checkmark.circle.fill"
            case .warning: return "exclamationmark.triangle.fill"
            case .info: return "info.circle.fill"
            }
        }
    }
}

// MARK: - Stock Performance
struct StockPerformance: Identifiable {
    let id = UUID()
    let currentPrice: Double
    let changePercent: Double
    let period: String
    let dayHigh: Double
    let dayLow: Double
    let volume: String
    let avgVolume: String
    let chartData: [Double]
    let followUpQuestion: String?

    var isPositive: Bool {
        changePercent >= 0
    }

    var formattedPrice: String {
        String(format: "$%.2f", currentPrice)
    }

    var formattedChange: String {
        let sign = changePercent >= 0 ? "+" : ""
        return "\(sign)\(String(format: "%.1f", changePercent))%"
    }

    var formattedDayHigh: String {
        String(format: "$%.2f", dayHigh)
    }

    var formattedDayLow: String {
        String(format: "$%.2f", dayLow)
    }
}

// MARK: - Risk Factor
struct RiskFactor: Identifiable {
    let id = UUID()
    let iconName: String
    let iconColor: Color
    let title: String
    let description: String
    let impactLevel: ImpactLevel

    enum ImpactLevel: String {
        case high = "High Impact"
        case medium = "Medium Impact"
        case variable = "Variable Impact"

        var color: Color {
            switch self {
            case .high: return AppColors.bearish
            case .medium: return AppColors.neutral
            case .variable: return AppColors.primaryBlue
            }
        }
    }
}

// MARK: - Risk Factors Data
struct RiskFactorsData: Identifiable {
    let id = UUID()
    let introText: String
    let factors: [RiskFactor]
}

// MARK: - Tip Data
struct TipData: Identifiable {
    let id = UUID()
    let title: String
    let content: String
}

// MARK: - Page Indicator
struct PageIndicatorData {
    let currentPage: Int
    let totalPages: Int
}

// MARK: - Tolerant ISO-8601 parsing

/// Parse a backend ISO-8601 timestamp, tolerating BOTH fractional-second and whole-second forms.
/// `ISO8601DateFormatter` with `.withFractionalSeconds` returns `nil` for a timestamp that has no
/// fractional part (e.g. a Postgres `now()` / Python `isoformat()` landing on an exact second), so
/// we fall back to the no-fractional formatter. Without this, such rows silently parse to `Date()`
/// (now) — corrupting message timestamps and bucketing history into the wrong day. Formatters are
/// cached (creating an `ISO8601DateFormatter` per parse is expensive).
enum BackendISO8601 {
    private static let withFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let withoutFractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func date(from string: String) -> Date? {
        withFractional.date(from: string) ?? withoutFractional.date(from: string)
    }
}

// MARK: - Backend Response DTOs (Codable)

/// Matches backend ``ChatSessionResponse``.
struct ChatSessionDTO: Codable, Identifiable, Sendable {
    let id: String
    let title: String?
    let sessionType: String?
    let stockId: String?
    let previewMessage: String?
    let messageCount: Int
    let isSaved: Bool
    let createdAt: String
    let lastMessageAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title
        case sessionType = "session_type"
        case stockId = "stock_id"
        case previewMessage = "preview_message"
        case messageCount = "message_count"
        case isSaved = "is_saved"
        case createdAt = "created_at"
        case lastMessageAt = "last_message_at"
    }

    /// Map session_type to ChatHistoryItemType for the history panel.
    var historyItemType: ChatHistoryItemType {
        switch sessionType?.uppercased() {
        case "STOCK": return .stock
        case "BOOK": return .book
        case "CONCEPT": return .concept
        case "JOURNEY": return .journey
        case "REPORT": return .report
        default: return .normal
        }
    }

    /// Parse createdAt into Date (tolerant of whole-second timestamps — see BackendISO8601).
    var date: Date {
        BackendISO8601.date(from: lastMessageAt ?? createdAt)
            ?? BackendISO8601.date(from: createdAt)
            ?? Date()
    }

    /// Convert to ChatHistoryItem for the history panel UI.
    func toChatHistoryItem() -> ChatHistoryItem {
        ChatHistoryItem(
            sessionId: id,
            type: historyItemType,
            title: title ?? "Chat",
            preview: previewMessage ?? "",
            timestamp: date,
            isSaved: isSaved
        )
    }
}

/// Matches backend ``ChatMessageResponse``.
struct ChatMessageDTO: Codable, Identifiable, Sendable {
    let id: String
    let sessionId: String
    let role: String
    let content: String
    let widget: ChatWidgetData?
    let citations: [ChatCitationDTO]?
    let tokensUsed: Int?
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id
        case sessionId = "session_id"
        case role, content, widget, citations
        case tokensUsed = "tokens_used"
        case createdAt = "created_at"
    }

    /// Convert to the UI-facing RichChatMessage.
    func toRichChatMessage() -> RichChatMessage {
        let msgRole: ChatMessageRole = role == "user" ? .user : .assistant
        var richContent: [RichContentType] = []

        // If there's a widget, add it first (polymorphic)
        if let widget = widget {
            switch widget {
            case .stockChart(let data):
                richContent.append(.stockChart(data))
            case .marketOverview(let data):
                richContent.append(.marketOverview(data))
            }
        }

        // Always add the text content
        if !content.isEmpty {
            richContent.append(.text(content))
        }

        let timestamp = BackendISO8601.date(from: createdAt) ?? Date()

        return RichChatMessage(role: msgRole, content: richContent, timestamp: timestamp)
    }
}

/// Codable citation from backend.
struct ChatCitationDTO: Codable, Sendable {
    let index: Int?
    let source: String?
    let text: String?
}

/// Matches backend ``ChatSessionListResponse``.
struct ChatSessionListDTO: Codable, Sendable {
    let sessions: [ChatSessionDTO]
    let total: Int
}

/// Matches backend ``ChatHistoryResponse``.
struct ChatHistoryDTO: Codable, Sendable {
    let session: ChatSessionDTO
    let messages: [ChatMessageDTO]
}

// MARK: - Sample Data
extension RichChatMessage {
    static let sampleConversation: [RichChatMessage] = [
        // User asks about Tesla
        RichChatMessage(
            role: .user,
            content: [.text("What's the current sentiment around Tesla stock?")],
            timestamp: Calendar.current.date(byAdding: .minute, value: -7, to: Date())!
        ),

        // AI responds with sentiment analysis
        RichChatMessage(
            role: .assistant,
            content: [
                .text("Based on the latest market data and social sentiment analysis, here's what I found about Tesla (TSLA):"),
                .sentimentAnalysis(SentimentAnalysis(
                    overallSentiment: .bullish,
                    percentage: 68,
                    bulletPoints: [
                        ChatBulletPoint(text: "Strong delivery numbers exceeded expectations in Q4", indicatorType: .success),
                        ChatBulletPoint(text: "Cybertruck production ramping up successfully", indicatorType: .success),
                        ChatBulletPoint(text: "Competition intensifying in EV market", indicatorType: .warning),
                        ChatBulletPoint(text: "Analyst price targets range from $180-$350", indicatorType: .info)
                    ],
                    dataUpdatedText: "Data updated 5 minutes ago"
                ))
            ],
            timestamp: Calendar.current.date(byAdding: .minute, value: -6, to: Date())!
        ),

        // User asks about performance
        RichChatMessage(
            role: .user,
            content: [.text("How's Tesla's stock performance?")],
            timestamp: Calendar.current.date(byAdding: .minute, value: -5, to: Date())!
        ),

        // AI responds with stock chart widget (Rich Media)
        RichChatMessage(
            role: .assistant,
            content: [
                .text("Here's Tesla's stock performance over the past month:"),
                .stockChart(StockChartWidgetData.sample)
            ],
            timestamp: Calendar.current.date(byAdding: .minute, value: -4, to: Date())!
        ),

        // User asks about risks
        RichChatMessage(
            role: .user,
            content: [.text("What are the key risks to consider?")],
            timestamp: Calendar.current.date(byAdding: .minute, value: -2, to: Date())!
        ),

        // AI responds with risk factors
        RichChatMessage(
            role: .assistant,
            content: [
                .text("Here are the major risk factors for Tesla investors to monitor:"),
                .riskFactors(RiskFactorsData(
                    introText: "",
                    factors: [
                        RiskFactor(
                            iconName: "exclamationmark.triangle.fill",
                            iconColor: AppColors.bearish,
                            title: "Market Competition",
                            description: "Traditional automakers and new EV startups intensifying competition globally",
                            impactLevel: .high
                        ),
                        RiskFactor(
                            iconName: "doc.text.fill",
                            iconColor: AppColors.neutral,
                            title: "Regulatory Changes",
                            description: "Potential changes in EV subsidies and environmental regulations",
                            impactLevel: .medium
                        ),
                        RiskFactor(
                            iconName: "shippingbox.fill",
                            iconColor: AppColors.neutral,
                            title: "Supply Chain Constraints",
                            description: "Battery materials and semiconductor availability concerns",
                            impactLevel: .medium
                        ),
                        RiskFactor(
                            iconName: "dollarsign.circle.fill",
                            iconColor: AppColors.primaryBlue,
                            title: "Valuation Concerns",
                            description: "High P/E ratio compared to traditional automakers",
                            impactLevel: .variable
                        )
                    ]
                )),
                .tip(TipData(
                    title: "RISK MITIGATION TIP",
                    content: "Consider diversifying your portfolio and maintaining a long-term investment horizon to weather short-term volatility."
                ))
            ],
            timestamp: Calendar.current.date(byAdding: .minute, value: 0, to: Date())!
        )
    ]
}

// MARK: - Sample Widget Data
extension StockChartWidgetData {
    static let sample = StockChartWidgetData(
        widgetType: "stock_chart",
        ticker: "TSLA",
        companyName: "Tesla, Inc.",
        currentPrice: 242.84,
        change: 19.42,
        changePercent: 8.7,
        dayHigh: 245.12,
        dayLow: 238.45,
        volume: 124_500_000,
        avgVolume: 98_200_000,
        marketCap: 789_000_000_000,
        peRatio: 62.3,
        yearHigh: 299.29,
        yearLow: 138.80,
        historicalData: [
            HistoricalDataPointDTO(date: "2026-01-30", open: 220, high: 223, low: 218, close: 220, volume: 80_000_000),
            HistoricalDataPointDTO(date: "2026-01-31", open: 221, high: 227, low: 220, close: 225, volume: 85_000_000),
            HistoricalDataPointDTO(date: "2026-02-03", open: 224, high: 225, low: 216, close: 218, volume: 90_000_000),
            HistoricalDataPointDTO(date: "2026-02-04", open: 219, high: 232, low: 218, close: 230, volume: 95_000_000),
            HistoricalDataPointDTO(date: "2026-02-05", open: 230, high: 236, low: 229, close: 235, volume: 88_000_000),
            HistoricalDataPointDTO(date: "2026-02-06", open: 234, high: 235, low: 226, close: 228, volume: 82_000_000),
            HistoricalDataPointDTO(date: "2026-02-07", open: 229, high: 241, low: 228, close: 240, volume: 110_000_000),
            HistoricalDataPointDTO(date: "2026-02-10", open: 240, high: 241, low: 236, close: 238, volume: 100_000_000),
            HistoricalDataPointDTO(date: "2026-02-11", open: 239, high: 246, low: 238, close: 245, volume: 115_000_000),
            HistoricalDataPointDTO(date: "2026-02-12", open: 244, high: 246, low: 240, close: 242, volume: 105_000_000),
            HistoricalDataPointDTO(date: "2026-02-13", open: 242, high: 244, low: 239, close: 240, volume: 98_000_000),
            HistoricalDataPointDTO(date: "2026-02-14", open: 241, high: 245, low: 240, close: 243, volume: 102_000_000),
            HistoricalDataPointDTO(date: "2026-02-18", open: 243, high: 247, low: 241, close: 245, volume: 108_000_000),
            HistoricalDataPointDTO(date: "2026-02-19", open: 244, high: 246, low: 240, close: 241, volume: 96_000_000),
            HistoricalDataPointDTO(date: "2026-02-20", open: 241, high: 244, low: 239, close: 242, volume: 99_000_000),
            HistoricalDataPointDTO(date: "2026-02-21", open: 242, high: 245, low: 240, close: 242.84, volume: 124_500_000),
        ]
    )
}
