//
//  ProfileView.swift
//  ios
//
//  Screen: Account & Settings Dashboard
//  Sections: Identity, Credits, Settings, About, Auth
//

import SwiftUI

struct ProfileView: View {
    @Environment(\.appState) private var appState
    @Environment(\.dismiss) private var dismiss
    @StateObject private var viewModel = ProfileViewModel()

    var body: some View {
        NavigationStack {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
                        // Section 1: User Identity & Tier
                        userIdentitySection

                        // Section 2: Credit Management
                        creditManagementSection

                        // Section 3: App Settings & Preferences
                        settingsSection

                        // Section 4: About & Legal
                        aboutSection

                        // Section 5: Sign Out
                        signOutSection

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
        .preferredColorScheme(.dark)
    }

    // MARK: - Section 1: User Identity & Tier

    private var userIdentitySection: some View {
        VStack(spacing: AppSpacing.lg) {
            // Avatar + Name + Email
            VStack(spacing: AppSpacing.md) {
                ProfileAvatarView(
                    avatarUrl: viewModel.avatarUrl,
                    size: 80
                )

                VStack(spacing: AppSpacing.xs) {
                    Text(viewModel.displayName)
                        .font(AppTypography.titleCompact)
                        .foregroundColor(AppColors.textPrimary)

                    Text(viewModel.email)
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.textSecondary)
                }

                // Membership Badge
                TierBadge(tier: viewModel.userTier)

                // Member Since
                Text(viewModel.memberSince)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
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
                        Text("Resets on Mar 1")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        Spacer()

                        Button(action: {
                            viewModel.addMoreCredits()
                        }) {
                            HStack(spacing: AppSpacing.xs) {
                                Image(systemName: "plus")
                                    .font(AppTypography.iconTiny).fontWeight(.bold)
                                Text("Add Credits")
                                    .font(AppTypography.captionEmphasis)
                            }
                            .foregroundColor(.white)
                            .padding(.horizontal, AppSpacing.md)
                            .padding(.vertical, AppSpacing.xs)
                            .background(
                                Capsule()
                                    .fill(
                                        LinearGradient(
                                            colors: [Color(hex: "F97316"), Color(hex: "EA580C")],
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
                        .fill(AppColors.cardBackground)
                )

                // Upgrade CTA
                if viewModel.userTier == .free {
                    UpgradeCard()
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
                // Appearance Picker
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

                // Notifications
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
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Section 4: About & Legal

    private var aboutSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ProfileSectionHeader(title: "About & Legal", icon: "info.circle.fill")

            VStack(spacing: 0) {
                // Help & Support
                ProfileSettingsRow(
                    icon: "questionmark.circle.fill",
                    iconColor: AppColors.textSecondary,
                    title: "Help & Support",
                    showChevron: true
                ) {
                    openSupport()
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

                // Terms of Service
                ProfileSettingsRow(
                    icon: "doc.text.fill",
                    iconColor: AppColors.textSecondary,
                    title: "Terms of Service",
                    showChevron: true
                ) {
                    openTerms()
                }

                settingsRowDivider

                // Privacy Policy
                ProfileSettingsRow(
                    icon: "hand.raised.fill",
                    iconColor: AppColors.textSecondary,
                    title: "Privacy Policy",
                    showChevron: true
                ) {
                    openPrivacy()
                }
            }
            .background(AppColors.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))

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
                        .fill(AppColors.cardBackground)
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

    private func openFeedback() {
        // Will open feedback form / email
        if let url = URL(string: "mailto:feedback@caydex.com?subject=App%20Feedback") {
            UIApplication.shared.open(url)
        }
    }

    private func openSupport() {
        if let url = URL(string: "mailto:support@caydex.com?subject=Support%20Request") {
            UIApplication.shared.open(url)
        }
    }

    private func openTerms() {
        if let url = URL(string: "https://caydex.com/terms") {
            UIApplication.shared.open(url)
        }
    }

    private func openPrivacy() {
        if let url = URL(string: "https://caydex.com/privacy") {
            UIApplication.shared.open(url)
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
        case .premium: return "PREMIUM"
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
        Color(hex: "F97316"),
        Color(hex: "EA580C")
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
