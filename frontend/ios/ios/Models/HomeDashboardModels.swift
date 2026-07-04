//
//  HomeDashboardModels.swift
//  ios
//
//  UI models for the redesigned Home dashboard (Caydex Home).
//
//  These are PRESENTATION models for the new home screen — they hold
//  display-ready strings (preformatted prices/percentages) so the views stay
//  dumb. They are intentionally separate from the legacy `HomeModels.swift`
//  feed DTOs so the redesign is self-contained and the old backend-connected
//  Home keeps working untouched.
//
//  Reuse note: `MarketTickerType` (from HomeModels.swift) is reused so a tapped
//  market-pulse item can route to the correct existing detail screen.
//

import SwiftUI

// MARK: - Market Pulse

/// One tile in the horizontally-scrolling "Markets Open" pulse strip
/// (S&P 500, Nasdaq, Bitcoin, …).
struct MarketPulseItem: Identifiable, Hashable {
    let id = UUID()
    let name: String
    let symbol: String
    let type: MarketTickerType
    /// Pre-formatted price exactly as shown in the design (e.g. "6,952.40", "112,430").
    let priceText: String
    /// Pre-formatted change (e.g. "+0.62%", "-1.85%").
    let changeText: String
    let isPositive: Bool
    /// Latest-session intraday series, ascending = later in the session.
    let spark: [Double]
    /// Prior trading day's close. The card draws a dashed reference line here and
    /// colours the sparkline green ABOVE / red BELOW it. `nil` → the sparkline
    /// anchors to its first point instead.
    let previousClose: Double?

    // Explicit init with a `previousClose` default so existing call sites (the
    // mock fixtures) keep compiling while the live repository supplies it.
    init(name: String, symbol: String, type: MarketTickerType,
         priceText: String, changeText: String, isPositive: Bool,
         spark: [Double], previousClose: Double? = nil) {
        self.name = name
        self.symbol = symbol
        self.type = type
        self.priceText = priceText
        self.changeText = changeText
        self.isPositive = isPositive
        self.spark = spark
        self.previousClose = previousClose
    }
}

// MARK: - Daily Scanners

enum ScannerKind: Hashable {
    case movers   // Today's Top Movers — gainers / losers toggle
    case volume   // Heavy Traffic — unusual trading volume
    case shorts   // Skeptical Money — highest short interest
}

/// A single leaderboard entry inside a scanner card's expanded list.
/// `primaryText` / `secondaryText` meaning depends on the scanner kind:
///   • movers  → primary = "+14.2%" (colored),  secondary = "$58.30"
///   • volume  → primary = "8.4×"  (white),     secondary = "+3.2%" (colored)
///   • shorts  → primary = "41.2%" (amber),     secondary = "$5.80"
struct ScannerEntry: Identifiable, Hashable {
    let id = UUID()
    let rank: Int
    let symbol: String
    let name: String
    let primaryText: String
    let secondaryText: String
    let isPositive: Bool
    /// Only the head (rank 1) entry carries a sparkline; rows render none.
    var spark: [Double] = []
}

/// One card in the "Daily Scanners" swipeable carousel.
struct DailyScanner: Identifiable {
    let id = UUID()
    let kind: ScannerKind
    let title: String
    let subtitle: String
    let iconSystemName: String
    let accent: Color
    /// Pill shown top-right for volume/shorts ("Volume", "Shorts"). Movers shows
    /// a Gainers/Losers toggle instead, so this is nil there.
    let badgeText: String?
    /// Label on the expand button ("See full leaderboard", …).
    let expandCTA: String
    /// Optional explainer note (shorts card only).
    let infoNote: String?

    // Movers uses two lists (toggle); volume/shorts use `entries`.
    let gainers: [ScannerEntry]
    let losers: [ScannerEntry]
    let entries: [ScannerEntry]

    init(
        kind: ScannerKind,
        title: String,
        subtitle: String,
        iconSystemName: String,
        accent: Color,
        badgeText: String? = nil,
        expandCTA: String,
        infoNote: String? = nil,
        gainers: [ScannerEntry] = [],
        losers: [ScannerEntry] = [],
        entries: [ScannerEntry] = []
    ) {
        self.kind = kind
        self.title = title
        self.subtitle = subtitle
        self.iconSystemName = iconSystemName
        self.accent = accent
        self.badgeText = badgeText
        self.expandCTA = expandCTA
        self.infoNote = infoNote
        self.gainers = gainers
        self.losers = losers
        self.entries = entries
    }
}

