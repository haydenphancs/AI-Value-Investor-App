//
//  MoneyMoveArticleModels.swift
//  ios
//
//  Data models for Money Move Article Detail View
//

import Foundation
import SwiftUI

// MARK: - Money Move Article (Full Detail)
struct MoneyMoveArticle: Identifiable {
    let id = UUID()
    let title: String
    let subtitle: String
    let category: MoneyMoveCategory
    let author: ArticleAuthor
    let publishedAt: Date
    let readTimeMinutes: Int
    let viewCount: String
    let commentCount: Int
    let isBookmarked: Bool
    let hasAudioVersion: Bool

    // Hero section
    let heroGradientColors: [String]
    let tagLabel: String?
    let isFeatured: Bool // Indicates if this is a featured article

    // Content sections
    let keyHighlights: [ArticleHighlight]
    let sections: [ArticleSection]
    let statistics: [ArticleStatistic]

    // Engagement
    let comments: [ArticleComment]
    let relatedArticles: [RelatedArticle]

    var formattedDate: String {
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM dd, yyyy"
        return formatter.string(from: publishedAt)
    }

    var formattedReadTime: String {
        "\(readTimeMinutes) min read"
    }
}

// MARK: - Article Author
struct ArticleAuthor: Identifiable {
    let id = UUID()
    let name: String
    let avatarName: String?
    let title: String
    let isVerified: Bool
    let followerCount: String

    var avatarInitials: String {
        let components = name.components(separatedBy: " ")
        let initials = components.compactMap { $0.first }.prefix(2)
        return String(initials).uppercased()
    }
}

// MARK: - Article Highlight (Key Points)
struct ArticleHighlight: Identifiable {
    let id = UUID()
    let icon: String
    let title: String
    let description: String
}

// MARK: - Article Section
struct ArticleSection: Identifiable {
    let id = UUID()
    let title: String
    let icon: String?
    let content: [ArticleSectionContent]
    let hasGlowEffect: Bool

    init(title: String, icon: String? = nil, content: [ArticleSectionContent], hasGlowEffect: Bool = false) {
        self.title = title
        self.icon = icon
        self.content = content
        self.hasGlowEffect = hasGlowEffect
    }
}

// MARK: - Article Section Content Types
enum ArticleSectionContent: Identifiable {
    case paragraph(String)
    case bulletList([String])
    case subheading(String)
    case quote(text: String, attribution: String?)
    case callout(icon: String, text: String, style: CalloutStyle)
    case chart(ChartData)

    var id: String {
        switch self {
        case .paragraph(let text): return "p-\(text.prefix(20))"
        case .bulletList(let items): return "bl-\(items.first ?? "")"
        case .subheading(let text): return "sh-\(text)"
        case .quote(let text, _): return "q-\(text.prefix(20))"
        case .callout(_, let text, _): return "c-\(text.prefix(20))"
        case .chart(let data): return "chart-\(data.title)"
        }
    }
}

// MARK: - Callout Style
enum CalloutStyle {
    case info
    case warning
    case success
    case highlight

    var backgroundColor: Color {
        switch self {
        case .info: return Color(hex: "3B82F6").opacity(0.05)
        case .warning: return Color(hex: "F59E0B").opacity(0.05)
        case .success: return Color(hex: "22C55E").opacity(0.05)
        case .highlight: return Color(hex: "A855F7").opacity(0.05)
        }
    }

    var borderColor: Color {
        switch self {
        case .info: return Color(hex: "3B82F6").opacity(0.3)
        case .warning: return Color(hex: "F59E0B").opacity(0.3)
        case .success: return Color(hex: "22C55E").opacity(0.3)
        case .highlight: return Color(hex: "A855F7").opacity(0.3)
        }
    }
}

// MARK: - Chart Data
struct ChartData: Identifiable {
    let id = UUID()
    let title: String
    let type: ChartType
    let dataPoints: [ChartDataPoint]
}

enum ChartType {
    case line
    case bar
    case area
}

struct ChartDataPoint: Identifiable {
    let id = UUID()
    let label: String
    let value: Double
    let color: Color?

