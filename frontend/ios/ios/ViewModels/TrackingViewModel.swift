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

    // Whales Tab (unchanged — stays as sample data)
    @Published var selectedWhaleCategory: WhaleCategory = .investors
    @Published var whaleActivities: [WhaleActivity] = WhaleActivity.sampleData
    @Published var trackedWhales: [TrendingWhale] = TrendingWhale.trackedWhalesData
    @Published var popularWhales: [TrendingWhale] = TrendingWhale.topPopularWhalesData
    @Published var heroWhales: [TrendingWhale] = TrendingWhale.heroWhalesData
    @Published var allPopularWhales: [TrendingWhale] = TrendingWhale.allPopularWhalesData
    @Published var showAllWhales: Bool = false
    @Published var showAllTrades: Bool = false
    @Published var groupedWhaleTrades: [GroupedWhaleTrades] = WhaleTradeGroupActivity.groupedSampleData
    @Published var allWhaleTrades: [GroupedWhaleTrades] = WhaleTradeGroupActivity.allGroupedSampleData
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

        // Load assets feed and holdings in parallel
        async let feedTask: () = loadTrackingFeed()
        async let holdingsTask: () = loadHoldings()

        _ = await (feedTask, holdingsTask)
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

    func refresh() async {
        isRefreshing = true
        await loadData()
        isRefreshing = false
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

    // MARK: - Whale Actions (unchanged)

    func selectWhaleCategory(_ category: WhaleCategory) {
        selectedWhaleCategory = category
    }

    func toggleFollowWhale(_ whale: TrendingWhale) {
        let newFollowing = !whale.isFollowing
        let updatedWhale = TrendingWhale(
            name: whale.name,
            category: whale.category,
            avatarName: whale.avatarName,
            followersCount: whale.followersCount,
            isFollowing: newFollowing,
            title: whale.title,
            description: whale.description,
            recentTradeCount: whale.recentTradeCount
        )

        // Update isFollowing in-place across all lists (don't remove)
        if let index = popularWhales.firstIndex(where: { $0.name == whale.name }) {
            popularWhales[index] = updatedWhale
        }
        if let index = allPopularWhales.firstIndex(where: { $0.name == whale.name }) {
            allPopularWhales[index] = updatedWhale
        }
        if let index = heroWhales.firstIndex(where: { $0.name == whale.name }) {
            heroWhales[index] = updatedWhale
        }

        if newFollowing {
            if !trackedWhales.contains(where: { $0.name == whale.name }) {
                trackedWhales.append(updatedWhale)
            }
        } else {
            trackedWhales.removeAll { $0.name == whale.name }
        }
    }

    // MARK: - Follow State Sync

    private func handleFollowStateChange(_ notification: Notification) {
        guard let userInfo = notification.userInfo,
              let isFollowing = userInfo["isFollowing"] as? Bool else { return }

        let whaleId = userInfo["whaleId"] as? String ?? ""
        let whaleName = userInfo["whaleName"] as? String ?? ""

        func nameToId(_ name: String) -> String {
            name.lowercased().replacingOccurrences(of: " ", with: "-")
        }
        func matches(_ whale: TrendingWhale) -> Bool {
            nameToId(whale.name) == whaleId || whale.name == whaleName
        }

        func makeUpdated(_ whale: TrendingWhale, following: Bool) -> TrendingWhale {
            TrendingWhale(
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
        selectedWhaleId = activity.entityName.lowercased().replacingOccurrences(of: " ", with: "-")
    }

    func viewWhaleProfile(_ whale: TrendingWhale) {
        selectedWhaleId = whale.name.lowercased().replacingOccurrences(of: " ", with: "-")
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
