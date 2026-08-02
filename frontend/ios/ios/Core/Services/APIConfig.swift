//
//  APIConfig.swift
//  ios
//
//  API Configuration
//
//  Handles environment-specific configuration:
//  - Base URL (dev, staging, production)
//  - Timeouts
//  - Retry policies
//

import Foundation

// MARK: - App Environment

enum AppEnvironment: Sendable {
    case development
    case staging
    case production

    nonisolated static var current: AppEnvironment {
        #if DEBUG
        return .development
        #else
        // Check for staging flag or default to production
        if ProcessInfo.processInfo.environment["STAGING"] != nil {
            return .staging
        }
        return .production
        #endif
    }
}

// MARK: - API Configuration

enum APIConfig: Sendable {

    // MARK: - Base URLs

    nonisolated static var baseURL: URL {
        // Auto-detection: ServerEnvironmentManager probes localhost on app launch
        // and caches the result. Manual overrides (USE_LOCAL=1, USE_RAILWAY=1)
        // are still supported via environment variables.
        #if DEBUG
        if let resolved = ServerEnvironmentManager.shared.resolvedBaseURL {
            return resolved
        }
        // Fallback if resolve() hasn't been called yet (e.g. unit tests)
        if ProcessInfo.processInfo.environment["USE_LOCAL"] == "1" {
            return URL(string: "http://127.0.0.1:8000")!
        }
        #endif

        return URL(string: "https://ai-value-investor-app-production.up.railway.app")!
    }

    // MARK: - OAuth (Google via the Supabase web redirect)

    /// Supabase project URL, used ONLY to build the OAuth authorize URL for the web flow.
    ///
    /// This is a public project URL, not a secret — the anon key is deliberately NOT in the
    /// app (all data traffic goes through our own backend). Read from Info.plist so it isn't
    /// hardcoded in two places.
    nonisolated static var supabaseURL: String? {
        (Bundle.main.object(forInfoDictionaryKey: "SupabaseURL") as? String)?
            .trimmingCharacters(in: .whitespaces)
            .replacingOccurrences(of: "/$", with: "", options: .regularExpression)
    }

    /// Custom URL scheme Supabase redirects back to after Google consent. Must match the
    /// CFBundleURLSchemes entry in Info.plist AND the redirect URL allow-list in the
    /// Supabase dashboard (Authentication → URL Configuration).
    nonisolated static let oauthCallbackScheme = "caydex"

    // MARK: - Timeouts

    /// Default request timeout (seconds)
    static let defaultTimeout: TimeInterval = 30

    /// Timeout for AI generation requests (seconds)
    static let aiGenerationTimeout: TimeInterval = 120

    /// Timeout for chat messages (seconds)
    static let chatTimeout: TimeInterval = 60

    // MARK: - Retry Policy

    /// Maximum retry attempts for failed requests
    static let maxRetryAttempts = 3

    /// Base delay between retries (seconds)
    static let retryBaseDelay: TimeInterval = 1.0

    /// Maximum delay between retries (seconds)
    static let retryMaxDelay: TimeInterval = 10.0

    // MARK: - Polling Configuration

    /// Interval for polling research status (seconds)
    nonisolated static let researchPollInterval: TimeInterval = 3.0

    /// Maximum duration the CLIENT keeps polling before it stops (seconds).
    /// The backend keeps generating past this — the report still resolves in
    /// the Reports list (startReportsPolling), and if it never delivers the
    /// server-side reconciliation sweep refunds the credits. Raised from 180s
    /// so a normal slow generation no longer surfaces a spurious "timeout".
    nonisolated static let researchPollTimeout: TimeInterval = 300.0 // 5 minutes

    // MARK: - Cache Configuration

    /// Default cache TTL (seconds)
    static let defaultCacheTTL: TimeInterval = 300 // 5 minutes

    /// Stock quote cache TTL (seconds)
    static let quoteCacheTTL: TimeInterval = 60 // 1 minute

    /// News feed cache TTL (seconds)
    static let newsCacheTTL: TimeInterval = 300 // 5 minutes

    // MARK: - Pagination

    /// Default page size for list endpoints
    static let defaultPageSize = 20

    /// Maximum page size allowed
    static let maxPageSize = 100
}

// MARK: - Feature Flags

enum FeatureFlags: Sendable {
    /// Enable offline mode with cached data
    nonisolated static var offlineModeEnabled: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }

    /// Enable debug logging for API calls
    nonisolated static var apiLoggingEnabled: Bool {
        #if DEBUG
        return true
        #else
        return false
        #endif
    }

    /// Enable mock data for development
    nonisolated static var useMockData: Bool {
        #if DEBUG
        return ProcessInfo.processInfo.environment["USE_MOCK"] != nil
        #else
        return false
        #endif
    }

    // MARK: - Built but not yet honest to ship
    //
    // The rule: if a control is visible, it must do what it says. A toggle that stores a
    // preference nothing reads is worse than an absent toggle — it tells the user
    // something is happening when nothing is. These hide such controls until the backing
    // behaviour exists, without deleting UI that will need re-enabling.

    /// Notification preference toggles (13 of them, in `NotificationsSettingsView`).
    ///
    /// FALSE because there is no delivery pipeline: `push_service.send_to_user` exists on
    /// the backend but has **zero callers**, so every toggle writes a preference nothing
    /// ever reads. Permission prompting and APNs token registration both already work —
    /// the gap is purely that nothing sends.
    ///
    /// Flip to `true` when: at least one real event calls `send_to_user`, the APNs `.p8`
    /// key + Push capability are configured, and `APNS_*` env vars are set on Railway.
    /// The `aps-environment` entitlement is already wired per-configuration
    /// (Debug → development, Release → production).
    nonisolated static let notificationPreferencesEnabled = false

    /// Appearance mode picker (Light / Dark / System).
    ///
    /// TRUE: the forced-dark sweep is done — the per-view `.preferredColorScheme(.dark)`
    /// modifiers were removed and `AppColors` surface/text tokens are now adaptive
    /// (see Theme/AppTheme.swift), so selecting Light or System actually re-themes the app.
    /// Appearance is applied by a reactive root `.preferredColorScheme` (iosApp) plus the
    /// `AppearanceManager` window override (robust across sheets/covers).
    nonisolated static let appearanceModeEnabled = true
}
