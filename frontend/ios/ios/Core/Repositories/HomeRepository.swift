//
//  HomeRepository.swift
//  ios
//
//  Repository layer for the redesigned Home dashboard.
//
//  Follows the same MVVM + Repository pattern as `StockRepository`: a protocol
//  the ViewModel depends on, with a swappable implementation. `MockHomeRepository`
//  is a UI-only data source (no backend / Supabase) populated with realistic
//  dummy data so the new screen renders fully. A real `HomeRepository` that talks
//  to the backend can be added later behind the same protocol.
//

import SwiftUI

// MARK: - Protocol

protocol HomeRepositoryProtocol {
    /// Fetch everything the Home dashboard renders.
    func fetchHomeDashboard() async throws -> HomeDashboardData
}

// MARK: - Live implementation (backend-backed)

/// Talks to `GET /api/v1/home/dashboard` via `APIClient`, decodes the
/// aggregated DTO, and maps it into the display-ready presentation models.
///
/// Mirrors `StockRepository`: an injected `APIClient`, with errors left to
/// propagate (the ViewModel maps them via `AppError`). Number→string
/// formatting and the green/red `isPositive` flag are computed HERE so the
/// views stay dumb — exactly what the mock did, just from live data.
///
/// Only the Market Pulse strip is served today; the lower sections are returned
/// empty (which `HomeDashboardView` renders as nothing) until the backend grows
/// the same response top-to-bottom.
final class HomeRepository: HomeRepositoryProtocol {

