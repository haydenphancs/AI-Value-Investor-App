//
//  WhaleProfileViewModel.swift
//  ios
//
//  ViewModel for the Whale Profile screen
//

import Foundation
import SwiftUI
import Combine

@MainActor
class WhaleProfileViewModel: ObservableObject {
    // MARK: - Published Properties

    @Published var profile: WhaleProfile?
    @Published var isLoading: Bool = false
    @Published var isRefreshing: Bool = false
    @Published var errorMessage: String?

    // Navigation
    @Published var selectedTickerSymbol: String?
    @Published var selectedTradeGroupId: String?
    @Published var showAllHoldings: Bool = false
    @Published var showRecentTradesInfo: Bool = false

    // MARK: - Configuration

    private let whaleId: String
    private let maxVisibleHoldings: Int = 10
    private let maxVisibleTrades: Int = 5

    // MARK: - Computed Properties

    var displayedHoldings: [WhaleHolding] {
        guard let profile = profile else { return [] }
        if showAllHoldings {
            return profile.currentHoldings
        }
        return Array(profile.currentHoldings.prefix(maxVisibleHoldings))
    }

    var displayedTradeGroups: [WhaleTradeGroup] {
        guard let profile = profile else { return [] }
        return profile.recentTradeGroups
    }

    var hasMoreHoldings: Bool {
        guard let profile = profile else { return false }
        return profile.currentHoldings.count > maxVisibleHoldings
    }

    func tradeGroup(for id: String) -> WhaleTradeGroup? {
        profile?.recentTradeGroups.first { $0.id == id }
    }

    // MARK: - Initialization

    init(whaleId: String) {
        self.whaleId = whaleId
        loadProfile()
    }

    // MARK: - Data Loading

    func loadProfile() {
        isLoading = true
        errorMessage = nil

        // Simulate network delay and load mock data
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self = self else { return }

            // In a real app, fetch from API based on whaleId
            switch self.whaleId {
            case "warren-buffett":
                self.profile = WhaleProfile.warrenBuffett
            case "cathie-wood":
                self.profile = WhaleProfile.cathieWood
            default:
                self.profile = WhaleProfile.warrenBuffett
            }

            self.isLoading = false
        }
    }

    func refresh() async {
        isRefreshing = true

        // Simulate network delay
        try? await Task.sleep(nanoseconds: 1_000_000_000)

        // Reload profile data
        loadProfile()

        isRefreshing = false
    }

    // MARK: - Actions

    func toggleFollow() {
        guard var currentProfile = profile else { return }
        currentProfile = WhaleProfile(
            id: currentProfile.id,
            name: currentProfile.name,
            title: currentProfile.title,
            avatarURL: currentProfile.avatarURL,
            riskProfile: currentProfile.riskProfile,
            portfolioValue: currentProfile.portfolioValue,
            ytdReturn: currentProfile.ytdReturn,
            sectorExposure: currentProfile.sectorExposure,
            currentHoldings: currentProfile.currentHoldings,
            recentTradeGroups: currentProfile.recentTradeGroups,
            recentTrades: currentProfile.recentTrades,
            behaviorSummary: currentProfile.behaviorSummary,
            sentimentSummary: currentProfile.sentimentSummary,
            isFollowing: !currentProfile.isFollowing
        )
        profile = currentProfile
    }

    func viewHolding(_ holding: WhaleHolding) {
        selectedTickerSymbol = holding.ticker
    }

    func viewTradeGroup(_ group: WhaleTradeGroup) {
        selectedTradeGroupId = group.id
    }

    func viewMoreHoldings() {
        showAllHoldings = true
    }

    func showOptionsMenu() {
        // Handle options menu
        print("Options menu tapped")
    }
}
