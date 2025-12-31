//
//  TrackingViewModel.swift
//  ios
//
//  ViewModel for the Tracking screen
//

import Foundation
import SwiftUI

@MainActor
class TrackingViewModel: ObservableObject {
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
    @Published var selectedWhaleCategory: WhaleCategory = .following
    @Published var whaleActivities: [WhaleActivity] = WhaleActivity.sampleData
    @Published var trendingWhales: [TrendingWhale] = TrendingWhale.sampleData

    // Loading States
    @Published var isLoading: Bool = false
    @Published var isRefreshing: Bool = false

    // Sheet States
    @Published var showAddAssetSheet: Bool = false
    @Published var showSortSheet: Bool = false

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
        if let index = trendingWhales.firstIndex(where: { $0.id == whale.id }) {
            let updatedWhale = TrendingWhale(
                name: whale.name,
                category: whale.category,
                avatarName: whale.avatarName,
                followersCount: whale.followersCount,
                isFollowing: !whale.isFollowing
            )
            trendingWhales[index] = updatedWhale
        }
    }

    func viewAssetDetail(_ asset: TrackedAsset) {
        print("View asset detail: \(asset.ticker)")
    }

    func viewAlertDetail(_ alert: AlertEvent) {
        print("View alert detail: \(alert.title)")
    }

    func viewWhaleDetail(_ activity: WhaleActivity) {
        print("View whale detail: \(activity.entityName)")
    }
}
