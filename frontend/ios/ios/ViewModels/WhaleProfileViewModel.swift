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
    /// A Follow tap the caller's plan does not allow → the plan sheet.
    @Published var showPaywall: Bool = false

    // MARK: - Configuration

    private let whaleId: String
    private let maxVisibleHoldings: Int = 10
    private let maxVisibleTrades: Int = 5
    private let whaleService = WhaleService.shared
    private let apiClient: APIClient
    private var cancellables = Set<AnyCancellable>()
    /// The profile fetch in flight, so a newer load can cancel an older one and
    /// `refresh()` can await the real thing.
    private var loadTask: Task<Void, Never>?

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
        // ⚠️ Consume the EMITTED value, and hop off the publishing frame.
        //
        // `@Published` fires during `willSet`, so at the moment this sink runs the
        // property still holds its PRE-mutation value. The old body threw the emitted
        // set away and called `whaleService.isFollowing(whaleId)` — re-reading exactly
        // that stale property — so every correction it made was computed from the state
        // it was trying to correct. A failed unfollow reverted in the service and this
        // screen kept showing the wrong pill until the next full profile load.
        //
        // `.receive(on: RunLoop.main)` matches what TrackingViewModel already does for
        // the same publisher.
        let id = whaleId
        whaleService.$followedWhaleIds
            .receive(on: RunLoop.main)
            .sink { [weak self] ids in
                self?.updateFollowStatus(isFollowing: ids.contains(id))
            }
            .store(in: &cancellables)
    }

    private func updateFollowStatus(isFollowing: Bool) {
        // Mutate isFollowing in place — a field-by-field WhaleProfile
        // reconstruction here silently dropped every defaulted field it
        // forgot (firmName vanished from the header on follow-state sync).
        guard var currentProfile = profile else { return }

        if currentProfile.isFollowing != isFollowing {
            currentProfile.isFollowing = isFollowing
            profile = currentProfile
        }
    }

    // MARK: - Data Loading

    func loadProfile() {
        // Cancel any load already in flight. Retry, pull-to-refresh and the
        // `.onChange(of: tier)` reload can all fire within a second of each other, and
        // without this the SLOWER response wins — so a stale (or pre-upgrade, still
        // locked) profile could overwrite the fresh one.
        loadTask?.cancel()

        // Only blank the screen when there is nothing to show. `isLoading` gates the
        // whole view, so setting it unconditionally replaced a fully-rendered profile
        // with a spinner on every refresh and on every tier change.
        isLoading = (profile == nil)
        errorMessage = nil

        loadTask = Task { [weak self] in
            guard let self = self else { return }

            do {
                let dto = try await self.apiClient.request(
                    endpoint: .getWhaleProfile(whaleId: self.whaleId),
                    responseType: WhaleProfileDTO.self
                )
                guard !Task.isCancelled else { return }
                let loadedProfile = dto.toWhaleProfile()

                // Trust the freshly-fetched server truth (dto.isFollowing) and
                // converge the local WhaleService cache to it — do NOT override
                // the DTO with a possibly-stale local value (that pinned a
                // "Following" header after a cross-device unfollow). Reconcile is
                // a local-only cache alignment; it issues no backend call.
                self.whaleService.reconcileLocalFollow(
                    self.whaleId, isFollowing: loadedProfile.isFollowing
                )

                self.profile = loadedProfile
                self.isLoading = false
                print("[WhaleProfileVM] ✅ Loaded profile for \(loadedProfile.name) from API")
            } catch {
                guard !Task.isCancelled else { return }
                // Routed through `AppError.from(_:)` as .claude/rules/ios-swiftui.md
                // requires — this used to invent its own string and bypass the mapping
                // entirely, so a 503 WHALE_PROFILE_UNAVAILABLE and a dead network read
                // identically.
                let appError = AppError.from(error)
                self.isLoading = false
                // And it must not CLAIM cached data. The old copy was
                // "Failed to load profile. Showing cached data." while
                // `loadSampleProfile()` was a documented no-op that showed nothing —
                // the sentence was false in every case it appeared.
                self.errorMessage = self.profile == nil
                    ? appError.message
                    : "\(appError.message) Showing the last loaded data."
                print("[WhaleProfileVM] ❌ Profile load failed: \(appError.title): \(error)")
            }
        }
    }

    func refresh() async {
        isRefreshing = true
        // AWAIT the real load instead of sleeping 500ms and hoping. The old form
        // dismissed the pull-to-refresh spinner on a timer that had nothing to do with
        // whether the request had finished.
        loadProfile()
        await loadTask?.value
        isRefreshing = false
    }

    // MARK: - Actions

    func toggleFollow() {
        guard var updatedProfile = profile else { return }
        let newFollowState = !updatedProfile.isFollowing

        // PLAN gate BEFORE anything else — the same rung `TrackingViewModel
        // .toggleFollowWhale` already checks, which this screen was missing entirely.
        //
        // Following (not unfollowing) a locked whale is refused server-side with 403
        // WHALE_FOLLOW_LOCKED. That maps to `AppError.planUpgradeRequired`, which
        // `reportMutationFailure` routes to its `default:` arm — a generic toast. So the
        // pill filled in, the request went out, and the pill snapped back with a message
        // that never mentioned the plan: exactly the "animates in, then reverts" symptom
        // the roster path was fixed for. Unfollow is NEVER gated, here or on the server.
        if newFollowState && updatedProfile.isLocked {
            showPaywall = true
            return
        }

        // Ask the service first — it owns the sign-in gate and returns false when the mutation
        // was never started, so a signed-out tap creates no optimistic state and, critically,
        // posts no `.whaleFollowStateChanged`. That notification is consumed by
        // `TrackingViewModel.handleFollowStateChange`, which will SYNTHESISE a whale row from
        // it; firing it for a follow that never happened fabricates a tracked investor.
        guard whaleService.toggleFollow(whaleId) else { return }

        // Optimistic UI update — in-place mutation, NOT reconstruction
        // (see updateFollowStatus).
        updatedProfile.isFollowing = newFollowState
        profile = updatedProfile

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
