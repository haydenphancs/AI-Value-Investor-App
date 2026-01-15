//
//  TickerFinancialsContent.swift
//  ios
//
//  Organism: Financials tab content combining all financial sections for Ticker Detail
//

import SwiftUI

struct TickerFinancialsContent: View {
    let earningsData: EarningsData
    var onEarningsDetailTap: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Earnings Section
            EarningsSectionCard(
                earningsData: earningsData,
                onDetailTap: {
                    onEarningsDetailTap?()
                }
            )

            // Placeholder for future financial sections
            // e.g., Revenue Section, Cash Flow Section, Balance Sheet Section

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
            earningsData: EarningsData.sampleData
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
