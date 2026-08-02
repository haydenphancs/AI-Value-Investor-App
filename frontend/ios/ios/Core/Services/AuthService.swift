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

/// Mirrors the backend `SignUpResponse`. Email confirmation is REQUIRED, so registration
/// normally returns `confirmationRequired = true` and NO tokens.
///
/// Every token field is Optional for exactly the reason the comment above describes: the
/// previous DTO required them, and decoding a token-less registration response against it
/// would throw `DecodingError` on every signup.
struct SignUpResponseDTO: Decodable, Sendable {
    let confirmationRequired: Bool
    let message: String
    let accessToken: String?
    let refreshToken: String?
    let userId: String?

    enum CodingKeys: String, CodingKey {
        case confirmationRequired = "confirmation_required"
        case message
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case userId = "user_id"
    }
}

/// What actually happened when the user registered.
enum SignUpOutcome: Sendable {
    /// Account created; the address must be confirmed before it can be used.
    case needsEmailConfirmation(message: String)
    /// Confirmation is disabled project-side, so a usable session exists.
    case signedIn(UserProfile)
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
        let response: AuthResponse
        do {
            response = try await apiClient.request(
                endpoint: .signIn(email: email, password: password),
                responseType: AuthResponse.self
            )
        } catch APIError.businessError(let code, let message) where code == "EMAIL_NOT_CONFIRMED" {
            // A DEAD END until now. The backend correctly distinguishes "unconfirmed" from
            // "wrong password" — but the sign-in screen only renders `errorMessage`, and there
            // is no resend affordance anywhere on it. A user whose confirmation email expired,
            // went to spam, or was opened on another device was told to confirm their address
            // with no way to obtain a new link, and with the correct password in hand. The
            // account is unreachable.
            //
            // Close the loop here rather than in the view (SignInView is off-limits): the
            // password already verified, so re-sending is safe and is what the user would tap
            // if the button existed. Backend rate limits (`resend:ip:` 5/hr + `resend:email:`)
            // cap it, so a retry loop cannot become a mail-bomb.
            let resent = (try? await resendConfirmation(email: email)) != nil
            throw APIError.businessError(
                code: code,
                message: resent
                    ? "\(message) We've just sent you a fresh confirmation link — check your inbox, then sign in again."
                    : message
            )
        }