    init(label: String, value: Double, color: Color? = nil) {
        self.label = label
        self.value = value
        self.color = color
    }
}

// MARK: - Article Statistic
struct ArticleStatistic: Identifiable {
    let id = UUID()
    let value: String
    let label: String
    let trend: StatisticTrend?
    let trendValue: String?

    init(value: String, label: String, trend: StatisticTrend? = nil, trendValue: String? = nil) {
        self.value = value
        self.label = label
        self.trend = trend
        self.trendValue = trendValue
    }
}

enum StatisticTrend {
    case up
    case down
    case neutral

    var color: Color {
        switch self {
        case .up: return AppColors.bullish
        case .down: return AppColors.bearish
        case .neutral: return AppColors.neutral
        }
    }

    var icon: String {
        switch self {
        case .up: return "arrow.up"
        case .down: return "arrow.down"
        case .neutral: return "minus"
        }
    }
}

// MARK: - Article Comment
struct ArticleComment: Identifiable {
    let id = UUID()
    let authorName: String
    let authorAvatar: String?
    let content: String
    let postedAt: Date
    let likeCount: Int
    let replyCount: Int
    let isVerified: Bool

    var timeAgo: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: postedAt, relativeTo: Date())
    }

    var avatarInitials: String {
        let components = authorName.components(separatedBy: " ")
        let initials = components.compactMap { $0.first }.prefix(2)
        return String(initials).uppercased()
    }
}

// MARK: - Related Article
struct RelatedArticle: Identifiable {
    let id = UUID()
    let title: String
    let subtitle: String
    let category: MoneyMoveCategory
    let readTimeMinutes: Int
    let viewCount: String
    let gradientColors: [String]
}

// MARK: - Article Action Type
enum ArticleActionType {
    case mobilePost
    case instantAccess
    case listen
    case share
    case team

    var icon: String {
        switch self {
        case .mobilePost: return "iphone"
        case .instantAccess: return "bolt.fill"
        case .listen: return "headphones"
        case .share: return "square.and.arrow.up"
        case .team: return "person.2.fill"
        }
    }

    var label: String {
        switch self {
        case .mobilePost: return "Mobile Post"
        case .instantAccess: return "Instant Access"
        case .listen: return "Listen"
        case .share: return "Share"
        case .team: return "Usho Team"
        }
    }
}

