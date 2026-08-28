//
//  NotificationModels.swift
//  ios
//
//  DTOs for the notification inbox and user-set price alerts, plus the route type the
//  tap handler resolves a payload into.
//
//  Co-located per the model-file convention: the `Codable` DTOs and the UI-facing
//  helpers live in ONE file per feature.
//
//  ⚠️ Every DTO field below is decoded from a backend response. A field the backend
//  renames — or makes nullable — without a matching change here is a DECODE CRASH in
//  production, not a missing row. `backend/tests/test_notification_schema_parity.py`
//  is the guard rail; keep it updated in the same change as any shape edit.
//

import Foundation
import SwiftUI

// MARK: - Notification inbox

/// One row of the in-app inbox.
///
/// The backend writes a row BEFORE it attempts delivery, so this includes notifications
/// that were deferred by quiet hours or that found no registered device. That is the
/// point: a push arriving while the phone is face-down is otherwise gone forever.
struct NotificationEventDTO: Decodable, Identifiable, Hashable, Sendable {
    let id: String
    /// `NotificationKind` key — `ticker_move`, `earnings_upcoming`, `price_alert`, …
    let kind: String
    /// Cap bucket: `watchlist` | `earnings` | `smart_money` | `price_alert` | `app` | `match`.
    ///
    /// SIX values, not five — `profile_match` is `CATEGORY_MATCH`. The list was wrong here from
    /// the day it was written, and nothing noticed because nothing read `category` at all until
    /// `ActivityFilter` below. `test_ios_alerts_badge_and_filters.py` now pins it against
    /// `notification_kinds.py`.
    let category: String
    let title: String
    let body: String
    /// The tap target, exactly as it shipped in the APNs payload — flattened to strings.
    ///
    /// The backend guarantees FLAT SCALARS here (it drops anything nested before
    /// serializing), and every consumer — `NotificationRouter` and the deep-link chain —
    /// wants a string. Decoding straight to `[String: String]`, coercing the occasional
    /// numeric, keeps the type `Hashable`/`Sendable` and removes a whole class of
    /// "what is inside this Any?" from the call sites.
    let route: [String: String]
    let createdAt: String
    let readAt: String?
    /// `sent` | `deferred` | `no_device` | `dry_run` | `failed` | `pending`.
    /// Surfaced so "I never got it" is answerable inside the app rather than from logs.
    let deliveryState: String

    enum CodingKeys: String, CodingKey {
        case id, kind, category, title, body, route
        case createdAt = "created_at"
        case readAt = "read_at"
        case deliveryState = "delivery_state"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = try c.decodeIfPresent(String.self, forKey: .kind) ?? "unknown"
        category = try c.decodeIfPresent(String.self, forKey: .category) ?? "unknown"
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        body = try c.decodeIfPresent(String.self, forKey: .body) ?? ""
        createdAt = try c.decodeIfPresent(String.self, forKey: .createdAt) ?? ""
        readAt = try c.decodeIfPresent(String.self, forKey: .readAt)
        deliveryState = try c.decodeIfPresent(String.self, forKey: .deliveryState) ?? "sent"
        route = (try? c.decode([String: RouteScalar].self, forKey: .route))?
            .compactMapValues(\.stringValue) ?? [:]
    }

    /// Decodes a single flat JSON scalar into a String.
    ///
    /// Tolerant on purpose: a route value the backend widens to a number later must not
    /// crash an older build, and a nested value must not either. Both degrade to a
    /// dropped key, which the router already handles by falling back to the inbox.
    private struct RouteScalar: Decodable {
        let stringValue: String?

        init(from decoder: Decoder) throws {
            let c = try decoder.singleValueContainer()
            if let s = try? c.decode(String.self) { stringValue = s }
            else if let i = try? c.decode(Int.self) { stringValue = String(i) }
            else if let d = try? c.decode(Double.self) { stringValue = String(d) }
            else if let b = try? c.decode(Bool.self) { stringValue = b ? "true" : "false" }
            else { stringValue = nil }
        }
    }

    var isRead: Bool { readAt != nil }

    /// True when the buzz genuinely went out. Everything else is a row the user is
    /// seeing here FIRST, which is worth saying in the UI.
    var wasDelivered: Bool { deliveryState == "sent" }

    /// Where a tap should land.
    var destination: NotificationRoute { NotificationRoute(payload: route) }

    // MARK: - Visual vocabulary

