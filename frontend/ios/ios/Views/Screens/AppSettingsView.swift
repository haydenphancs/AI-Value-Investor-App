//
//  AppSettingsView.swift
//  ios
//
//  Screen: General app settings — research defaults, playback, subscription,
//  security (App Lock), data, about, and account deletion.
//

import SwiftUI
import StoreKit

struct AppSettingsView: View {
    @Environment(\.appState) private var appState
    @Environment(\.dismiss) private var dismiss
    @Environment(\.requestReview) private var requestReview

    // Synced preferences (see SettingsSyncManager key lists).
    @AppStorage("default_persona") private var defaultPersona: String = AnalysisPersona.warrenBuffett.key
    @AppStorage("playback_speed") private var playbackSpeedRaw: Double = PlaybackSpeed.normal.rawValue
    @AppStorage("autoplay_next") private var autoplayNext: Bool = true
    @AppStorage("haptic_feedback") private var hapticFeedback: Bool = true

    // Device-local (NOT synced): App Lock security state.
    @State private var appLockEnabled: Bool = AppLockManager.shared.isEnabled

    @State private var showDeleteConfirmation = false
    @State private var showClearCacheConfirmation = false
    @State private var isRestoring = false
    @State private var restoreMessage: String?
    @State private var cacheSize = "Calculating..."

    /// Third-party AI processing consent. 5.1.1(ii)/5.1.2 require an accessible way to
    /// withdraw consent, so it is surfaced here rather than only at the first-send gate.
    @ObservedObject private var aiConsent = AIConsentStore.shared
    @State private var showWithdrawAIConsentConfirmation = false

