//
//  FinancialProfitPowerSection.swift
//  ios
//
//  Organism: Profit Power section showing margin trends over time
//

import SwiftUI

struct FinancialProfitPowerSection: View {
    let data: ProfitPowerData
    @Binding var selectedPeriod: GrowthPeriodType
    var onDetailTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section Header
            FinancialSectionHeader(
                title: "Profit Power",
                infoTitle: "Profit Power",
                infoDescription: FinancialInfoContent.profitPower,
                showDetailLink: true,
                onDetailTap: onDetailTap
            )

            // Period toggle
            HStack {
                FinancialSegmentedControl(
                    selection: $selectedPeriod,
                    style: .toggle
                )
                Spacer()
            }

            // Chart
            ProfitPowerChart(
                data: data.marginData,
                showSectorAverage: true
            )
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
            FinancialProfitPowerSection(
                data: ProfitPowerData.sampleData,
                selectedPeriod: .constant(.annual)
            )
            .padding(.vertical)
        }
    }
}
