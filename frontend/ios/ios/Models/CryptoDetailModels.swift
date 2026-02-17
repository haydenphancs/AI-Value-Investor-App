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
    let paragraphs: [String]
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
    let performancePeriods: [PerformancePeriod]
    let snapshots: [CryptoSnapshotItem]
    let cryptoProfile: CryptoProfile
    let relatedCryptos: [RelatedTicker]
    let benchmarkSummary: PerformanceBenchmarkSummary?

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
    static let sampleEthereum = CryptoDetailData(
        symbol: "ETH",
        name: "Ethereum",
        currentPrice: 3456.78,
        priceChange: 78.32,
        priceChangePercent: 2.32,
        marketStatus: .trading,
        chartData: [3120, 3180, 3250, 3210, 3320, 3280, 3350, 3410, 3380, 3420, 3390, 3456],
        keyStatistics: CryptoKeyStatistic.sampleETH,
        keyStatisticsGroups: CryptoKeyStatisticsGroup.sampleETH,
        performancePeriods: CryptoPerformance.sampleETH,
        snapshots: CryptoSnapshotItem.sampleETH,
        cryptoProfile: CryptoProfile(
            description: "Ethereum is the world's largest programmable blockchain and the birthplace of smart contracts, DeFi, and NFTs. Launched in 2015 by Vitalik Buterin and a team of co-founders, Ethereum allows developers to build decentralized applications that run exactly as programmed without any possibility of censorship, downtime, or third-party interference. After its historic shift to Proof of Stake in September 2022 known as 'The Merge,' Ethereum cut its energy consumption by over 99% and introduced a deflationary supply mechanism that burns ETH with every transaction.",
            symbol: "ETH",
            launchDate: "July 30, 2015",
            consensusMechanism: "Proof of Stake (PoS)",
            blockchain: "Ethereum",
            website: "ethereum.org",
            whitepaper: "ethereum.org/en/whitepaper"
        ),
        relatedCryptos: CryptoRelatedTicker.sampleData,
        benchmarkSummary: PerformanceBenchmarkSummary(
            avgAnnualReturn: 52.3,
            spBenchmark: 68.4,
            benchmarkName: "Bitcoin (BTC) Benchmark"
        )
    )
}

// MARK: - Crypto Performance Sample Data
enum CryptoPerformance {
    static let sampleETH: [PerformancePeriod] = [
        PerformancePeriod(label: "7 Days", changePercent: 5.42, vsMarketPercent: 3.18, benchmarkLabel: "BTC"),
        PerformancePeriod(label: "1 Month", changePercent: 12.34, vsMarketPercent: 8.71, benchmarkLabel: "BTC"),
        PerformancePeriod(label: "YTD", changePercent: 45.12, vsMarketPercent: 32.50, benchmarkLabel: "BTC"),
        PerformancePeriod(label: "1 Year", changePercent: 62.89, vsMarketPercent: 85.24, benchmarkLabel: "BTC"),
        PerformancePeriod(label: "3 Years", changePercent: 148.60, vsMarketPercent: 210.35, benchmarkLabel: "BTC"),
        PerformancePeriod(label: "Max", changePercent: 8542.30, vsMarketPercent: 12450.00, benchmarkLabel: "BTC")
    ]
}

// MARK: - Crypto Key Statistics Sample Data (FMP-based)
enum CryptoKeyStatistic {
    static let sampleETH: [KeyStatistic] = [
        KeyStatistic(label: "Market Cap", value: "$415.8B"),
        KeyStatistic(label: "24h Volume", value: "$18.2B"),
        KeyStatistic(label: "Circulating Supply", value: "120.27M ETH"),
        KeyStatistic(label: "Max Supply", value: "No Cap"),
        KeyStatistic(label: "24h High", value: "$3,512.40"),
        KeyStatistic(label: "24h Low", value: "$3,378.15"),
        KeyStatistic(label: "All-Time High", value: "$4,891.70"),
        KeyStatistic(label: "All-Time Low", value: "$0.42"),
        KeyStatistic(label: "Market Dominance", value: "17.8%", isHighlighted: true),
        KeyStatistic(label: "Volume/Market Cap", value: "4.38%"),
        KeyStatistic(label: "Total Supply", value: "120.27M ETH"),
        KeyStatistic(label: "Fully Diluted Val.", value: "$415.8B")
    ]
}

