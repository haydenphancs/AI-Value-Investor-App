//
//  NotificationRouter.swift
//  ios
//
//  Turns a notification payload into a destination. Pure — no UIKit, no SwiftUI, no
//  state — so every routing decision is testable without launching the app.
//
//  WHAT THIS REPLACES
//  ------------------
//  The tap handler used to read exactly one key:
//
//      guard let ticker = info["ticker"] as? String else { return }
//      PushNotificationManager.shared.handleTap(ticker: ticker)
//
//  Three problems with that, all of which shipped:
//
//    1. It read `info["kind"]` and then ignored it, so every notification landed in the
//       same place. A "your report is ready" push opened a ticker screen, not the report.
//    2. The consumer hardcoded `type: .stock`, so a crypto or ETF alert opened
//       `TickerDetailView` — the wrong screen, showing stock fundamentals for a coin.
//    3. A payload without a `ticker` was a silent no-op: the banner was tappable, the tap
//       did nothing, and nothing was logged.
//
//  Every route below now degrades to `.inbox` rather than to nothing. A tap that lands
//  SOMEWHERE useful is always better than a tap that appears broken.
//

import Foundation

extension Notification.Name {
    /// Posted whenever an authoritative unread count is observed (an inbox load, a
    /// mark-read, or a badge value arriving on a push). `iosApp` observes it and writes
    /// `AppState.unreadNotificationCount`, which drives the tab-bar badge.
    ///
    /// A broadcast rather than a direct write because the inbox ViewModel is an
    /// `ObservableObject` with no AppState reference, and reaching for a global from
    /// inside it would violate the "dependencies arrive via init" rule.
    static let caydexNotificationUnreadChanged =
        Notification.Name("caydexNotificationUnreadChanged")
}

/// Where inside a ticker screen a tap should land.
///
/// Separate from `NotificationRoute` because `route` names the destination FAMILY (which
/// screen) while this names a location within it. Folding them together would mean one
/// route case per screen×tab combination — `ticker`, `tickerHolders`, `tickerFinancials`,
/// … — and `test_every_registered_kind_is_nameable_by_the_ios_router` demands a matching
/// literal in this file for each one.
///
/// ⚠️ ONLY the STOCK screen (`TickerDetailTab`) has Financials and Holders. Crypto, ETF,
/// index and commodity top out at Overview/News/Analysis, so a tab is a HINT: an asset
/// type that lacks it falls back to its own default rather than failing.
struct TickerDestination: Equatable, Hashable, Sendable {
    /// `nil` = the screen's own default tab.
    let tab: TickerDetailTab?
    /// Sub-tab within `tab`. Only Holders has any.
    let section: RecentActivitiesTab?

    static let `default` = TickerDestination(tab: nil, section: nil)

    /// Parse the backend's lowercase `tab` / `section` values.
    ///
    /// The backend spells these lowercase; the Swift `rawValue`s are title-case
    /// ("Holders"), so match on a lowercased comparison rather than `init(rawValue:)`.
    /// Anything unrecognised becomes `nil` — a newer backend naming a tab this build has
    /// never heard of must land on the default screen, not fail to open.
    init(tabRaw: String?, sectionRaw: String?) {
        func match<T: CaseIterable & RawRepresentable>(_ raw: String?) -> T?
        where T.RawValue == String {
            guard let raw = raw?.trimmingCharacters(in: .whitespacesAndNewlines).lowercased(),
                  !raw.isEmpty else { return nil }
            return T.allCases.first { $0.rawValue.lowercased() == raw }
        }
        let parsedTab: TickerDetailTab? = match(tabRaw)
        self.tab = parsedTab
        // A section without its tab is meaningless, and acting on it would select a
        // sub-tab on a screen the user did not ask for.
        self.section = parsedTab == nil ? nil : match(sectionRaw)
    }

    init(tab: TickerDetailTab?, section: RecentActivitiesTab?) {
        self.tab = tab
        self.section = section
    }
}

/// Where a notification tap should take the user.
enum NotificationRoute: Equatable, Hashable, Sendable {
    /// A ticker detail screen. `assetType` decides WHICH of the five it is;
    /// `destination` decides where INSIDE it to land.
    ///
    /// Stays `Hashable` on purpose — `AppState.pendingPushRoute` is observed with
    /// `.onChange(of:)`, so every associated value has to be.
    case ticker(symbol: String, assetType: MarketTickerType, destination: TickerDestination)
    /// A generated research report.
    ///
    /// `persona` decides WHICH report. A ticker holds one report per persona — six PLUG rows
    /// in one tester's feed — so the id alone is not enough for a screen keyed by
    /// (ticker, persona). Optional because rows written before the backend sent it have none;
    /// those fall back to the report screen's own default persona.
    case report(id: String, ticker: String?, persona: String?)
    /// The in-app notification inbox — the fallback for anything unroutable.
    case inbox

