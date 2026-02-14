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
        relatedCryptos: CryptoRelatedTicker.sampleData
    )
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
                "Ethereum is the world's largest Layer 1 smart contract platform. Think of it as a giant, global computer that anyone can program. While Bitcoin was built to be digital money, Ethereum was built to be digital everything. It powers decentralized finance, NFTs, gaming, identity systems, and thousands of apps that run without any company controlling them.",

                "It was dreamed up by Vitalik Buterin, a Russian-Canadian programmer who was just 19 years old when he published the Ethereum whitepaper in 2013. He was joined by a powerhouse group of co-founders including Gavin Wood, who invented the Solidity programming language and later went on to create Polkadot, and Joseph Lubin, who founded ConsenSys, one of the biggest blockchain companies in the world. This was not a team of amateurs. They were visionaries who saw that blockchain could do far more than just move money around.",

                "What makes Ethereum's technology stand out is the Ethereum Virtual Machine, or EVM. It became the industry standard that almost every other blockchain copies. Developers write smart contracts in Solidity, and those contracts execute exactly as written, every single time, with no middleman. After The Merge in September 2022, Ethereum switched from energy-hungry Proof of Work to Proof of Stake, slashing its energy use by over 99%. The network now processes around 15-30 transactions per second on its base layer, but with Layer 2 rollups like Arbitrum and Optimism built on top, it can handle thousands more. Security is Ethereum's obsession. Over 900,000 validators stake their own ETH to keep the network honest, making it the most economically secured blockchain on earth."
            ]
        ),

        // MARK: Tokenomics
        CryptoSnapshotItem(
            category: .tokenomics,
            paragraphs: [
                "Ethereum makes money the same way a highway collects tolls. Every time someone swaps tokens on Uniswap, mints an NFT, lends on Aave, or does anything on the network, they pay a gas fee in ETH. In busy periods, the network can generate tens of millions of dollars in fees per day. This is not speculative revenue. It is real economic activity from real users doing real things.",

                "Here is where it gets interesting for ETH holders. Since August 2021, a mechanism called EIP-1559 burns the majority of those gas fees permanently. That means every transaction destroys a small amount of ETH forever. When network activity is high enough, more ETH gets burned than is created through staking rewards, making ETH a deflationary asset. Since The Merge, over 4 million ETH have been burned. That is billions of dollars in value removed from circulation, which is similar in concept to a company doing stock buybacks, except it happens automatically with every single transaction.",

                "Unlike Bitcoin, Ethereum has no hard supply cap. But it does not need one. The burn mechanism acts as a dynamic supply controller. When demand is high, supply shrinks. When demand is low, supply grows slightly. Currently around 120 million ETH exists in circulation, and the net issuance rate hovers near zero or even negative. On top of that, roughly 28% of all ETH is staked, locked up by validators earning around 3-4% annual yield. This means a huge chunk of supply is removed from the market, further tightening the available ETH for buyers."
            ]
        ),

        // MARK: Next Big Moves
        CryptoSnapshotItem(
            category: .nextBigMoves,
            paragraphs: [
                "The biggest thing on Ethereum's roadmap is the continued rollout of Danksharding. The first phase, called Proto-Danksharding or EIP-4844, already went live and slashed Layer 2 transaction costs by up to 100x. The next phase will dramatically expand data availability, allowing rollups to process tens of thousands of transactions per second at near-zero cost. This is Ethereum's master plan to become the settlement layer for the entire internet of value without sacrificing decentralization.",

                "Ethereum has attracted the most powerful investors in the world. The Ethereum Foundation oversaw the initial development, but the ecosystem now includes backing from a16z, which has invested billions across Ethereum-based projects, Paradigm, Sequoia Capital, and most notably, BlackRock, the largest asset manager on the planet with over $10 trillion under management. When BlackRock launched its spot Ethereum ETF in mid-2024, it was the clearest signal yet that traditional finance considers Ethereum a legitimate asset class.",

                "These institutions are not investing because they think Ethereum is a cool technology experiment. They are betting that Ethereum will become the backbone of a new financial system. Tokenized real-world assets like Treasury bonds, real estate, and private equity are starting to move on-chain, and Ethereum is where most of that activity is happening. BlackRock's own tokenized fund, BUIDL, runs on Ethereum. When the world's largest asset manager builds on your platform, that is not just an endorsement. That is a signal about where finance is heading.",

                "The spot Ethereum ETFs are still in their early days and inflows have been growing steadily. As financial advisors and retirement funds begin adding ETH exposure to portfolios, the demand side of the equation could shift dramatically. Some analysts estimate that ETF inflows alone could absorb billions in ETH over the coming years, which matters a lot when a significant portion of supply is already locked up in staking."
            ]
        ),

        // MARK: Risks
        CryptoSnapshotItem(
            category: .risks,
            paragraphs: [
                "The biggest existential threat to Ethereum is regulatory. The SEC has historically been ambiguous about whether ETH is a security. While the approval of spot ETFs strongly suggests it is treated as a commodity, a future administration or a global regulatory crackdown could change that overnight. If major governments decided to ban staking, restrict DeFi access, or classify ETH as an unregistered security, the price impact would be severe and the ecosystem could face an exodus of developers and users to more friendly jurisdictions.",

                "On the technical side, Ethereum has never suffered a protocol-level hack, but the ecosystem around it has. The 2016 DAO hack led to a controversial hard fork that split the community. Smart contract exploits across DeFi have cost users billions, though these are application-level bugs rather than flaws in Ethereum itself. The shift to Proof of Stake introduced new risks too. If a few large staking providers like Lido accumulate too much control, centralization concerns grow. There is also the so-called 'complexity risk' since Ethereum's roadmap involves many moving parts, and each upgrade carries the possibility of introducing new bugs into a system securing hundreds of billions of dollars.",

                "Competition is real and growing. Solana offers much faster and cheaper transactions for everyday users. Newer chains keep launching with fresh technology. While Ethereum's network effects and developer ecosystem are massive advantages, nothing in crypto is guaranteed. If Layer 2 solutions fragment liquidity or a competitor chain captures the next wave of mainstream users, Ethereum could find its dominance slowly eroded over time."
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
