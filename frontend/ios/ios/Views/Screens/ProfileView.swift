//
//  ProfileView.swift
//  ios
//
//  Screen: Account & Settings Dashboard
//  Sections: Identity, Credits, Settings, About, Auth
//

import SwiftUI
import Combine

struct ProfileView: View {
    @Environment(\.appState) private var appState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel = ProfileViewModel()
    /// Presents the sign-in sheet (guest-first: sign-in is optional and offered here).
    @State private var showSignIn = false
    /// Working copy for the display-name editor, so cancelling leaves the profile alone.
    @State private var editedName = ""
    /// Presents the upgrade / plan paywall (from the Upgrade card + Add Credits).
    @State private var showPaywall = false
    /// Set when the feedback `mailto:` cannot be opened (no Mail app — always true on the
    /// Simulator). Drives an ALERT rather than relying on the global toast: `AppActions` does
    /// raise one, but `ToastView` is an `.overlay` on `RootView` (iosApp.swift) and this screen
    /// is presented as a `.fullScreenCover` ABOVE it, so that toast is drawn behind the cover
    /// and the user never sees it. Verified on the Simulator — the report fired and nothing
    /// appeared. An alert presents in this screen's own context, so it does.
    @State private var feedbackMailUnavailable = false

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
                        // Section 1: User Identity & Tier
                        userIdentitySection

                        // Section 2: Credit Management (signed-in only — guests are
                        // not credit-metered, so the balance is meaningless for them)
                        if viewModel.isAuthenticated {
                            creditManagementSection
                        }

                        // Section 3: App Settings & Preferences
                        settingsSection

                        // Section 4: About & Legal
                        aboutSection

                        // Section 5: Sign Out (only when signed in)
                        if viewModel.isAuthenticated {
                            signOutSection
                        }

