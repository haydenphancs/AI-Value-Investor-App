//
//  AlertDestination.swift
//  ios
//
//  Where an alert can take you, and how to decide that from the alert itself.
//

import SwiftUI

/// One "go here" option offered by an alert's detail screen.
///
/// WHY THIS EXISTS. A tester: *"It should open a screen to show the reason or detail of information
/// first, then users want to go to that ticker or else. This screen should open news tab or
/// financial tab or holder tab or whale tab profile depends on the activity information."*
///
/// Both halves of Activity needed the same answer — the digest cards and the notification rows —
/// so the choice of destinations is modelled once, here, rather than twice in two screens that
/// would drift. `AlertDestinationRow` renders one; `NotificationDetailView` and `AlertDetailView`
/// both consume the lists built below.
struct AlertDestination: Identifiable, Hashable {
    let label: String
    let systemImage: String
    let target: Target

    enum Target: Hashable {
        case ticker(symbol: String, assetType: MarketTickerType, destination: TickerDestination)
        case whale(id: String)
        case report(ticker: String, persona: String?, reportId: String?)
    }

    var id: String { "\(label)|\(systemImage)|\(String(describing: target))" }

    /// The screen-dispatch route for this destination, or nil for one that is not a
    /// `NotificationRoute` family (the whale profile).
    ///
    /// Exists so both detail screens push through `NotificationRouteContent` — the ONE five-way
    /// asset switch — instead of re-deriving "which of the five detail screens is this".
    var route: NotificationRoute? {
        switch target {
        case .ticker(let symbol, let assetType, let destination):
            return .ticker(symbol: symbol, assetType: assetType, destination: destination)
        case .report(let ticker, let persona, let reportId):
            return .report(id: reportId ?? "", ticker: ticker, persona: persona)
        case .whale:
            return nil
        }
    }
}

// MARK: - Building the list for a delivered notification

extension AlertDestination {

    /// The destinations a notification row offers, in the order they should be shown.
    static func destinations(for item: NotificationEventDTO) -> [AlertDestination] {
        let route = item.route
        let symbol = (route["ticker"] ?? "").uppercased()
        let assetType = NotificationRoute.assetType(from: route["asset_type"])
        var out: [AlertDestination] = []

        // The report leads for `research_complete`, because the body literally says "tap to read
        // the full report" — and until this change it opened the ticker screen instead.
        if item.kind == "research_complete", !symbol.isEmpty {
            out.append(AlertDestination(
                label: "Read the full report",
                systemImage: "doc.text",
                target: .report(
                    ticker: symbol,
                    persona: route["persona"],
                    reportId: route["report_id"]
                )
            ))
        }

        guard !symbol.isEmpty else { return out }

        out.append(AlertDestination(
            label: "Open \(symbol)",
            systemImage: "chart.line.uptrend.xyaxis",
            target: .ticker(symbol: symbol, assetType: assetType, destination: .default)
        ))

        // ⚠️ STOCK ONLY. Only `TickerDetailView` accepts a `TickerDestination`; the crypto, ETF,
        // index and commodity screens take a bare symbol and silently ignore a tab they do not
        // have. Offering "Financials" on a BTC alert would be a control that lies — it would open
        // the same screen as the row above it and look like a bug in the tab bar.
        if assetType == .stock, let tab = tabDestination(forKind: item.kind) {
            out.append(AlertDestination(
                label: tab.label,
                systemImage: tab.systemImage,
                target: .ticker(symbol: symbol, assetType: .stock, destination: tab.destination)
            ))
        }

        // The investor the alert is ABOUT. Present only on notifications written after the sender
        // started carrying `whale_id`; older rows fall back to the Holders destination above,
        // which is why that one is not conditional on this.
        if let whaleId = route["whale_id"], !whaleId.isEmpty {
            out.append(AlertDestination(
                label: "View investor profile",
                systemImage: "person.crop.circle",
                target: .whale(id: whaleId)
            ))
        }

        return out
    }

    /// The extra tab this KIND of alert is about, beyond the ticker's default screen.
    ///
    /// ⚠️ Every kind registered in `backend/app/services/notification_kinds.py` must have an arm
    /// here, even the ones that return nil. `test_ios_alerts_badge_and_filters.py` fails the build
    /// otherwise — a kind that silently falls through to `default` is an alert whose detail screen
    /// offers a generic "Open TICKER" when it could have landed on the exact tab the alert is
    /// about, and nothing on screen would ever look wrong.
    static func tabDestination(
        forKind kind: String
    ) -> (label: String, systemImage: String, destination: TickerDestination)? {
        switch kind {
        case "ticker_move":
            return ("Read the news", "newspaper", TickerDestination(tab: .news, section: nil))
        case "earnings_upcoming", "earnings_result":
            return ("See the numbers", "chart.bar.doc.horizontal",
                    TickerDestination(tab: .financials, section: nil))
        case "insider_trade":
            return ("Insider activity", "person.badge.key.fill",
                    TickerDestination(tab: .holders, section: .insiders))
        case "whale_13f":
            return ("Institutional holders", "building.columns.fill",
                    TickerDestination(tab: .holders, section: .institutions))
        case "congress_trade":
            return ("Congress trades", "building.columns.fill",
                    TickerDestination(tab: .holders, section: .congress))
        // Deliberately no extra tab: these are about the ticker as a whole, or about something
        // that is not on the ticker screen at all.
        case "price_alert", "research_complete", "research_failed", "profile_match":
            return nil
        default:
            return nil
        }
    }
}

// MARK: - Building the list for a digest roll-up item

extension AlertDestination {

    /// Where one ROW of a digest card should lead.
    ///
    /// A roll-up spans several tickers ("AAPL, CRM and 2 more"), so the destination belongs to the
    /// item, not to the card — a single action at the bottom could only ever mean one of them.
    ///
    /// Always `.stock`: 13F filings, Form 4s, analyst ratings and earnings are equity-only.
    static func destinations(forRollupItem ticker: String, in alert: AppAlert) -> [AlertDestination] {
        let symbol = ticker.uppercased()
        guard !symbol.isEmpty else { return [] }

        var out: [AlertDestination] = [
            AlertDestination(
                label: "Open \(symbol)",
                systemImage: "chart.line.uptrend.xyaxis",
                target: .ticker(symbol: symbol, assetType: .stock, destination: .default)
            )
        ]

        let tab: (String, String, TickerDestination)? = {
            switch alert {
            case .whaleTrade:
                return ("Institutional holders", "building.columns.fill",
                        TickerDestination(tab: .holders, section: .institutions))
            case .insiderTransaction:
                return ("Insider activity", "person.badge.key.fill",
                        TickerDestination(tab: .holders, section: .insiders))
            case .analystRating:
                // Wall Street targets and rating history live on Analysis, not Holders.
                return ("Analyst view", "chart.xyaxis.line",
                        TickerDestination(tab: .analysis, section: nil))
            case .earnings:
                return ("See the numbers", "chart.bar.doc.horizontal",
                        TickerDestination(tab: .financials, section: nil))
            case .market:
                return nil
            }
        }()

        if let tab {
            out.append(AlertDestination(
                label: tab.0,
                systemImage: tab.1,
                target: .ticker(symbol: symbol, assetType: .stock, destination: tab.2)
            ))
        }
        return out
    }
}
