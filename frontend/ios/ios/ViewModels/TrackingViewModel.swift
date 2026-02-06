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
    @Published var trackedWhales: [TrendingWhale] = []
    @Published var popularWhales: [TrendingWhale] = []
    @Published var heroWhales: [TrendingWhale] = TrendingWhale.heroWhalesData
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
    @Published var selectedTradeGroup: TradeGroupNavigation?
    
    // MARK: - Private Properties
    
    private let whaleService = WhaleService.shared
    private var cancellables = Set<AnyCancellable>()
    
    // MARK: - Initialization
    
    init() {
        setupWhales()
        observeFollowChanges()
    }
    
    // MARK: - Setup
    
    private func setupWhales() {
        updateWhaleListsFromService()
    }
    
    private func observeFollowChanges() {
        // Update whale lists when follow status changes
        whaleService.$followedWhaleIds
            .sink { [weak self] _ in
                self?.updateWhaleListsFromService()
            }
            .store(in: &cancellables)
    }
    
    private func updateWhaleListsFromService() {
        let allWhales = TrendingWhale.sampleData
        let followedIds = whaleService.followedWhaleIds
        
        // Split whales based on follow status from shared service
        trackedWhales = allWhales.filter { whale in
            let whaleId = whale.name.lowercased().replacingOccurrences(of: " ", with: "-")
            return followedIds.contains(whaleId)
        }.map { whale in
            TrendingWhale(
                name: whale.name,
                category: whale.category,
                avatarName: whale.avatarName,
                followersCount: whale.followersCount,
                isFollowing: true,
                title: whale.title,
                description: whale.description,
                recentTradeCount: whale.recentTradeCount
            )
        }
        
        popularWhales = allWhales.filter { whale in
            let whaleId = whale.name.lowercased().replacingOccurrences(of: " ", with: "-")
            return !followedIds.contains(whaleId)
        }.map { whale in
            TrendingWhale(
                name: whale.name,
                category: whale.category,
                avatarName: whale.avatarName,
                followersCount: whale.followersCount,
                isFollowing: false,
                title: whale.title,
                description: whale.description,
                recentTradeCount: whale.recentTradeCount
            )
        }
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
        // Convert whale name to ID format
        let whaleId = whale.name.lowercased().replacingOccurrences(of: " ", with: "-")
        
        // Toggle in shared service - this will automatically update both lists
        whaleService.toggleFollow(whaleId)
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
        // Create a WhaleTradeGroup from the activity
        // In a real app, you'd fetch the full trade group data from your API
        // For now, we'll create sample data based on the activity
        
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
    
    // Helper to parse amount string like "$4.34B" to Double
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
    
    // Generate sample insights
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
    
    // Generate sample trades
    private func generateSampleTrades(for activity: WhaleTradeGroupActivity) -> [WhaleTrade] {
        // Generate sample trades based on the activity
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
