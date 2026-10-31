//
//  HoldingsSort.swift
//  ios
//
//  The Holdings "Sort By" choice, and the ONE ordering every list of the active group's
//  tickers applies with it.
//
//  Foundation-only on purpose: `test_ios_holdings_sort_behaviour.py` pipes this file into
//  `xcrun swift -` and runs the comparator itself, so keep SwiftUI and every app type out.
//

import Foundation

// MARK: - Sort option

/// One "Sort By" choice.
///
/// The raw values are what `HoldingsSort` stores in `UserDefaults`, so a shipped raw value never
/// changes. "Name" has always sorted by ticker and now DISPLAYS as "Symbol" (owner decision
/// 2026-10-31). "Date Added" used to read the group's stored order — which a Manage Tickers drag
/// rewrites — and now reads the real add date (`watchlist_items.added_at`, written once).
enum AssetSortOption: String, CaseIterable {
    /// The group's stored order: the one Manage Tickers and Edit Portfolio drag. Declared first
    /// because `allCases` is the menu order. A drag on the active group switches to it, so a
    /// reorder is always visible (it was invisible under every other option).
    case custom = "Custom"
    case name = "Name"
    case price = "Price"
    case change = "Change"
    case marketCap = "Market Cap"
    case dateAdded = "Date Added"

    var displayName: String {
        switch self {
        case .custom: return "Custom"
        // It compares tickers; the rows and the Updates chips both lead with the ticker.
        case .name: return "Symbol"
        case .price: return "Price"
        // Always the percent move, even while the rows show dollars — the label says so.
        case .change: return "Change %"
        case .marketCap: return "Market Cap"
        case .dateAdded: return "Date Added"
        }
    }

    /// The direction an option starts in when the user picks it: A→Z for symbols, largest first
    /// for the numbers, newest first for dates. Every option used to inherit the previous one's
    /// direction, so Price / Change / Market Cap picked from the default started low → high.
    var naturalAscending: Bool {
        switch self {
        case .custom, .name: return true
        case .price, .change, .marketCap, .dateAdded: return false
        }
    }

    /// The menu's two direction rows, or nil when the option has no direction (Custom is the
    /// user's own order, top to bottom).
    var directionLabels: (ascending: String, descending: String)? {
        switch self {
        case .custom: return nil
        case .name: return (ascending: "A → Z", descending: "Z → A")
        case .price, .change, .marketCap: return (ascending: "Low → High", descending: "High → Low")
        case .dateAdded: return (ascending: "Oldest First", descending: "Newest First")
        }
    }
}

// MARK: - Sort

/// The Tracking tab's "Sort By" choice, shared with every other surface that lists the active
/// group's tickers (today: the Updates chip strip).
///
/// It used to live on `TrackingViewModel` alone, so the Updates chips stayed in the group's
/// stored order whatever the user picked. Both screens now read this value and order through
/// `ordered(_:key:)`, so they cannot drift: change one comparator and both lists move together.
///
/// Local-only, like the rest of Tracking's display choices (see
/// `TrackingViewModel.changeDisplayMode`): never in `SettingsSyncManager`, never cleared on
/// sign-out.
struct HoldingsSort: Equatable {
    var option: AssetSortOption
    var ascending: Bool

    /// Posted by `save()`. Every listener re-reads the stored value (`load()`), so the order
    /// changes on screens that are already mounted — `ContentView` opacity-mounts every tab.
    static let didChangeNotification = Notification.Name("HoldingsSort.didChange")

    /// The keys `TrackingViewModel` has always written, kept so every user's existing choice
    /// survives the move.
    private static let optionKey = "TrackingView.sortOption"
    private static let ascendingKey = "TrackingView.sortAscending"

    /// Symbol, A→Z — what Tracking showed before the user ever picked a sort.
    static let defaultValue = HoldingsSort(option: .name, ascending: true)

    static func load(from defaults: UserDefaults = .standard) -> HoldingsSort {
        var sort = defaultValue
        if let raw = defaults.string(forKey: optionKey),
           let restored = AssetSortOption(rawValue: raw) {
            sort.option = restored
        }
        if defaults.object(forKey: ascendingKey) != nil {
            sort.ascending = defaults.bool(forKey: ascendingKey)
        }
        return sort
    }

    /// Stores the choice and tells every mounted list to re-order.
    func save(to defaults: UserDefaults = .standard) {
        defaults.set(option.rawValue, forKey: Self.optionKey)
        defaults.set(ascending, forKey: Self.ascendingKey)
        NotificationCenter.default.post(name: Self.didChangeNotification, object: nil)
    }

