//
//  SavedModels.swift
//  ios
//
//  Data models for the Saved tab in Wiser section
//

import Foundation
import SwiftUI

// MARK: - Saved Item Type
enum SavedItemType: String, CaseIterable {
    case book = "Book"
    case concept = "Concept"
    case chat = "Chat"
    case report = "Report"

    var displayName: String {
        rawValue.uppercased()
    }

    var textColor: Color {
        switch self {
        case .book: return Color(hex: "3B82F6")      // Blue
        case .concept: return Color(hex: "A855F7")   // Purple
        case .chat: return Color(hex: "06B6D4")      // Cyan
        case .report: return Color(hex: "22C55E")    // Green
        }
    }

    var iconName: String {
        switch self {
        case .book: return "book.fill"
        case .concept: return "lightbulb.fill"
        case .chat: return "bubble.left.fill"
        case .report: return "doc.text.fill"
        }
    }

    var iconBackgroundColor: Color {
        switch self {
        case .book: return Color(hex: "3B82F6").opacity(0.15)
        case .concept: return Color(hex: "A855F7").opacity(0.15)
        case .chat: return Color(hex: "06B6D4").opacity(0.15)
        case .report: return Color(hex: "22C55E").opacity(0.15)
        }
    }
}

// MARK: - Saved Filter Type
enum SavedFilterType: String, CaseIterable {
    case all = "All"
    case books = "Books"
    case concepts = "Concepts"
    case reports = "Reports"

    var associatedItemType: SavedItemType? {
        switch self {
        case .all: return nil
        case .books: return .book
        case .concepts: return .concept
        case .reports: return .report
        }
    }
}

// MARK: - Saved Item
struct SavedItem: Identifiable {
    let id = UUID()
    let type: SavedItemType
    let title: String
    let description: String
    let savedAt: Date
    let progress: SavedItemProgress?
    let messageCount: Int?
    let level: String?

    var timeAgo: String {
        let calendar = Calendar.current
        let now = Date()
        let components = calendar.dateComponents([.day, .hour, .minute], from: savedAt, to: now)

        if let days = components.day, days > 0 {
            if days == 1 {
                return "Yesterday"
            } else if days < 7 {
                return "\(days) days ago"
            } else if days < 14 {
                return "1 week ago"
            } else {
                return "\(days / 7) weeks ago"
            }
        } else if let hours = components.hour, hours > 0 {
            return "\(hours)h ago"
        } else if let minutes = components.minute, minutes > 0 {
            return "\(minutes)m ago"
        }
        return "Just now"
    }

    var formattedMessages: String? {
        guard let count = messageCount else { return nil }
        return "\(count) messages"
    }

    var actionButtonTitle: String {
        switch type {
        case .book: return "Continue"
        case .concept: return "Review"
        case .chat: return "Continue"
        case .report: return "View"
        }
    }
}

// MARK: - Saved Item Progress
struct SavedItemProgress {
    let currentChapter: Int?
    let totalChapters: Int?

    var formattedChapter: String? {
        guard let current = currentChapter else { return nil }
        return "Chapter \(current)"
    }
}

// MARK: - Storage Info
struct StorageInfo {
    let usedGB: Double
    let totalGB: Double

    var usedPercentage: Int {
        guard totalGB > 0 else { return 0 }
        return Int((usedGB / totalGB) * 100)
    }

    var progress: Double {
        guard totalGB > 0 else { return 0 }
        return usedGB / totalGB
    }

    var formattedUsed: String {
        String(format: "%.1f GB of %.1f GB used", usedGB, totalGB)
    }

    var formattedPercentage: String {
        "\(usedPercentage)% used"
    }
}

// MARK: - Sample Data
extension SavedItem {
    static let sampleData: [SavedItem] = [
        SavedItem(
            type: .book,
            title: "The Intelligent Investor",
            description: "The Bible of Value Investing. Warren Buffett's #1 recommended book.",
            savedAt: Calendar.current.date(byAdding: .day, value: -2, to: Date())!,
            progress: SavedItemProgress(currentChapter: 7, totalChapters: 20),
            messageCount: nil,
            level: nil
        ),
        SavedItem(
            type: .concept,
            title: "Understanding Moats",
            description: "Understanding qubits, superposition, and quantum entanglement in modern...",
            savedAt: Calendar.current.date(byAdding: .weekOfYear, value: -1, to: Date())!,
            progress: nil,
            messageCount: nil,
            level: "Advanced"
        ),
        SavedItem(
            type: .chat,
            title: "Blockchain ArchitectureDiscussion",
            description: "Detailed conversation about decentralized systems, consensus...",
            savedAt: Calendar.current.date(byAdding: .day, value: -1, to: Date())!,
            progress: nil,
            messageCount: 47,
            level: nil
        ),
        SavedItem(
            type: .chat,
            title: "Microsoft: The AI Moat Deepens",
            description: "Chat with AI about Microsoft report.\nWhat is Azure's AI services and UX Pilot AI? Q4 cloud growth of 28% YoY signals strong market demand.",
            savedAt: Calendar.current.date(byAdding: .day, value: -1, to: Date())!,
            progress: nil,
            messageCount: 4,
            level: nil
        )
    ]
}

extension StorageInfo {
    static let sampleData = StorageInfo(usedGB: 3.2, totalGB: 4.4)
}
