//
//  TrackingViewModel.swift
//  ios
//
//  ViewModel for the Tracking screen
//

import Foundation
import SwiftUI
import Combine

@MainActor
class TrackingViewModel: ObservableObject {
    // MARK: - Private
    private var cancellables = Set<AnyCancellable>()
    private let apiClient: APIClient
    let portfolioStore: PortfolioStore
    private var priceRefreshTask: Task<Void, Never>?

    // MARK: - Published Properties

    // Tab State
    @Published var selectedTab: TrackingTab = .assets
    @Published var searchText: String = ""

    // Assets Tab
    @Published var trackedAssets: [TrackedAsset] = []
    @Published var sortOption: AssetSortOption = .name
    @Published var sortAscending: Bool = true

    // Alerts & Events
    @Published var alerts: [AppAlert] = []

    /// Local UI preference for showing the Portfolio Insights section.
    /// Persisted in UserDefaults so it survives app restarts on this device.
    @Published var isInsightsEnabled: Bool {
        didSet {
            UserDefaults.standard.set(isInsightsEnabled, forKey: Self.insightsEnabledKey)
        }
    }
    private static let insightsEnabledKey = "TrackingView.isInsightsEnabled"
    private static let sortOptionKey = "TrackingView.sortOption"
    private static let sortAscendingKey = "TrackingView.sortAscending"

    /// Server-computed diversification score (the source of truth). Nil until
    /// loaded, when the user has < 2 holdings, or when the call fails.
    @Published var portfolioInsights: DiversificationScore?
    /// True only when the insights call failed for connectivity reasons — used
    /// to decide whether to fall back to the on-device estimate.
    @Published var portfolioInsightsLoadFailed: Bool = false

    // Whales Tab
    @Published var selectedWhaleCategory: WhaleCategory = .investors
    @Published var whaleActivities: [WhaleActivity] = []
    @Published var trackedWhales: [TrendingWhale] = []
    @Published var popularWhales: [TrendingWhale] = []
    @Published var heroWhales: [TrendingWhale] = []
    @Published var allPopularWhales: [TrendingWhale] = []
    @Published var showAllWhales: Bool = false
    @Published var showAllTrades: Bool = false
    @Published var groupedWhaleTrades: [GroupedWhaleTrades] = []
    @Published var allWhaleTrades: [GroupedWhaleTrades] = []

    // Loading States
    @Published var isLoading: Bool = false
    @Published var isRefreshing: Bool = false

    // Sheet States
    @Published var showAddAssetSheet: Bool = false
    @Published var showSortSheet: Bool = false
    @Published var showPortfolioConfigSheet: Bool = false
    @Published var showNewPortfolioSheet: Bool = false
    @Published var showEditPortfolioSheet: Bool = false
    @Published var showManageTickersSheet: Bool = false

    /// Tickers the user just added via the in-sheet star button, keyed by
    /// the portfolio they were added to. Used to fill the star instantly
    /// while the server round-trip is in flight; cleared per-entry once the
    /// real `portfolioStore.activePortfolio.tickers` reflects the new row.
    /// Per-portfolio scoping prevents an optimistic add to "Holdings" from
    /// leaking into the star state when the user switches to "Tech".
    @Published var recentlyAddedTickers: [String: Set<String>] = [:]

    // Navigation States
    @Published var selectedAssetNavigation: SearchSelection?
    @Published var selectedSearchResult: SearchSelection?
    @Published var selectedWhaleId: String?
    @Published var selectedTradeGroup: TradeGroupNavigation?
    @Published var selectedAlert: AppAlert?

    // MARK: - Init

