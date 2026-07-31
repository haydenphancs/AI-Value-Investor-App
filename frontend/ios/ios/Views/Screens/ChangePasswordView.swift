//
//  ChangePasswordView.swift
//  ios
//
//  Screen: change the password of a signed-in user.
//
//  Requires the CURRENT password even though the caller is already authenticated. Without
//  that, a stolen access token would be enough to take permanent ownership of the account
//  by changing its password — the backend enforces the same rule.
//

import SwiftUI

struct ChangePasswordView: View {
    @Environment(\.dismiss) private var dismiss

    @State private var currentPassword = ""
    @State private var newPassword = ""
    @State private var confirmPassword = ""
    @State private var isSubmitting = false
    @State private var errorMessage: String?
    @State private var didChange = false

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    if didChange {
                        successState
                    } else {
                        form
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
        .navigationTitle("Change Password")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
    }

    private var form: some View {
        VStack(alignment: .leading, spacing: 20) {
            labeled("Current password") {
                SecureField("", text: $currentPassword)
                    .textContentType(.password)
            }

            labeled("New password") {
                SecureField("", text: $newPassword)
                    .textContentType(.newPassword)
            }

            labeled("Confirm new password") {
                SecureField("", text: $confirmPassword)
                    .textContentType(.newPassword)
            }

            Text("At least 8 characters. Other devices will need to sign in again.")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .fixedSize(horizontal: false, vertical: true)

            // Mismatch is worth calling out before submitting rather than after a
            // round-trip — it is the most common mistake on this form.
            if !confirmPassword.isEmpty && confirmPassword != newPassword {
                Text("The new passwords don't match.")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.bearish)
            }

            Button(action: submit) {
                HStack {
                    Spacer()
                    if isSubmitting {
                        ProgressView().tint(.white)
                    } else {
                        Text("Change Password")
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
        }
    }

    private var successState: some View {
        VStack(alignment: .leading, spacing: 12) {
            Image(systemName: "checkmark.circle.fill")
                .font(AppTypography.iconXXL)
                .foregroundColor(AppColors.bullish)

            Text("Password changed")
                .font(AppTypography.titleLarge)
                .foregroundColor(AppColors.textPrimary)

            Text("Your password has been updated. Any other devices signed in to this account will need to sign in again.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            Button { dismiss() } label: {
                Text("Done")
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(.white)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
                    .background(AppColors.primaryBlue)
                    .cornerRadius(AppCornerRadius.medium)
            }
            .padding(.top, 8)
        }
    }

    private var canSubmit: Bool {
        !currentPassword.isEmpty
            && newPassword.count >= 8
            && newPassword == confirmPassword
            && newPassword != currentPassword
    }

    private func submit() {
        errorMessage = nil
        isSubmitting = true
        Task {
            defer { isSubmitting = false }
            do {
                _ = try await APIClient.shared.request(
                    endpoint: .changePassword(
                        currentPassword: currentPassword,
                        newPassword: newPassword
                    ),
                    responseType: MessageResponse.self
                )
                // Clear the secrets from memory as soon as they're no longer needed.
                currentPassword = ""
                newPassword = ""
                confirmPassword = ""
                didChange = true
            } catch {
                errorMessage = AppError.from(error).message
            }
        }
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
    NavigationStack { ChangePasswordView() }
        .preferredColorScheme(.dark)
}