                        Spacer()
                            .frame(height: AppSpacing.xxxl)
                    }
                    .padding(.top, AppSpacing.md)
                }
            }
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .principal) {
                    Text("Account")
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button(action: { dismiss() }) {
                        Image(systemName: "xmark")
                            .font(AppTypography.iconDefault).fontWeight(.semibold)
                            .foregroundColor(AppColors.textSecondary)
                    }
                }
            }
            .toolbarBackground(AppColors.background, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .onAppear {
                viewModel.appState = appState
                viewModel.loadData()
            }
        }
        .sheet(isPresented: $showSignIn) {
            SignInView()
                .environment(appState)
        }
        .alert("Display Name", isPresented: $viewModel.isEditingName) {
            TextField("Your name", text: $editedName)
                .textInputAutocapitalization(.words)
                .autocorrectionDisabled()
            Button("Cancel", role: .cancel) {}
            Button("Save") { viewModel.saveDisplayName(editedName) }
        } message: {
            Text("This is the name shown on your profile.")
        }
        .alert("No Email App", isPresented: $feedbackMailUnavailable) {
            Button("Copy Address") {
                UIPasteboard.general.string = SupportView.supportEmail
            }
            Button("OK", role: .cancel) {}
        } message: {
            Text("This device has no app set up to send email. Write to \(SupportView.supportEmail) from wherever you read mail.")
        }
        .sheet(isPresented: $showPaywall) {
            PaywallView()
                .environment(\.appState, appState)
        }
        .onChange(of: appState.auth.status) { _, newStatus in
            // Auth just succeeded from the sheet → dismiss it and refresh
            // the now-real profile + credits.
            if newStatus == .authenticated {
                showSignIn = false
                viewModel.loadData()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .caydexSettingsHydrated)) { _ in
            // Settings just synced from the server → reflect any appearance change
            // in the picker (otherwise it shows the pre-sign-in selection).
            viewModel.syncAppearanceFromStore()
        }
    }

    // MARK: - Section 1: User Identity & Tier

    private var userIdentitySection: some View {
        VStack(spacing: AppSpacing.lg) {
            VStack(spacing: AppSpacing.md) {
                ProfileAvatarView(
                    avatarUrl: viewModel.avatarUrl,
                    size: 80
                )

                if viewModel.isAuthenticated {
                    // Signed in: real name / email / tier / member-since.
                    VStack(spacing: AppSpacing.xs) {
                        // Tap the name to rename. PATCH /users/me existed on both sides
                        // but nothing called it, so the name was read-only in the app.
                        Button {
                            editedName = viewModel.displayName
                            viewModel.isEditingName = true
                        } label: {
                            HStack(spacing: AppSpacing.xs) {
                                Text(viewModel.displayName)
                                    .font(AppTypography.titleCompact)
                                    .foregroundColor(AppColors.textPrimary)
                                Image(systemName: "pencil")
                                    .font(AppTypography.iconXS)
                                    .foregroundColor(AppColors.textMuted)
                            }
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Edit display name. Currently \(viewModel.displayName)")

                        Text(viewModel.email)
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textSecondary)
                    }

                    TierBadge(tier: viewModel.userTier)

                    Text(viewModel.memberSince)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                } else {
                    // Guest: prompt to sign in.
                    VStack(spacing: AppSpacing.xs) {
                        Text("Guest")
                            .font(AppTypography.titleCompact)
                            .foregroundColor(AppColors.textPrimary)

                        Text("Sign in to sync your account and unlock monthly credits.")
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textSecondary)
                            .multilineTextAlignment(.center)
                            .fixedSize(horizontal: false, vertical: true)
                    }

                    Button(action: { showSignIn = true }) {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "person.crop.circle.badge.plus")
                                .font(AppTypography.iconSmall)
                            Text("Sign In")
                                .font(AppTypography.bodyEmphasis)
                        }
                        .foregroundColor(AppColors.textOnAccent)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, AppSpacing.md)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                                .fill(AppColors.primaryFill)
                        )
                    }
                    .buttonStyle(PlainButtonStyle())
                    .padding(.top, AppSpacing.xs)
                }
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Section 2: Credit Management

    private var creditManagementSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ProfileSectionHeader(title: "Credit Management", icon: "creditcard.fill")

            VStack(spacing: AppSpacing.sm) {
                // Usage Card
                VStack(alignment: .leading, spacing: AppSpacing.md) {
                    HStack {
                        Text("Monthly Credits")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.textPrimary)

                        Spacer()

                        Text("\(viewModel.creditsUsed)/\(viewModel.creditsTotal)")
                            .font(AppTypography.dataHeading)
                            .foregroundColor(AppColors.primaryBlue)
                    }

                    // Progress Bar
                    GeometryReader { geometry in
                        ZStack(alignment: .leading) {
                            RoundedRectangle(cornerRadius: 4)
                                .fill(AppColors.cardBackgroundLight)
                                .frame(height: 8)

                            RoundedRectangle(cornerRadius: 4)
                                .fill(usageBarColor)
                                .frame(
                                    width: geometry.size.width * min(viewModel.creditUsagePercent, 1.0),
                                    height: 8
                                )
                        }
                    }
                    .frame(height: 8)

                    // Reset Date + Add Credits
                    HStack {
                        Text(viewModel.creditResetLabel)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        Spacer()

                        Button(action: {
                            showPaywall = true
                        }) {
                            HStack(spacing: AppSpacing.xs) {
                                Image(systemName: "plus")
                                    .font(AppTypography.iconTiny).fontWeight(.bold)
                                Text("Add Credits")
                                    .font(AppTypography.captionEmphasis)
                            }
                            .foregroundColor(AppColors.textOnAccent)
                            .padding(.horizontal, AppSpacing.md)
                            .padding(.vertical, AppSpacing.xs)
                            .background(
                                Capsule()
                                    .fill(
                                        LinearGradient(
                                            colors: [AppColors.alertOrange, AppColors.alertOrange],
                                            startPoint: .leading,
                                            endPoint: .trailing
                                        )
                                    )
                            )
                        }
                        .buttonStyle(PlainButtonStyle())
                    }
                }
                .padding(AppSpacing.lg)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .cardFill()
                )

                // Upgrade CTA
                if viewModel.userTier == .free {
                    Button(action: { showPaywall = true }) {
                        UpgradeCard()
                    }
                    .buttonStyle(PlainButtonStyle())
                }

            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Section 3: App Settings

    private var settingsSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ProfileSectionHeader(title: "Settings & Preferences", icon: "gearshape.fill")

            VStack(spacing: 0) {
                // Appearance Picker — restored now the forced-dark sweep is done
                // (per-view `.preferredColorScheme(.dark)` removed + adaptive AppColors).
                // Gated on the flag so it can be killed instantly if a regression appears.
                if FeatureFlags.appearanceModeEnabled {
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        HStack(spacing: AppSpacing.md) {
                            Image(systemName: "eye")
                                .font(AppTypography.iconDefault)
                                .foregroundColor(AppColors.textSecondary)
                                .frame(width: 28, height: 28)

                            Text("Appearance")
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)
                        }

                        HStack(spacing: 2) {
                            ForEach(AppearanceMode.allCases) { mode in
                                Button {
                                    viewModel.appearanceMode = mode
                                } label: {
                                    HStack(spacing: AppSpacing.xs) {
                                        Image(systemName: mode.icon)
                                            .font(AppTypography.iconXS)
                                        Text(mode.rawValue)
                                            .font(AppTypography.caption)
                                    }
                                    .frame(maxWidth: .infinity)
                                    .padding(.vertical, AppSpacing.xs)
                                    .background(
                                        Capsule()
                                            .fill(viewModel.appearanceMode == mode
                                                  ? AppColors.textMuted.opacity(0.3)
                                                  : Color.clear)
                                    )
                                    .foregroundColor(viewModel.appearanceMode == mode
                                                     ? AppColors.textPrimary
                                                     : AppColors.textMuted)
                                }
                                .buttonStyle(PlainButtonStyle())
                            }
                        }
                        .padding(2)
                        .background(
                            Capsule()
                                .fill(AppColors.textMuted.opacity(0.1))
                        )
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.md)

                    settingsRowDivider
                }

                // Notifications — hidden until a delivery pipeline exists. Every toggle
                // in there currently writes a preference that nothing reads
                // (`push_service.send_to_user` has zero callers). See FeatureFlags.
                if FeatureFlags.notificationPreferencesEnabled {
                    NavigationLink {
                        NotificationsSettingsView()
                    } label: {
                        ProfileSettingsRowContent(
                            icon: "bell.badge.fill",
                            iconColor: AppColors.textSecondary,
                            title: "Notifications"
                        )
                    }

                    settingsRowDivider
                }

                // General Settings
                NavigationLink {
                    AppSettingsView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "slider.horizontal.3",
                        iconColor: AppColors.textSecondary,
                        title: "General Settings"
                    )
                }

                settingsRowDivider

                // Feedback
                ProfileSettingsRow(
                    icon: "bubble.left.and.bubble.right.fill",
                    iconColor: AppColors.textSecondary,
                    title: "Help Us Improve",
                    showChevron: true
                ) {
                    openFeedback()
                }
            }
            .background(AppColors.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
            // Card on the page background: an edge in light, nothing in dark.
            .cardBorder(cornerRadius: AppCornerRadius.large)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Section 4: About & Legal

    private var aboutSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ProfileSectionHeader(title: "About & Legal", icon: "info.circle.fill")

            VStack(spacing: 0) {
                // Help & Support — a native screen, like the four rows below it.
                //
                // This was a bare `mailto:` handler, which meant the row did NOTHING on the
                // Simulator (no Mail app to hand off to) and on any device without a mail
                // account. It was also the only row here that tried to leave the app. Email is
                // still offered, inside SupportView, on a screen that works without it.
                NavigationLink {
                    SupportView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "questionmark.circle.fill",
                        iconColor: AppColors.textSecondary,
                        title: "Help & Support"
                    )
                }

                settingsRowDivider

                // Disclaimers
                NavigationLink {
                    DisclaimersView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "exclamationmark.shield.fill",
                        iconColor: AppColors.textSecondary,
                        title: "Disclaimers"
                    )
                }

                settingsRowDivider

                // Data Sources — attribution for every upstream market-data provider.
                NavigationLink {
                    DataSourcesView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "chart.bar.doc.horizontal.fill",
                        iconColor: AppColors.textSecondary,
                        title: "Data Sources"
                    )
                }

                settingsRowDivider

                // Terms of Use (native in-app screen)
                NavigationLink {
                    TermsOfUseView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "doc.text.fill",
                        iconColor: AppColors.textSecondary,
                        title: "Terms of Use"
                    )
                }

                settingsRowDivider

                // Privacy Policy (native in-app screen)
                NavigationLink {
                    PrivacyPolicyView()
                } label: {
                    ProfileSettingsRowContent(
                        icon: "hand.raised.fill",
                        iconColor: AppColors.textSecondary,
                        title: "Privacy Policy"
                    )
                }
            }
            .background(AppColors.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
            // Card on the page background: an edge in light, nothing in dark.
            .cardBorder(cornerRadius: AppCornerRadius.large)

            // App Version
            HStack {
                Spacer()
                Text("Caydex v\(appVersion) (\(buildNumber))")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                Spacer()
            }
            .padding(.top, AppSpacing.xs)
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Section 5: Sign Out

    private var signOutSection: some View {
        VStack(spacing: AppSpacing.md) {
            Button(action: {
                viewModel.showSignOutConfirmation = true
            }) {
                HStack(spacing: AppSpacing.sm) {
                    Image(systemName: "rectangle.portrait.and.arrow.right")
                        .font(AppTypography.iconDefault).fontWeight(.semibold)

                    Text("Sign Out")
                        .font(AppTypography.bodyEmphasis)
                }
                .foregroundColor(AppColors.bearish)
                .frame(maxWidth: .infinity)
                .padding(.vertical, AppSpacing.lg)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .cardFill()
                )
            }
            .buttonStyle(PlainButtonStyle())
            .alert("Sign Out", isPresented: $viewModel.showSignOutConfirmation) {
                Button("Cancel", role: .cancel) {}
                Button("Sign Out", role: .destructive) {
                    viewModel.signOut()
                    dismiss()
                }
            } message: {
                Text("Are you sure you want to sign out? You'll need to sign in again to access your account.")
            }
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Helpers

    /// Inset divider that starts after the icon column (like iOS Settings)
    private var settingsRowDivider: some View {
        Divider()
            .overlay(AppColors.textMuted.opacity(0.3))
            .padding(.leading, AppSpacing.lg + 28 + AppSpacing.md)
    }

    private var usageBarColor: Color {
        if viewModel.creditUsagePercent > 0.9 { return AppColors.bearish }
        if viewModel.creditUsagePercent > 0.7 { return AppColors.neutral }
        return AppColors.primaryBlue
    }

    private var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
    }

    private var buildNumber: String {
        Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "1"
    }

    // Feedback still hands off to Mail — `SFSafariViewController` accepts http/https ONLY, so
    // a `mailto:` cannot go through the in-app browser. What it must NOT do is call
    // `UIApplication.shared.open(url)` bare: that discards the result, and the Simulator has no
    // Mail app, so the row silently did nothing. `openInSystem` reports the failure instead.
    //
    // Help & Support no longer lives here at all — it is `SupportView`, a native screen, which
    // is why there is only one handler left.

    private func openFeedback() {
        // `support@`, NOT `feedback@`. Cloudflare Email Routing carries only support@,
        // copyright@ and privacy@ (LAUNCH_CHECKLIST §2) — feedback@ has no route, so every
        // message sent from this button bounced or vanished. The subject line keeps the two
        // streams separable in the one inbox.
        guard let url = URL(
            string: "mailto:\(SupportView.supportEmail)?subject=App%20Feedback"
        ) else { return }
        openInSystem(url, action: "open your email app") {
            feedbackMailUnavailable = true
        }
    }
}

