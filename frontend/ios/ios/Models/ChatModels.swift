//
//  ChatModels.swift
//  ios
//
//  Data models for the Chat tab in Learn/Wiser section
//

import Foundation
import SwiftUI

// MARK: - Suggestion Chip Type
enum SuggestionChipType {
    case question
    case hashtag
    case ticker

    var textColor: Color {
        switch self {
        case .question: return AppColors.primaryBlue
        case .hashtag: return AppColors.neutral
        case .ticker: return AppColors.bullish
        }
    }

    var backgroundColor: Color {
        switch self {
        case .question: return AppColors.primaryBlue.opacity(0.15)
        case .hashtag: return AppColors.neutral.opacity(0.15)
        case .ticker: return AppColors.bullish.opacity(0.15)
        }
    }

    var borderColor: Color {
        switch self {
        case .question: return AppColors.primaryBlue.opacity(0.3)
        case .hashtag: return AppColors.neutral.opacity(0.3)
        case .ticker: return AppColors.bullish.opacity(0.3)
        }
    }
}

// MARK: - Suggestion Chip
struct SuggestionChip: Identifiable {
    let id = UUID()
    let text: String
    let type: SuggestionChipType

    static func inferType(from text: String) -> SuggestionChipType {
        if text.contains("#") && text.contains("?") {
            return .question
        } else if text.hasPrefix("#") {
            return .hashtag
        } else if text.contains("?") {
            return .question
        } else if text.contains("$") || text.uppercased() == text {
            return .ticker
        }
        return .question
    }
}

// MARK: - Chat Message
struct ChatMessage: Identifiable {
    let id = UUID()
    let content: String
    let isFromUser: Bool
    let timestamp: Date
    let citations: [ChatCitation]?

    init(content: String, isFromUser: Bool, timestamp: Date = Date(), citations: [ChatCitation]? = nil) {
        self.content = content
        self.isFromUser = isFromUser
        self.timestamp = timestamp
        self.citations = citations
    }
}

// MARK: - Chat Citation
struct ChatCitation: Identifiable {
    let id = UUID()
    let source: String
    let title: String
    let url: String?
}

// MARK: - Chat Session
struct ChatSession: Identifiable {
    let id = UUID()
    let title: String
    let previewMessage: String
    let lastMessageAt: Date
    let messageCount: Int

    var timeAgo: String {
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: lastMessageAt, relativeTo: Date())
    }
}

// MARK: - Chat Input Attachment Type
enum ChatAttachmentType: String, CaseIterable {
    case voice = "Voice"
    case image = "Image"

    var iconName: String {
        switch self {
        case .voice: return "mic.fill"
        case .image: return "photo.fill"
        }
    }
}

// MARK: - Chat History Item Type
enum ChatHistoryItemType: String, CaseIterable {
    case book = "BOOK"
    case concept = "CONCEPT"
    case stock = "STOCK"
    case normal = "NORMAL"
    case journey = "JOURNEY"
    case report = "REPORT"

    var displayName: String {
        rawValue
    }

    var textColor: Color {
        switch self {
        case .book: return Color(hex: "22C55E") // Green
        case .concept: return Color(hex: "3B82F6") // Blue
        case .stock: return Color(hex: "22C55E") // Green
        case .normal: return AppColors.textSecondary
        case .journey: return Color(hex: "F59E0B") // Amber
        case .report: return Color(hex: "EF4444") // Red
        }
    }
}

// MARK: - Chat History Item
struct ChatHistoryItem: Identifiable {
    let id = UUID()
    let type: ChatHistoryItemType
    let title: String
    let preview: String
    let timestamp: Date
    let isSaved: Bool

    var timeAgo: String {
        let calendar = Calendar.current
        let now = Date()

        if calendar.isDateInToday(timestamp) {
            let formatter = RelativeDateTimeFormatter()
            formatter.unitsStyle = .abbreviated
            return formatter.localizedString(for: timestamp, relativeTo: now)
        } else if calendar.isDateInYesterday(timestamp) {
            return "1d ago"
        } else {
            let formatter = DateFormatter()
            formatter.dateFormat = "MM/dd/yyyy"
            return formatter.string(from: timestamp)
        }
    }
}

// MARK: - Chat History Section
enum ChatHistorySection: String, CaseIterable {
    case today = "TODAY"
    case yesterday = "YESTERDAY"
    case older = "OLDER"
}

// MARK: - Chat History Grouped
struct ChatHistoryGroup: Identifiable {
    let id = UUID()
    let section: ChatHistorySection
    let items: [ChatHistoryItem]
}

// MARK: - Sample Data
extension SuggestionChip {
    static let sampleData: [SuggestionChip] = [
        SuggestionChip(text: "Should I buy #AAPL?", type: .question),
        SuggestionChip(text: "#Tech Stocks", type: .hashtag),
        SuggestionChip(text: "#Crypto", type: .hashtag),
        SuggestionChip(text: "What does this chart mean?", type: .question),
        SuggestionChip(text: "Why #TSLA moved?", type: .ticker)
    ]
}

