//
//  ResearchItem.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import Foundation

enum ResearchRating: String {
    case buy = "BUY"
    case sell = "SELL"
    case hold = "HOLD"
}

struct ResearchItem: Identifiable {
    let id = UUID()
    let company: String
    let logoName: String
    let title: String
    let description: String
    let rating: ResearchRating
    let targetPrice: String
    let postedTime: String

    var ratingColor: String {
        switch rating {
        case .buy:
            return "RatingBuy"
        case .sell:
            return "RatingSell"
        case .hold:
            return "RatingHold"
        }
    }
}

// MARK: - Mock Data
extension ResearchItem {
    static let mockData: [ResearchItem] = [
        ResearchItem(
            company: "Microsoft",
            logoName: "icon_microsoft",
            title: "Microsoft: The AI Moat Deepens",
            description: "Azure's AI services and UX Pilot AI partnership position MSFT as a dominant force in enterprise AI. Q4 cloud growth of 28% YoY signals strong market demand.",
            rating: .buy,
            targetPrice: "$425",
            postedTime: "3 hours ago"
        ),
        ResearchItem(
            company: "Google",
            logoName: "icon_google",
            title: "Google: Gemini's Market Impact",
            description: "Gemini AI integration across products shows promise. Search market share stable while cloud business accelerates with 26% growth.",
            rating: .buy,
            targetPrice: "$155",
            postedTime: "4 weeks ago"
        ),
        ResearchItem(
            company: "AMD",
            logoName: "icon_amd",
            title: "AMD: AI Chip Wars Heat Up",
            description: "MI300 series gaining traction in data centers. While trailing NVIDIA, AMD's competitive pricing and supply availability create opportunities.",
            rating: .hold,
            targetPrice: "$23",
            postedTime: "5 days ago"
        )
    ]
}