// MARK: - Tier Badge

struct TierBadge: View {
    let tier: UserTier

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: tierIcon)
                .font(AppTypography.iconXS).fontWeight(.bold)

            Text(tierLabel)
                .font(AppTypography.captionEmphasis)
        }
        .foregroundColor(tierTextColor)
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.xs + 2)
        .background(
            Capsule()
                .fill(tierBackgroundColor)
                .overlay(
                    Capsule()
                        .stroke(tierBorderColor, lineWidth: 1)
                )
        )
    }

    private var tierLabel: String {
        switch tier {
        case .free: return "FREE"
        case .pro: return "PRO"
        case .premium: return "MAX"   // 'premium' enum is displayed as "Max" (plan_credits.display_name)
        }
    }

    private var tierIcon: String {
        switch tier {
        case .free: return "person.fill"
        case .pro: return "bolt.fill"
        case .premium: return "crown.fill"
        }
    }

    private var tierTextColor: Color {
        switch tier {
        case .free: return AppColors.textSecondary
        case .pro: return AppColors.primaryBlue
        case .premium: return AppColors.accentYellow
        }
    }

    private var tierBackgroundColor: Color {
        switch tier {
        case .free: return AppColors.textSecondary.opacity(0.1)
        case .pro: return AppColors.primaryBlue.opacity(0.15)
        case .premium: return AppColors.accentYellow.opacity(0.12)
        }
    }

    private var tierBorderColor: Color {
        switch tier {
        case .free: return AppColors.textSecondary.opacity(0.2)
        case .pro: return AppColors.primaryBlue.opacity(0.3)
        case .premium: return AppColors.accentYellow.opacity(0.3)
        }
    }
}

