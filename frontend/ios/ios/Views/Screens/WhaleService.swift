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

    private init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
        loadFollowedWhales()
    }

    // MARK: - Public Methods

    func isFollowing(_ whaleId: String) -> Bool {
        followedWhaleIds.contains(whaleId)
    }

    /// Optimistic toggle: update UI immediately, sync to backend, revert on failure
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

        // Backend sync
        Task {
            do {
                let endpoint: APIEndpoint = newFollowing
                    ? .followWhale(whaleId: whaleId)
                    : .unfollowWhale(whaleId: whaleId)

                let response = try await apiClient.request(
                    endpoint: endpoint,
                    responseType: FollowResponseDTO.self
                )
                print("[WhaleService] ✅ Backend confirmed: isFollowing=\(response.isFollowing), followers=\(response.followersCount)")
            } catch {
                // Revert on failure
                print("[WhaleService] ❌ Backend follow/unfollow failed: \(error). Reverting.")
                if wasFollowing {
                    followedWhaleIds.insert(whaleId)
                } else {
                    followedWhaleIds.remove(whaleId)
                }
                saveFollowedWhales()
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

    /// Sync local follow state from API-returned whale list
    func syncFromAPIResponse(_ whales: [TrendingWhale]) {
        let apiFollowed = Set(whales.filter { $0.isFollowing }.map { $0.id })
        if !apiFollowed.isEmpty {
            followedWhaleIds = apiFollowed
            saveFollowedWhales()
            print("[WhaleService] 🔄 Synced \(apiFollowed.count) followed whales from API")
        }
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
