//
//  SignInView.swift
//  ios
//
//  Minimal email/password sign-in + sign-up gating screen.
//  Without this, RootView falls through to the main app as a guest
//  user (id 00000000-...) and every authed call quietly fails.
//

import SwiftUI

struct SignInView: View {
    @Environment(AppState.self) private var appState

    @State private var mode: Mode = .signIn
    @State private var email: String = ""
    @State private var password: String = ""
    @State private var displayName: String = ""
    @State private var errorMessage: String?
    @State private var isSubmitting = false

    enum Mode { case signIn, signUp }

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Image("CaydexLogo")
                        .resizable()
                        .scaledToFit()
                        .frame(width: 80, height: 80)
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
                            .textContentType(.emailAddress)
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
                        .background(canSubmit ? AppColors.primaryBlue : AppColors.primaryBlue.opacity(0.4))
                        .cornerRadius(AppCornerRadius.medium)
                    }
                    .disabled(!canSubmit || isSubmitting)
                    .padding(.top, 8)

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
                    try await appState.signUp(
                        email: email, password: password, displayName: displayName
                    )
                }
            } catch {
                errorMessage = friendlyError(error)
            }
        }
    }

    private func friendlyError(_ error: Error) -> String {
        if let api = error as? APIError {
            switch api {
            case .unauthorized:
                return "Email or password is incorrect."
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
                .background(AppColors.cardBackground)
                .cornerRadius(AppCornerRadius.medium)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}