    private let apiClient: APIClient

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
    }

    func fetchHomeDashboard() async throws -> HomeDashboardData {
        let dto = try await apiClient.request(
            endpoint: .getHomeDashboard,
            responseType: HomeDashboardResponseDTO.self
        )
        return Self.map(dto)
    }

    // MARK: - DTO → presentation mapping

    private static func map(_ dto: HomeDashboardResponseDTO) -> HomeDashboardData {
        HomeDashboardData(
            marketStatusText: dto.marketStatusText,
            marketIsOpen: dto.marketIsOpen,
            pulse: dto.pulse.map(mapPulse),
            scanners: mapScanners(dto.scanners),
            // Sections below the scanners aren't served yet — empty arrays
            // render nothing (sections hide).
            signals: [],
            themes: []
        )
    }

    private static func mapPulse(_ dto: MarketPulseItemDTO) -> MarketPulseItem {
        let type = MarketTickerType(rawValue: dto.type) ?? .stock
        return MarketPulseItem(
            name: dto.name,
            symbol: dto.symbol,
            type: type,
            priceText: formatPrice(dto.price, type: type),
            changeText: formatPercent(dto.changePercent),
            isPositive: dto.changePercent >= 0,
            // Backend sends the latest-session intraday closes oldest-first, and
            // `previousClose` is the dashed reference line — so the card colours
            // green ABOVE / red BELOW it, exactly like the Holdings cards.
            spark: dto.spark,
            previousClose: dto.previousClose
        )
    }

    // MARK: - Number → display-string formatting

    /// Signed, 2-decimal percent: `0.62 → "+0.62%"`, `-1.85 → "-1.85%"`.
    private static func formatPercent(_ pct: Double) -> String {
        String(format: "%+.2f%%", pct)
    }

    /// Grouped price string matching the design: crypto at whole-dollar
    /// magnitudes (BTC ≈ 112,430) shows no decimals; indices and commodities
    /// show cents (6,952.40, 70.95).
    private static func formatPrice(_ price: Double, type: MarketTickerType) -> String {
        let decimals = (type == .crypto && abs(price) >= 100) ? 0 : 2
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.usesGroupingSeparator = true
        formatter.minimumFractionDigits = decimals
        formatter.maximumFractionDigits = decimals
        return formatter.string(from: NSNumber(value: price))
            ?? String(format: "%.\(decimals)f", price)
    }

    // MARK: - Daily Scanners mapping

    /// Build the three scanner cards. The presentation chrome (title / icon /
    /// accent / badge / CTA / infoNote) is FIXED per kind, so it's hardcoded here
    /// (matching the mock); the backend supplies only the ranked rows.
    private static func mapScanners(_ dto: ScannerGroupsDTO?) -> [DailyScanner] {
        guard let dto else { return [] }
        var out: [DailyScanner] = []

        if let m = dto.movers, !(m.gainers.isEmpty && m.losers.isEmpty) {
            out.append(DailyScanner(
                kind: .movers,
                title: "Today's Top Movers",
                subtitle: "",
                iconSystemName: "chart.line.uptrend.xyaxis",
                accent: AppColors.bullish,
                expandCTA: "See full leaderboard",
                gainers: m.gainers.map(mapMoverRow),
                losers: m.losers.map(mapMoverRow)
            ))
        }

        if let v = dto.volume, !v.entries.isEmpty {
            out.append(DailyScanner(
                kind: .volume,
                title: "Heavy Traffic",
                subtitle: "Unusual trading volume",
                iconSystemName: "chart.bar.fill",
                accent: AppColors.accentCyan,
                badgeText: "Volume",
                expandCTA: "See all unusual volume",
                entries: v.entries.map(mapVolumeRow)
            ))
        }

        if let s = dto.shorts, !s.entries.isEmpty {
            out.append(DailyScanner(
                kind: .shorts,
                title: "Skeptical Money",
                subtitle: "Highest short interest",
                iconSystemName: "eye.fill",
                accent: AppColors.neutral,
                badgeText: "Shorts",
                expandCTA: "See all high short interest",
                infoNote: "High short interest reflects skepticism — and can fuel a short squeeze if the stock rallies.",
                entries: s.entries.map(mapShortRow)
            ))
        }

        return out
    }

    // MOVERS: primary = signed % (colored), secondary = price · market cap.
    private static func mapMoverRow(_ r: ScannerRowDTO) -> ScannerEntry {
        ScannerEntry(
            rank: r.rank, symbol: r.symbol, name: r.name,
            primaryText: formatSignedPercent(r.changePercent),
            secondaryText: formatDollar(r.price) + capSuffix(r.marketCap),
            isPositive: r.changePercent >= 0,
            spark: r.spark
        )
    }

    // VOLUME: primary = RVOL "8.4×" (white), secondary = signed % (colored).
    private static func mapVolumeRow(_ r: ScannerRowDTO) -> ScannerEntry {
        ScannerEntry(
            rank: r.rank, symbol: r.symbol, name: r.name,
            primaryText: String(format: "%.1f×", r.volumeMultiple ?? 0),
            secondaryText: formatSignedPercent(r.changePercent),
            isPositive: r.changePercent >= 0,
            spark: r.spark
        )
    }

    // SHORTS: primary = % of float (amber), secondary = price · market cap; isPositive always false.
    private static func mapShortRow(_ r: ScannerRowDTO) -> ScannerEntry {
        ScannerEntry(
            rank: r.rank, symbol: r.symbol, name: r.name,
            primaryText: String(format: "%.1f%%", r.shortPercentOfFloat ?? 0),
            secondaryText: formatDollar(r.price) + capSuffix(r.marketCap),
            isPositive: false,
            spark: r.spark
        )
    }

    private static func formatSignedPercent(_ p: Double) -> String {
        String(format: "%+.1f%%", p)
    }

    private static func formatDollar(_ p: Double) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .decimal
        formatter.usesGroupingSeparator = true
        formatter.minimumFractionDigits = 2
        formatter.maximumFractionDigits = 2
        return "$" + (formatter.string(from: NSNumber(value: p)) ?? String(format: "%.2f", p))
    }

    /// " · 45.2B Cap" appended to a price, or "" when market cap is absent.
    private static func capSuffix(_ cap: Double?) -> String {
        let text = formatMarketCap(cap)
        return text.isEmpty ? "" : " · " + text
    }

    /// Market cap abbreviated as M / B / T with one decimal, e.g. "500.5M Cap",
    /// "45.2B Cap", "3.1T Cap". Empty when missing/non-finite.
    private static func formatMarketCap(_ cap: Double?) -> String {
        guard let cap, cap.isFinite, cap > 0 else { return "" }
        if cap >= 1_000_000_000_000 { return String(format: "%.1fT Cap", cap / 1_000_000_000_000) }
        if cap >= 1_000_000_000 { return String(format: "%.1fB Cap", cap / 1_000_000_000) }
        return String(format: "%.1fM Cap", cap / 1_000_000)
    }
}