    /// Icon for this notification's kind.
    ///
    /// `kind` has been on the wire and decoded since the inbox shipped, and NOTHING read
    /// it — the router deliberately switches on `route` instead. That left notifications
    /// as the only rows in the Alerts tab with no icon, sitting beside `AppAlert` cards
    /// that have one, which is most of why the two looked like different apps.
    ///
    /// Glyphs are taken from `NotificationSettingsViewModel.groups`, where the same
    /// categories already have icons the user has seen — inventing a second set would
    /// mean the Notifications settings screen and the Alerts tab disagreed about what an
    /// earnings alert looks like. `price_alert` has no settings group of its own (its
    /// toggle lives under Watchlist), so it borrows the app's price-alert bell.
    ///
    /// An unknown `kind` decodes to `"unknown"` upstream and lands on the default here,
    /// so a kind added by a newer backend degrades to a neutral glyph rather than a gap.
    var iconName: String {
        switch kind {
        case "ticker_move":       return "chart.line.uptrend.xyaxis"
        case "earnings_upcoming",
             "earnings_result":   return "calendar.badge.clock"
        case "insider_trade":     return "person.badge.key.fill"
        case "whale_13f":         return "building.columns.fill"
        case "congress_trade":    return "building.columns.fill"
        case "price_alert":       return "bell.badge"
        // The RESEARCH TAB'S OWN GLYPH, read from `HomeTab` rather than written here a
        // second time — that is what makes "the same icon as the navigator" true by
        // construction instead of by coincidence. A tester asked for this directly.
        //
        // It also frees `sparkles` for `profile_match` alone, which sharpens the
        // vocabulary rather than blurring it: sparkles = an AI topic match the app
        // derived, magnifier = a report you asked for.
        case "research_complete": return HomeTab.research.systemIconName
        case "research_failed":   return "exclamationmark.arrow.circlepath"
        case "profile_match":     return AppSymbols.ai
        default:                  return "app.badge.fill"
        }
    }

    /// Tint for `iconName`.
    ///
    /// ⚠️ These are TEXT-role tokens, and they must stay that way. The icon is a
    /// meaningful glyph carrying the row's category — not chart furniture — so it needs
    /// the 4.5:1 bar, not the 3:1 `*Graphic` one. See the role table in
    /// `.claude/rules/ios-swiftui.md`.
    ///
    /// Deliberately NOT direction-coloured (bullish/bearish) the way `AppAlert` is: a
    /// `NotificationEventDTO` carries no direction — "Insider activity in ACHR" does not
    /// say bought or sold in any structured field — so tinting by sentiment would mean
    /// guessing, and a green icon over a sale is worse than a neutral one.
    var iconColor: Color {
        switch kind {
        case "insider_trade", "whale_13f", "congress_trade":
            return AppColors.alertOrange
        case "profile_match":
            return AppColors.alertPurple
        case "price_alert":
            return AppColors.caution
        // A failure the user paid for. `caution` and not `loss`: the credits came back,
        // so this is something to look at, not a loss to mourn — and `loss` in a feed of
        // market rows would read as a price move.
        case "research_failed":
            return AppColors.caution
        default:
            return AppColors.primaryBlue
        }
    }

    var relativeTime: String {
        guard let date = NotificationEventDTO.isoFormatter.date(from: createdAt)
                ?? NotificationEventDTO.isoFormatterNoFraction.date(from: createdAt)
        else { return "" }
        let formatter = RelativeDateTimeFormatter()
        formatter.unitsStyle = .abbreviated
        return formatter.localizedString(for: date, relativeTo: Date())
    }

    // Two parsers: Postgres emits fractional seconds for `timestamptz` but not always,
    // and `ISO8601DateFormatter` is strict about which one it was configured for. A
    // single formatter silently returns nil for the other shape, which would blank
    // every timestamp in the inbox.
    private static let isoFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()

    private static let isoFormatterNoFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()
}

struct NotificationListDTO: Decodable, Sendable {
    let items: [NotificationEventDTO]
    let unreadCount: Int
    /// Keyset cursor for the next page. `nil` = no more.
    let nextCursor: String?

    enum CodingKeys: String, CodingKey {
        case items
        case unreadCount = "unread_count"
        case nextCursor = "next_cursor"
    }
}

struct MarkNotificationsReadDTO: Decodable, Sendable {
    let updated: Int
    let unreadCount: Int

    enum CodingKeys: String, CodingKey {
        case updated
        case unreadCount = "unread_count"
    }
}

// MARK: - Price alerts

enum PriceAlertKind: String, Codable, CaseIterable, Sendable {
    case above = "price_above"
    case below = "price_below"
    case percentMove = "percent_move"

    var label: String {
        switch self {
        case .above: return "Rises above"
        case .below: return "Falls below"
        case .percentMove: return "Moves by"
        }
    }

