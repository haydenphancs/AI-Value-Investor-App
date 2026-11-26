//
//  SignInView.swift
//  ios
//
//  Minimal email/password sign-in + sign-up gating screen.
//  Without this, RootView falls through to the main app as a guest
//  user (id 00000000-...) and every authed call quietly fails.
//

import SwiftUI
import Accessibility
import AuthenticationServices
import OSLog

struct SignInView: View {
    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    @State private var mode: Mode = .signIn
    @State private var email: String = ""
    /// Kept ONLY here, in memory, for the life of this screen — never persisted. The code screen
    /// needs it (confirming the address signs the user in with it), so it is not cleared while
    /// that screen is up.
    @State private var password: String = ""
    @State private var displayName: String = ""
    @State private var errorMessage: String?
    @State private var isSubmitting = false
    @State private var showForgotPassword = false
    /// Sign-up only: the new password can be shown while typing it (CC-07). Sign-in keeps the
    /// plain secure field AutoFill fills.
    @State private var showPassword = false
    @FocusState private var focusedField: Field?
    /// Terms / Privacy, readable BEFORE an account exists. See `legalFooter`.
    @State private var legalDocument: SignInLegalDocument?
    /// Sign-in-wrap assent, shown under every sign-in / sign-up button. See `legalFooter`.
    static let assentLine = "By tapping Sign In or Create Account, or continuing with Apple or Google, you agree to the Terms of Use and acknowledge the Privacy Policy."
    /// Set when the address still needs confirming — after a sign-up, and when a sign-in is
    /// refused with EMAIL_NOT_CONFIRMED. The code screen (`ConfirmEmailView`) then covers the
    /// form: there is no session yet, so this view must not pretend the user is signed in.
    @State private var pendingConfirmation: PendingConfirmation?
    /// The Forgot-password sheet was opened from the code screen. See `forgotPasswordDismissed`.
    @State private var forgotPasswordFromConfirmation = false

    private static let log = Logger(subsystem: "com.phan.caydex", category: "auth")
    /// The fields' vertical inset (`labeled`). The show / hide button cancels it so its 44-pt
    /// target fills the field's height instead of growing the field.
    private static let fieldVerticalPadding: CGFloat = 12
    /// Apple's minimum tap target.
    private static let minTapTarget: CGFloat = 44

    enum Mode { case signIn, signUp }

    private enum Field: Hashable { case displayName, email, password }

