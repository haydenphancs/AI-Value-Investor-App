//
//  MarketInsight.swift
//  ios
//
//  Created by Hai Phan on 12/23/25.
//

import Foundation

enum SentimentType: String {
    case positive = "Positive"
    case negative = "Negative"
    case neutral = "Neutral"
}

struct MarketInsight: Identifiable {
    let id = UUID()
    let title: String
    let bulletPoints: [String]
    let sentiment: SentimentType
    let updatedTime: String

    var sentimentColor: String {
        switch sentiment {
        case .positive:
            return "InsightPositive"
        case .negative:
            return "InsightNegative"
        case .neutral:
            return "InsightNeutral"
        }
    }
}

// MARK: - Mock Data
extension MarketInsight {
    static let mockData = MarketInsight(
        title: "Tech Stocks Rally on Strong AI Earnings",
        bulletPoints: [
            "Major technology companies posted impressive Q4 results driven by AI infrastructure investments.",
            "Cloud computing revenue exceeded expectations, with Microsoft and Google leading the charge. Market sentiment remains bullish heading into 2024."
        ],
        sentiment: .positive,
        updatedTime: "Updated 2 hours ago"
    )
}