    init(apiClient: APIClient = .shared, portfolioStore: PortfolioStore? = nil) {
        self.apiClient = apiClient
        // `.shared` is @MainActor-isolated; defaulting the parameter to it
        // crosses the isolation boundary at the call site. Resolve here
        // instead — this initializer is itself @MainActor.
        self.portfolioStore = portfolioStore ?? PortfolioStore.shared
        self.isInsightsEnabled = UserDefaults.standard.bool(forKey: Self.insightsEnabledKey)

        // Restore persisted sort preferences. Sort lives on the VM so it
        // applies to whichever portfolio is active — the menu in the new
        // PortfolioHeaderBar writes to the same keys.
        if let raw = UserDefaults.standard.string(forKey: Self.sortOptionKey),
           let restored = AssetSortOption(rawValue: raw) {
            self.sortOption = restored
        }
        if UserDefaults.standard.object(forKey: Self.sortAscendingKey) != nil {
            self.sortAscending = UserDefaults.standard.bool(forKey: Self.sortAscendingKey)
        }

        NotificationCenter.default.publisher(for: .whaleFollowStateChanged)
            .receive(on: RunLoop.main)
            .sink { [weak self] notification in
                self?.handleFollowStateChange(notification)
            }
            .store(in: &cancellables)

        // Republish whenever the portfolio store changes so filteredAssets,
        // filteredAlerts, and portfolioDiversificationScore re-render.
        self.portfolioStore.objectWillChange
            .sink { [weak self] _ in self?.objectWillChange.send() }
            .store(in: &cancellables)

        // Load real data on init
        Task { [weak self] in
            await self?.loadData()
            self?.startPriceRefreshTimer()
        }
    }

    deinit {
        priceRefreshTask?.cancel()
    }

    // MARK: - Computed Properties

    /// Tickers in the active portfolio, uppercased Set for O(1) membership.
    private var activeTickerSet: Set<String> {
        Set(portfolioStore.activePortfolio?.tickers.map { $0.uppercased() } ?? [])
    }

    /// Active portfolio's tickers in their stored order — used for `.dateAdded`
    /// sort, which now means "order in the portfolio" (the portfolio is the
    /// closest analogue to the old "added at" concept).
    private var activeTickerOrder: [String] {
        (portfolioStore.activePortfolio?.tickers ?? []).map { $0.uppercased() }
    }

    var filteredAssets: [TrackedAsset] {
        let active = activeTickerSet
        var assets = trackedAssets.filter { active.contains($0.ticker.uppercased()) }

        // Apply search filter
        if !searchText.isEmpty {
            assets = assets.filter { asset in
                asset.ticker.localizedCaseInsensitiveContains(searchText) ||
                asset.companyName.localizedCaseInsensitiveContains(searchText)
            }
        }

        // Apply sorting
        switch sortOption {
        case .name:
            assets.sort { sortAscending ? $0.ticker < $1.ticker : $0.ticker > $1.ticker }
        case .price:
            assets.sort { sortAscending ? $0.price < $1.price : $0.price > $1.price }
        case .change:
            assets.sort { sortAscending ? $0.changePercent < $1.changePercent : $0.changePercent > $1.changePercent }
        case .marketCap:
            // Assets without a market cap (e.g. crypto, indices) sort to the
            // bottom in ascending order, top in descending — matches how the
            // Watchlist screen handles missing fundamentals.
            assets.sort { lhs, rhs in
                switch (lhs.marketCap, rhs.marketCap) {
                case let (l?, r?):
                    return sortAscending ? l < r : l > r
                case (nil, _?):
                    return !sortAscending
                case (_?, nil):
                    return sortAscending
                case (nil, nil):
                    return sortAscending ? lhs.ticker < rhs.ticker : lhs.ticker > rhs.ticker
                }
            }
        case .dateAdded:
            // "Date added" now means position in the active portfolio.
            let order = activeTickerOrder
            let positions = Dictionary(uniqueKeysWithValues:
                order.enumerated().map { ($1, $0) })
            assets.sort { lhs, rhs in
                let l = positions[lhs.ticker.uppercased()] ?? Int.max
                let r = positions[rhs.ticker.uppercased()] ?? Int.max
                return sortAscending ? l < r : l > r
            }
        }

        return assets
    }

