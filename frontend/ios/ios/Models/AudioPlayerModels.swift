//
//  AudioPlayerModels.swift
//  ios
//
//  Data models for the Global Audio Player system
//

import Foundation
import SwiftUI

// MARK: - Audio Episode
/// Represents a playable audio episode (Money Moves story, Book chapter, etc.)
struct AudioEpisode: Identifiable, Equatable {
    let id: String
    let title: String
    let subtitle: String
    let artworkGradientColors: [String]
    let artworkIcon: String
    let duration: TimeInterval
    let category: AudioCategory
    let authorName: String
    let sourceId: String // Original article/book ID for navigation

    var formattedDuration: String {
        let minutes = Int(duration) / 60
        let seconds = Int(duration) % 60
        return String(format: "%d:%02d", minutes, seconds)
    }

    var artworkColors: [Color] {
        artworkGradientColors.map { Color(hex: $0) }
    }

    static func == (lhs: AudioEpisode, rhs: AudioEpisode) -> Bool {
        lhs.id == rhs.id
    }
}

// MARK: - Audio Category
enum AudioCategory: String, CaseIterable {
    case moneyMoves = "Money Moves"
    case books = "Books"
    case dailyBrief = "Daily Brief"
    case podcast = "Podcast"

    var icon: String {
        switch self {
        case .moneyMoves: return "chart.line.uptrend.xyaxis"
        case .books: return "book.fill"
        case .dailyBrief: return "newspaper.fill"
        case .podcast: return "mic.fill"
        }
    }

    var accentColor: Color {
        switch self {
        case .moneyMoves: return AppColors.primaryBlue
        case .books: return Color(hex: "A855F7")
        case .dailyBrief: return AppColors.alertOrange
        case .podcast: return AppColors.bullish
        }
    }
}

// MARK: - Playback State
enum PlaybackState: Equatable {
    case idle
    case loading
    case playing
    case paused
    case error(String)

    var isActive: Bool {
        switch self {
        case .playing, .paused, .loading:
            return true
        default:
            return false
        }
    }
}

// MARK: - Playback Speed
enum PlaybackSpeed: Double, CaseIterable, Identifiable {
    case slow = 0.5
    case normal = 1.0
    case faster = 1.25
    case fast = 1.5
    case veryFast = 1.75
    case double = 2.0

    var id: Double { rawValue }

    var label: String {
        switch self {
        case .slow: return "0.5x"
        case .normal: return "1x"
        case .faster: return "1.25x"
        case .fast: return "1.5x"
        case .veryFast: return "1.75x"
        case .double: return "2x"
        }
    }
}

// MARK: - Sleep Timer Option
enum SleepTimerOption: Int, CaseIterable, Identifiable {
    case off = 0
    case fiveMinutes = 5
    case tenMinutes = 10
    case fifteenMinutes = 15
    case thirtyMinutes = 30
    case oneHour = 60
    case endOfEpisode = -1

    var id: Int { rawValue }

    var label: String {
        switch self {
        case .off: return "Off"
        case .fiveMinutes: return "5 minutes"
        case .tenMinutes: return "10 minutes"
        case .fifteenMinutes: return "15 minutes"
        case .thirtyMinutes: return "30 minutes"
        case .oneHour: return "1 hour"
        case .endOfEpisode: return "End of episode"
        }
    }
}

// MARK: - Audio Queue Item
struct AudioQueueItem: Identifiable {
    let id = UUID()
    let episode: AudioEpisode
    var isCurrentlyPlaying: Bool = false
}

// MARK: - Sample Data
extension AudioEpisode {
    static let sampleMoneyMoves = AudioEpisode(
        id: "mm-digital-finance-001",
        title: "The Future of Digital Finance",
        subtitle: "Exploring fintech innovation and banking transformation",
        artworkGradientColors: ["1E3A5F", "0D1B2A", "1B263B"],
        artworkIcon: "chart.line.uptrend.xyaxis",
        duration: 1080, // 18 minutes
        category: .moneyMoves,
        authorName: "The Alpha",
        sourceId: "article-digital-finance-001"
    )

    static let sampleFTX = AudioEpisode(
        id: "mm-ftx-collapse-001",
        title: "The FTX Collapse",
        subtitle: "Crypto's biggest fraud unraveled",
        artworkGradientColors: ["DC2626", "991B1B", "7F1D1D"],
        artworkIcon: "exclamationmark.triangle.fill",
        duration: 1080, // 18 minutes
        category: .moneyMoves,
        authorName: "The Alpha",
        sourceId: "article-ftx-001"
    )

    static let sampleBook = AudioEpisode(
        id: "book-intelligent-investor-ch1",
        title: "The Intelligent Investor - Chapter 1",
        subtitle: "Investment vs. Speculation",
        artworkGradientColors: ["059669", "047857", "064E3B"],
        artworkIcon: "book.fill",
        duration: 2700, // 45 minutes
        category: .books,
        authorName: "Benjamin Graham",
        sourceId: "book-intelligent-investor"
    )

    static let sampleDailyBrief = AudioEpisode(
        id: "daily-brief-jan-30",
        title: "Morning Market Brief",
        subtitle: "January 30, 2026 - Key market movers",
        artworkGradientColors: ["F97316", "EA580C", "C2410C"],
        artworkIcon: "newspaper.fill",
        duration: 420, // 7 minutes
        category: .dailyBrief,
        authorName: "AI Value Investor",
        sourceId: "brief-jan-30-2026"
    )
}
