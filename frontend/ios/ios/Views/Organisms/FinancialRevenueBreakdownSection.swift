//
//  FinancialRevenueBreakdownSection.swift
//  ios
//
//  Organism: Revenue breakdown section showing how company makes money
//

import SwiftUI

struct FinancialRevenueBreakdownSection: View {
    let data: RevenueBreakdownData
    let tickerSymbol: String
    var onDetailTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section Header with dynamic title
            FinancialSectionHeader(
                title: "How \(tickerSymbol) Makes Money",
                infoTitle: "Revenue Breakdown",
                infoDescription: FinancialInfoContent.revenueBreakdown,
                showDetailLink: true,
                onDetailTap: onDetailTap
            )

            // Chart and breakdown
            RevenueBreakdownChart(data: data)
        }
        .padding(.horizontal, AppSpacing.lg)
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            FinancialRevenueBreakdownSection(
                data: RevenueBreakdownData.sampleApple,
                tickerSymbol: "AAPL"
            )
            .padding(.vertical)
        }
    }
}