// MARK: - Mock implementation (UI only)

/// Hard-coded, realistic dummy data mirroring the approved Caydex Home design.
/// Swap this for a backend-backed implementation later — the ViewModel never
/// needs to change.
final class MockHomeRepository: HomeRepositoryProtocol {

    func fetchHomeDashboard() async throws -> HomeDashboardData {
        HomeDashboardData(
            marketStatusText: "Markets Open",
            marketIsOpen: true,
            pulse: Self.pulse,
            scanners: [Self.movers, Self.heavyTraffic, Self.skepticalMoney],
            signals: Self.signals,
            themes: Self.themes
        )
    }

    // MARK: - Sparkline helper

    /// The design encodes sparklines as SVG y-coordinates in a 0…36 box where a
    /// SMALLER y means a HIGHER price. Flip them so ascending = rising for the
    /// chart renderer.
    private static func spark(_ ys: [Double]) -> [Double] { ys.map { 36 - $0 } }

    // MARK: - Market Pulse

    static let pulse: [MarketPulseItem] = [
        MarketPulseItem(name: "S&P 500", symbol: "^GSPC", type: .index,
                        priceText: "6,952.40", changeText: "+0.62%", isPositive: true,
                        spark: spark([28, 24, 26, 18, 21, 13, 16, 8])),
        MarketPulseItem(name: "Nasdaq", symbol: "^IXIC", type: .index,
                        priceText: "23,840.10", changeText: "+0.94%", isPositive: true,
                        spark: spark([30, 27, 22, 24, 16, 18, 11, 6])),
        MarketPulseItem(name: "Dow Jones", symbol: "^DJI", type: .index,
                        priceText: "44,265.80", changeText: "+0.18%", isPositive: true,
                        spark: spark([22, 24, 20, 22, 19, 21, 17, 15])),
        MarketPulseItem(name: "Bitcoin", symbol: "BTCUSD", type: .crypto,
                        priceText: "112,430", changeText: "-1.85%", isPositive: false,
                        spark: spark([9, 13, 11, 17, 15, 21, 19, 27])),
        MarketPulseItem(name: "Gold", symbol: "GCUSD", type: .commodity,
                        priceText: "3,486.20", changeText: "+0.41%", isPositive: true,
                        spark: spark([24, 22, 25, 19, 21, 17, 18, 13])),
        MarketPulseItem(name: "Crude Oil", symbol: "CLUSD", type: .commodity,
                        priceText: "70.95", changeText: "-0.77%", isPositive: false,
                        spark: spark([12, 10, 16, 14, 19, 17, 22, 25])),
    ]

    // MARK: - Scanner: Today's Top Movers

