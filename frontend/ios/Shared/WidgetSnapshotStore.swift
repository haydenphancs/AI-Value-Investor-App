//
//  WidgetSnapshotStore.swift
//  Caydex
//
//  The ONE channel between the app and the Movers widget extension.
//
//  The app fetches (it owns the auth token) and writes; the widget reads and renders.
//  The widget performs no network call and no identity read.
//
//  ⚠️ WHY NOT JUST LET THE WIDGET CALL THE API
//  `.claude/rules/auth.md` §8 makes `APIClient` the single token source because the
//  client token and the Keychain deliberately DIVERGE during `.restoring`: a Keychain
//  reader authenticates as the real account while the app UI says guest. A widget is a
//  separate process — it cannot reach `APIClient`'s actor state — so "read the token
//  yourself" is the only shape available to it, and that is exactly the shape the rule
//  forbids. It also could not refresh an expired token (refresh is main-actor, in the
//  app), so it would 401 at expiry with no recovery path.
//
//  ⚠️ AND A WORSE TRAP, IF SOMEONE TRIES THE GUEST ROUTE
//  `GuestIdentity` writes the per-install id with no `kSecAttrAccessGroup`. Adding one
//  so an extension could read it makes the EXISTING read miss, `current` mints a fresh
//  UUID, and `write()` stores it in the new group — silently abandoning that install's
//  watchlist, portfolios, chats and Learn progress, with no recovery (the old rows are
//  service-role-only). Do not add an access group to GuestIdentity.
//
//  Market mode is a `.public` route, so a future revision COULD let the widget fetch it
//  directly for extra freshness. Portfolio mode never can.
//

import Foundation
import OSLog

#if canImport(WidgetKit)
import WidgetKit
#endif

/// Shared identifiers. Must stay byte-identical to both `.entitlements` files —
/// `test_ios_widget_parity.py` fails the build if they drift, because a mismatch is
/// silent: `UserDefaults(suiteName:)` simply returns nil and the widget shows its
/// placeholder forever with nothing logged on either side.
public enum WidgetSharedConfig {
    public static let appGroupIdentifier = "group.com.phan.caydex"
    public static let snapshotKey = "widget.movers.snapshot.v1"
    /// Widget kind, shared so the app can reload exactly this widget.
    public static let moversKind = "CaydexMoversWidget"
}

// MARK: - Wire model

/// Mirrors `backend/app/schemas/widget.py`. Explicit `CodingKeys` throughout —
/// `APIClient` does not use `.convertFromSnakeCase`.
///
/// Every field that can be absent is Optional. A widget that fails to decode does not
/// show an error state; it shows the placeholder, on the Home Screen, with no way for
/// the user to retry and no crash report anyone would think to send.
public struct WidgetMoverSnapshot: Codable, Equatable, Sendable {
    public let mode: String
    /// When the payload was BUILT — not what day the numbers describe. Use
    /// `sessionDate` for that; see `WidgetSessionLabel`.
    public let asOf: Date
    public let marketSession: String
    /// ET calendar date (`YYYY-MM-DD`) of the trading session these numbers describe.
    ///
    /// A `String`, deliberately NOT a `Date`: it is a plain calendar date with no time,
    /// and running it through the `.iso8601` strategy would fail to decode.
    ///
    /// This is what lets the tile age its own label with no network and no flag. The app
    /// may have fetched Friday at 15:58 and the tile may be read on Sunday; the date says
    /// which day, so the widget re-derives "Fri close" at render time.
    public let sessionDate: String?
    /// The sentence that was true at `asOf` — "Live 2:14 PM ET", "Fri close". The client
    /// may DOWNGRADE it as it ages but never composes its own.
    public let sessionLabel: String?
    /// Which universe the movers came from — "Your holdings", "The stocks Caydex tracks".
    public let scopeLabel: String?
    /// How the market itself did. Absent when every upstream leg failed — the tile
    /// then leads with the mover, exactly as it did before this field existed.
    public let marketContext: WidgetMarketContext?
    public let headlineMover: WidgetMover?
    /// The one-sentence read on the whole market — Market mode only, and only when the
    /// backend's roll-up is dated to this session. Absent is NORMAL: the tile then
    /// leads with the index numbers, which are always current.
    public let marketBrief: WidgetMarketBrief?
    public let basket: WidgetBasket?
    /// Next few movers, for the large family. Empty on smaller sizes' data too —
    /// the backend always sends them; only Large renders them.
    public let runnersUp: [WidgetMover]

