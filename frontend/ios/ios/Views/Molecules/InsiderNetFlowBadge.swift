//
//  InsiderNetFlowBadge.swift
//  ios
//
//  Molecule: Badge displaying the net informative flow for insiders
//  Shows net flow value with up/down indicator and appropriate color
//

import SwiftUI

struct InsiderNetFlowBadge: View {
    let summary: InsiderActivitySummary

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Text("Net Informative Flow:")
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)

            Text(summary.formattedNetFlow)
                .font(AppTypography.calloutBold)
                .foregroundColor(summary.netFlowColor)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            // Positive flow
            InsiderNetFlowBadge(
                summary: InsiderActivitySummary.sampleData
            )

            // Negative flow
            InsiderNetFlowBadge(
                summary: InsiderActivitySummary(
                    periodDescription: "Last 6 Months",
                    informativeBuysInMillions: 2.5,
                    informativeSellsInMillions: 6.8,
                    numBuyers: 1,
                    numSellers: 4
                )
            )
        }
        .padding()
    }
}
