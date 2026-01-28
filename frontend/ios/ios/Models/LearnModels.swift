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
    case foundation = "Foundation"
    case analyst = "Analyst"
    case strategist = "Strategist"
    case master = "Master"

    var iconName: String {
        switch self {
        case .foundation: return "graduationcap.fill"
        case .analyst: return "chart.bar.fill"
        case .strategist: return "bolt.fill"
        case .master: return "crown.fill"
        }
    }

    var color: Color {
        switch self {
        case .foundation: return Color(hex: "22C55E")
        case .analyst: return Color(hex: "3B82F6")
        case .strategist: return Color(hex: "A855F7")
        case .master: return Color(hex: "F59E0B")
        }
    }

    var index: Int {
        switch self {
        case .foundation: return 0
        case .analyst: return 1
        case .strategist: return 2
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

// MARK: - Money Move Category
enum MoneyMoveCategory: String, CaseIterable {
    case blueprints = "The Blueprints"
    case valueTraps = "Value Traps"
    case battles = "Battles"
    
    var tagline: String {
        switch self {
        case .blueprints: return "How the winners won."
        case .valueTraps: return "Failures, frauds, and lessons learned."
        case .battles: return "Comparative analysis of giants."
        }
    }
    
    var iconName: String {
        switch self {
        case .blueprints: return "crown.fill"
        case .valueTraps: return "flame.fill"
        case .battles: return "bolt.horizontal.fill"
        }
    }
    
    var iconBackgroundColor: Color {
        switch self {
        case .blueprints: return Color(hex: "22C55E") // Green - success
        case .valueTraps: return Color(hex: "EF4444") // Red - warning
        case .battles: return Color(hex: "8B5CF6") // Purple - strategic
        }
    }
}

// MARK: - Money Move
struct MoneyMove: Identifiable {
    let id = UUID()
    let title: String
    let subtitle: String
    let category: MoneyMoveCategory
    let estimatedMinutes: Int
    let learnerCount: String
    let isBookmarked: Bool
    
    var iconName: String {
        category.iconName
    }
    
    var iconBackgroundColor: Color {
        category.iconBackgroundColor
    }
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



// MARK: - Sample Data Extensions
extension JourneyTrack {
    static let sampleBeginner = JourneyTrack(
        level: .foundation,
        completedCount: 1,
        totalCount: 7,
        items: [
            JourneyItem(title: "Compound Interest", isCompleted: true, isActive: false, stepNumber: 1),
            JourneyItem(title: "Stock vs. Business", isCompleted: false, isActive: true, stepNumber: 2),
            JourneyItem(title: "Mr. Market", isCompleted: false, isActive: false, stepNumber: 3),
            JourneyItem(title: "Risk and Reward are Linked", isCompleted: false, isActive: false, stepNumber: 4)
        ]
    )
}

extension NextLesson {
    static let sampleData = NextLesson(
        journeyNumber: 2,
        journeyTitle: "Analyst",
        lessonTitle: "How the System Works",
        lessonDescription: "Understanding market mechanics, analysis financial report and more.",
        estimatedMinutes: 18,
        chapterCount: 7
    )
}

extension MoneyMove {
    static let sampleData: [MoneyMove] = [
        // The Blueprints - Success stories
        MoneyMove(
            title: "How Amazon Built Its Moat",
            subtitle: "The strategy behind unstoppable dominance.",
            category: .blueprints,
            estimatedMinutes: 12,
            learnerCount: "2.1k",
            isBookmarked: false
        ),
        MoneyMove(
            title: "Warren Buffett's Early Days",
            subtitle: "The moves that built a fortune.",
            category: .blueprints,
            estimatedMinutes: 10,
            learnerCount: "1.8k",
            isBookmarked: false
        ),
        
        // Value Traps - Failures and lessons
        MoneyMove(
            title: "The Fall of Enron",
            subtitle: "Red flags every investor should know.",
            category: .valueTraps,
            estimatedMinutes: 15,
            learnerCount: "1.5k",
            isBookmarked: false
        ),
        MoneyMove(
            title: "WeWork's Unraveling",
            subtitle: "When valuations don't match reality.",
            category: .valueTraps,
            estimatedMinutes: 11,
            learnerCount: "1.3k",
            isBookmarked: false
        ),
        
        // Battles - Comparative analysis
        MoneyMove(
            title: "Netflix vs. Disney+",
            subtitle: "The streaming wars breakdown.",
            category: .battles,
            estimatedMinutes: 14,
            learnerCount: "2.3k",
            isBookmarked: false
        ),
        MoneyMove(
            title: "Tesla vs. Traditional Auto",
            subtitle: "Innovation meets industry giants.",
            category: .battles,
            estimatedMinutes: 13,
            learnerCount: "1.9k",
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
