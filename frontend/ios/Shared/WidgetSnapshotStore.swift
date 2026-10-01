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
//  ⚠️ MARKET MODE NOW FETCHES ITSELF — the paragraph above still governs, narrowly.
//  `/widget/market-mover` is no longer a `.public` route (End-User Display Rights permit FMP
//  data only through an authenticated platform), so the extension authenticates it with a
//  WIDGET TOKEN: long-lived, scoped to that one market-wide route, published into this App
//  Group by the app, and refused as a session bearer everywhere else. That does not weaken §8
//  — the extension still never reads the Keychain and still holds no session, so it cannot
//  diverge from `APIClient` and has nothing to refresh. See `WidgetMarketFetcher` and the
//  WIDGET TOKEN block in `backend/app/core/security.py`.
//
//  PORTFOLIO MODE NEVER CAN, and the widget token cannot reach it — that is precisely why a
//  long-lived credential is defensible for the other route.
//
//  ONE KEY PER MODE (v2)
//  The two modes used to share one envelope that both processes read, modified and wrote
//  back. The extension writing a market refresh therefore re-encoded the PORTFOLIO bytes it
//  had read seconds earlier — so a sign-out `clearAll()` landing in between was undone, and
//  the previous account's holdings came back. Now each mode has its own key, the extension
//  writes only the market key, and the Holdings slot stores its OWNER beside the snapshot in
//  one value, so "whose holdings are these" can never be torn from the holdings themselves.
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
///
/// `nonisolated`: pure constants, read by the nonisolated `WidgetSharedDefaults` and by the
/// widget's nonisolated `AppIntent.perform()`.
nonisolated public enum WidgetSharedConfig {
    public static let appGroupIdentifier = "group.com.phan.caydex"
    /// The LEGACY single envelope (both modes in one blob). Read only as the market fallback
    /// until the app migrates it, and always removed by `clearAll()`. Still named
    /// `snapshotKey` because the sign-out guard in `test_ios_sign_in_wall.py` pins its removal.
    public static let snapshotKey = "widget.movers.snapshot.v1"
    /// The Market slot: a bare `WidgetMoverSnapshot`. The only key the extension writes.
    public static let snapshotKeyV2Market = "widget.movers.snapshot.v2.market"
    /// The Holdings slot: a `WidgetPortfolioSlot` — the snapshot AND the account it was
    /// fetched for, stored as one value.
    public static let snapshotKeyV2Portfolio = "widget.movers.snapshot.v2.portfolio"
    /// The in-tile toggle's choices, one per CONFIGURED mode — see `WidgetModeOverride`.
    /// A legacy build stored a single global string here; it reads as "no override".
    public static let modeOverrideKey = "widget.movers.modeOverride"
    /// Widget kind, shared so the app can reload exactly this widget.
    public static let moversKind = "CaydexMoversWidget"
}

/// Why a decode dropped something. One logger for the whole wire model, so a missing row
/// on a Home Screen is diagnosable from Console alone.
private let widgetDecodeLog = Logger(subsystem: "com.phan.caydex", category: "widget")

// MARK: - Lossy arrays

/// An array that survives a bad ELEMENT: the element is skipped and logged, the rest stand.
///
/// ⚠️ A STRICT `[WidgetMover]` WAS ALL-OR-NOTHING. One runner the decoder could not read
/// failed the array, the array failed the snapshot, and `read()` returned nil — so a single
/// malformed row blanked the whole tile to its placeholder, on a Home Screen, with no error
/// surface and no retry. Decode-only: encoding stays a plain array, so the stored bytes are
/// exactly the wire shape. Order is preserved — the backend's ranking is the order shown.
struct LossyArray<Element: Decodable>: Decodable {
    let elements: [Element]

