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
    private let whaleService = WhaleService.shared
    private let apiClient: APIClient
    private var cancellables = Set<AnyCancellable>()

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

    init(whaleId: String, apiClient: APIClient = .shared) {
        self.whaleId = whaleId
        self.apiClient = apiClient
        loadProfile()
        observeFollowChanges()
    }

    // MARK: - Observation

    private func observeFollowChanges() {
        whaleService.$followedWhaleIds
            .sink { [weak self] _ in
                self?.updateFollowStatus()
            }
            .store(in: &cancellables)
    }

    private func updateFollowStatus() {
        guard var currentProfile = profile else { return }
        let isFollowing = whaleService.isFollowing(whaleId)

        if currentProfile.isFollowing != isFollowing {
            currentProfile = WhaleProfile(
                id: currentProfile.id,
                name: currentProfile.name,
                title: currentProfile.title,
                description: currentProfile.description,
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
                isFollowing: isFollowing
            )
            profile = currentProfile
        }
    }

    // MARK: - Data Loading

    func loadProfile() {
        isLoading = true
        errorMessage = nil

        Task { [weak self] in
            guard let self = self else { return }

            do {
                let dto = try await self.apiClient.request(
                    endpoint: .getWhaleProfile(whaleId: self.whaleId),
                    responseType: WhaleProfileDTO.self
                )
                var loadedProfile = dto.toWhaleProfile()

                // Merge local follow state from WhaleService
                let isFollowing = self.whaleService.isFollowing(self.whaleId)
                if loadedProfile.isFollowing != isFollowing {
                    loadedProfile = WhaleProfile(
                        id: loadedProfile.id,
                        name: loadedProfile.name,
                        title: loadedProfile.title,
                        description: loadedProfile.description,
                        avatarURL: loadedProfile.avatarURL,
                        riskProfile: loadedProfile.riskProfile,
                        portfolioValue: loadedProfile.portfolioValue,
                        ytdReturn: loadedProfile.ytdReturn,
                        sectorExposure: loadedProfile.sectorExposure,
                        currentHoldings: loadedProfile.currentHoldings,
                        recentTradeGroups: loadedProfile.recentTradeGroups,
                        recentTrades: loadedProfile.recentTrades,
                        behaviorSummary: loadedProfile.behaviorSummary,
                        sentimentSummary: loadedProfile.sentimentSummary,
                        isFollowing: isFollowing
                    )
                }

                self.profile = loadedProfile
                self.isLoading = false
                print("[WhaleProfileVM] ✅ Loaded profile for \(loadedProfile.name) from API")
            } catch {
                print("[WhaleProfileVM] ❌ API profile load failed: \(error)")

                // Fallback to sample data
                self.loadSampleProfile()
                self.isLoading = false
                self.errorMessage = "Failed to load profile. Showing cached data."
            }
        }
    }

    private func loadSampleProfile() {
        // No sample fallback — real UUIDs won't match slug-based sample data.
        // The error state is shown via errorMessage instead.
        print("[WhaleProfileVM] ⚠️ No sample fallback available for whale \(whaleId)")
    }

    func refresh() async {
        isRefreshing = true
        loadProfile()
        try? await Task.sleep(nanoseconds: 500_000_000)
        isRefreshing = false
    }

    // MARK: - Actions

    func toggleFollow() {
        guard let currentProfile = profile else { return }
        let newFollowState = !currentProfile.isFollowing

        // Optimistic UI update
        let updatedProfile = WhaleProfile(
            id: currentProfile.id,
            name: currentProfile.name,
            title: currentProfile.title,
            description: currentProfile.description,
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
            isFollowing: newFollowState
        )
        profile = updatedProfile

        // Sync to backend via WhaleService (handles optimistic + revert)
        whaleService.toggleFollow(whaleId)

        // Notify TrackingViewModel so the followed whales row stays in sync
        NotificationCenter.default.post(
            name: .whaleFollowStateChanged,
            object: nil,
            userInfo: [
                "whaleId": whaleId,
                "whaleName": currentProfile.name,
                "whaleTitle": currentProfile.title,
                "isFollowing": newFollowState
            ]
        )
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
        print("Options menu tapped")
    }
}
