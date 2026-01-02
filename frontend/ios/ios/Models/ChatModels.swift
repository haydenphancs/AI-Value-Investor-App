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