// MARK: - Sample Data
extension MoneyMoveArticle {
    static let sampleDigitalFinance = MoneyMoveArticle(
        title: "The Future of Digital Finance",
        subtitle: "Exploring the intersection of fintech innovation, cryptocurrency adoption, and traditional banking transformation.",
        category: .blueprints,
        author: ArticleAuthor(
            name: "The Alpha",
            avatarName: nil,
            title: "Investment Research",
            isVerified: true,
            followerCount: "45.2k"
        ),
        publishedAt: Calendar.current.date(byAdding: .day, value: -3, to: Date())!,
        readTimeMinutes: 18,
        viewCount: "4.2M",
        commentCount: 124,
        isBookmarked: false,
        hasAudioVersion: true,
        heroGradientColors: ["1E3A5F", "0D1B2A", "1B263B"],
        tagLabel: "MUST READ",
        isFeatured: false,
        keyHighlights: [
            ArticleHighlight(
                icon: "building.columns.fill",
                title: "The Alpha",
                description: "As technology becomes ubiquitous, decentralized finance (DeFi) is reshaping how we invest."
            ),
            ArticleHighlight(
                icon: "chart.line.uptrend.xyaxis",
                title: "Key Trends",
                description: "The pace of banking innovation has never been faster. This conversation isn't about tomorrow's shift."
            ),
            ArticleHighlight(
                icon: "shield.checkered",
                title: "Risk Factors",
                description: "Despite strong prospects, regulatory challenges and market volatility remain key concerns."
            )
        ],
        sections: [
            ArticleSection(
                title: "The Rise of Decentralized Finance",
                icon: "chart.bar.fill",
                content: [
                    .paragraph("Decentralized finance, or DeFi, represents a fundamental shift in how financial services are delivered. By removing intermediaries through blockchain technology and smart contracts, DeFi platforms are offering users direct access to financial instruments that were once the exclusive domain of institutions."),
                    .paragraph("The implications are profound. Users can now access loans, trade assets, and earn yields on their digital assets without needing to seek permission or provide extensive documentation."),
                    .callout(
                        icon: "lightbulb.fill",
                        text: "DeFi protocols have processed over $180B in total value locked, representing a 340% increase from last year.",
                        style: .highlight
                    ),
                    .bulletList([
                        "Permissionless lending and borrowing",
                        "Automated market makers (AMMs)",
                        "Yield farming and liquidity mining",
                        "Cross-chain interoperability"
                    ])
                ],
                hasGlowEffect: true
            ),
            ArticleSection(
                title: "Artificial Intelligence in Banking",
                icon: "cpu.fill",
                content: [
                    .paragraph("Traditional banks are not standing still. The integration of artificial intelligence and machine learning has revolutionized everything from fraud detection to customer service. AI-powered chatbots now handle over 70% of customer inquiries, while advanced algorithms identify suspicious transactions in milliseconds."),
                    .subheading("Enhanced Security"),
                    .paragraph("Biometric authentication, combined with behavioral analysis, has reduced fraud rates by 45% across major financial institutions. Banks are investing heavily in zero-trust security architectures."),
                    .subheading("Personalized Experiences"),
                    .paragraph("Machine learning models analyze spending patterns to provide personalized financial advice, automatically categorize transactions, and predict future expenses with remarkable accuracy.")
                ]
            ),
            ArticleSection(
                title: "Embedded Finance and Super Apps",
                icon: "apps.iphone",
                content: [
                    .paragraph("The boundaries between financial services and other digital experiences are dissolving. Embedded finance allows non-financial companies to offer banking, lending, and payment services seamlessly within their platforms."),
                    .quote(
                        text: "The future of finance isn't about going to the bank—it's about banking coming to you, wherever you are.",
                        attribution: "Industry Analyst"
                    ),
                    .paragraph("Super apps like WeChat in China have already demonstrated the power of consolidating multiple services. Western markets are now seeing similar evolution, with ride-sharing apps offering banking, e-commerce platforms providing credit, and social media enabling peer-to-peer payments.")
                ]
            ),
            ArticleSection(
                title: "Navigating the New Financial Frontier",
                icon: "map.fill",
                content: [
                    .paragraph("The transformation of finance through technology is not a distant future speculation—it's happening now. From the way we pay for groceries to how global corporations manage treasury operations, every aspect of our financial lives is being reimagined."),
                    .callout(
                        icon: "exclamationmark.triangle.fill",
                        text: "Investors should remain vigilant. While opportunities abound, the regulatory landscape is still evolving, and not all innovations will survive.",
                        style: .warning
                    ),
                    .paragraph("The journey ahead promises to be transformative—reshaping not just how we manage money, but how we think about value, ownership, and economic participation in an increasingly connected world.")
                ]
            )
        ],
        statistics: [
            ArticleStatistic(value: "$180B", label: "Total Value Locked", trend: .up, trendValue: "340%"),
            ArticleStatistic(value: "4.2M", label: "Daily Active Users", trend: .up, trendValue: "127%"),
            ArticleStatistic(value: "2,400+", label: "DeFi Protocols", trend: .up, trendValue: "89%")
        ],
        comments: [
            ArticleComment(
                authorName: "Alex Johnson",
                authorAvatar: nil,
                content: "Excellent breakdown of the current DeFi landscape! The data on portfolio fragility suggests wealth creation through early adoption needs more critical analysis.",
                postedAt: Calendar.current.date(byAdding: .hour, value: -5, to: Date())!,
                likeCount: 47,
                replyCount: 8,
                isVerified: false
            ),
            ArticleComment(
                authorName: "Maya Patel",
                authorAvatar: nil,
                content: "As a traditional banker transitioning to fintech, this article perfectly captures the challenges and opportunities we face. The embedded finance section was particularly insightful.",
                postedAt: Calendar.current.date(byAdding: .hour, value: -12, to: Date())!,
                likeCount: 32,
                replyCount: 3,
                isVerified: true
            )
        ],
        relatedArticles: [
            RelatedArticle(
                title: "The FTX Collapse",
                subtitle: "What the failure of crypto's top exchange tells us about the future.",
                category: .valueTraps,
                readTimeMinutes: 14,
                viewCount: "2.8M",
                gradientColors: ["DC2626", "991B1B"]
            ),
            RelatedArticle(
                title: "How Amazon Built Its Moat",
                subtitle: "The strategy behind unstoppable dominance.",
                category: .blueprints,
                readTimeMinutes: 12,
                viewCount: "3.1M",
                gradientColors: ["059669", "047857"]
            ),
            RelatedArticle(
                title: "How AI Is Revolutionizing Stock Market Analysis",
                subtitle: "From pattern recognition to predictive analytics.",
                category: .blueprints,
                readTimeMinutes: 16,
                viewCount: "1.9M",
                gradientColors: ["7C3AED", "5B21B6"]
            )
        ]
    )
    
