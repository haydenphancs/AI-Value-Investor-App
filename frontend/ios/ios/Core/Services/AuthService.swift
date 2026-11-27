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
import OSLog
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
    /// The install's lockout exemption for this address — see `DeviceProofStore`. Optional:
    /// a refresh carries none, and an older backend sends none.
    let deviceToken: String?

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case tokenType = "token_type"
        case userId = "user_id"
        case deviceToken = "device_token"
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

/// What confirming an address with its emailed code ended in (`AppState.confirmEmail`).
enum EmailConfirmationOutcome: Sendable {
    /// Confirmed and signed in.
    case signedIn
    /// Confirmed, and the server handed back a session, but the profile read after it failed on
    /// the network. The session is kept and the app keeps retrying (`.restoring`); the code is
    /// spent, so the screen must not ask for it again.
    case confirmedPendingConnection
}

/// Thrown by a sign-in whose credential the server ACCEPTED — tokens stored in the Keychain,
/// client armed — when the `/users/me` read that follows then failed.
///
/// Without it the caller saw only the network error, and the half-finished session stayed
/// behind: the client kept sending the new Bearer while the UI said signed out, and the next
/// heal trigger signed the user in without telling them (IOS-AUTH-09, SOC-03). `AppState` turns
/// it into the `.restoring` path (Keychain kept, client disarmed, restore scheduled) or, when
/// the server refused the token it had just minted, undoes the adoption — then rethrows
/// `underlying`, so screens only ever see the ordinary error.
struct SignedInProfileUnavailable: Error {
    let underlying: Error
    /// `sessionEpoch` right after the adoption: the undo is skipped if anything moved it since.
    let epoch: Int
}

// MARK: - Auth Service

/// Handles authentication and token management
@MainActor
final class AuthService {

    // MARK: - Properties

    private let apiClient: APIClient
    private let keychain: KeychainService

    private static let log = Logger(subsystem: "com.phan.caydex", category: "auth")

    private static let accessTokenKey = "access_token"
    private static let refreshTokenKey = "refresh_token"

    /// Monotonic session counter, bumped on every session transition (sign-in,
    /// sign-up, sign-out). A token-persisting operation (refresh) that started
    /// under an older epoch discards its result, and sign-out's trailing client-token
    /// clear (`finishSignOut`'s backstop) is skipped if a newer session began
    /// mid-logout. This makes sign-out/sign-in
    /// races safe (a stale refresh can't resurrect a signed-out session; a quick
    /// re-sign-in isn't wiped by a late logout clear). @MainActor serializes it.
    private var sessionEpoch: Int = 0

    /// Whether the Keychain holds an access token, known WITHOUT asking it. This class is the
    /// item's only writer (`saveTokens` / `clearToken`), so a mirror set on every write and every
    /// read stays exact, and `hasStoredToken` never makes a main-thread Keychain call. nil until
    /// the launch read lands.
    private var storedTokenPresent: Bool?

    /// Bumped by every Keychain write. An off-main read captures it first and updates the mirror
    /// only if no write overtook it, so a stale read never undoes a sign-in or sign-out.
    private var keychainWrites = 0

    /// The stored REFRESH token, mirrored in memory so sign-out can hand it to `/auth/logout`
    /// (migration 214) without a main-thread Keychain read. Set by every write (`saveTokens`) and by
    /// the off-main read (`loadStoredRefreshToken`), cleared by `clearToken`. nil after a cold
    /// launch until the first refresh or sign-in — the logout then names the session by the
    /// bearer's `sid` alone, which every 214-minted token carries.
    private var refreshTokenMirror: String?

    // MARK: - Initialization

    init(apiClient: APIClient, keychain: KeychainService? = nil) {
        self.apiClient = apiClient
        self.keychain = keychain ?? .shared
    }

    // MARK: - Authentication

