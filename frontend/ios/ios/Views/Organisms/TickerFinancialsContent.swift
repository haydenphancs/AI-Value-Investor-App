//
//  TickerFinancialsContent.swift
//  ios
//
//  Organism: Complete Financials tab content combining all financial sections
//

import SwiftUI

struct TickerFinancialsContent: View {
    let financialData: TickerFinancialData
    let tickerSymbol: String

    // Earnings section state
    @State private var selectedEarningsMetric: EarningsMetricType = .eps
    @State private var selectedEarningsPeriod: EarningsTimePeriod = .oneYear
    @State private var showEarningsPrice: Bool = false

    // Growth section state
    @State private var selectedGrowthMetric: GrowthMetricType = .revenue
    @State private var selectedGrowthPeriod: GrowthPeriodType = .annual

    // Profit Power section state
    @State private var selectedProfitPeriod: GrowthPeriodType = .annual

    // Signal of Confidence section state
    @State private var selectedSignalViewType: SignalViewType = .yield

    // Callbacks for detail views
    var onEarningsDetailTap: (() -> Void)?
    var onGrowthDetailTap: (() -> Void)?
    var onRevenueDetailTap: (() -> Void)?
    var onProfitPowerDetailTap: (() -> Void)?
    var onHealthCheckDetailTap: (() -> Void)?
    var onSignalDetailTap: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.xxl) {
            // Earnings Section
            FinancialEarningsSection(
                data: financialData.earnings,
                selectedMetric: $selectedEarningsMetric,
                selectedPeriod: $selectedEarningsPeriod,
                showPrice: $showEarningsPrice,
                onDetailTap: onEarningsDetailTap
            )

            // Growth Section
            FinancialGrowthSection(
                data: financialData.growth,
                selectedMetric: $selectedGrowthMetric,
                selectedPeriod: $selectedGrowthPeriod,
                onDetailTap: onGrowthDetailTap
            )

            // Revenue Breakdown Section (How Company Makes Money)
            FinancialRevenueBreakdownSection(
                data: financialData.revenueBreakdown,
                tickerSymbol: tickerSymbol,
                onDetailTap: onRevenueDetailTap
            )

            // Profit Power Section
            FinancialProfitPowerSection(
                data: financialData.profitPower,
                selectedPeriod: $selectedProfitPeriod,
                onDetailTap: onProfitPowerDetailTap
            )

            // Health Check Section
            FinancialHealthCheckSection(
                data: financialData.healthCheck,
                onDetailTap: onHealthCheckDetailTap
            )

            // Signal of Confidence Section
            FinancialSignalOfConfidenceSection(
                data: financialData.signalOfConfidence,
                selectedViewType: $selectedSignalViewType,
                onDetailTap: onSignalDetailTap
            )

            // Bottom spacing for AI bar
            Spacer()
                .frame(height: 120)
        }
        .padding(.top, AppSpacing.lg)
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            TickerFinancialsContent(
                financialData: TickerFinancialData.sampleApple,
                tickerSymbol: "AAPL"
            )
        }
    }
    .preferredColorScheme(.dark)
}
