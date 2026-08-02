//
//  AppState.swift
//  ios
//
//  Global Application State - Single Source of Truth
//
//  Architecture Decision:
//  Using @Observable (iOS 17+) for global state instead of:
//  - EnvironmentObject: Requires @Published boilerplate
//  - Singleton: Hard to test, implicit dependencies
//  - Full DI Container: Overkill for solo developer
//
//  Benefits:
//  - Simple, no boilerplate
//  - Automatic UI updates
//  - Easy to pass via Environment
//  - Works with existing @StateObject ViewModels
//

import SwiftUI
import Combine

// MARK: - Global App State

/// Central state container for data that needs to be shared across the app.
/// Injected via `.environment()` at the app root.
///
/// Usage in Views:
/// ```swift
/// struct MyView: View {
///     @Environment(AppState.self) private var appState
///     var body: some View {
///         Text("Credits: \(appState.user.credits)")
///     }
/// }
/// ```
///
/// Usage in ViewModels:
/// ```swift
/// @MainActor
/// class MyViewModel: ObservableObject {
///     private let appState: AppState
///     init(appState: AppState) {
///         self.appState = appState
///     }
/// }
/// ```
@Observable
@MainActor
final class AppState {

    // MARK: - Sub-States

    /// Authentication state
    var auth = AuthState()

    /// Current user state (profile, credits, tier)
    var user = UserState()

    /// Watchlist and tracked stocks
    var watchlist = WatchlistState()

    /// Research reports state
    var research = ResearchState()

    // MARK: - Global UI State

    /// Network connectivity status
    var isOnline: Bool = true

    /// Global loading indicator
    var isLoading: Bool = false

    /// Global error to display
    var currentError: AppError?

    /// Toast message to display
    var toastMessage: ToastMessage?

    /// Ticker a notification tap wants opened, consumed by the Home tab.
    ///
    /// A tapped push used to land wherever the user happened to be — the alert said
    /// "NVDA moved 8%" and then showed you the Wiser tab. Routed through AppState
    /// rather than a new navigation stack so it reuses the ticker presentation Home
    /// already owns. Cleared by whoever consumes it, so one tap opens one screen.
    var pendingPushTicker: String?

    // MARK: - Services (Injected)

    private(set) var apiClient: APIClient!
    private(set) var authService: AuthService!

    // MARK: - Initialization

    init() {
        // Services will be set up in configure()
    }

    /// Adopt a newly granted entitlement without waiting for a relaunch.
    ///
    /// Re-reads BOTH the profile (for `tier`) and the credit balance. `refreshCredits()` alone
    /// is not enough: `user.tier` is only ever assigned in `applyProfile`, so after a purchase
    /// the credits would update while the tier badge, paywall highlighting, and
    /// `canGenerateResearch` all still said Free.
    func refreshEntitlement() async {
        do {
            applyProfile(try await apiClient.request(
                endpoint: .getCurrentUser, responseType: UserProfile.self
            ))
        } catch {
            #if DEBUG
            print("⚠️ [AppState] entitlement profile refresh failed: \(AppError.from(error).message)")
            #endif
        }
        await refreshCredits()
    }

    /// Configure services - called from App entry point
    func configure(apiClient: APIClient, authService: AuthService) {
        self.apiClient = apiClient
        self.authService = authService

        // Adopt a purchase as soon as the backend records it. StoreKitService posts this from
        // the single funnel that covers interactive purchases AND Transaction.updates replays;
        // it holds no AppState reference, and injecting one would mean editing iosApp.swift.
        NotificationCenter.default.addObserver(
            forName: .caydexEntitlementChanged, object: nil, queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in await self?.refreshEntitlement() }
        }

