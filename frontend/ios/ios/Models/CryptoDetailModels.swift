//
//  CryptoDetailModels.swift
//  ios
//
//  Data models for the Crypto Detail screen
//

import Foundation
import SwiftUI

// MARK: - Crypto Detail Tab
enum CryptoDetailTab: String, CaseIterable {
    case overview = "Overview"
    case news = "News"
    case analysis = "Analysis"
}

// MARK: - Crypto Market Status
enum CryptoMarketStatus {
    case trading
    case maintenance(resumeTime: String)

    var displayText: String {
        switch self {
        case .trading:
            return "24/7 Trading"
        case .maintenance(let resumeTime):
            return "Maintenance - Resumes \(resumeTime)"
        }
    }
}

// MARK: - Crypto Key Statistic (reuses KeyStatistic from TickerDetailModels)
// KeyStatistic and KeyStatisticsGroup are shared

// MARK: - Crypto Snapshot Category
enum CryptoSnapshotCategory: String, CaseIterable {
    case originAndTechnology = "Origin and Technology"
    case tokenomics = "Tokenomics"
    case nextBigMoves = "Next Big Moves"
    case risks = "Risks"

    var iconName: String {
        switch self {
        case .originAndTechnology: return "cpu.fill"
        case .tokenomics: return "chart.pie.fill"
        case .nextBigMoves: return "arrow.up.forward.circle.fill"
        case .risks: return "exclamationmark.triangle.fill"
        }
    }

    var iconColor: Color {
        switch self {
        case .originAndTechnology: return AppColors.primaryBlue
        case .tokenomics: return AppColors.accentCyan
        case .nextBigMoves: return AppColors.bullish
        case .risks: return AppColors.neutral
        }
    }
}

// MARK: - Crypto Snapshot Item
struct CryptoSnapshotItem: Identifiable {
    let id = UUID()
    let category: CryptoSnapshotCategory
    let content: String // Placeholder for future content
}

// MARK: - Crypto Profile
struct CryptoProfile {
    let description: String
    let symbol: String
    let launchDate: String
    let consensusMechanism: String
    let blockchain: String
    let website: String
    let whitepaper: String?

    var formattedLaunchDate: String {
        launchDate
    }
}

// MARK: - Crypto Detail Data
struct CryptoDetailData: Identifiable {
    let id = UUID()
    let symbol: String
    let name: String
    let currentPrice: Double
    let priceChange: Double
    let priceChangePercent: Double
    let marketStatus: CryptoMarketStatus
    let chartData: [Double]
    let keyStatistics: [KeyStatistic]
    let keyStatisticsGroups: [KeyStatisticsGroup]
    let snapshots: [CryptoSnapshotItem]
    let cryptoProfile: CryptoProfile
    let relatedCryptos: [RelatedTicker]

    var isPositive: Bool {
        priceChange >= 0
    }

    var formattedPrice: String {
        if currentPrice >= 1 {
            return String(format: "$%.2f", currentPrice)
        } else {
            return String(format: "$%.6f", currentPrice)
        }
    }

    var formattedChange: String {
        let sign = priceChange >= 0 ? "+" : ""
        if abs(priceChange) >= 1 {
            return "\(sign)\(String(format: "%.2f", priceChange))"
        } else {
            return "\(sign)\(String(format: "%.6f", priceChange))"
        }
    }

    var formattedChangePercent: String {
        let sign = priceChangePercent >= 0 ? "+" : ""
        return "(\(sign)\(String(format: "%.2f", priceChangePercent))%)"
    }
}

// MARK: - Crypto AI Suggestion
struct CryptoAISuggestion: Identifiable {
    let id = UUID()
    let text: String

    static let defaultSuggestions: [CryptoAISuggestion] = [
        CryptoAISuggestion(text: "What is this coin?"),
        CryptoAISuggestion(text: "Tokenomics breakdown"),
        CryptoAISuggestion(text: "Should I buy?"),
        CryptoAISuggestion(text: "What are the risks?")
    ]
}

// MARK: - Sample Data

