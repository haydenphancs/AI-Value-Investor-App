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

// MARK: - Book Level
enum BookLevel: String, CaseIterable {
    case starter = "Starter"
    case intermediate = "Intermediate"
    case advanced = "Advanced"

    var color: Color {
        switch self {
        case .starter: return Color(hex: "06B6D4") // Cyan
        case .intermediate: return Color(hex: "8B5CF6") // Purple
        case .advanced: return Color(hex: "F59E0B") // Amber
        }
    }
}

// MARK: - Book Category Tag
enum BookCategoryTag: String, CaseIterable {
    case mindset = "Mindset"
    case finance = "Finance"
    case strategy = "Strategy"
    case analysis = "Analysis"
    case psychology = "Psychology"
    case investing = "Investing"
    case economics = "Economics"
    case business = "Business"
}

// MARK: - Book Author
struct BookAuthor: Identifiable {
    let id = UUID()
    let name: String
    let title: String
    let bio: String
    let avatarGradientColors: [String]
}

// MARK: - Key Highlight
struct BookKeyHighlight: Identifiable {
    let id = UUID()
    let title: String
    let description: String
    let iconName: String
    let iconColor: String
}

// MARK: - Core Chapter
struct BookCoreChapter: Identifiable {
    let id = UUID()
    let number: Int
    let title: String
    let description: String

    /// Returns the detailed content for this chapter if available
    /// This links to the full CoreChapterContent for the detail view
    func getDetailContent(for book: LibraryBook) -> CoreChapterContent? {
        // Map chapter numbers to their detailed content
        // In a real app, this would fetch from a database or API
        switch (book.curriculumOrder, number) {
        case (1, 1): return .sampleEmployeeMindset
        case (1, 2): return .sampleFinancialScorecard
        default: return createGenericContent(for: book)
        }
    }

    /// Creates generic content for chapters without detailed content
    private func createGenericContent(for book: LibraryBook) -> CoreChapterContent {
        CoreChapterContent(
            chapterNumber: number,
            chapterTitle: title,
            bookTitle: book.title,
            bookAuthor: book.author,
            sections: [
                CoreChapterSection(
                    type: .heading,
                    title: nil,
                    content: .text("Overview")
                ),
                CoreChapterSection(
                    type: .paragraph,
                    title: nil,
                    content: .text(description)
                ),
                CoreChapterSection(
                    type: .callout,
                    title: nil,
                    content: .callout(CalloutContent(
                        title: "Coming Soon",
                        text: "Detailed content for this chapter is being developed. Check back soon for the full learning experience.",
                        style: .info
                    ))
                )
            ],
            audioDurationSeconds: 600,
            currentProgress: 0.0
        )
    }
}

// MARK: - Book Discussion/Review
struct BookDiscussion: Identifiable {
    let id = UUID()
    let authorName: String
    let authorAvatarGradient: [String]
    let rating: Int // 1-5 stars
    let content: String
    let postedDate: Date

    var formattedDate: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .full
        return formatter.localizedString(for: postedDate, relativeTo: Date())
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

    // Detail view properties
    let level: BookLevel
    let chapterCount: Int
    let categoryTags: [BookCategoryTag]
    let whyThisBook: String
    let authorDetail: BookAuthor
    let audioDurationSeconds: Int
    let readTimeMinutes: Int
    let viewCount: String
    let lastUpdated: Date

    // Core tab content
    let keyHighlights: [BookKeyHighlight]
    let coreChapters: [BookCoreChapter]
    let discussions: [BookDiscussion]

    // User progress - which chapter the user is currently on (1-indexed)
    let currentChapter: Int

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

    var formattedChapters: String {
        "\(chapterCount) Cores"
    }

    var formattedAudioDuration: String {
        let minutes = audioDurationSeconds / 60
        let seconds = audioDurationSeconds % 60
        return String(format: "%d:%02d", minutes, seconds)
    }

    var formattedReadTime: String {
        "\(readTimeMinutes) min"
    }

    var formattedViewCount: String {
        viewCount
    }

    var formattedLastUpdated: String {
        let calendar = Calendar.current
        let year = calendar.component(.year, from: lastUpdated)
        return "Updated \(year)"
    }