        // Restore auth state from keychain
        Task {
            await restoreAuthState()
        }
    }

    // MARK: - Auth Actions

    /// Guest-first auth restore. Runs on launch:
    ///  - No stored token  → stay a guest (`.unauthenticated`); the app is fully
    ///    usable and requests fall back to the backend's GUEST_USER_ID.
    ///  - Stored token      → arm the API client, fetch `/users/me`, and go
    ///    `.authenticated`. On a 401 (expired), try ONE refresh then retry.
    ///  - Any hard failure  → drop to guest (never a login wall).
    private func restoreAuthState() async {
        guard let token = authService.getStoredToken() else {
            auth.status = .unauthenticated   // guest — app still shown
            return
        }

        await apiClient.setAuthToken(token)

        // Own the refresh decision here (allowAuthRetry:false bypasses the APIClient
        // interceptor) so a TRANSIENT failure is never mistaken for a dead session.
        do {
            applyProfile(try await fetchCurrentUserNoRetry())
            auth.status = .authenticated
            await onAuthenticated()
            return
        } catch {
            // A non-auth error (offline / 5xx) → preserve the STORED token, run as guest.
            guard AppError.from(error).isAuthError else {
                // Disarm the CLIENT token to match what we are about to show.
                //
                // The Keychain copy is deliberately kept (this is transient — the next launch
                // must be able to restore), but leaving the token armed on APIClient made the
                // app's wire identity disagree with its UI: `auth.status` says guest and every
                // screen renders the signed-out affordances, while every request still carries
                // the Bearer token and the backend answers as the real account. The user sees
                // "Sign In" and a guest profile, yet their watchlist writes land on the real
                // account and report generation debits their real credits — and the guest
                // per-install partition they appear to be in is not the one being written.
                await apiClient.setAuthToken(nil)
                auth.status = .unauthenticated
                return
            }
            // Access token rejected → fall through to an explicit refresh.
        }

        do {
            try await authService.refreshToken()
        } catch {
            // Clear ONLY on a genuine auth failure (refresh token itself dead). A
            // transient refresh outage (offline / 5xx) preserves the token so the
            // next launch can restore.
            if AppError.from(error).isAuthError {
                authService.clearToken()
                await apiClient.setAuthToken(nil)
                user = UserState()
                // The session is definitively over (the refresh token itself is dead), so this is
                // a sign-out in everything but name — drop the Learn data with it, exactly as
                // `signOut()` does. Without this, an expired session left the previous account's
                // completions and bookmarks on the device for the NEXT person to sign in, and
                // their `pushUnsynced` would write them into that account.
                //
                // Deliberately NOT done on the two transient branches above/below: those keep the
                // token because the same user is expected back, and wiping there would throw away
                // local progress that has not synced yet (an offline learner's work).
                discardLearnDataForEndedSession()
            }
            auth.status = .unauthenticated
            return
        }

        // Refresh succeeded → retry the profile once.
        do {
            applyProfile(try await fetchCurrentUserNoRetry())
            auth.status = .authenticated
            await onAuthenticated()
        } catch {
            // Refreshed OK but the profile read still failed → transient; preserve the STORED
            // token so the next launch can restore, but disarm the client one so the wire
            // identity matches the guest UI we are about to present (same reasoning as above).
            await apiClient.setAuthToken(nil)
            auth.status = .unauthenticated
        }
    }

    /// Fetch `/users/me` WITHOUT the APIClient 401-refresh interceptor, so
    /// `restoreAuthState` can distinguish a transient failure from a dead session.
    private func fetchCurrentUserNoRetry() async throws -> UserProfile {
        try await apiClient.request(
            endpoint: .getCurrentUser,
            responseType: UserProfile.self,
            allowAuthRetry: false
        )
    }

    /// Store the fetched profile AND map its tier string into the `UserTier` enum.
    /// This is the ONLY place `user.tier` is assigned — it drives the tier badge,
    /// paywall highlighting, and `canGenerateResearch`.
    func applyProfile(_ profile: UserProfile) {
        user.profile = profile
        user.tier = UserTier(rawValue: profile.tier) ?? .free
    }

    /// Post-authentication side effects: claim guest data, refresh credits, pull synced settings.
    /// Called from every path that transitions to `.authenticated`.
    private func onAuthenticated() async {
        // FIRST — before any read of the user's data. Migration 108 partitions guest watchlists
        // per install, so a user who added tickers during onboarding and then signed up owns
        // nothing until these rows are moved. Claiming after a watchlist read would show them an
        // empty list they'd have to pull-to-refresh away.
        await claimGuestDataIfNeeded()
        await refreshCredits()
        SettingsSyncManager.shared.hydrate()
        PushNotificationManager.shared.flushPendingToken()
        hydrateLearnStores()
    }

    /// Pull the user's Learn progress down at the auth transition.
    ///
    /// `BookProgressStore.hydrate()` was reachable from exactly ONE place — the Book Library
    /// screen's `.task`. Anything that shows book progress WITHOUT going through the Library
    /// first therefore rendered the empty UserDefaults set: on a fresh install, or a new device,
    /// the Learn tab's "continue reading" row showed nothing and a part-finished book offered to
    /// start at core 1, silently discarding progress the server already had. The four stores are
    /// all device-global and all hydrate the same way, so they belong here — the single funnel
    /// every path to `.authenticated` passes through, and the mirror of
    /// `discardLearnDataForEndedSession()` on the way out.
    ///
    /// Fire-and-forget and individually isolated: each store swallows its own errors, and a
    /// Learn sync must never delay or fail sign-in.
    private func hydrateLearnStores() {
        Task {
            await BookProgressStore.shared.hydrate()
            await BookmarkStore.shared.hydrate()
            await JourneyProgressStore.shared.hydrate()
            await MoneyMovesProgressStore.shared.hydrate()
        }
    }

    /// Re-entrancy guard for `claimGuestDataIfNeeded`. `restoreAuthState()` on launch can race an
    /// explicit `signIn()`, and two concurrent claims both read the guest rows before either
    /// writes — harmless to the data (the second UPDATE just re-stamps rows already moved) but it
    /// double-counts, which makes the log lie about what happened. `AppState` is `@MainActor`, so
    /// this check-and-set cannot interleave.
    private var isClaimingGuestData = false

    /// Move this install's guest watchlist + portfolios + Learn progress onto the account that
    /// just signed in (Learn covers completions AND book bookmarks — one unified table).
    ///
    /// Deliberately NOT latched in `UserDefaults`: the endpoint is idempotent by construction
    /// (the rows are gone the second time, and it refuses the shared legacy bucket outright), and
    /// a persisted latch would permanently skip the claim for anyone who signed in on a build
    /// that shipped before this call existed — the exact users who need it. One cheap indexed
    /// POST per auth transition is the right trade.
    ///
    /// Never throws and never blocks sign-in: the user is already authenticated by the time this
    /// runs, so a failure here must not turn a successful sign-in into a visible error.
    private func claimGuestDataIfNeeded() async {
        guard !isClaimingGuestData else { return }
        isClaimingGuestData = true
        defer { isClaimingGuestData = false }

        do {
            // `AccountRepository.shared` rather than an injected dependency: adding one would mean
            // changing `configure(apiClient:authService:)`, whose only caller is iosApp.swift.
            // Matches how the two lines below reach SettingsSyncManager / PushNotificationManager.
            let result = try await AccountRepository.shared.claimGuestData()
            // No refresh signal is published on purpose: `TrackingViewModel.loadData()` has no
            // "already loaded" short-circuit, so the Tracking tab re-reads whenever it reappears
            // after the sign-in sheet dismisses. A revision counter here would be state nothing
            // observes.
            #if DEBUG
            if let skipped = result.skipped {
                print("ℹ️ [AppState] guest claim skipped: \(skipped)")
            } else if let error = result.error {
                print("⚠️ [AppState] guest claim partial failure: \(error)")
            } else {
                print("✅ [AppState] guest claim: \(result.claimed.watchlistItems) watchlist, \(result.claimed.portfolios) portfolio(s)")
            }
            #endif
        } catch {
            #if DEBUG
            print("⚠️ [AppState] claimGuestData failed: \(AppError.from(error).message)")
            #endif
        }
    }

    /// Refresh the credit balance into `user.credits` (single source of truth).
    /// Best-effort: failures are logged, not surfaced (the Profile screen shows
    /// the last-known balance rather than an error toast).
    func refreshCredits() async {
        do {
            user.credits = try await apiClient.request(
                endpoint: .getUserCredits,
                responseType: CreditInfo.self
            )
        } catch {
            #if DEBUG
            print("⚠️ [AppState] refreshCredits failed: \(AppError.from(error).message)")
            #endif
        }
    }

    /// Sign out. Resets local state immediately for a snappy UI; the backend
    /// `/auth/logout` call + Keychain/APIClient token clear run in the background
    /// (they need the token to still be set, so they fire before it is cleared).
    func signOut() {
        Task { [authService] in await authService?.signOut() }
        auth.status = .unauthenticated
        user = UserState()
        watchlist = WatchlistState()
        research = ResearchState()

        discardLearnDataForEndedSession()
        // Same argument, different store: the synced preference keys are device-global too, so
        // without this the next account inherits the previous user's persona, playback speed,
        // appearance, and notification opt-ins — and then pushes them up as its own.
        SettingsSyncManager.shared.clearLocalForEndedSession()
    }

    /// Drop this device's Learn progress + bookmarks because the session that owned them ended.
    ///
    /// Learn state is DEVICE-GLOBAL — the four stores' UserDefaults keys carry no user id — so
    /// leaving it in place handed the next account to sign in on this device the previous user's
    /// data: each store's `hydrate()` unions the stale local set into the new account's view, and
    /// `pushUnsynced()` then POSTs those items INTO that account, durable and visible on all of
    /// its other devices.
    ///
    /// Costs a signed-in user nothing: their rows are on the server, so signing back in
    /// re-hydrates everything on the next Learn open.
    private func discardLearnDataForEndedSession() {
        // Bump FIRST: a hydrate issued seconds ago is still in flight and would otherwise land
        // after these resets and refill the stores with the ended session's rows.
        LearnIdentityEpoch.bump()
        BookProgressStore.shared.reset()
        JourneyProgressStore.shared.reset()
        MoneyMovesProgressStore.shared.reset()
        BookmarkStore.shared.reset()
    }

    /// Sign in. Throws on failure so the SignInView can render the error inline.
    func signIn(email: String, password: String) async throws {
        auth.status = .loading
        do {
            applyProfile(try await authService.signIn(email: email, password: password))
            auth.status = .authenticated
            await onAuthenticated()
        } catch {
            auth.status = .unauthenticated
            throw error
        }
    }

    /// Sign up. Throws on failure so the SignInView can render the error inline.
    ///
    /// Returns the outcome because email confirmation is REQUIRED: the normal result is
    /// `.needsEmailConfirmation`, where the account exists but there is no session yet, so
    /// auth status must stay `.unauthenticated`. Treating that as a successful sign-in would
    /// drop the user into an app with no working token.
    @discardableResult
    func signUp(
        email: String, password: String, displayName: String
    ) async throws -> SignUpOutcome {
        auth.status = .loading
        do {
            let outcome = try await authService.signUp(
                email: email, password: password, displayName: displayName
            )
            switch outcome {
            case .needsEmailConfirmation:
                auth.status = .unauthenticated
            case .signedIn(let profile):
                applyProfile(profile)
                auth.status = .authenticated
                await onAuthenticated()
            }
            return outcome
        } catch {
            auth.status = .unauthenticated
            throw error
        }
    }

    /// Re-send the signup confirmation email.
    func resendConfirmation(email: String) async throws {
        try await authService.resendConfirmation(email: email)
    }

    /// Complete a social sign-in from a provider handshake.
    ///
    /// Both provider paths converge here. Not subject to the email-confirmation gate: Apple
    /// and Google supply an already-verified address.
    func completeSocialSignIn(_ result: SocialSignInResult) async throws {
        auth.status = .loading
        do {
            let profile: UserProfile
            switch result {
            case let .identityToken(provider, token, nonce, displayName):
                profile = try await authService.signInWithProvider(
                    provider: provider, idToken: token, nonce: nonce, displayName: displayName
                )
            case let .supabaseSession(accessToken):
                profile = try await authService.exchangeSupabaseSession(accessToken: accessToken)
            }
            applyProfile(profile)
            auth.status = .authenticated
            await onAuthenticated()
        } catch {
            auth.status = .unauthenticated
            throw error
        }
    }

    // MARK: - Error Handling

    func handleError(_ error: Error) {
        let appError = AppError.from(error)

        // Handle auth errors globally
        if case .unauthorized = appError {
            signOut()
            return
        }

        currentError = appError
    }

    func clearError() {
        currentError = nil
    }

    func showToast(_ message: String, type: ToastType = .info) {
        toastMessage = ToastMessage(message: message, type: type)

        // Auto-dismiss after 3 seconds
        Task {
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            if toastMessage?.message == message {
                toastMessage = nil
            }
        }
    }
}

