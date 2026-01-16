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

    // MARK: - Services (Injected)

    private(set) var apiClient: APIClient!
    private(set) var authService: AuthService!

    // MARK: - Initialization

    init() {
        // Services will be set up in configure()
    }

    /// Configure services - called from App entry point
    func configure(apiClient: APIClient, authService: AuthService) {
        self.apiClient = apiClient
        self.authService = authService

        // Restore auth state from keychain
        Task {
            await restoreAuthState()
        }
    }

    // MARK: - Auth Actions

    private func restoreAuthState() async {
        guard authService.getStoredToken() != nil else {
            auth.status = .unauthenticated
            return
        }

        auth.status = .loading

        do {
            let user = try await apiClient.request(
                endpoint: .getCurrentUser,
                responseType: UserProfile.self
            )
            self.user.profile = user
            auth.status = .authenticated
        } catch {
            // Token invalid, clear it
            authService.clearToken()
            auth.status = .unauthenticated
        }
    }

    func signOut() {
        authService.clearToken()
        auth.status = .unauthenticated
        user = UserState()
        watchlist = WatchlistState()
        research = ResearchState()
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

struct UserProfile: Codable, Identifiable {
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

struct CreditInfo: Codable {
    let total: Int
    let used: Int
    let remaining: Int
    let resetsAt: String?

    enum CodingKeys: String, CodingKey {
        case total, used, remaining
        case resetsAt = "resets_at"
    }
}

enum UserTier: String, Codable {
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

struct WatchlistStock: Codable, Identifiable, Equatable {
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

struct ResearchReportSummary: Codable, Identifiable {
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

struct ToastMessage: Equatable {
    let message: String
    let type: ToastType
}

enum ToastType {
    case success
    case error
    case info
    case warning
}

// MARK: - Environment Key

extension EnvironmentValues {
    @Entry var appState: AppState = AppState()
}
