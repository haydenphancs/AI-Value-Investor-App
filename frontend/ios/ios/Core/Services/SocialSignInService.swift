//
//  SocialSignInService.swift
//  ios
//
//  Sign in with Apple (native) and Google (web redirect through Supabase).
//
//  Two different mechanisms on purpose:
//
//  * APPLE uses the native `ASAuthorizationAppleIDProvider`. It returns an OpenID identity
//    token that Supabase verifies against Apple's public keys, so nothing here is trusted on
//    the client's word. Native is also what Apple expects to see for its own button.
//
//  * GOOGLE uses `ASWebAuthenticationSession` against Supabase's OAuth endpoint. The
//    alternative — Google's own SDK — would give a native sheet but adds a third-party
//    dependency, an OAuth client-ID in the bundle, and its own privacy manifest to reconcile.
//    The web flow needs neither and reuses one code path for any future Supabase provider.
//
//  App Review 4.8: offering Google OBLIGES us to offer Sign in with Apple as well, which is
//  why they ship together.
//

import AuthenticationServices
import CryptoKit
import Foundation

enum SocialSignInError: LocalizedError {
    case cancelled
    case missingIdentityToken
    case malformedCallback
    case notConfigured(String)

    var errorDescription: String? {
        switch self {
        case .cancelled:
            return nil   // user tapped cancel; never surfaced as an error
        case .missingIdentityToken:
            return "Apple didn't return a sign-in token. Please try again."
        case .malformedCallback:
            return "That sign-in didn't complete. Please try again."
        case .notConfigured(let what):
            return "Sign-in isn't configured yet (\(what))."
        }
    }
}

/// Result of a provider handshake, ready to hand to the backend.
enum SocialSignInResult: Sendable {
    /// A provider identity token (Apple) → POST /auth/oauth
    case identityToken(provider: String, token: String, nonce: String?, displayName: String?)
    /// A Supabase access token (web flow) → POST /auth/session-exchange
    case supabaseSession(accessToken: String)
}

@MainActor
final class SocialSignInService: NSObject {
    static let shared = SocialSignInService()

    /// Kept alive for the duration of a web session; ASWebAuthenticationSession is
    /// deallocated (and silently cancelled) if nothing holds it.
    private var webSession: ASWebAuthenticationSession?

    // MARK: - Apple

    /// Raw nonce for the in-flight Apple request. Apple receives its SHA-256 hash; Supabase
    /// needs the raw value to verify the pairing, which is what stops a captured token being
    /// replayed.
    private var currentAppleNonce: String?

    func makeAppleRequest() -> ASAuthorizationAppleIDRequest {
        let nonce = Self.randomNonceString()
        currentAppleNonce = nonce

        let request = ASAuthorizationAppleIDProvider().createRequest()
        request.requestedScopes = [.fullName, .email]
        request.nonce = Self.sha256(nonce)
        return request
    }

    /// Convert a completed Apple authorization into something the backend can verify.
    func handleAppleCompletion(
        _ result: Result<ASAuthorization, Error>
    ) throws -> SocialSignInResult {
        switch result {
        case .failure(let error):
            if let authError = error as? ASAuthorizationError, authError.code == .canceled {
                throw SocialSignInError.cancelled
            }
            throw error

        case .success(let authorization):
            guard
                let credential = authorization.credential as? ASAuthorizationAppleIDCredential,
                let tokenData = credential.identityToken,
                let identityToken = String(data: tokenData, encoding: .utf8)
            else {
                throw SocialSignInError.missingIdentityToken
            }

            // Apple returns the name ONLY on the first authorization for this app. If we
            // don't capture it here it is gone permanently, so pass it through for the
            // backend to persist (it never overwrites an existing name).
            let displayName = [
                credential.fullName?.givenName,
                credential.fullName?.familyName,
            ]
                .compactMap { $0 }
                .joined(separator: " ")
                .trimmingCharacters(in: .whitespaces)

            let nonce = currentAppleNonce
            currentAppleNonce = nil

            return .identityToken(
                provider: "apple",
                token: identityToken,
                nonce: nonce,
                displayName: displayName.isEmpty ? nil : displayName
            )
        }
    }