        // Store tokens
        saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)
        sessionEpoch += 1   // new session — invalidate any in-flight older-session refresh

        // Update API client
        await apiClient.setAuthToken(response.accessToken)

        return try await fetchCurrentUser()
    }

    /// Sign up with email and password.
    ///
    /// Returns `.needsEmailConfirmation` in the normal case — the account exists but has no
    /// session until the address is confirmed. Only when the Supabase project has email
    /// confirmation disabled does this come back `.signedIn`.
    func signUp(
        email: String, password: String, displayName: String
    ) async throws -> SignUpOutcome {
        let response = try await apiClient.request(
            endpoint: .signUp(email: email, password: password, displayName: displayName),
            responseType: SignUpResponseDTO.self
        )

        // Guard on the tokens, not just the flag: a `confirmationRequired == false` response
        // that somehow arrived without tokens must not be treated as a live session.
        guard !response.confirmationRequired,
              let accessToken = response.accessToken,
              let refreshToken = response.refreshToken else {
            return .needsEmailConfirmation(message: response.message)
        }

        saveTokens(accessToken: accessToken, refreshToken: refreshToken)
        sessionEpoch += 1
        await apiClient.setAuthToken(accessToken)
        return .signedIn(try await fetchCurrentUser())
    }

    /// Re-send the signup confirmation email. Fire-and-forget by design: the backend never
    /// reveals whether the address needs confirming, so there is nothing to branch on.
    /// Change the signed-in user's password AND adopt the credentials the server hands back.
    ///
    /// Lives here rather than in the view because it is a session transition, not a form submit:
    /// the server stamps `users.password_changed_at`, which invalidates every token issued
    /// before that moment — including the one this call was authenticated with. The response
    /// carries replacements minted after the stamp; adopting them is what keeps THIS device
    /// signed in while every other device is correctly evicted.
    ///
    /// The view previously decoded this as `MessageResponse` and discarded the tokens (Swift
    /// ignores unknown keys, so it failed silently), which meant a successful password change
    /// signed the user out on the next request.
    @discardableResult
    func changePassword(currentPassword: String, newPassword: String) async throws -> String {
        let response = try await apiClient.request(
            endpoint: .changePassword(
                currentPassword: currentPassword, newPassword: newPassword
            ),
            responseType: PasswordChangedResponse.self
        )

        // Adopt only if the server actually rotated. An older backend (the app ships separately
        // from Railway) returns just a message; there is nothing to adopt and the pre-existing
        // behaviour — the session dies and the user signs in again — still applies. Reporting a
        // failure for a password change that SUCCEEDED would be worse than that.
        guard let access = response.accessToken, let refresh = response.refreshToken else {
            #if DEBUG
            print("ℹ️ [Auth] change-password returned no replacement tokens (older backend); this session will end")
            #endif
            return response.message
        }

        // Same order as signIn: persist, invalidate any in-flight older-session refresh, then
        // arm the client. Bumping the epoch matters here too — a refresh already in flight was
        // started with the now-dead token and must not overwrite these.
        saveTokens(accessToken: access, refreshToken: refresh)
        sessionEpoch += 1
        await apiClient.setAuthToken(access)
        return response.message
    }

    func resendConfirmation(email: String) async throws {
        _ = try await apiClient.request(
            endpoint: .resendConfirmation(email: email),
            responseType: MessageResponse.self
        )
    }

    /// Sign in with a native provider identity token (Sign in with Apple).
    ///
    /// The token is verified by Supabase against Apple's public keys — the client is not
    /// trusted. `displayName` is passed through because Apple supplies the name only on the
    /// very first authorization.
    func signInWithProvider(
        provider: String, idToken: String, nonce: String?, displayName: String?
    ) async throws -> UserProfile {
        let response = try await apiClient.request(
            endpoint: .oauthSignIn(
                provider: provider, idToken: idToken, nonce: nonce, displayName: displayName
            ),
            responseType: AuthResponse.self
        )
        saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)
        sessionEpoch += 1
        await apiClient.setAuthToken(response.accessToken)
        return try await fetchCurrentUser()
    }

    /// Exchange a Supabase-issued access token for app tokens (web OAuth flow, e.g. Google).
    func exchangeSupabaseSession(accessToken supabaseToken: String) async throws -> UserProfile {
        let response = try await apiClient.request(
            endpoint: .sessionExchange(supabaseAccessToken: supabaseToken),
            responseType: AuthResponse.self
        )
        saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)
        sessionEpoch += 1
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

        // Clear the DURABLE credential FIRST, before the awaited network call.
        //
        // The Keychain write used to sit after `await apiClient.request(.signOut)`. On a dead
        // connection that request hangs for the full 30s timeout, and if the user force-quits
        // in that window — which is exactly what people do when an app looks stuck — the line
        // never runs. Both Keychain items are `AfterFirstUnlockThisDeviceOnly`, so they
        // survive the kill, and the next launch's `restoreAuthState()` finds a stored token
        // and silently signs the "signed-out" user back in. The UI already showed them as
        // signed out before this function was even called.
        //
        // Ordering is safe: the logout request below is best-effort and its outcome changes
        // nothing locally, and the in-memory APIClient token dies with the process anyway.
        // The epoch guard still protects a fast re-sign-in — a newer session will have written
        // its own tokens after this point, and we must not clear those.
        if epoch == sessionEpoch {
            clearToken()
        }

        // Detach the APNs token BEFORE the session ends — the call is authenticated, so it has
        // to run while the token is still armed. Otherwise `device_tokens` keeps mapping this
        // device to the account being signed out and the sweeper keeps pushing its alerts here.
        await PushNotificationManager.shared.unregisterCurrentDevice()

        // Best-effort backend logout. Fired with the token still armed on the APIClient so the
        // server can see who is logging out.
        try? await apiClient.request(endpoint: .signOut)

        // Only disarm if no NEWER session started during the logout round-trip — a
        // quick re-sign-in bumps the epoch and its fresh tokens must survive.
        guard epoch == sessionEpoch else { return }
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
