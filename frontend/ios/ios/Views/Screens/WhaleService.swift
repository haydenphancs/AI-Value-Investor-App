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
    @discardableResult
    func toggleFollow(_ whaleId: String) -> Bool {
        // Following is account-scoped (`whale_follows.user_id` is FK-bound to `public.users`),
        // so a signed-out tap can only ever be refused. Ask for sign-in BEFORE creating any
        // optimistic state: this is the reported bug's exact shape — the button used to fill
        // in, the row used to animate into "Tracked Whales", and then both silently snapped
        // back a moment later, which reads as a broken app rather than a missing account.
        guard AppActions.shared.isSignedIn else {
            AppActions.shared.requestSignIn(for: "follow investors")
            return false
        }

        let wasFollowing = followedWhaleIds.contains(whaleId)
        let newFollowing = !wasFollowing

        // Optimistic update in MEMORY only. `saveFollowedWhales()` used to run here, before the
        // request — so killing the app mid-flight left a follow the server never heard about
        // durably on disk, and a later `/whales` load would silently undo what the user did.
        // The @Published set still drives the snappy UI; disk now only ever records what the
        // server confirmed.
        if newFollowing {
            followedWhaleIds.insert(whaleId)
        } else {
            followedWhaleIds.remove(whaleId)
        }

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
            } catch {
                // Revert, and TELL THE USER. The revert was always correct; the silence was the
                // bug — a `print` in a release build is indistinguishable from the app deciding
                // on its own that the tap didn't happen.
                if wasFollowing {
                    self.followedWhaleIds.insert(whaleId)
                } else {
                    self.followedWhaleIds.remove(whaleId)
                }
                self.saveFollowedWhales()
                AppActions.shared.reportMutationFailure(
                    error,
                    action: newFollowing ? "follow this investor" : "unfollow this investor",
                    signInFeature: "follow investors"
                )
            }
        }
        return true
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

    /// Drop this device's followed-whale cache because the session that owned it ended.
    ///
    /// `followedWhaleIds` persists under a device-global UserDefaults key with no user id in
    /// it, and nothing used to clear it — so after A signed out and B signed in on the same
    /// phone, B saw A's followed investors until (and unless) a `/whales` load happened to
    /// reconcile them. Identical in shape to the Learn-store bleed, so it is cleared from the
    /// same funnel: `AppState.discardDataForEndedSession()`.
    func reset() {
        followedWhaleIds = []
        UserDefaults.standard.removeObject(forKey: "followedWhaleIds")
    }
}