    /// Sign in with email and password.
    /// Stores the tokens, arms the API client, then fetches the canonical profile
    /// from `GET /users/me` (the token body does not carry the user object).
    ///
    /// EMAIL_NOT_CONFIRMED is rethrown UNCHANGED. This used to catch it, silently re-send the
    /// confirmation email (`try?`, no log) and append "We've just sent you a fresh confirmation
    /// link" to the backend's own sentence. That printed the same message twice, claimed a send
    /// that GoTrue often skipped, rotated the code in the user's inbox on every attempt — so the
    /// email they were about to use died — and spent the 3-an-hour resend budget before they
    /// tapped anything. `SignInView` now routes the error to the code screen, which has an
    /// explicit "Send a new code".
    func signIn(email: String, password: String) async throws -> UserProfile {
        let response = try await apiClient.request(
            endpoint: .signIn(email: email, password: password),
            responseType: AuthResponse.self
        )

        // Not an Apple session: drop any Apple account link an earlier session left (SOC-04).
        recordAppleIdentity(nil, accountId: response.userId)
        // Store tokens
        saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)
        // A verified password earns this install its per-email lockout exemption.
        DeviceProofStore.remember(response.deviceToken, for: email)
        sessionEpoch += 1   // new session — invalidate any in-flight older-session refresh
        let epoch = sessionEpoch

        // Update API client
        await apiClient.setAuthToken(response.accessToken)

