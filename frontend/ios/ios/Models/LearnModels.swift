//
//  LearnModels.swift
//  ios
//
//  Data models for the Learn (Wiser) screen
//

import Foundation
import SwiftUI

// MARK: - Learn Tab
enum LearnTab: String, CaseIterable {
    case learn = "Learn"
    case chat = "Chat"
    case saved = "Saved"
}

// MARK: - Investor Level
enum InvestorLevel: String, CaseIterable {
    case beginner = "Beginner"
    case analyst = "Analyst"
    case modern = "Modern"
    case master = "Master"

    var iconName: String {
        switch self {
        case .beginner: return "graduationcap.fill"
        case .analyst: return "chart.bar.fill"
        case .modern: return "bolt.fill"
        case .master: return "crown.fill"
        }
    }

    var color: Color {
        switch self {
        case .beginner: return Color(hex: "22C55E")
        case .analyst: return Color(hex: "3B82F6")
        case .modern: return Color(hex: "A855F7")
        case .master: return Color(hex: "F59E0B")
        }
    }

    var index: Int {
        switch self {
        case .beginner: return 0
        case .analyst: return 1
        case .modern: return 2
        case .master: return 3
        }
    }
}

// MARK: - Journey Track
struct JourneyTrack: Identifiable {
    let id = UUID()
    let level: InvestorLevel
    let completedCount: Int
    let totalCount: Int
    let items: [JourneyItem]

    var progress: Double {
        guard totalCount > 0 else { return 0 }
        return Double(completedCount) / Double(totalCount)
    }

    var progressPercentage: Int {
        Int(progress * 100)
    }

    var formattedProgress: String {
        "\(completedCount) of \(totalCount) completed"
    }
}

// MARK: - Journey Item
struct JourneyItem: Identifiable {
    let id = UUID()
    let title: String
    let isCompleted: Bool
    let isActive: Bool
    let stepNumber: Int
}

// MARK: - Next Lesson
struct NextLesson: Identifiable {
    let id = UUID()
    let journeyNumber: Int
    let journeyTitle: String
    let lessonTitle: String
    let lessonDescription: String
    let estimatedMinutes: Int
    let chapterCount: Int
}

// MARK: - Key Concept
struct KeyConcept: Identifiable {
    let id = UUID()
    let title: String
    let subtitle: String
    let iconName: String
    let iconBackgroundColor: Color
    let estimatedMinutes: Int
    let learnerCount: String
    let isBookmarked: Bool
}

// MARK: - Education Book
struct EducationBook: Identifiable {
    let id = UUID()
    let title: String
    let author: String
    let description: String
    let coverImageName: String
    let pageCount: Int
    let publishedYear: Int
    let rating: Double
    let isMostRead: Bool

    var formattedRating: String {
        String(format: "%.1f", rating)
    }

    var formattedPages: String {
        "\(pageCount) pages"
    }

    var formattedPublished: String {
        "Published \(publishedYear)"
    }
}

// MARK: - Community Discussion
struct CommunityDiscussion: Identifiable {
    let id = UUID()
    let authorName: String
    let authorAvatarName: String
    let content: String
    let postedAt: Date
    let replyCount: Int
    let likeCount: Int

    var timeAgo: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: postedAt, relativeTo: Date())
    }

    var formattedReplies: String {
        "\(replyCount) replies"
    }

    var formattedLikes: String {
        "\(likeCount)"
    }
}

// MARK: - Credit Balance
struct CreditBalance: Identifiable {
    let id = UUID()
    let credits: Int
    let renewsAt: Date

    var formattedRenewalDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d, yyyy"
        return "Renews \(formatter.string(from: renewsAt))"
    }

    static let mock = CreditBalance(
        credits: 47,
        renewsAt: Calendar.current.date(byAdding: .day, value: 30, to: Date()) ?? Date()
    )
}

