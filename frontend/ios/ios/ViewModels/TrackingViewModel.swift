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

    // MARK: - Published Properties

    // Tab State
    @Published var selectedTab: TrackingTab = .assets
    @Published var searchText: String = ""

    // Assets Tab
    @Published var trackedAssets: [TrackedAsset] = TrackedAsset.sampleData
    @Published var sortOption: AssetSortOption = .name
    @Published var sortAscending: Bool = true

    // Alerts & Events
    @Published var alertEvents: [AlertEvent] = AlertEvent.sampleData
    @Published var smartMoneyAlert: SmartMoneyAlert? = SmartMoneyAlert.sampleData

    // Portfolio Insights
    @Published var diversificationScore: DiversificationScore? = DiversificationScore.sampleData

    // Whales Tab
    @Published var selectedWhaleCategory: WhaleCategory = .investors
    @Published var whaleActivities: [WhaleActivity] = WhaleActivity.sampleData
    @Published var trackedWhales: [TrendingWhale] = TrendingWhale.trackedWhalesData
    @Published var popularWhales: [TrendingWhale] = TrendingWhale.topPopularWhalesData
    @Published var heroWhales: [TrendingWhale] = TrendingWhale.heroWhalesData
    @Published var allPopularWhales: [TrendingWhale] = TrendingWhale.allPopularWhalesData
    @Published var showAllWhales: Bool = false
    @Published var groupedWhaleTrades: [GroupedWhaleTrades] = WhaleTradeGroupActivity.groupedSampleData
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

    // MARK: - Init

    init() {
        NotificationCenter.default.publisher(for: .whaleFollowStateChanged)
            .receive(on: RunLoop.main)
            .sink { [weak self] notification in
                self?.handleFollowStateChange(notification)
            }
            .store(in: &cancellables)
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

    // MARK: - Actions

    func refresh() async {
        isRefreshing = true
        // Simulate network delay
        try? await Task.sleep(nanoseconds: 1_500_000_000)

        // In a real app, fetch data from API here
        // For now, we use sample data

        isRefreshing = false
    }

    func loadData() async {
        isLoading = true
        // Simulate initial load
        try? await Task.sleep(nanoseconds: 800_000_000)
        isLoading = false
    }

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
        trackedAssets.removeAll { $0.id == asset.id }
    }

    func selectWhaleCategory(_ category: WhaleCategory) {
        selectedWhaleCategory = category
        // Reload whale activities based on category
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
            // Add to tracked whales if not already there
            if !trackedWhales.contains(where: { $0.name == whale.name }) {
                trackedWhales.append(updatedWhale)
            }
        } else {
            // Remove from tracked whales
            trackedWhales.removeAll { $0.name == whale.name }
        }
    }

    // MARK: - Follow State Sync

    private func handleFollowStateChange(_ notification: Notification) {
        guard let userInfo = notification.userInfo,
              let whaleName = userInfo["whaleName"] as? String,
              let isFollowing = userInfo["isFollowing"] as? Bool else { return }

        // Helper to create an updated whale with new follow state
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

        // Update isFollowing in-place across all discovery lists
        if let index = popularWhales.firstIndex(where: { $0.name == whaleName }) {
            popularWhales[index] = makeUpdated(popularWhales[index], following: isFollowing)
        }
        if let index = allPopularWhales.firstIndex(where: { $0.name == whaleName }) {
            allPopularWhales[index] = makeUpdated(allPopularWhales[index], following: isFollowing)
        }
        if let index = heroWhales.firstIndex(where: { $0.name == whaleName }) {
            heroWhales[index] = makeUpdated(heroWhales[index], following: isFollowing)
        }

        if isFollowing {
            // Already tracked — nothing to do
            guard !trackedWhales.contains(where: { $0.name == whaleName }) else { return }

            // Find whale data from any list to copy into tracked
            if let whale = allPopularWhales.first(where: { $0.name == whaleName }) {
                trackedWhales.append(makeUpdated(whale, following: true))
            } else if let whale = heroWhales.first(where: { $0.name == whaleName }) {
                trackedWhales.append(makeUpdated(whale, following: true))
            } else {
                // Whale not in any list — create from notification data
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
            // Unfollowing — remove from tracked
            trackedWhales.removeAll { $0.name == whaleName }
        }
    }

    func viewMorePopularWhales() {
        showAllWhales = true
    }

    func viewAssetDetail(_ asset: TrackedAsset) {
        selectedTickerSymbol = asset.ticker
    }

    func viewAlertDetail(_ alert: AlertEvent) {
        print("View alert detail: \(alert.title)")
    }

    func viewWhaleDetail(_ activity: WhaleActivity) {
        // Convert entity name to whale ID format
        selectedWhaleId = activity.entityName.lowercased().replacingOccurrences(of: " ", with: "-")
    }

    func viewWhaleProfile(_ whale: TrendingWhale) {
        selectedWhaleId = whale.name.lowercased().replacingOccurrences(of: " ", with: "-")
    }

    func viewTradeGroupDetail(_ activity: WhaleTradeGroupActivity) {
        selectedWhaleId = activity.entityName.lowercased().replacingOccurrences(of: " ", with: "-")
    }

    func viewWhaleAlert() {
        print("View whale alert tapped")
    }
}
