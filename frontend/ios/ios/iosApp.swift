//
//  iosApp.swift
//  ios
//
//  Created by Hai Phan on 12/30/25.
//
//  App Entry Point
//
//  This is where we:
//  1. Initialize global AppState
//  2. Configure services (API client, auth)
//  3. Inject state via Environment
//

import SwiftUI

@main
struct iosApp: App {

    // MARK: - Global State

    /// Single source of truth for app-wide state
    /// Injected into all views via .environment()
    @State private var appState = AppState()

    /// Tracks if services have been configured
    @State private var isConfigured = false

    // MARK: - Initialization

    init() {
        // Configure appearance
        configureAppearance()
    }

    // MARK: - Body

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appState)
                .preferredColorScheme(.dark)
                .task {
                    guard !isConfigured else { return }
                    isConfigured = true

                    // Get services (APIClient.shared is safe to access in async context)
                    let apiClient = APIClient.shared
                    let authService = AuthService(apiClient: apiClient)

                    // Configure AppState with services
                    appState.configure(
                        apiClient: apiClient,
                        authService: authService
                    )

                    // Restore auth token to API client
                    if let token = authService.getStoredToken() {
                        await apiClient.setAuthToken(token)
                    }
                }
        }
    }

    // MARK: - Appearance Configuration

    private func configureAppearance() {
        // Navigation bar appearance
        let navAppearance = UINavigationBarAppearance()
        navAppearance.configureWithOpaqueBackground()
        navAppearance.backgroundColor = UIColor(AppColors.background)
        navAppearance.titleTextAttributes = [.foregroundColor: UIColor.white]
        navAppearance.largeTitleTextAttributes = [.foregroundColor: UIColor.white]

        UINavigationBar.appearance().standardAppearance = navAppearance
        UINavigationBar.appearance().compactAppearance = navAppearance
        UINavigationBar.appearance().scrollEdgeAppearance = navAppearance
    }
}

// MARK: - Root View

/// Root view that handles auth state and navigation
struct RootView: View {
    @Environment(AppState.self) private var appState

    var body: some View {
        Group {
            switch appState.auth.status {
            case .unknown, .loading:
                // Loading/splash screen
                SplashView()

            case .unauthenticated:
                // For now, go straight to main app (no auth required yet)
                // Later: Show onboarding/login
                MainTabView()

            case .authenticated:
                MainTabView()
            }
        }
        .overlay {
            // Global error toast
            if let error = appState.currentError {
                ErrorToastView(error: error) {
                    appState.clearError()
                }
            }
        }
        .overlay {
            // Global toast messages
            if let toast = appState.toastMessage {
                ToastView(message: toast)
            }
        }
    }
}

// MARK: - Splash View

struct SplashView: View {
    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 20) {
                Image(systemName: "chart.line.uptrend.xyaxis.circle.fill")
                    .font(.system(size: 80))
                    .foregroundColor(AppColors.primaryBlue)

                Text("AI Value Investor")
                    .font(AppTypography.title)
                    .foregroundColor(AppColors.textPrimary)

                ProgressView()
                    .tint(AppColors.primaryBlue)
            }
        }
    }
}

// MARK: - Error Toast View

struct ErrorToastView: View {
    let error: AppError
    let onDismiss: () -> Void

    var body: some View {
        VStack {
            Spacer()

            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .foregroundColor(AppColors.bearish)

                    Text(error.title)
                        .font(AppTypography.headline)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    Button(action: onDismiss) {
                        Image(systemName: "xmark")
                            .foregroundColor(AppColors.textSecondary)
                    }
                }

                Text(error.message)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)

                if error.suggestedAction != .fixInput {
                    Button(action: onDismiss) {
                        Text(error.suggestedAction.buttonTitle)
                            .font(AppTypography.bodyBold)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                }
            }
            .padding()
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
            .padding(.horizontal)
            .padding(.bottom, 100) // Above tab bar
        }
        .transition(.move(edge: .bottom).combined(with: .opacity))
        .animation(.spring(), value: error.id)
    }
}

// MARK: - Toast View

struct ToastView: View {
    let message: ToastMessage

    var body: some View {
        VStack {
            Spacer()

            HStack {
                Image(systemName: iconName)
                    .foregroundColor(iconColor)

                Text(message.message)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.pill)
            .padding(.bottom, 100)
        }
        .transition(.move(edge: .bottom).combined(with: .opacity))
    }

    private var iconName: String {
        switch message.type {
        case .success: return "checkmark.circle.fill"
        case .error: return "exclamationmark.circle.fill"
        case .info: return "info.circle.fill"
        case .warning: return "exclamationmark.triangle.fill"
        }
    }

    private var iconColor: Color {
        switch message.type {
        case .success: return AppColors.bullish
        case .error: return AppColors.bearish
        case .info: return AppColors.primaryBlue
        case .warning: return AppColors.neutral
        }
    }
}