    init(from decoder: Decoder) throws {
        var container: UnkeyedDecodingContainer
        do {
            container = try decoder.unkeyedContainer()
        } catch {
            // Not an array at all — a contract break, but one field's worth of it: the rest
            // of the snapshot still renders.
            widgetDecodeLog.warning(
                "widget: expected an array of \(String(describing: Element.self), privacy: .public) at \(decoder.codingPath.map(\.stringValue).joined(separator: "."), privacy: .public) — read as empty: \(String(describing: error), privacy: .public)"
            )
            elements = []
            return
        }
        var out: [Element] = []
        while !container.isAtEnd {
            do {
                // `LossyElement` never throws for a bad VALUE, so a successful decode
                // always advances the container past the element.
                let element = try container.decode(LossyElement<Element>.self)
                if let value = element.value { out.append(value) }
            } catch {
                // The container could not even hand the element over. Stop rather than
                // spin on an index that will not advance: the elements read so far stand.
                widgetDecodeLog.warning(
                    "widget: stopped reading a \(String(describing: Element.self), privacy: .public) array at \(container.currentIndex, privacy: .public): \(String(describing: error), privacy: .public)"
                )
                break
            }
        }
        elements = out
    }
}

/// One never-throwing element of a `LossyArray`. A bad element decodes to nil and is LOGGED —
/// silent leniency would make "a row never showed up" undiagnosable.
private struct LossyElement<Wrapped: Decodable>: Decodable {
    let value: Wrapped?

    init(from decoder: Decoder) throws {
        do {
            value = try Wrapped(from: decoder)
        } catch {
            value = nil
            widgetDecodeLog.warning(
                "widget: dropped one \(String(describing: Wrapped.self), privacy: .public) at \(decoder.codingPath.map(\.stringValue).joined(separator: "."), privacy: .public): \(String(describing: error), privacy: .public)"
            )
        }
    }
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
    /// How the market itself did. Absent when every upstream leg failed.
    public let marketContext: WidgetMarketContext?
    /// Holdings: the biggest |%| mover. Market: still sent for installed builds, never
    /// rendered by this one — the Market tile is not a mover tile.
    public let headlineMover: WidgetMover?
    /// The one-sentence read on the whole market — Market mode only, and only when the
    /// backend's roll-up is dated to this session. Absent is NORMAL: the tile then
    /// leads with the asset numbers, which are always current.
    public let marketBrief: WidgetMarketBrief?
    public let basket: WidgetBasket?
    /// Next few movers in ranking order. Kept for an old backend's Holdings payload, which
    /// has no `top_gainers` / `top_losers`.
    public let runnersUp: [WidgetMover]

    // ── Holdings mode only ──

    /// The active group's name. nil ⇒ "My Holdings" (see `holdingsTitle`).
    public let groupName: String?
    /// N = the group's size; 0 = an authoritative empty group; nil = DEGRADED (holdings or
    /// quotes unreadable) — `WidgetSnapshotStore.write` keeps a good snapshot over that.
    public let holdingsCount: Int?
    public let upCount: Int?
    public let downCount: Int?
    public let flatCount: Int?
    /// ≤5 each, the headline excluded. Gainers by change descending, losers ascending.
    public let topGainers: [WidgetMover]
    public let topLosers: [WidgetMover]

    // ── Market mode only ──

