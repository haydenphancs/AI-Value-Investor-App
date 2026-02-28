//
//  NotificationsSettingsView.swift
//  ios
//
//  Screen: Notification preferences for Earnings, Market, and Smart Money alerts
//

import SwiftUI

struct NotificationsSettingsView: View {
    @Environment(\.dismiss) private var dismiss

    // MARK: - Notification Toggles

    @AppStorage("notify_earnings_alerts") private var earningsAlerts: Bool = true
    @AppStorage("notify_earnings_surprises") private var earningsSurprises: Bool = true
    @AppStorage("notify_earnings_upcoming") private var earningsUpcoming: Bool = true

    @AppStorage("notify_market_alerts") private var marketAlerts: Bool = true
    @AppStorage("notify_market_macro") private var marketMacro: Bool = true
    @AppStorage("notify_market_volatility") private var marketVolatility: Bool = false
    @AppStorage("notify_market_sector") private var marketSector: Bool = true

    @AppStorage("notify_smart_money") private var smartMoneyAlerts: Bool = true
    @AppStorage("notify_smart_money_whale") private var smartMoneyWhale: Bool = true
    @AppStorage("notify_smart_money_insider") private var smartMoneyInsider: Bool = true
    @AppStorage("notify_smart_money_institutional") private var smartMoneyInstitutional: Bool = false

    @AppStorage("notify_research_complete") private var researchComplete: Bool = true
    @AppStorage("notify_watchlist_changes") private var watchlistChanges: Bool = true

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: AppSpacing.xxl) {
                    // Earnings Alerts
                    notificationGroup(
                        title: "Earnings Alerts",
                        subtitle: "Get notified about earnings events for your watchlist",
                        icon: "chart.line.uptrend.xyaxis",
                        iconColor: AppColors.bullish,
                        masterToggle: $earningsAlerts
                    ) {
                        NotificationToggleRow(
                            title: "Earnings Surprises",
                            subtitle: "Beat or miss notifications",
                            isOn: $earningsSurprises,
                            disabled: !earningsAlerts
                        )

                        NotificationToggleRow(
                            title: "Upcoming Earnings",
                            subtitle: "Reminders before earnings dates",
                            isOn: $earningsUpcoming,
                            disabled: !earningsAlerts
                        )
                    }

                    // Market Alerts
                    notificationGroup(
                        title: "Market Alerts",
                        subtitle: "Macro events and market-moving news",
                        icon: "globe.americas.fill",
                        iconColor: AppColors.primaryBlue,
                        masterToggle: $marketAlerts
                    ) {
                        NotificationToggleRow(
                            title: "Macro Events",
                            subtitle: "Fed decisions, CPI, GDP, jobs data",
                            isOn: $marketMacro,
                            disabled: !marketAlerts
                        )

                        NotificationToggleRow(
                            title: "Volatility Spikes",
                            subtitle: "VIX and unusual market moves",
                            isOn: $marketVolatility,
                            disabled: !marketAlerts
                        )

                        NotificationToggleRow(
                            title: "Sector Rotation",
                            subtitle: "Significant sector flow changes",
                            isOn: $marketSector,
                            disabled: !marketAlerts
                        )
                    }

                    // Smart Money Alerts
                    notificationGroup(
                        title: "Smart Money Alerts",
                        subtitle: "Track institutional and whale activity",
                        icon: "banknote.fill",
                        iconColor: AppColors.accentYellow,
                        masterToggle: $smartMoneyAlerts
                    ) {
                        NotificationToggleRow(
                            title: "Whale Trades",
                            subtitle: "Large position changes",
                            isOn: $smartMoneyWhale,
                            disabled: !smartMoneyAlerts
                        )

                        NotificationToggleRow(
                            title: "Insider Trading",
                            subtitle: "SEC Form 4 filings",
                            isOn: $smartMoneyInsider,
                            disabled: !smartMoneyAlerts
                        )

                        NotificationToggleRow(
                            title: "Institutional Moves",
                            subtitle: "13F filings and fund activity",
                            isOn: $smartMoneyInstitutional,
                            disabled: !smartMoneyAlerts
                        )
                    }

                    // App Notifications
                    notificationGroup(
                        title: "App Activity",
                        subtitle: "Research and watchlist updates",
                        icon: "app.badge.fill",
                        iconColor: AppColors.alertPurple,
                        masterToggle: .constant(true),
                        showMasterToggle: false
                    ) {
                        NotificationToggleRow(
                            title: "Research Complete",
                            subtitle: "When your AI analysis is ready",
                            isOn: $researchComplete
                        )

                        NotificationToggleRow(
                            title: "Watchlist Price Changes",
                            subtitle: "Significant moves in tracked stocks",
                            isOn: $watchlistChanges
                        )
                    }

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
                    .fill(AppColors.cardBackground)
            )

            // Sub-toggles
            VStack(spacing: 1) {
                content()
            }
            .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.large))
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
    .preferredColorScheme(.dark)
}
