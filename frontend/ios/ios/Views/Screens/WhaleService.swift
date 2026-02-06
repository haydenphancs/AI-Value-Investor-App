//
//  WhaleService.swift
//  ios
//
//  Shared service for managing whale follow state
//

import Foundation
import Combine

@MainActor
class WhaleService: ObservableObject {
    static let shared = WhaleService()
    
    // Published set of followed whale IDs
    @Published private(set) var followedWhaleIds: Set<String> = []
    
    private init() {
        // Load followed whales from UserDefaults or your data store
        loadFollowedWhales()
    }
    
    // MARK: - Public Methods
    
    func isFollowing(_ whaleId: String) -> Bool {
        followedWhaleIds.contains(whaleId)
    }
    
    func toggleFollow(_ whaleId: String) {
        if followedWhaleIds.contains(whaleId) {
            followedWhaleIds.remove(whaleId)
        } else {
            followedWhaleIds.insert(whaleId)
        }
        saveFollowedWhales()
    }
    
    func follow(_ whaleId: String) {
        followedWhaleIds.insert(whaleId)
        saveFollowedWhales()
    }
    
    func unfollow(_ whaleId: String) {
        followedWhaleIds.remove(whaleId)
        saveFollowedWhales()
    }
    
    // MARK: - Persistence
    
    private func loadFollowedWhales() {
        if let data = UserDefaults.standard.data(forKey: "followedWhaleIds"),
           let ids = try? JSONDecoder().decode(Set<String>.self, from: data) {
            followedWhaleIds = ids
        } else {
            // Initialize with sample followed whales for demo purposes
            followedWhaleIds = ["warren-buffett", "nancy-pelosi", "bill-ackman"]
        }
    }
    
    private func saveFollowedWhales() {
        if let data = try? JSONEncoder().encode(followedWhaleIds) {
            UserDefaults.standard.set(data, forKey: "followedWhaleIds")
        }
    }
}
