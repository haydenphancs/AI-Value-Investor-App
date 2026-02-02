//
//  BookCoreDetailModels.swift
//  ios
//
//  Data models for the Book Core Detail View content
//  Represents detailed content for each core chapter
//

import Foundation
import SwiftUI

// MARK: - Core Chapter Content
/// Full content model for a core chapter detail view
struct CoreChapterContent: Identifiable {
    let id = UUID()
    let chapterNumber: Int
    let chapterTitle: String
    let bookTitle: String
    let bookAuthor: String
    let sections: [CoreChapterSection]
    let audioDurationSeconds: Int
    let currentProgress: Double // 0.0 - 1.0

    var formattedDuration: String {
        let minutes = audioDurationSeconds / 60
        return "\(minutes) min"
    }

    var formattedChapterLabel: String {
        "Core \(chapterNumber)"
    }
}

// MARK: - Core Chapter Section
/// Represents a section within a core chapter
struct CoreChapterSection: Identifiable {
    let id = UUID()
    let type: CoreSectionType
    let title: String?
    let content: CoreSectionContent
}

// MARK: - Core Section Type
enum CoreSectionType: String {
    case heading          // Main section heading
    case paragraph        // Regular text paragraph
    case quote            // Highlighted quote
    case assetList        // List of asset categories
    case actionPlan       // Action steps section
    case bulletPoints     // Bulleted list
    case callout          // Highlighted callout box
}

// MARK: - Core Section Content
/// Union type for different section content
enum CoreSectionContent {
    case text(String)
    case richText(AttributedString)
    case quote(QuoteContent)
    case assetList([AssetCategory])
    case actionPlan([ActionStep])
    case bulletPoints([BulletPoint])
    case callout(CalloutContent)
}

// MARK: - Quote Content
struct QuoteContent: Identifiable {
    let id = UUID()
    let text: String
    let author: String
    let source: String?
}

// MARK: - Asset Category
struct AssetCategory: Identifiable {
    let id = UUID()
    let icon: String
    let iconColor: String
    let title: String
    let description: String
}

// MARK: - Action Step
struct ActionStep: Identifiable {
    let id = UUID()
    let title: String
    let description: String
    let isCompleted: Bool
}

// MARK: - Bullet Point
struct BulletPoint: Identifiable {
    let id = UUID()
    let text: String
    let isHighlighted: Bool
}

// MARK: - Callout Content
struct CalloutContent: Identifiable {
    let id = UUID()
    let title: String
    let text: String
    let style: CalloutStyle
}

enum CalloutStyle {
    case info
    case warning
    case success
    case insight

    var backgroundColor: Color {
        switch self {
        case .info: return Color(hex: "3B82F6").opacity(0.15)
        case .warning: return Color(hex: "F59E0B").opacity(0.15)
        case .success: return Color(hex: "22C55E").opacity(0.15)
        case .insight: return Color(hex: "A855F7").opacity(0.15)
        }
    }

    var iconColor: Color {
        switch self {
        case .info: return Color(hex: "3B82F6")
        case .warning: return Color(hex: "F59E0B")
        case .success: return Color(hex: "22C55E")
        case .insight: return Color(hex: "A855F7")
        }
    }

    var iconName: String {
        switch self {
        case .info: return "info.circle.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .success: return "checkmark.circle.fill"
        case .insight: return "lightbulb.fill"
        }
    }
}

// MARK: - Audio Transition State
/// Represents the state for chapter audio transitions
struct ChapterAudioTransition {
    let fromChapter: Int
    let toChapter: Int
    let pauseDurationSeconds: Int
    let transitionMessage: String

    static func createTransition(from: Int, to: Int) -> ChapterAudioTransition {
        ChapterAudioTransition(
            fromChapter: from,
            toChapter: to,
            pauseDurationSeconds: 10,
            transitionMessage: "Think about those action steps for a moment... Now, let's move to Core \(to)."
        )
    }
}

