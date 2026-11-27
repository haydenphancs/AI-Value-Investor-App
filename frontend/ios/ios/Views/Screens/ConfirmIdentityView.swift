//
//  ConfirmIdentityView.swift
//  ios
//
//  "Confirm it's you" — the step before a permanent account deletion (SEC-11, 2026-11-26).
//  Presented as a sheet from General Settings. On success it hands the five-minute proof to
//  `onConfirmed` and closes; the caller sends `DELETE /users/me` with it.
//

import AuthenticationServices
import SwiftUI

struct ConfirmIdentityView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel: ConfirmIdentityViewModel
    @FocusState private var passwordFocused: Bool

    /// Show the password field. False only when the account is KNOWN to have no password
    /// (`hasPassword == false`: an Apple/Google account); unknown shows it.
    let showsPassword: Bool
    let accountEmail: String
    let onConfirmed: (String) -> Void

    init(
        viewModel: ConfirmIdentityViewModel,
        showsPassword: Bool,
        accountEmail: String,
        onConfirmed: @escaping (String) -> Void
    ) {
        _viewModel = StateObject(wrappedValue: viewModel)
        self.showsPassword = showsPassword
        self.accountEmail = accountEmail
        self.onConfirmed = onConfirmed
    }

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background.ignoresSafeArea()
                ScrollView(showsIndicators: false) {
                    VStack(alignment: .leading, spacing: AppSpacing.lg) {
                        Text("Deleting your account is permanent. To make sure it's really you, sign in again below.")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)

                        if showsPassword { passwordSection }

                        if showsPassword { orDivider }

                        providerButtons

                        if let message = viewModel.errorMessage {
                            Text(message)
                                .font(AppTypography.bodySmall)
                                .foregroundColor(AppColors.loss)
                                .fixedSize(horizontal: false, vertical: true)
                                .accessibilityAddTraits(.isStaticText)
                        }

                        Text("Use the same way you signed in to this account.")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(AppSpacing.lg)
                }
            }
            .navigationTitle("Confirm It's You")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                        .disabled(viewModel.isWorking)
                }
            }
        }
        .interactiveDismissDisabled(viewModel.isWorking)
    }

    // MARK: - Sections

    private var passwordSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Password")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .accessibilityHidden(true)
                SecureField("", text: $viewModel.password)
                    .textContentType(.password)
                    .focused($passwordFocused)
                    .submitLabel(.go)
                    .onSubmit(submitPassword)
                    .accessibilityLabel("Password")
                    .padding(.vertical, 12)
                    .padding(.horizontal, 14)
                    .cardSurface(cornerRadius: AppCornerRadius.medium)
                    .foregroundColor(AppColors.textPrimary)
            }
            if !accountEmail.isEmpty {
                Text(accountEmail)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .accessibilityLabel("Account email \(accountEmail)")
            }
            Button(action: submitPassword) {
                HStack {
                    Spacer()
                    if viewModel.isWorking {
                        ProgressView()
                            .tint(AppColors.textOnAccent)
                            .accessibilityLabel("Confirming")
                    } else {
                        Text("Confirm with Password")
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textOnAccent)
                    }
                    Spacer()
                }
                .padding(.vertical, 14)
                .background(viewModel.canSubmitPassword ? AppColors.primaryFill : AppColors.primaryFill.opacity(0.4))
                .cornerRadius(AppCornerRadius.medium)
            }
            .disabled(!viewModel.canSubmitPassword)
        }
    }

    private var orDivider: some View {
        HStack(spacing: 12) {
            Rectangle().fill(AppColors.textMuted.opacity(0.3)).frame(height: 1)
            Text("or")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
            Rectangle().fill(AppColors.textMuted.opacity(0.3)).frame(height: 1)
        }
        .accessibilityHidden(true)
    }

    private var providerButtons: some View {
        VStack(spacing: 12) {
            SignInWithAppleButton(.continue) { request in
                let prepared = SocialSignInService.shared.makeAppleRequest()
                request.requestedScopes = prepared.requestedScopes
                request.nonce = prepared.nonce
            } onCompletion: { result in
                Task {
                    if let proof = await viewModel.confirmWithApple(result) { finish(proof) }
                }
            }
            .signInWithAppleButtonStyle(.white)
            .frame(height: 48)
            .cornerRadius(AppCornerRadius.medium)
            .disabled(viewModel.isWorking)

            Button {
                Task {
                    if let proof = await viewModel.confirmWithGoogle() { finish(proof) }
                }
            } label: {
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
            .disabled(viewModel.isWorking)
        }
    }

    // MARK: - Actions

    private func submitPassword() {
        guard viewModel.canSubmitPassword else { return }
        passwordFocused = false
        Task {
            if let proof = await viewModel.confirmWithPassword() { finish(proof) }
        }
    }

    private func finish(_ proof: String) {
        onConfirmed(proof)
        dismiss()
    }
}

#Preview {
    ConfirmIdentityView(
        viewModel: ConfirmIdentityViewModel.mockError,
        showsPassword: true,
        accountEmail: "you@example.com",
        onConfirmed: { _ in }
    )
}
