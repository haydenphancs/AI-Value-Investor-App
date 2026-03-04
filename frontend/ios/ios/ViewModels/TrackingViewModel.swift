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
    @Published var whaleAlertBanner: WhaleAlertBanner? = WhaleAlertBanner.sampleData

    // Loading States
    @Published var isLoading: Bool = false
    @Published var isRefreshing: Bool = false

    // Sheet States
    @Published var showAddAssetSheet: Bool = false
    @Published var showSortSheet: Bool = false

    // Navigation States
    @Published var selectedTickerSymbol: String?
    @Published var selectedWhaleId: String?
    @Published var selectedTradeGroup: TradeGroupNavigation?
    @Published var selectedAlert: AppAlert?

    // MARK: - Init

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient

        // Recalculate diversification score whenever holdings change
        $portfolioHoldings
            .debounce(for: .milliseconds(300), scheduler: RunLoop.main)
            .sink { [weak self] holdings in
                self?.recalculateDiversification(holdings)
            }
            .store(in: &cancellables)

        NotificationCenter.default.publisher(for: .whaleFollowStateChanged)
            .receive(on: RunLoop.main)
            .sink { [weak self] notification in
                self?.handleFollowStateChange(notification)
            }
            .store(in: &cancellables)

        // Load real data on init
        Task { [weak self] in
            await self?.loadData()
        }
    }

    private func recalculateDiversification(_ holdings: [PortfolioHolding]) {
        diversificationScore = DiversificationCalculator.calculate(holdings: holdings)
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
            // For now, sort by price as proxy for market cap
            assets.sort { sortAscending ? $0.price < $1.price : $0.price > $1.price }
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

        // Load assets, holdings, AND whale data in parallel
        async let feedTask: () = loadTrackingFeed()
        async let holdingsTask: () = loadHoldings()
        async let whalesTask: () = loadWhaleData()

        _ = await (feedTask, holdingsTask, whalesTask)
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
                self.popularWhales = Array(allWhales.filter { !$0.isFollowing }.prefix(5))
                self.allPopularWhales = allWhales

                // Hero whales: first 4 with descriptions (or top 4 overall)
                let whalesWithDesc = allWhales.filter { !$0.description.isEmpty }
                self.heroWhales = Array((whalesWithDesc.isEmpty ? allWhales : whalesWithDesc).prefix(4))

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

            // Group into timeline sections
            self.groupedWhaleTrades = activities.map { activity in
                GroupedWhaleTrades(
                    sectionTitle: activity.formattedDate,
                    activities: [activity]
                )
            }
            self.allWhaleTrades = self.groupedWhaleTrades

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

    // MARK: - Navigation

    func viewAssetDetail(_ asset: TrackedAsset) {
        selectedTickerSymbol = asset.ticker
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
        let tradeGroup = WhaleTradeGroup(
            id: UUID().uuidString,
            date: activity.date,
            tradeCount: activity.tradeCount,
            netAction: activity.action == .bought ? .bought : .sold,
            netAmount: parseAmount(activity.totalAmount),
            summary: activity.summary,
            insights: generateInsights(for: activity),
            trades: generateSampleTrades(for: activity)
        )

        selectedTradeGroup = TradeGroupNavigation(tradeGroup: tradeGroup, whaleName: activity.entityName)
    }

    private func parseAmount(_ amountString: String) -> Double {
        let cleaned = amountString.replacingOccurrences(of: "$", with: "").replacingOccurrences(of: ",", with: "")

        if let value = Double(cleaned.dropLast()) {
            let suffix = cleaned.last
            switch suffix {
            case "B": return value * 1_000_000_000
            case "M": return value * 1_000_000
            case "K": return value * 1_000
            default: return Double(cleaned) ?? 0
            }
        }
        return 0
    }

    private func generateInsights(for activity: WhaleTradeGroupActivity) -> [String] {
        var insights: [String] = []

        if activity.tradeCount > 5 {
            insights.append("High trading activity detected")
        }

        if activity.action == .bought {
            insights.append("Bullish positioning in this sector")
        } else {
            insights.append("Portfolio rebalancing activity")
        }

        return insights
    }

    private func generateSampleTrades(for activity: WhaleTradeGroupActivity) -> [WhaleTrade] {
        let tickers = ["AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA"]
        let companies = ["Apple Inc.", "Microsoft", "Alphabet Inc.", "Amazon", "Tesla", "NVIDIA"]

        return (0..<min(activity.tradeCount, 6)).map { index in
            let tradeType: WhaleTradeType = {
                let random = Int.random(in: 0...3)
                switch random {
                case 0: return .new
                case 1: return .increased
                case 2: return .decreased
                default: return .closed
                }
            }()

            let previousAllocation = Double.random(in: 0...15)
            let newAllocation: Double

            switch tradeType {
            case .new:
                newAllocation = Double.random(in: 1...10)
            case .increased:
                newAllocation = previousAllocation + Double.random(in: 1...5)
            case .decreased:
                newAllocation = max(0, previousAllocation - Double.random(in: 1...5))
            case .closed:
                newAllocation = 0
            }

            return WhaleTrade(
                id: UUID().uuidString,
                ticker: tickers[index % tickers.count],
                companyName: companies[index % companies.count],
                action: activity.action == .bought ? .bought : .sold,
                tradeType: tradeType,
                amount: Double.random(in: 1000000...50000000),
                previousAllocation: previousAllocation,
                newAllocation: newAllocation,
                date: activity.date
            )
        }
    }

    func viewWhaleAlert() {
        print("View whale alert tapped")
    }
}