    /// The Home Market Pulse, in its order — S&P 500, Nasdaq, Dow, Russell 2000, Gold,
    /// Bitcoin. Empty on an old backend; the tile then falls back to `marketContext.indices`.
    public let marketAssets: [WidgetIndex]

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
        case groupName = "group_name"
        case holdingsCount = "holdings_count"
        case upCount = "up_count"
        case downCount = "down_count"
        case flatCount = "flat_count"
        case topGainers = "top_gainers"
        case topLosers = "top_losers"
        case marketAssets = "market_assets"
    }

    /// ⚠️ EVERY OPTIONAL FIELD MUST BE READ WITH `decodeIfPresent`, WITH A DEFAULT.
    ///
    /// The widget ships in an app update; the backend deploys independently. A new app
    /// running against a not-yet-deployed backend must still render — and a decode
    /// failure here has no error surface at all: `read()` returns nil and the user gets
    /// the placeholder on their Home Screen, with no retry and no crash report anyone
    /// would think to send. `runners_up` set this precedent; keep it for everything.
    /// Arrays go through `LossyArray`, so one bad row costs that row, not the tile.
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
        runnersUp = try c.decodeIfPresent(LossyArray<WidgetMover>.self, forKey: .runnersUp)?.elements ?? []
        groupName = try c.decodeIfPresent(String.self, forKey: .groupName)
        holdingsCount = try c.decodeIfPresent(Int.self, forKey: .holdingsCount)
        upCount = try c.decodeIfPresent(Int.self, forKey: .upCount)
        downCount = try c.decodeIfPresent(Int.self, forKey: .downCount)
        flatCount = try c.decodeIfPresent(Int.self, forKey: .flatCount)
        topGainers = try c.decodeIfPresent(LossyArray<WidgetMover>.self, forKey: .topGainers)?.elements ?? []
        topLosers = try c.decodeIfPresent(LossyArray<WidgetMover>.self, forKey: .topLosers)?.elements ?? []
        marketAssets = try c.decodeIfPresent(LossyArray<WidgetIndex>.self, forKey: .marketAssets)?.elements ?? []
    }

    public init(
        mode: String, asOf: Date, marketSession: String,
        sessionDate: String? = nil, sessionLabel: String? = nil, scopeLabel: String? = nil,
        marketBrief: WidgetMarketBrief? = nil,
        marketContext: WidgetMarketContext? = nil,
        headlineMover: WidgetMover?, basket: WidgetBasket?, runnersUp: [WidgetMover] = [],
        groupName: String? = nil, holdingsCount: Int? = nil,
        upCount: Int? = nil, downCount: Int? = nil, flatCount: Int? = nil,
        topGainers: [WidgetMover] = [], topLosers: [WidgetMover] = [],
        marketAssets: [WidgetIndex] = []
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
        self.groupName = groupName
        self.holdingsCount = holdingsCount
        self.upCount = upCount
        self.downCount = downCount
        self.flatCount = flatCount
        self.topGainers = topGainers
        self.topLosers = topLosers
        self.marketAssets = marketAssets
    }

    /// True when the payload carries no mover at all. LEGACY — what a snapshot is worth
    /// keeping is `hasContent(for:)`, which knows the two modes render different things.
    public var isEmpty: Bool { headlineMover == nil && runnersUp.isEmpty }

    /// Whether this payload is worth storing over (or rendering instead of) an older one.
    ///
    /// The backend degrades-never-errors, so an upstream failure answers HTTP 200 with
    /// little or nothing in it. Each mode has its own idea of "nothing":
    ///   • Market renders assets and the brief (a legacy mover only for an old backend).
    ///   • Holdings renders a mover — or, with `holdingsCount` set, an HONEST empty state
    ///     ("No holdings in Growth yet", "No prices for your 3 holdings"). Only a nil
    ///     count with no mover is degraded: it says nothing about the group at all.
    public func hasContent(for mode: WidgetSnapshotStore.WidgetMode) -> Bool {
        switch mode {
        case .market:
            return !marketAssets.isEmpty || marketBrief != nil || headlineMover != nil
        case .portfolio:
            return headlineMover != nil || holdingsCount != nil
        }
    }

    /// The Holdings header's name. A blank name is the backend's "" normalised late, not a
    /// name — "My Holdings" is what the tile always said.
    public var holdingsTitle: String {
        if let name = groupName?.trimmingCharacters(in: .whitespacesAndNewlines), !name.isEmpty {
            return name
        }
        return "My Holdings"
    }

    /// Holdings with no current-session price: N − up − down − flat. nil unless every term
    /// is known and the arithmetic is sane — a negative "unpriced" count is a contract bug,
    /// not something to print.
    public var unpricedCount: Int? {
        guard let n = holdingsCount, let up = upCount, let down = downCount, let flat = flatCount
        else { return nil }
        let rest = n - up - down - flat
        return rest >= 0 ? rest : nil
    }
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
        // Lenient: nothing renders this, and the backend passes the card's own timestamp
        // through. A fractional-second or zone-less value must cost this one field — not
        // the brief, the snapshot and the whole Market tile.
        do {
            generatedAt = try c.decodeIfPresent(Date.self, forKey: .generatedAt)
        } catch {
            generatedAt = nil
            widgetDecodeLog.warning(
                "widget: unreadable market_brief.generated_at — dropped: \(String(describing: error), privacy: .public)"
            )
        }
    }

    public init(headline: String, sentiment: String? = nil, generatedAt: Date? = nil) {
        self.headline = headline
        self.sentiment = sentiment
        self.generatedAt = generatedAt
    }
}