    /// Alerts scoped strictly to the active portfolio. Multi-ticker rollups
    /// are trimmed to only their portfolio members, and dollar totals are
    /// re-aggregated from per-item raw amounts so the displayed total always
    /// matches the displayed ticker list. `.market` events have no ticker
    /// and are always shown (macro relevance).
    var filteredAlerts: [AppAlert] {
        let active = activeTickerSet
        return alerts.compactMap { alert -> AppAlert? in
            switch alert {
            case .market:
                return alert
            case .earnings(let data):
                return active.contains(data.ticker.uppercased()) ? alert : nil
            case .whaleTrade(let data):
                let trimmed = data.items.filter { active.contains($0.ticker.uppercased()) }
                guard !trimmed.isEmpty else { return nil }
                return .whaleTrade(AppAlert.WhaleTradeAlertData(
                    action: data.action,
                    totalAmount: Self.recomputedTotal(
                        trimmed.compactMap(\.rawAmount),
                        fallback: data.totalAmount,
                        expectedCount: trimmed.count
                    ),
                    timeWindowLabel: data.timeWindowLabel,
                    items: trimmed
                ))
            case .analystRating(let data):
                let trimmed = data.items.filter { active.contains($0.ticker.uppercased()) }
                guard !trimmed.isEmpty else { return nil }
                return .analystRating(AppAlert.AnalystRatingAlertData(
                    timeWindowLabel: data.timeWindowLabel,
                    items: trimmed
                ))
            case .insiderTransaction(let data):
                let trimmed = data.items.filter { active.contains($0.ticker.uppercased()) }
                guard !trimmed.isEmpty else { return nil }
                return .insiderTransaction(AppAlert.InsiderTransactionAlertData(
                    action: data.action,
                    totalAmount: Self.recomputedTotal(
                        trimmed.compactMap(\.rawAmount),
                        fallback: data.totalAmount,
                        expectedCount: trimmed.count
                    ),
                    timeWindowLabel: data.timeWindowLabel,
                    items: trimmed
                ))
            }
        }
    }

    /// Sum trimmed items' raw amounts and format. If any item is missing
    /// `rawAmount` (older backend), fall back to the server-supplied label
    /// rather than print a misleading $0.
    private static func recomputedTotal(
        _ amounts: [Double], fallback: String, expectedCount: Int
    ) -> String {
        guard amounts.count == expectedCount else { return fallback }
        return formatDollars(amounts.reduce(0, +))
    }

    /// Mirrors backend `_format_amount` in tracking_service.py so re-aggregated
    /// totals look identical to alerts that come straight from the server.
    private static func formatDollars(_ value: Double) -> String {
        let amt = abs(value)
        if amt >= 1_000_000_000 { return String(format: "$%.2fB", amt / 1_000_000_000) }
        if amt >= 1_000_000     { return String(format: "$%.1fM", amt / 1_000_000) }
        if amt >= 1_000         { return String(format: "$%.0fK", amt / 1_000) }
        return String(format: "$%.0f", amt)
    }

    /// Diversification score computed locally from the active portfolio's
    /// per-portfolio holdings (`shares` / `marketValue` on each
    /// `PortfolioItem`). Joined with `trackedAssets` for the live price (so
    /// share-count entries can be converted to dollars) and the price-feed
    /// metadata (sector, asset_type, country, company name) that the
    /// calculator needs but the per-portfolio item doesn't carry.
    var portfolioDiversificationScore: DiversificationScore? {
        guard let active = portfolioStore.activePortfolio else { return nil }
        let assetsByTicker = Dictionary(uniqueKeysWithValues:
            trackedAssets.map { ($0.ticker.uppercased(), $0) })

        let holdings: [PortfolioHolding] = active.items.compactMap { item in
            guard item.isHolding else { return nil }
            let asset = assetsByTicker[item.ticker.uppercased()]

            // The user enters EITHER shares OR a dollar amount per ticker.
            // For shares-only entries we multiply by the live price so the
            // calculator gets a non-zero dollar weight — without this the
            // score collapses to nil whenever every holding was entered as
            // shares (the storage column for market_value stays null).
            let effectiveMarketValue: Double
            if let mv = item.marketValue, mv > 0 {
                effectiveMarketValue = mv
            } else if let shares = item.shares, shares > 0,
                      let price = asset?.price, price > 0 {
                effectiveMarketValue = shares * price
            } else {
                effectiveMarketValue = 0
            }

            let assetTypeLower = (asset?.assetType ?? "stock").lowercased()
            let mappedAssetType: AssetType
            switch assetTypeLower {
            case "etf":     mappedAssetType = .etf
            case "bond":    mappedAssetType = .bond
            case "crypto":  mappedAssetType = .crypto
            case "cash":    mappedAssetType = .cash
            default:
                mappedAssetType = (asset?.country ?? "US") == "US" ? .stock : .internationalStock
            }
            return PortfolioHolding(
                ticker: item.ticker.uppercased(),
                companyName: asset?.companyName ?? item.ticker,
                marketValue: effectiveMarketValue,
                shares: item.shares,
                sector: asset?.sector,
                assetType: mappedAssetType,
                country: asset?.country ?? "US"
            )
        }
        return DiversificationCalculator.calculate(holdings: holdings)
    }