extension CryptoDetailData {
    static let sampleBitcoin = CryptoDetailData(
        symbol: "BTC",
        name: "Bitcoin",
        currentPrice: 97542.18,
        priceChange: 1832.45,
        priceChangePercent: 1.91,
        marketStatus: .trading,
        chartData: [91200, 92500, 93800, 92100, 94600, 95200, 93700, 96100, 95800, 97200, 96500, 97542],
        keyStatistics: CryptoKeyStatistic.sampleData,
        keyStatisticsGroups: CryptoKeyStatisticsGroup.sampleData,
        snapshots: CryptoSnapshotItem.sampleData,
        cryptoProfile: CryptoProfile(
            description: "Bitcoin is the first decentralized cryptocurrency, created in 2009 by an anonymous entity known as Satoshi Nakamoto. It introduced blockchain technology as a peer-to-peer electronic cash system, enabling trustless transactions without intermediaries. Bitcoin uses a Proof-of-Work consensus mechanism and has a fixed supply cap of 21 million coins, making it a deflationary digital asset often referred to as 'digital gold.'",
            symbol: "BTC",
            launchDate: "January 3, 2009",
            consensusMechanism: "Proof of Work (PoW)",
            blockchain: "Bitcoin",
            website: "bitcoin.org",
            whitepaper: "bitcoin.org/bitcoin.pdf"
        ),
        relatedCryptos: CryptoRelatedTicker.sampleData
    )
}

// MARK: - Crypto Key Statistics Sample Data (FMP-based)
enum CryptoKeyStatistic {
    static let sampleData: [KeyStatistic] = [
        KeyStatistic(label: "Market Cap", value: "$1.92T"),
        KeyStatistic(label: "24h Volume", value: "$38.7B"),
        KeyStatistic(label: "Circulating Supply", value: "19.82M BTC"),
        KeyStatistic(label: "Max Supply", value: "21M BTC"),
        KeyStatistic(label: "24h High", value: "$98,124.32"),
        KeyStatistic(label: "24h Low", value: "$95,201.47"),
        KeyStatistic(label: "All-Time High", value: "$108,268.45"),
        KeyStatistic(label: "All-Time Low", value: "$67.81"),
        KeyStatistic(label: "Market Dominance", value: "54.2%", isHighlighted: true),
        KeyStatistic(label: "Volume/Market Cap", value: "2.01%"),
        KeyStatistic(label: "Total Supply", value: "19.82M BTC"),
        KeyStatistic(label: "Fully Diluted Val.", value: "$2.05T")
    ]
}

// MARK: - Crypto Key Statistics Groups (FMP-based)
enum CryptoKeyStatisticsGroup {
    static let sampleData: [KeyStatisticsGroup] = [
        // Column 1: Price & Volume
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Market Cap", value: "$1.92T"),
            KeyStatistic(label: "24h Volume", value: "$38.7B"),
            KeyStatistic(label: "Volume/Market Cap", value: "2.01%"),
            KeyStatistic(label: "24h High", value: "$98,124.32"),
            KeyStatistic(label: "24h Low", value: "$95,201.47")
        ]),
        // Column 2: Supply
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Circulating Supply", value: "19.82M BTC"),
            KeyStatistic(label: "Total Supply", value: "19.82M BTC"),
            KeyStatistic(label: "Max Supply", value: "21M BTC"),
            KeyStatistic(label: "Fully Diluted Val.", value: "$2.05T"),
            KeyStatistic(label: "Market Dominance", value: "54.2%", isHighlighted: true)
        ]),
        // Column 3: Historical
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "All-Time High", value: "$108,268.45"),
            KeyStatistic(label: "ATH Date", value: "12/17/2024"),
            KeyStatistic(label: "ATH Change", value: "-9.9%"),
            KeyStatistic(label: "All-Time Low", value: "$67.81"),
            KeyStatistic(label: "ATL Date", value: "07/06/2013")
        ])
    ]
}

// MARK: - Crypto Snapshot Sample Data
extension CryptoSnapshotItem {
    static let sampleData: [CryptoSnapshotItem] = [
        CryptoSnapshotItem(
            category: .originAndTechnology,
            content: "" // Content to be added later
        ),
        CryptoSnapshotItem(
            category: .tokenomics,
            content: "" // Content to be added later
        ),
        CryptoSnapshotItem(
            category: .nextBigMoves,
            content: "" // Content to be added later
        ),
        CryptoSnapshotItem(
            category: .risks,
            content: "" // Content to be added later
        )
    ]
}

// MARK: - Related Crypto Sample Data
enum CryptoRelatedTicker {
    static let sampleData: [RelatedTicker] = [
        RelatedTicker(symbol: "ETH", name: "Ethereum", price: 3456.78, changePercent: 2.3),
        RelatedTicker(symbol: "SOL", name: "Solana", price: 187.42, changePercent: 4.1),
        RelatedTicker(symbol: "BNB", name: "BNB", price: 612.34, changePercent: -0.8),
        RelatedTicker(symbol: "XRP", name: "XRP", price: 2.47, changePercent: 1.5),
        RelatedTicker(symbol: "ADA", name: "Cardano", price: 0.89, changePercent: -1.2),
        RelatedTicker(symbol: "AVAX", name: "Avalanche", price: 34.56, changePercent: 3.7)
    ]
}
