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
        case .blueprints: return "trophy.fill"
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

// MARK: - Library Book (For Book Library/Curriculum View)
struct LibraryBook: Identifiable {
    let id = UUID()
    let title: String
    let author: String
    let description: String
    let pageCount: Int
    let publishedYear: Int
    let rating: Double
    let curriculumOrder: Int
    let isMastered: Bool
    let keyIdeasCount: Int
    let coverGradientStart: String
    let coverGradientEnd: String

    var formattedRating: String {
        String(format: "%.1f", rating)
    }

    var formattedPages: String {
        "\(pageCount) pages"
    }

    var formattedPublished: String {
        "Published \(publishedYear)"
    }

    var formattedKeyIdeas: String {
        "\(keyIdeasCount) Key Ideas"
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

extension LibraryBook {
    static let sampleData: [LibraryBook] = [
        // Book 1 - MASTERED
        LibraryBook(
            title: "Rich Dad Poor Dad",
            author: "Robert Kiyosaki",
            description: "What the rich teach their kids about money that the poor and middle class do not.",
            pageCount: 336,
            publishedYear: 1997,
            rating: 4.7,
            curriculumOrder: 1,
            isMastered: true,
            keyIdeasCount: 12,
            coverGradientStart: "7C3AED",
            coverGradientEnd: "4C1D95"
        ),
        // Book 2 - MASTERED
        LibraryBook(
            title: "The Intelligent Investor",
            author: "Benjamin Graham",
            description: "The definitive book on value investing. Warren Buffett calls it 'the best book on investing ever written.'",
            pageCount: 623,
            publishedYear: 1949,
            rating: 4.8,
            curriculumOrder: 2,
            isMastered: true,
            keyIdeasCount: 18,
            coverGradientStart: "1E3A5F",
            coverGradientEnd: "0F1F35"
        ),
        // Book 3
        LibraryBook(
            title: "The Psychology of Money",
            author: "Morgan Housel",
            description: "Timeless lessons on wealth, greed, and happiness. Understanding how emotions drive financial decisions.",
            pageCount: 256,
            publishedYear: 2020,
            rating: 4.9,
            curriculumOrder: 3,
            isMastered: false,
            keyIdeasCount: 15,
            coverGradientStart: "059669",
            coverGradientEnd: "064E3B"
        ),
        // Book 4
        LibraryBook(
            title: "One Up On Wall Street",
            author: "Peter Lynch",
            description: "How to use what you already know to make money in the market from the legendary Fidelity fund manager.",
            pageCount: 304,
            publishedYear: 1989,
            rating: 4.5,
            curriculumOrder: 4,
            isMastered: false,
            keyIdeasCount: 14,
            coverGradientStart: "2D4A3E",
            coverGradientEnd: "1A2D25"
        ),
        // Book 5
        LibraryBook(
            title: "Common Stocks and Uncommon Profits",
            author: "Philip Fisher",
            description: "The growth investing masterpiece that influenced Warren Buffett's investment philosophy.",
            pageCount: 271,
            publishedYear: 1958,
            rating: 4.7,
            curriculumOrder: 5,
            isMastered: false,
            keyIdeasCount: 16,
            coverGradientStart: "4A1E1E",
            coverGradientEnd: "2D1212"
        ),
        // Book 6
        LibraryBook(
            title: "The Little Book of Common Sense Investing",
            author: "John C. Bogle",
            description: "The only way to guarantee your fair share of stock market returns. The case for index funds.",
            pageCount: 304,
            publishedYear: 2007,
            rating: 4.6,
            curriculumOrder: 6,
            isMastered: false,
            keyIdeasCount: 10,
            coverGradientStart: "1E40AF",
            coverGradientEnd: "1E3A8A"
        ),
        // Book 7
        LibraryBook(
            title: "A Random Walk Down Wall Street",
            author: "Burton Malkiel",
            description: "The time-tested strategy for successful investing. Understanding market efficiency.",
            pageCount: 432,
            publishedYear: 1973,
            rating: 4.4,
            curriculumOrder: 7,
            isMastered: false,
            keyIdeasCount: 13,
            coverGradientStart: "7C2D12",
            coverGradientEnd: "451A03"
        ),
        // Book 8
        LibraryBook(
            title: "The Essays of Warren Buffett",
            author: "Warren Buffett & Lawrence Cunningham",
            description: "Lessons for corporate America. Wisdom from the Oracle of Omaha's annual letters.",
            pageCount: 368,
            publishedYear: 1997,
            rating: 4.8,
            curriculumOrder: 8,
            isMastered: false,
            keyIdeasCount: 20,
            coverGradientStart: "B45309",
            coverGradientEnd: "78350F"
        ),
        // Book 9
        LibraryBook(
            title: "Security Analysis",
            author: "Benjamin Graham & David Dodd",
            description: "The classic 1934 edition. The foundation of modern value investing analysis.",
            pageCount: 725,
            publishedYear: 1934,
            rating: 4.6,
            curriculumOrder: 9,
            isMastered: false,
            keyIdeasCount: 22,
            coverGradientStart: "374151",
            coverGradientEnd: "1F2937"
        ),
        // Book 10
        LibraryBook(
            title: "The Most Important Thing",
            author: "Howard Marks",
            description: "Uncommon sense for the thoughtful investor. Legendary Oaktree Capital insights.",
            pageCount: 200,
            publishedYear: 2011,
            rating: 4.7,
            curriculumOrder: 10,
            isMastered: false,
            keyIdeasCount: 17,
            coverGradientStart: "581C87",
            coverGradientEnd: "3B0764"
        )
    ]
}
