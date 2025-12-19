import SwiftUI

@MainActor
class ProfileViewModel: ObservableObject {
    @Published var user: User?
    @Published var usage: UsageStats?
    @Published var stats: UserStats?
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiService = APIService()

    func loadProfile() async {
        isLoading = true
        errorMessage = nil

        async let userData = loadUser()
        async let usageData = loadUsage()
        async let statsData = loadStats()

        await userData
        await usageData
        await statsData

        isLoading = false
    }

    private func loadUser() async {
        do {
            user = try await apiService.getUserProfile()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadUsage() async {
        do {
            usage = try await apiService.getUserUsage()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func loadStats() async {
        do {
            stats = try await apiService.getUserStats()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func signOut() async {
        do {
            try await apiService.logout()
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}
