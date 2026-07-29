//
//  AppLockView.swift
//  ios
//
//  Full-screen cover shown while the app is locked (App Lock setting on). Auto-prompts
//  for biometrics/passcode on appear; a manual "Unlock" button re-prompts if dismissed.
//

import SwiftUI

struct AppLockView: View {
    @State private var appLock = AppLockManager.shared
    @State private var isAuthenticating = false

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            VStack(spacing: AppSpacing.xl) {
                Image("CaydexLogo")
                    .resizable()
                    .scaledToFit()
                    .frame(width: 96, height: 96)

                VStack(spacing: AppSpacing.xs) {
                    Text("Caydex is locked")
                        .font(AppTypography.titleCompact)
                        .foregroundColor(AppColors.textPrimary)
                    Text("Unlock with \(appLock.biometryLabel) to continue.")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                        .multilineTextAlignment(.center)
                }

                Button(action: unlock) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "lock.open.fill")
                            .font(AppTypography.iconSmall)
                        Text("Unlock")
                            .font(AppTypography.bodyEmphasis)
                    }
                    .foregroundColor(.white)
                    .padding(.horizontal, AppSpacing.xl)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.primaryBlue)
                    )
                }
                .buttonStyle(PlainButtonStyle())
                .disabled(isAuthenticating)
            }
            .padding(AppSpacing.xl)
        }
        .task { await authenticate() }
    }

    private func unlock() { Task { await authenticate() } }

    private func authenticate() async {
        guard !isAuthenticating else { return }
        isAuthenticating = true
        _ = await appLock.authenticate()
        isAuthenticating = false
    }
}

#Preview {
    AppLockView()
        .preferredColorScheme(.dark)
}
