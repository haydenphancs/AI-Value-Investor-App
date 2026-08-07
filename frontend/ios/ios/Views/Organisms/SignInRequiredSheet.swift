//
//  SignInRequiredSheet.swift
//  ios
//
//  The one place the app says "this needs an account".
//
//  Before this existed, a sign-in-only action taken while signed out did nothing visible: the
//  request went out without a credential, came back refused, and the call site logged and
//  reverted. Tapping Follow on a whale filled the button in and then quietly emptied it again,
//  which reads as a broken app rather than a missing account.
//
//  Organism, not Molecule: it reads `@Environment(AppState.self)` and drives sign-in.
//

import SwiftUI

/// Modal shown when a DELIBERATE action needs an account.
///
/// The split is intentional. A deliberate tap gets this sheet — it interrupts, because the user
/// is waiting on an outcome and deserves a clear next step. A background or incidental failure
/// (a sync, a refresh) instead sets `AppState.currentError`, which renders the non-blocking
/// global toast whose action button opens the same sign-in screen. Interrupting someone for a
/// failure they did not initiate is how a fix becomes an annoyance.
struct SignInRequiredSheet: View {
    /// Verb phrase completing "Sign in to …", e.g. "follow investors".
    let feature: String?

    @Environment(AppState.self) private var appState
    @Environment(\.dismiss) private var dismiss

    @State private var showSignIn = false

    private var headline: String {
        if let feature, !feature.isEmpty {
            return "Sign in to \(feature)"
        }
        return "Sign in to continue"
    }

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            VStack(spacing: AppSpacing.lg) {
                Spacer(minLength: AppSpacing.xl)

                Image(systemName: "person.crop.circle.badge.checkmark")
                    .font(AppTypography.iconHero)
                    .foregroundColor(AppColors.primaryBlue)

                Text(headline)
                    .font(AppTypography.titleLarge)
                    .foregroundColor(AppColors.textPrimary)
                    .multilineTextAlignment(.center)

                Text("Your account keeps this in sync across your devices. Everything else in the app stays available without one.")
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
                    .multilineTextAlignment(.center)
                    .fixedSize(horizontal: false, vertical: true)

                Button {
                    showSignIn = true
                } label: {
                    HStack {
                        Spacer()
                        Text("Sign In")
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textOnAccent)
                        Spacer()
                    }
                    .padding(.vertical, 14)
                    .background(AppColors.primaryFill)
                    .cornerRadius(AppCornerRadius.medium)
                }

                Button {
                    dismiss()
                } label: {
                    Text("Not now")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer(minLength: AppSpacing.xl)
            }
            .padding(.horizontal, 24)
        }
        .sheet(isPresented: $showSignIn) {
            SignInView()
                .environment(appState)
        }
        // Close as soon as the user actually signs in, so they land back where they were
        // instead of on a prompt for something they have now done.
        .onChange(of: appState.auth.status) { _, newStatus in
            if newStatus == .authenticated {
                dismiss()
            }
        }
    }
}

#Preview {
    SignInRequiredSheet(feature: "follow investors")
        .environment(AppState())
}