// MARK: - App-Exclusive Signals

/// A leader inside an expanded signal row (e.g. "NVDA · 4 buys").
struct SignalLeader: Identifiable, Hashable {
    let id = UUID()
    let symbol: String
    let stat: String
}

/// One row in the glowing "App-Exclusive Signals" card.
struct ExclusiveSignal: Identifiable {
    let id = UUID()
    /// "congress" | "whale" | "earnings" — routes the leader tap (whale/congress
    /// open the per-ticker detail; earnings opens TickerDetailView directly).
    let kind: String
    let title: String
    let subtitle: String
    let iconSystemName: String
    let accent: Color
    let topSymbol: String
    let topStat: String
    let leaders: [SignalLeader]
}

// MARK: - Emerging Frontiers (Trending Themes)

/// One tile in the "Emerging Frontiers" grid — a server-driven megatrend card.
struct TrendingTheme: Identifiable {
    let id = UUID()
    /// The "Next-Wave" name shown on the card, e.g. "The Silicon Rush".
    let title: String
    /// e.g. "8 stocks".
    let count: String
    /// Pre-formatted signed change, e.g. "+3.4%". Empty when the backend had no
    /// resolvable quotes → the tile hides the badge.
    let changeText: String
    /// Drives the badge colour (green up / red down).
    let isPositive: Bool
    /// Remote card image (Supabase Storage). `nil`/empty → an accent-gradient
    /// placeholder is drawn instead.
    let imageUrl: String?
    let accent: Color
}

// MARK: - Aggregate

/// Everything the redesigned Home screen renders, supplied by `HomeRepositoryProtocol`.
struct HomeDashboardData {
    let marketStatusText: String   // "Markets Open"
    let marketIsOpen: Bool
    let pulse: [MarketPulseItem]
    let scanners: [DailyScanner]
    let signals: [ExclusiveSignal]
    let themes: [TrendingTheme]
}

// MARK: - Live wire models (DTOs)

/// Decoded from `GET /api/v1/home/dashboard` by the live `HomeRepository`, then
/// mapped into the presentation models above. These carry RAW numbers — the
/// repository formats them into the display strings the views consume.
///
/// Explicit snake_case `CodingKeys` are required: the iOS `APIClient` decoder
/// deliberately does NOT use `.convertFromSnakeCase`, so every DTO declares its
/// own keys (mismatch = a decode crash).
struct HomeDashboardResponseDTO: Decodable {
    let marketStatusText: String
    let marketIsOpen: Bool
    let pulse: [MarketPulseItemDTO]
    /// Optional so an older backend that omits it can't crash decode.
    let scanners: ScannerGroupsDTO?
    /// Optional for the same reason — a backend that predates signals decodes nil.
    let signals: SignalGroupsDTO?
    /// Optional for the same reason — a backend that predates themes decodes nil.
    let themes: ThemesGroupDTO?

    enum CodingKeys: String, CodingKey {
        case marketStatusText = "market_status_text"
        case marketIsOpen = "market_is_open"
        case pulse
        case scanners
        case signals
        case themes
    }
}

/// One Market Pulse tile as served by the backend (raw values).
struct MarketPulseItemDTO: Decodable {
    let symbol: String
    let name: String
    let type: String          // "index" | "crypto" | "commodity" | "stock" | "etf"
    let price: Double
    let changePercent: Double
    /// Prior trading day's close → the dashed reference line. May be null.
    let previousClose: Double?
    /// Latest-session intraday closes, oldest-first. May be empty.
    let spark: [Double]

    enum CodingKeys: String, CodingKey {
        case symbol, name, type, price, spark
        case changePercent = "change_percent"
        case previousClose = "previous_close"
    }
}

// MARK: - Daily Scanner DTOs

/// One ranked leaderboard row (raw numbers; the repository formats them per kind).
struct ScannerRowDTO: Decodable {
    let rank: Int
    let symbol: String
    let name: String
    let price: Double
    let changePercent: Double
    let marketCap: Double?              // shown next to price as "· 45.2B Cap"
    let volumeMultiple: Double?         // Heavy Traffic (RVOL)
    let shortPercentOfFloat: Double?    // Skeptical Money
    let spark: [Double]                 // rank-1 only, else []

