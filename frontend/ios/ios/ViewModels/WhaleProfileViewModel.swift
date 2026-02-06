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

    init(whaleId: String) {
        self.whaleId = whaleId
        loadProfile()
        observeFollowChanges()
    }
    
    // MARK: - Observation
    
    private func observeFollowChanges() {
        // Update profile when follow status changes in the shared service
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

        // Simulate network delay and load mock data
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self = self else { return }

            // In a real app, fetch from API based on whaleId
            var loadedProfile: WhaleProfile?
            switch self.whaleId {
            case "warren-buffett":
                loadedProfile = WhaleProfile.warrenBuffett
            case "cathie-wood":
                loadedProfile = WhaleProfile.cathieWood
            default:
                loadedProfile = WhaleProfile.warrenBuffett
            }
            
            // Update follow status from shared service
            if var profile = loadedProfile {
                let isFollowing = self.whaleService.isFollowing(self.whaleId)
                profile = WhaleProfile(
                    id: profile.id,
                    name: profile.name,
                    title: profile.title,
                    description: profile.description,
                    avatarURL: profile.avatarURL,
                    riskProfile: profile.riskProfile,
                    portfolioValue: profile.portfolioValue,
                    ytdReturn: profile.ytdReturn,
                    sectorExposure: profile.sectorExposure,
                    currentHoldings: profile.currentHoldings,
                    recentTradeGroups: profile.recentTradeGroups,
                    recentTrades: profile.recentTrades,
                    behaviorSummary: profile.behaviorSummary,
                    sentimentSummary: profile.sentimentSummary,
                    isFollowing: isFollowing
                )
                self.profile = profile
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
        guard let currentProfile = profile else { return }
        
        // Toggle in the shared service - this will trigger updates everywhere
        whaleService.toggleFollow(whaleId)
        
        // Local state will be updated via the Combine observer
        // But we can also update immediately for instant UI feedback
        var updatedProfile = WhaleProfile(
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
            isFollowing: !currentProfile.isFollowing
        )
        profile = updatedProfile
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