    /// What the code screen needs. The address is captured when the screen opens, so editing
    /// the form underneath can never change which account a code is checked against.
    private struct PendingConfirmation {
        let email: String
        /// True right after a sign-up, when an email has just gone out: the code screen then
        /// starts its resend cooldown. False on the sign-in path, which sends nothing.
        let codeJustSent: Bool
        /// The sign-up response's sentence ("…Already have an account? Sign in or reset your
        /// password."), shown on the code screen. The server words it identically for a new, a
        /// pending and an already-confirmed address, and it is the only hint an existing user
        /// gets that no code is coming (IOS-CONF-2). nil on the sign-in path.
        let signUpMessage: String?
    }

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
                        .accessibilityAddTraits(.isHeader)

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
                                .focused($focusedField, equals: .displayName)
                                .submitLabel(.next)
                                .onSubmit { focusedField = .email }
                                .accessibilityLabel("Display name")
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
                            .focused($focusedField, equals: .email)
                            .submitLabel(.next)
                            .onSubmit { focusedField = .password }
                            .accessibilityLabel("Email")
                    }

                    labeled("Password") {
                        passwordField
                    }

                    // Sign-up ONLY. On sign-in the rules are irrelevant and actively harmful:
                    // accounts created before this rule existed do not satisfy it, and showing
                    // it there would tell a user with a valid password that it is wrong.
                    if mode == .signUp {
                        PasswordRequirementsView(password: password)
                            .padding(.top, AppSpacing.xs)
                    }

                    if let errorMessage {
                        Text(errorMessage)
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.loss)
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(.top, 4)
                    }

                    Button(action: submit) {
                        HStack {
                            Spacer()
                            if isSubmitting {
                                ProgressView()
                                    .tint(AppColors.textOnAccent)
                                    .accessibilityLabel(mode == .signIn ? "Signing in" : "Creating account")
                            } else {
                                Text(mode == .signIn ? "Sign In" : "Create Account")
                                    .font(AppTypography.bodyEmphasis)
                                    .foregroundColor(AppColors.textOnAccent)
                            }
                            Spacer()
                        }
                        .padding(.vertical, 14)
                        .background(canSubmit ? AppColors.primaryFill : AppColors.primaryFill.opacity(0.4))
                        .cornerRadius(AppCornerRadius.medium)
                    }
                    .disabled(!canSubmit || isSubmitting)
                    .padding(.top, 8)

                    // Sign-in-wrap assent, directly under the button it covers and above the
                    // Apple / Google buttons it also names — in readable body text, never below
                    // the fold (see `legalFooter` for the why).
                    Text(Self.assentLine)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .multilineTextAlignment(.center)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .center)

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

                    legalFooter

                    Spacer(minLength: 40)
                }
                .padding(.horizontal, 24)
            }
            // The code screen is opaque, but VoiceOver would still walk the form beneath it.
            .accessibilityHidden(pendingConfirmation != nil)
        }
        .sheet(isPresented: $showForgotPassword, onDismiss: forgotPasswordDismissed) {
            // Carry the typed email over so the user doesn't retype it.
            ForgotPasswordView(initialEmail: pendingConfirmation?.email ?? email)
        }
        .sheet(item: $legalDocument) { document in
            NavigationStack {
                Group {
                    switch document {
                    case .terms: TermsOfUseView()
                    case .privacy: PrivacyPolicyView()
                    }
                }
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("Done") { legalDocument = nil }
                    }
                }
            }
        }
        .overlay {
            if let pending = pendingConfirmation {
                ConfirmEmailView(
                    email: pending.email,
                    password: password,
                    codeJustSent: pending.codeJustSent,
                    signUpMessage: pending.signUpMessage,
                    onForgotPassword: {
                        forgotPasswordFromConfirmation = true
                        showForgotPassword = true
                    },
                    onUseDifferentEmail: leaveConfirmation
                )
            }
        }
        // An error that appears silently is invisible to VoiceOver (CC-05).
        .onChange(of: errorMessage) { _, message in
            if let message, !message.isEmpty {
                AccessibilityNotification.Announcement(message).post()
            }
        }
        // CLOSE ITSELF WHEN THE SESSION ARRIVES.
        //
        // `submit()` succeeds and does nothing but stop the spinner, so this view relied
        // entirely on its presenter to take it away — and of the five presenters, only
        // `SignInRequiredSheet` did (via its own `.onChange`, which tears down the nested
        // sheet with it). The other four are plain `.sheet(isPresented:)`, which SwiftUI
        // never closes on its own: the user typed their password, authentication SUCCEEDED,
        // and they were left looking at a filled-in "Welcome back" form with no
        // confirmation — indistinguishable from a silent failure, escapable only by
        // swiping. Reached from an error toast's "Sign In" action, from Profile, from the
        // Notifications banner, and from the root's own error path.
        //
        // Fixed HERE rather than at four call sites so a fifth presenter cannot reintroduce
        // it. `.onChange` and not `.task`/`.onAppear`: it must fire on the TRANSITION, or a
        // view presented while already authenticated would dismiss itself instantly.
        //
        // An address that still needs confirming deliberately does NOT dismiss — there is no
        // session yet, so `isAuthenticated` stays false and the code screen above remains.
        // Confirming there signs the user in, which is what closes this view.
        .onChange(of: appState.auth.isAuthenticated) { _, isAuthenticated in
            if isAuthenticated { dismiss() }
        }
    }

    // MARK: - Password field

    /// Sign-in: one secure field, the one AutoFill fills. Sign-up: the same field with a show /
    /// hide toggle, so a typo is not baked into a brand-new account's password (CC-07).
    @ViewBuilder
    private var passwordField: some View {
        HStack(spacing: 8) {
            Group {
                if mode == .signUp && showPassword {
                    TextField("", text: $password)
                        .textContentType(.newPassword)
                        .autocapitalization(.none)
                        .autocorrectionDisabled()
                } else {
                    SecureField("", text: $password)
                        .textContentType(mode == .signIn ? .password : .newPassword)
                }
            }
            .focused($focusedField, equals: .password)
            .submitLabel(.go)
            .onSubmit(submit)
            .accessibilityLabel("Password")

            if mode == .signUp {
                Button {
                    let hadFocus = focusedField == .password
                    showPassword.toggle()
                    // The toggle swaps the SecureField for a TextField (or back), so the field
                    // holding focus leaves the hierarchy and takes the keyboard with it. Writing
                    // `.password` now is a no-op — it already reads `.password` — so focus is
                    // re-asserted on the next main-actor turn, once the new field is mounted (PW-6).
                    if hadFocus {
                        Task { @MainActor in focusedField = .password }
                    }
                } label: {
                    Image(systemName: showPassword ? "eye.slash" : "eye")
                        .font(AppTypography.iconSmall)
                        .foregroundColor(AppColors.textMuted)
                        // The glyph alone was a ~14-pt target (PW-6). A button hit-tests what its
                        // label draws, so the frame needs a shape to make all of it tappable.
                        .frame(minWidth: Self.minTapTarget, minHeight: Self.minTapTarget)
                        .contentShape(Rectangle())
                }
                .buttonStyle(PlainButtonStyle())
                .padding(.vertical, -Self.fieldVerticalPadding)
                .accessibilityLabel(showPassword ? "Hide password" : "Show password")
            }
        }
    }

    // MARK: - Legal

    /// Guideline 5.1.1(i): the privacy policy must be easy to find inside the app, and this is
    /// the form that collects email, name and password. The in-app copies of both documents
    /// were reachable only from Profile and the paywall — both behind sign-in — so nobody
    /// could read them before handing the data over.
    ///
    /// The assent line makes this sign-IN-wrap rather than browsewrap: the Terms' arbitration
    /// clause, class-action waiver and liability cap are far more likely to bind a user who was
    /// told, next to the button they tapped, that continuing means agreeing (Berman v. Freedom
    /// Financial, 9th Cir. 2022). Found by the 2026-09-25 legal review
    /// (documents/research/dcf-fair-value.md §9.4). The assent line itself sits directly under
    /// the submit button (Berman rejected small grey text below the fold); this footer keeps the
    /// links to both documents.
    private var legalFooter: some View {
        VStack(spacing: AppSpacing.xs) {
            HStack(spacing: AppSpacing.xs) {
                Button { legalDocument = .terms } label: {
                    Text("Terms of Use").foregroundColor(AppColors.primaryBlue)
                }
                Text("·").foregroundColor(AppColors.textMuted)
                Button { legalDocument = .privacy } label: {
                    Text("Privacy Policy").foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .font(AppTypography.caption)
        .buttonStyle(PlainButtonStyle())
        .frame(maxWidth: .infinity, alignment: .center)
        .padding(.top, 8)
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
            .accessibilityHidden(true)

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
                        .accessibilityHidden(true)
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

    // MARK: - Email confirmation

    /// Open the code screen for `address`. The form's error is cleared on the way in: the code
    /// screen has its own, and a stale line under the password field would be waiting on return.
    private func showConfirmation(for address: String, codeJustSent: Bool, signUpMessage: String? = nil) {
        errorMessage = nil
        focusedField = nil
        pendingConfirmation = PendingConfirmation(
            email: address, codeJustSent: codeJustSent, signUpMessage: signUpMessage
        )
    }

    /// "Use a different email": back to the form in the mode it was left in, fields kept so a
    /// mistyped address can be corrected in place.
    private func leaveConfirmation() {
        errorMessage = nil
        pendingConfirmation = nil
        focusedField = .email
    }

    /// The Forgot-password sheet closed. When it was opened from the code screen, a reset may
    /// have replaced the password this view holds (and a recovery code also confirms the
    /// address), so the code screen's "Sign in" would try a dead password. Back to the sign-in
    /// form instead, address kept, password cleared for the new one. A reset abandoned halfway
    /// costs a retyped password; signing in then routes straight back to the code screen.
    private func forgotPasswordDismissed() {
        guard forgotPasswordFromConfirmation else { return }
        forgotPasswordFromConfirmation = false
        pendingConfirmation = nil
        errorMessage = nil
        mode = .signIn
        showPassword = false
        password = ""
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
                SocialSignInService.logFailure(error, provider: "apple")
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
                SocialSignInService.logFailure(error, provider: "google")
                errorMessage = friendlyError(error)
            }
        }
    }

    private var canSubmit: Bool {
        let emailOK = email.contains("@") && email.count >= 5
        // Sign-IN keeps the length-only check on purpose — an existing account's password
        // predates these rules and gating sign-in on them would lock its owner out.
        let passwordOK = mode == .signIn
            ? password.count >= 8
            : PasswordRule.isSatisfied(password)
        let nameOK = mode == .signIn || !displayName.trimmingCharacters(in: .whitespaces).isEmpty
        return emailOK && passwordOK && nameOK
    }

    private func toggleMode() {
        mode = (mode == .signIn ? .signUp : .signIn)
        showPassword = false
        errorMessage = nil
    }

    private func submit() {
        // `.onSubmit` on the password field reaches here too, so the button's own gate is
        // repeated rather than assumed.
        guard canSubmit, !isSubmitting else { return }
        errorMessage = nil
        isSubmitting = true
        let submittedMode = mode
        let address = email.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            defer { isSubmitting = false }
            do {
                switch submittedMode {
                case .signIn:
                    try await appState.signIn(email: email, password: password)
                case .signUp:
                    let outcome = try await appState.signUp(
                        email: email, password: password, displayName: displayName
                    )
                    // Confirmation required is the NORMAL path — there is no session yet, so
                    // go to the code screen rather than dismissing as if signed in. The server's
                    // sentence travels with it: for an address that already has an account it
                    // is the only thing saying "sign in instead" (IOS-CONF-2).
                    if case .needsEmailConfirmation(let message) = outcome {
                        showConfirmation(for: address, codeJustSent: true, signUpMessage: message)
                    }
                }
            } catch {
                // The password checked out and only the ADDRESS is unconfirmed: the user needs
                // the code screen, not a red line. Nothing is emailed from here — the old
                // auto-resend rotated the code on every attempt, killing the one the user was
                // about to type, and spent the hourly resend budget before they tapped anything.
                // The code screen has an explicit "Send a new code".
                if case .emailNotConfirmed = AppError.from(error) {
                    showConfirmation(for: address, codeJustSent: false)
                    return
                }
                // The code only — never the address or anything typed.
                let action = submittedMode == .signIn ? "sign-in" : "sign-up"
                let code = AppError.from(error).analyticsCode
                Self.log.notice("email \(action, privacy: .public) failed: \(code, privacy: .public)")
                errorMessage = friendlyError(error)
            }
        }
    }

    /// One line of copy for any failure on this screen, through `AppError.from` like every
    /// other screen (IOS-AUTH-07). The fallback names what the user was doing: a failed Create
    /// Account used to read "Sign in failed", which suggested the account already existed.
    private func friendlyError(_ error: Error) -> String {
        // SocialSignInError FIRST — it is not an APIError, so without this arm it fell straight
        // past the `as? APIError` cast to the generic line at the bottom and its
        // `errorDescription` was never read. The descriptions are fixed copy (SOC-06): the
        // provider's raw reason goes to the log (`SocialSignInService.logFailure`), never here.
        //
        // `.cancelled` deliberately has a nil description and never reaches here: the callers
        // catch it separately as a non-error.
        if let social = error as? SocialSignInError {
            return social.errorDescription ?? genericFailure
        }
        let appError = AppError.from(error)
        switch appError {
        case .unauthorized, .tokenExpired:
            // Legacy shape only: a 401 whose body is not on the contract (an older backend, or a
            // proxy's own response). `AppError`'s own copy says "your session has expired",
            // which is false on a form the user is signing in with.
            return mode == .signIn
                ? "We couldn't sign you in. Please try again."
                : "We couldn't create your account. Please try again."
        case .noConnection, .timeout:
            return "Couldn't reach the server. Check your connection."
        case .rateLimited(let seconds):
            // In human units, from the bucket that refused (IOS-AUTH-04). Sign-in also points at
            // recovery: the forgot-password route has its own limits.
            let wait = AppError.waitPhrase(seconds: seconds)
            return mode == .signIn
                ? "Too many attempts. Please wait \(wait) and try again, or use Forgot password."
                : "Too many attempts. Please wait \(wait) and try again."
        case .unknown, .cancelled:
            // A decode failure, a body off the contract, or an SDK's own text — none of it is
            // written for users. The code is already in the log (`submit`).
            return genericFailure
        default:
            let message = appError.message
            return message.isEmpty ? genericFailure : message
        }
    }

    private var genericFailure: String {
        mode == .signIn
            ? "Sign in failed. Please try again."
            : "Couldn't create your account. Please try again."
    }

    @ViewBuilder
    private func labeled(_ label: String, @ViewBuilder content: () -> some View) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                // The field below carries the same name as its accessibility label, so
                // VoiceOver reads it once, on the control.
                .accessibilityHidden(true)
            content()
                .padding(.horizontal, 14)
                .padding(.vertical, Self.fieldVerticalPadding)
                .cardSurface(cornerRadius: AppCornerRadius.medium)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}

/// Which legal document the sign-in screen is showing. `Identifiable` for `.sheet(item:)`.
private enum SignInLegalDocument: String, Identifiable {
    case terms, privacy
    var id: String { rawValue }
}

#Preview {
    SignInView()
        .environment(AppState())
}
