//
//  AlertDetailView.swift
//  ios
//
//  Detail screen for alert events (earnings, market, smart money)
//

import SwiftUI

struct AlertDetailView: View {
    let alert: AppAlert
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                VStack(spacing: AppSpacing.xl) {
                    // Header Icon
                    headerIcon
                        .padding(.top, AppSpacing.xxl)

                    // Title & Description
                    VStack(spacing: AppSpacing.sm) {
                        Text(alert.title)
                            .font(AppTypography.title2)
                            .foregroundColor(AppColors.textPrimary)

                        Text(alert.description)
                            .font(AppTypography.body)
                            .foregroundColor(AppColors.textSecondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, AppSpacing.lg)
                    }

                    // Type-specific content
                    detailContent
                        .padding(.horizontal, AppSpacing.lg)

                    Spacer()
                        .frame(height: 40)
                }
            }
        }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text(alert.title)
                    .font(AppTypography.bodyBold)
                    .foregroundColor(AppColors.textPrimary)
            }
        }
    }

    // MARK: - Header Icon

    private var headerIcon: some View {
        ZStack {
            Circle()
                .fill(alert.iconColor.opacity(0.15))
                .frame(width: 72, height: 72)

            Image(systemName: alert.iconName)
                .font(.system(size: 32, weight: .semibold))
                .foregroundColor(alert.iconColor)
        }
    }

    // MARK: - Detail Content

    @ViewBuilder
    private var detailContent: some View {
        switch alert {
        case .earnings(let data):
            earningsDetail(data)
        case .market(let data):
            marketDetail(data)
        case .smartMoney(let data):
            smartMoneyDetail(data)
        }
    }

    // MARK: - Earnings Detail

    private func earningsDetail(_ data: AppAlert.EarningsData) -> some View {
        VStack(spacing: AppSpacing.md) {
            detailRow(label: "Ticker", value: data.ticker)
            detailRow(label: "Company", value: data.companyName)
            detailRow(label: "Report Time", value: data.reportTime.displayText.capitalized)
            detailRow(label: "Consensus", value: data.consensus)
            detailRow(label: "Date", value: "\(data.formattedMonth) \(data.formattedDay)")
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    // MARK: - Market Detail

    private func marketDetail(_ data: AppAlert.MarketData) -> some View {
        VStack(spacing: AppSpacing.md) {
            detailRow(label: "Event", value: data.eventName)
            detailRow(label: "Details", value: data.description)
            detailRow(label: "Date", value: "\(data.formattedMonth) \(data.formattedDay)")
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    // MARK: - Smart Money Detail

    private func smartMoneyDetail(_ data: AppAlert.SmartMoneyData) -> some View {
        VStack(spacing: AppSpacing.md) {
            detailRow(label: "Ticker", value: data.ticker)
            detailRow(label: "Funds Buying", value: "\(data.fundCount) hedge funds")
            detailRow(label: "Avg. Position", value: data.positionSize)
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }

    // MARK: - Detail Row

    private func detailRow(label: String, value: String) -> some View {
        HStack {
            Text(label)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(value)
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}

#Preview {
    NavigationStack {
        AlertDetailView(alert: AppAlert.sampleData[0])
    }
    .preferredColorScheme(.dark)
}
