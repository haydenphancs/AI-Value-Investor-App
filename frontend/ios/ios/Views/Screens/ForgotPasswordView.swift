//
//  ForgotPasswordView.swift
//  ios
//
//  Screen: password recovery. Two steps in one view — request a code, then set a new
//  password with it.
//
//  Why this exists: there was no recovery path at all. A user who forgot their password
//  was permanently locked out of their account, their research, and their paid credits,
//  with no way back in.
//
//  Why a CODE rather than a tapped email link: the link flow needs either a hosted
//  redirect page or Universal Links with an Associated Domains entitlement. Neither exists
//  yet, and a 6-digit code the user types needs no hosting and no deep-link plumbing.
//

import SwiftUI

struct ForgotPasswordView: View {
    /// Pre-filled from the sign-in form so the user doesn't retype it.
    let initialEmail: String

    @Environment(\.dismiss) private var dismiss

    private enum Step { case requestCode, enterCode }

    @State private var step: Step = .requestCode
    @State private var email: String
    @State private var code: String = ""
    @State private var newPassword: String = ""
    @State private var isSubmitting = false
    @State private var errorMessage: String?
    /// Set after a successful reset; the view then shows a terminal success state.
    @State private var didReset = false

    init(initialEmail: String = "") {
        self.initialEmail = initialEmail
        _email = State(initialValue: initialEmail)
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()

                ScrollView {
                    VStack(alignment: .leading, spacing: 20) {
                        header

                        if didReset {
                            successState
                        } else {
                            switch step {
                            case .requestCode: requestCodeForm
                            case .enterCode:   enterCodeForm
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
                }
            }
            .navigationTitle("Reset Password")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(AppColors.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(didReset ? "Done" : "Cancel") { dismiss() }
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    // MARK: - Steps

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: didReset ? "checkmark.circle.fill" : "lock.rotation")
                .font(AppTypography.iconXXL)
                .foregroundColor(didReset ? AppColors.bullish : AppColors.primaryBlue)
                .padding(.top, 24)

            Text(headerTitle)
                .font(AppTypography.titleLarge)
                .foregroundColor(AppColors.textPrimary)

            Text(headerSubtitle)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.bottom, 8)
    }

    private var headerTitle: String {
        if didReset { return "Password reset" }
        return step == .requestCode ? "Forgot your password?" : "Enter your code"
    }

    private var headerSubtitle: String {
        if didReset {
            return "You can now sign in with your new password. Any other devices will need to sign in again."
        }
        switch step {
        case .requestCode:
            return "Enter your email and we'll send you a code to reset it."
        case .enterCode:
            // Deliberately hedged: the backend never confirms whether the address is
            // registered, so promising "we sent you an email" would be a lie for an
            // unknown address.
            return "If an account exists for \(email), we've sent a code. Enter it below with your new password. Check spam if it hasn't arrived."
        }
    }

    private var requestCodeForm: some View {
        VStack(alignment: .leading, spacing: 20) {
            labeled("Email") {
                TextField("", text: $email)
                    .textContentType(.emailAddress)
                    .keyboardType(.emailAddress)
                    .autocapitalization(.none)
                    .autocorrectionDisabled()
            }

            primaryButton(title: "Send Code", enabled: emailLooksValid) {
                requestCode()
            }
        }
    }

    private var enterCodeForm: some View {
        VStack(alignment: .leading, spacing: 20) {
            labeled("Code from email") {
                TextField("", text: $code)
                    .textContentType(.oneTimeCode)
                    .keyboardType(.numberPad)
                    .autocorrectionDisabled()
            }

            labeled("New password") {
                SecureField("", text: $newPassword)
                    .textContentType(.newPassword)
            }

            Text("At least 8 characters.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            primaryButton(title: "Reset Password", enabled: canReset) {
                submitReset()
            }

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
        primaryButton(title: "Back to Sign In", enabled: true) { dismiss() }
    }

    // MARK: - Validation

    private var emailLooksValid: Bool {
        email.contains("@") && email.count >= 5
    }

    private var canReset: Bool {
        let digits = code.filter(\.isNumber)
        return digits.count >= 6 && newPassword.count >= 8
    }

    // MARK: - Actions

    private func requestCode() {
        errorMessage = nil
        isSubmitting = true
        Task {
            defer { isSubmitting = false }
            do {
                _ = try await APIClient.shared.request(
                    endpoint: .forgotPassword(email: email.trimmingCharacters(in: .whitespaces)),
                    responseType: MessageResponse.self
                )
                // Advance regardless of whether the address is registered — the backend
                // deliberately doesn't say, and neither should the UI.
                step = .enterCode
            } catch {
                errorMessage = AppError.from(error).message
            }
        }
    }

    private func submitReset() {
        errorMessage = nil
        isSubmitting = true
        Task {
            defer { isSubmitting = false }
            do {
                _ = try await APIClient.shared.request(
                    endpoint: .resetPassword(
                        email: email.trimmingCharacters(in: .whitespaces),
                        code: code.filter(\.isNumber),
                        newPassword: newPassword
                    ),
                    responseType: MessageResponse.self
                )
                didReset = true
            } catch {
                errorMessage = AppError.from(error).message
            }
        }
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
                    ProgressView().tint(.white)
                } else {
                    Text(title)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(.white)
                }
                Spacer()
            }
            .padding(.vertical, 14)
            .background(enabled ? AppColors.primaryBlue : AppColors.primaryBlue.opacity(0.4))
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
                .background(AppColors.cardBackground)
                .cornerRadius(AppCornerRadius.medium)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}

#Preview {
    ForgotPasswordView(initialEmail: "someone@example.com")
}