// MARK: - Auth State

@Observable
final class AuthState {
    var status: AuthStatus = .unknown
    var accessToken: String?

    var isAuthenticated: Bool {
        status == .authenticated
    }

    var isLoading: Bool {
        status == .loading
    }
}

enum AuthStatus: Equatable {
    case unknown
    case loading
    case authenticated
    case unauthenticated
}

// MARK: - User State

@Observable
final class UserState {
    var profile: UserProfile?
    var credits: CreditInfo?
    var tier: UserTier = .free

    var displayName: String {
        profile?.displayName ?? "Guest"
    }

    var remainingCredits: Int {
        credits?.remaining ?? 0
    }

    var canGenerateResearch: Bool {
        remainingCredits > 0
    }
}

struct UserProfile: Codable, Identifiable, Sendable {
    let id: String
    let email: String
    let displayName: String?
    let avatarUrl: String?
    let tier: String
    let createdAt: String

    enum CodingKeys: String, CodingKey {
        case id, email, tier
        case displayName = "display_name"
        case avatarUrl = "avatar_url"
        case createdAt = "created_at"
    }
}

struct CreditInfo: Codable, Sendable {
    let total: Int
    let used: Int
    let remaining: Int
    let resetsAt: String?

    enum CodingKeys: String, CodingKey {
        case total, used, remaining
        case resetsAt = "resets_at"
    }
}

