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

        // AI responds with stock performance
        RichChatMessage(
            role: .assistant,
            content: [
                .text("Here's Tesla's stock performance over the past month:"),
                .stockPerformance(StockPerformance(
                    currentPrice: 242.84,
                    changePercent: 8.7,
                    period: "1 Month",
                    dayHigh: 245.12,
                    dayLow: 238.45,
                    volume: "124.5M",
                    avgVolume: "98.2M",
                    chartData: [220, 225, 218, 230, 235, 228, 240, 238, 245, 242],
                    followUpQuestion: "Would you like me to analyze any specific timeframe or technical indicators?"
                ))
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
