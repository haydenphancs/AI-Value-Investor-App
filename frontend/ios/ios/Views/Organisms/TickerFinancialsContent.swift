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
    var onEarningsDetailTap: (() -> Void)?
    var onGrowthDetailTap: (() -> Void)?

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
            growthData: GrowthSectionData.sampleData
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