    enum CodingKeys: String, CodingKey {
        case mode
        case asOf = "as_of"
        case marketSession = "market_session"
        case sessionDate = "session_date"
        case sessionLabel = "session_label"
        case scopeLabel = "scope_label"
        case marketBrief = "market_brief"
        case marketContext = "market_context"
        case headlineMover = "headline_mover"
        case basket
        case runnersUp = "runners_up"
    }

    /// ⚠️ EVERY OPTIONAL FIELD MUST BE READ WITH `decodeIfPresent`, WITH A DEFAULT.
    ///
    /// The widget ships in an app update; the backend deploys independently. A new app
    /// running against a not-yet-deployed backend must still render — and a decode
    /// failure here has no error surface at all: `read()` returns nil and the user gets
    /// the placeholder on their Home Screen, with no retry and no crash report anyone
    /// would think to send. `runners_up` set this precedent; keep it for everything.
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        mode = try c.decode(String.self, forKey: .mode)
        asOf = try c.decode(Date.self, forKey: .asOf)
        marketSession = try c.decode(String.self, forKey: .marketSession)
        sessionDate = try c.decodeIfPresent(String.self, forKey: .sessionDate)
        sessionLabel = try c.decodeIfPresent(String.self, forKey: .sessionLabel)
        scopeLabel = try c.decodeIfPresent(String.self, forKey: .scopeLabel)
        marketBrief = try c.decodeIfPresent(WidgetMarketBrief.self, forKey: .marketBrief)
        marketContext = try c.decodeIfPresent(WidgetMarketContext.self, forKey: .marketContext)
        headlineMover = try c.decodeIfPresent(WidgetMover.self, forKey: .headlineMover)
        basket = try c.decodeIfPresent(WidgetBasket.self, forKey: .basket)
        runnersUp = try c.decodeIfPresent([WidgetMover].self, forKey: .runnersUp) ?? []
    }

    public init(
        mode: String, asOf: Date, marketSession: String,
        sessionDate: String? = nil, sessionLabel: String? = nil, scopeLabel: String? = nil,
        marketBrief: WidgetMarketBrief? = nil,
        marketContext: WidgetMarketContext? = nil,
        headlineMover: WidgetMover?, basket: WidgetBasket?, runnersUp: [WidgetMover] = []
    ) {
        self.mode = mode
        self.asOf = asOf
        self.marketSession = marketSession
        self.sessionDate = sessionDate
        self.sessionLabel = sessionLabel
        self.scopeLabel = scopeLabel
        self.marketBrief = marketBrief
        self.marketContext = marketContext
        self.headlineMover = headlineMover
        self.basket = basket
        self.runnersUp = runnersUp
    }

    /// True when the payload carries no mover at all.
    ///
    /// The backend degrades-never-errors: an upstream failure answers HTTP 200 with
    /// `headline_mover: null`. That is a legitimate response but NOT something worth
    /// overwriting a good snapshot with — see `WidgetSnapshotStore.write`.
    public var isEmpty: Bool { headlineMover == nil && runnersUp.isEmpty }
}

/// The one-sentence read on the whole market, for the Market tile.
///
/// Market mode answers "what is the market doing"; Holdings mode answers "what moved
/// most of mine". The backend session-gates this so an off-session roll-up arrives as
/// nil rather than as a stale sentence the tile would have to caveat.
public struct WidgetMarketBrief: Codable, Equatable, Sendable {
    public let headline: String
    /// 'Bullish' | 'Bearish' | 'Neutral'. Optional — an older backend omits it.
    public let sentiment: String?
    public let generatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case headline
        case sentiment
        case generatedAt = "generated_at"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        headline = try c.decode(String.self, forKey: .headline)
        sentiment = try c.decodeIfPresent(String.self, forKey: .sentiment)
        generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
    }

    public init(headline: String, sentiment: String? = nil, generatedAt: Date? = nil) {
        self.headline = headline
        self.sentiment = sentiment
        self.generatedAt = generatedAt
    }
}

