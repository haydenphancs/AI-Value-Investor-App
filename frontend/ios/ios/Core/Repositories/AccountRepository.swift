//
//  AccountRepository.swift
//  ios
//
//  Repository layer for the account surface: profile, credits, plan catalog,
//  subscription, synced settings, and APNs device registration.
//
//  Follows the same MVVM + Repository pattern as `HomeRepository` /
//  `StockRepository`: a protocol the ViewModels depend on + a live, backend-backed
//  implementation with an injected `APIClient`. Errors propagate for the caller to
//  map via `AppError`. Consumed by `ProfileViewModel`, `PaywallViewModel`, and the
//  settings-sync + push layers so the account network calls live in one place.
//

import Foundation

// MARK: - Protocol

protocol AccountRepositoryProtocol: Sendable {
    func fetchProfile() async throws -> UserProfile
    func updateProfile(displayName: String?, avatarUrl: String?) async throws -> UserProfile
    func fetchCredits() async throws -> CreditInfo
    func fetchPlanCatalog() async throws -> PlanCatalog
    func fetchSubscription() async throws -> SubscriptionDTO
    func fetchSettings() async throws -> [String: PreferenceValue]
    func updateSettings(_ preferences: [String: PreferenceValue]) async throws -> [String: PreferenceValue]
    func registerDevice(token: String, environment: String?) async throws -> Bool
}

// MARK: - Live implementation

final class AccountRepository: AccountRepositoryProtocol {

    static let shared = AccountRepository()

    private let apiClient: APIClient

    init(apiClient: APIClient = .shared) {
        self.apiClient = apiClient
    }

    func fetchProfile() async throws -> UserProfile {
        try await apiClient.request(endpoint: .getCurrentUser, responseType: UserProfile.self)
    }

    /// PATCH /users/me. The endpoint and its `UpdateProfileRequest` schema already
    /// existed on both sides; nothing called them, so there was no way to change a
    /// display name from the app. Returns the updated profile so the caller can adopt
    /// server state rather than guessing what was saved.
    func updateProfile(displayName: String?, avatarUrl: String?) async throws -> UserProfile {
        try await apiClient.request(
            endpoint: .updateProfile(displayName: displayName, avatarUrl: avatarUrl),
            responseType: UserProfile.self
        )
    }

    func fetchCredits() async throws -> CreditInfo {
        try await apiClient.request(endpoint: .getUserCredits, responseType: CreditInfo.self)
    }

    func fetchPlanCatalog() async throws -> PlanCatalog {
        try await apiClient.request(endpoint: .getPlanCatalog, responseType: PlanCatalog.self)
    }

    func fetchSubscription() async throws -> SubscriptionDTO {
        try await apiClient.request(endpoint: .getMySubscription, responseType: SubscriptionDTO.self)
    }

    func fetchSettings() async throws -> [String: PreferenceValue] {
        try await apiClient.request(
            endpoint: .getMySettings, responseType: UserSettingsDTO.self
        ).preferences
    }

    func updateSettings(
        _ preferences: [String: PreferenceValue]
    ) async throws -> [String: PreferenceValue] {
        try await apiClient.request(
            endpoint: .updateMySettings(preferences: preferences),
            responseType: UserSettingsDTO.self
        ).preferences
    }

    func registerDevice(token: String, environment: String?) async throws -> Bool {
        try await apiClient.request(
            endpoint: .registerDevice(token: token, platform: "ios", environment: environment),
            responseType: DeviceRegisterResult.self
        ).registered
    }
}