// MARK: - Sample Data Extensions
extension CoreChapterContent {
    /// Sample data for "Mastering the Financial Scorecard" - Chapter 2 of Rich Dad Poor Dad
    static let sampleFinancialScorecard = CoreChapterContent(
        chapterNumber: 2,
        chapterTitle: "Mastering the Financial Scorecard",
        bookTitle: "Rich Dad Poor Dad",
        bookAuthor: "Robert T. Kiyosaki",
        sections: [
            // Section 1: The Illusion of Wealth
            CoreChapterSection(
                type: .heading,
                title: nil,
                content: .text("The Illusion of Wealth")
            ),
            CoreChapterSection(
                type: .paragraph,
                title: nil,
                content: .text("Here is the secret that keeps the middle class exhausted: You are looking at the wrong scoreboard. Most professionals believe that a high salary equals wealth. It doesn't. You can earn $250,000 a year and still be technically insolvent if your monthly burn rate matches your income. The friction isn't your paycheck; it's your financial literacy. You have been trained to read words, but you haven't been trained to read numbers. Consequently, you spend your life building someone else's business (your boss's), buying someone else's investments (the bank's), and paying someone else's bills (the government's).")
            ),

            // Quote Section
            CoreChapterSection(
                type: .quote,
                title: nil,
                content: .quote(QuoteContent(
                    text: "\"The rich don't work for money. They make money work for them. Assets generate income whether you're working or not—that's the key to financial freedom.\"",
                    author: "Robert Kiyosaki",
                    source: "Rich Dad Poor Dad"
                ))
            ),

            // Section 2: The Tale of Two Columns
            CoreChapterSection(
                type: .heading,
                title: nil,
                content: .text("The Tale of Two Columns")
            ),
            CoreChapterSection(
                type: .paragraph,
                title: nil,
                content: .text("The author illustrates this with a simple, brutal truth that shattered the worldview of a young couple. They celebrate a pay raise by buying their \"dream home.\" They proudly list this house under the \"Asset\" column of their financial statement. But the author's mentor, the \"Rich Dad,\" draws a simple diagram to prove them wrong. He defines an asset not by accounting tradition, but by the direction of cash flow. An asset puts money in your pocket. A liability takes money out. Because that house requires a mortgage, property taxes, insurance, and maintenance, cash is flowing out. Therefore, the house is a liability.")
            ),
            CoreChapterSection(
                type: .paragraph,
                title: nil,
                content: .text("The \"Rich Dad\" explains that the poor work for money to pay expenses; the middle class buys liabilities they think are assets (like houses and cars); but the rich focus entirely on the Asset Column—acquiring things that generate cash while they sleep.")
            ),

            // Asset Categories Section
            CoreChapterSection(
                type: .assetList,
                title: "True Assets",
                content: .assetList([
                    AssetCategory(
                        icon: "building.2.fill",
                        iconColor: "F59E0B",
                        title: "Real Estate",
                        description: "Tangible, proven track record, generates rental income, tax benefits, but requires significant capital and management."
                    ),
                    AssetCategory(
                        icon: "bitcoinsign.circle.fill",
                        iconColor: "F7931A",
                        title: "Cryptocurrency",
                        description: "Highly liquid, 24/7 markets, potential for high returns, but volatile and requires technical knowledge and risk management."
                    ),
                    AssetCategory(
                        icon: "laptopcomputer",
                        iconColor: "8B5CF6",
                        title: "Digital Assets",
                        description: "Online businesses, SaaS products, digital courses, and content libraries generate passive income with minimal overhead. Scalable and location-independent."
                    )
                ])
            ),

            // Section 3: The New Mask
            CoreChapterSection(
                type: .heading,
                title: nil,
                content: .text("The New Mask")
            ),
            CoreChapterSection(
                type: .paragraph,
                title: nil,
                content: .text("Now, the \"Liability Trap\" is even more sophisticated. It's no longer just a house; it's digital. It is the crypto \"investment\" that has zero utility and generates no yield—that is speculation, not an asset. It is the five different AI software subscriptions you pay for monthly but don't use to generate income.")
            ),
            CoreChapterSection(
                type: .paragraph,
                title: nil,
                content: .text("In the modern era, true assets have evolved. An asset might be a high-yield DeFi staking protocol, a dividend-paying ETF, or a piece of code you wrote once that sells itself repeatedly. Conversely, \"Buy Now, Pay Later\" schemes for consumer goods are the modern shackles. The principle remains: if you have to work to keep it, it's a job. If you have to pay to keep it, it's a liability. If it pays you to keep it, it's an asset.")
            ),

            // Section 4: The Action Plan
            CoreChapterSection(
                type: .heading,
                title: nil,
                content: .text("The Action Plan")
            ),
            CoreChapterSection(
                type: .actionPlan,
                title: nil,
                content: .actionPlan([
                    ActionStep(
                        title: "The Ruthless Audit",
                        description: "Tonight, draw a T-chart. List everything you own. If it requires money to maintain and yields zero cash flow (your car, your house, your unused tech), move it to the Liability column immediately. Stop lying to yourself.",
                        isCompleted: false
                    ),
                    ActionStep(
                        title: "The Replacement Ratio",
                        description: "For every new liability you want (e.g., a new phone), you must first buy an asset that covers the monthly cost of that liability. Do not buy the toy until the asset buys it for you.",
                        isCompleted: false
                    )
                ])
            )
        ],
        audioDurationSeconds: 1080,
        currentProgress: 0.0
    )