/// One index in the market band.
///
/// `label` comes from the SERVER on purpose: an already-installed widget cannot learn
/// that a newly added symbol is called "Russell 2000" without an app update, so the
/// client must never map symbols to names itself.
public struct WidgetIndex: Codable, Equatable, Sendable {
    public let symbol: String
    public let label: String
    public let changePercent: Double?
    public let price: Double?

    enum CodingKeys: String, CodingKey {
        case symbol, label, price
        case changePercent = "change_percent"
    }

    /// Flat prints "0.00%", never "+0.00%" — same rule as `WidgetMover`.
    public var formattedChange: String? {
        guard let c = changePercent else { return nil }
        if (c * 100).rounded() == 0 { return "0.00%" }
        return String(format: "%+.2f%%", c)
    }

    public var isPositive: Bool { (changePercent ?? 0) > 0 }
    public var isFlat: Bool {
        guard let c = changePercent else { return false }
        return (c * 100).rounded() == 0
    }
}

/// How the MARKET is doing — distinct from `WidgetMoveContext`, which is arithmetic
/// about one ticker's move.
///
/// Every field is optional and each leg of the backend fetch degrades on its own, so a
/// tile can legitimately show indices with no breadth line, or the reverse.
public struct WidgetMarketContext: Codable, Equatable, Sendable {
    public let indices: [WidgetIndex]
    public let breadthUp: Int?
    public let breadthTotal: Int?
    public let leadingSector: String?
    public let leadingSectorChangePercent: Double?
    public let laggingSector: String?
    public let laggingSectorChangePercent: Double?
    /// The server's rendered sentence. Preferred over composing one here, so the wording
    /// lives in one place and cannot contradict the numbers beside it.
    public let text: String?

    enum CodingKeys: String, CodingKey {
        case indices, text
        case breadthUp = "breadth_up"
        case breadthTotal = "breadth_total"
        case leadingSector = "leading_sector"
        case leadingSectorChangePercent = "leading_sector_change_percent"
        case laggingSector = "lagging_sector"
        case laggingSectorChangePercent = "lagging_sector_change_percent"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        indices = try c.decodeIfPresent([WidgetIndex].self, forKey: .indices) ?? []
        breadthUp = try c.decodeIfPresent(Int.self, forKey: .breadthUp)
        breadthTotal = try c.decodeIfPresent(Int.self, forKey: .breadthTotal)
        leadingSector = try c.decodeIfPresent(String.self, forKey: .leadingSector)
        leadingSectorChangePercent = try c.decodeIfPresent(Double.self, forKey: .leadingSectorChangePercent)
        laggingSector = try c.decodeIfPresent(String.self, forKey: .laggingSector)
        laggingSectorChangePercent = try c.decodeIfPresent(Double.self, forKey: .laggingSectorChangePercent)
        text = try c.decodeIfPresent(String.self, forKey: .text)
    }

    public init(
        indices: [WidgetIndex] = [], breadthUp: Int? = nil, breadthTotal: Int? = nil,
        leadingSector: String? = nil, leadingSectorChangePercent: Double? = nil,
        laggingSector: String? = nil, laggingSectorChangePercent: Double? = nil,
        text: String? = nil
    ) {
        self.indices = indices
        self.breadthUp = breadthUp
        self.breadthTotal = breadthTotal
        self.leadingSector = leadingSector
        self.leadingSectorChangePercent = leadingSectorChangePercent
        self.laggingSector = laggingSector
        self.laggingSectorChangePercent = laggingSectorChangePercent
        self.text = text
    }

    /// "3 of 11 sectors up" — nil unless BOTH halves are present. A count without its
    /// denominator is not a breadth reading.
    public var breadthLabel: String? {
        guard let up = breadthUp, let total = breadthTotal, total > 0 else { return nil }
        return "\(up) of \(total) sectors up"
    }

