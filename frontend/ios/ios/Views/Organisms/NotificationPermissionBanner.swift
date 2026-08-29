//
//  NotificationPermissionBanner.swift
//  ios
//
//  Tells the user when iOS is dropping every notification this screen offers.
//
//  WHY THIS EXISTS — before it, a user who had denied the system permission saw the
//  Notifications screen exactly as a granted user did: every toggle ON, every control
//  live, nothing amiss. The only acknowledgement anywhere was a static caption pointing
//  OUT of the app ("use iOS Settings › Caydex") that never pointed back in and never
//  appeared conditionally.
//
//  That state is unrecoverable without help: iOS prompts for notification permission
//  exactly ONCE, so `requestAuthorization()` from a `.denied` state is a silent no-op.
//  The only route back is the system Settings app, and the only way a user finds it is
//  if the app says so.
//

import SwiftUI
import UserNotifications

struct NotificationPermissionBanner: View {
    let status: UNAuthorizationStatus
    /// No account on this install — so nothing below can be delivered no matter what iOS
    /// says. Takes precedence over every permission state because it is the binding
    /// constraint: `device_tokens` is FK-bound to `public.users`, so a guest cannot hold a
    /// push token at all, and `/me/settings` is `.signInRequired` so their choices never
    /// reach the server either.
    ///
    /// ⚠️ Must be false while a session is merely RESTORING. `AuthStatus.restoring` means
    /// "we hold a credential we could not validate yet" and renders like a guest — telling
    /// that user to sign in is the exact defect auth.md §5 names, and the restore backoff
    /// runs indefinitely, so the window is not brief.
    var needsAccount: Bool = false
    /// Called when the user taps the primary button in the `needsAccount` state.
    var onSignIn: () -> Void = {}
    /// Called when the user taps the primary button in `.notDetermined`.
    var onEnable: () -> Void

    var body: some View {
        if needsAccount {
            // BEFORE the permission switch, deliberately. A guest in `.notDetermined`
            // would otherwise be invited to allow notifications that can never be sent —
            // spending the one-shot iOS prompt on a permission with nothing behind it.
            banner(
                icon: "person.crop.circle.badge.plus",
                tint: AppColors.primaryBlue,
                title: "Notifications need an account",
                message: "Alerts are delivered to your account, not to this device. "
                       + "Set your preferences here — they're saved and applied as soon "
                       + "as you sign in.",
                buttonTitle: "Sign In",
                action: onSignIn
            )
        } else {
            permissionBanner
        }
    }

    @ViewBuilder
    private var permissionBanner: some View {
        switch status {
        case .denied:
            banner(
                icon: "bell.slash.fill",
                tint: AppColors.caution,
                title: "Notifications are off for Caydex",
                message: "iOS is blocking every alert below. Turn them back on in Settings — "
                       + "the choices you make here are saved either way.",
                buttonTitle: "Open Settings",
                action: openSystemSettings
            )

        case .notDetermined:
            banner(
                icon: "bell.badge.fill",
                tint: AppColors.primaryBlue,
                title: "Turn on notifications",
                message: "Choose what you want to hear about below, then allow "
                       + "notifications so we can send them.",
                buttonTitle: "Allow Notifications",
                action: onEnable
            )

        case .provisional:
            // Provisional delivery is quiet: notifications land in Notification Center
            // with no banner and no sound. Worth saying, because "I get them but I never
            // see them" is otherwise a mystery.
            banner(
                icon: "bell.badge",
                tint: AppColors.primaryBlue,
                title: "Delivering quietly",
                message: "Alerts appear in Notification Center without a banner or sound. "
                       + "Open Settings to allow them to interrupt.",
                buttonTitle: "Open Settings",
                action: openSystemSettings
            )

        default:
            // .authorized / .ephemeral — nothing to say. A "notifications are on!" banner
            // would be permanent chrome that carries no information.
            EmptyView()
        }
    }

    // MARK: - Shell

    @ViewBuilder
    private func banner(
        icon: String,
        tint: Color,
        title: String,
        message: String,
        buttonTitle: String,
        action: @escaping () -> Void
    ) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.md) {
                Image(systemName: icon)
                    .font(AppTypography.iconMedium)
                    .foregroundColor(tint)
                    .frame(width: 36, height: 36)
                    .background(tint.opacity(0.15))
                    .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.medium))

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(title)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                    Text(message)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                Spacer(minLength: 0)
            }

            Button(action: action) {
                Text(buttonTitle)
                    .font(AppTypography.bodyEmphasis)
                    // On a saturated fill, `textOnAccent` — never `.white` and never
                    // `textPrimary`. White on a light-mode-safe accent collapses below AA.
                    .foregroundColor(AppColors.textOnAccent)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.md)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .fill(AppColors.primaryFill)
                    )
            }
            .buttonStyle(.plain)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
        .cardBorder(cornerRadius: AppCornerRadius.large)
        .padding(.horizontal, AppSpacing.lg)
    }

    /// Route through `openInSystem`, never a bare `UIApplication.shared.open`.
    ///
    /// `tests/test_ios_no_silent_url_open.py` walks every Swift file and fails the build
    /// on the bare call — because a failed open with no handler is a button that does
    /// nothing, silently, which is exactly the class of bug this banner exists to fix.
    private func openSystemSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        openInSystem(url, action: "open Settings")
    }
}

#Preview {
    VStack(spacing: AppSpacing.xl) {
        NotificationPermissionBanner(status: .notDetermined, needsAccount: true, onEnable: {})
        NotificationPermissionBanner(status: .denied, onEnable: {})
        NotificationPermissionBanner(status: .notDetermined, onEnable: {})
        NotificationPermissionBanner(status: .provisional, onEnable: {})
    }
    .padding(.vertical)
    .frame(maxWidth: .infinity, maxHeight: .infinity)
    .background(AppColors.background)
}
