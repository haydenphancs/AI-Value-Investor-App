//
//  FinancialEarningsSection.swift
//  ios
//
//  Organism: Earnings section with chart, toggles, and controls
//

import SwiftUI

struct FinancialEarningsSection: View {
    let data: EarningsData
    @Binding var selectedMetric: EarningsMetricType
    @Binding var selectedPeriod: EarningsTimePeriod
    @Binding var showPrice: Bool
    var onDetailTap: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section Header
            FinancialSectionHeader(
                title: "Earnings",
                infoTitle: "Earnings",
                infoDescription: FinancialInfoContent.earnings,
                showDetailLink: true,
                onDetailTap: onDetailTap
            )

            // Controls row
            HStack(spacing: AppSpacing.md) {
                // Metric toggle (EPS/Revenue)
                FinancialSegmentedControl(
                    selection: $selectedMetric,
                    style: .pill
                )

                Spacer()

                // Period toggle (1Y/3Y)
                FinancialSegmentedControl(
                    selection: $selectedPeriod,
                    style: .compact
                )

                // Price toggle
                FinancialTogglePill(
                    label: "Price",
                    isOn: $showPrice
                )
            }

            // Chart
            EarningsChart(
                data: filteredData,
                showPrice: showPrice,
                metricType: selectedMetric
            )
        }
        .padding(.horizontal, AppSpacing.lg)
    }

    private var filteredData: [EarningsQuarter] {
        switch selectedPeriod {
        case .oneYear:
            return Array(data.quarters.suffix(4))
        case .threeYears:
            return data.quarters
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            FinancialEarningsSection(
                data: EarningsData.sampleData,
                selectedMetric: .constant(.eps),
                selectedPeriod: .constant(.oneYear),
                showPrice: .constant(true)
            )
            .padding(.vertical)
        }
    }
}
