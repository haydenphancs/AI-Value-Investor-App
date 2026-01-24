//
//  InvestorJourneyModels.swift
//  ios
//
//  Data models for The Investor Journey screen
//

import Foundation
import SwiftUI

// MARK: - Lesson Status
enum LessonStatus: String, CaseIterable {
    case completed = "Completed"
    case upNext = "Up Next"
    case notStarted = "Not Started"

    var color: Color {
        switch self {
        case .completed: return AppColors.bullish
        case .upNext: return AppColors.primaryBlue
        case .notStarted: return AppColors.textMuted
        }
    }

    var backgroundColor: Color {
        switch self {
        case .completed: return AppColors.bullish.opacity(0.15)
        case .upNext: return AppColors.primaryBlue.opacity(0.15)
        case .notStarted: return Color.clear
        }
    }
}

// MARK: - Lesson Category
enum LessonCategory {
    case standard
    case crypto

    var badgeText: String? {
        switch self {
        case .crypto: return "Crypto"
        case .standard: return nil
        }
    }

    var badgeColor: Color {
        return Color(hex: "F59E0B")
    }
}

// MARK: - Lesson
struct Lesson: Identifiable {
    let id = UUID()
    let title: String
    let description: String
    let durationMinutes: Int
    let status: LessonStatus
    let category: LessonCategory

    var formattedDuration: String {
        "\(durationMinutes) min"
    }

    init(
        title: String,
        description: String,
        durationMinutes: Int,
        status: LessonStatus = .notStarted,
        category: LessonCategory = .standard
    ) {
        self.title = title
        self.description = description
        self.durationMinutes = durationMinutes
        self.status = status
        self.category = category
    }
}

// MARK: - Journey Level
enum JourneyLevel: Int, CaseIterable {
    case foundation = 1
    case analysis = 2
    case strategies = 3
    case mastery = 4

    var title: String {
        switch self {
        case .foundation: return "Foundation"
        case .analysis: return "Analysis"
        case .strategies: return "Strategies"
        case .mastery: return "Mastery"
        }
    }

    var iconName: String {
        switch self {
        case .foundation: return "🌱"
        case .analysis: return "📊"
        case .strategies: return "⚡"
        case .mastery: return "👑"
        }
    }

    var color: Color {
        switch self {
        case .foundation: return AppColors.bullish
        case .analysis: return AppColors.primaryBlue
        case .strategies: return AppColors.alertPurple
        case .mastery: return AppColors.neutral
        }
    }
}

// MARK: - Level Progress
struct LevelProgress: Identifiable {
    let id = UUID()
    let level: JourneyLevel
    let lessons: [Lesson]

    var completedCount: Int {
        lessons.filter { $0.status == .completed }.count
    }

    var totalCount: Int {
        lessons.count
    }

    var progress: Double {
        guard totalCount > 0 else { return 0 }
        return Double(completedCount) / Double(totalCount)
    }

    var formattedProgress: String {
        "\(completedCount)/\(totalCount)"
    }

    var isUnlocked: Bool {
        // Level is unlocked if it's Foundation, or previous level has some progress
        level == .foundation || completedCount > 0
    }
}

// MARK: - Investor Journey Data
struct InvestorJourneyData {
    let levels: [LevelProgress]
    let totalLessonsCompleted: Int
    let totalLessons: Int

    var overallProgress: Double {
        guard totalLessons > 0 else { return 0 }
        return Double(totalLessonsCompleted) / Double(totalLessons)
    }

    var formattedOverallProgress: String {
        "\(totalLessonsCompleted)/\(totalLessons) Lessons Completed"
    }
}

// MARK: - Study Schedule
struct StudySchedule {
    var dailyReminderEnabled: Bool
    var morningSessionTime: Date
    var reviewTime: Date

    var formattedMorningTime: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        return formatter.string(from: morningSessionTime)
    }

    var formattedReviewTime: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "h:mm a"
        return formatter.string(from: reviewTime)
    }
}

// MARK: - Investor Quote
struct InvestorQuote {
    let text: String
    let author: String
}

