//
//  TickerFinancialsContent.swift
//  ios
//
//  Organism: Financials tab content combining all financial sections for Ticker Detail
//

import SwiftUI

struct TickerFinancialsContent: View {
    let earningsData: EarningsData
    let growthData: GrowthSectionData?
    let profitPowerData: ProfitPowerSectionData?
    let signalOfConfidenceData: SignalOfConfidenceSectionData?
    let revenueBreakdownData: RevenueBreakdownData?
    var onEarningsDetailTap: (() -> Void)?
    var onGrowthDetailTap: (() -> Void)?
    var onProfitPowerDetailTap: (() -> Void)?
    var onSignalOfConfidenceDetailTap: (() -> Void)?
    var onRevenueBreakdownDetailTap: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Earnings Section
            EarningsSectionCard(
                earningsData: earningsData,
                onDetailTap: {
                    onEarningsDetailTap?()
                }
            )

            // Growth Section
            if let growthData = growthData {
                GrowthSectionCard(
                    growthData: growthData,
                    onDetailTapped: {
                        onGrowthDetailTap?()
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

            // Revenue Breakdown Section ("How TICKER Makes Money")
            if let revenueBreakdownData = revenueBreakdownData {
                RevenueBreakdownSectionCard(
                    data: revenueBreakdownData,
                    onDetailTapped: {
                        onRevenueBreakdownDetailTap?()
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

            // Bottom spacing for AI bar
            Spacer()
                .frame(height: 120)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.lg)
    }
}

#Preview {
    ScrollView {
        TickerFinancialsContent(
            earningsData: EarningsData.sampleData,
            growthData: GrowthSectionData.sampleData,
            profitPowerData: ProfitPowerSectionData.sampleData,
            signalOfConfidenceData: SignalOfConfidenceSectionData.sampleData,
            revenueBreakdownData: RevenueBreakdownData.sampleApple
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