// MARK: - Profile Stat Item

struct ProfileStatItem: View {
    let value: String
    let label: String
    let icon: String

    var body: some View {
        VStack(spacing: AppSpacing.xs) {
            Image(systemName: icon)
                .font(AppTypography.iconSmall)
                .foregroundColor(AppColors.primaryBlue)

            Text(value)
                .font(AppTypography.dataLarge)
                .foregroundColor(AppColors.textPrimary)

            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity)
    }
}

// MARK: - Section Header

struct ProfileSectionHeader: View {
    let title: String
    let icon: String

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: icon)
                .font(AppTypography.iconSmall)
                .foregroundColor(AppColors.textMuted)

            Text(title)
                .font(AppTypography.labelSmallEmphasis)
                .foregroundColor(AppColors.textMuted)
                .textCase(.uppercase)
                .tracking(0.5)
        }
        .padding(.leading, AppSpacing.xs)
    }
}

// MARK: - Credit Info Pill

struct CreditInfoPill: View {
    let label: String
    let value: String
    let color: Color

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)

            Text("\(label): \(value)")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
    }
}

// MARK: - Upgrade Card

struct UpgradeCard: View {
    private let gradientColors = [
        AppColors.alertOrange,
        AppColors.alertOrange
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    HStack(spacing: AppSpacing.xs) {
                        Image(systemName: "bolt.fill")
                            .font(AppTypography.iconSmall)
                            .foregroundColor(AppColors.textPrimary)

                        Text("Upgrade Plan")
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                    }

                    Text("Unlock your investing potential with priority AI and advanced analytics.")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textPrimary.opacity(0.8))
                        .lineLimit(2)
                }

                Spacer()

                Image(systemName: "chevron.right")
                    .font(AppTypography.iconSmall).fontWeight(.semibold)
                    .foregroundColor(AppColors.textPrimary.opacity(0.8))
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(
                    LinearGradient(
                        colors: gradientColors,
                        startPoint: .topLeading,
                        endPoint: .bottomTrailing
                    )
                )
        )
    }
}