    /// Build a route from an APNs `userInfo` dict (or an inbox row's `route`).
    ///
    /// `payload` is `[AnyHashable: Any]` from APNs and `[String: String]` from the
    /// inbox; both funnel through `string(_:)` so there is ONE parsing path and the
    /// simctl-push fixtures exercise the same code the real payloads do.
    init(payload: [AnyHashable: Any]) {
        func string(_ key: String) -> String? {
            guard let raw = payload[key] else { return nil }
            if let s = raw as? String {
                let trimmed = s.trimmingCharacters(in: .whitespacesAndNewlines)
                return trimmed.isEmpty ? nil : trimmed
            }
            if let n = raw as? NSNumber { return n.stringValue }
            return nil
        }

        // `route` names the DESTINATION FAMILY and is deliberately separate from `kind`,
        // which names the notification type. Several kinds share one destination (every
        // smart-money kind opens a ticker) without collapsing their preferences or caps.
        let family = string("route") ?? "ticker"

        if family == "report", let reportID = string("report_id") {
            self = .report(
                id: reportID,
                ticker: string("ticker")?.uppercased(),
                persona: string("persona")
            )
            return
        }

        if let symbol = string("ticker") {
            self = .ticker(
                symbol: symbol.uppercased(),
                // FROM THE PAYLOAD, never hardcoded. The backend sources this from
                // `watchlist_items.asset_type` / `price_alerts.asset_type`, so a crypto
                // alert opens the crypto screen. An unknown or missing value falls back
                // to `.stock`, which is the overwhelming majority and the least wrong
                // guess — but it is a FALLBACK now, not the only behaviour.
                assetType: NotificationRoute.assetType(from: string("asset_type")),
                // Absent keys → `.default` → the screen's own default tab. An older
                // backend sends neither and nothing changes.
                destination: TickerDestination(
                    tabRaw: string("tab"), sectionRaw: string("section")
                )
            )
            return
        }

        // Unroutable: a payload with no ticker and no report id. Land in the inbox,
        // where the notification itself is visible, rather than doing nothing.
        self = .inbox
    }

    /// Convenience for the inbox rows, whose `route` is already `[String: String]`.
    init(payload: [String: String]) {
        self.init(payload: payload as [AnyHashable: Any])
    }

    /// Map the backend's `asset_type` string onto the enum that picks a detail screen.
    ///
    /// Accepts the spellings that actually appear in `watchlist_items.asset_type`
    /// ("Stock", "ETF", "Crypto", …) case-insensitively, because that column is populated
    /// from several sources and has never been normalised.
    static func assetType(from raw: String?) -> MarketTickerType {
        switch (raw ?? "").lowercased() {
        case "crypto", "cryptocurrency", "coin": return .crypto
        case "etf", "fund": return .etf
        case "index", "indices": return .index
        case "commodity", "commodities": return .commodity
        default: return .stock
        }
    }

    /// The ticker a route concerns, when it has one. Used for analytics dimensions.
    var symbol: String? {
        switch self {
        case .ticker(let symbol, _, _): return symbol
        case .report(_, let ticker, _): return ticker
        case .inbox: return nil
        }
    }

    /// Whether this tap has no detail screen to open, and must fall back to the notification
    /// list in Tracking → Alerts.
    ///
    /// Defined HERE, once, because two files act on it — `ContentView` picks the tab and
    /// `HomeDashboardView` declines to consume these routes — and a forked copy of the
    /// predicate would drift into "some unroutable taps land nowhere", which is silent.
    ///
    /// `.report` with no ticker qualifies: the report id alone cannot open anything today, so
    /// it is as unroutable as `.inbox` is.
    var needsAlertsFallback: Bool {
        switch self {
        case .inbox: return true
        case .report(_, let ticker, _): return ticker?.isEmpty != false
        case .ticker: return false
        }
    }

    /// Low-cardinality label for analytics. Never include a ticker here — `props` is for
    /// dimensions, not for user-specific values.
    var analyticsName: String {
        switch self {
        case .ticker: return "ticker"
        case .report: return "report"
        case .inbox: return "inbox"
        }
    }
}
