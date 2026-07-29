//
//  AuthService.swift
//  ios
//
//  Authentication Service
//
//  Handles:
//  - Sign in / Sign up
//  - Token storage (Keychain)
//  - Token refresh
//  - Sign out
//

import Foundation
import Security

// MARK: - Auth Response

/// Mirrors the backend `TokenResponse` (`backend/app/schemas/auth.py`) EXACTLY:
/// `{access_token, refresh_token, token_type, user_id}`. The backend does NOT nest
/// the user object or an `expires_in` here — an earlier DTO that required those two
/// fields made every login/register/refresh throw `DecodingError`. The canonical
/// profile is fetched separately via `GET /users/me` after the token is stored.
struct AuthResponse: Decodable, Sendable {
    let accessToken: String
    let refreshToken: String
    let tokenType: String
    let userId: String

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case tokenType = "token_type"
        case userId = "user_id"
    }
}

// MARK: - Auth Service

/// Handles authentication and token management
@MainActor
final class AuthService {

    // MARK: - Properties

    private let apiClient: APIClient
    private let keychain: KeychainService

    private static let accessTokenKey = "access_token"
    private static let refreshTokenKey = "refresh_token"

    /// Monotonic session counter, bumped on every session transition (sign-in,
    /// sign-up, sign-out). A token-persisting operation (refresh) that started
    /// under an older epoch discards its result, and sign-out's token clear is
    /// skipped if a newer session began mid-logout. This makes sign-out/sign-in
    /// races safe (a stale refresh can't resurrect a signed-out session; a quick
    /// re-sign-in isn't wiped by a late logout clear). @MainActor serializes it.
    private var sessionEpoch: Int = 0

    // MARK: - Initialization

    init(apiClient: APIClient, keychain: KeychainService? = nil) {
        self.apiClient = apiClient
        self.keychain = keychain ?? .shared
    }

    // MARK: - Authentication

    /// Sign in with email and password.
    /// Stores the tokens, arms the API client, then fetches the canonical profile
    /// from `GET /users/me` (the token body does not carry the user object).
    func signIn(email: String, password: String) async throws -> UserProfile {
        let response = try await apiClient.request(
            endpoint: .signIn(email: email, password: password),
            responseType: AuthResponse.self
        )

        // Store tokens
        saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)
        sessionEpoch += 1   // new session — invalidate any in-flight older-session refresh

        // Update API client
        await apiClient.setAuthToken(response.accessToken)

        return try await fetchCurrentUser()
    }

    /// Sign up with email and password.
    /// Same flow as `signIn`: store tokens → arm client → fetch `/users/me`.
    func signUp(email: String, password: String, displayName: String) async throws -> UserProfile {
        let response = try await apiClient.request(
            endpoint: .signUp(email: email, password: password, displayName: displayName),
            responseType: AuthResponse.self
        )

        // Store tokens
        saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)
        sessionEpoch += 1   // new session — invalidate any in-flight older-session refresh

        // Update API client
        await apiClient.setAuthToken(response.accessToken)

        return try await fetchCurrentUser()
    }

    /// Fetch the current user's canonical profile (`GET /users/me`).
    /// `UserProfile` decodes the backend `UserResponse`; the extra `updated_at`
    /// field is ignored by the decoder.
    func fetchCurrentUser() async throws -> UserProfile {
        try await apiClient.request(
            endpoint: .getCurrentUser,
            responseType: UserProfile.self
        )
    }

    /// Sign out
    func signOut() async {
        sessionEpoch += 1
        let epoch = sessionEpoch

        // Best-effort backend logout with the current token.
        try? await apiClient.request(endpoint: .signOut)

        // Only clear if no NEWER session started during the logout round-trip — a
        // quick re-sign-in bumps the epoch and its fresh tokens must survive.
        guard epoch == sessionEpoch else { return }
        clearToken()
        await apiClient.setAuthToken(nil)
    }

    /// Refresh the access token
    func refreshToken() async throws {
        guard let refreshToken = getStoredRefreshToken() else {
            throw APIError.unauthorized
        }

        let epoch = sessionEpoch
        let response = try await apiClient.request(
            endpoint: .refreshToken(refreshToken: refreshToken),
            responseType: AuthResponse.self
        )

        // Discard the refreshed tokens if the session changed mid-flight (sign-out
        // or a new sign-in) — otherwise a stale refresh could re-arm a signed-out
        // session or clobber a newer one.
        guard epoch == sessionEpoch else { return }

        // Store new tokens
        saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)

        // Update API client
        await apiClient.setAuthToken(response.accessToken)
    }

    // MARK: - Token Management

    /// Get stored access token
    func getStoredToken() -> String? {
        keychain.get(Self.accessTokenKey)
    }

    /// Get stored refresh token
    func getStoredRefreshToken() -> String? {
        keychain.get(Self.refreshTokenKey)
    }

    /// Check if user has stored token
    var hasStoredToken: Bool {
        getStoredToken() != nil
    }

    /// Save tokens to keychain
    private func saveTokens(accessToken: String, refreshToken: String) {
        keychain.set(accessToken, forKey: Self.accessTokenKey)
        keychain.set(refreshToken, forKey: Self.refreshTokenKey)
    }

    /// Clear stored tokens
    func clearToken() {
        keychain.delete(Self.accessTokenKey)
        keychain.delete(Self.refreshTokenKey)
    }
}

// MARK: - Keychain Service

/// Simple Keychain wrapper for secure token storage
/// @unchecked Sendable because Keychain APIs are thread-safe
final class KeychainService: @unchecked Sendable {

    static let shared = KeychainService()

    private let service = Bundle.main.bundleIdentifier ?? "com.aivalueinvestor"

    private init() {}

    func set(_ value: String, forKey key: String) {
        guard let data = value.data(using: .utf8) else { return }

        // Delete existing item first
        delete(key)

        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecValueData as String: data,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
        ]

        SecItemAdd(query as CFDictionary, nil)
    }

    func get(_ key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne
        ]

        var result: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &result)

        guard status == errSecSuccess,
              let data = result as? Data,
              let string = String(data: data, encoding: .utf8) else {
            return nil
        }

        return string
    }

    func delete(_ key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key
        ]

        SecItemDelete(query as CFDictionary)
    }

    func deleteAll() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service
        ]

        SecItemDelete(query as CFDictionary)
    }
}