// MARK: - Settings Row (Tappable)

enum RoundedCornerPosition {
    case top, bottom, all, none
}

struct ProfileSettingsRow: View {
    let icon: String
    let iconColor: Color
    let title: String
    var subtitle: String? = nil
    var showChevron: Bool = true
    var roundedCorners: RoundedCornerPosition = .none
    var action: () -> Void = {}

    var body: some View {
        Button(action: action) {
            ProfileSettingsRowContent(
                icon: icon,
                iconColor: iconColor,
                title: title,
                subtitle: subtitle
            )
        }
        .buttonStyle(PlainButtonStyle())
        .background(AppColors.cardBackground)
        .clipShape(
            UnevenRoundedRectangle(
                topLeadingRadius: roundedCorners == .top || roundedCorners == .all ? AppCornerRadius.large : 0,
                bottomLeadingRadius: roundedCorners == .bottom || roundedCorners == .all ? AppCornerRadius.large : 0,
                bottomTrailingRadius: roundedCorners == .bottom || roundedCorners == .all ? AppCornerRadius.large : 0,
                topTrailingRadius: roundedCorners == .top || roundedCorners == .all ? AppCornerRadius.large : 0
            )
        )
    }
}

// MARK: - Settings Row Content (for NavigationLink usage)

struct ProfileSettingsRowContent: View {
    let icon: String
    let iconColor: Color
    let title: String
    var subtitle: String? = nil

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            Image(systemName: icon)
                .font(AppTypography.iconDefault)
                .foregroundColor(iconColor)
                .frame(width: 28, height: 28)

            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)

                if let subtitle {
                    Text(subtitle)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            Spacer()

            Image(systemName: "chevron.right")
                .font(AppTypography.iconSmall).fontWeight(.semibold)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .contentShape(Rectangle())
    }
}

// MARK: - Preview

#Preview {
    ProfileView()
        .environment(AppState())
}
