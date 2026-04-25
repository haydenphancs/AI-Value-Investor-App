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

    // Portfolio Insights
    @Published var portfolioHoldings: [PortfolioHolding] = []
    @Published var diversificationScore: DiversificationScore?

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
    @Published var showAddHoldingSheet: Bool = false

    // Navigation States
    @Published var selectedAssetNavigation: SearchSelection?
    @Published var selectedSearchResult: SearchSelection?
    @Published var selectedWhaleId: String?
    @Published var selectedTradeGroup: TradeGroupNavigation?
    @Published var selectedAlert: AppAlert?

    // MARK: - Init

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient

        NotificationCenter.default.publisher(for: .whaleFollowStateChanged)
            .receive(on: RunLoop.main)
            .sink { [weak self] notification in
                self?.handleFollowStateChange(notification)
            }
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

    var filteredAssets: [TrackedAsset] {
        var assets = trackedAssets

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
            // Keep original order from backend (added_at desc)
            if !sortAscending { assets.reverse() }
        }

        return assets
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

        // Load assets, holdings, insights, AND whale data in parallel
        async let feedTask: () = loadTrackingFeed()
        async let holdingsTask: () = loadHoldings()
        async let insightsTask: () = loadPortfolioInsights()
        async let whalesTask: () = loadWhaleData()

        _ = await (feedTask, holdingsTask, insightsTask, whalesTask)
    }

    private func loadTrackingFeed() async {
        do {
            let feed = try await apiClient.request(
                endpoint: .getTrackingAssets,
                responseType: TrackingFeedResponse.self
            )
            self.trackedAssets = feed.assets.map { $0.toTrackedAsset() }
            self.alerts = feed.alerts.map { $0.toAppAlert() }
            print("[TrackingVM] ✅ Loaded \(feed.assets.count) assets, \(feed.alerts.count) alerts from API")
        } catch {
            print("[TrackingVM] ❌ Tracking feed failed: \(error)")
            // Fallback to sample data on first load if empty
            if trackedAssets.isEmpty {
                trackedAssets = TrackedAsset.sampleData
                alerts = AppAlert.sampleData
                print("[TrackingVM] ⚠️ Using sample data as fallback")
            }
        }
    }

    private func loadHoldings() async {
        do {
            let holdings = try await apiClient.request(
                endpoint: .getHoldings,
                responseType: [PortfolioHolding].self
            )
            self.portfolioHoldings = holdings
            print("[TrackingVM] ✅ Loaded \(holdings.count) portfolio holdings from API")
        } catch {
            print("[TrackingVM] ❌ Holdings load failed: \(error)")
            // Fallback to sample data
            if portfolioHoldings.isEmpty {
                portfolioHoldings = PortfolioHolding.sampleData
                print("[TrackingVM] ⚠️ Using sample holdings as fallback")
            }
        }
    }

    /// Fetch the server-computed Portfolio Insights payload (diversification
    /// score, sector breakdown, sub-scores). The backend returns ``null`` when
    /// the user has fewer than the minimum number of holdings — we map that to
    /// ``nil`` so the UI shows its empty state.
    private func loadPortfolioInsights() async {
        do {
            let dto = try await apiClient.request(
                endpoint: .getPortfolioInsights,
                responseType: PortfolioInsightsDTO?.self
            )
            self.diversificationScore = dto?.toDiversificationScore()
            print("[TrackingVM] ✅ Loaded portfolio insights (score=\(dto?.score ?? -1))")
        } catch {
            print("[TrackingVM] ❌ Portfolio insights load failed: \(error)")
            // Dev fallback: if we're showing sample holdings, compute locally
            // so the preview card still renders. In production this branch is
            // a no-op because real holdings get a real server response.
            if portfolioHoldings.elementsEqual(PortfolioHolding.sampleData, by: { $0.ticker == $1.ticker }) {
                diversificationScore = DiversificationCalculator.calculate(holdings: portfolioHoldings)
                print("[TrackingVM] ⚠️ Using local diversification calc on sample data")
            }
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
    }

    func selectSortOption(_ option: AssetSortOption) {
        sortOption = option
        showSortSheet = false
    }

    func removeAsset(_ asset: TrackedAsset) {
        // Optimistic UI removal
        trackedAssets.removeAll { $0.id == asset.id }

        // Fire-and-forget backend call
        Task {
            do {
                try await apiClient.request(
                    endpoint: .removeFromWatchlist(stockId: asset.ticker)
                )
                print("[TrackingVM] ✅ Removed \(asset.ticker) from watchlist")
            } catch {
                print("[TrackingVM] ❌ Failed to remove \(asset.ticker) from watchlist: \(error)")
                // Could re-add on failure, but for now just log
            }
        }
    }

    // MARK: - Holding Actions

    func openAddHoldingSheet() {
        showAddHoldingSheet = true
    }

    /// Add a holding by share count (preferred — ``marketValue`` will track
    /// the live FMP price) or by static dollar amount. After the upsert,
    /// re-fetches holdings + insights so the score updates.
    func addHolding(
        ticker: String,
        companyName: String?,
        shares: Double?,
        marketValue: Double?,
        assetType: String?
    ) async throws {
        try await apiClient.request(
            endpoint: .addHolding(
                ticker: ticker,
                companyName: companyName,
                shares: shares,
                marketValue: marketValue,
                assetType: assetType
            )
        )
        await loadHoldings()
        await loadPortfolioInsights()
    }

    /// Remove a holding. Optimistically pulls it from the local list and
    /// re-fetches insights so the score updates without a full reload.
    func removeHolding(_ holding: PortfolioHolding) {
        portfolioHoldings.removeAll { $0.id == holding.id }
        Task {
            do {
                try await apiClient.request(
                    endpoint: .deleteHolding(ticker: holding.ticker)
                )
                await loadPortfolioInsights()
                print("[TrackingVM] ✅ Removed \(holding.ticker) from holdings")
            } catch {
                print("[TrackingVM] ❌ Failed to remove \(holding.ticker) from holdings: \(error)")
                await loadHoldings() // restore on failure
            }
        }
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