// MARK: - Sample Data
extension InvestorJourneyData {
    static let sampleData: InvestorJourneyData = {
        let foundationLessons = [
            Lesson(
                title: "Compound Interest",
                description: "Discover why Einstein called it the eighth wonder of the world.",
                durationMinutes: 3,
                status: .completed
            ),
            Lesson(
                title: "Stock vs. Business",
                description: "Learn to think like an owner, not a trader. The fundamental shift.",
                durationMinutes: 4,
                status: .upNext
            ),
            Lesson(
                title: "Mr. Market",
                description: "How the market works. Meet the bipolar partner who offers to buy or sell every day.",
                durationMinutes: 5,
                status: .notStarted
            ),
            Lesson(
                title: "Risk and Reward are Linked",
                description: "Two sides of the same coin. Understanding how they trade off is the first step to smarter investing.",
                durationMinutes: 4,
                status: .notStarted
            ),
            Lesson(
                title: "Bitcoin: Digital Gold?",
                description: "Understanding the \"Store of Value\" thesis. Why scarcity (21 million coins) matters in a world of printing money.",
                durationMinutes: 4,
                status: .notStarted,
                category: .crypto
            ),
            Lesson(
                title: "ETFs 101",
                description: "What is an ETF? How to invest in ETFs.",
                durationMinutes: 3,
                status: .notStarted
            ),
            Lesson(
                title: "The Inflation Thief",
                description: "How your money loses value while sitting still, and what to do.",
                durationMinutes: 3,
                status: .notStarted
            )
        ]

        let analysisLessons = [
            Lesson(
                title: "Key Statistics",
                description: "The numbers that matter most when evaluating any company.",
                durationMinutes: 5,
                status: .notStarted
            ),
            Lesson(
                title: "The Income Statement",
                description: "Reading the story of profitability and growth over time.",
                durationMinutes: 8,
                status: .notStarted
            ),
            Lesson(
                title: "The Balance Sheet",
                description: "Understanding assets, liabilities, and what the company owns.",
                durationMinutes: 6,
                status: .notStarted
            ),
            Lesson(
                title: "Cash Flow is King",
                description: "Why cash is king and profits can lie. Follow the money.",
                durationMinutes: 5,
                status: .notStarted
            ),
            Lesson(
                title: "Economic Moats",
                description: "Identifying the competitive advantages that protect great businesses.",
                durationMinutes: 4,
                status: .notStarted
            ),
            Lesson(
                title: "Tokenomics 101",
                description: "The \"Central Bank\" of a crypto project. Who holds the tokens? Are they printing more tomorrow (inflation)?",
                durationMinutes: 4,
                status: .notStarted,
                category: .crypto
            ),
            Lesson(
                title: "Red Flags",
                description: "Warning signs: Insider selling, frequent accounting changes, or missed earnings.",
                durationMinutes: 4,
                status: .notStarted
            )
        ]

        let strategiesLessons = [
            Lesson(
                title: "The Buffett Way",
                description: "Value investing principles from the Oracle of Omaha himself.",
                durationMinutes: 7,
                status: .notStarted
            ),
            Lesson(
                title: "The Lynch Way",
                description: "Invest in what you know. Peter Lynch's common sense approach.",
                durationMinutes: 6,
                status: .notStarted
            ),
            Lesson(
                title: "The Cathie Wood Way",
                description: "Disruptive innovation investing and exponential growth thinking.",
                durationMinutes: 5,
                status: .notStarted
            ),
            Lesson(
                title: "Whale Watching",
                description: "Following institutional investors and learning from their moves.",
                durationMinutes: 4,
                status: .notStarted
            ),
            Lesson(
                title: "Portfolio Gardening",
                description: "Cultivating your investments: when to water, prune, or uproot. The myth of diversification.",
                durationMinutes: 5,
                status: .notStarted
            ),
            Lesson(
                title: "The Power of Discipline",
                description: "Keeps your strategy on track and turns plans into profits.",
                durationMinutes: 5,
                status: .notStarted
            ),
            Lesson(
                title: "Common Investing Mistakes",
                description: "Even smart investors make mistakes. Learning the common ones helps you avoid costly decisions before they happen.",
                durationMinutes: 5,
                status: .notStarted
            )
        ]

        let masteryLessons = [
            Lesson(
                title: "The FOMO Cycle",
                description: "Recognizing and breaking free from emotion-driven investing patterns.",
                durationMinutes: 6,
                status: .notStarted
            ),
            Lesson(
                title: "Second-Order Thinking",
                description: "Everyone asks \"What happens next?\" Masters ask \"And then what?\"",
                durationMinutes: 4,
                status: .notStarted
            ),
            Lesson(
                title: "Risk vs. Uncertainty",
                description: "Understanding the crucial difference and managing both effectively.",
                durationMinutes: 5,
                status: .notStarted
            ),
            Lesson(
                title: "The Art of Selling",
                description: "When and how to exit positions without emotion clouding judgment.",
                durationMinutes: 4,
                status: .notStarted
            ),
            Lesson(
                title: "Inversion Thinking",
                description: "Charlie Munger's secret weapon: solving problems backwards.",
                durationMinutes: 5,
                status: .notStarted
            ),
            Lesson(
                title: "AI and Beyond",
                description: "AI is changing how trades are made and investments are chosen, redefining the future of the markets.",
                durationMinutes: 5,
                status: .notStarted
            )
        ]

        let levels = [
            LevelProgress(level: .foundation, lessons: foundationLessons),
            LevelProgress(level: .analysis, lessons: analysisLessons),
            LevelProgress(level: .strategies, lessons: strategiesLessons),
            LevelProgress(level: .mastery, lessons: masteryLessons)
        ]

        let totalCompleted = levels.reduce(0) { $0 + $1.completedCount }
        let totalLessons = levels.reduce(0) { $0 + $1.totalCount }

        return InvestorJourneyData(
            levels: levels,
            totalLessonsCompleted: totalCompleted,
            totalLessons: totalLessons
        )
    }()
}

extension StudySchedule {
    static let defaultSchedule: StudySchedule = {
        let calendar = Calendar.current
        var morningComponents = DateComponents()
        morningComponents.hour = 9
        morningComponents.minute = 0
        let morningTime = calendar.date(from: morningComponents) ?? Date()

        var eveningComponents = DateComponents()
        eveningComponents.hour = 20
        eveningComponents.minute = 0
        let eveningTime = calendar.date(from: eveningComponents) ?? Date()

        return StudySchedule(
            dailyReminderEnabled: true,
            morningSessionTime: morningTime,
            reviewTime: eveningTime
        )
    }()
}

extension InvestorQuote {
    static let buffettQuote = InvestorQuote(
        text: "The stock market is a device for transferring money from the impatient to the patient.",
        author: "Warren Buffett"
    )
}
