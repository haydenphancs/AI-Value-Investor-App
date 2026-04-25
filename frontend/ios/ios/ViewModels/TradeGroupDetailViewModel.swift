//
//  TradeGroupDetailViewModel.swift
//  ios
//
//  ViewModel for the Trade Group Detail screen
//

import Foundation
import SwiftUI
import Combine

@MainActor
class TradeGroupDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var tradeGroup: WhaleTradeGroup
    @Published var selectedFilter: TradeFilterTab = .all
    @Published var selectedAssetNavigation: SearchSelection?
    @Published var isLoading: Bool = false
    @Published var loadError: String?

    let whaleName: String

    // MARK: - Private

    private let apiClient: APIClient
    private let whaleId: String?
    private let groupId: String?

    // MARK: - Computed Properties

    var filteredTrades: [WhaleTrade] {
        switch selectedFilter {
        case .all:
            return tradeGroup.trades
        case .new:
            return tradeGroup.trades.filter { $0.tradeType == .new }
        case .increased:
            return tradeGroup.trades.filter { $0.tradeType == .increased }
        case .decreased:
            return tradeGroup.trades.filter { $0.tradeType == .decreased }
        case .closed:
            return tradeGroup.trades.filter { $0.tradeType == .closed }
        }
    }

    var filterCounts: [TradeFilterTab: Int] {
        var counts: [TradeFilterTab: Int] = [:]
        counts[.all] = tradeGroup.trades.count
        counts[.new] = tradeGroup.trades.filter { $0.tradeType == .new }.count
        counts[.increased] = tradeGroup.trades.filter { $0.tradeType == .increased }.count
        counts[.decreased] = tradeGroup.trades.filter { $0.tradeType == .decreased }.count
        counts[.closed] = tradeGroup.trades.filter { $0.tradeType == .closed }.count
        return counts
    }

    /// `true` while the initial fetch is running and we still have no trades
    /// to render — used to swap the trade list for a spinner.
    var showsLoadingState: Bool {
        isLoading && tradeGroup.trades.isEmpty
    }

    // MARK: - Initialization

    /// Production path: build a placeholder group from the activity so the
    /// header renders immediately, then fetch the real trades + insights from
    /// `GET /whales/{whaleId}/trade-groups/{groupId}`.
    init(activity: WhaleTradeGroupActivity, whaleName: String, apiClient: APIClient = .shared) {
        self.tradeGroup = WhaleTradeGroup(
            id: activity.id,
            date: activity.date,
            tradeCount: activity.tradeCount,
            netAction: activity.action == .bought ? .bought : .sold,
            netAmount: 0,
            summary: activity.summary,
            insights: [],
            trades: []
        )
        self.whaleName = whaleName
        self.apiClient = apiClient
        self.whaleId = activity.whaleId
        self.groupId = activity.id
        self.isLoading = !activity.whaleId.isEmpty

        Task { [weak self] in await self?.loadTradeGroup() }
    }

    /// Preview / offline path: render an already-built trade group as-is.
    init(tradeGroup: WhaleTradeGroup, whaleName: String, apiClient: APIClient = .shared) {
        self.tradeGroup = tradeGroup
        self.whaleName = whaleName
        self.apiClient = apiClient
        self.whaleId = nil
        self.groupId = nil
    }

    // MARK: - Data Loading

    func loadTradeGroup() async {
        guard let whaleId = whaleId, !whaleId.isEmpty,
              let groupId = groupId, !groupId.isEmpty else {
            isLoading = false
            return
        }

        isLoading = true
        loadError = nil
        defer { isLoading = false }

        do {
            let dto = try await apiClient.request(
                endpoint: .getWhaleTradeGroupDetail(whaleId: whaleId, groupId: groupId),
                responseType: WhaleTradeGroupDTO.self
            )
            self.tradeGroup = dto.toWhaleTradeGroup()
            print("[TradeGroupDetailVM] ✅ Loaded \(dto.trades.count) trades for group \(groupId)")
        } catch {
            print("[TradeGroupDetailVM] ❌ Failed to load trade group \(groupId): \(error)")
            self.loadError = "Couldn't load these trades. Pull down to retry."
        }
    }

    // MARK: - Actions

    func selectFilter(_ filter: TradeFilterTab) {
        withAnimation(.easeInOut(duration: 0.2)) {
            selectedFilter = filter
        }
    }

    func viewTrade(_ trade: WhaleTrade) {
        selectedAssetNavigation = SearchSelection(symbol: trade.ticker, type: trade.assetType)
    }
}
