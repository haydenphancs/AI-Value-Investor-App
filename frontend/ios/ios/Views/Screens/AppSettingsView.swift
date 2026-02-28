//
//  AppSettingsView.swift
//  ios
//
//  Screen: General app settings — currency, defaults, cache, delete account
//

import SwiftUI

struct AppSettingsView: View {
    @Environment(\.appState) private var appState
    @Environment(\.dismiss) private var dismiss

    @AppStorage("default_currency") private var defaultCurrency: String = "USD"
    @AppStorage("default_persona") private var defaultPersona: String = "buffett"
    @AppStorage("auto_refresh_quotes") private var autoRefreshQuotes: Bool = true
    @AppStorage("show_premarket") private var showPremarket: Bool = true
    @AppStorage("compact_numbers") private var compactNumbers: Bool = false
    @AppStorage("haptic_feedback") private var hapticFeedback: Bool = true

    @State private var showDeleteConfirmation: Bool = false
    @State private var showClearCacheConfirmation: Bool = false
    @State private var cacheSize: String = "Calculating..."

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: AppSpacing.xxl) {
                    // Market Defaults
                    settingsGroup(title: "Market Preferences", icon: "chart.xyaxis.line") {
                        // Currency
                        HStack {
                            settingsLabel(title: "Default Currency", subtitle: "For prices and valuations")

                            Spacer()

                            Picker("Currency", selection: $defaultCurrency) {
                                Text("USD ($)").tag("USD")
                                Text("EUR (\u{20AC})").tag("EUR")
                                Text("GBP (\u{00A3})").tag("GBP")
                                Text("JPY (\u{00A5})").tag("JPY")
                                Text("CAD (C$)").tag("CAD")
                            }
                            .tint(AppColors.primaryBlue)
                        }
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.md)
                        .background(AppColors.cardBackground)

                        SettingsToggleRow(
                            title: "Auto-Refresh Quotes",
                            subtitle: "Live price updates when app is open",
                            isOn: $autoRefreshQuotes
                        )

                        SettingsToggleRow(
                            title: "Pre/After-Market Data",
                            subtitle: "Show extended hours pricing",
                            isOn: $showPremarket
                        )

                        SettingsToggleRow(
                            title: "Compact Numbers",
                            subtitle: "Show 1.2B instead of 1,200,000,000",
                            isOn: $compactNumbers
                        )
                    }

                    // AI & Research
                    settingsGroup(title: "AI & Research", icon: "brain") {
                        HStack {
                            settingsLabel(title: "Default Analyst", subtitle: "Pre-selected for new research")

                            Spacer()

                            Picker("Persona", selection: $defaultPersona) {
                                Text("Buffett").tag("buffett")
                                Text("Lynch").tag("lynch")
                                Text("Wood").tag("wood")
                                Text("Ackman").tag("ackman")
                            }
                            .tint(AppColors.primaryBlue)
                        }
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.md)
                        .background(AppColors.cardBackground)
                    }

                    // General
                    settingsGroup(title: "General", icon: "wrench.and.screwdriver") {
                        SettingsToggleRow(
                            title: "Haptic Feedback",
                            subtitle: "Vibrations for interactions",
                            isOn: $hapticFeedback
                        )

                        // Clear Cache
                        Button(action: {
                            showClearCacheConfirmation = true
                        }) {
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
                    }

                    // Danger Zone
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

                        Button(action: {
                            showDeleteConfirmation = true
                        }) {
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
                                    .fill(AppColors.cardBackground)
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

                    Spacer()
                        .frame(height: AppSpacing.xxxl)
                }
                .padding(.top, AppSpacing.md)
            }
        }
        .navigationTitle("General Settings")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(AppColors.background, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
        .onAppear {
            calculateCacheSize()
        }
        .alert("Clear Cache", isPresented: $showClearCacheConfirmation) {
            Button("Cancel", role: .cancel) {}
            Button("Clear", role: .destructive) {
                clearCache()
            }
        } message: {
            Text("This will clear cached images and data. Your account, settings, and saved research will not be affected.")
        }
        .alert("Delete Account", isPresented: $showDeleteConfirmation) {
            Button("Cancel", role: .cancel) {}
            Button("Delete Forever", role: .destructive) {
                deleteAccount()
            }
        } message: {
            Text("This action is permanent and cannot be undone. All your research reports, watchlists, and settings will be deleted.")
        }
    }

    // MARK: - Settings Group Builder

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

            VStack(spacing: 1) {
                content()
            }
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

    // MARK: - Helpers

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

            DispatchQueue.main.async {
                cacheSize = formatted
            }
        }
    }

    private func clearCache() {
        let cacheURL = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
        guard let cacheURL else { return }

        try? FileManager.default.removeItem(at: cacheURL)
        try? FileManager.default.createDirectory(at: cacheURL, withIntermediateDirectories: true)
        calculateCacheSize()
    }

    private func deleteAccount() {
        // In production, call delete account API first
        appState.signOut()
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
            .environment(AppState())
    }
    .preferredColorScheme(.dark)
}