/// One index in the market band, or one asset in the Market tile's grid.
///
/// `label` comes from the SERVER on purpose: an already-installed widget cannot learn
/// that a newly added symbol is called "Russell 2000" without an app update, so the
/// client must never map symbols to names itself.
public struct WidgetIndex: Codable, Equatable, Sendable {
    public let symbol: String
    /// The honest fund name ("S&P 500 ETF"). The ONLY label shown beside a price.
    public let label: String
    public let changePercent: Double?
    public let price: Double?
    /// The cramped-grid label ("S&P 500"), for a cell that draws only the %. ⚠️ Never beside
    /// a price: an ETF's ~$650 under "S&P 500" reads as the index being off by 10×.
    public let shortLabel: String?
    /// A round-the-clock asset (Bitcoin): its change is a rolling 24 h, tagged "24h".
    public let rolling24h: Bool?
    /// "etf" | "crypto" | "index" | "commodity" | "stock" — SERVER-owned, like `label`, so a
    /// tap-through can open the right detail screen. nil ⇒ unknown: link nowhere, never guess.
    public let assetType: String?

    enum CodingKeys: String, CodingKey {
        case symbol, label, price
        case changePercent = "change_percent"
        case shortLabel = "short_label"
        case rolling24h = "rolling_24h"
        case assetType = "asset_type"
    }