    /// Sample data for "De-Programming the Employee Mindset" - Chapter 1
    static let sampleEmployeeMindset = CoreChapterContent(
        chapterNumber: 1,
        chapterTitle: "De-Programming the \"Employee\" Mindset",
        bookTitle: "Rich Dad Poor Dad",
        bookAuthor: "Robert T. Kiyosaki",
        sections: [
            CoreChapterSection(
                type: .heading,
                title: nil,
                content: .text("The Rat Race")
            ),
            CoreChapterSection(
                type: .paragraph,
                title: nil,
                content: .text("Most people are trapped in a cycle they don't even see. Wake up, go to work, pay bills, repeat. The fear of not having enough money drives them to work harder, but the greed for more possessions keeps them spending everything they earn. This is the Rat Race—running faster and faster on a wheel that goes nowhere.")
            ),
            CoreChapterSection(
                type: .quote,
                title: nil,
                content: .quote(QuoteContent(
                    text: "\"The fear of being different prevents most people from seeking new ways to solve their problems.\"",
                    author: "Robert Kiyosaki",
                    source: "Rich Dad Poor Dad"
                ))
            ),
            CoreChapterSection(
                type: .heading,
                title: nil,
                content: .text("Two Fathers, Two Philosophies")
            ),
            CoreChapterSection(
                type: .paragraph,
                title: nil,
                content: .text("Robert's biological father—his \"Poor Dad\"—was highly educated, had a PhD, and held prestigious positions in education. He believed in working hard, getting good grades, and finding a secure job with benefits. His \"Rich Dad\"—his best friend's father—never finished eighth grade but became one of the wealthiest men in Hawaii.")
            ),
            CoreChapterSection(
                type: .bulletPoints,
                title: "The Two Mindsets",
                content: .bulletPoints([
                    BulletPoint(text: "Poor Dad: \"I can't afford it\" - A statement that shuts down thinking", isHighlighted: false),
                    BulletPoint(text: "Rich Dad: \"How can I afford it?\" - A question that opens possibilities", isHighlighted: true),
                    BulletPoint(text: "Poor Dad: \"Study hard so you can find a good company to work for\"", isHighlighted: false),
                    BulletPoint(text: "Rich Dad: \"Study hard so you can find a good company to buy\"", isHighlighted: true)
                ])
            ),
            CoreChapterSection(
                type: .actionPlan,
                title: nil,
                content: .actionPlan([
                    ActionStep(
                        title: "Question Your Beliefs",
                        description: "Write down three financial beliefs you inherited from your parents. For each one, ask: \"Is this serving me, or limiting me?\"",
                        isCompleted: false
                    ),
                    ActionStep(
                        title: "Change Your Language",
                        description: "For the next week, whenever you think \"I can't afford it,\" replace it with \"How can I afford it?\" Notice how this shifts your thinking from defeat to creativity.",
                        isCompleted: false
                    )
                ])
            )
        ],
        audioDurationSeconds: 900,
        currentProgress: 1.0
    )
}

extension AssetCategory {
    static let sampleCategories: [AssetCategory] = [
        AssetCategory(
            icon: "building.2.fill",
            iconColor: "F59E0B",
            title: "Real Estate",
            description: "Tangible, proven track record, generates rental income, tax benefits, but requires significant capital and management."
        ),
        AssetCategory(
            icon: "bitcoinsign.circle.fill",
            iconColor: "F7931A",
            title: "Cryptocurrency",
            description: "Highly liquid, 24/7 markets, potential for high returns, but volatile and requires technical knowledge and risk management."
        ),
        AssetCategory(
            icon: "laptopcomputer",
            iconColor: "8B5CF6",
            title: "Digital Assets",
            description: "Online businesses, SaaS products, digital courses, and content libraries generate passive income with minimal overhead. Scalable and location-independent."
        )
    ]
}