        return try await adoptedSessionProfile(epoch: epoch)
    }

    /// Confirm a new account's address with the code from the "Confirm signup" email, and sign
    /// in with the password the user just typed (`POST /auth/confirm-email`).
    ///
    /// Token adoption is EXACTLY `signIn`'s — persist, bump the epoch, arm the client, then read
    /// the profile — because this is a sign-in: the server answers with the same
    /// `TokenResponse`. No device proof: the server never mints one on this route (the code,
    /// not a verified password, is what got the caller here), so there is nothing to remember.
    ///
    /// `retryCount: 0`: the code is single-use. A lost response is not repeated automatically.
    func confirmEmail(email: String, code: String, password: String) async throws -> UserProfile {
        let response = try await apiClient.request(
            endpoint: .confirmEmail(email: email, code: code, password: password),
            responseType: AuthResponse.self,
            retryCount: 0
        )

        recordAppleIdentity(nil, accountId: response.userId)
        saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)
        sessionEpoch += 1   // new session — invalidate any in-flight older-session refresh
        let epoch = sessionEpoch

        await apiClient.setAuthToken(response.accessToken)

        return try await adoptedSessionProfile(epoch: epoch)
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

        recordAppleIdentity(nil, accountId: response.userId)
        saveTokens(accessToken: accessToken, refreshToken: refreshToken)
        sessionEpoch += 1
        let epoch = sessionEpoch
        await apiClient.setAuthToken(accessToken)
        return .signedIn(try await adoptedSessionProfile(epoch: epoch))
    }

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
            responseType: PasswordChangedResponse.self,
            // retryCount 0. The default of 2 auto-retries any `.serverError`, and the backend
            // still raises bare-string 500s here (a missing users row, a Supabase transport
            // fault) which iOS cannot decode as APIErrorResponse — so they surface as
            // `.serverError` and ONE user tap became THREE requests. The per-user limiter is
            // charged before any work and never refunded, so a brief blip burned 3 of the 5
            // attempts per tap: two taps and the user is locked out of a security-critical
            // action for 15 minutes without a password ever having been checked.
            //
            // Retrying is wrong here on its own terms too — this is a non-idempotent mutation
            // guarded by an attempt limiter. It should be attempted exactly once.
            retryCount: 0
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

    /// Create a FIRST password for a signed-in account that has none (Apple/Google sign-in).
    ///
    /// The `code` is the emailed recovery code from `POST /auth/forgot-password` (its length is
    /// a Supabase setting — 8 digits today — so nothing here assumes one). The session
    /// alone is deliberately not sufficient on the server: without the code, a stolen access
    /// token would be enough to take permanent ownership of the account.
    ///
    /// Token handling is identical to `changePassword` and for the identical reason — setting a
    /// password stamps `password_changed_at`, which evicts every token minted before it,
    /// including the one that made this request. Dropping the replacements here would sign the
    /// user out seconds after telling them it worked.
    @discardableResult
    func setPassword(code: String, newPassword: String) async throws -> String {
        let response = try await apiClient.request(
            endpoint: .setPassword(code: code, newPassword: newPassword),
            responseType: PasswordChangedResponse.self,
            // retryCount 0, same reasoning as changePassword — a non-idempotent mutation behind
            // an attempt limiter that is charged before any work and never refunded. Here there
            // is a second reason: the OTP is single-use, so a retry that reached the handler
            // would consume the code and then fail on the replay.
            retryCount: 0
        )

        guard let access = response.accessToken, let refresh = response.refreshToken else {
            #if DEBUG
            print("ℹ️ [Auth] set-password returned no replacement tokens (older backend); this session will end")
            #endif
            return response.message
        }

        saveTokens(accessToken: access, refreshToken: refresh)
        sessionEpoch += 1
        await apiClient.setAuthToken(access)
        return response.message
    }

    /// Re-send the signup confirmation email (a new code). Only ever on the user's explicit tap
    /// on the code screen. The backend answers the same 200 whether or not the address still
    /// needs confirming — it never reveals which addresses have accounts — so there is nothing
    /// to branch on, and the screen's copy must not claim an email was definitely sent.
    func resendConfirmation(email: String) async throws {
        _ = try await apiClient.request(
            endpoint: .resendConfirmation(email: email),
            responseType: MessageResponse.self
        )
    }

    /// Sign in with a native provider identity token (Sign in with Apple, native Google).
    ///
    /// The token is verified by Supabase against the provider's public keys — the client is not
    /// trusted. `displayName` is passed through because Apple supplies the name only on the
    /// very first authorization — and for Apple it is kept in the Keychain until this request
    /// succeeds (SOC-05, `AppleSignInStore.nameForSignIn`), so a failed first attempt no longer
    /// loses it. `authorizationCode` (Apple) lets the backend keep a revocable token (SOC-04);
    /// `appleUserID` becomes the session's account link for the credential-state check.
    func signInWithProvider(
        provider: String, idToken: String, nonce: String?, displayName: String?,
        authorizationCode: String? = nil, appleUserID: String? = nil
    ) async throws -> UserProfile {
        let isApple = provider == "apple"
        // Both capped at the backend's 64-scalar limit: builds before 1.04 sent a longer
        // provider name as-is and the backend 422'd the whole sign-in.
        let name = isApple
            ? await Self.appleNameForSignIn(received: displayName, appleUserID: appleUserID, keychain: keychain)
            : AppleSignInStore.normalisedName(displayName)
        let response = try await apiClient.request(
            endpoint: .oauthSignIn(
                provider: provider, idToken: idToken, nonce: nonce, displayName: name,
                authorizationCode: isApple ? authorizationCode : nil
            ),
            responseType: AuthResponse.self
        )
        // The server has the name now (it persists it before answering): nothing is pending.
        if isApple { AppleSignInStore.clearPendingName(keychain: keychain) }
        recordAppleIdentity(isApple ? appleUserID : nil, accountId: response.userId)
        saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)
        sessionEpoch += 1
        let epoch = sessionEpoch
        await apiClient.setAuthToken(response.accessToken)
        let profile = try await adoptedSessionProfile(epoch: epoch)
        // The address is only known once the profile is back; the proof is bound to it.
        DeviceProofStore.remember(response.deviceToken, for: profile.email)
        return profile
    }

    /// Exchange a Supabase-issued access token for app tokens (web OAuth flow, e.g. Google).
    func exchangeSupabaseSession(accessToken supabaseToken: String) async throws -> UserProfile {
        let response = try await apiClient.request(
            endpoint: .sessionExchange(supabaseAccessToken: supabaseToken),
            responseType: AuthResponse.self
        )
        recordAppleIdentity(nil, accountId: response.userId)
        saveTokens(accessToken: response.accessToken, refreshToken: response.refreshToken)
        sessionEpoch += 1
        let epoch = sessionEpoch
        await apiClient.setAuthToken(response.accessToken)
        let profile = try await adoptedSessionProfile(epoch: epoch)
        DeviceProofStore.remember(response.deviceToken, for: profile.email)
        return profile
    }

    /// Which Apple ID (if any) the session being adopted came in with — `AppleSignInStore`'s
    /// account link, read by `AppState.checkAppleCredentialState` (SOC-04). EVERY sign-in path
    /// calls this, just before `saveTokens`: an Apple sign-in records its Apple ID, every other
    /// way in clears the link, so a link left by an earlier session can never sign out someone
    /// who came in by email or Google. `clearToken()` drops it with the tokens at every session end.
    private func recordAppleIdentity(_ appleUserID: String?, accountId: String?) {
        guard let appleUserID, !appleUserID.isEmpty, let accountId, !accountId.isEmpty else {
            AppleSignInStore.clearAccountLink(keychain: keychain)
            return
        }
        AppleSignInStore.setAccountLink(
            .init(appleUserID: appleUserID, accountId: accountId), keychain: keychain
        )
    }

    /// The profile read that completes a sign-in whose tokens were just adopted. A failure is
    /// wrapped in `SignedInProfileUnavailable`, so `AppState` knows a session now exists and
    /// can settle it (`.restoring`, or undo) instead of leaving it half signed in.
    private func adoptedSessionProfile(epoch: Int) async throws -> UserProfile {
        do {
            return try await fetchCurrentUser()
        } catch {
            throw SignedInProfileUnavailable(underlying: error, epoch: epoch)
        }
    }

    /// Undo a sign-in whose tokens were adopted but whose profile read was REFUSED (an auth
    /// error on a token the server had just minted, e.g. the account vanished in between).
    /// Clears the session tokens and disarms the client, the local half of sign-out — unless a
    /// newer session transition (a sign-out, another sign-in) moved the epoch since, in which
    /// case that session owns the Keychain and nothing is touched. The device proof is NOT
    /// cleared: a verified password earned it (auth.md §3a). Returns whether it undid anything.
    func discardAdoptedSession(epoch: Int) -> Bool {
        guard epoch == sessionEpoch else { return false }
        sessionEpoch += 1
        self.clearToken()
        _ = apiClient.disarmAuthToken()
        return true
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

    /// What sign-out's network half needs from its synchronous half.
    struct SignOutHandoff: Sendable {
        /// The token the client was armed with, already disarmed. nil when it held none.
        let endedToken: String?
        /// `sessionEpoch` right after the bump: the backstop's guard.
        let epoch: Int
        /// The ended session's REFRESH token (from the in-memory mirror, taken just before the
        /// Keychain clear), so `POST /auth/logout` can name exactly this session (migration 214).
        /// Defaulted so a caller that has none (a test double, a mutation scan) can omit it.
        var endedRefreshToken: String? = nil
        /// The server already ended every session ("Sign out of all devices"): the detach and the
        /// logout would each be a guaranteed 401, and the server has detached every device.
        var endedByServer: Bool = false
    }

    /// Set by `noteSessionsEndedByServer()` and consumed by the very next `beginSignOut()`.
    private var nextSignOutEndedByServer = false

    /// Sign out, part 1 — everything LOCAL, synchronously: end the session epoch, clear the
    /// Keychain, disarm the client. `AppState.signOut()` calls this BEFORE it changes the
    /// identity, then hands the result to `finishSignOut(_:)` in a Task.
    ///
    /// Synchronous on purpose (2026-10-13). `AppState.signOut()` bumps the identity generation and
    /// shows the signed-out UI at once, so every screen reloads. Sign-out used to begin inside a
    /// Task and disarm the client only after the device detach and logout round trips, so those
    /// reloads were built with the ENDED account's token — its reports list landed in the
    /// signed-out UI — and an ACCESS token stays valid server-side until it expires (≤ 60 min; the
    /// logout revokes the session's refresh tokens, not the access token). With no suspension
    /// point in here, the client is disarmed before `AppState` has changed anything.
    func beginSignOut() -> SignOutHandoff {
        sessionEpoch += 1

        // Clear the DURABLE credential before any network call.
        //
        // The Keychain write used to sit after `await apiClient.request(.signOut)`. On a dead
        // connection that request hangs for the full 30s timeout, and if the user force-quits
        // in that window — which is exactly what people do when an app looks stuck — the line
        // never runs. Both Keychain items are `AfterFirstUnlockThisDeviceOnly`, so they
        // survive the kill, and the next launch's `restoreAuthState()` finds a stored token
        // and silently signs the "signed-out" user back in.
        //
        // No epoch guard: nothing in this function suspends, so no newer session can have
        // written its own tokens yet.
        //
        // The refresh token is taken FIRST, from the in-memory mirror (never a main-thread
        // Keychain read — `test_ios_launch_no_main_thread_keychain.py`): `/auth/logout` uses it
        // to end THIS session server-side (migration 214), and `clearToken()` below drops it.
        let endedRefreshToken = refreshTokenMirror
        clearToken()

        // Read and cleared as one swap under the client's lock, without an actor hop, so no
        // request can be built with the ended token after this line.
        let endedToken = apiClient.disarmAuthToken()
        let endedByServer = nextSignOutEndedByServer
        nextSignOutEndedByServer = false
        return SignOutHandoff(endedToken: endedToken, epoch: sessionEpoch, endedRefreshToken: endedRefreshToken, endedByServer: endedByServer)
    }

    /// "Sign out of all devices": the server ends every session of the account, this one included
    /// (`POST /auth/sign-out-all`). Sent through the ARMED client — the session is still live —
    /// so an expired access token is refreshed first like any request. On success the caller runs
    /// the local sign-out at once (`AppState.signOutEverywhere()`); on failure nothing changed.
    func signOutAllDevices() async throws {
        try await apiClient.request(endpoint: .signOutAllDevices)
    }

    /// Marks the next `beginSignOut()` as ending a session the SERVER already ended. Called
    /// synchronously right before `AppState.signOut()`, in the same main-actor turn, so no other
    /// sign-out can consume it.
    func noteSessionsEndedByServer() {
        nextSignOutEndedByServer = true
    }

    /// Re-enter a credential before a permanent action (account deletion, SEC-11) and get the
    /// five-minute proof the server asks for. Never touches the session or the Keychain.
    func reauthenticate(_ body: ReauthenticateRequestBody) async throws -> String {
        try await apiClient.request(
            endpoint: .reauthenticate(body),
            responseType: ReauthenticateResponse.self
        ).reauthToken
    }

    /// Sign out, part 2 — the network calls, sent with the ended token passed explicitly, after
    /// `beginSignOut()` has already ended the session locally. Best-effort throughout.
    func finishSignOut(_ handoff: SignOutHandoff) async {
        // GoogleSignIn keeps its own signed-in state in the Keychain; the ended session must not
        // leave it behind (SOC-04). Local and synchronous, so first — before the two round trips
        // below, which a kill can cut short. A no-op when nobody signed in with Google here.
        SocialSignInService.endGoogleSession()

        if handoff.endedByServer {
            // Sign out of all devices: the server revoked every session and detached every device
            // already. Both calls below would be a 401 for the token it just evicted.
            Self.log.notice("sign-out: the server already ended every session — device detach and backend logout skipped")
        } else if let endedToken = handoff.endedToken {
            // Detach the APNs token with the ended session's credential — the call is
            // authenticated. Otherwise `device_tokens` keeps mapping this device to the account
            // being signed out and the sweeper keeps pushing its alerts here.
            await PushNotificationManager.shared.unregisterCurrentDevice(authenticatedAs: endedToken)

            // Best-effort backend logout, with the same credential so the server can see who is
            // logging out, and the session's refresh token so it can revoke exactly this session
            // (migration 214; the bearer's `sid` names it too). Passed explicitly: re-arming the
            // shared client to send these two would reopen the window `beginSignOut()` closed.
            do {
                try await apiClient.requestAuthenticated(as: endedToken, endpoint: .signOut(refreshToken: handoff.endedRefreshToken))
            } catch {
                Self.log.warning("sign-out: POST /auth/logout failed (best-effort, the session is already ended locally): \(AppError.from(error).analyticsCode, privacy: .public)")
            }
        } else {
            // No armed credential: a sign-out while `.restoring` after a transient failure (the
            // client was disarmed and the stored token never re-validated) or a repeated
            // sign-out. Nothing can authenticate the detach or the logout; the device binding
            // heals on the next sign-in (`clearLocalRegistrationForEndedSession` re-stashes the
            // APNs token).
            Self.log.notice("sign-out: the client held no credential — device detach and backend logout skipped")
        }

        // Backstop, not the disarm: a token refresh already in flight when sign-out began can
        // land after the disarm and re-arm the ended session. Only if no NEWER session started
        // meanwhile — a quick re-sign-in bumps the epoch and its fresh tokens must survive.
        guard handoff.epoch == sessionEpoch else { return }
        await apiClient.setAuthToken(nil)
    }

    /// Refresh the access token
    func refreshToken() async throws {
        // Captured BEFORE the read: it runs off the main thread and suspends, and a sign-in or
        // sign-out may land meanwhile.
        let epoch = sessionEpoch
        guard let refreshToken = await loadStoredRefreshToken() else {
            throw APIError.unauthorized
        }
        // A session that changed during the read owns the Keychain now. Sending what the read
        // returned could spend a NEWER session's refresh token (single-use since migration 214:
        // the server retires it and a second use outside a short grace window ends the session),
        // leaving that session holding a dead one.
        guard epoch == sessionEpoch else { return }

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

    /// The stored access token, read OFF the main thread.
    ///
    /// There is no synchronous getter any more. `SecItemCopyMatching` is a round trip to
    /// securityd, and this class is `@MainActor`, so a synchronous read blocks the main thread
    /// for as long as securityd takes. Cold launch made five of them before Home's first frame,
    /// and a stalled securityd would freeze the splash the way a stalled `stat()` did (Sentry
    /// CAYDEX-APPLE-IOS-S). Also refreshes `storedTokenPresent`.
    func loadStoredToken() async -> String? {
        let writes = keychainWrites
        let token = await keychainValueOffMain(forKey: Self.accessTokenKey)
        if writes == keychainWrites {
            storedTokenPresent = token != nil
        }
        return token
    }

    /// The stored refresh token, read OFF the main thread (see `loadStoredToken`).
    private func loadStoredRefreshToken() async -> String? {
        let writes = keychainWrites
        let token = await keychainValueOffMain(forKey: Self.refreshTokenKey)
        // Only if no write overtook the read: a stale value must never replace a newer session's.
        if writes == keychainWrites {
            refreshTokenMirror = token
        }
        return token
    }

    /// Every Keychain read in this class: one `get`, on a detached task, so the main actor never
    /// waits on securityd. `KeychainService` is `nonisolated` and `Sendable`, so the closure
    /// touches no main-actor state.
    private func keychainValueOffMain(forKey key: String) async -> String? {
        let keychain = self.keychain
        return await Task.detached(priority: .userInitiated) {
            keychain.get(key)
        }.value
    }

    /// The name to send with an Apple sign-in (SOC-05), resolved OFF the main actor: it is a
    /// Keychain read (and, when Apple sent a name, a write). Kept below `keychainValueOffMain`
    /// on purpose: that session-token reader stays the file's first detached read.
    private static func appleNameForSignIn(
        received: String?, appleUserID: String?, keychain: KeychainService
    ) async -> String? {
        await Task.detached(priority: .userInitiated) {
            AppleSignInStore.nameForSignIn(received: received, appleUserID: appleUserID, keychain: keychain)
        }.value
    }

    /// Whether the Keychain holds an access token: the `storedTokenPresent` mirror, never a
    /// Keychain read. Unknown (before the launch read lands) answers false. Nothing is armed
    /// before that read, and the launch restore runs right after it, so no caller has a
    /// credential to heal yet.
    var hasStoredToken: Bool {
        storedTokenPresent ?? false
    }

    // MARK: - Fresh install

    /// Set in the app's own UserDefaults on the first launch that runs this check. UserDefaults
    /// go with the app when it is deleted; Keychain items do not.
    private static let installMarkerKey = "auth.install-marker.v1"

    /// Delete session tokens an EARLIER install of the app left in the Keychain (SEC-8).
    ///
    /// Keychain items outlive the app. A user who deleted Caydex believing that signed them
    /// out, then handed the phone on, had the next install restore their session — portfolio,
    /// chats, credits — for as long as the refresh token lived. Called first thing in the
    /// launch Task, before the one launch read.
    ///
    /// ONLY the two session tokens, through `clearToken()`. Never `KeychainService.deleteAll`,
    /// never `DeviceProofStore` (the lockout exemption is meant to survive, auth.md §3a) and
    /// never `GuestIdentity` (the install id is meant to survive a reinstall). The widget token
    /// lives in the App Group container, which iOS deletes with the app.
    ///
    /// An UPDATE must not sign anyone out, and an update from a build that predates this marker
    /// also arrives without it. So a missing marker counts as a fresh install only when the App
    /// Group holds no trace of an earlier launch either: every build since 2026-08-28 publishes
    /// the widget's base URL there on every launch, and that container is deleted with the app.
    /// If the App Group cannot be opened at all, this cannot tell, and keeps the session —
    /// signing every user out on an update is the worse failure.
    func purgeSessionLeftByAnEarlierInstall() async {
        let markerKey = Self.installMarkerKey
        let decision = await Task.detached(priority: .userInitiated) { () -> InstallKind in
            if UserDefaults.standard.bool(forKey: markerKey) { return .seenBefore }
            guard let group = WidgetSharedDefaults.store else { return .unknown }
            return group.string(forKey: WidgetAPIConfig.baseURLOverrideKey) == nil ? .fresh : .updated
        }.value

        switch decision {
        case .seenBefore:
            return
        case .fresh:
            Self.log.notice("fresh install: clearing any session tokens an earlier install left in the Keychain")
            self.clearToken()
        case .updated:
            Self.log.info("install marker missing but this install has run before (an update) — session kept")
        case .unknown:
            Self.log.warning("install marker missing and the App Group is unreadable — cannot tell a fresh install from an update; session kept")
        }
        // After the clear, so a kill between the two re-runs the check rather than skipping it.
        UserDefaults.standard.set(true, forKey: Self.installMarkerKey)
    }

    nonisolated private enum InstallKind: Sendable { case seenBefore, fresh, updated, unknown }

    /// Save tokens to keychain
    private func saveTokens(accessToken: String, refreshToken: String) {
        keychain.set(accessToken, forKey: Self.accessTokenKey)
        keychain.set(refreshToken, forKey: Self.refreshTokenKey)
        keychainWrites &+= 1
        storedTokenPresent = true
        refreshTokenMirror = refreshToken
    }

    /// Clear stored tokens
    func clearToken() {
        keychain.delete(Self.accessTokenKey)
        keychain.delete(Self.refreshTokenKey)
        keychainWrites &+= 1
        storedTokenPresent = false
        refreshTokenMirror = nil
        // The Apple account link describes THIS session, so it ends with it (SOC-04) — on every
        // path that ends one: sign-out, a dead credential, an undone adoption, the fresh-install
        // purge (a link an earlier install left would otherwise outlive its session).
        AppleSignInStore.clearAccountLink(keychain: keychain)
    }
}

// MARK: - Keychain Service

/// Simple Keychain wrapper for secure token storage
///
/// `nonisolated`: the target defaults to MainActor isolation, but `DeviceProofStore` reads and
/// writes through this from `APIClient`'s actor. It holds only an immutable service name and the
/// Keychain APIs are thread-safe, so it is checked `Sendable` rather than `@unchecked`.
nonisolated final class KeychainService: Sendable {

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