    public var isEmpty: Bool { indices.isEmpty && breadthLabel == nil }
}

public struct WidgetMover: Codable, Equatable, Sendable {
    public let ticker: String
    public let companyName: String?
    public let changePercent: Double?
    public let price: Double?
    public let tier: String?
    public let z: Double?
    /// Why it moved TODAY. Always present; `.none` is a real answer, not a failure.
    public let cause: WidgetCause
    /// The arithmetic beside it — always true, never a guess.
    public let context: WidgetMoveContext

    enum CodingKeys: String, CodingKey {
        case ticker
        case companyName = "company_name"
        case changePercent = "change_percent"
        case price, tier, z, cause, context
    }

    /// `nil` ⇒ the number is HIDDEN, never rendered as 0.0%. A fabricated flat reading
    /// on a stock that actually moved is worse than no reading.
    ///
    /// A stock that closed EXACTLY flat prints "0.00%", not "+0.00%". `%+.2f%%` emits a
    /// leading `+` for zero while `isPositive` (`> 0`) is false for it, so the same glyph
    /// run said "gain" with its sign and "loss" with its colour.
    public var formattedChange: String? {
        guard let c = changePercent else { return nil }
        if isFlat { return "0.00%" }
        return String(format: "%+.2f%%", c)
    }

    /// Rounded to the two decimals actually displayed, so a value that PRINTS as flat is
    /// treated as flat. `-0.001` renders "0.00%" and must not be painted red.
    public var isFlat: Bool {
        guard let c = changePercent else { return false }
        return (c * 100).rounded() == 0
    }

    /// `-0.0 > 0` is false, so a signed zero cannot paint a gainer.
    public var isPositive: Bool { (changePercent ?? 0) > 0 }

    /// "1.1× normal" — the single most useful thing to put beside a percentage,
    /// because it says whether this move is remarkable *for this stock*.
    public var volatilityLabel: String? {
        guard let z else { return nil }
        return String(format: "%.1f× normal", z)
    }
}

/// What kind of cause the backend was able to establish. Mirrors `CauseKind`.
public enum WidgetCauseKind: String, Codable, Sendable {
    case earnings
    case analyst
    case companyNews = "company_news"
    case sector
    case market
    /// Nothing identifiable — the common, honest case.
    case none

    /// An unknown value decodes to `.none` rather than throwing: a backend that adds a
    /// seventh kind must not break every already-installed widget.
    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = WidgetCauseKind(rawValue: raw) ?? .none
    }

    /// Only an established cause earns the "Why it moved" framing.
    public var isEstablished: Bool { self != .none }
}

public struct WidgetCause: Codable, Equatable, Sendable {
    public let kind: WidgetCauseKind
    public let tag: String?
    public let detail: String
}

public struct WidgetMoveContext: Codable, Equatable, Sendable {
    public let changePercent: Double
    public let z: Double?
    public let gapPercent: Double?
    public let intradayPercent: Double?
    public let gapDominant: Bool
    public let industryName: String?
    public let industryChangePercent: Double?
    public let marketChangePercent: Double?

    enum CodingKeys: String, CodingKey {
        case changePercent = "change_percent"
        case z
        case gapPercent = "gap_percent"
        case intradayPercent = "intraday_percent"
        case gapDominant = "gap_dominant"
        case industryName = "industry_name"
        case industryChangePercent = "industry_change_percent"
        case marketChangePercent = "market_change_percent"
    }

    /// "Aerospace & Defense −1.2%" — the comparison that tells a reader whether this
    /// was a company event or a group move.
    public var industryLabel: String? {
        guard let name = industryName, let c = industryChangePercent else { return nil }
        return String(format: "%@ %+.1f%%", name, c)
    }
}

public struct WidgetBasket: Codable, Equatable, Sendable {
    public let direction: String
    public let movedCount: Int
    public let totalCount: Int
    public let factorKind: String?
    public let factorLabel: String?
    public let averageChangePercent: Double?
    public let tickers: [String]
    public let text: String

