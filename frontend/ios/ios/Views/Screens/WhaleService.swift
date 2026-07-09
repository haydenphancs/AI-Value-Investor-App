//
//  WhaleService.swift
//  ios
//
//  Shared service for managing whale follow state
//  with backend sync and local UserDefaults fallback.
//

import Foundation
import Combine

@MainActor
class WhaleService: ObservableObject {
    static let shared = WhaleService()

    // Published set of followed whale IDs
    @Published private(set) var followedWhaleIds: Set<String> = []

    private let apiClient: APIClient

    /// In-flight backend follow-mutation per whale. A new toggle chains after
    /// the previous one for the SAME whale so their requests are strictly
    /// ordered — otherwise two rapid taps race and the server can end in a
    /// state the client never converges to. Keyed by whaleId; the entry always
    /// points at the most recent task (completed tasks are harmless to retain).
    private var followTasks: [String: Task<Void, Never>] = [:]

    private init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        loadFollowedWhales()
    }

    // MARK: - Public Methods

    func isFollowing(_ whaleId: String) -> Bool {
        followedWhaleIds.contains(whaleId)
    }

    /// Align the LOCAL follow cache to an authoritative server value (e.g. a
    /// freshly-fetched profile's `is_following`) WITHOUT issuing a backend
    /// follow/unfollow. Lets a screen converge stale local state to server truth
    /// so downstream lookups (isFollowing) stop contradicting the server.
    func reconcileLocalFollow(_ whaleId: String, isFollowing: Bool) {
        guard followedWhaleIds.contains(whaleId) != isFollowing else { return }
        if isFollowing {
            followedWhaleIds.insert(whaleId)
        } else {
            followedWhaleIds.remove(whaleId)
        }
        saveFollowedWhales()
    }

    /// Optimistic toggle: update UI immediately, sync to backend, reconcile the
    /// local set to the authoritative server response, and revert on failure.
    func toggleFollow(_ whaleId: String) {
        let wasFollowing = followedWhaleIds.contains(whaleId)
        let newFollowing = !wasFollowing

        // Optimistic UI update
        if newFollowing {
            followedWhaleIds.insert(whaleId)
        } else {
            followedWhaleIds.remove(whaleId)
        }
        saveFollowedWhales()

        print("[WhaleService] \(newFollowing ? "➕ Following" : "➖ Unfollowing") whale \(whaleId) (optimistic)")

        // Backend sync — chained AFTER any in-flight mutation for THIS whale so
        // the requests are strictly ordered and the last server response wins.
        let previousTask = followTasks[whaleId]
        followTasks[whaleId] = Task { [weak self] in
            await previousTask?.value
            guard let self else { return }
            do {
                let endpoint: APIEndpoint = newFollowing
                    ? .followWhale(whaleId: whaleId)
                    : .unfollowWhale(whaleId: whaleId)

                let response = try await self.apiClient.request(
                    endpoint: endpoint,
                    responseType: FollowResponseDTO.self
                )
                // Reconcile to the authoritative server state instead of
                // trusting the optimistic guess (which a later toggle may have
                // already superseded).
                if response.isFollowing {
                    self.followedWhaleIds.insert(whaleId)
                } else {
                    self.followedWhaleIds.remove(whaleId)
                }
                self.saveFollowedWhales()
                print("[WhaleService] ✅ Backend confirmed: isFollowing=\(response.isFollowing), followers=\(response.followersCount)")
            } catch {
                // Revert on failure
                print("[WhaleService] ❌ Backend follow/unfollow failed: \(error). Reverting.")
                if wasFollowing {
                    self.followedWhaleIds.insert(whaleId)
                } else {
                    self.followedWhaleIds.remove(whaleId)
                }
                self.saveFollowedWhales()
            }
        }
    }

    func follow(_ whaleId: String) {
        guard !followedWhaleIds.contains(whaleId) else { return }
        toggleFollow(whaleId)
    }

    func unfollow(_ whaleId: String) {
        guard followedWhaleIds.contains(whaleId) else { return }
        toggleFollow(whaleId)
    }

    /// Sync local follow state from the API-returned whale list.
    ///
    /// The sole caller (TrackingViewModel.loadWhaleList) passes the FULL
    /// unfiltered list (category nil), and the backend stamps a fresh per-user
    /// `is_following` on every row — so it is authoritative. Assign
    /// UNCONDITIONALLY: an all-false response (the user unfollowed everyone, incl.
    /// on another device) must CLEAR stale local ids, not be ignored. The old
    /// `if !apiFollowed.isEmpty` guard silently kept stale follows forever.
    func syncFromAPIResponse(_ whales: [TrendingWhale]) {
        let apiFollowed = Set(whales.filter { $0.isFollowing }.map { $0.id })
        followedWhaleIds = apiFollowed
        saveFollowedWhales()
        print("[WhaleService] 🔄 Synced \(apiFollowed.count) followed whales from API")
    }

    // MARK: - Persistence (local cache)

    private func loadFollowedWhales() {
        if let data = UserDefaults.standard.data(forKey: "followedWhaleIds"),
           let ids = try? JSONDecoder().decode(Set<String>.self, from: data) {
            followedWhaleIds = ids
        } else {
            followedWhaleIds = []
        }
    }

    private func saveFollowedWhales() {
        if let data = try? JSONEncoder().encode(followedWhaleIds) {
            UserDefaults.standard.set(data, forKey: "followedWhaleIds")
        }
    }
}