    /// `%` reads as a percentage of the previous close; the other two are dollars.
    var isPercent: Bool { self == .percentMove }
}

enum PriceAlertRepeat: String, Codable, CaseIterable, Sendable {
    case once
    case daily

    var label: String {
        switch self {
        case .once: return "Once"
        case .daily: return "Once a day"
        }
    }

    var explanation: String {
        switch self {
        case .once: return "Alerts you the first time, then turns itself off."
        case .daily: return "Alerts you at most once per trading day."
        }
    }
}

struct PriceAlertDTO: Decodable, Identifiable, Hashable, Sendable {
    let id: String
    let ticker: String
    let assetType: String
    let kind: String
    let threshold: Double
    let repeatMode: String
    let isActive: Bool
    /// False after a fire, until the price retreats past the re-arm band. Surfaced so
    /// the sheet can explain a rule that is active but deliberately quiet — otherwise
    /// it looks broken.
    let armed: Bool
    let lastPrice: Double?
    let lastTriggeredAt: String?
    let triggerCount: Int
    let note: String?
    let createdAt: String?

    enum CodingKeys: String, CodingKey {
        case id, ticker, kind, threshold, armed, note
        case assetType = "asset_type"
        case repeatMode = "repeat_mode"
        case isActive = "is_active"
        case lastPrice = "last_price"
        case lastTriggeredAt = "last_triggered_at"
        case triggerCount = "trigger_count"
        case createdAt = "created_at"
    }

    var alertKind: PriceAlertKind { PriceAlertKind(rawValue: kind) ?? .above }
    var repeatRule: PriceAlertRepeat { PriceAlertRepeat(rawValue: repeatMode) ?? .once }

    var thresholdLabel: String {
        alertKind.isPercent
            ? String(format: "%g%%", threshold)
            : String(format: "$%.2f", threshold)
    }

    var summary: String { "\(alertKind.label) \(thresholdLabel)" }

    /// A one-line explanation of why a live rule might be silent right now, or nil when
    /// there is nothing to explain. Without this, "armed = false" is invisible state and
    /// the user concludes the feature is broken.
    var quietReason: String? {
        guard isActive else { return "Turned off" }
        guard armed else { return "Already triggered — re-arms when the price moves back" }
        return nil
    }

    /// A copy with `isActive` flipped, for `PriceAlertStore`'s optimistic toggle.
    ///
    /// The properties stay `let` deliberately — this is a decoded server row, and the only
    /// legitimate reason to change one locally is the brief optimistic window before the
    /// server's own row replaces it. An explicit copy makes that window visible at the call
    /// site instead of letting any holder mutate a DTO in place.
    func withIsActive(_ newValue: Bool) -> PriceAlertDTO {
        PriceAlertDTO(
            id: id,
            ticker: ticker,
            assetType: assetType,
            kind: kind,
            threshold: threshold,
            repeatMode: repeatMode,
            isActive: newValue,
            armed: armed,
            lastPrice: lastPrice,
            lastTriggeredAt: lastTriggeredAt,
            triggerCount: triggerCount,
            note: note,
            createdAt: createdAt
        )
    }
}

struct PriceAlertListDTO: Decodable, Sendable {
    let items: [PriceAlertDTO]
    let maxPerUser: Int
    let maxPerTicker: Int

    enum CodingKeys: String, CodingKey {
        case items
        case maxPerUser = "max_per_user"
        case maxPerTicker = "max_per_ticker"
    }
}

struct DeletePriceAlertDTO: Decodable, Sendable {
    let deleted: Bool
}

// MARK: - Activity filtering

/// The chip buckets on Tracking → Alerts → Activity.
///
/// WHY. A TestFlight tester: *"Activity is a very long list. Should add tags on the top (same row
/// as Activity), just like in the Report tab."* Activity is two concatenated families — roll-up
/// `AppAlert` cards and delivered `NotificationEventDTO` rows — so a chip has to mean something on
/// BOTH, which is what the two `bucket(for:)` overloads below are for.
///
/// Buckets key off `category`, not `kind`: `category` is the coarse grouping the backend already
/// maintains (it is the daily-cap bucket), so a NEW kind lands in the right chip with no client
/// change. `kind` would need an entry per kind, added by hand, every time.
enum ActivityFilter: String, CaseIterable, Identifiable, Hashable, Sendable {
    case prices
    case earnings
    case smartMoney
    case reports

    var id: String { rawValue }

    var label: String {
        switch self {
        case .prices:     return "Prices"
        case .earnings:   return "Earnings"
        case .smartMoney: return "Smart Money"
        case .reports:    return "Reports"
        }
    }

