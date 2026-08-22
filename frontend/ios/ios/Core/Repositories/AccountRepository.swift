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
    func updateProfile(displayName: String?) async throws -> UserProfile
    /// Set the profile picture from an already-processed square JPEG.
    func uploadAvatar(jpeg: Data) async throws -> UserProfile
    /// Clear the profile picture.
    func deleteAvatar() async throws -> UserProfile
    func fetchCredits() async throws -> CreditInfo
    func fetchPlanCatalog() async throws -> PlanCatalog
    func fetchCreditPackCatalog() async throws -> CreditPackCatalog
    func fetchSubscription() async throws -> SubscriptionDTO
    func fetchSettings() async throws -> [String: PreferenceValue]
    func updateSettings(_ preferences: [String: PreferenceValue]) async throws -> [String: PreferenceValue]
    func registerDevice(token: String, environment: String?) async throws -> Bool
    func unregisterDevice(token: String) async throws -> Bool
    func verifyPurchase(signedTransaction: String) async throws -> PurchaseApplied
    func claimGuestData() async throws -> ClaimGuestDataResult
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

    /// POST /billing/verify — hand an Apple-signed transaction to the server and return what
    /// it applied.
    ///
    /// Only the signed blob is sent. Everything that comes back — the tier for a subscription,
    /// the credits for a consumable pack — is derived from the server's verification of
    /// Apple's signature, so the client never asserts what it bought.
    ///
    /// Returns `PurchaseApplied` rather than a bare tier string: a credit pack grants credits
    /// and leaves the tier alone, so a tier string cannot tell a $19.99 top-up from a no-op.
    func verifyPurchase(signedTransaction: String) async throws -> PurchaseApplied {
        let response = try await apiClient.request(
            endpoint: .verifyPurchase(signedTransaction: signedTransaction),
            responseType: VerifyPurchaseResponse.self
        )
        return PurchaseApplied(from: response)
    }

    /// PATCH /users/me. The endpoint and its `UpdateProfileRequest` schema already
    /// existed on both sides; nothing called them, so there was no way to change a
    /// display name from the app. Returns the updated profile so the caller can adopt
    /// server state rather than guessing what was saved.
    func updateProfile(displayName: String?) async throws -> UserProfile {
        try await apiClient.request(
            endpoint: .updateProfile(displayName: displayName),
            responseType: UserProfile.self
        )
    }

    /// Base64-encoding happens HERE, at the transport boundary — not in the ViewModel and not
    /// in the picker. Everything above this line deals in `Data`; only the wire needs a string.
    ///
    /// Both calls return the FULL updated profile, so the caller adopts server state (including
    /// the freshly SIGNED avatar URL) rather than guessing what was saved.
    func uploadAvatar(jpeg: Data) async throws -> UserProfile {
        try await apiClient.request(
            endpoint: .updateAvatar(imageBase64: jpeg.base64EncodedString()),
            responseType: UserProfile.self
        )
    }

    func deleteAvatar() async throws -> UserProfile {
        try await apiClient.request(endpoint: .deleteAvatar, responseType: UserProfile.self)
    }

    func fetchCredits() async throws -> CreditInfo {
        try await apiClient.request(endpoint: .getUserCredits, responseType: CreditInfo.self)
    }

    func fetchPlanCatalog() async throws -> PlanCatalog {
        try await apiClient.request(endpoint: .getPlanCatalog, responseType: PlanCatalog.self)
    }

    /// GET /billing/credit-packs — the consumable catalog behind the Buy Credits screen.
    ///
    /// The CREDIT COUNT comes from here rather than from StoreKit, because `Product` has no
    /// such field: hardcoding it in the app (or parsing it out of a localized description)
    /// would let a server-side config change make the screen lie about what the user gets.
    /// The PRICE goes the other way — Apple's localized `displayPrice` wins.
    func fetchCreditPackCatalog() async throws -> CreditPackCatalog {
        try await apiClient.request(
            endpoint: .getCreditPackCatalog, responseType: CreditPackCatalog.self
        )
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

    /// Detach this device's APNs token. Returns true when it is no longer registered.
    func unregisterDevice(token: String) async throws -> Bool {
        let stillRegistered = try await apiClient.request(
            endpoint: .unregisterDevice(token: token),
            responseType: DeviceRegisterResult.self
        ).registered
        return !stillRegistered
    }

    /// Claim this install's guest watchlist + portfolios for the signed-in account.
    /// The install id rides along in the `X-Guest-Id` header `APIClient` sets on every request.
    func claimGuestData() async throws -> ClaimGuestDataResult {
        try await apiClient.request(
            endpoint: .claimGuestData,
            responseType: ClaimGuestDataResult.self
        )
    }
}
