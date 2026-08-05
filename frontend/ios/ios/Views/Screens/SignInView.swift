//
//  SignInView.swift
//  ios
//
//  Minimal email/password sign-in + sign-up gating screen.
//  Without this, RootView falls through to the main app as a guest
//  user (id 00000000-...) and every authed call quietly fails.
//

import SwiftUI
import AuthenticationServices

struct SignInView: View {
    @Environment(AppState.self) private var appState

    @State private var mode: Mode = .signIn
    @State private var email: String = ""
    @State private var password: String = ""
    @State private var displayName: String = ""
    @State private var errorMessage: String?
    @State private var isSubmitting = false
    @State private var showForgotPassword = false
    /// Set after a signup that needs email confirmation — the view then shows a
    /// terminal "check your inbox" state instead of pretending the user is signed in.
    @State private var pendingConfirmationMessage: String?
    @State private var didResendConfirmation = false

    enum Mode { case signIn, signUp }

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    CaydexLogoMark(size: 80)
                        .padding(.top, 40)

                    Text(mode == .signIn ? "Welcome back" : "Create account")
                        .font(AppTypography.titleLarge)
                        .foregroundColor(AppColors.textPrimary)

                    Text(mode == .signIn
                         ? "Sign in to access your research and credits."
                         : "New accounts start with 50 free credits.")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                        .padding(.bottom, 8)

                    if mode == .signUp {
                        labeled("Display name") {
                            TextField("", text: $displayName)
                                .textContentType(.name)
                                .autocapitalization(.words)
                        }
                    }

                    labeled("Email") {
                        TextField("", text: $email)
                            // `.username`, NOT `.emailAddress`, even though the value IS an
                            // email. `.emailAddress` marks a generic email field, so iOS offers
                            // addresses from the contact card instead of the domain-scoped saved
                            // credential — meaning saved-password AutoFill and Automatic Strong
                            // Passwords both degrade. It would also silently defeat passkey
                            // AutoFill later: Apple ties `performAutoFillAssistedRequests()`
                            // specifically to a field whose content type is `username`.
                            //
                            // The documented pattern for email-as-username is exactly this pair:
                            // `.username` for the credential binding, `.emailAddress` keyboard
                            // for the typing experience. You keep both.
                            //
                            // NOTE: this only becomes fully effective once the app has an
                            // associated-domains entitlement with `webcredentials:<domain>` and
                            // the matching AASA file is served. Neither exists yet — tracked
                            // separately. Correct trait now, so that work is a config change
                            // rather than a code change.
                            .textContentType(.username)
                            .keyboardType(.emailAddress)
                            .autocapitalization(.none)
                            .autocorrectionDisabled()
                    }

                    labeled("Password") {
                        SecureField("", text: $password)
                            .textContentType(mode == .signIn ? .password : .newPassword)
                    }

