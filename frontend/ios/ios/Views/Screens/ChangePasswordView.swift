//
//  ChangePasswordView.swift
//  ios
//
//  Screen: change the password of a signed-in user — or CREATE one, for an account that has
//  none.
//
//  `.change` requires the CURRENT password even though the caller is already authenticated.
//  Without that, a stolen access token would be enough to take permanent ownership of the
//  account by changing its password — the backend enforces the same rule.
//
//  `.set` exists because an Apple/Google account has no password at all: Supabase provisions it
//  through `sign_in_with_id_token` and one is never written, so `auth.users.encrypted_password`
//  is NULL. Those users were shown the `.change` form and told **"Your current password is
//  incorrect"** about a password that has never existed — and burned one of five attempts per
//  15 minutes each time they tried. A TestFlight tester found it by asking the obvious question.
//
//  `.set` cannot simply drop the Current-password field: something has to replace the proof it
//  was providing, or a stolen token becomes account ownership by a shorter route. That proof is
//  the 6-digit code emailed to the account address — the same recovery OTP the signed-out
//  ForgotPassword flow uses, via the unchanged `POST /auth/forgot-password`.
//
//  ⚠️ It does NOT reuse `ForgotPasswordView`, which posts to `/auth/reset-password` and gets
//  back a bare message. That stamps `password_changed_at` and evicts every token minted before
//  it — including this session's — so a SIGNED-IN user completing it would be silently signed
//  out. `/auth/set-password` re-mints this caller's tokens after the stamp; `AuthService`
//  adopts them.
//

import SwiftUI

struct ChangePasswordView: View {
    /// Which job this screen is doing. Defaults to `.change`, so every existing call site and
    /// the preview keep their exact previous behaviour.
    enum Mode {
        /// The account has a password. Prove it, then replace it.
        case change
        /// The account has none (Apple/Google). Prove the mailbox with an emailed code instead.
        case set
    }

    /// Two-step flow, `.set` only. `.change` never leaves `.form`.
    private enum Step { case form, requestCode, enterCode }

    let mode: Mode

    @Environment(\.appState) private var appState
    @Environment(\.dismiss) private var dismiss

    @State private var step: Step
    @State private var currentPassword = ""
    @State private var code = ""
    @State private var newPassword = ""
    @State private var confirmPassword = ""
    @State private var isSubmitting = false
    @State private var errorMessage: String?
    @State private var didChange = false

    init(mode: Mode = .change) {
        self.mode = mode
        _step = State(initialValue: mode == .set ? .requestCode : .form)
    }

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if didChange {
                        successState
                    } else {
                        switch step {
                        case .form:        form
                        case .requestCode: requestCodeStep
                        case .enterCode:   enterCodeStep
                        }
                    }