    static let movers = DailyScanner(
        kind: .movers,
        title: "Today's Top Movers",
        subtitle: "",  // no subtitle on the Top Movers card (toggle stands in for it)
        iconSystemName: "chart.line.uptrend.xyaxis",
        accent: AppColors.bullish,
        expandCTA: "See full leaderboard",
        gainers: [
            ScannerEntry(rank: 1, symbol: "SMCI", name: "Super Micro", primaryText: "+14.2%", secondaryText: "$58.30", isPositive: true,
                         spark: spark([31, 26, 28, 18, 20, 12, 14, 5])),
            ScannerEntry(rank: 2, symbol: "ARM", name: "Arm Holdings", primaryText: "+9.6%", secondaryText: "$198.70", isPositive: true),
            ScannerEntry(rank: 3, symbol: "AVGO", name: "Broadcom", primaryText: "+8.1%", secondaryText: "$312.40", isPositive: true),
            ScannerEntry(rank: 4, symbol: "PLTR", name: "Palantir", primaryText: "+6.7%", secondaryText: "$92.30", isPositive: true),
            ScannerEntry(rank: 5, symbol: "MU", name: "Micron", primaryText: "+5.9%", secondaryText: "$148.20", isPositive: true),
            ScannerEntry(rank: 6, symbol: "TSM", name: "Taiwan Semi", primaryText: "+5.2%", secondaryText: "$264.10", isPositive: true),
            ScannerEntry(rank: 7, symbol: "DELL", name: "Dell Tech", primaryText: "+4.8%", secondaryText: "$158.40", isPositive: true),
            ScannerEntry(rank: 8, symbol: "AMD", name: "Adv. Micro", primaryText: "+4.3%", secondaryText: "$214.60", isPositive: true),
        ],
        losers: [
            ScannerEntry(rank: 1, symbol: "WBD", name: "Warner Bros", primaryText: "-9.4%", secondaryText: "$9.10", isPositive: false,
                         spark: spark([7, 11, 9, 16, 14, 20, 22, 29])),
            ScannerEntry(rank: 2, symbol: "INTC", name: "Intel", primaryText: "-7.8%", secondaryText: "$24.30", isPositive: false),
            ScannerEntry(rank: 3, symbol: "PARA", name: "Paramount", primaryText: "-6.9%", secondaryText: "$13.40", isPositive: false),
            ScannerEntry(rank: 4, symbol: "F", name: "Ford Motor", primaryText: "-5.2%", secondaryText: "$11.80", isPositive: false),
            ScannerEntry(rank: 5, symbol: "PYPL", name: "PayPal", primaryText: "-4.7%", secondaryText: "$78.20", isPositive: false),
            ScannerEntry(rank: 6, symbol: "NKE", name: "Nike", primaryText: "-4.1%", secondaryText: "$74.60", isPositive: false),
            ScannerEntry(rank: 7, symbol: "SBUX", name: "Starbucks", primaryText: "-3.6%", secondaryText: "$96.30", isPositive: false),
            ScannerEntry(rank: 8, symbol: "T", name: "AT&T", primaryText: "-3.1%", secondaryText: "$27.40", isPositive: false),
        ]
    )

    // MARK: - Scanner: Heavy Traffic (volume)

    static let heavyTraffic = DailyScanner(
        kind: .volume,
        title: "Heavy Traffic",
        subtitle: "Unusual trading volume",
        iconSystemName: "chart.bar.fill",
        accent: AppColors.accentCyan,
        badgeText: "Volume",
        expandCTA: "See all unusual volume",
        entries: [
            ScannerEntry(rank: 1, symbol: "GME", name: "GameStop", primaryText: "8.4×", secondaryText: "+3.2%", isPositive: true,
                         spark: spark([24, 18, 26, 15, 22, 12, 20, 9])),
            ScannerEntry(rank: 2, symbol: "HOOD", name: "Robinhood", primaryText: "5.7×", secondaryText: "+4.6%", isPositive: true),
            ScannerEntry(rank: 3, symbol: "SOFI", name: "SoFi Tech", primaryText: "4.9×", secondaryText: "+2.1%", isPositive: true),
            ScannerEntry(rank: 4, symbol: "RIVN", name: "Rivian", primaryText: "4.2×", secondaryText: "-1.8%", isPositive: false),
            ScannerEntry(rank: 5, symbol: "AMC", name: "AMC Ent.", primaryText: "3.8×", secondaryText: "+6.8%", isPositive: true),
            ScannerEntry(rank: 6, symbol: "LCID", name: "Lucid", primaryText: "3.3×", secondaryText: "-2.4%", isPositive: false),
            ScannerEntry(rank: 7, symbol: "NIO", name: "NIO Inc", primaryText: "3.1×", secondaryText: "+1.2%", isPositive: true),
            ScannerEntry(rank: 8, symbol: "PLUG", name: "Plug Power", primaryText: "2.9×", secondaryText: "-3.1%", isPositive: false),
        ]
    )

    // MARK: - Scanner: Skeptical Money (short interest)