    enum CodingKeys: String, CodingKey {
        case rank, symbol, name, price, spark
        case changePercent = "change_percent"
        case marketCap = "market_cap"
        case volumeMultiple = "volume_multiple"
        case shortPercentOfFloat = "short_percent_of_float"
    }
}

/// One scanner card's data. Movers uses gainers+losers; volume/shorts use entries.
struct ScannerGroupDTO: Decodable {
    let kind: String
    let gainers: [ScannerRowDTO]
    let losers: [ScannerRowDTO]
    let entries: [ScannerRowDTO]
    /// Card-level "as of" settlement date (ISO `yyyy-MM-dd`) — SHORTS only; nil
    /// for movers/volume (and for shorts when no shown row carries a date). The
    /// repository renders it as the "As of Jun 15" subtitle. Optional → decode-safe.
    let asOfDate: String?

    enum CodingKeys: String, CodingKey {
        case kind, gainers, losers, entries
        case asOfDate = "as_of_date"
    }
}

/// The three scanner cards. A null group → that card is omitted.
struct ScannerGroupsDTO: Decodable {
    let movers: ScannerGroupDTO?
    let volume: ScannerGroupDTO?
    let shorts: ScannerGroupDTO?
}

// MARK: - App-Exclusive Signal DTOs

/// One ranked leader row in a signal card (raw numbers; the repository formats
/// them per kind). `value` is polymorphic by the enclosing group's `kind`:
/// congress → distinct-member count · whale → distinct-fund count · earnings →
/// SIGNED EPS surprise % (beat +, miss −). All keys are already snake_case-safe
/// (single words), so no `CodingKeys` map is needed.
struct SignalRowDTO: Decodable {
    let rank: Int
    let symbol: String
    /// Optional for decode-safety: the backend always sends it (defaults to "")
    /// but it's unused by `mapSignals`, so a missing key must NOT crash the whole
    /// dashboard decode. `?` makes `keyNotFound` a no-op instead of a fatal error.
    let name: String?
    let value: Double
}

/// One signal card's data. The headline (top ticker + its stat) is `entries[0]`
/// — the repository derives it, mirroring the scanner cards.
struct SignalGroupDTO: Decodable {
    let kind: String                // "congress" | "whale" | "earnings"
    let entries: [SignalRowDTO]
    /// Card-level "as of" context (ISO `yyyy-MM-dd`) or nil. Optional → decode-safe.
    let asOfDate: String?

    enum CodingKeys: String, CodingKey {
        case kind, entries
        case asOfDate = "as_of_date"
    }
}

/// The three App-Exclusive Signal cards. A null group → that card is omitted.
struct SignalGroupsDTO: Decodable {
    let congress: SignalGroupDTO?
    let whale: SignalGroupDTO?
    let earnings: SignalGroupDTO?
}

// MARK: - Emerging Frontiers (Trending Theme) DTOs

/// One Emerging Frontiers card as served by the backend (raw numbers; the
/// repository formats count/percent + picks the badge colour). `category` and the
/// constituent tickers are NOT on the wire — they drive the metrics server-side,
/// so editing them in Supabase updates the card with no app release.
struct TrendingThemeDTO: Decodable {
    let slug: String
    let title: String
    /// Public Supabase Storage URL, or nil → the tile draws an accent gradient.
    let imageUrl: String?
    /// 6-digit hex WITHOUT a leading '#', e.g. "22D3EE". Decoded via `Color(hex:)`.
    let accentHex: String
    /// Number of configured tickers → "N stocks".
    let tickerCount: Int
    /// Avg daily % over the resolvable tickers; nil → the tile hides the % badge.
    let changePercent: Double?

    enum CodingKeys: String, CodingKey {
        case slug, title
        case imageUrl = "image_url"
        case accentHex = "accent_hex"
        case tickerCount = "ticker_count"
        case changePercent = "change_percent"
    }
}

/// The Emerging Frontiers list. The wire shape is an OBJECT (`{"themes":[...]}`,
/// symmetric with ScannerGroupsDTO/SignalGroupsDTO), so this wrapper matches it
/// exactly — decoding the field as a bare array would crash. Empty list → hidden.
struct ThemesGroupDTO: Decodable {
    let themes: [TrendingThemeDTO]
}
