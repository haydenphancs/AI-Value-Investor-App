//
//  TickerHoldersContent.swift
//  ios
//
//  Organism: Holders tab content combining all holder-related sections for Ticker Detail
//  Includes Shareholder Breakdown and Smart Money sections
//

import SwiftUI

struct TickerHoldersContent: View {
    let holdersData: HoldersData

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Shareholder Breakdown Section
            ShareholderBreakdownSection(
                breakdownData: holdersData.shareholderBreakdown
            )

            // Smart Money Section
            SmartMoneySection(holdersData: holdersData)

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
        TickerHoldersContent(
            holdersData: HoldersData.sampleData
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
