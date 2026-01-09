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
    var onActionsTapped: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Header with toggle
            HStack {
                Text("Analyst Momentum")
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button(action: {
                    onActionsTapped?()
                }) {
                    Text("Actions")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }

            // Period toggle - centered
            HStack {
                Spacer()
                MomentumPeriodToggle(selectedPeriod: $selectedPeriod)
                Spacer()
            }

            // Bar chart
            MomentumBarChart(data: momentumData)

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