    static let skepticalMoney = DailyScanner(
        kind: .shorts,
        title: "Skeptical Money",
        subtitle: "Highest short interest",
        iconSystemName: "eye.fill",
        accent: AppColors.neutral,
        badgeText: "Shorts",
        expandCTA: "See all high short interest",
        infoNote: "High short interest reflects skepticism — and can fuel a short squeeze if the stock rallies.",
        entries: [
            ScannerEntry(rank: 1, symbol: "BYND", name: "Beyond Meat", primaryText: "41.2%", secondaryText: "$5.80", isPositive: false,
                         spark: spark([10, 13, 11, 16, 14, 19, 18, 24])),
            ScannerEntry(rank: 2, symbol: "WOLF", name: "Wolfspeed", primaryText: "35.7%", secondaryText: "$8.40", isPositive: false),
            ScannerEntry(rank: 3, symbol: "UPST", name: "Upstart", primaryText: "32.6%", secondaryText: "$62.40", isPositive: false),
            ScannerEntry(rank: 4, symbol: "RILY", name: "B. Riley", primaryText: "30.2%", secondaryText: "$4.70", isPositive: false),
            ScannerEntry(rank: 5, symbol: "AI", name: "C3.ai", primaryText: "28.4%", secondaryText: "$24.60", isPositive: false),
            ScannerEntry(rank: 6, symbol: "FUBO", name: "fuboTV", primaryText: "26.1%", secondaryText: "$1.90", isPositive: false),
            ScannerEntry(rank: 7, symbol: "BIGC", name: "BigCommerce", primaryText: "24.9%", secondaryText: "$6.10", isPositive: false),
            ScannerEntry(rank: 8, symbol: "CVNA", name: "Carvana", primaryText: "22.8%", secondaryText: "$244.10", isPositive: false),
        ]
    )

    // MARK: - App-Exclusive Signals

    static let signals: [ExclusiveSignal] = [
        ExclusiveSignal(
            title: "Congressional Buys",
            subtitle: "Most-bought on Capitol Hill this week",
            iconSystemName: "building.columns.fill",
            accent: AppColors.primaryBlue,
            topSymbol: "NVDA",
            topStat: "7 members buying",
            leaders: [
                SignalLeader(symbol: "NVDA", stat: "4 buys"),
                SignalLeader(symbol: "MSFT", stat: "3 buys"),
                SignalLeader(symbol: "GOOGL", stat: "2 buys"),
            ]
        ),
        ExclusiveSignal(
            title: "Whale Accumulation",
            subtitle: "Institutions quietly loading up",
            iconSystemName: "square.3.layers.3d.down.right",
            accent: AppColors.alertOrange,
            topSymbol: "MSFT",
            topStat: "14 funds adding",
            leaders: [
                SignalLeader(symbol: "MSFT", stat: "+$2.1B"),
                SignalLeader(symbol: "AMZN", stat: "+$1.4B"),
                SignalLeader(symbol: "META", stat: "+$0.9B"),
            ]
        ),
        ExclusiveSignal(
            title: "Earnings Shockers",
            subtitle: "Just beat or missed the Street big",
            iconSystemName: "bolt.fill",
            accent: AppColors.accentYellow,
            topSymbol: "AVGO",
            topStat: "+22% surprise",
            leaders: [
                SignalLeader(symbol: "AVGO", stat: "+22% EPS"),
                SignalLeader(symbol: "CRM", stat: "+15% EPS"),
                SignalLeader(symbol: "NOW", stat: "+12% EPS"),
            ]
        ),
    ]

    // MARK: - Trending Themes

    static let themes: [TrendingTheme] = [
        TrendingTheme(title: "AI & Semiconductors", count: "28 stocks", changeText: "+3.4%",
                      iconSystemName: "cpu.fill", accent: Color(hex: "22D3EE")),
        TrendingTheme(title: "Clean Energy & Grids", count: "22 stocks", changeText: "+1.2%",
                      iconSystemName: "sun.max.fill", accent: Color(hex: "34D399")),
        TrendingTheme(title: "Blockchain & DeFi", count: "18 stocks", changeText: "+5.1%",
                      iconSystemName: "hexagon.fill", accent: Color(hex: "C084FC")),
        TrendingTheme(title: "Defense & Aerospace", count: "16 stocks", changeText: "+0.8%",
                      iconSystemName: "shield.fill", accent: Color(hex: "FBBF24")),
        TrendingTheme(title: "The Future of Healthcare", count: "24 stocks", changeText: "+2.7%",
                      iconSystemName: "cross.case.fill", accent: Color(hex: "2DD4BF")),
        TrendingTheme(title: "Robotics", count: "19 stocks", changeText: "+4.2%",
                      iconSystemName: "gearshape.2.fill", accent: Color(hex: "FB923C")),
    ]
}