// MARK: - Crypto Key Statistics Groups (FMP-based)
enum CryptoKeyStatisticsGroup {
    static let sampleETH: [KeyStatisticsGroup] = [
        // Column 1: Price & Volume
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Market Cap", value: "$415.8B"),
            KeyStatistic(label: "24h Volume", value: "$18.2B"),
            KeyStatistic(label: "Volume/Market Cap", value: "4.38%"),
            KeyStatistic(label: "24h High", value: "$3,512.40"),
            KeyStatistic(label: "24h Low", value: "$3,378.15")
        ]),
        // Column 2: Supply
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "Circulating Supply", value: "120.27M ETH"),
            KeyStatistic(label: "Total Supply", value: "120.27M ETH"),
            KeyStatistic(label: "Max Supply", value: "No Cap"),
            KeyStatistic(label: "Fully Diluted Val.", value: "$415.8B"),
            KeyStatistic(label: "Market Dominance", value: "17.8%", isHighlighted: true)
        ]),
        // Column 3: Historical
        KeyStatisticsGroup(statistics: [
            KeyStatistic(label: "All-Time High", value: "$4,891.70"),
            KeyStatistic(label: "ATH Date", value: "11/16/2021"),
            KeyStatistic(label: "ATH Change", value: "-29.3%"),
            KeyStatistic(label: "All-Time Low", value: "$0.42"),
            KeyStatistic(label: "ATL Date", value: "10/21/2015")
        ])
    ]
}

// MARK: - Crypto Snapshot Sample Data (ETH - Storytelling Style)
extension CryptoSnapshotItem {
    static let sampleETH: [CryptoSnapshotItem] = [
        // MARK: Origin and Technology
        CryptoSnapshotItem(
            category: .originAndTechnology,
            paragraphs: [
                "Ethereum is the world's largest Layer 1 smart contract platform. Bitcoin is digital money. Ethereum is digital everything — DeFi, NFTs, gaming, and thousands of apps with no company in control.",

                "Built by Vitalik Buterin, who wrote the whitepaper at just 19. His co-founders include Gavin Wood (invented Solidity, later created Polkadot) and Joseph Lubin (founded ConsenSys). Not amateurs — visionaries.",

                "The secret weapon is the EVM — the industry standard every other chain copies. After The Merge in 2022, Ethereum switched to Proof of Stake, cutting energy use by 99%. Over 900,000 validators now secure the network, making it the most economically fortified blockchain on earth."
            ]
        ),

        // MARK: Tokenomics
        CryptoSnapshotItem(
            category: .tokenomics,
            paragraphs: [
                "Ethereum collects tolls. Every swap on Uniswap, every NFT mint, every DeFi loan pays a gas fee in ETH. In busy periods, that is tens of millions per day in real revenue from real users.",

                "Since EIP-1559, most of those fees get burned permanently. When activity is high, more ETH is destroyed than created — making it deflationary. Over 4 million ETH burned since The Merge. Think automatic stock buybacks, every single transaction.",

                "No hard supply cap like Bitcoin, but it does not need one. The burn adjusts dynamically. Around 120 million ETH in circulation, net issuance near zero, and 28% is locked in staking. Supply is tight."
            ]
        ),

        // MARK: Next Big Moves
        CryptoSnapshotItem(
            category: .nextBigMoves,
            paragraphs: [
                "Danksharding is the big one. Phase one (EIP-4844) already slashed Layer 2 costs by 100x. The next phase aims to push throughput to tens of thousands of transactions per second at near-zero cost.",

                "The investor list reads like a who's who: a16z, Paradigm, Sequoia, and BlackRock — which launched a spot Ethereum ETF and built its own tokenized fund (BUIDL) on Ethereum. When the world's largest asset manager builds on your chain, that is a signal.",

                "ETF inflows are still early but growing steadily. As advisors and retirement funds add ETH exposure, demand could shift fast — especially with so much supply already locked in staking."
            ]
        ),

        // MARK: Risks
        CryptoSnapshotItem(
            category: .risks,
            paragraphs: [
                "The kill scenario is regulatory. The SEC has been ambiguous on whether ETH is a security. ETF approval suggests commodity status, but a future crackdown on staking or DeFi could change everything overnight.",

                "Ethereum itself has never been hacked, but the 2016 DAO exploit caused a hard fork that split the community. DeFi hacks have cost billions — app-level bugs, not protocol flaws. Centralization risk is growing too, with large staking providers like Lido gaining outsized control.",

                "Competition is real. Solana is faster and cheaper. New chains keep launching. Ethereum's network effects are massive, but if Layer 2 fragmentation or a rival chain captures the next wave of users, dominance could erode."
            ]
        )
    ]
}

// MARK: - Related Crypto Sample Data
enum CryptoRelatedTicker {
    static let sampleData: [RelatedTicker] = [
        RelatedTicker(symbol: "BTC", name: "Bitcoin", price: 97542.18, changePercent: 1.9),
        RelatedTicker(symbol: "SOL", name: "Solana", price: 187.42, changePercent: 4.1),
        RelatedTicker(symbol: "BNB", name: "BNB", price: 612.34, changePercent: -0.8),
        RelatedTicker(symbol: "MATIC", name: "Polygon", price: 0.74, changePercent: 3.2),
        RelatedTicker(symbol: "ARB", name: "Arbitrum", price: 1.42, changePercent: 5.1),
        RelatedTicker(symbol: "OP", name: "Optimism", price: 2.87, changePercent: 2.8)
    ]
}