extension ChatSession {
    static let sampleData: [ChatSession] = [
        ChatSession(
            title: "Apple Stock Analysis",
            previewMessage: "Based on the current P/E ratio and market conditions...",
            lastMessageAt: Calendar.current.date(byAdding: .hour, value: -2, to: Date())!,
            messageCount: 5
        ),
        ChatSession(
            title: "Understanding P/E Ratios",
            previewMessage: "The P/E ratio is calculated by dividing...",
            lastMessageAt: Calendar.current.date(byAdding: .day, value: -1, to: Date())!,
            messageCount: 8
        ),
        ChatSession(
            title: "Crypto Market Overview",
            previewMessage: "The cryptocurrency market has been volatile...",
            lastMessageAt: Calendar.current.date(byAdding: .day, value: -3, to: Date())!,
            messageCount: 12
        )
    ]
}

extension ChatHistoryItem {
    // Today items
    static let todayItems: [ChatHistoryItem] = [
        ChatHistoryItem(
            type: .book,
            title: "The Psychology of Money",
            preview: "Discussing key insights from Morgan Housel's book about wealth, greed, and...",
            timestamp: Calendar.current.date(byAdding: .hour, value: -2, to: Date())!,
            isSaved: false
        ),
        ChatHistoryItem(
            type: .concept,
            title: "Compound Interest Explained",
            preview: "Understanding the power of compound interest and how it can exponentially gro...",
            timestamp: Calendar.current.date(byAdding: .hour, value: -4, to: Date())!,
            isSaved: false
        ),
        ChatHistoryItem(
            type: .stock,
            title: "AAPL Stock Analysis",
            preview: "Current price: $182.45 (+2.3%) • Market cap: $2.85T • P/E ratio: 29.4 • Analyzin...",
            timestamp: Calendar.current.date(byAdding: .hour, value: -5, to: Date())!,
            isSaved: false
        )
    ]

    // Yesterday items
    static let yesterdayItems: [ChatHistoryItem] = [
        ChatHistoryItem(
            type: .normal,
            title: "What is a report?",
            preview: "Comprehensive analysis of market trends, sector performance, and economic...",
            timestamp: Calendar.current.date(byAdding: .day, value: -1, to: Date())!,
            isSaved: false
        ),
        ChatHistoryItem(
            type: .journey,
            title: "My Investment Journey",
            preview: "Planning a 10-year investment strategy from beginner to advanced portfolio...",
            timestamp: Calendar.current.date(byAdding: .day, value: -1, to: Date())!,
            isSaved: false
        ),
        ChatHistoryItem(
            type: .normal,
            title: "What is Rich Dad Poor Dad book?",
            preview: "Exploring Robert Kiyosaki's principles on financial education and building wealth...",
            timestamp: Calendar.current.date(byAdding: .day, value: -1, to: Date())!,
            isSaved: false
        ),
        ChatHistoryItem(
            type: .normal,
            title: "TSLA Review",
            preview: "Current price: $248.92 (-1.2%) • Market cap: $789B • Analyzing Tesla's recent...",
            timestamp: Calendar.current.date(byAdding: .day, value: -1, to: Date())!,
            isSaved: false
        )
    ]

    // Older items
    static let olderItems: [ChatHistoryItem] = [
        ChatHistoryItem(
            type: .stock,
            title: "AMZN E-commerce Dominance",
            preview: "Current price: $151.23 (+0.9%) • AWS cloud services and retail expansion...",
            timestamp: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 20))!,
            isSaved: false
        ),
        ChatHistoryItem(
            type: .report,
            title: "Global Economic Outlook",
            preview: "Comprehensive analysis of GDP growth, inflation trends, and monetary policy...",
            timestamp: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 18))!,
            isSaved: true
        ),
        ChatHistoryItem(
            type: .book,
            title: "The Millionaire Next Door",
            preview: "Research-based insights on wealth accumulation habits and the frugal...",
            timestamp: Calendar.current.date(from: DateComponents(year: 2025, month: 12, day: 18))!,
            isSaved: false
        ),
        ChatHistoryItem(
            type: .journey,
            title: "Debt Elimination Strategy",
            preview: "Systematic approach to becoming debt-free using snowball and avalanche...",
            timestamp: Calendar.current.date(from: DateComponents(year: 2025, month: 11, day: 25))!,
            isSaved: true
        ),
        ChatHistoryItem(
            type: .normal,
            title: "Risk-Adjusted Returns",
            preview: "Understanding Sharpe ratio, beta, and alpha for evaluating investment...",
            timestamp: Calendar.current.date(from: DateComponents(year: 2025, month: 11, day: 23))!,
            isSaved: false
        )
    ]

    static let sampleGroups: [ChatHistoryGroup] = [
        ChatHistoryGroup(section: .today, items: todayItems),
        ChatHistoryGroup(section: .yesterday, items: yesterdayItems),
        ChatHistoryGroup(section: .older, items: olderItems)
    ]
}