    /// Featured version of the digital finance article with orange gradient for hero card
    static let featuredDigitalFinance = MoneyMoveArticle(
        title: "The Future of Digital Finance",
        subtitle: "Exploring the intersection of fintech innovation, cryptocurrency adoption, and traditional banking transformation.",
        category: .blueprints,
        author: ArticleAuthor(
            name: "The Alpha",
            avatarName: nil,
            title: "Investment Research",
            isVerified: true,
            followerCount: "45.2k"
        ),
        publishedAt: Calendar.current.date(byAdding: .day, value: -3, to: Date())!,
        readTimeMinutes: 18,
        viewCount: "4.2M",
        commentCount: 124,
        isBookmarked: false,
        hasAudioVersion: true,
        heroGradientColors: ["EA580C", "C2410C", "9A3412"], // Orange gradient for featured articles
        tagLabel: "MUST READ",
        isFeatured: true, // Mark as featured to show "FEATURED DEEP DIVE" instead of category
        keyHighlights: [
            ArticleHighlight(
                icon: "building.columns.fill",
                title: "The Alpha",
                description: "As technology becomes ubiquitous, decentralized finance (DeFi) is reshaping how we invest."
            ),
            ArticleHighlight(
                icon: "chart.line.uptrend.xyaxis",
                title: "Key Trends",
                description: "The pace of banking innovation has never been faster. This conversation isn't about tomorrow's shift."
            ),
            ArticleHighlight(
                icon: "shield.checkered",
                title: "Risk Factors",
                description: "Despite strong prospects, regulatory challenges and market volatility remain key concerns."
            )
        ],
        sections: [
            ArticleSection(
                title: "The Rise of Decentralized Finance",
                icon: "chart.bar.fill",
                content: [
                    .paragraph("Decentralized finance, or DeFi, represents a fundamental shift in how financial services are delivered. By removing intermediaries through blockchain technology and smart contracts, DeFi platforms are offering users direct access to financial instruments that were once the exclusive domain of institutions."),
                    .paragraph("The implications are profound. Users can now access loans, trade assets, and earn yields on their digital assets without needing to seek permission or provide extensive documentation."),
                    .callout(
                        icon: "lightbulb.fill",
                        text: "DeFi protocols have processed over $180B in total value locked, representing a 340% increase from last year.",
                        style: .highlight
                    ),
                    .bulletList([
                        "Permissionless lending and borrowing",
                        "Automated market makers (AMMs)",
                        "Yield farming and liquidity mining",
                        "Cross-chain interoperability"
                    ])
                ],
                hasGlowEffect: true
            ),
            ArticleSection(
                title: "Artificial Intelligence in Banking",
                icon: "cpu.fill",
                content: [
                    .paragraph("Traditional banks are not standing still. The integration of artificial intelligence and machine learning has revolutionized everything from fraud detection to customer service. AI-powered chatbots now handle over 70% of customer inquiries, while advanced algorithms identify suspicious transactions in milliseconds."),
                    .subheading("Enhanced Security"),
                    .paragraph("Biometric authentication, combined with behavioral analysis, has reduced fraud rates by 45% across major financial institutions. Banks are investing heavily in zero-trust security architectures."),
                    .subheading("Personalized Experiences"),
                    .paragraph("Machine learning models analyze spending patterns to provide personalized financial advice, automatically categorize transactions, and predict future expenses with remarkable accuracy.")
                ]
            ),
            ArticleSection(
                title: "Embedded Finance and Super Apps",
                icon: "apps.iphone",
                content: [
                    .paragraph("The boundaries between financial services and other digital experiences are dissolving. Embedded finance allows non-financial companies to offer banking, lending, and payment services seamlessly within their platforms."),
                    .quote(
                        text: "The future of finance isn't about going to the bank—it's about banking coming to you, wherever you are.",
                        attribution: "Industry Analyst"
                    ),
                    .paragraph("Super apps like WeChat in China have already demonstrated the power of consolidating multiple services. Western markets are now seeing similar evolution, with ride-sharing apps offering banking, e-commerce platforms providing credit, and social media enabling peer-to-peer payments.")
                ]
            ),
            ArticleSection(
                title: "Navigating the New Financial Frontier",
                icon: "map.fill",
                content: [
                    .paragraph("The transformation of finance through technology is not a distant future speculation—it's happening now. From the way we pay for groceries to how global corporations manage treasury operations, every aspect of our financial lives is being reimagined."),
                    .callout(
                        icon: "exclamationmark.triangle.fill",
                        text: "Investors should remain vigilant. While opportunities abound, the regulatory landscape is still evolving, and not all innovations will survive.",
                        style: .warning
                    ),
                    .paragraph("The journey ahead promises to be transformative—reshaping not just how we manage money, but how we think about value, ownership, and economic participation in an increasingly connected world.")
                ]
            )
        ],
        statistics: [
            ArticleStatistic(value: "$180B", label: "Total Value Locked", trend: .up, trendValue: "340%"),
            ArticleStatistic(value: "4.2M", label: "Daily Active Users", trend: .up, trendValue: "127%"),
            ArticleStatistic(value: "2,400+", label: "DeFi Protocols", trend: .up, trendValue: "89%")
        ],
        comments: [
            ArticleComment(
                authorName: "Alex Johnson",
                authorAvatar: nil,
                content: "Excellent breakdown of the current DeFi landscape! The data on portfolio fragility suggests wealth creation through early adoption needs more critical analysis.",
                postedAt: Calendar.current.date(byAdding: .hour, value: -5, to: Date())!,
                likeCount: 47,
                replyCount: 8,
                isVerified: false
            ),
            ArticleComment(
                authorName: "Maya Patel",
                authorAvatar: nil,
                content: "As a traditional banker transitioning to fintech, this article perfectly captures the challenges and opportunities we face. The embedded finance section was particularly insightful.",
                postedAt: Calendar.current.date(byAdding: .hour, value: -12, to: Date())!,
                likeCount: 32,
                replyCount: 3,
                isVerified: true
            )
        ],
        relatedArticles: [
            RelatedArticle(
                title: "The FTX Collapse",
                subtitle: "What the failure of crypto's top exchange tells us about the future.",
                category: .valueTraps,
                readTimeMinutes: 14,
                viewCount: "2.8M",
                gradientColors: ["DC2626", "991B1B"]
            ),
            RelatedArticle(
                title: "How Amazon Built Its Moat",
                subtitle: "The strategy behind unstoppable dominance.",
                category: .blueprints,
                readTimeMinutes: 12,
                viewCount: "3.1M",
                gradientColors: ["059669", "047857"]
            ),
            RelatedArticle(
                title: "How AI Is Revolutionizing Stock Market Analysis",
                subtitle: "From pattern recognition to predictive analytics.",
                category: .blueprints,
                readTimeMinutes: 16,
                viewCount: "1.9M",
                gradientColors: ["7C3AED", "5B21B6"]
            )
        ]
    )
}