                    if let errorMessage {
                        Text(errorMessage)
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.bearish)
                            .padding(.top, 4)
                    }

                    Button(action: submit) {
                        HStack {
                            Spacer()
                            if isSubmitting {
                                ProgressView().tint(.white)
                            } else {
                                Text(mode == .signIn ? "Sign In" : "Create Account")
                                    .font(AppTypography.bodyEmphasis)
                                    .foregroundColor(.white)
                            }
                            Spacer()
                        }
                        .padding(.vertical, 14)
                        .background(canSubmit ? AppColors.primaryFill : AppColors.primaryFill.opacity(0.4))
                        .cornerRadius(AppCornerRadius.medium)
                    }
                    .disabled(!canSubmit || isSubmitting)
                    .padding(.top, 8)

                    socialSignInSection

                    // Recovery. Sign-in only: on the sign-up form there is no account to
                    // recover yet, and it would just be noise.
                    if mode == .signIn {
                        Button {
                            showForgotPassword = true
                        } label: {
                            Text("Forgot password?")
                                .font(AppTypography.bodySmall)
                                .foregroundColor(AppColors.primaryBlue)
                        }
                        .frame(maxWidth: .infinity, alignment: .center)
                        .padding(.top, 4)
                    }

                    Button(action: toggleMode) {
                        Text(mode == .signIn
                             ? "No account? Create one"
                             : "Already have an account? Sign in")
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.primaryBlue)
                    }
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.top, 4)

                    Spacer(minLength: 40)
                }
                .padding(.horizontal, 24)
            }
        }
        .sheet(isPresented: $showForgotPassword) {
            // Carry the typed email over so the user doesn't retype it.
            ForgotPasswordView(initialEmail: email)
        }
        .overlay {
            if let message = pendingConfirmationMessage {
                confirmationPrompt(message)
            }
        }
    }

    // MARK: - Social sign-in

    /// Sign in with Apple + Google.
    ///
    /// Both are here because App Review 4.8 requires it: offering Google obliges us to also
    /// offer a login service that limits collection to name and email and lets the user keep
    /// their address private. Sign in with Apple satisfies that, so they ship together.
    @ViewBuilder
    private var socialSignInSection: some View {
        VStack(spacing: 12) {
            HStack(spacing: 12) {
                Rectangle().fill(AppColors.textMuted.opacity(0.3)).frame(height: 1)
                Text("or")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Rectangle().fill(AppColors.textMuted.opacity(0.3)).frame(height: 1)
            }
            .padding(.vertical, 4)

            SignInWithAppleButton(.continue) { request in
                // Nonce is generated and hashed by the service; the raw value goes to the
                // backend so Supabase can verify the pairing.
                let prepared = SocialSignInService.shared.makeAppleRequest()
                request.requestedScopes = prepared.requestedScopes
                request.nonce = prepared.nonce
            } onCompletion: { result in
                handleApple(result)
            }
            .signInWithAppleButtonStyle(.white)
            .frame(height: 48)
            .cornerRadius(AppCornerRadius.medium)
            .disabled(isSubmitting)

            Button(action: signInWithGoogle) {
                HStack(spacing: 8) {
                    Image(systemName: "globe")
                        .font(AppTypography.iconSmall)
                    Text("Continue with Google")
                        .font(AppTypography.bodyEmphasis)
                }
                .foregroundColor(AppColors.textPrimary)
                .frame(maxWidth: .infinity)
                .frame(height: 48)
                .cardSurface(cornerRadius: AppCornerRadius.medium)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .stroke(AppColors.textMuted.opacity(0.3), lineWidth: 1)
                )
            }
            .disabled(isSubmitting)
        }
    }

    // MARK: - Email confirmation prompt

    @ViewBuilder
    private func confirmationPrompt(_ message: String) -> some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            VStack(spacing: 16) {
                Image(systemName: "envelope.badge.fill")
                    .font(AppTypography.iconHero)
                    .foregroundColor(AppColors.primaryBlue)

                Text("Confirm your email")
                    .font(AppTypography.titleLarge)
                    .foregroundColor(AppColors.textPrimary)

                Text(message)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)

                Text(email)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Button {
                    pendingConfirmationMessage = nil
                    mode = .signIn
                    password = ""
                } label: {
                    Text("Back to Sign In")
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(.white)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(AppColors.primaryFill)
                        .cornerRadius(AppCornerRadius.medium)
                }
                .padding(.top, 8)

                // Without this, a lost or spam-filtered confirmation email leaves the
                // account permanently unusable with no way forward.
                Button(action: resendConfirmation) {
                    Text(didResendConfirmation ? "Confirmation email sent" : "Resend confirmation email")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(didResendConfirmation ? AppColors.textMuted : AppColors.primaryBlue)
                }
                .disabled(didResendConfirmation || isSubmitting)
            }
            .padding(.horizontal, 32)
        }
    }

    // MARK: - Social actions

    private func handleApple(_ result: Result<ASAuthorization, Error>) {
        errorMessage = nil
        Task {
            do {
                let handshake = try SocialSignInService.shared.handleAppleCompletion(result)
                isSubmitting = true
                defer { isSubmitting = false }
                try await appState.completeSocialSignIn(handshake)
            } catch SocialSignInError.cancelled {
                // User backed out — not an error.
            } catch {
                errorMessage = friendlyError(error)
            }
        }
    }

    private func signInWithGoogle() {
        errorMessage = nil
        Task {
            // BEFORE the await, not after. `signInWithGoogle()` is the long one — it presents
            // the whole web flow — and setting the flag afterwards left every button enabled
            // and spinner-less for that entire round trip, so a second tap could start a
            // second authentication session on top of the first.
            isSubmitting = true
            defer { isSubmitting = false }
            do {
                let handshake = try await SocialSignInService.shared.signInWithGoogle()
                try await appState.completeSocialSignIn(handshake)
            } catch SocialSignInError.cancelled {
                // User backed out — not an error.
            } catch {
                errorMessage = friendlyError(error)
            }
        }
    }

    private func resendConfirmation() {
        Task {
            do {
                try await appState.resendConfirmation(email: email)
                didResendConfirmation = true
            } catch {
                errorMessage = friendlyError(error)
            }
        }
    }

    private var canSubmit: Bool {
        let emailOK = email.contains("@") && email.count >= 5
        let passwordOK = password.count >= 8
        let nameOK = mode == .signIn || !displayName.trimmingCharacters(in: .whitespaces).isEmpty
        return emailOK && passwordOK && nameOK
    }

    private func toggleMode() {
        mode = (mode == .signIn ? .signUp : .signIn)
        errorMessage = nil
    }

    private func submit() {
        errorMessage = nil
        isSubmitting = true
        Task {
            defer { isSubmitting = false }
            do {
                switch mode {
                case .signIn:
                    try await appState.signIn(email: email, password: password)
                case .signUp:
                    let outcome = try await appState.signUp(
                        email: email, password: password, displayName: displayName
                    )
                    // Confirmation required is the NORMAL path — there is no session yet,
                    // so show the inbox prompt rather than dismissing as if signed in.
                    if case .needsEmailConfirmation(let message) = outcome {
                        pendingConfirmationMessage = message
                    }
                }
            } catch {
                errorMessage = friendlyError(error)
            }
        }
    }

    private func friendlyError(_ error: Error) -> String {
        // SocialSignInError FIRST — it is not an APIError, so without this arm it fell straight
        // past the `as? APIError` cast to the generic line at the bottom and its
        // `errorDescription` was never read. That made the whole "surface the provider's own
        // reason" change unobservable: Supabase with Google disabled returns
        // `#error_description=Unsupported+provider`, the parser correctly threw
        // `.provider("Unsupported provider…")`, and the user still saw "Sign in failed. Please
        // try again." Same dead end for `.notConfigured("Supabase URL missing")` — a missing
        // Info.plist key in a Release build looked identical to a wrong password.
        //
        // `.cancelled` deliberately has a nil description and never reaches here: the callers
        // catch it separately as a non-error.
        if let social = error as? SocialSignInError, let reason = social.errorDescription {
            return reason
        }
        if let api = error as? APIError {
            switch api {
            case .authError(_, let message):
                // A 401 carrying the structured contract — the backend's own `user_message`.
                // MUST come before any fallback: `/auth/login`, `/auth/oauth` and
                // `/auth/session-exchange` all return `AUTH_CREDENTIALS_INVALID` or
                // `AUTH_PROVIDER_FAILED` now, and without this arm they fell through to
                // `default:` and showed the useless generic line at the bottom of this function.
                return message
            case .unauthorized:
                // Legacy shape only: a 401 whose body is not on the contract (an older backend,
                // or a proxy's own response). It is no longer reachable from our auth routes —
                // this used to hardcode "Email or password is incorrect", which is why a failed
                // Apple sign-in accused the user of mistyping a password that flow never asks for.
                return "We couldn't sign you in. Please try again."
            case .businessError(_, let message):
                return message
            case .rateLimited:
                return "Too many attempts. Please wait a minute and try again."
            case .networkError:
                return "Couldn't reach the server. Check your connection."
            default:
                break
            }
        }
        return "Sign in failed. Please try again."
    }

    @ViewBuilder
    private func labeled(_ label: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
            content()
                .padding(.horizontal, 14)
                .padding(.vertical, 12)
                .cardSurface(cornerRadius: AppCornerRadius.medium)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}
