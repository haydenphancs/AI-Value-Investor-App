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
    /// True while a picked photo is being processed and uploaded. Drives the spinner over the
    /// avatar, and disables the picker so a second tap cannot start a competing upload.
    @Published private(set) var isUploadingAvatar: Bool = false

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

    /// VoiceOver's reading of the "Monthly Credits used/total" row, e.g. "4 of 50 used.
    /// Renews on Nov 1. Plus 120 credits that never expire". Not "purchased": that balance also
    /// holds reward credits for a confirmed issue report, which nobody paid for.
    ///
    /// The row's visual stays "4/50" (owner, 2026-10-12: "4/50 is correct way" — USED of the
    /// monthly total), but spoken it was "4 slash 50", which reads as either used or left. This
    /// says which, from the same monthly figures the visual draws, and folds in the bar, the
    /// purchased row and the reset label, which are hidden from VoiceOver.
    var creditUsageAccessibilityValue: String {
        guard appState?.user.credits != nil else { return "Not loaded" }
        var parts = ["\(creditsUsed) of \(creditsTotal) used", creditResetLabel]
        if purchasedCredits > 0 {
            let tail = purchasedCredits == 1 ? "credit that never expires" : "credits that never expire"
            parts.append("Plus \(purchasedCredits) \(tail)")
        }
        return parts.joined(separator: ". ")
    }

    /// "Renews on <date>" — the day the monthly allowance comes back. From the backend
    /// `resets_at`: for a Pro/Max subscriber that is Apple's renewal day (migration 196 ties the
    /// allowance to the paid period), for Free and complimentary accounts the ET 1st.
    /// `CreditRenewalDate` picks the right time zone for each. "Renews monthly" when the date is
    /// absent (a brand-new row) or unparseable.
    ///
    /// "Renews", not "Resets" (owner, 2026-10-13): it reads as the plan's renewal date, which for
    /// a subscriber it now is. Every account's allowance does come back that day — a lapsed plan
    /// falls to the Free allowance — so the sentence is true even after a cancellation.
    var creditResetLabel: String {
        guard let resetsAt = appState?.user.credits?.resetsAt,
              let date = Self.parseISODate(resetsAt) else {
            return "Renews monthly"
        }
        return "Renews on \(CreditRenewalDate.short(date))"
    }

    /// `"MMM yyyy"` for "Member since". Same locale reasoning as `CreditRenewalDate`: without
    /// the locale a Japanese-calendar device renders the ERA year — "Member since 8月 8" rather
    /// than "Member since Aug 2026".
    ///
    /// The zone is the PHONE's (owner, 2026-10-29: the user's own events are local time).
    /// `users.created_at` is an instant, the moment this user signed up, so its month is the
    /// month on their clock. UTC put an account opened on the evening of March 31 in a US zone
    /// in April. `.autoupdatingCurrent`, not `.current`: this formatter is built once per
    /// launch, and it must follow the phone into a new zone.
    private static let memberSinceFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.calendar = Calendar(identifier: .gregorian)
        f.timeZone = .autoupdatingCurrent
        f.dateFormat = "MMM yyyy"
        return f
    }()

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
            return "Member since \(Self.memberSinceFormatter.string(from: date))"
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

    /// We hold a credential we could not validate yet. NOT a guest.
    ///
    /// `.claude/rules/auth.md` §5: `.restoring` "renders like a guest but says 'Reconnecting'
    /// and keeps trying. Do not collapse it into `.unauthenticated`." This screen did exactly
    /// that — it branched on `isAuthenticated`, which is `.authenticated` only — so a signed-in
    /// user on a flaky connection was shown the word "Guest" and invited to "Sign in to sync
    /// your account and unlock monthly credits", for an account they already have. `.restoring`
    /// is entered on every transient network failure and every launch on a poor connection, so
    /// this is the ordinary case, not an edge one.
    var isRestoring: Bool {
        appState?.auth.status == .restoring
    }

    /// Whether there is an account profile a confirmed Help Us Improve report could be credited
    /// to. The same source FeedbackView reads the report's Account ID from, so the row never
    /// advertises the reward to someone whose report would go out without one: true when signed
    /// in and through a mid-session `.restoring`, false for a guest and on a cold launch that has
    /// not fetched the profile yet.
    var hasAccountProfile: Bool {
        appState?.user.profile != nil
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
            let updated = try await AccountRepository.shared.updateProfile(displayName: trimmed)
            self?.appState?.user.profile = updated
            self?.isEditingName = false
        }
    }

    // MARK: - Profile picture

    /// Upload an already-processed square JPEG and adopt the server's profile.
    ///
    /// The response carries a freshly SIGNED avatar URL (the bucket is private), which is why
    /// we take the server's profile wholesale instead of patching the local one — a URL we
    /// invented would not be signed, and would 400 on load.
    func saveAvatar(_ jpeg: Data) {
        guard !isUploadingAvatar else { return }
        isUploadingAvatar = true
        performTask("saveAvatar", showLoading: false) { [weak self] in
            defer { self?.isUploadingAvatar = false }
            let updated = try await AccountRepository.shared.uploadAvatar(jpeg: jpeg)
            self?.appState?.user.profile = updated
        }
    }

    /// Remove the picture, reverting to the placeholder glyph.
    ///
    /// Its OWN task name, deliberately: `performTask` cancels any in-flight task with the same
    /// name, so sharing one with `saveAvatar` would make a Remove tapped during an upload
    /// silently cancel it — and `BaseViewModel` swallows `CancellationError` without reporting,
    /// so the user would see nothing happen at all.
    func removeAvatar() {
        guard !isUploadingAvatar else { return }
        isUploadingAvatar = true
        performTask("removeAvatar", showLoading: false) { [weak self] in
            defer { self?.isUploadingAvatar = false }
            let updated = try await AccountRepository.shared.deleteAvatar()
            self?.appState?.user.profile = updated
        }
    }

    /// The picker handed back something we could not turn into a JPEG — an iCloud photo that
    /// never downloaded, or a format this device cannot decode.
    ///
    /// Reported, never swallowed: a picker that appears to do nothing is indistinguishable
    /// from a broken feature, which is exactly the class of silent failure auth.md §6 bans.
    func reportAvatarPickFailed() {
        errorMessage = "We couldn't read that photo. Try a different one."
    }

    func loadCredits() {
        // `.getUserCredits` is `.signInRequired`: APIClient refuses an unarmed call before it is
        // sent, so it can no longer reach the shared guest sentinel's ~100,000 seeded credits
        // (which once showed as this user's balance on every surface that reads AppState).
        // The explicit guard stays anyway, because a signed-out viewer of this screen must
        // also CLEAR a leftover balance — without it one from a previous session survives a
        // sign-out here.
        //
        // ⚠️ `.restoring` is NOT the signed-out case. We hold a credential that is healing, so
        // clearing here would blank a balance the user can currently see — and `restoreSession`
        // re-runs this once the token is re-armed anyway. Skip, don't wipe.
        if isRestoring { return }
        guard appState?.auth.isAuthenticated == true else {
            appState?.user.credits = nil
            return
        }
        // Through AppState, never a direct GET + `user.credits =`: its read joins one already
        // in flight, lands only when it is newer than the balance shown, and is fenced to the
        // identity that sent it — the direct write here could put an ended session's balance
        // back after a sign-out. A real failure still reaches this screen's alert: thrown into
        // `performTask`, whose `setError` drops cancellation.
        performTask("loadCredits", showLoading: false) { [weak self] in
            guard let appState = self?.appState else { return }
            if let failure = await appState.refreshCredits() {
                throw failure
            }
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