    public init(
        symbol: String, label: String, changePercent: Double? = nil, price: Double? = nil,
        shortLabel: String? = nil, rolling24h: Bool? = nil, assetType: String? = nil
    ) {
        self.symbol = symbol
        self.label = label
        self.changePercent = changePercent
        self.price = price
        self.shortLabel = shortLabel
        self.rolling24h = rolling24h
        self.assetType = assetType
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
    public var isRolling24h: Bool { rolling24h == true }
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
        indices = try c.decodeIfPresent(LossyArray<WidgetIndex>.self, forKey: .indices)?.elements ?? []
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
    /// A round-the-clock asset (crypto): its change is a rolling 24 h, so the row is tagged
    /// "24h" rather than dated by the tile's equity footer.
    public let rolling24h: Bool?
    /// "etf" | "crypto" | "stock", from the server's own quote flags. nil ⇒ unknown — a
    /// tap-through then resolves by symbol, never by a guess made here.
    public let assetType: String?

    enum CodingKeys: String, CodingKey {
        case ticker
        case companyName = "company_name"
        case changePercent = "change_percent"
        case price, tier, z, cause, context
        case rolling24h = "rolling_24h"
        case assetType = "asset_type"
    }

    /// Explicit so the gallery and preview samples keep compiling as fields are added —
    /// a synthesized memberwise init would make every new Optional `let` a required label.
    public init(
        ticker: String, companyName: String? = nil, changePercent: Double?,
        price: Double? = nil, tier: String? = nil, z: Double? = nil,
        cause: WidgetCause, context: WidgetMoveContext, rolling24h: Bool? = nil,
        assetType: String? = nil
    ) {
        self.ticker = ticker
        self.companyName = companyName
        self.changePercent = changePercent
        self.price = price
        self.tier = tier
        self.z = z
        self.cause = cause
        self.context = context
        self.rolling24h = rolling24h
        self.assetType = assetType
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

    public var isRolling24h: Bool { rolling24h == true }

    /// What the tile PRINTS: a crypto pair's base symbol ("BTC" for "BTCUSD"), the ticker
    /// otherwise. A Small tile cannot hold "BTCUSD" beside "+12.34%" — rendered, it cut the
    /// ticker to "B…" (and to "…" on an SE). Display only: links and ids keep `ticker`.
    public var displayTicker: String {
        guard isRolling24h, ticker.count > 3, ticker.hasSuffix("USD") else { return ticker }
        return String(ticker.dropLast(3))
    }

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
    /// True at `as_of` — and it may say "today".
    public let detail: String
    /// The same attribution worded for a LATER reader ("…in Tuesday's news"), never "today".
    /// Headline only; nil on an old backend. Shown once the snapshot is aged — by its session
    /// date, or for a round-the-clock headline by the ET day it was built (`isPriorETDay`) —
    /// so the cause cannot contradict the "Tue close" footer beside it.
    public let detailAged: String?

    enum CodingKeys: String, CodingKey {
        case kind, tag, detail
        case detailAged = "detail_aged"
    }

    public init(kind: WidgetCauseKind, tag: String? = nil, detail: String, detailAged: String? = nil) {
        self.kind = kind
        self.tag = tag
        self.detail = detail
        self.detailAged = detailAged
    }
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

/// Both modes, as the widget reads them. Assembled by `read()` from the per-mode keys; the
/// legacy v1 key stored exactly this shape, which is why it is still `Codable`.
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

/// What the Holdings key stores: the snapshot and the account it was fetched for, in ONE
/// value. Not a wire type — `owner` is deliberately not a `WidgetMoverSnapshot` field (the
/// backend never sends it, and the parity test holds Swift's keys to the backend's).
struct WidgetPortfolioSlot: Codable {
    /// The JWT `sub` of the session that fetched it. nil only for a write that could not
    /// tell, which `write()` treats as belonging to nobody in particular.
    let owner: String?
    let snapshot: WidgetMoverSnapshot
}

// MARK: - Store

/// Reads and writes the shared snapshots. Safe to use from both processes.
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
    /// the SAME date strategy the stored snapshots use — a second decoder would be one
    /// `.iso8601` away from silently failing on `as_of` and blanking the tile.
    public static var decoder: JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }

    /// Reads whatever was last written. Never throws — a widget with no data renders its
    /// empty state, which is a legitimate first-install condition, not an error.
    ///
    /// Each mode decodes on its own, so an unreadable Holdings slot no longer costs the
    /// Market one (or the reverse). The legacy v1 envelope is consulted for MARKET only,
    /// and only until the app migrates it: its Holdings half carries no owner, so it cannot
    /// be shown to whoever is signed in now.
    public static func read() -> WidgetSnapshotEnvelope? {
        guard let defaults else {
            // Almost always a misconfigured App Group: the suite silently returns nil
            // rather than failing, so without this line the widget just looks broken.
            log.error("App Group \(WidgetSharedConfig.appGroupIdentifier) unavailable — check the entitlement on BOTH targets")
            return nil
        }
        let market = storedMarket(defaults)
        let portfolio = storedPortfolio(defaults)?.snapshot
        guard market != nil || portfolio != nil else { return nil }
        let newest = [market?.asOf, portfolio?.asOf].compactMap { $0 }.max() ?? Date()
        return WidgetSnapshotEnvelope(market: market, portfolio: portfolio, writtenAt: newest)
    }

    /// The account the stored Holdings snapshot was fetched for. nil when there is no
    /// Holdings snapshot, or it was written without one (the v1 envelope never had one).
    public static func portfolioOwner() -> String? {
        guard let defaults else { return nil }
        return storedPortfolio(defaults)?.owner
    }

    /// Whether ANY widget state exists — a snapshot under any key, or a widget token.
    ///
    /// The backup-restore check: a restore to a new phone carries the App Group (token and
    /// snapshots) but not the `ThisDeviceOnly` Keychain session, so a launch with no stored
    /// session and `hasAnyState` true has someone else's market access and holdings on it.
    public static var hasAnyState: Bool {
        if WidgetAPIConfig.widgetToken != nil { return true }
        guard let defaults else { return false }
        let keys = [
            WidgetSharedConfig.snapshotKey,
            WidgetSharedConfig.snapshotKeyV2Market,
            WidgetSharedConfig.snapshotKeyV2Portfolio,
        ]
        return keys.contains { defaults.object(forKey: $0) != nil }
    }

    /// Stores one mode's payload and asks WidgetKit to redraw.
    ///
    /// - Parameter owner: the account the Holdings payload was fetched for (the JWT `sub`).
    ///   Ignored for the market slot, which is the same for everyone.
    /// - Returns: whether the payload was stored.
    @discardableResult
    public static func write(
        mode: WidgetMode, snapshot: WidgetMoverSnapshot, owner: String? = nil
    ) -> Bool {
        write(mode: mode, snapshot: snapshot, owner: owner, reloading: true, fromExtension: false)
    }

    /// The same write, from the WIDGET process, WITHOUT asking WidgetKit to redraw — and
    /// for the MARKET slot only.
    ///
    /// ⚠️ THE RELOAD IS THE WHOLE REASON THIS EXISTS. The extension fetches from inside
    /// `timeline(for:in:)`; calling `reloadTimelines()` there asks WidgetKit for a new
    /// timeline while it is building one, which is a loop that spends the day's refresh
    /// allowance and leaves the tile staler than doing nothing. The extension is already
    /// returning the fresh entries directly, so it has nothing to gain from a reload —
    /// it writes only so the NEXT failed fetch has something current to fall back on.
    ///
    /// ⚠️ AND IT RE-CHECKS THE WIDGET TOKEN RIGHT BEFORE STORING. The fetch can take 12 s; a
    /// sign-out in that window clears the token and every snapshot, and the 200 still
    /// arrives (widget tokens are not revocable). Storing it would put FMP prices back on a
    /// signed-out Home Screen for good.
    @discardableResult
    public static func writeFromExtension(
        mode: WidgetMode, snapshot: WidgetMoverSnapshot
    ) -> Bool {
        guard mode == .market else {
            log.error("widget: the extension may write only the market slot — refused \(mode.rawValue, privacy: .public)")
            return false
        }
        return write(mode: .market, snapshot: snapshot, owner: nil, reloading: false, fromExtension: true)
    }

    private enum SlotWrite {
        case stored
        /// The payload was refused, but the slot was removed (a different account's holdings).
        case cleared
        case refused
    }

    @discardableResult
    private static func write(
        mode: WidgetMode, snapshot: WidgetMoverSnapshot, owner: String?,
        reloading: Bool, fromExtension: Bool
    ) -> Bool {
        guard let defaults else {
            log.error("cannot write widget snapshot (\(mode.rawValue, privacy: .public)) — App Group unavailable")
            return false
        }
        let outcome: SlotWrite
        switch mode {
        case .market:
            outcome = writeMarket(snapshot, fromExtension: fromExtension, defaults: defaults)
        case .portfolio:
            outcome = writePortfolio(snapshot, owner: owner, defaults: defaults)
        }
        if reloading, outcome != .refused { reloadTimelines() }
        return outcome == .stored
    }

    private static func writeMarket(
        _ snapshot: WidgetMoverSnapshot, fromExtension: Bool, defaults: UserDefaults
    ) -> SlotWrite {
        // ⚠️ A DEGRADED PAYLOAD MUST NOT REPLACE A GOOD ONE.
        //
        // The backend degrades-never-errors, so a transient FMP or Supabase failure comes
        // back as a perfectly valid HTTP 200 with nothing in it. Yesterday's numbers,
        // correctly labelled with their own session date, are strictly better than a blank.
        if !snapshot.hasContent(for: .market),
           let existing = storedMarket(defaults), existing.hasContent(for: .market) {
            log.warning("widget: ignoring a market payload with no content — keeping the last good snapshot")
            return .refused
        }
        let data: Data
        do {
            data = try encoder.encode(snapshot)
        } catch {
            log.error("widget market snapshot encode failed: \(String(describing: error), privacy: .public)")
            return .refused
        }
        // The extension's sign-out fence — see `writeFromExtension`. Checked at the last
        // moment before the store, after everything that could have taken time.
        if fromExtension, WidgetAPIConfig.widgetToken == nil {
            log.warning("widget: market refresh discarded — the session ended while it was in flight")
            return .refused
        }
        defaults.set(data, forKey: WidgetSharedConfig.snapshotKeyV2Market)
        return .stored
    }

    private static func writePortfolio(
        _ snapshot: WidgetMoverSnapshot, owner: String?, defaults: UserDefaults
    ) -> SlotWrite {
        let existing = storedPortfolio(defaults)
        // A different account's holdings protect nothing: whatever arrives replaces them.
        // An unowned slot (stored owner nil) counts as different from any real owner. Case-
        // insensitive: both are JWT `sub` uuids, and `AppState` compares them the same way.
        let ownerChanged = existing != nil && existing?.owner?.lowercased() != owner?.lowercased()

        // ⚠️ A MARKET-SCOPED PAYLOAD IS NEVER THE USER'S HOLDINGS.
        //
        // This refusal used to rest on `/widget/portfolio-mover` being `.guestAllowed`: a
        // call made before the bearer was installed was answered for a holdings-less guest
        // and degraded to the MARKET payload. That premise is dead — the route is
        // `.signInRequired` and APIClient refuses it without a token. What is still real is
        // an OLD backend (before the empty-group contract shipped) answering an empty group,
        // or an unreadable holdings read, with that same market payload. The current backend
        // answers both with `mode == "portfolio"` (`holdings_count` 0 or nil), which the
        // rules below handle honestly — and the Holdings tile never renders market data
        // under a holdings header.
        if snapshot.mode != "portfolio" {
            if ownerChanged {
                defaults.removeObject(forKey: WidgetSharedConfig.snapshotKeyV2Portfolio)
                log.warning("widget: market-scoped payload for a different account's Holdings slot — slot cleared")
                return .cleared
            }
            log.warning(
                "widget: ignoring \(snapshot.mode, privacy: .public)-scope payload for the Holdings slot"
            )
            return .refused
        }

        // ⚠️ A DEGRADED HOLDINGS PAYLOAD MUST NOT REPLACE A GOOD ONE OF THE SAME ACCOUNT.
        //
        // `holdings_count == nil` with no mover means the holdings or the quotes were
        // unreadable — not that the group is empty. An authoritative empty group (count 0),
        // a switched group and an unpriceable one (count N, no mover) all carry a count, so
        // they DO replace the slot: freezing the old group's movers there was the bug.
        if !ownerChanged, !snapshot.hasContent(for: .portfolio),
           let existing, existing.snapshot.hasContent(for: .portfolio) {
            log.warning("widget: ignoring a degraded holdings payload — keeping the last good snapshot")
            return .refused
        }

        do {
            let data = try encoder.encode(WidgetPortfolioSlot(owner: owner, snapshot: snapshot))
            defaults.set(data, forKey: WidgetSharedConfig.snapshotKeyV2Portfolio)
        } catch {
            log.error("widget holdings snapshot encode failed: \(String(describing: error), privacy: .public)")
            return .refused
        }
        if ownerChanged {
            log.info("widget: Holdings slot replaced for a different account")
        }
        return .stored
    }

    /// Clears the HOLDINGS snapshot only — "this account's holdings ended, the session did
    /// not": an account switch, or a slot whose owner is not the signed-in account.
    ///
    /// ⚠️ **Not the sign-out path, and must not be restored as one.** The market snapshot is
    /// FMP data, and End-User Display Rights permit it only "through the Licensee's
    /// authenticated platform" — so a signed-out phone keeping it on its Home Screen is a
    /// contract breach. A session ending uses `clearAll()`.
    public static func clearPortfolio() {
        guard let defaults else {
            log.error("cannot clear the widget Holdings snapshot — App Group unavailable")
            return
        }
        defaults.removeObject(forKey: WidgetSharedConfig.snapshotKeyV2Portfolio)
        reloadTimelines()
    }

    /// Removes EVERYTHING: both modes, the legacy envelope and the in-tile toggle's choices.
    /// **This is what a session ending uses** — see `clearPortfolio()` for why the market
    /// snapshot may not be kept, and `WidgetRefreshService.clearForEndedSession()`.
    ///
    /// The toggle's choices go too: they are this person's preference, and the next account
    /// to sign in on the device should get the tiles as they were configured.
    public static func clearAll() {
        if let defaults {
            defaults.removeObject(forKey: WidgetSharedConfig.snapshotKey)
            defaults.removeObject(forKey: WidgetSharedConfig.snapshotKeyV2Market)
            defaults.removeObject(forKey: WidgetSharedConfig.snapshotKeyV2Portfolio)
            defaults.removeObject(forKey: WidgetSharedConfig.modeOverrideKey)
        } else {
            log.error("cannot clear the widget snapshots — App Group unavailable")
        }
        reloadTimelines()
    }

    /// One-time move from the v1 envelope to the per-mode keys. The APP calls this in
    /// `configure`, before its first widget refresh.
    ///
    /// - The v1 MARKET half moves to the v2 market key, unless that key already holds one.
    /// - The v1 HOLDINGS half is DROPPED: it carries no owner, so there is no telling whose
    ///   holdings they are, and the next refresh replaces them anyway.
    /// - The v1 key is then deleted, which also ends `read()`'s market fallback to it.
    public static func migrateLegacyIfNeeded() {
        guard let defaults else {
            log.error("cannot migrate the widget snapshot — App Group unavailable")
            return
        }
        guard let data = defaults.data(forKey: WidgetSharedConfig.snapshotKey) else { return }

        if defaults.data(forKey: WidgetSharedConfig.snapshotKeyV2Market) == nil {
            do {
                let legacy = try decoder.decode(WidgetSnapshotEnvelope.self, from: data)
                if let market = legacy.market, market.hasContent(for: .market) {
                    defaults.set(try encoder.encode(market), forKey: WidgetSharedConfig.snapshotKeyV2Market)
                }
            } catch {
                log.warning("widget: legacy snapshot unreadable during migration — dropped: \(String(describing: error), privacy: .public)")
            }
        }
        defaults.removeObject(forKey: WidgetSharedConfig.snapshotKey)
        log.info("widget: migrated the v1 snapshot to per-mode keys (unowned holdings dropped)")
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

    // MARK: - Slot reads

    /// The Market slot: the v2 key, else the legacy v1 envelope's market half while it exists.
    private static func storedMarket(_ defaults: UserDefaults) -> WidgetMoverSnapshot? {
        if let data = defaults.data(forKey: WidgetSharedConfig.snapshotKeyV2Market) {
            do {
                return try decoder.decode(WidgetMoverSnapshot.self, from: data)
            } catch {
                // A shape change between an updated app and a not-yet-reloaded widget lands
                // here. Log and fall through, so the tile shows an older reading or its empty
                // state instead of stale garbage.
                log.error("widget market snapshot decode failed: \(String(describing: error), privacy: .public)")
            }
        }
        guard let legacy = defaults.data(forKey: WidgetSharedConfig.snapshotKey) else { return nil }
        do {
            return try decoder.decode(WidgetSnapshotEnvelope.self, from: legacy).market
        } catch {
            log.error("widget legacy snapshot decode failed: \(String(describing: error), privacy: .public)")
            return nil
        }
    }

    /// The Holdings slot — v2 only. The v1 Holdings half is never shown (it has no owner).
    private static func storedPortfolio(_ defaults: UserDefaults) -> WidgetPortfolioSlot? {
        guard let data = defaults.data(forKey: WidgetSharedConfig.snapshotKeyV2Portfolio) else {
            return nil
        }
        do {
            return try decoder.decode(WidgetPortfolioSlot.self, from: data)
        } catch {
            log.error("widget holdings snapshot decode failed: \(String(describing: error), privacy: .public)")
            return nil
        }
    }
}