// MARK: - Sample Data Extensions
extension JourneyTrack {
    static let sampleBeginner = JourneyTrack(
        level: .beginner,
        completedCount: 3,
        totalCount: 12,
        items: [
            JourneyItem(title: "It's all about mindset", isCompleted: true, isActive: false, stepNumber: 1),
            JourneyItem(title: "What is a Stock?", isCompleted: true, isActive: false, stepNumber: 2),
            JourneyItem(title: "Value Investing 101", isCompleted: true, isActive: false, stepNumber: 3),
            JourneyItem(title: "Understanding the Market", isCompleted: false, isActive: true, stepNumber: 4)
        ]
    )
}

extension NextLesson {
    static let sampleData = NextLesson(
        journeyNumber: 2,
        journeyTitle: "Analyst",
        lessonTitle: "How the System Works",
        lessonDescription: "Understanding market mechanics, analysis financial report and more.",
        estimatedMinutes: 4,
        chapterCount: 5
    )
}

extension KeyConcept {
    static let sampleData: [KeyConcept] = [
        KeyConcept(
            title: "Understanding Moats",
            subtitle: "Why competitive advantage matters most.",
            iconName: "shield.fill",
            iconBackgroundColor: Color(hex: "F59E0B"),
            estimatedMinutes: 8,
            learnerCount: "1.4k",
            isBookmarked: false
        ),
        KeyConcept(
            title: "What is P/E Ratio?",
            subtitle: "How to tell if a stock is cheap or expensive.",
            iconName: "chart.pie.fill",
            iconBackgroundColor: Color(hex: "3B82F6"),
            estimatedMinutes: 12,
            learnerCount: "1.4k",
            isBookmarked: false
        ),
        KeyConcept(
            title: "The Power of Compounding",
            subtitle: "How time doubles your money exponentially.",
            iconName: "arrow.triangle.2.circlepath",
            iconBackgroundColor: Color(hex: "22C55E"),
            estimatedMinutes: 8,
            learnerCount: "1.1k",
            isBookmarked: false
        )
    ]
}

extension EducationBook {
    static let sampleData: [EducationBook] = [
        EducationBook(
            title: "The Intelligent Investor",
            author: "Benjamin Graham",
            description: "The Bible of Value Investing. Warren Buffett's #1 recommended book.",
            coverImageName: "book_intelligent_investor",
            pageCount: 623,
            publishedYear: 1949,
            rating: 4.8,
            isMostRead: true
        ),
        EducationBook(
            title: "One Up On Wall Street",
            author: "Peter Lynch",
            description: "How to use what you already know to make money in the market.",
            coverImageName: "book_one_up_wall_street",
            pageCount: 304,
            publishedYear: 1989,
            rating: 4.5,
            isMostRead: false
        ),
        EducationBook(
            title: "Common Stocks and Uncommon Profits",
            author: "Philip Fisher",
            description: "The growth investing masterpiece that influenced Warren Buffett.",
            coverImageName: "book_common_stocks",
            pageCount: 271,
            publishedYear: 1958,
            rating: 4.7,
            isMostRead: false
        )
    ]
}

extension CommunityDiscussion {
    static let sampleData: [CommunityDiscussion] = [
        CommunityDiscussion(
            authorName: "Sarah Chen",
            authorAvatarName: "avatar_sarah",
            content: "Just finished reading about economic moats. Can someone explain how to identify them in tech companies?",
            postedAt: Calendar.current.date(byAdding: .hour, value: -2, to: Date())!,
            replyCount: 24,
            likeCount: 156
        ),
        CommunityDiscussion(
            authorName: "Marcus Johnson",
            authorAvatarName: "avatar_marcus",
            content: "My portfolio is down 15% this month. Should I hold or sell? Looking for advice from experienced investors.",
            postedAt: Calendar.current.date(byAdding: .hour, value: -5, to: Date())!,
            replyCount: 89,
            likeCount: 203
        )
    ]
}
