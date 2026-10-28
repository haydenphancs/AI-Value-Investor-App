//
//  HoldingsSort.swift
//  ios
//
//  The Holdings "Sort By" choice, and the ONE ordering every list of the active group's
//  tickers applies with it.
//

import Foundation

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

    /// Name, ascending — what Tracking showed before the user ever picked a sort.
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
    /// `price` and `changePercent` are NOT optional on purpose: Tracking's rows carry an unknown
    /// value as a 0 placeholder (`priceKnown` / `changeKnown` false) and have always sorted it as
    /// 0, so a caller with an optional passes `?? 0` to sort exactly as Tracking does. A missing
    /// market cap keeps its own rule (below). Non-finite numbers are cleaned here — a NaN breaks
    /// the sort's ordering contract.
    struct Key {
        let ticker: String
        let price: Double
        let changePercent: Double
        let marketCap: Double?
        /// The ticker's position in the active group's stored order ("Date Added"). nil sorts
        /// after every known position.
        let position: Int?

        init(ticker: String, price: Double, changePercent: Double, marketCap: Double?, position: Int?) {
            self.ticker = ticker.uppercased()
            self.price = price.isFinite ? price : 0
            self.changePercent = changePercent.isFinite ? changePercent : 0
            if let cap = marketCap, cap.isFinite {
                self.marketCap = cap
            } else {
                self.marketCap = nil
            }
            self.position = position
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

    /// The comparator, moved verbatim from `TrackingViewModel.filteredAssets`, plus one
    /// addition: equal values fall back to the ticker (A→Z). Without it the two screens, which
    /// start from different input orders, could settle a tie differently.
    func precedes(_ lhs: Key, _ rhs: Key) -> Bool {
        switch option {
        case .name:
            if lhs.ticker != rhs.ticker {
                return ascending ? lhs.ticker < rhs.ticker : lhs.ticker > rhs.ticker
            }
        case .price:
            if lhs.price != rhs.price {
                return ascending ? lhs.price < rhs.price : lhs.price > rhs.price
            }
        case .change:
            if lhs.changePercent != rhs.changePercent {
                return ascending ? lhs.changePercent < rhs.changePercent : lhs.changePercent > rhs.changePercent
            }
        case .marketCap:
            // Assets without a market cap (e.g. crypto, indices) sort to the bottom in
            // ascending order, top in descending — matches how the Watchlist screen handles
            // missing fundamentals.
            switch (lhs.marketCap, rhs.marketCap) {
            case let (l?, r?):
                if l != r {
                    return ascending ? l < r : l > r
                }
            case (nil, _?):
                return !ascending
            case (_?, nil):
                return ascending
            case (nil, nil):
                if lhs.ticker != rhs.ticker {
                    return ascending ? lhs.ticker < rhs.ticker : lhs.ticker > rhs.ticker
                }
            }
        case .dateAdded:
            let l: Int = lhs.position ?? Int.max
            let r: Int = rhs.position ?? Int.max
            if l != r {
                return ascending ? l < r : l > r
            }
        }
        return lhs.ticker < rhs.ticker
    }
}
