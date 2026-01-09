//
//  AnalysisMomentumSection.swift
//  ios
//
//  Complete momentum section with header, chart, legend, and actions
//

import SwiftUI

struct AnalysisMomentumSection: View {
    let momentumData: [AnalystMomentumMonth]
    let netPositive: Int
    let netNegative: Int
    let actionsSummary: AnalystActionsSummary
    @Binding var selectedPeriod: AnalystMomentumPeriod

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Header with toggle
            HStack {
                Text("Analyst Momentum")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                HStack(spacing: AppSpacing.sm) {
                    Text("Actions")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.primaryBlue)

                    Image(systemName: "chevron.right")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundColor(AppColors.primaryBlue)
                }
            }

            // Period toggle
            HStack {
                MomentumPeriodToggle(selectedPeriod: $selectedPeriod)
                Spacer()
            }

            // Bar chart
            MomentumBarChart(data: momentumData)

            // Legend
            HStack(spacing: AppSpacing.xl) {
                MomentumLegendItem(
                    color: AppColors.bullish,
                    label: "Net Positive",
                    value: netPositive
                )

                MomentumLegendItem(
                    color: AppColors.bearish,
                    label: "Net Negative",
                    value: -netNegative
                )

                Spacer()
            }

            // Actions row
            AnalystActionsRow(actionsSummary: actionsSummary)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        AnalysisMomentumSection(
            momentumData: AnalystMomentumMonth.sampleData,
            netPositive: 17,
            netNegative: 7,
            actionsSummary: AnalystActionsSummary.sampleData,
            selectedPeriod: .constant(.sixMonths)
        )
        .padding()
    }
}
