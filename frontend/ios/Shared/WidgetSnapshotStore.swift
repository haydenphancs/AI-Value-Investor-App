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
    public let asOf: Date
    public let marketSession: String
    public let isStale: Bool
    public let headlineMover: WidgetMover?
    public let basket: WidgetBasket?
    public let marketStory: String?
    public let universeLabel: String?

    enum CodingKeys: String, CodingKey {
        case mode
        case asOf = "as_of"
        case marketSession = "market_session"
        case isStale = "is_stale"
        case headlineMover = "headline_mover"
        case basket
        case marketStory = "market_story"
        case universeLabel = "universe_label"
    }
}

public struct WidgetMover: Codable, Equatable, Sendable {
    public let ticker: String
    public let companyName: String?
    public let changePercent: Double?
    public let price: Double?
    public let tier: String?
    public let z: Double?
    public let reason: WidgetReason

    enum CodingKeys: String, CodingKey {
        case ticker
        case companyName = "company_name"
        case changePercent = "change_percent"
        case price, tier, z, reason
    }

    /// `nil` ⇒ the number is HIDDEN, never rendered as 0.0%. A fabricated flat reading
    /// on a stock that actually moved is worse than no reading.
    public var formattedChange: String? {
        guard let c = changePercent else { return nil }
        return String(format: "%+.2f%%", c)
    }

    /// `-0.0 > 0` is false, so a signed zero cannot paint a gainer.
    public var isPositive: Bool { (changePercent ?? 0) > 0 }
}

/// Provenance of the reason line. The app renders a "Why it moved" treatment ONLY for
/// `.catalyst`; the other two are deliberately styled as what they are.
public enum WidgetReasonKind: String, Codable, Sendable {
    /// Web-grounded and source-cited. May be framed as *why*.
    case catalyst
    /// A news headline. Says what is going on; establishes no causal link.
    case context
    /// Arithmetic only. Always available, never wrong.
    case none

    /// Unknown values decode to `.context` rather than throwing: a backend that adds a
    /// fourth kind must not break every already-installed widget.
    public init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = WidgetReasonKind(rawValue: raw) ?? .context
    }
}

public struct WidgetReason: Codable, Equatable, Sendable {
    public let kind: WidgetReasonKind
    public let text: String
    public let catalystTag: String?

    enum CodingKeys: String, CodingKey {
        case kind, text
        case catalystTag = "catalyst_tag"
    }

    /// True only when the backend established a cause. Gates the "Why it moved" label.
    public var isCausal: Bool { kind == .catalyst }
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

    private static var decoder: JSONDecoder {
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
        guard let defaults else {
            log.error("cannot write widget snapshot — App Group unavailable")
            return false
        }
        var envelope = read() ?? WidgetSnapshotEnvelope()
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
        reloadTimelines()
        return true
    }

    /// Clears both modes. Called when a session ends — see `.claude/rules/auth.md` §7:
    /// a device-global store that survives sign-out hands the next account the previous
    /// user's data. A portfolio snapshot on the Home Screen is exactly that, and it is
    /// visible without even unlocking into the app.
    public static func clear() {
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
