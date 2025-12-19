import SwiftUI
import Combine

@MainActor
class AuthViewModel: ObservableObject {
    @Published var isAuthenticated = false
    @Published var currentUser: User?
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiService = APIService()
    private let apiClient = APIClient.shared

    init() {
        checkAuthStatus()
    }

    func checkAuthStatus() {
        isAuthenticated = apiClient.isAuthenticated

        if isAuthenticated {
            Task {
                await loadCurrentUser()
            }
        }
    }

    // MARK: - Supabase Auth Integration
    // Note: You'll need to integrate Supabase Swift SDK
    // Add dependency: https://github.com/supabase/supabase-swift

    func signInWithSupabase(email: String, password: String) async {
        isLoading = true
        errorMessage = nil

        do {
            // Step 1: Authenticate with Supabase
            // TODO: Integrate Supabase SDK
            // let supabaseAuth = await supabaseClient.auth.signIn(email: email, password: password)
            // let supabaseToken = supabaseAuth.session.accessToken

            // For now, using a placeholder:
            let supabaseToken = "supabase_token_placeholder"

            // Step 2: Exchange Supabase token for backend token
            _ = try await apiService.login(supabaseToken: supabaseToken)

            // Step 3: Load user profile
            await loadCurrentUser()

            isAuthenticated = true
        } catch {
            errorMessage = error.localizedDescription
            isAuthenticated = false
        }

        isLoading = false
    }

    func signOut() async {
        isLoading = true

        do {
            try await apiService.logout()
            isAuthenticated = false
            currentUser = nil
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    private func loadCurrentUser() async {
        do {
            currentUser = try await apiService.getCurrentUser()
        } catch {
            errorMessage = error.localizedDescription
            if let apiError = error as? APIError, apiError.isAuthError {
                isAuthenticated = false
            }
        }
    }
}
