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
    /// The provider rejected the handshake and told us why. Carried rather than collapsed into
    /// `.malformedCallback`, because "provider disabled", "redirect URL not allow-listed" and
    /// "user declined consent" are three different problems with three different fixes — and
    /// all three used to render as the same shrug.
    case provider(String)

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
        case .provider(let reason):
            return "That sign-in didn't complete: \(reason)"
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
        // Consume the nonce on EVERY outcome, not just success. It was cleared only on the
        // success path, so a cancelled or failed authorization left the previous attempt's
        // nonce sitting in `currentAppleNonce` — and `makeAppleRequest()` is called from
        // SwiftUI's `onRequest` builder, which is not guaranteed to run exactly once per
        // presentation. A one-time value that outlives its one time is not a nonce.
        defer { currentAppleNonce = nil }

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
    /// Google's client id for the **iOS** OAuth client, from `Info.plist` → `GIDClientID`.
    ///
    /// Empty or absent means the native SDK path is not configured yet, and we fall back to
    /// the web flow. That is what lets this file compile and ship before the Google Cloud
    /// client exists.
    static var googleClientID: String? {
        let raw = Bundle.main.object(forInfoDictionaryKey: "GIDClientID") as? String
        guard let raw, !raw.trimmingCharacters(in: .whitespaces).isEmpty,
              !raw.hasPrefix("REPLACE_ME") else { return nil }
        return raw
    }

    /// Native Google sign-in, falling back to the web flow when the SDK or the client id is
    /// absent.
    ///
    /// WHY THE NATIVE PATH EXISTS — the web flow dead-ends for passkey-only Google accounts.
    /// Routing through Supabase's `/auth/v1/authorize` makes the request on Google's **Web**
    /// OAuth client, and Google applies browser-grade risk checks to a Web client opened
    /// inside a mobile web view: it reaches its credential step, finds no usable credential
    /// (Google's passkeys live in Google Password Manager, which iOS cannot read unless
    /// Chrome is the AutoFill provider, so iCloud Keychain offers no `google.com` passkey),
    /// and the only WebAuthn transport left is hybrid — a QR code asking the user to scan
    /// with a second device. On the phone that is displaying it. iOS also cannot persistently
    /// link a hybrid device, so it is a fresh QR every single time.
    ///
    /// No query parameter fixes that; `prompt=select_account` and non-ephemeral sessions were
    /// both tried and only change which chooser appears, not whether Google demands a
    /// credential. The fix is the CLIENT TYPE: with an iOS client id the request is a native
    /// app flow, which does not escalate to hybrid-only.
    func signInWithGoogle() async throws -> SocialSignInResult {
        #if canImport(GoogleSignIn)
        if let clientID = Self.googleClientID {
            return try await signInWithGoogleNative(clientID: clientID)
        }
        #endif
        return try await signInWithGoogleWeb()
    }

    /// The original web flow. Retained as the fallback, and as the only path until the
    /// GoogleSignIn package and `GIDClientID` are both present.
    private func signInWithGoogleWeb() async throws -> SocialSignInResult {
        guard let supabaseURL = APIConfig.supabaseURL, !supabaseURL.isEmpty else {
            throw SocialSignInError.notConfigured("Supabase URL missing")
        }

        let callbackScheme = APIConfig.oauthCallbackScheme
        var components = URLComponents(string: "\(supabaseURL)/auth/v1/authorize")
        components?.queryItems = [
            URLQueryItem(name: "provider", value: "google"),
            URLQueryItem(name: "redirect_to", value: "\(callbackScheme)://auth-callback"),
            // Ask Google for the account chooser EXPLICITLY, instead of getting it as a
            // side effect of throwing away the session (see the ephemeral note below).
            URLQueryItem(name: "prompt", value: "select_account"),
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
            // INTERIM — remove when the GoogleSignIn SDK lands (it uses this same class via
            // AppAuth, simply without opting into ephemeral).
            //
            // This was `true`, to stop the picker silently reusing whichever Google account
            // Safari was signed into. That goal is real, but ephemeral bought it by destroying
            // the cookie jar — so EVERY sign-in became a cold, fully unauthenticated one.
            // Google then always reached its credential step, and on an iPhone with no
            // `google.com` passkey in iCloud Keychain (Google's passkeys usually live in Google
            // Password Manager, which iOS can't read unless Chrome is the AutoFill provider)
            // the only WebAuthn transport left is hybrid — a QR code asking you to scan with a
            // second device you are not holding. iOS also can't persistently link a hybrid
            // device, so it is a fresh QR every single time.
            //
            // To be precise about cause: ephemeral does NOT hide passkeys — those live in a
            // system credential store, not "browsing data", and ASWebAuthenticationSession has
            // full WebAuthn support. What ephemeral does is guarantee the user always reaches
            // the screen that offers the QR. `prompt=select_account` above gets the account
            // chooser honestly, so we can keep the session and skip that whole escalation.
            session.prefersEphemeralWebBrowserSession = false
            self.webSession = session
            session.start()
        }

        webSession = nil
        return .supabaseSession(accessToken: try Self.accessToken(from: callbackURL))
    }

    /// Supabase returns tokens in the URL **fragment**, not the query string.
    private static func accessToken(from url: URL) throws -> String {
        let components = URLComponents(url: url, resolvingAgainstBaseURL: false)

        /// Split `a=1&b=2` into a dictionary, percent-decoding values.
        func pairs(_ raw: String?) -> [String: String] {
            guard let raw, !raw.isEmpty else { return [:] }
            var out: [String: String] = [:]
            for pair in raw.split(separator: "&") {
                let parts = pair.split(separator: "=", maxSplits: 1)
                guard parts.count == 2 else { continue }
                out[String(parts[0])] = String(parts[1])
                    .replacingOccurrences(of: "+", with: " ")
                    .removingPercentEncoding ?? String(parts[1])
            }
            return out
        }

        // Read BOTH halves of the URL. The implicit flow returns tokens in the fragment, but
        // OAuth errors can arrive in either — and a PKCE-flow project returns `?code=` in the
        // query with no fragment at all. Fragment-only parsing meant a fragment-less callback
        // was indistinguishable from a malformed one.
        let fragment = pairs(components?.fragment)
        let query = pairs(components?.query)

        // Surface the provider's own reason instead of discarding it. This branch used to be
        // unreachable: any failure fell through to `.malformedCallback` → "That sign-in didn't
        // complete. Please try again." So a disabled provider, a redirect URL that isn't
        // allow-listed, or the user declining consent all looked identical, and the one piece
        // of information that would have explained it was thrown away.
        if let description = fragment["error_description"] ?? query["error_description"] {
            throw SocialSignInError.provider(description)
        }
        if let code = fragment["error"] ?? query["error"] {
            throw SocialSignInError.provider(code)
        }

        guard let token = fragment["access_token"], !token.isEmpty else {
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

// MARK: - Native Google (GoogleSignIn SDK)

#if canImport(GoogleSignIn)
import GoogleSignIn

extension SocialSignInService {
    /// Present Google's native sign-in and return the resulting **ID token**.
    ///
    /// The result is `.identityToken`, the same case Sign in with Apple produces, so it lands
    /// on the existing `POST /auth/oauth` path in `AppState` — which already accepts
    /// `provider: "google"` and hands the token to Supabase's `sign_in_with_id_token`. No
    /// backend change, and the web flow's `/auth/session-exchange` round trip disappears.
    ///
    /// No nonce is sent. Supabase must therefore have **Skip nonce check** enabled on the
    /// Google provider — the SDK does not expose the raw nonce needed to verify the pairing,
    /// which is why Google's own documented Supabase integration requires that setting. Apple
    /// is unaffected and keeps its nonce binding.
    @MainActor
    fileprivate func signInWithGoogleNative(clientID: String) async throws -> SocialSignInResult {
        guard let presenter = Self.topViewController() else {
            throw SocialSignInError.notConfigured("no view controller to present from")
        }

        GIDSignIn.sharedInstance.configuration = GIDConfiguration(clientID: clientID)

        let result: GIDSignInResult
        do {
            result = try await GIDSignIn.sharedInstance.signIn(withPresenting: presenter)
        } catch {
            // `.canceled` must surface as our own cancelled case, or the caller shows an
            // error toast for a user who simply tapped the X.
            if (error as NSError).code == GIDSignInError.canceled.rawValue {
                throw SocialSignInError.cancelled
            }
            throw SocialSignInError.provider(error.localizedDescription)
        }

        guard let idToken = result.user.idToken?.tokenString, !idToken.isEmpty else {
            throw SocialSignInError.missingIdentityToken
        }

        return .identityToken(
            provider: "google",
            token: idToken,
            nonce: nil,
            displayName: result.user.profile?.name
        )
    }

    /// Topmost presented controller, so the sheet is not attached to something already
    /// covered (the sign-in screen is itself frequently presented modally).
    private static func topViewController() -> UIViewController? {
        let root = UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.keyWindow }
            .first?.rootViewController
        var top = root
        while let presented = top?.presentedViewController {
            top = presented
        }
        return top
    }
}
#endif

// MARK: - Presentation anchor

extension SocialSignInService: ASWebAuthenticationPresentationContextProviding {
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes
            .compactMap { ($0 as? UIWindowScene)?.keyWindow }
            .first ?? ASPresentationAnchor()
    }
}
