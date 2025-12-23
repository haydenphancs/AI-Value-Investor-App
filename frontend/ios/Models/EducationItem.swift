//
//  EducationItem.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import Foundation

enum EducationType: String {
    case strategy = "Strategy"
    case book = "Book"
    case article = "Article"
}

struct EducationItem: Identifiable {
    let id = UUID()
    let type: EducationType
    let iconName: String
    let title: String
    let description: String
    let readTime: String
    let author: String?
    let rating: Double?

    var typeColor: String {
        switch type {
        case .strategy:
            return "EducationStrategy"
        case .book:
            return "EducationBook"
        case .article:
            return "EducationArticle"
        }
    }
}

// MARK: - Mock Data
extension EducationItem {
    static let mockData: [EducationItem] = [
        EducationItem(
            type: .strategy,
            iconName: "icon_strategy",
            title: "Diversification reduces risk without killing returns.",
            description: "Spread investments across different asset classes and sectors to minimize portfolio volatility while maintaining growth potential.",
            readTime: "15 minutes read",
            author: nil,
            rating: nil
        ),
        EducationItem(
            type: .book,
            iconName: "icon_book",
            title: "The Intelligent Investor",
            description: "Spread investments across different asset classes and sectors to minimize portfolio volatility while maintaining growth potential.",
            readTime: "",
            author: "by Benjamin Graham",
            rating: 4.9
        ),
        EducationItem(
            type: .article,
            iconName: "icon_article",
            title: "Understanding Moats",
            description: "Learn how competitive advantages protect companies from rivals and create long-term value.",
            readTime: "5 minutes read",
            author: nil,
            rating: nil
        )
    ]
}
