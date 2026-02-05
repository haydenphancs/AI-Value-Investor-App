//
//  TradeGroupDetailViewModel.swift
//  ios
//
//  ViewModel for the Trade Group Detail screen
//

import Foundation
import SwiftUI

@MainActor
class TradeGroupDetailViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var tradeGroup: WhaleTradeGroup
    @Published var selectedFilter: TradeFilterTab = .all
    @Published var selectedTickerSymbol: String?

    let whaleName: String

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

    // MARK: - Initialization

    init(tradeGroup: WhaleTradeGroup, whaleName: String) {
        self.tradeGroup = tradeGroup
        self.whaleName = whaleName
    }

    // MARK: - Actions

    func selectFilter(_ filter: TradeFilterTab) {
        withAnimation(.easeInOut(duration: 0.2)) {
            selectedFilter = filter
        }
    }

    func viewTrade(_ trade: WhaleTrade) {
        selectedTickerSymbol = trade.ticker
    }
}