    enum CodingKeys: String, CodingKey {
        case direction
        case movedCount = "moved_count"
        case totalCount = "total_count"
        case factorKind = "factor_kind"
        case factorLabel = "factor_label"
        case averageChangePercent = "average_change_percent"
        case tickers, text
    }
}

/// What actually crosses the App Group boundary: both modes plus when they were written.
public struct WidgetSnapshotEnvelope: Codable, Equatable, Sendable {
    public var market: WidgetMoverSnapshot?
    public var portfolio: WidgetMoverSnapshot?
    public var writtenAt: Date

    public init(
        market: WidgetMoverSnapshot? = nil,
        portfolio: WidgetMoverSnapshot? = nil,
        writtenAt: Date = Date()
    ) {
        self.market = market
        self.portfolio = portfolio
        self.writtenAt = writtenAt
    }
}

// MARK: - Store

/// Reads and writes the shared snapshot. Safe to use from both processes.
public enum WidgetSnapshotStore {
    private static let log = Logger(subsystem: "com.phan.caydex", category: "widget")

    private static var defaults: UserDefaults? {
        UserDefaults(suiteName: WidgetSharedConfig.appGroupIdentifier)
    }

    private static var encoder: JSONEncoder {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        return e
    }

    /// The wire decoder. Public so the extension's fetcher decodes a live response with
    /// the SAME date strategy the stored envelope uses — a second decoder would be one
    /// `.iso8601` away from silently failing on `as_of` and blanking the tile.
    public static var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }

    /// Reads whatever the app last wrote. Never throws — a widget with no data renders
    /// its empty state, which is a legitimate first-install condition, not an error.
    public static func read() -> WidgetSnapshotEnvelope? {
        guard let defaults else {
            // Almost always a misconfigured App Group: the suite silently returns nil
            // rather than failing, so without this line the widget just looks broken.
            log.error("App Group \(WidgetSharedConfig.appGroupIdentifier) unavailable — check the entitlement on BOTH targets")
            return nil
        }
        guard let data = defaults.data(forKey: WidgetSharedConfig.snapshotKey) else {
            return nil
        }
        do {
            return try decoder.decode(WidgetSnapshotEnvelope.self, from: data)
        } catch {
            // A shape change between an updated app and a not-yet-reloaded widget lands
            // here. Log and return nil so the widget shows its empty state instead of
            // stale garbage.
            log.error("widget snapshot decode failed: \(String(describing: error))")
            return nil
        }
    }

    /// Merges one mode into the stored envelope and asks WidgetKit to redraw.
    ///
    /// Merging rather than replacing matters: the two modes are fetched independently,
    /// and writing a whole envelope from a market-only refresh would wipe the portfolio
    /// snapshot, blanking that widget until the next portfolio fetch.
    @discardableResult
    public static func write(mode: WidgetMode, snapshot: WidgetMoverSnapshot) -> Bool {
        write(mode: mode, snapshot: snapshot, reloading: true)
    }

    /// The same write, from the WIDGET process, WITHOUT asking WidgetKit to redraw.
    ///
    /// ⚠️ THE RELOAD IS THE WHOLE REASON THIS EXISTS. The extension fetches from inside
    /// `timeline(for:in:)`; calling `reloadTimelines()` there asks WidgetKit for a new
    /// timeline while it is building one, which is a loop that spends the day's refresh
    /// allowance and leaves the tile staler than doing nothing. The extension is already
    /// returning the fresh entries directly, so it has nothing to gain from a reload —
    /// it writes only so the NEXT failed fetch has something current to fall back on.
    @discardableResult
    public static func writeFromExtension(
        mode: WidgetMode, snapshot: WidgetMoverSnapshot
    ) -> Bool {
        write(mode: mode, snapshot: snapshot, reloading: false)
    }

    @discardableResult
    private static func write(
        mode: WidgetMode, snapshot: WidgetMoverSnapshot, reloading: Bool
    ) -> Bool {
        guard let defaults else {
            log.error("cannot write widget snapshot — App Group unavailable")
            return false
        }
        var envelope = read() ?? WidgetSnapshotEnvelope()

        // ⚠️ AN EMPTY PAYLOAD MUST NOT REPLACE A GOOD ONE.
        //
        // The backend degrades-never-errors, so a transient FMP or Supabase failure comes
        // back as a perfectly valid HTTP 200 with `headline_mover: null`. Writing that
        // unconditionally replaced a live tile with "Open the app to load today's
        // movers" — an instruction that cannot help, shown WHILE the app is open, from a
        // blip the user never saw. Yesterday's mover, correctly labelled with its own
        // session date, is strictly better than nothing.
        let existing = (mode == .market) ? envelope.market : envelope.portfolio
        if snapshot.isEmpty, let existing, !existing.isEmpty {
            log.warning(
                "widget: ignoring empty \(mode.rawValue, privacy: .public) payload — keeping the last good snapshot"
            )
            return false
        }

        // ⚠️ NOR MAY A SCOPE-MISMATCHED PAYLOAD REPLACE A GOOD ONE.
        //
        // `/widget/portfolio-mover` is `.guestAllowed`, so a call made before the bearer is
        // installed is ANSWERED — as this install's per-install guest, who owns no
        // portfolio — and the backend's documented "degrade, never error" path returns the
        // MARKET payload instead (`endpoints/widget.py`). That is a perfectly valid,
        // non-empty body, so the empty-check above waves it through and market movers land
        // in the portfolio slot, where they persist until some later refresh replaces them.
        //
        // Keeping the previous good snapshot is strictly better: it is the user's real
        // holdings, correctly labelled with its own session date. A first-ever fetch (no
        // existing snapshot) still writes, so someone who genuinely holds nothing gets a
        // populated tile rather than a blank one — `MoversWidget.mismatched` captions that
        // case honestly.
        if mode == .portfolio, snapshot.mode != "portfolio", let existing, !existing.isEmpty {
            log.warning(
                """
                widget: ignoring \(snapshot.mode, privacy: .public)-scope payload for the \
                portfolio slot — keeping the last good holdings snapshot
                """
            )
            return false
        }

        switch mode {
        case .market:    envelope.market = snapshot
        case .portfolio: envelope.portfolio = snapshot
        }
        envelope.writtenAt = Date()

        do {
            defaults.set(try encoder.encode(envelope), forKey: WidgetSharedConfig.snapshotKey)
        } catch {
            log.error("widget snapshot encode failed: \(String(describing: error))")
            return false
        }
        if reloading { reloadTimelines() }
        return true
    }

    /// Clears the PORTFOLIO snapshot when a session ends — see `.claude/rules/auth.md`
    /// §7: a device-global store that survives sign-out hands the next account the
    /// previous user's data, and a portfolio snapshot on the Home Screen is visible
    /// without even unlocking into the app.
    ///
    /// The MARKET snapshot deliberately survives. It comes from a `.public` route, is
    /// identical for every caller, and carries nothing about the user — so §7 does not
    /// reach it. Wiping it too meant the default-configured widget (Market is the
    /// AppIntent default) went blank on sign-out and stayed blank until the user next
    /// backgrounded and re-foregrounded the app, which reads as the sign-out having
    /// broken something.
    public static func clear() {
        guard let defaults else { return }
        var envelope = read() ?? WidgetSnapshotEnvelope()
        envelope.portfolio = nil
        envelope.writtenAt = Date()
        if let market = envelope.market, !market.isEmpty,
           let data = try? encoder.encode(envelope) {
            defaults.set(data, forKey: WidgetSharedConfig.snapshotKey)
        } else {
            defaults.removeObject(forKey: WidgetSharedConfig.snapshotKey)
        }
        reloadTimelines()
    }

    /// Removes BOTH modes. For a deliberate reset, not for a session ending.
    public static func clearAll() {
        defaults?.removeObject(forKey: WidgetSharedConfig.snapshotKey)
        reloadTimelines()
    }

    public static func reloadTimelines() {
        #if canImport(WidgetKit)
        WidgetCenter.shared.reloadTimelines(ofKind: WidgetSharedConfig.moversKind)
        #endif
    }

    public enum WidgetMode: String, Sendable {
        case market
        case portfolio
    }
}
