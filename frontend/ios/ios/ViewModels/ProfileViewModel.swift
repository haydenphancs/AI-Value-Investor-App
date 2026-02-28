//
//  ProfileViewModel.swift
//  ios
//
//  ViewModel for the Profile / Account Settings screen
//

import Foundation
import SwiftUI
import Combine

@MainActor
class ProfileViewModel: BaseViewModel {

    // MARK: - Published Properties

    @Published var appearanceMode: AppearanceMode = .system
    @Published var showDeleteConfirmation: Bool = false
    @Published var showSignOutConfirmation: Bool = false
    @Published var isDeleting: Bool = false

    // MARK: - Credit Usage

    var creditUsagePercent: Double {
        guard let credits = appState?.user.credits, credits.total > 0 else { return 0 }
        return Double(credits.used) / Double(credits.total)
    }

    var creditsUsed: Int {
        appState?.user.credits?.used ?? 0
    }

    var creditsTotal: Int {
        appState?.user.credits?.total ?? 0
    }

    var creditsRemaining: Int {
        appState?.user.credits?.remaining ?? 0
    }

    var creditResetDate: String? {
        appState?.user.credits?.resetsAt
    }

    // MARK: - User Info

    var displayName: String {
        appState?.user.profile?.displayName ?? "Investor"
    }

    var email: String {
        appState?.user.profile?.email ?? "No email"
    }

    var avatarUrl: String? {
        appState?.user.profile?.avatarUrl
    }

    var memberSince: String {
        guard let createdAt = appState?.user.profile?.createdAt else { return "N/A" }
        // Parse ISO 8601 date and format
        let isoFormatter = ISO8601DateFormatter()
        isoFormatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = isoFormatter.date(from: createdAt) {
            let displayFormatter = DateFormatter()
            displayFormatter.dateFormat = "MMM yyyy"
            return "Member since \(displayFormatter.string(from: date))"
        }
        return "Member since \(createdAt.prefix(7))"
    }

    var userTier: UserTier {
        appState?.user.tier ?? .free
    }

    // MARK: - Activity Stats

    var totalReports: Int {
        appState?.research.reports.count ?? 0
    }

    var watchlistCount: Int {
        appState?.watchlist.stocks.count ?? 0
    }

    // MARK: - Actions

    func signOut() {
        appState?.signOut()
    }

    func deleteAccount() {
        isDeleting = true
        performTask("deleteAccount") { [weak self] in
            // Call delete account API
            try await self?.apiClient.request(endpoint: .signOut)
            self?.isDeleting = false
            self?.appState?.signOut()
        }
    }

    func addMoreCredits() {
        // Will open credits purchase flow
        print("Navigate to add credits")
    }

    func loadCredits() {
        performTask("loadCredits", showLoading: false) { [weak self] in
            let credits = try await self?.apiClient.request(
                endpoint: .getUserCredits,
                responseType: CreditInfo.self
            )
            self?.appState?.user.credits = credits
        }
    }

    override func loadData() {
        loadCredits()
    }
}

// MARK: - Appearance Mode

enum AppearanceMode: String, CaseIterable, Identifiable {
    case system = "System"
    case dark = "Dark"
    case light = "Light"

    var id: String { rawValue }

    var icon: String {
        switch self {
        case .system: return "circle.lefthalf.filled"
        case .dark: return "moon.fill"
        case .light: return "sun.max.fill"
        }
    }

    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .dark: return .dark
        case .light: return .light
        }
    }
}
