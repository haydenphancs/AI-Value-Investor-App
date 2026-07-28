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

    /// Backed by AppearanceManager (UserDefaults). Changing it persists + applies the
    /// window style and syncs the choice to the backend for signed-in users.
    @Published var appearanceMode: AppearanceMode = AppearanceManager.current {
        didSet {
            guard oldValue != appearanceMode else { return }
            AppearanceManager.set(appearanceMode)
            SettingsSyncManager.shared.push()   // self-gates on auth
        }
    }
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

    /// Human label for the monthly reset. Derived from the backend `resets_at`
    /// (written by `ensure_credit_period` = next-month boundary in ET). Falls back
    /// to "Renews monthly" when absent (e.g. a brand-new row) or unparseable.
    var creditResetLabel: String {
        guard let resetsAt = appState?.user.credits?.resetsAt,
              let date = Self.parseISODate(resetsAt) else {
            return "Renews monthly"
        }
        let formatter = DateFormatter()
        formatter.dateFormat = "MMM d"
        return "Resets on \(formatter.string(from: date))"
    }

    /// Parse an ISO-8601 timestamp tolerant of fractional seconds (Postgres/Supabase
    /// timestamptz may or may not include them).
    static func parseISODate(_ string: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: string) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: string)
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

    /// Whether a real user is signed in. Guest-first: when false the Profile
    /// screen shows a Sign In affordance instead of the identity/credit blocks.
    var isAuthenticated: Bool {
        appState?.auth.isAuthenticated ?? false
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

    var interfaceStyle: UIUserInterfaceStyle {
        switch self {
        case .system: return .unspecified
        case .dark: return .dark
        case .light: return .light
        }
    }
}
