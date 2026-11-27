//
//  ConfirmIdentityViewModel.swift
//  ios
//
//  "Confirm it's you" before deleting the account (SEC-11, 2026-11-26).
//
//  The user re-enters a credential — their password, or Sign in with Apple / Google — and the
//  server (`POST /auth/reauthenticate`) returns a five-minute proof that `DELETE /users/me` reads
//  from `X-Caydex-Reauth`. Nothing here touches the session: the provider handshake's token goes
//  to `/auth/reauthenticate`, never to `/auth/oauth`, so no tokens are minted or saved.
//

import AuthenticationServices
import Combine
import Foundation
import os

@MainActor
final class ConfirmIdentityViewModel: ObservableObject {
    @Published var password = ""
    @Published var isWorking = false
    @Published var errorMessage: String?

    private let authService: AuthService?

    private static let log = Logger(subsystem: "com.phan.caydex", category: "auth")

    init(authService: AuthService?) {
        self.authService = authService
    }

    var canSubmitPassword: Bool { !password.isEmpty && !isWorking }

    /// The proof, or nil when it failed (`errorMessage` says why) or the user backed out.
    func confirmWithPassword() async -> String? {
        let entered = password
        let proof = await confirm(ReauthenticateRequestBody(password: entered), provider: "password")
        // The password leaves memory either way; a wrong one is retyped.
        password = ""
        return proof
    }

    func confirmWithApple(_ result: Result<ASAuthorization, Error>) async -> String? {
        do {
            let handshake = try SocialSignInService.shared.handleAppleCompletion(result)
            return await confirm(Self.body(for: handshake), provider: "apple")
        } catch SocialSignInError.cancelled {
            return nil
        } catch {
            SocialSignInService.logFailure(error, provider: "apple")
            errorMessage = AppError.from(error).message
            return nil
        }
    }

    func confirmWithGoogle() async -> String? {
        guard !isWorking else { return nil }
        // Set BEFORE the await: the handshake presents Google's whole flow, and a second tap
        // meanwhile would start a second one.
        isWorking = true
        let handshake: SocialSignInResult
        do {
            handshake = try await SocialSignInService.shared.signInWithGoogle()
        } catch SocialSignInError.cancelled {
            isWorking = false
            return nil
        } catch {
            isWorking = false
            SocialSignInService.logFailure(error, provider: "google")
            errorMessage = AppError.from(error).message
            return nil
        }
        isWorking = false
        return await confirm(Self.body(for: handshake), provider: "google")
    }

    /// The request body for a provider handshake: an identity token (Apple, native Google) or the
    /// Supabase session the web Google flow ends with.
    nonisolated static func body(for handshake: SocialSignInResult) -> ReauthenticateRequestBody {
        switch handshake {
        // The token proves identity. Apple's authorization code rides along so the server can
        // store the grant that the deletion right after this revokes (SOC-04): an account last
        // signed in on 1.0–1.03 never sent one. displayName and appleUserID are sign-in only.
        case .identityToken(let provider, let token, let nonce, _, let authorizationCode, _):
            return ReauthenticateRequestBody(
                provider: provider, idToken: token, nonce: nonce,
                authorizationCode: provider == "apple" ? authorizationCode : nil
            )
        case .supabaseSession(let accessToken):
            return ReauthenticateRequestBody(supabaseAccessToken: accessToken)
        }
    }

    private func confirm(_ body: ReauthenticateRequestBody, provider: String) async -> String? {
        guard let authService else {
            errorMessage = AppError.from(APIError.authRequired).message
            return nil
        }
        guard !isWorking else { return nil }
        isWorking = true
        defer { isWorking = false }
        errorMessage = nil
        do {
            return try await authService.reauthenticate(body)
        } catch {
            // APIClient wraps cancellation into a network error; Task.isCancelled is the signal.
            guard !Task.isCancelled else { return nil }
            let appError = AppError.from(error)
            Self.log.warning("reauthenticate (\(provider, privacy: .public)) failed: \(appError.analyticsCode, privacy: .public)")
            errorMessage = appError.message
            return nil
        }
    }
}

#if DEBUG
extension ConfirmIdentityViewModel {
    /// A sheet that just failed, for previews.
    static var mockError: ConfirmIdentityViewModel {
        let vm = ConfirmIdentityViewModel(authService: nil)
        vm.errorMessage = "That password isn't right. Please try again."
        return vm
    }
}
#endif