    var body: some View {
        ZStack {
            AppColors.background.ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: AppSpacing.xxl) {
                    aiResearchSection
                    playbackSection
                    subscriptionSection
                    if AppLockManager.shared.isAvailable { securitySection }
                    generalSection
                    aboutSection
                    dangerZoneSection

                    Spacer().frame(height: AppSpacing.xxxl)
                }
                .padding(.top, AppSpacing.md)
            }
        }
        .navigationTitle("General Settings")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .onAppear { calculateCacheSize() }
        // Sync general prefs to the backend when leaving (no-op for guests).
        .onDisappear { SettingsSyncManager.shared.push() }
        .onChange(of: playbackSpeedRaw) { _, newValue in
            AudioManager.shared.playbackSpeed = PlaybackSpeed(rawValue: newValue) ?? .normal
        }
        .onChange(of: appLockEnabled) { _, newValue in
            // Ignore programmatic reverts (state already matches the manager).
            guard newValue != AppLockManager.shared.isEnabled else { return }
            Task {
                let ok = await AppLockManager.shared.setEnabled(newValue)
                if newValue && !ok { appLockEnabled = false }  // enable cancelled → revert
            }
        }
        .alert("Clear Cache", isPresented: $showClearCacheConfirmation) {
            Button("Cancel", role: .cancel) {}
            Button("Clear", role: .destructive) { clearCache() }
        } message: {
            Text("This will clear cached images and data. Your account, settings, and saved research will not be affected.")
        }
        .alert("Withdraw AI Chat Permission", isPresented: $showWithdrawAIConsentConfirmation) {
            Button("Cancel", role: .cancel) {}
            Button("Withdraw", role: .destructive) { aiConsent.withdraw() }
        } message: {
            Text("Cay AI chat will stop working until you allow it again. Everything else in Caydex keeps working. Conversations you've already saved are not deleted — you can remove those individually from chat history.")
        }
        .alert("Restore Purchases", isPresented: restoreFinished) {
            Button("OK", role: .cancel) { restoreMessage = nil }
        } message: {
            Text(restoreMessage ?? "")
        }
        .alert("Delete Account", isPresented: $showDeleteConfirmation) {
            Button("Cancel", role: .cancel) {}
            Button("Delete Forever", role: .destructive) { deleteAccount() }
        } message: {
            Text("This action is permanent and cannot be undone. All your research reports, watchlists, and settings will be deleted.")
        }
    }

    // MARK: - AI & Research

    private var aiResearchSection: some View {
        settingsGroup(title: "AI & Research", icon: "brain") {
            HStack {
                settingsLabel(title: "Default Analyst", subtitle: "Pre-selected for new research")
                Spacer()
                Picker("Analyst", selection: $defaultPersona) {
                    ForEach(AnalysisPersona.allCases) { persona in
                        Text(persona.name).tag(persona.key)
                    }
                }
                .tint(AppColors.primaryBlue)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .background(AppColors.cardBackground)
        }
    }

    // MARK: - Playback

    private var playbackSection: some View {
        settingsGroup(title: "Playback", icon: "headphones") {
            HStack {
                settingsLabel(title: "Default Speed", subtitle: "For book & lesson narration")
                Spacer()
                Picker("Speed", selection: $playbackSpeedRaw) {
                    ForEach(PlaybackSpeed.allCases) { speed in
                        Text(speed.label).tag(speed.rawValue)
                    }
                }
                .tint(AppColors.primaryBlue)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .background(AppColors.cardBackground)

            SettingsToggleRow(
                title: "Autoplay Next",
                subtitle: "Continue to the next track automatically",
                isOn: $autoplayNext
            )
        }
    }

    // MARK: - Subscription

    private var subscriptionSection: some View {
        settingsGroup(title: "Subscription", icon: "creditcard") {
            Button(action: openManageSubscription) {
                actionRow(title: "Manage Subscription",
                          subtitle: "Change or cancel in the App Store",
                          systemImage: "arrow.up.right.square")
            }
            .buttonStyle(PlainButtonStyle())

            Button {
                Task {
                    isRestoring = true
                    let count = await StoreKitService.shared.restorePurchases()
                    isRestoring = false
                    // Distinguish the two outcomes: "nothing to restore" is the common case
                    // for someone who never subscribed, and a generic success there just
                    // confuses them.
                    restoreMessage = count > 0
                        ? "Restored your subscription."
                        : "No previous purchases found for this Apple Account."
                }
            } label: {
                actionRow(title: "Restore Purchases",
                          subtitle: "Recover a subscription on this Apple ID",
                          systemImage: "arrow.clockwise")
            }
            .buttonStyle(PlainButtonStyle())
        }
    }

    // MARK: - Security

    private var securitySection: some View {
        settingsGroup(title: "Security", icon: "lock.shield") {
            SettingsToggleRow(
                title: "App Lock",
                subtitle: "Require \(AppLockManager.shared.biometryLabel) to open Caydex",
                isOn: $appLockEnabled
            )
        }
    }

    // MARK: - General

    private var generalSection: some View {
        settingsGroup(title: "General", icon: "wrench.and.screwdriver") {
            SettingsToggleRow(
                title: "Haptic Feedback",
                subtitle: "Vibrations for interactions",
                isOn: $hapticFeedback
            )

            Button(action: { showClearCacheConfirmation = true }) {
                HStack {
                    settingsLabel(title: "Clear Cache", subtitle: cacheSize)
                    Spacer()
                    Image(systemName: "trash")
                        .font(AppTypography.iconSmall)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)
                .background(AppColors.cardBackground)
                .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())

            // Change password — signed-in only. Guests have no password to change, and
            // recovery for signed-out users lives on the sign-in screen instead.
            if appState.auth.status == .authenticated {
                NavigationLink {
                    ChangePasswordView()
                } label: {
                    HStack {
                        settingsLabel(title: "Change Password", subtitle: "Update your account password")
                        Spacer()
                        Image(systemName: "chevron.right")
                            .font(AppTypography.iconXS)
                            .foregroundColor(AppColors.textMuted)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.md)
                    .background(AppColors.cardBackground)
                    .contentShape(Rectangle())
                }
                .buttonStyle(PlainButtonStyle())
            }

            // AI processing consent — shown only once granted; before that the first-send
            // gate is the surface. Withdrawing stops chat until it's granted again.
            if aiConsent.hasConsented {
                Button(action: { showWithdrawAIConsentConfirmation = true }) {
                    HStack {
                        settingsLabel(
                            title: "AI Chat Data Permission",
                            subtitle: aiConsent.grantedAt
                                .map { "Allowed \(Self.consentDateFormatter.string(from: $0))" }
                                ?? "Allowed"
                        )
                        Spacer()
                        Text("Withdraw")
                            .font(AppTypography.bodySmallEmphasis)
                            .foregroundColor(AppColors.bearish)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.md)
                    .background(AppColors.cardBackground)
                    .contentShape(Rectangle())
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
    }

    private var restoreFinished: Binding<Bool> {
        Binding(
            get: { restoreMessage != nil },
            set: { if !$0 { restoreMessage = nil } }
        )
    }

    private static let consentDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateStyle = .medium
        f.timeStyle = .none
        return f
    }()

    // MARK: - About

    private var aboutSection: some View {
        settingsGroup(title: "About", icon: "info.circle") {
            Button(action: { requestReview() }) {
                actionRow(title: "Rate the App",
                          subtitle: "Tell us what you think",
                          systemImage: "star")
            }
            .buttonStyle(PlainButtonStyle())

            NavigationLink {
                AcknowledgementsView()
            } label: {
                actionRow(title: "Acknowledgements",
                          subtitle: "Open-source licenses",
                          systemImage: "doc.plaintext")
            }

            HStack {
                settingsLabel(title: "Version", subtitle: "Caydex")
                Spacer()
                Text("\(appVersion) (\(buildNumber))")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .background(AppColors.cardBackground)
        }
    }

    // MARK: - Danger Zone

    private var dangerZoneSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.bearish)
                Text("DANGER ZONE")
                    .font(AppTypography.labelSmallEmphasis)
                    .foregroundColor(AppColors.bearish)
                    .tracking(0.5)
            }
            .padding(.leading, AppSpacing.xs)

            Button(action: { showDeleteConfirmation = true }) {
                HStack(spacing: AppSpacing.md) {
                    Image(systemName: "person.crop.circle.badge.xmark")
                        .font(AppTypography.iconDefault)
                        .foregroundColor(AppColors.bearish)
                        .frame(width: 28, height: 28)
                        .background(AppColors.bearish.opacity(0.15))
                        .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.small))

                    VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                        Text("Delete Account")
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.bearish)
                        Text("Permanently remove your account and all data")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)
                    }

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(AppTypography.iconSmall).fontWeight(.semibold)
                        .foregroundColor(AppColors.textMuted)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .cardFill()
                        .overlay(
                            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                                .stroke(AppColors.bearish.opacity(0.2), lineWidth: 1)
                        )
                )
                .contentShape(Rectangle())
            }
            .buttonStyle(PlainButtonStyle())
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Builders

    @ViewBuilder
    private func settingsGroup<Content: View>(
        title: String,
        icon: String,
        @ViewBuilder content: () -> Content
    ) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: icon)
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.textMuted)
                Text(title.uppercased())
                    .font(AppTypography.labelSmallEmphasis)
                    .foregroundColor(AppColors.textMuted)
                    .tracking(0.5)
            }
            .padding(.leading, AppSpacing.xs)

            VStack(spacing: 1) { content() }
                .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    @ViewBuilder
    private func settingsLabel(title: String, subtitle: String) -> some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            Text(title)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
            Text(subtitle)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }

    @ViewBuilder
    private func actionRow(title: String, subtitle: String, systemImage: String) -> some View {
        HStack {
            settingsLabel(title: title, subtitle: subtitle)
            Spacer()
            Image(systemName: systemImage)
                .font(AppTypography.iconSmall)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
        .contentShape(Rectangle())
    }

    // MARK: - Helpers

    private var appVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
    }

    private var buildNumber: String {
        Bundle.main.infoDictionary?["CFBundleVersion"] as? String ?? "1"
    }

    private func openManageSubscription() {
        if let url = URL(string: "https://apps.apple.com/account/subscriptions") {
            UIApplication.shared.open(url)
        }
    }

    private func calculateCacheSize() {
        let cacheURL = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
        guard let cacheURL else {
            cacheSize = "Unable to calculate"
            return
        }
        DispatchQueue.global(qos: .utility).async {
            var size: Int64 = 0
            if let enumerator = FileManager.default.enumerator(
                at: cacheURL,
                includingPropertiesForKeys: [.fileSizeKey],
                options: [.skipsHiddenFiles]
            ) {
                for case let fileURL as URL in enumerator {
                    if let fileSize = try? fileURL.resourceValues(forKeys: [.fileSizeKey]).fileSize {
                        size += Int64(fileSize)
                    }
                }
            }
            let formatter = ByteCountFormatter()
            formatter.countStyle = .file
            let formatted = formatter.string(fromByteCount: size)
            DispatchQueue.main.async { cacheSize = formatted }
        }
    }

    private func clearCache() {
        let cacheURL = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
        guard let cacheURL else { return }
        try? FileManager.default.removeItem(at: cacheURL)
        try? FileManager.default.createDirectory(at: cacheURL, withIntermediateDirectories: true)
        StockRepository.shared.clearCache()
        calculateCacheSize()
    }

    private func deleteAccount() {
        // Actually delete the account server-side (cascades all user data) BEFORE
        // signing out. Surface a failure instead of silently signing out.
        Task {
            do {
                try await appState.apiClient.request(endpoint: .deleteAccount)
                appState.signOut()
            } catch {
                appState.currentError = AppError.from(error)
            }
        }
    }
}

// MARK: - Settings Toggle Row

struct SettingsToggleRow: View {
    let title: String
    let subtitle: String
    @Binding var isOn: Bool

    var body: some View {
        HStack {
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(title)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)
                Text(subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
            Spacer()
            Toggle("", isOn: $isOn)
                .labelsHidden()
                .tint(AppColors.primaryBlue)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .background(AppColors.cardBackground)
    }
}

// MARK: - Preview

#Preview {
    NavigationStack {
        AppSettingsView()
            .environment(\.appState, AppState())
    }
}