    /// Text-role accent (4.5:1) — the chip's ink when unselected, and its resting tint at 0.15.
    ///
    /// These are chosen for legibility as a set, the way the Reports tab's persona accents are.
    /// They are deliberately NOT claimed to equal `NotificationEventDTO.iconColor` for every row
    /// in the bucket, because two buckets contain kinds that disagree: `.prices` holds
    /// `price_alert` (caution) alongside `ticker_move` (primaryBlue), and `.reports` holds
    /// `profile_match` (alertPurple) alongside `research_complete` (primaryBlue). Asserting a
    /// parity that only holds for half the rows would be a guard that lies.
    var accent: Color {
        switch self {
        case .prices:     return AppColors.caution
        case .earnings:   return AppColors.primaryBlue
        case .smartMoney: return AppColors.alertOrange
        case .reports:    return AppColors.alertPurple
        }
    }

    /// Fill-role colour for the SELECTED state, carrying `AppColors.textOnAccent`.
    ///
    /// ⚠️ Must always be a `*Fill` token, never the `accent` above. A text-role token used as a
    /// saturated fill puts white ink at ~2.3–2.8:1 in dark — see the fill/ink table in
    /// `AppTheme.swift`. The two halves move together.
    var accentFill: Color {
        switch self {
        case .prices:     return AppColors.cautionFill
        case .earnings:   return AppColors.primaryFill
        case .smartMoney: return AppColors.alertOrangeFill
        case .reports:    return AppColors.alertPurpleFill
        }
    }

    /// Bucket for a delivered notification, or `nil` when its category is unknown to this build.
    ///
    /// ⚠️ Every `CATEGORY_*` in `backend/app/services/notification_kinds.py` must appear here;
    /// `test_ios_alerts_badge_and_filters.py` fails the build otherwise. `nil` therefore only
    /// happens when the BACKEND is ahead of the client — and `admits` fails OPEN on it, so an
    /// unrecognised row stays visible under every filter rather than silently disappearing.
    static func bucket(forCategory category: String) -> ActivityFilter? {
        switch category {
        case "price_alert", "watchlist": return .prices
        case "earnings":                 return .earnings
        case "smart_money":              return .smartMoney
        case "app", "match":             return .reports
        default:                         return nil
        }
    }

    /// Bucket for a digest roll-up card.
    ///
    /// `.earnings` and `.market` are `isUpcoming`, so they render in the Upcoming section and
    /// never reach a filtered list — mapped anyway so the switch stays exhaustive and a future
    /// re-split of the sections cannot silently drop them.
    static func bucket(forRollup alert: AppAlert) -> ActivityFilter? {
        switch alert {
        case .whaleTrade, .analystRating, .insiderTransaction: return .smartMoney
        case .earnings:                                        return .earnings
        case .market:                                          return nil
        }
    }

    /// UserDefaults key for the persisted chip selection.
    ///
    /// Declared HERE, beside the values it encodes, so the writer (`AlertsTabContent`) and the
    /// clearer (`AppState.discardDataForEndedSession`) cannot drift apart on a string literal.
    static let storageKey = "activity_filters"

    /// Encode a selection for `@AppStorage`.
    ///
    /// Iterates `allCases` rather than the Set so the order is STABLE. A `Set`'s iteration
    /// order is not, and joining it directly would rewrite the stored string — and wake every
    /// `@AppStorage` reader — on writes that changed nothing.
    static func encode(_ selection: Set<ActivityFilter>) -> String {
        allCases.filter { selection.contains($0) }.map(\.rawValue).joined(separator: ",")
    }

    /// Decode a persisted selection, dropping anything this build no longer knows.
    ///
    /// Tolerant on purpose: a bucket renamed or removed in a later version must degrade to
    /// "not selected", never to a crash or to a filter nobody can clear.
    static func decode(_ raw: String) -> Set<ActivityFilter> {
        Set(raw.split(separator: ",").compactMap { ActivityFilter(rawValue: String($0)) })
    }

    /// Does `selection` admit a notification in `category`? Empty selection admits everything.
    static func admits(_ selection: Set<ActivityFilter>, category: String) -> Bool {
        guard !selection.isEmpty else { return true }
        guard let bucket = bucket(forCategory: category) else { return true }
        return selection.contains(bucket)
    }

    /// Does `selection` admit this roll-up? Empty selection admits everything.
    static func admits(_ selection: Set<ActivityFilter>, rollup: AppAlert) -> Bool {
        guard !selection.isEmpty else { return true }
        guard let bucket = bucket(forRollup: rollup) else { return true }
        return selection.contains(bucket)
    }
}
