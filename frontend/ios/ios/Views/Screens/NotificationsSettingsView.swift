//
//  NotificationsSettingsView.swift
//  ios
//
//  Screen: notification preferences.
//
//  THE INVARIANT, IN BOTH DIRECTIONS:
//
//    * every visible toggle has a real sender behind it, and
//    * every registered sender has a visible toggle.
//
//  Both are pinned by `backend/tests/test_push_preference_typing.py` against
//  `NotificationSettingsViewModel.groups` and `notification_kinds.NOTIFICATION_KINDS`.
//  Each direction has already failed in production:
//
//    * Twelve of the original thirteen toggles stored a preference NOTHING read, so they
//      had to be hidden — a control that tells the user something is happening when
//      nothing is.
//    * Then push shipped while the screen was still hidden, so users received alerts with
//      no in-app way to turn them off — only iOS Settings, which kills every type at once
//      and never re-prompts.
//
//  The rows live in the ViewModel's declarative `groups` manifest, not here, so the
//  source-scan guard reads one list rather than parsing SwiftUI.
//
//  ⚠️ `preferenceDefaults` / `preferenceStringDefaults` below are the DECLARED SOURCE OF
//  TRUTH for what an untouched preference means. `SettingsSyncManager.currentBlob()` omits
//  any key the user has never set, so for most users the BACKEND default is what the
//  toggle means — and `notification_kinds.preference_defaults()` mirrors these exactly.
//

import SwiftUI
import UserNotifications