    /// Caption shown next to the diversification score telling the user how
    /// many of the active portfolio's tickers actually contributed to it
    /// (i.e. have shares or a dollar amount). Nil when there's no active
    /// portfolio or it has no tickers — the score itself is also nil in
    /// those cases, so the caption simply hides with the card.
    var portfolioInsightsCoverageNote: String? {
        guard let active = portfolioStore.activePortfolio,
              !active.items.isEmpty else { return nil }
        let used = active.items.filter { $0.isHolding }.count
        let total = active.items.count
        let noun = total == 1 ? "ticker" : "tickers"
        return "Based on \(used) of \(total) \(noun)"
    }

    /// How many of the active portfolio's tickers have shares or a dollar
    /// amount entered (i.e. count toward the score). Drives the "add at least
    /// N holdings" hint: when the user has entered some holdings but fewer than
    /// `DiversificationThresholds.minimumHoldings`, the score is nil and we want
    /// to tell them why instead of showing the blank first-run empty state.
    var enteredHoldingsCount: Int {
        portfolioStore.activePortfolio?.items.filter { $0.isHolding }.count ?? 0
    }

    var filteredWhaleActivities: [WhaleActivity] {
        // Filter by category if needed
        // For now, return all activities
        whaleActivities
    }

    // MARK: - Data Loading (Real API)

    func loadData() async {
        isLoading = true
        defer { isLoading = false }

        // Load assets, portfolios, and whale data in parallel.
        async let feedTask: Bool = loadTrackingFeed()
        async let portfoliosTask: () = portfolioStore.loadPortfolios()
        async let whalesTask: () = loadWhaleData()

        let (feedSucceeded, _, _) = await (feedTask, portfoliosTask, whalesTask)

        // Drop tickers from any portfolio that no longer exist on the master
        // watchlist (e.g. removed on another device). Only purge when the
        // feed call actually succeeded — otherwise we'd wipe real tickers
        // off portfolios on a transient network failure.
        if feedSucceeded {
            await portfolioStore.purgeTickers(notIn: Set(trackedAssets.map(\.ticker)))
        }

        // The diversification score is computed server-side (single source of
        // truth, richer FMP data). Runs after portfolios load so the active
        // portfolio id is known.
        await loadPortfolioInsights()
    }

    /// Fetch the server-computed diversification health score for the active
    /// portfolio. On a genuine connectivity failure we flag it so the UI can
    /// fall back to the on-device estimate; a `null` body (fewer than the
    /// minimum holdings) just clears the score.
    func loadPortfolioInsights() async {
        guard let portfolioId = portfolioStore.activePortfolioId else {
            portfolioInsights = nil
            portfolioInsightsLoadFailed = false
            return
        }
        do {
            let dto = try await apiClient.request(
                endpoint: .getPortfolioInsightsForPortfolio(id: portfolioId),
                responseType: PortfolioInsightsDTO?.self
            )
            portfolioInsights = dto?.toDiversificationScore()
            portfolioInsightsLoadFailed = false
        } catch {
            portfolioInsights = nil
            if let apiError = error as? APIError, case .networkError = apiError {
                portfolioInsightsLoadFailed = true
            } else {
                portfolioInsightsLoadFailed = false
            }
            print("[TrackingVM] ❌ Portfolio insights failed: \(error)")
        }
    }

    /// What the Portfolio Insights card renders: the server score when present,
    /// the on-device estimate only when the server call failed for connectivity.
    var displayedDiversificationScore: DiversificationScore? {
        if let server = portfolioInsights { return server }
        if portfolioInsightsLoadFailed { return portfolioDiversificationScore }
        return nil
    }

