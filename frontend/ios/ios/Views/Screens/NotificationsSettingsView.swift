//
//  NotificationsSettingsView.swift
//  ios
//
//  Screen: notification preferences.
//
//  ⚠️ ONE TOGGLE SHIPS, DELIBERATELY.
//
//  This screen used to render 13 toggles across four groups (Earnings, Market, Smart Money,
//  App Activity). Exactly ONE of them is wired to anything that sends:
//
//      updates_insight_sweeper.py:423  notify_watchers(preference_key: "notify_watchlist_changes")
//        -> push_dispatch_service.py:283  preference_enabled(user_id, key)
//          -> push_dispatch_service.py:305  PushService.send_to_user(...)
//
//  The other twelve wrote an @AppStorage key, synced it to the backend, and were read by
//  nothing. That is the exact failure `FeatureFlags.notificationPreferencesEnabled` was
//  invented to prevent — "a toggle that stores a preference nothing ever reads" — so shipping
//  all 13 to un-hide the working one would have recreated it at a larger scale.
//
//  The flag is now TRUE because push genuinely delivers (all four APNS_* signing vars are set
//  in production and the sweeper has called notify_watchers since 2026-08-01). Leaving it
//  false meant users were prompted for permission during onboarding, received alerts, and had
//  NO in-app way to turn them off — only iOS Settings, which kills every type at once and
//  never re-prompts.
//
//  TO RESTORE A GROUP: wire its key to a real sender first (a `preference_key=` argument on a
//  notify_* call), then add its rows back here. The keys below are still synced by
//  SettingsSyncManager and still honoured by push_dispatch_service.preference_enabled, so
//  nothing else needs changing.
//

import SwiftUI

struct NotificationsSettingsView: View {
    @Environment(\.dismiss) private var dismiss

    // MARK: - Notification Toggles

    /// The ONLY preference with a sender behind it. `preference_enabled` defaults to TRUE when
    /// the key is absent (push_dispatch_service.py:118), so this default matches the backend —
    /// a user who never opens this screen is opted in, and flipping it off is honoured.
    @AppStorage("notify_watchlist_changes") private var watchlistChanges: Bool = true

    /// Declared default for EVERY notification preference key — including the twelve whose UI
    /// is hidden pending a sender.
    ///
    /// These keys did not stop existing when their toggles were hidden: `SettingsSyncManager`
    /// still syncs them, and `PushDispatchService.preference_enabled` still reads them. So the
    /// defaults still have to be recorded somewhere, and the `@AppStorage` declarations that
    /// used to carry them are gone.
    ///
    /// ⚠️ The backend MIRRORS the false entries in `PushDispatchService._PREFERENCE_DEFAULTS`.
    /// A blanket `true` there was wrong for exactly these two: `currentBlob()` omits any key
    /// the user never touched, so a user with no stored value would be opted INTO something
    /// the UI shows as off the moment a dispatcher is wired up.
    /// `tests/test_push_preference_typing.py` pins the two sides together.
    static let preferenceDefaults: [String: Bool] = [
        // Live — rendered above.
        "notify_watchlist_changes": true,

        // Hidden pending a sender. Keep the values; they are what a user would get if the
        // corresponding dispatcher were wired up tomorrow.
        "notify_earnings_alerts": true,
        "notify_earnings_surprises": true,
        "notify_earnings_upcoming": true,
        "notify_market_alerts": true,
        "notify_market_macro": true,
        "notify_market_volatility": false,
        "notify_market_sector": true,
        "notify_smart_money": true,
        "notify_smart_money_whale": true,
        "notify_smart_money_insider": true,
        "notify_smart_money_institutional": false,
        "notify_research_complete": true,
    ]

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: AppSpacing.xxl) {
                    // App Activity — the only group with a live sender behind it.
                    notificationGroup(
                        title: "App Activity",
                        subtitle: "Watchlist updates",
                        icon: "app.badge.fill",
                        iconColor: AppColors.alertPurple,
                        masterToggle: .constant(true),
                        showMasterToggle: false
                    ) {
                        NotificationToggleRow(
                            title: "Watchlist Price Changes",
                            subtitle: "Unusual moves in stocks you track",
                            isOn: $watchlistChanges
                        )
                    }

                    Text("More notification types will appear here as they go live. "
                         + "To turn off all notifications, use iOS Settings › Caydex.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity)
                        .padding(.horizontal, AppSpacing.xl)

                    Spacer()
                        .frame(height: AppSpacing.xxxl)
                }
                .padding(.top, AppSpacing.md)
            }
        }
        .navigationTitle("Notifications")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        // Ask for push permission when the user opens Notifications (iOS prompts once).
        .onAppear { PushNotificationManager.shared.requestAuthorization() }
        // Sync the toggles to the backend when leaving (no-op for guests).
        .onDisappear { SettingsSyncManager.shared.push() }
    }

    // MARK: - Notification Group Builder

    @ViewBuilder
    private func notificationGroup<Content: View>(
        title: String,
        subtitle: String,
        icon: String,
        iconColor: Color,
        masterToggle: Binding<Bool>,
        showMasterToggle: Bool = true,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Group Header with master toggle
            HStack(spacing: AppSpacing.md) {
                Image(systemName: icon)
                    .font(AppTypography.iconMedium)
                    .foregroundColor(iconColor)
                    .frame(width: 36, height: 36)
                    .background(iconColor.opacity(0.15))
                    .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.medium))

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(title)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Text(subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer()

                if showMasterToggle {
                    Toggle("", isOn: masterToggle)
                        .labelsHidden()
                        .tint(AppColors.primaryBlue)
                }
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .cardFill()
            )

            // Sub-toggles
            VStack(spacing: 1) {
                content()
            }
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
            // Card on the page background: an edge in light, nothing in dark.
            .cardBorder(cornerRadius: AppCornerRadius.large)
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

// MARK: - Notification Toggle Row

struct NotificationToggleRow: View {
    let title: String
    let subtitle: String
    @Binding var isOn: Bool
    var disabled: Bool = false

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.body)
                    .foregroundColor(disabled ? AppColors.textMuted : AppColors.textPrimary)

                Text(subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            Spacer()

            Toggle("", isOn: $isOn)
                .labelsHidden()
                .tint(AppColors.primaryBlue)
                .disabled(disabled)
                .opacity(disabled ? 0.4 : 1)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        NotificationsSettingsView()
    }
}