struct NotificationsSettingsView: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel = NotificationSettingsViewModel()

    // MARK: - Declared defaults

    /// Declared default for every BOOLEAN notification preference key.
    ///
    /// ⚠️ The backend MIRRORS the false entries in `PushDispatchService._PREFERENCE_DEFAULTS`.
    /// A blanket `true` there was wrong for exactly these: `currentBlob()` omits any key the
    /// user never touched, so a user with no stored value would be opted INTO something the
    /// UI shows as off.
    static let preferenceDefaults: [String: Bool] = [
        // Watchlist
        "notify_watchlist_changes": true,
        "notify_price_alerts": true,

        // Earnings
        "notify_earnings_alerts": true,
        "notify_earnings_upcoming": true,
        "notify_earnings_surprises": true,

        // Smart Money
        "notify_smart_money": true,
        "notify_smart_money_whale": true,
        "notify_smart_money_insider": true,
        // Ships OFF: 13F data is up to 45 days stale by the time it is public, and one
        // filing touches dozens of positions — the easiest signal in the app to over-send.
        "notify_smart_money_institutional": false,

        // Topics you follow
        // Ships OFF: every other kind is triggered by something the user explicitly
        // tracks (a watchlist ticker, a threshold they typed, a report they paid for).
        // This one is DERIVED from stated interests, so it has to be opted into — a
        // notification nobody asked for is how the permission is lost permanently.
        "notify_profile_topics": false,

        // App activity
        "notify_research_complete": true,
        "notify_research_failed": true,

        // Quiet hours ship OFF: a delivery schedule the user never asked for is a
        // notification silently withheld, and "why didn't I get it?" is the hardest
        // question to answer about a push system.
        "notify_quiet_hours_enabled": false,

        // ── Synced but NOT rendered ─────────────────────────────────────────────
        // The "Market" group never got a sender. These keys are still synced by
        // SettingsSyncManager and still read by `preference_enabled`, so their defaults
        // must be declared — dropping `notify_market_volatility` here would silently opt
        // a user INTO something the (absent) UI declares as off. Mirrored in
        // `notification_kinds.LEGACY_PREFERENCE_DEFAULTS`.
        "notify_market_alerts": true,
        "notify_market_macro": true,
        "notify_market_volatility": false,
        "notify_market_sector": true,
    ]

    /// Declared default for every NON-Bool notification preference.
    ///
    /// Split from `preferenceDefaults` rather than widening it: that map's `[String: Bool]`
    /// type is what makes "which toggles ship off?" readable on both sides of the contract,
    /// and `_PREFERENCE_DEFAULTS` mirrors only its false entries.
    ///
    /// `notify_timezone` deliberately has NO static default — it is the device's own
    /// `TimeZone.current.identifier`, written on first change. The backend falls back to ET
    /// when it is absent, which is what every user got before quiet hours existed.
    static let preferenceStringDefaults: [String: String] = [
        "notify_quiet_start": "22:00",
        "notify_quiet_end": "07:00",
    ]

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: AppSpacing.xxl) {
                    NotificationPermissionBanner(status: viewModel.permission) {
                        viewModel.requestPermissionIfNeeded()
                    }

                    // Permission granted, device not registered — nothing below can be
                    // delivered. Previously indistinguishable from a healthy screen.
                    // `AppDelegate.applicationDidBecomeActive` re-registers on every
                    // foreground, so this usually clears itself; this covers the case where
                    // it does not, instead of lying about it.
                    if viewModel.deviceUnregistered {
                        InlineRetryNotice(
                            message: "This device isn't registered for notifications yet. "
                                + "Your choices below are saved, but nothing can be delivered "
                                + "until it is.",
                            systemImage: "exclamationmark.arrow.circlepath",
                            iconColor: AppColors.caution,
                            retryTitle: "Retry",
                            onRetry: {
                                Task {
                                    await PushNotificationManager.shared.registerIfAuthorized()
                                    try? await Task.sleep(nanoseconds: 800_000_000)
                                    await viewModel.refreshPermission()
                                }
                            }
                        )
                        .padding(.horizontal, AppSpacing.lg)
                    }

                    ForEach(NotificationSettingsViewModel.groups) { group in
                        groupCard(group)
                    }

                    quietHoursCard

                    footer

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
        .task {
            viewModel.load()
            await viewModel.refreshPermission()
        }
        // Re-read on foreground: the user may have just changed the permission in iOS
        // Settings, and coming back to a stale banner telling them to do what they just
        // did is worse than showing nothing.
        .onReceive(NotificationCenter.default.publisher(
            for: UIApplication.willEnterForegroundNotification
        )) { _ in
            Task { await viewModel.refreshPermission() }
        }
        // Belt and braces behind the ViewModel's debounced sync. `push()` no-ops when
        // nothing is dirty, so this costs nothing on a screen the user only read.
        .onDisappear { viewModel.pushNow() }
    }

    // MARK: - Group card

    @ViewBuilder
    private func groupCard(_ group: NotificationGroupSpec) -> some View {
        let suppressed = viewModel.isSuppressedByMaster(group)
        // A denied system permission dims EVERY row: a live-looking toggle that iOS is
        // guaranteed to override is the most confusing state this screen can present.
        let blocked = viewModel.systemNotificationsBlocked

        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.md) {
                Image(systemName: group.icon)
                    .font(AppTypography.iconMedium)
                    .foregroundColor(AppColors.alertPurple)
                    .frame(width: 36, height: 36)
                    .background(AppColors.alertPurple.opacity(0.15))
                    .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.medium))

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(group.title)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Text(group.subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer()

                if let master = group.masterKey {
                    Toggle("", isOn: viewModel.binding(for: master))
                        .labelsHidden()
                        .tint(AppColors.primaryBlue)
                        .disabled(blocked)
                        .opacity(blocked ? 0.4 : 1)
                        .accessibilityLabel("\(group.title) notifications")
                }
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .cardFill()
            )

            VStack(spacing: 1) {
                ForEach(group.rows) { row in
                    NotificationToggleRow(
                        title: row.title,
                        subtitle: row.subtitle,
                        isOn: viewModel.binding(for: row.key),
                        disabled: suppressed || blocked,
                        disabledReason: blocked
                            ? "Notifications are off for Caydex in iOS Settings"
                            : (suppressed ? "Turn on \(group.title) to enable this" : nil)
                    )
                }
            }
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
            // Card on the page background: an edge in light, nothing in dark.
            .cardBorder(cornerRadius: AppCornerRadius.large)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Quiet hours

    @ViewBuilder
    private var quietHoursCard: some View {
        let blocked = viewModel.systemNotificationsBlocked

        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.md) {
                Image(systemName: "moon.fill")
                    .font(AppTypography.iconMedium)
                    .foregroundColor(AppColors.accentCyan)
                    .frame(width: 36, height: 36)
                    .background(AppColors.accentCyan.opacity(0.15))
                    .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.medium))

                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Quiet Hours")
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                    Text("Hold alerts until morning")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer()

                Toggle("", isOn: Binding(
                    get: { viewModel.quietHoursEnabled },
                    set: { viewModel.setQuietHoursEnabled($0) }
                ))
                .labelsHidden()
                .tint(AppColors.primaryBlue)
                .disabled(blocked)
                .opacity(blocked ? 0.4 : 1)
                .accessibilityLabel("Quiet hours")
            }
            .padding(AppSpacing.lg)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .cardFill()
            )

            if viewModel.quietHoursEnabled {
                VStack(spacing: 1) {
                    // `blocked` is passed through, like every Toggle on this screen. Without
                    // it these two were the ONLY live-looking, fully interactive controls left
                    // when iOS notifications are denied — the user could carefully set a quiet
                    // window that cannot possibly take effect, next to rows dimmed to 0.4 for
                    // exactly that reason.
                    timeRow("From", blocked: blocked, selection: Binding(
                        get: { viewModel.quietStart },
                        set: { viewModel.setQuietStart($0) }
                    ))
                    timeRow("Until", blocked: blocked, selection: Binding(
                        get: { viewModel.quietEnd },
                        set: { viewModel.setQuietEnd($0) }
                    ))
                }
                .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
                .cardBorder(cornerRadius: AppCornerRadius.large)

                Text(
                    viewModel.quietWindowIsDegenerate
                        // The backend treats start == end as DISABLED rather than as
                        // "always quiet" — silencing the app forever with no error is the
                        // worse reading. Say so here so the user finds out on this screen
                        // instead of by never receiving a notification.
                        ? "Start and end are the same, so quiet hours are off. "
                          + "Pick two different times."
                        : "Alerts that arrive in this window are held and delivered when it "
                          + "ends — nothing is lost. Price alerts you set yourself and "
                          + "\u{201C}report ready\u{201D} still come through immediately."
                )
                .font(AppTypography.caption)
                .foregroundColor(
                    viewModel.quietWindowIsDegenerate ? AppColors.caution : AppColors.textMuted
                )
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, AppSpacing.xs)
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    @ViewBuilder
    private func timeRow(
        _ label: String,
        blocked: Bool,
        selection: Binding<Date>
    ) -> some View {
        HStack {
            Text(label)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
            Spacer()
            DatePicker("", selection: selection, displayedComponents: .hourAndMinute)
                .labelsHidden()
                .accessibilityLabel("Quiet hours \(label)")
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
        .background(AppColors.cardBackground)
        .disabled(blocked)
        // Dim the whole row, matching `NotificationToggleRow`. Never by swapping a fill:
        // that hides the site from the theme greps and can invert the ink.
        .opacity(blocked ? 0.4 : 1)
    }

    // MARK: - Footer

    private var footer: some View {
        Text("Caydex limits how many alerts you get per day in each category, so a busy "
             + "market doesn't fill your lock screen.")
            .font(AppTypography.caption)
            .foregroundColor(AppColors.textSecondary)
            .multilineTextAlignment(.center)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, AppSpacing.xl)
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        NotificationsSettingsView()
    }
}