enum UserTier: String, Codable, Sendable {
    case free
    case pro
    case premium
}

// MARK: - Watchlist State

@Observable
final class WatchlistState {
    var stocks: [WatchlistStock] = []
    var isLoading: Bool = false

    func contains(_ ticker: String) -> Bool {
        stocks.contains { $0.ticker.uppercased() == ticker.uppercased() }
    }

    func toggle(_ stock: WatchlistStock) {
        if let index = stocks.firstIndex(where: { $0.ticker == stock.ticker }) {
            stocks.remove(at: index)
        } else {
            stocks.insert(stock, at: 0)
        }
    }
}

struct WatchlistStock: Codable, Identifiable, Equatable, Sendable {
    var id: String { ticker }
    let ticker: String
    let companyName: String
    let logoUrl: String?
    var price: Double?
    var changePercent: Double?

    enum CodingKeys: String, CodingKey {
        case ticker
        case companyName = "company_name"
        case logoUrl = "logo_url"
        case price
        case changePercent = "change_percent"
    }
}

// MARK: - Research State

@Observable
final class ResearchState {
    var reports: [ResearchReportSummary] = []
    var generatingReports: Set<String> = [] // Report IDs currently generating
    var selectedPersona: String = "buffett"

    func isGenerating(_ reportId: String) -> Bool {
        generatingReports.contains(reportId)
    }

    var hasActiveGeneration: Bool {
        !generatingReports.isEmpty
    }
}

struct ResearchReportSummary: Codable, Identifiable, Sendable {
    let id: String
    let stockId: String
    let ticker: String
    let companyName: String
    let investorPersona: String
    let status: String
    let title: String?
    let executiveSummary: String?
    let createdAt: String
    let completedAt: String?

    enum CodingKeys: String, CodingKey {
        case id
        case stockId = "stock_id"
        case ticker
        case companyName = "company_name"
        case investorPersona = "investor_persona"
        case status, title
        case executiveSummary = "executive_summary"
        case createdAt = "created_at"
        case completedAt = "completed_at"
    }

    var isCompleted: Bool { status == "completed" }
    var isFailed: Bool { status == "failed" }
    var isPending: Bool { status == "pending" || status == "processing" }
}

// MARK: - Toast Message

struct ToastMessage: Equatable, Sendable {
    let message: String
    let type: ToastType
}

enum ToastType: Sendable {
    case success
    case error
    case info
    case warning
}

// MARK: - Environment Key

extension EnvironmentValues {
    @Entry var appState: AppState = AppState()
}