    /// Convert to AudioEpisode for playback
    var audioEpisode: AudioEpisode {
        AudioEpisode(
            id: "book-\(id.uuidString)",
            title: title,
            subtitle: "by \(author)",
            artworkGradientColors: [coverGradientStart, coverGradientEnd],
            artworkIcon: "book.fill",
            duration: TimeInterval(audioDurationSeconds),
            category: .books,
            authorName: author,
            sourceId: id.uuidString
        )
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
        // Mix of categories for visual variety
        MoneyMove(
            title: "How Amazon Built Its Moat",
            subtitle: "The strategy behind unstoppable dominance.",
            category: .blueprints,
            estimatedMinutes: 12,
            learnerCount: "2.1k",
            isBookmarked: false
        ),
        MoneyMove(
            title: "The Fall of Enron",
            subtitle: "Red flags every investor should know.",
            category: .valueTraps,
            estimatedMinutes: 15,
            learnerCount: "1.5k",
            isBookmarked: false
        ),
        MoneyMove(
            title: "Netflix vs. Disney+",
            subtitle: "The streaming wars breakdown.",
            category: .battles,
            estimatedMinutes: 14,
            learnerCount: "2.3k",
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
        MoneyMove(
            title: "Tesla vs. Traditional Auto",
            subtitle: "Innovation meets industry giants.",
            category: .battles,
            estimatedMinutes: 13,
            learnerCount: "1.9k",
            isBookmarked: false
        ),
        MoneyMove(
            title: "WeWork's Unraveling",
            subtitle: "When valuations don't match reality.",
            category: .valueTraps,
            estimatedMinutes: 11,
            learnerCount: "1.3k",
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
    // Sample discussions used across books
    private static let sampleDiscussions: [BookDiscussion] = [
        BookDiscussion(
            authorName: "Michael Chen",
            authorAvatarGradient: ["3B82F6", "1E40AF"],
            rating: 5,
            content: "This book completely changed my perspective on money and wealth. The lessons about assets vs liabilities are eye-opening. Highly recommend for anyone wanting to improve their financial literacy!",
            postedDate: Calendar.current.date(byAdding: .day, value: -2, to: Date())!
        ),
        BookDiscussion(
            authorName: "Sarah Johnson",
            authorAvatarGradient: ["EC4899", "BE185D"],
            rating: 5,
            content: "A must-read for everyone! The storytelling approach makes complex financial concepts easy to understand. I've already started applying these principles to my own finances.",
            postedDate: Calendar.current.date(byAdding: .day, value: -5, to: Date())!
        ),
        BookDiscussion(
            authorName: "David Park",
            authorAvatarGradient: ["22C55E", "15803D"],
            rating: 4,
            content: "Great foundational book for beginners. Some concepts are a bit simplified, but that's what makes it accessible. Perfect starting point for your investment journey.",
            postedDate: Calendar.current.date(byAdding: .day, value: -7, to: Date())!
        )
    ]

    static let sampleData: [LibraryBook] = [
        // Book 1 - Rich Dad Poor Dad - MASTERED
        LibraryBook(
            title: "Rich Dad Poor Dad",
            author: "Robert T. Kiyosaki",
            description: "What the rich teach their kids about money that the poor and middle class do not.",
            pageCount: 336,
            publishedYear: 1997,
            rating: 4.7,
            curriculumOrder: 1,
            isMastered: true,
            keyIdeasCount: 12,
            coverGradientStart: "7C3AED",
            coverGradientEnd: "4C1D95",
            level: .starter,
            chapterCount: 7,
            categoryTags: [.mindset, .finance],
            whyThisBook: "Rich Dad Poor Dad is Robert Kiyosaki's best-selling book about the difference in mindset between the poor, middle class, and rich. It advocates the importance of financial literacy, financial independence and building wealth through investing in assets.\n\nThe book is largely based on Kiyosaki's upbringing and education in Hawaii. It highlights the different attitudes toward money, work, and life between his biological father and the father of his best friend.",
            authorDetail: BookAuthor(
                name: "Robert T. Kiyosaki",
                title: "Entrepreneur, Investor & Author",
                bio: "Robert Kiyosaki is an American businessman and author. He founded the Rich Dad Company which provides personal finance and business education through books and videos.",
                avatarGradientColors: ["3B82F6", "1E40AF"]
            ),
            audioDurationSeconds: 1080,
            readTimeMinutes: 18,
            viewCount: "4.2M",
            lastUpdated: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 27))!,
            keyHighlights: [
                BookKeyHighlight(title: "Assets vs. Liabilities", description: "The rich acquire assets. The poor and middle class acquire liabilities that they think are assets.", iconName: "chart.pie.fill", iconColor: "22C55E"),
                BookKeyHighlight(title: "Financial Education", description: "Schools don't teach financial literacy. You must educate yourself about money and investing.", iconName: "book.fill", iconColor: "3B82F6"),
                BookKeyHighlight(title: "Work to Learn", description: "Don't work for money. Work to learn skills that will help you become financially independent.", iconName: "lightbulb.fill", iconColor: "F59E0B"),
                BookKeyHighlight(title: "Take Risks", description: "Playing it safe is actually the riskiest thing you can do. Learn to manage risk and take calculated chances.", iconName: "bolt.fill", iconColor: "8B5CF6")
            ],
            coreChapters: [
                BookCoreChapter(number: 1, title: "De-Programming the \"Employee\" Mindset", description: "Stop trading time for money by breaking the psychological cycle of fear and greed that keeps 90% of people trapped in the Rat Race."),
                BookCoreChapter(number: 2, title: "Mastering the Financial Scorecard", description: "Wealth isn't about how much you make, but how much you keep—learn to read the \"report card\" of adulthood by strictly distinguishing assets from liabilities."),
                BookCoreChapter(number: 3, title: "The Corporate Shield Strategy", description: "Utilize the \"secret weapon\" of the wealthy to legally minimize tax exposure and protect your assets before the government gets paid."),
                BookCoreChapter(number: 4, title: "The Opportunity Hunter", description: "Train your brain to see deals where others see chaos; financial intelligence is simply having more options than the average person."),
                BookCoreChapter(number: 5, title: "Trading Security for Skills", description: "View your job as a paid internship where you acquire the \"Triangle of Skills\"—sales, marketing, and communication—rather than a safety net."),
                BookCoreChapter(number: 6, title: "Conquering the Inner Saboteur", description: "Identify and neutralize the emotional barriers that kill investment deals even when the numbers make sense."),
                BookCoreChapter(number: 7, title: "The \"First 3 Steps\" Launchpad", description: "A tactical checklist to move from \"analysis paralysis\" to your first asset acquisition within 90 days.")
            ],
            discussions: sampleDiscussions,
            currentChapter: 2
        ),

        // Book 2 - The Intelligent Investor - MASTERED
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
            coverGradientEnd: "0F1F35",
            level: .intermediate,
            chapterCount: 20,
            categoryTags: [.investing, .analysis, .strategy],
            whyThisBook: "The Intelligent Investor is widely considered the bible of value investing. Benjamin Graham's timeless wisdom on how to think about investing has guided generations of the world's most successful investors.\n\nThe book teaches the concept of 'Mr. Market,' margin of safety, and the distinction between investing and speculation. Warren Buffett credits this book for shaping his investment philosophy.",
            authorDetail: BookAuthor(
                name: "Benjamin Graham",
                title: "Father of Value Investing",
                bio: "Benjamin Graham was an influential economist, professor, and professional investor. Known as the 'father of value investing,' he mentored Warren Buffett and developed fundamental analysis techniques still used today.",
                avatarGradientColors: ["1E3A5F", "0F1F35"]
            ),
            audioDurationSeconds: 2700,
            readTimeMinutes: 45,
            viewCount: "8.1M",
            lastUpdated: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 20))!,
            keyHighlights: [
                BookKeyHighlight(title: "Mr. Market", description: "Think of the market as a moody partner who offers to buy or sell shares daily at different prices.", iconName: "person.fill.questionmark", iconColor: "3B82F6"),
                BookKeyHighlight(title: "Margin of Safety", description: "Always buy at a significant discount to intrinsic value to protect against errors and bad luck.", iconName: "shield.fill", iconColor: "22C55E"),
                BookKeyHighlight(title: "Investor vs Speculator", description: "An investor analyzes fundamentals; a speculator bets on price movements.", iconName: "chart.bar.fill", iconColor: "F59E0B"),
                BookKeyHighlight(title: "Defensive Investing", description: "Build a diversified portfolio that doesn't require constant attention.", iconName: "lock.shield.fill", iconColor: "8B5CF6")
            ],
            coreChapters: [
                BookCoreChapter(number: 1, title: "Investment versus Speculation", description: "Defining what it means to be a true investor rather than a gambler."),
                BookCoreChapter(number: 2, title: "The Investor and Inflation", description: "How to protect your portfolio against the erosion of purchasing power."),
                BookCoreChapter(number: 3, title: "A Century of Stock Market History", description: "Lessons from 100 years of market performance."),
                BookCoreChapter(number: 4, title: "General Portfolio Policy", description: "The defensive investor's approach to asset allocation."),
                BookCoreChapter(number: 5, title: "The Defensive Investor and Common Stocks", description: "Criteria for selecting quality stocks for conservative portfolios."),
                BookCoreChapter(number: 6, title: "The Enterprising Investor", description: "Strategies for more active investors seeking higher returns.")
            ],
            discussions: sampleDiscussions,
            currentChapter: 6
        ),

        // Book 3 - The Psychology of Money
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
            coverGradientEnd: "064E3B",
            level: .starter,
            chapterCount: 20,
            categoryTags: [.psychology, .mindset, .finance],
            whyThisBook: "Morgan Housel explores the strange ways people think about money and teaches you how to make better sense of one of life's most important topics.\n\nThrough 19 short stories, the book demonstrates that financial success is not about what you know technically but how you behave. It's about soft skills that are often overlooked in financial education.",
            authorDetail: BookAuthor(
                name: "Morgan Housel",
                title: "Partner at Collaborative Fund",
                bio: "Morgan Housel is a partner at Collaborative Fund and a former columnist at The Motley Fool and The Wall Street Journal. He is a two-time winner of the Best in Business Award from the Society of American Business Editors.",
                avatarGradientColors: ["059669", "047857"]
            ),
            audioDurationSeconds: 1560,
            readTimeMinutes: 26,
            viewCount: "5.7M",
            lastUpdated: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 15))!,
            keyHighlights: [
                BookKeyHighlight(title: "Compounding", description: "The most powerful force in finance. Small gains over long periods create enormous wealth.", iconName: "arrow.up.right", iconColor: "22C55E"),
                BookKeyHighlight(title: "Room for Error", description: "Plan for things going wrong. Survival is the cornerstone of wealth building.", iconName: "exclamationmark.shield.fill", iconColor: "F59E0B"),
                BookKeyHighlight(title: "Wealth is Hidden", description: "True wealth is what you don't see—money not spent on visible luxuries.", iconName: "eye.slash.fill", iconColor: "8B5CF6"),
                BookKeyHighlight(title: "Reasonable > Rational", description: "Being reasonable is more sustainable than being coldly rational with money.", iconName: "heart.fill", iconColor: "EC4899")
            ],
            coreChapters: [
                BookCoreChapter(number: 1, title: "No One's Crazy", description: "Everyone has unique experiences that shape their financial behavior."),
                BookCoreChapter(number: 2, title: "Luck & Risk", description: "Nothing is as good or bad as it seems; outcomes involve luck."),
                BookCoreChapter(number: 3, title: "Never Enough", description: "The hardest financial skill is getting the goalpost to stop moving."),
                BookCoreChapter(number: 4, title: "Confounding Compounding", description: "The counterintuitive math behind exponential growth."),
                BookCoreChapter(number: 5, title: "Getting Wealthy vs. Staying Wealthy", description: "Making money requires risk; keeping it requires humility."),
                BookCoreChapter(number: 6, title: "Tails, You Win", description: "A few outlier events drive the majority of outcomes.")
            ],
            discussions: sampleDiscussions,
            currentChapter: 1
        ),

        // Book 4 - One Up On Wall Street
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
            coverGradientEnd: "1A2D25",
            level: .intermediate,
            chapterCount: 18,
            categoryTags: [.investing, .strategy, .analysis],
            whyThisBook: "Peter Lynch ran the Magellan Fund at Fidelity, achieving an average annual return of 29.2% over 13 years. In this book, he shares his investment approach of finding 'tenbaggers' - stocks that increase tenfold in value.\n\nLynch teaches investors to use their everyday experiences to find investment opportunities, categorizing stocks into six types to help identify the best opportunities.",
            authorDetail: BookAuthor(
                name: "Peter Lynch",
                title: "Legendary Fidelity Fund Manager",
                bio: "Peter Lynch is a legendary American investor, known for achieving an average annual return of 29.2% as manager of the Magellan Fund at Fidelity Investments, making it the best-performing mutual fund in the world.",
                avatarGradientColors: ["2D4A3E", "1A2D25"]
            ),
            audioDurationSeconds: 1680,
            readTimeMinutes: 28,
            viewCount: "3.2M",
            lastUpdated: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 10))!,
            keyHighlights: [
                BookKeyHighlight(title: "Invest in What You Know", description: "Your personal experiences give you an edge over Wall Street analysts.", iconName: "lightbulb.fill", iconColor: "F59E0B"),
                BookKeyHighlight(title: "Tenbaggers", description: "Look for stocks with the potential to increase 10x in value.", iconName: "arrow.up.circle.fill", iconColor: "22C55E"),
                BookKeyHighlight(title: "Six Stock Categories", description: "Classify stocks as slow growers, stalwarts, fast growers, cyclicals, turnarounds, or asset plays.", iconName: "square.grid.2x2.fill", iconColor: "3B82F6"),
                BookKeyHighlight(title: "Do Your Homework", description: "Research the company's story, financials, and competitive position.", iconName: "doc.text.magnifyingglass", iconColor: "8B5CF6")
            ],
            coreChapters: [
                BookCoreChapter(number: 1, title: "The Making of a Stockpicker", description: "Lynch's journey to becoming a legendary investor."),
                BookCoreChapter(number: 2, title: "The Wall Street Oxymorons", description: "Why individual investors can beat the professionals."),
                BookCoreChapter(number: 3, title: "Is This Gambling, or What?", description: "Distinguishing investing from speculation."),
                BookCoreChapter(number: 4, title: "Passing the Mirror Test", description: "Knowing yourself before investing."),
                BookCoreChapter(number: 5, title: "Is This a Good Market?", description: "Why market timing doesn't work."),
                BookCoreChapter(number: 6, title: "Stalking the Tenbagger", description: "Finding stocks with multi-bagger potential.")
            ],
            discussions: sampleDiscussions,
            currentChapter: 1
        ),

        // Book 5 - Common Stocks and Uncommon Profits
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
            coverGradientEnd: "2D1212",
            level: .intermediate,
            chapterCount: 14,
            categoryTags: [.investing, .strategy, .analysis],
            whyThisBook: "Philip Fisher pioneered growth investing and developed the 'scuttlebutt' method of research. This book presents Fisher's 15 points to look for in a common stock.\n\nWarren Buffett describes himself as '85% Graham and 15% Fisher,' highlighting the profound impact this book had on his transition from pure value investing to quality-focused investing.",
            authorDetail: BookAuthor(
                name: "Philip Fisher",
                title: "Pioneer of Growth Investing",
                bio: "Philip Fisher was an American stock investor known for his investment philosophy, detailed in his book 'Common Stocks and Uncommon Profits.' He pioneered the growth investing strategy and influenced Warren Buffett.",
                avatarGradientColors: ["4A1E1E", "2D1212"]
            ),
            audioDurationSeconds: 1440,
            readTimeMinutes: 24,
            viewCount: "2.1M",
            lastUpdated: Calendar.current.date(from: DateComponents(year: 2026, month: 1, day: 5))!,
            keyHighlights: [
                BookKeyHighlight(title: "Scuttlebutt Method", description: "Research by talking to customers, suppliers, competitors, and employees.", iconName: "bubble.left.and.bubble.right.fill", iconColor: "3B82F6"),
                BookKeyHighlight(title: "15 Points", description: "A checklist of qualities to evaluate before investing in a company.", iconName: "checklist", iconColor: "22C55E"),
                BookKeyHighlight(title: "Hold for Long Term", description: "Buy great companies and hold them for years or decades.", iconName: "clock.fill", iconColor: "F59E0B"),
                BookKeyHighlight(title: "Management Quality", description: "The caliber of management is crucial to long-term success.", iconName: "person.3.fill", iconColor: "8B5CF6")
            ],
            coreChapters: [
                BookCoreChapter(number: 1, title: "Clues from the Past", description: "How historical performance indicates future potential."),
                BookCoreChapter(number: 2, title: "What 'Scuttlebutt' Can Do", description: "The power of on-the-ground research."),
                BookCoreChapter(number: 3, title: "What to Buy: The Fifteen Points", description: "Fisher's famous checklist for stock selection."),
                BookCoreChapter(number: 4, title: "What to Buy: Applying This to Your Own Needs", description: "Customizing the approach to your situation."),
                BookCoreChapter(number: 5, title: "When to Buy", description: "Timing your purchases for maximum value."),
                BookCoreChapter(number: 6, title: "When to Sell", description: "The rare circumstances that justify selling.")
            ],
            discussions: sampleDiscussions,
            currentChapter: 1
        ),

        // Book 6 - The Little Book of Common Sense Investing
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
            coverGradientEnd: "1E3A8A",
            level: .starter,
            chapterCount: 20,
            categoryTags: [.investing, .strategy],
            whyThisBook: "John Bogle, founder of Vanguard, revolutionized investing by creating the first index fund. This book makes the case for passive investing and demonstrates why most active managers fail to beat the market.\n\nThe book teaches the importance of low costs, broad diversification, and staying the course through market volatility.",
            authorDetail: BookAuthor(
                name: "John C. Bogle",
                title: "Founder of Vanguard Group",
                bio: "John Clifton Bogle was the founder and chief executive of The Vanguard Group. He is credited with creating the first index fund and was a driving force behind the growth of passive investing.",
                avatarGradientColors: ["1E40AF", "1E3A8A"]
            ),
            audioDurationSeconds: 1320,
            readTimeMinutes: 22,
            viewCount: "2.8M",
            lastUpdated: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 28))!,
            keyHighlights: [
                BookKeyHighlight(title: "Index Funds Win", description: "Most active managers underperform the market over time.", iconName: "chart.line.uptrend.xyaxis", iconColor: "22C55E"),
                BookKeyHighlight(title: "Costs Matter", description: "Every dollar paid in fees is a dollar less in returns.", iconName: "dollarsign.circle.fill", iconColor: "F59E0B"),
                BookKeyHighlight(title: "Stay the Course", description: "Time in the market beats timing the market.", iconName: "clock.arrow.circlepath", iconColor: "3B82F6"),
                BookKeyHighlight(title: "Simple is Best", description: "A total market index fund is all most investors need.", iconName: "sparkles", iconColor: "8B5CF6")
            ],
            coreChapters: [
                BookCoreChapter(number: 1, title: "A Parable", description: "The Gotrocks family learns about the cost of financial intermediaries."),
                BookCoreChapter(number: 2, title: "Rational Exuberance", description: "Understanding long-term stock market returns."),
                BookCoreChapter(number: 3, title: "Cast Your Lot with Business", description: "Why owning businesses beats speculating."),
                BookCoreChapter(number: 4, title: "How Most Investors Turn a Winner's Game into a Loser's Game", description: "The mathematics of active management failure."),
                BookCoreChapter(number: 5, title: "The Grand Illusion", description: "Why past performance doesn't predict future results."),
                BookCoreChapter(number: 6, title: "Taxes Are Costs, Too", description: "The hidden drag of tax inefficiency.")
            ],
            discussions: sampleDiscussions,
            currentChapter: 1
        ),

        // Book 7 - A Random Walk Down Wall Street
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
            coverGradientEnd: "451A03",
            level: .intermediate,
            chapterCount: 15,
            categoryTags: [.economics, .investing, .analysis],
            whyThisBook: "Burton Malkiel's classic introduces the efficient market hypothesis and challenges the notion that expert stock pickers can consistently beat the market.\n\nThe book covers both fundamental and technical analysis, exploring their limitations, and makes the case for a diversified, low-cost investment strategy.",
            authorDetail: BookAuthor(
                name: "Burton Malkiel",
                title: "Professor at Princeton University",
                bio: "Burton Gordon Malkiel is an American economist and writer, most known for his classic finance book A Random Walk Down Wall Street. He is a proponent of the efficient market hypothesis.",
                avatarGradientColors: ["7C2D12", "451A03"]
            ),
            audioDurationSeconds: 1920,
            readTimeMinutes: 32,
            viewCount: "1.9M",
            lastUpdated: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 20))!,
            keyHighlights: [
                BookKeyHighlight(title: "Efficient Markets", description: "Stock prices reflect all available information, making consistent outperformance difficult.", iconName: "equal.circle.fill", iconColor: "3B82F6"),
                BookKeyHighlight(title: "Random Walk", description: "Short-term price movements are unpredictable and follow a random pattern.", iconName: "shuffle", iconColor: "F59E0B"),
                BookKeyHighlight(title: "Diversification", description: "Don't put all your eggs in one basket; spread risk across assets.", iconName: "square.grid.3x3.fill", iconColor: "22C55E"),
                BookKeyHighlight(title: "Buy and Hold", description: "Time in the market beats timing the market.", iconName: "hand.raised.fill", iconColor: "8B5CF6")
            ],
            coreChapters: [
                BookCoreChapter(number: 1, title: "Firm Foundations and Castles in the Air", description: "Two theories of how markets work."),
                BookCoreChapter(number: 2, title: "The Madness of Crowds", description: "Historical bubbles and manias."),
                BookCoreChapter(number: 3, title: "Stock Valuation from the Sixties", description: "How valuation methods evolved."),
                BookCoreChapter(number: 4, title: "The Biggest Bubble of All", description: "Lessons from the internet bubble."),
                BookCoreChapter(number: 5, title: "Technical and Fundamental Analysis", description: "Examining the two main approaches to picking stocks."),
                BookCoreChapter(number: 6, title: "A New Walking Shoe", description: "Modern portfolio theory explained.")
            ],
            discussions: sampleDiscussions,
            currentChapter: 1
        ),

        // Book 8 - The Essays of Warren Buffett
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
            coverGradientEnd: "78350F",
            level: .advanced,
            chapterCount: 8,
            categoryTags: [.investing, .business, .strategy],
            whyThisBook: "This collection compiles Warren Buffett's annual shareholder letters into a coherent philosophy of investing and business management.\n\nThe essays cover corporate governance, finance, investing, and common stock, providing direct insight into the mind of the world's most successful investor.",
            authorDetail: BookAuthor(
                name: "Warren Buffett",
                title: "Chairman & CEO, Berkshire Hathaway",
                bio: "Warren Edward Buffett is an American business magnate, investor, and philanthropist. Known as the 'Oracle of Omaha,' he is one of the most successful investors in history and consistently ranks among the world's wealthiest people.",
                avatarGradientColors: ["B45309", "78350F"]
            ),
            audioDurationSeconds: 2100,
            readTimeMinutes: 35,
            viewCount: "4.5M",
            lastUpdated: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 15))!,
            keyHighlights: [
                BookKeyHighlight(title: "Circle of Competence", description: "Stay within areas you truly understand.", iconName: "circle.dashed", iconColor: "3B82F6"),
                BookKeyHighlight(title: "Economic Moats", description: "Invest in businesses with durable competitive advantages.", iconName: "shield.checkered", iconColor: "22C55E"),
                BookKeyHighlight(title: "Owner Earnings", description: "Focus on cash flow available to shareholders after maintenance capex.", iconName: "banknote.fill", iconColor: "F59E0B"),
                BookKeyHighlight(title: "Long-term Focus", description: "Our favorite holding period is forever.", iconName: "infinity", iconColor: "8B5CF6")
            ],
            coreChapters: [
                BookCoreChapter(number: 1, title: "Corporate Governance", description: "How boards and managers should serve shareholders."),
                BookCoreChapter(number: 2, title: "Corporate Finance and Investing", description: "Capital allocation and investment philosophy."),
                BookCoreChapter(number: 3, title: "Alternatives to Common Stock", description: "Fixed-income securities and other investments."),
                BookCoreChapter(number: 4, title: "Common Stock", description: "How to value and select individual stocks."),
                BookCoreChapter(number: 5, title: "Mergers and Acquisitions", description: "When acquisitions create or destroy value."),
                BookCoreChapter(number: 6, title: "Accounting and Valuation", description: "Understanding financial statements deeply.")
            ],
            discussions: sampleDiscussions,
            currentChapter: 1
        ),

        // Book 9 - Security Analysis
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
            coverGradientEnd: "1F2937",
            level: .advanced,
            chapterCount: 52,
            categoryTags: [.analysis, .investing, .finance],
            whyThisBook: "The foundational text of value investing, Security Analysis established the discipline of professional financial analysis during the Great Depression.\n\nThis comprehensive guide covers bond analysis, preferred stock analysis, and common stock analysis with timeless principles that remain relevant today.",
            authorDetail: BookAuthor(
                name: "Benjamin Graham & David Dodd",
                title: "Founders of Value Investing",
                bio: "Benjamin Graham and David Dodd were professors at Columbia Business School who together wrote Security Analysis, establishing the intellectual foundation for what would later be called value investing.",
                avatarGradientColors: ["374151", "1F2937"]
            ),
            audioDurationSeconds: 3600,
            readTimeMinutes: 60,
            viewCount: "1.5M",
            lastUpdated: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 10))!,
            keyHighlights: [
                BookKeyHighlight(title: "Intrinsic Value", description: "Every security has a value based on its underlying fundamentals.", iconName: "dollarsign.square.fill", iconColor: "22C55E"),
                BookKeyHighlight(title: "Margin of Safety", description: "The difference between price and value protects against loss.", iconName: "shield.fill", iconColor: "3B82F6"),
                BookKeyHighlight(title: "Financial Statement Analysis", description: "Deep dive into balance sheets, income statements, and cash flows.", iconName: "doc.text.fill", iconColor: "F59E0B"),
                BookKeyHighlight(title: "Bond Analysis", description: "Evaluating fixed income securities for safety and yield.", iconName: "percent", iconColor: "8B5CF6")
            ],
            coreChapters: [
                BookCoreChapter(number: 1, title: "The Scope and Limits of Security Analysis", description: "Defining what analysis can and cannot do."),
                BookCoreChapter(number: 2, title: "Fundamental Elements in the Problem of Analysis", description: "Core concepts underlying all security analysis."),
                BookCoreChapter(number: 3, title: "Sources of Information", description: "Where to find reliable data for analysis."),
                BookCoreChapter(number: 4, title: "Distinctions Between Investment and Speculation", description: "A clear framework for classifying activities."),
                BookCoreChapter(number: 5, title: "Classification of Securities", description: "Understanding different types of financial instruments."),
                BookCoreChapter(number: 6, title: "The Selection of Fixed-Value Investments", description: "Criteria for choosing bonds and preferred stocks.")
            ],
            discussions: sampleDiscussions,
            currentChapter: 1
        ),

        // Book 10 - The Most Important Thing
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
            coverGradientEnd: "3B0764",
            level: .advanced,
            chapterCount: 20,
            categoryTags: [.investing, .psychology, .strategy],
            whyThisBook: "Howard Marks distills 40 years of investment wisdom into the essential principles that separate successful investors from the rest.\n\nThe book covers second-level thinking, understanding market cycles, managing risk, and the importance of contrarian thinking in achieving superior returns.",
            authorDetail: BookAuthor(
                name: "Howard Marks",
                title: "Co-Chairman, Oaktree Capital",
                bio: "Howard Stanley Marks is an American investor and writer. He is the co-founder and co-chairman of Oaktree Capital Management, the largest investor in distressed securities worldwide.",
                avatarGradientColors: ["581C87", "3B0764"]
            ),
            audioDurationSeconds: 1200,
            readTimeMinutes: 20,
            viewCount: "2.3M",
            lastUpdated: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 5))!,
            keyHighlights: [
                BookKeyHighlight(title: "Second-Level Thinking", description: "Go beyond the obvious to find insights others miss.", iconName: "brain.head.profile", iconColor: "8B5CF6"),
                BookKeyHighlight(title: "Understanding Risk", description: "Risk means more things can happen than will happen.", iconName: "exclamationmark.triangle.fill", iconColor: "F59E0B"),
                BookKeyHighlight(title: "Market Cycles", description: "Markets swing between euphoria and despair; position accordingly.", iconName: "waveform.path", iconColor: "3B82F6"),
                BookKeyHighlight(title: "Contrarian Thinking", description: "The best opportunities come from disagreeing with consensus.", iconName: "arrow.left.arrow.right", iconColor: "22C55E")
            ],
            coreChapters: [
                BookCoreChapter(number: 1, title: "Second-Level Thinking", description: "The foundation of superior returns."),
                BookCoreChapter(number: 2, title: "Understanding Market Efficiency", description: "What efficient markets mean for investors."),
                BookCoreChapter(number: 3, title: "Value", description: "The relationship between price and intrinsic value."),
                BookCoreChapter(number: 4, title: "The Relationship Between Price and Value", description: "Why buying below value is essential."),
                BookCoreChapter(number: 5, title: "Understanding Risk", description: "Redefining risk beyond volatility."),
                BookCoreChapter(number: 6, title: "Recognizing Risk", description: "When risk is highest and lowest.")
            ],
            discussions: sampleDiscussions,
            currentChapter: 1
        )
    ]
}
