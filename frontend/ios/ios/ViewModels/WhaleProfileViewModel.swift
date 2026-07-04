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
    @Published var selectedAssetNavigation: SearchSelection?
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
        // Mutate isFollowing in place — a field-by-field WhaleProfile
        // reconstruction here silently dropped every defaulted field it
        // forgot (firmName vanished from the header on follow-state sync).
        guard var currentProfile = profile else { return }
        let isFollowing = whaleService.isFollowing(whaleId)

        if currentProfile.isFollowing != isFollowing {
            currentProfile.isFollowing = isFollowing
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

                // Merge local follow state from WhaleService — in-place
                // mutation, NOT reconstruction (see updateFollowStatus).
                let isFollowing = self.whaleService.isFollowing(self.whaleId)
                if loadedProfile.isFollowing != isFollowing {
                    loadedProfile.isFollowing = isFollowing
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
        guard var updatedProfile = profile else { return }
        let newFollowState = !updatedProfile.isFollowing

        // Optimistic UI update — in-place mutation, NOT reconstruction
        // (see updateFollowStatus).
        updatedProfile.isFollowing = newFollowState
        profile = updatedProfile

        // Sync to backend via WhaleService (handles optimistic + revert)
        whaleService.toggleFollow(whaleId)

        // Notify TrackingViewModel so the followed whales row stays in sync.
        // Includes the firm so a fallback-built row keeps its firm line.
        NotificationCenter.default.post(
            name: .whaleFollowStateChanged,
            object: nil,
            userInfo: [
                "whaleId": whaleId,
                "whaleName": updatedProfile.name,
                "whaleTitle": updatedProfile.title,
                "whaleFirmName": updatedProfile.firmName ?? "",
                "isFollowing": newFollowState
            ]
        )
    }

    func viewHolding(_ holding: WhaleHolding) {
        selectedAssetNavigation = SearchSelection(symbol: holding.ticker, type: holding.assetType)
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
