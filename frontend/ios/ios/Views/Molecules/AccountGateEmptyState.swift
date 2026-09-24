//
//  AccountGateEmptyState.swift
//  ios
//
//  Molecule: the centered "this needs your account" state for a gated surface that has
//  nothing it can render because the session is not armed.
//

import SwiftUI

/// The state a gated tab shows when it cannot render its content because the session is not
/// armed — either there is no credential, or a stored one has not been validated yet.
///
/// WHY THIS EXISTS
/// ---------------
/// TestFlight, build 1.0 (7), Home: *"Just the sign in button as in reports if users don't sign
/// in. Apply for the rest."* The screenshot shows a single thin banner — an orange
/// wifi-exclamation glyph and the sentence "Sign in to use this feature." — over a completely
/// blank page, with nothing to tap. That banner is `HomeDashboardView.errorBanner`, i.e. the
/// GENERIC NETWORK-FAILURE affordance, because the ViewModel had already flattened
/// `AppError.signInRequired` down to its `.message` string and the view could no longer tell the
/// two apart.
///
/// Research › Reports, meanwhile, had the deliberate version: glyph, headline, subtitle, and a
/// filled Sign In button. So did Tracking › Alerts, in a third shape. Updates had a fourth
/// ("Couldn't load the news" over a Try Again button that re-fired the refused request forever).
/// This is that state, once.
///
/// ## The two modes are an ENUM, not a Bool plus an optional closure
///
/// `auth.md` §5: a stored-but-unvalidated credential means the user **is** signed in as far as
/// they are concerned, and `AppState.requestSignIn` deliberately declines to prompt while a
/// restore is pending — so a Sign In button in that window is both a false statement and inert.
/// That invariant was previously carried by a hand-written comment in five separate places
/// (`ReportsListSection`, `AlertsTabContent` ×2, `CreditHistoryView`, `PriceAlertStore`). Here
/// `.reconnecting` carries no closure, so the button cannot be wired into it by mistake — the
/// compiler holds the rule instead of a reviewer.
///
/// ## The COPY stays with the caller
///
/// "Sign in to see your analyses" and "Sign in to see your watchlist and portfolio" are
/// different promises, and one shared string would have to be wrong about one of them. The
/// molecule owns the LAYOUT; every call site owns its own sentence and its own
/// `requestSignIn(for:)` feature phrase.
///
/// Pinned by `backend/tests/test_ios_account_gate_state.py`.
struct AccountGateEmptyState: View {

    enum Mode {
        /// No usable credential. The caller's closure raises `AppState.requestSignIn(for:)`.
        case signedOut(onSignIn: () -> Void)

        /// A credential is stored but not armed yet. Deliberately carries NO action: the session
        /// heals itself on launch, foreground, network-restored and a bounded backoff, and
        /// `requestSignIn` refuses to prompt here anyway.
        case reconnecting
    }

    /// Drawn in `.signedOut` only. `.reconnecting` shows a spinner instead — that state is a
    /// process in flight, not a fact about the account.
    var systemImage: String = "person.crop.circle.badge.checkmark"
    let headline: String
    let subtitle: String
    let mode: Mode

    var body: some View {
        // Combined into ONE accessibility element while reconnecting (there is no control to
        // reach, and five separate labels is noise). Deliberately NOT combined in `.signedOut`:
        // that would swallow the Sign In button, which is the only control on the screen.
        if case .reconnecting = mode {
            stack
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(headline). \(subtitle)")
        } else {
            stack
        }
    }

    private var stack: some View {
        VStack(spacing: AppSpacing.md) {
            switch mode {
            case .signedOut:
                Image(systemName: systemImage)
                    // `iconXXL`, not the raw `.system(size: 40)` this was extracted from —
                    // that literal does not scale with Dynamic Type.
                    .font(AppTypography.iconXXL)
                    .foregroundColor(AppColors.textMuted)

            case .reconnecting:
                ProgressView()
                    .controlSize(.large)
            }

            Text(headline)
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)

            Text(subtitle)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                // Without this the subtitle truncates to a single line inside a flexible
                // parent instead of wrapping — the omission the extracted original carried.
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, AppSpacing.xl)

            if case .signedOut(let onSignIn) = mode {
                Button(action: onSignIn) {
                    Text("Sign In")
                        .font(AppTypography.bodySmallEmphasis)
                        // `textOnAccent` is the ink `primaryFill` DECLARES (AppTheme's FILLS
                        // block). Never `textPrimary` or a bare `.white` here.
                        .foregroundColor(AppColors.textOnAccent)
                        .padding(.horizontal, AppSpacing.xl)
                        .padding(.vertical, AppSpacing.md)
                        .background(AppColors.primaryFill)
                        .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(PlainButtonStyle())
                .padding(.top, AppSpacing.xs)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.xxxl)
    }
}

#Preview("Signed out") {
    ZStack {
        AppColors.background.ignoresSafeArea()
        AccountGateEmptyState(
            headline: "Sign in to see your dashboard",
            subtitle: "Your watchlist, your signals and your markets are saved to your account, "
                + "so they follow you across devices.",
            mode: .signedOut(onSignIn: {})
        )
    }
}

#Preview("Reconnecting") {
    ZStack {
        AppColors.background.ignoresSafeArea()
        AccountGateEmptyState(
            headline: "Reconnecting…",
            subtitle: "Getting your dashboard. This usually takes a moment.",
            mode: .reconnecting
        )
    }
}