    @discardableResult
    private func loadTrackingFeed() async -> Bool {
        do {
            let feed = try await apiClient.request(
                endpoint: .getTrackingAssets,
                responseType: TrackingFeedResponse.self
            )
            self.trackedAssets = feed.assets.map { $0.toTrackedAsset() }
            self.alerts = feed.alerts.map { $0.toAppAlert() }
            print("[TrackingVM] ✅ Loaded \(feed.assets.count) assets, \(feed.alerts.count) alerts from API")
            return true
        } catch {
            print("[TrackingVM] ❌ Tracking feed failed: \(error)")
            // Fallback to sample data on first load if empty
            if trackedAssets.isEmpty {
                trackedAssets = TrackedAsset.sampleData
                alerts = AppAlert.sampleData
                print("[TrackingVM] ⚠️ Using sample data as fallback")
            }
            return false
        }
    }

    // MARK: - Whale Data Loading (Real API)

    private func loadWhaleData() async {
        async let listTask: () = loadWhaleList()
        async let activityTask: () = loadWhaleActivityFeed()
        _ = await (listTask, activityTask)
    }

    private func loadWhaleList(retryCount: Int = 3) async {
        for attempt in 1...retryCount {
            do {
                let dtos = try await apiClient.request(
                    endpoint: .getWhaleList(category: nil),
                    responseType: [TrendingWhaleDTO].self
                )
                let allWhales = dtos.map { $0.toTrendingWhale() }

                // Sync follow state from API
                WhaleService.shared.syncFromAPIResponse(allWhales)

                // Split into followed vs not-followed
                self.trackedWhales = allWhales.filter { $0.isFollowing }
                self.allPopularWhales = allWhales

                // Hero whales: top 5 with descriptions (fallback: top 5 overall)
                let whalesWithDesc = allWhales.filter { !$0.description.isEmpty }
                self.heroWhales = Array((whalesWithDesc.isEmpty ? allWhales : whalesWithDesc).prefix(5))

                // Popular row: next 5 unfollowed whales after the hero (no dup with hero)
                let heroIds = Set(self.heroWhales.map(\.id))
                self.popularWhales = Array(
                    allWhales
                        .filter { !$0.isFollowing && !heroIds.contains($0.id) }
                        .prefix(5)
                )

                print("[TrackingVM] ✅ Loaded \(allWhales.count) whales from API (\(trackedWhales.count) followed)")
                return // success — exit loop
            } catch {
                print("[TrackingVM] ❌ Whale list attempt \(attempt)/\(retryCount) failed: \(error)")
                if attempt < retryCount {
                    let delay = UInt64(attempt) * 2_000_000_000 // 2s, 4s backoff
                    try? await Task.sleep(nanoseconds: delay)
                }
            }
        }
        // All retries exhausted — leave lists empty so UI shows empty state.
        // Never fall back to sample data (sample UUIDs cause 404s on profile fetch).
        print("[TrackingVM] ⚠️ Whale list unavailable after \(retryCount) attempts. Pull to refresh to retry.")
    }

    private func loadWhaleActivityFeed() async {
        do {
            let dtos = try await apiClient.request(
                endpoint: .getWhaleActivity,
                responseType: [WhaleTradeGroupActivityDTO].self
            )
            let activities = dtos.map { $0.toWhaleTradeGroupActivity() }

            // Bucket consecutive same-date activities into a single section
            // so a date header isn't repeated for every whale who traded that
            // day. The feed is already sorted desc by date on the backend.
            var grouped: [GroupedWhaleTrades] = []
            for activity in activities {
                if let last = grouped.last, last.sectionTitle == activity.formattedDate {
                    grouped[grouped.count - 1] = GroupedWhaleTrades(
                        sectionTitle: last.sectionTitle,
                        activities: last.activities + [activity]
                    )
                } else {
                    grouped.append(GroupedWhaleTrades(
                        sectionTitle: activity.formattedDate,
                        activities: [activity]
                    ))
                }
            }
            self.groupedWhaleTrades = grouped
            self.allWhaleTrades = grouped

            print("[TrackingVM] ✅ Loaded \(activities.count) whale activity items from API")
        } catch {
            print("[TrackingVM] ❌ Whale activity failed: \(error)")
            // No sample fallback — leave empty so UI shows empty state
        }
    }