                    if let errorMessage {
                        Text(errorMessage)
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.bearish)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    Spacer(minLength: 40)
                }
                .padding(.horizontal, 24)
                .padding(.top, 16)
            }
        }
        .navigationTitle(mode == .set ? "Set a Password" : "Change Password")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
    }

    // MARK: - Steps

    /// `.change` — unchanged from the original screen.
    private var form: some View {
        VStack(alignment: .leading, spacing: 20) {
            labeled("Current password") {
                SecureField("", text: $currentPassword)
                    .textContentType(.password)
            }

            newPasswordFields

            Text("At least 8 characters. Other devices will need to sign in again.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            mismatchNotice

            primaryButton(title: "Change Password", enabled: canSubmitChange, action: submitChange)
        }
    }

    /// `.set` step 1 — explain why there is no Current-password field, and send the code.
    private var requestCodeStep: some View {
        VStack(alignment: .leading, spacing: 20) {
            Image(systemName: "lock.badge.clock")
                .font(AppTypography.iconXXL)
                .foregroundColor(AppColors.primaryBlue)
                .padding(.top, 8)

            Text(providerExplanation)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            // Naming the exact address matters for Hide My Email: an Apple private-relay user
            // sees an @privaterelay.appleid.com address here and would otherwise assume the
            // code had gone somewhere they cannot read.
            Text("We'll email a 6-digit code to \(accountEmail) to confirm it's you.")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            primaryButton(title: "Send Code", enabled: !accountEmail.isEmpty, action: requestCode)
        }
    }

    /// `.set` step 2 — the code plus the new password.
    private var enterCodeStep: some View {
        VStack(alignment: .leading, spacing: 20) {
            Text("Enter the code we sent to \(accountEmail), then choose your password.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            labeled("Code from email") {
                TextField("", text: $code)
                    .textContentType(.oneTimeCode)
                    .keyboardType(.numberPad)
                    .autocorrectionDisabled()
            }

            newPasswordFields

            // The full rule, not just the length. The server enforces upper + lower + digit +
            // symbol via `_validate_password_strength`, so "At least 8 characters" here would
            // send the user into a 422 on a form they believed they had satisfied — and a
            // Pydantic 422 body does not match APIErrorResponse, so it surfaces as a generic
            // server error rather than as the field problem it is.
            Text("At least 8 characters, with an uppercase and a lowercase letter, a number and a symbol. You'll still be able to sign in with \(providerName) as well.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            mismatchNotice

            primaryButton(title: "Set Password", enabled: canSubmitSet, action: submitSet)

            Button {
                step = .requestCode
                code = ""
                errorMessage = nil
            } label: {
                Text("Didn't get a code? Try again")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.primaryBlue)
            }
            .frame(maxWidth: .infinity, alignment: .center)
        }
    }

    private var successState: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(AppTypography.iconXXL)
                .foregroundColor(AppColors.bullish)

            Text(mode == .set ? "Password set" : "Password changed")
                .font(AppTypography.titleLarge)
                .foregroundColor(AppColors.textPrimary)

            Text(mode == .set
                 ? "You can now sign in with your email and password, as well as with \(providerName). Any other devices signed in to this account will need to sign in again."
                 : "Your password has been updated. Any other devices signed in to this account will need to sign in again.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            Button { dismiss() } label: {
                Text("Done")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textOnAccent)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(AppColors.primaryFill)
                    .cornerRadius(AppCornerRadius.medium)
            }
            .padding(.top, 8)
        }
    }

    // MARK: - Shared pieces

    private var newPasswordFields: some View {
        Group {
            labeled("New password") {
                SecureField("", text: $newPassword)
                    .textContentType(.newPassword)
            }

            labeled("Confirm new password") {
                SecureField("", text: $confirmPassword)
                    .textContentType(.newPassword)
            }
        }
    }

    /// Mismatch is worth calling out before submitting rather than after a round-trip — it is
    /// the most common mistake on this form.
    @ViewBuilder
    private var mismatchNotice: some View {
        if !confirmPassword.isEmpty && confirmPassword != newPassword {
            Text("The new passwords don't match.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.bearish)
        }
    }

    // MARK: - Derived copy

    private var accountEmail: String {
        appState.user.profile?.email ?? ""
    }

    /// "Apple" / "Google", or a neutral fallback when the backend sent no provider list (an
    /// older build, or a probe that failed). Never prints a raw identifier at the user.
    private var providerName: String {
        appState.user.profile?.primaryProviderLabel ?? "your current sign-in method"
    }

    private var providerExplanation: String {
        if let label = appState.user.profile?.primaryProviderLabel {
            return "You sign in with \(label), so this account doesn't have a password yet. You can add one and use either."
        }
        return "This account doesn't have a password yet. You can add one and still use your current sign-in method."
    }

    // MARK: - Validation

    private var canSubmitChange: Bool {
        !currentPassword.isEmpty
            && newPassword.count >= 8
            && newPassword == confirmPassword
            && newPassword != currentPassword
    }

    private var canSubmitSet: Bool {
        code.filter(\.isNumber).count >= 6
            // `PasswordRule` is the same rule the sign-up form shows as a live checklist and the
            // same one `_validate_password_strength` enforces server-side. Gating on it here
            // makes the 422 unreachable from this screen.
            && PasswordRule.isSatisfied(newPassword)
            && newPassword == confirmPassword
    }

    // MARK: - Actions

    private func submitChange() {
        errorMessage = nil
        isSubmitting = true
        Task {
            defer { isSubmitting = false }
            do {
                // Through AuthService, NOT APIClient directly: the server invalidates every
                // token issued before this change — including the one authenticating this very
                // request — and returns replacements. AuthService adopts them, which is what
                // keeps THIS device signed in while other devices are correctly evicted.
                // Decoding it here as a bare MessageResponse silently dropped those tokens and
                // signed the user out on their next request.
                try await appState.authService.changePassword(
                    currentPassword: currentPassword,
                    newPassword: newPassword
                )
                clearSecrets()
                didChange = true
            } catch {
                errorMessage = AppError.from(error).message
            }
        }
    }

    private func requestCode() {
        errorMessage = nil
        isSubmitting = true
        Task {
            defer { isSubmitting = false }
            do {
                // The unchanged public recovery route — no new delivery path to get wrong. It
                // answers identically whether or not the address is registered, but here we
                // know it is: it came from this signed-in account's own profile.
                _ = try await APIClient.shared.request(
                    endpoint: .forgotPassword(email: accountEmail),
                    responseType: MessageResponse.self
                )
                step = .enterCode
            } catch {
                errorMessage = AppError.from(error).message
            }
        }
    }

    private func submitSet() {
        errorMessage = nil
        isSubmitting = true
        Task {
            defer { isSubmitting = false }
            do {
                // Same token-adoption reason as submitChange: setting a password stamps
                // `password_changed_at`, which kills the token that made this request.
                try await appState.authService.setPassword(
                    code: code.filter(\.isNumber),
                    newPassword: newPassword
                )
                // The settings row is driven by `hasPassword`, which just flipped. Re-read the
                // profile so it stops offering "Set a Password" the moment this screen closes.
                await appState.refreshProfile()
                clearSecrets()
                didChange = true
            } catch {
                errorMessage = AppError.from(error).message
            }
        }
    }

    /// Clear the secrets from memory as soon as they're no longer needed.
    private func clearSecrets() {
        currentPassword = ""
        newPassword = ""
        confirmPassword = ""
        code = ""
    }

    // MARK: - Building blocks

    @ViewBuilder
    private func primaryButton(
        title: String, enabled: Bool, action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack {
                Spacer()
                if isSubmitting {
                    ProgressView().tint(AppColors.textOnAccent)
                } else {
                    Text(title)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textOnAccent)
                }
                Spacer()
            }
            .padding(.vertical, 14)
            .background(enabled ? AppColors.primaryFill : AppColors.primaryFill.opacity(0.4))
            .cornerRadius(AppCornerRadius.medium)
        }
        .disabled(!enabled || isSubmitting)
    }

    @ViewBuilder
    private func labeled(
        _ label: String, @ViewBuilder content: () -> some View
    ) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
            content()
                .padding(.vertical, 12)
                .padding(.horizontal, 14)
                .cardSurface(cornerRadius: AppCornerRadius.medium)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}

#Preview("Change") {
    NavigationStack { ChangePasswordView() }
}

#Preview("Set") {
    NavigationStack { ChangePasswordView(mode: .set) }
}
