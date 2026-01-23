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
    var onTopHoldersTap: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Shareholder Breakdown Section
            ShareholderBreakdownSection(
                breakdownData: holdersData.shareholderBreakdown,
                onTopHoldersTapped: {
                    onTopHoldersTap?()
                }
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
            holdersData: HoldersData.sampleData,
            onTopHoldersTap: {
                print("Top holders tapped")
            }
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
