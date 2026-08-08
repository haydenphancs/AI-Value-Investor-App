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
            // A change originating from a server hydrate is already persisted + applied
            // (and must not be pushed straight back) — only user-driven changes persist+sync.
            guard !isApplyingRemoteAppearance else { return }
            AppearanceManager.set(appearanceMode)
            SettingsSyncManager.shared.push()   // self-gates on auth
        }
    }
    /// True while reflecting a hydrated (server) appearance into the picker, so the
    /// `didSet` above doesn't re-persist/re-push a value that came FROM the server.
    private var isApplyingRemoteAppearance = false

    /// Re-read the appearance choice from the store into the picker (call after a
    /// settings hydrate changes `appearance_mode` while this screen is open).
    func syncAppearanceFromStore() {
        let stored = AppearanceManager.current
        guard stored != appearanceMode else { return }
        isApplyingRemoteAppearance = true
        appearanceMode = stored
        isApplyingRemoteAppearance = false
    }
    @Published var showDeleteConfirmation: Bool = false
    @Published var showSignOutConfirmation: Bool = false
    @Published var isDeleting: Bool = false
    /// Display-name editing (see `saveDisplayName`).
    @Published var isEditingName: Bool = false
    @Published var isSavingName: Bool = false

    // MARK: - Credit Usage

    /// Fraction of the MONTHLY allowance consumed, clamped to 0...1.
    ///
    /// Reads the GRANTED pool, not the combined one. `credits.total` / `.used` are
    /// lifetime-inclusive of every credit pack the user has ever bought, so the old
    /// `used / total` made the "Monthly Credits" bar meaningless: buy the 1,200-credit pack on
    /// the Free tier and it reads 0/1250 — implying a monthly quota 25x the real 50 — and it
    /// can never fill, so the >70% / >90% colour warnings never fire again.
    var creditUsagePercent: Double {
        appState?.user.credits?.monthlyUsageFraction ?? 0
    }

    var creditsUsed: Int {
        appState?.user.credits?.monthlyUsed ?? 0
    }

    var creditsTotal: Int {
        appState?.user.credits?.monthlyTotal ?? 0
    }

    /// Spendable across BOTH pools — this one is deliberately the combined figure, because it
    /// answers "how much can I spend right now".
    var creditsRemaining: Int {
        appState?.user.credits?.remaining ?? 0
    }

    /// Never-expiring credits bought as packs. 0 when there are none, so the row can be hidden.
    var purchasedCredits: Int {
        appState?.user.credits?.purchasedCredits ?? 0
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
        // Reuse the tolerant parser (handles timestamps WITH or WITHOUT fractional
        // seconds — a plain formatter with .withFractionalSeconds fails on whole-second
        // values this backend emits, silently degrading to "Member since 2024-03").
        if let date = Self.parseISODate(createdAt) {
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

    // MARK: - Activity Stats
    //
    // DEAD CODE — deliberately left as-is, and documented rather than "fixed".
    //
    // Both of these read arrays that are declared, defaulted to `[]`, and NEVER ASSIGNED anywhere
    // in the app: `AppState.research.reports` and `AppState.watchlist.stocks`, whose element types
    // (`ResearchReportSummary`, `WatchlistStock`) are not constructed in a single file. So both
    // computeds are hardcoded 0.
    //
    // They are also read by NO view — not by the current ProfileView, and not by the one at HEAD.
    // So there is no user-visible wrong number here; nothing renders them. Wiring them to real
    // fetches would add two network calls per Profile open for values nobody displays, which is
    // strictly worse than the dead code.
    //
    // The right resolution is deletion (these two, plus the two unused arrays and their two unused
    // model types) — held back only because `AppState`/`ProfileView` are in the middle of an
    // in-flight edit. If an activity-stats row IS planned, the counts want
    // `GET /watchlist` and `GET /research/reports`, and this is the place to put them.

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
            // Actually delete the account (cascades all data), THEN sign out.
            // performTask surfaces any failure via errorMessage instead of signing out.
            try await self?.apiClient.request(endpoint: .deleteAccount)
            self?.isDeleting = false
            self?.appState?.signOut()
        }
    }

    /// Save a new display name. `PATCH /users/me` and its schema already existed on both
    /// sides but nothing called them, so there was no way to change your name in the app.
    ///
    /// Adopts the SERVER's returned profile rather than the local string, so any
    /// normalisation or truncation the backend applies is what the UI shows.
    func saveDisplayName(_ newName: String) {
        let trimmed = newName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed != appState?.user.profile?.displayName else {
            isEditingName = false
            return
        }
        isSavingName = true
        performTask("updateProfile", showLoading: false) { [weak self] in
            defer { self?.isSavingName = false }
            let updated = try await AccountRepository.shared.updateProfile(
                displayName: trimmed,
                avatarUrl: nil
            )
            self?.appState?.user.profile = updated
            self?.isEditingName = false
        }
    }

    func loadCredits() {
        // A signed-out caller is NOT rejected by this endpoint — `.getUserCredits` is
        // `.guestAllowed`, and the backend resolves a tokenless request to the SHARED guest
        // sentinel, which is seeded ~100,000 credits. Loading that wrote a balance that is not
        // the user's into `AppState.user.credits`, and every other surface reads AppState: the
        // Wiser/Learn card and the Research badge then displayed the sentinel's credits as
        // theirs, and `canGenerateResearch` returned true for someone who cannot generate.
        //
        // Clearing rather than merely skipping matters too: without it a balance left over from
        // a previous session survives a sign-out on this screen.
        //
        // `ResearchViewModel.loadCredits()` has carried this guard since the same bug was found
        // there; Profile is the other caller and was missed.
        guard appState?.auth.isAuthenticated == true else {
            appState?.user.credits = nil
            return
        }
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

extension AppearanceMode {
    /// Tolerant parse for values that did NOT originate in this client — the synced
    /// server blob, another platform, a hand-edited Supabase row. Case-insensitive
    /// and whitespace-trimmed.
    ///
    /// Returns `nil` rather than a default, deliberately. Both read sites
    /// (`AppearanceManager.parse` and the root `.preferredColorScheme`) coalesce a
    /// miss to `.dark`, so a value that merely LOOKS wrong has to be logged and
    /// rejected — never quietly turned into a mode the user did not choose. Silently
    /// coalescing is how a "System" user would become a "Dark" user with nothing in
    /// the logs to say so.
    ///
    /// All three keys are ASCII, so `lowercased()` has no locale hazard here.
    init?(tolerantRawValue raw: String) {
        switch raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "system": self = .system
        case "dark":   self = .dark
        case "light":  self = .light
        default:       return nil
        }
    }
}