    // MARK: - Google (web redirect via Supabase)

    /// Present Google's consent screen and return the Supabase session it ends with.
    func signInWithGoogle() async throws -> SocialSignInResult {
        guard let supabaseURL = APIConfig.supabaseURL, !supabaseURL.isEmpty else {
            throw SocialSignInError.notConfigured("Supabase URL missing")
        }

        let callbackScheme = APIConfig.oauthCallbackScheme
        var components = URLComponents(string: "\(supabaseURL)/auth/v1/authorize")
        components?.queryItems = [
            URLQueryItem(name: "provider", value: "google"),
            URLQueryItem(name: "redirect_to", value: "\(callbackScheme)://auth-callback"),
        ]
        guard let authURL = components?.url else {
            throw SocialSignInError.notConfigured("could not build the authorize URL")
        }

        let callbackURL: URL = try await withCheckedThrowingContinuation { continuation in
            let session = ASWebAuthenticationSession(
                url: authURL,
                callbackURLScheme: callbackScheme
            ) { url, error in
                if let error {
                    let cancelled = (error as? ASWebAuthenticationSessionError)?.code
                        == .canceledLogin
                    continuation.resume(
                        throwing: cancelled ? SocialSignInError.cancelled : error
                    )
                    return
                }
                guard let url else {
                    continuation.resume(throwing: SocialSignInError.malformedCallback)
                    return
                }
                continuation.resume(returning: url)
            }
            session.presentationContextProvider = self
            // Don't reuse Safari's cookies: otherwise the picker silently reuses whichever
            // Google account the browser is already signed into, with no way to switch.
            session.prefersEphemeralWebBrowserSession = true
            self.webSession = session
            session.start()
        }

        webSession = nil
        return .supabaseSession(accessToken: try Self.accessToken(from: callbackURL))
    }

    /// Supabase returns tokens in the URL **fragment**, not the query string.
    private static func accessToken(from url: URL) throws -> String {
        guard let fragment = URLComponents(url: url, resolvingAgainstBaseURL: false)?.fragment
        else {
            throw SocialSignInError.malformedCallback
        }
        // Parse the fragment as query pairs.
        var parsed: [String: String] = [:]
        for pair in fragment.split(separator: "&") {
            let parts = pair.split(separator: "=", maxSplits: 1)
            guard parts.count == 2 else { continue }
            parsed[String(parts[0])] = String(parts[1])
                .replacingOccurrences(of: "+", with: " ")
                .removingPercentEncoding ?? String(parts[1])
        }
        guard let token = parsed["access_token"], !token.isEmpty else {
            throw SocialSignInError.malformedCallback
        }
        return token
    }

    // MARK: - Nonce helpers

    /// Cryptographically random nonce. `SecRandomCopyBytes` rather than `Int.random` —
    /// this value is a replay defence, so it has to come from a CSPRNG.
    private static func randomNonceString(length: Int = 32) -> String {
        var bytes = [UInt8](repeating: 0, count: length)
        let status = SecRandomCopyBytes(kSecRandomDefault, length, &bytes)
        if status != errSecSuccess {
            // Fall back to UUIDs rather than a weak RNG. Still unpredictable enough to be a
            // usable nonce, and this branch should never be reached.
            return UUID().uuidString + UUID().uuidString
        }
        let charset = Array("0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-._")
        return String(bytes.map { charset[Int($0) % charset.count] })
    }

    private static func sha256(_ input: String) -> String {
        SHA256.hash(data: Data(input.utf8))
            .map { String(format: "%02x", $0) }
            .joined()
    }
}

// MARK: - Presentation anchor

extension SocialSignInService: ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.keyWindow }
            .first ?? ASPresentationAnchor()
    }
}