    // MARK: - Ordering

    /// What one row is sorted by.
    ///
    /// Every value is optional, and nil means UNKNOWN: a row whose price or day change is a
    /// placeholder (`priceKnown` / `changeKnown` false — pass nil, never the 0 placeholder), a
    /// missing market cap, a missing add date, a ticker outside the stored order. Unknown values
    /// sort LAST in both directions. They used to sort as 0, so the unpriced "—" rows led
    /// Price ↑ and sat among the real flat moves under Change, and a missing cap led
    /// Market Cap ↓. Non-finite numbers are unknown too — a NaN breaks the sort's contract.
    struct Key {
        let ticker: String
        let price: Double?
        let changePercent: Double?
        let marketCap: Double?
        /// When the ticker joined the watchlist.
        let addedAt: Date?
        /// The ticker's position in the active group's stored order (Custom).
        let position: Int?

        init(
            ticker: String,
            price: Double?,
            changePercent: Double?,
            marketCap: Double?,
            addedAt: Date?,
            position: Int?
        ) {
            self.ticker = ticker.uppercased()
            self.price = Self.finite(price)
            self.changePercent = Self.finite(changePercent)
            // Not positive → unknown: the Updates tabs already send None for it and the feed
            // None for 0, so both screens must sort a junk cap the same way.
            self.marketCap = Self.finite(marketCap).flatMap { $0 > 0 ? $0 : nil }
            self.addedAt = addedAt
            self.position = position
        }

        private static func finite(_ value: Double?) -> Double? {
            guard let value, value.isFinite else { return nil }
            return value
        }
    }

    /// `items` in this sort's order.
    func ordered<T>(_ items: [T], key: (T) -> Key) -> [T] {
        let keyed: [(item: T, key: Key)] = items.map { (item: $0, key: key($0)) }
        let sorted: [(item: T, key: Key)] = keyed.sorted { (lhs: (item: T, key: Key), rhs: (item: T, key: Key)) -> Bool in
            precedes(lhs.key, rhs.key)
        }
        return sorted.map { $0.item }
    }

    /// The comparator. Equal (or both-unknown) values always fall back to the ticker A→Z, in
    /// either direction — the two screens start from different input orders and must settle a
    /// tie the same way.
    func precedes(_ lhs: Key, _ rhs: Key) -> Bool {
        let decided: Bool?
        switch option {
        case .custom:
            // The user's order, top to bottom. There is no direction to apply (the menu hides
            // it), so a stored "descending" can never reverse what the user just dragged.
            decided = Self.knownFirst(lhs.position, rhs.position, ascending: true)
        case .name:
            decided = lhs.ticker == rhs.ticker
                ? nil
                : (ascending ? lhs.ticker < rhs.ticker : lhs.ticker > rhs.ticker)
        case .price:
            decided = Self.knownFirst(lhs.price, rhs.price, ascending: ascending)
        case .change:
            decided = Self.knownFirst(lhs.changePercent, rhs.changePercent, ascending: ascending)
        case .marketCap:
            decided = Self.knownFirst(lhs.marketCap, rhs.marketCap, ascending: ascending)
        case .dateAdded:
            decided = Self.knownFirst(lhs.addedAt, rhs.addedAt, ascending: ascending)
        }
        return decided ?? (lhs.ticker < rhs.ticker)
    }

    /// The order of two possibly-unknown values, or nil when they tie (equal, or both unknown).
    /// A known value precedes an unknown one whatever the direction.
    private static func knownFirst<V: Comparable>(_ lhs: V?, _ rhs: V?, ascending: Bool) -> Bool? {
        switch (lhs, rhs) {
        case let (l?, r?):
            if l == r { return nil }
            return ascending ? l < r : l > r
        case (.some, .none):
            return true
        case (.none, .some):
            return false
        case (.none, .none):
            return nil
        }
    }

    // MARK: - Add date

    /// `added_at` off the wire ("2026-09-14T12:34:56.789Z"; the backend normalises it to UTC
    /// milliseconds). Absent, blank or unparseable → nil, which sorts last under Date Added.
    nonisolated static func parseAddedAt(_ raw: String?) -> Date? {
        guard let text = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty else {
            return nil
        }
        return fractionalFormatter.date(from: text) ?? wholeSecondsFormatter.date(from: text)
    }

    // `ISO8601DateFormatter` is documented thread-safe; built once rather than per feed row.
    nonisolated(unsafe) private static let fractionalFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    nonisolated(unsafe) private static let wholeSecondsFormatter: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()
}
