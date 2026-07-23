//
//  TickerFinancialsContent.swift
//  ios
//
//  Organism: Financials tab content combining all financial sections for Ticker Detail
//

import SwiftUI

struct TickerFinancialsContent: View {
    let earningsData: EarningsData?
    let growthData: GrowthSectionData?
    let profitPowerData: ProfitPowerSectionData?
    let signalOfConfidenceData: SignalOfConfidenceSectionData?
    let revenueBreakdownData: RevenueBreakdownData?
    let healthCheckData: HealthCheckSectionData?
    /// False while the Financials fetches are still in flight. Without it, a
    /// loading tab and a tab whose backend returned nothing render identically
    /// (six missing cards), so the user can't tell which they're looking at.
    var isLoaded: Bool = true
    var onEarningsDetailTap: (() -> Void)?
    var onGrowthDetailTap: (() -> Void)?
    var onProfitPowerDetailTap: (() -> Void)?
    var onSignalOfConfidenceDetailTap: (() -> Void)?
    var onRevenueBreakdownDetailTap: (() -> Void)?
    var onHealthCheckDetailTap: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Earnings Section
            if let earningsData = earningsData {
                EarningsSectionCard(
                    earningsData: earningsData,
                    onDetailTap: {
                        onEarningsDetailTap?()
                    }
                )
            }

            // Growth Section
            if let growthData = growthData {
                GrowthSectionCard(
                    growthData: growthData,
                    onDetailTapped: {
                        onGrowthDetailTap?()
                    }
                )
            }

            // Revenue Breakdown Section ("How TICKER Makes Money")
            if let revenueBreakdownData = revenueBreakdownData {
                RevenueBreakdownSectionCard(
                    data: revenueBreakdownData,
                    onDetailTapped: {
                        onRevenueBreakdownDetailTap?()
                    }
                )
            }

            // Profit Power Section
            if let profitPowerData = profitPowerData {
                ProfitPowerSectionCard(
                    profitPowerData: profitPowerData,
                    onDetailTapped: {
                        onProfitPowerDetailTap?()
                    }
                )
            }

            // Health Check Section
            if let healthCheckData = healthCheckData {
                HealthCheckSectionCard(
                    healthCheckData: healthCheckData,
                    onDetailTapped: {
                        onHealthCheckDetailTap?()
                    }
                )
            }

            // Signal of Confidence Section
            if let signalOfConfidenceData = signalOfConfidenceData {
                SignalOfConfidenceSectionCard(
                    signalData: signalOfConfidenceData,
                    onDetailTapped: {
                        onSignalOfConfidenceDetailTap?()
                    }
                )
            }

            // Still loading, or everything failed — say which.
            if !isLoaded && !hasAnySection {
                loadingPlaceholder
            } else if isLoaded && !hasAnySection {
                ChartUnavailableView(
                    message: "Financial data isn't available for this company right now.",
                    systemImage: "doc.text.magnifyingglass"
                )
                .padding(.vertical, AppSpacing.xl)
            }

            // Bottom spacing for AI bar
            Spacer()
                .frame(height: 120)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
    }

    private var hasAnySection: Bool {
        earningsData != nil || growthData != nil || revenueBreakdownData != nil
            || profitPowerData != nil || healthCheckData != nil
            || signalOfConfidenceData != nil
    }

    private var loadingPlaceholder: some View {
        VStack(spacing: AppSpacing.lg) {
            ForEach(0..<3, id: \.self) { _ in
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackground)
                    .frame(height: 180)
                    .shimmer()
            }
        }
        .accessibilityLabel("Loading financials")
    }
}

#Preview {
    ScrollView {
        TickerFinancialsContent(
            earningsData: EarningsData.sampleData,
            growthData: GrowthSectionData.sampleData,
            profitPowerData: ProfitPowerSectionData.sampleData,
            signalOfConfidenceData: SignalOfConfidenceSectionData.sampleData,
            revenueBreakdownData: RevenueBreakdownData.sampleApple,
            healthCheckData: HealthCheckSectionData.sampleData
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