    func refresh() async {
        isRefreshing = true
        await loadData()
        isRefreshing = false
    }

    // MARK: - Live Price Refresh

    /// Periodically re-fetches asset prices while the market is active.
    func startPriceRefreshTimer() {
        priceRefreshTask?.cancel()
        priceRefreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 30_000_000_000) // 30 seconds
                guard !Task.isCancelled else { break }
                guard let self = self else { break }
                guard MarketHoursUtil.isMarketActive() else { continue }
                await self.loadTrackingFeed()
                print("[TrackingVM] 🔄 Auto-refreshed asset prices")
            }
        }
    }

    func stopPriceRefreshTimer() {
        priceRefreshTask?.cancel()
        priceRefreshTask = nil
    }

    /// Called when the Whales tab appears — retries loading if the list is still empty.
    func retryWhaleListIfNeeded() {
        guard allPopularWhales.isEmpty, !isLoading else { return }
        Task { [weak self] in
            await self?.loadWhaleList()
        }
    }

    // MARK: - Asset Actions

    func addNewAsset() {
        showAddAssetSheet = true
    }

    func openSortOptions() {
        showSortSheet = true
    }

    func toggleSort() {
        sortAscending.toggle()
        UserDefaults.standard.set(sortAscending, forKey: Self.sortAscendingKey)
    }

    func selectSortOption(_ option: AssetSortOption) {
        sortOption = option
        UserDefaults.standard.set(option.rawValue, forKey: Self.sortOptionKey)
        showSortSheet = false
    }

    /// Swipe-to-delete: removes the ticker from the active portfolio only.
    /// The master watchlist (and any other portfolio that contains it) is
    /// untouched. Use `removeAssetFromAll` for the long-press path that
    /// fully removes the ticker.
    func removeAsset(_ asset: TrackedAsset) {
        Task {
            do {
                try await portfolioStore.removeTicker(asset.ticker)
            } catch {
                print("[TrackingVM] ❌ Failed to remove \(asset.ticker) from portfolio: \(error)")
            }
        }
    }

    /// Whether the ticker is in the *active portfolio* (or was just added to
    /// it in this session). Used by the search sheet's star icon — filled
    /// means "already in this portfolio", empty means "tap to add here".
    /// Master-watchlist membership is intentionally NOT what this checks:
    /// the user's mental model is portfolio-scoped, not watchlist-scoped.
    func isOnWatchlist(_ ticker: String) -> Bool {
        let upper = ticker.uppercased()
        guard let activeId = portfolioStore.activePortfolioId else { return false }
        if recentlyAddedTickers[activeId]?.contains(upper) == true { return true }
        return portfolioStore.activePortfolio?.tickers.contains(upper) ?? false
    }

    /// Add a ticker to the master watchlist + active portfolio from the in-sheet
    /// search star button. Idempotent on the server (UNIQUE constraint), so it's
    /// safe to call repeatedly. Tickers already on the watchlist still get
    /// pushed into the active portfolio — that's the whole point of tapping
    /// the star while looking at this portfolio.
    func addTickerFromSearch(_ result: StockSearchResult) {
        let symbol = result.ticker.uppercased()

        Task { @MainActor in
            // Self-heal: if the user taps the star before portfolios have
            // loaded (or the initial load silently failed — e.g. backend
            // missing the new endpoint), try reloading once and create a
            // default "Holdings" portfolio if the list is still empty.
            // Without this the tap looks like a dead button.
            if portfolioStore.activePortfolioId == nil {
                print("[TrackingVM] ⚠️ No active portfolio for \(symbol); attempting recovery…")
                await portfolioStore.loadPortfolios()
                if portfolioStore.portfolios.isEmpty {
                    do {
                        _ = try await portfolioStore.createPortfolio(named: "Holdings")
                        print("[TrackingVM] ✅ Created default Holdings portfolio")
                    } catch {
                        print("[TrackingVM] ❌ Couldn't create default portfolio: \(error). Check that backend migration 037 is applied and /api/v1/portfolios is deployed.")
                        return
                    }
                }
            }

            guard let portfolioId = portfolioStore.activePortfolioId else {
                print("[TrackingVM] ❌ Still no active portfolio after recovery; aborting add for \(symbol)")
                return
            }
            // Capture the portfolio at this point — if the user switches mid-
            // flight, we still target the one they were looking at when they
            // tapped (and the optimistic star fills only on that portfolio).
            recentlyAddedTickers[portfolioId, default: []].insert(symbol)

            do {
                try await apiClient.request(
                    endpoint: .addToWatchlist(stockId: result.ticker)
                )
                print("[TrackingVM] ✅ Added \(symbol) to watchlist via search star")
            } catch {
                // Most common reason this fails is that the ticker is already
                // on the master watchlist (409). That's fine — we still want
                // it in the active portfolio.
                print("[TrackingVM] ⚠️ Watchlist add failed for \(symbol) (likely already present): \(error)")
            }
            try? await portfolioStore.addTicker(symbol, to: portfolioId)
            await refresh()
            // Real portfolio.tickers now carries the truth — drop the
            // optimistic marker so future state reads from authoritative data.
            recentlyAddedTickers[portfolioId]?.remove(symbol)
            if recentlyAddedTickers[portfolioId]?.isEmpty == true {
                recentlyAddedTickers.removeValue(forKey: portfolioId)
            }
        }
    }

    /// Long-press "Remove from all portfolios": removes the ticker from every
    /// portfolio it belongs to AND from the master watchlist.
    func removeAssetFromAll(_ asset: TrackedAsset) {
        // Optimistic UI removal from the underlying asset list — the swipe
        // animation looks broken if the row sticks around while the network
        // request flies.
        trackedAssets.removeAll { $0.id == asset.id }

        Task {
            await portfolioStore.removeTickerFromAllPortfolios(asset.ticker)
            do {
                try await apiClient.request(
                    endpoint: .removeFromWatchlist(stockId: asset.ticker)
                )
                print("[TrackingVM] ✅ Removed \(asset.ticker) from watchlist + all portfolios")
            } catch {
                print("[TrackingVM] ❌ Failed to remove \(asset.ticker) from watchlist: \(error)")
            }
        }
    }

    // MARK: - Portfolio Actions

    func setActivePortfolio(_ id: String) {
        portfolioStore.setActivePortfolio(id)
    }

    func openNewPortfolioSheet() {
        showNewPortfolioSheet = true
    }

    func openEditPortfolioSheet() {
        showEditPortfolioSheet = true
    }

    func openManageTickersSheet() {
        showManageTickersSheet = true
    }

    @discardableResult
    func createPortfolio(named name: String) async throws -> Portfolio {
        try await portfolioStore.createPortfolio(named: name)
    }

    func renamePortfolio(id: String, to newName: String) async throws {
        _ = try await portfolioStore.renamePortfolio(id: id, to: newName)
    }

    func deletePortfolio(id: String) async throws {
        try await portfolioStore.deletePortfolio(id: id)
    }

    // MARK: - Portfolio Insights Actions

    func openPortfolioConfigSheet() {
        showPortfolioConfigSheet = true
    }

    /// Push every row's `shares` / `marketValue` from the config sheet to
    /// the backend in a single bulk PUT, scoped to the active portfolio, then
    /// re-fetch the server-computed diversification score so the card reflects
    /// the new holdings immediately.
    ///
    /// `null` for both fields on a row clears that ticker's holding values —
    /// the row stays in the portfolio but stops counting toward the
    /// diversification score.
    func savePortfolioHoldings(_ items: [HoldingUpdateItem]) async throws {
        guard let portfolioId = portfolioStore.activePortfolioId else {
            throw APIError.unknown(message: "No active portfolio selected.")
        }
        try await portfolioStore.setHoldings(items, in: portfolioId)
        await loadPortfolioInsights()
    }

    // MARK: - Navigation

    func viewAssetDetail(_ asset: TrackedAsset) {
        selectedAssetNavigation = SearchSelection(symbol: asset.ticker, type: asset.assetType)
    }

    func viewAlertDetail(_ alert: AppAlert) {
        selectedAlert = alert
    }

    // MARK: - Whale Actions

    func selectWhaleCategory(_ category: WhaleCategory) {
        selectedWhaleCategory = category
    }

    func toggleFollowWhale(_ whale: TrendingWhale) {
        let newFollowing = !whale.isFollowing
        let updatedWhale = TrendingWhale(
            id: whale.id,
            name: whale.name,
            category: whale.category,
            avatarName: whale.avatarName,
            followersCount: whale.followersCount,
            isFollowing: newFollowing,
            title: whale.title,
            description: whale.description,
            recentTradeCount: whale.recentTradeCount
        )

        // Update isFollowing in-place across all lists
        if let index = popularWhales.firstIndex(where: { $0.id == whale.id }) {
            popularWhales[index] = updatedWhale
        }
        if let index = allPopularWhales.firstIndex(where: { $0.id == whale.id }) {
            allPopularWhales[index] = updatedWhale
        }
        if let index = heroWhales.firstIndex(where: { $0.id == whale.id }) {
            heroWhales[index] = updatedWhale
        }

        if newFollowing {
            if !trackedWhales.contains(where: { $0.id == whale.id }) {
                trackedWhales.append(updatedWhale)
            }
        } else {
            trackedWhales.removeAll { $0.id == whale.id }
        }

        // Sync to backend via WhaleService
        WhaleService.shared.toggleFollow(whale.id)
    }

    // MARK: - Follow State Sync

    private func handleFollowStateChange(_ notification: Notification) {
        guard let userInfo = notification.userInfo,
              let isFollowing = userInfo["isFollowing"] as? Bool else { return }

        let whaleId = userInfo["whaleId"] as? String ?? ""
        let whaleName = userInfo["whaleName"] as? String ?? ""

        func matches(_ whale: TrendingWhale) -> Bool {
            whale.id == whaleId || whale.name == whaleName
        }

        func makeUpdated(_ whale: TrendingWhale, following: Bool) -> TrendingWhale {
            TrendingWhale(
                id: whale.id,
                name: whale.name,
                category: whale.category,
                avatarName: whale.avatarName,
                followersCount: whale.followersCount,
                isFollowing: following,
                title: whale.title,
                description: whale.description,
                recentTradeCount: whale.recentTradeCount
            )
        }

        if let index = popularWhales.firstIndex(where: { matches($0) }) {
            popularWhales[index] = makeUpdated(popularWhales[index], following: isFollowing)
        }
        if let index = allPopularWhales.firstIndex(where: { matches($0) }) {
            allPopularWhales[index] = makeUpdated(allPopularWhales[index], following: isFollowing)
        }
        if let index = heroWhales.firstIndex(where: { matches($0) }) {
            heroWhales[index] = makeUpdated(heroWhales[index], following: isFollowing)
        }

        if isFollowing {
            guard !trackedWhales.contains(where: { matches($0) }) else { return }

            if let whale = allPopularWhales.first(where: { matches($0) }) {
                trackedWhales.append(makeUpdated(whale, following: true))
            } else if let whale = heroWhales.first(where: { matches($0) }) {
                trackedWhales.append(makeUpdated(whale, following: true))
            } else {
                let title = userInfo["whaleTitle"] as? String ?? ""
                let newWhale = TrendingWhale(
                    id: whaleId,
                    name: whaleName,
                    category: .investors,
                    avatarName: "",
                    followersCount: 0,
                    isFollowing: true,
                    title: title
                )
                trackedWhales.append(newWhale)
            }
        } else {
            trackedWhales.removeAll { matches($0) }
        }
    }

    func viewMorePopularWhales() {
        showAllWhales = true
    }

    func viewMoreRecentTrades() {
        showAllTrades = true
    }

    func viewWhaleDetail(_ activity: WhaleActivity) {
        if let whale = allPopularWhales.first(where: { $0.name == activity.entityName }) {
            selectedWhaleId = whale.id
        }
    }

    func viewWhaleProfile(_ whale: TrendingWhale) {
        selectedWhaleId = whale.id
    }

    func viewTradeGroupDetail(_ activity: WhaleTradeGroupActivity) {
        // The destination view fetches the real trades + insights from
        // GET /whales/{whaleId}/trade-groups/{groupId} on appear. We just
        // hand it the activity so the header can render while the fetch
        // is in flight.
        selectedTradeGroup = TradeGroupNavigation(activity: activity)
    }

}
