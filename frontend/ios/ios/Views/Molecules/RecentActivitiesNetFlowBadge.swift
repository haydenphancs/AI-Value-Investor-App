//
//  RecentActivitiesNetFlowBadge.swift
//  ios
//
//  Molecule: Badge displaying the net flow summary
//  Shows quarter and net flow value with appropriate color
//

import SwiftUI

struct RecentActivitiesNetFlowBadge: View {
    let summary: RecentActivitiesFlowSummary

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Text("\(summary.quarterDescription) Net Flow:")
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
            RecentActivitiesNetFlowBadge(
                summary: RecentActivitiesFlowSummary(
                    periodDescription: "Oct - Dec 2025",
                    quarterDescription: "Q4",
                    inFlowInBillions: 2.1,
                    outFlowInBillions: 1.8
                )
            )

            RecentActivitiesNetFlowBadge(
                summary: RecentActivitiesFlowSummary(
                    periodDescription: "Jul - Sep 2025",
                    quarterDescription: "Q3",
                    inFlowInBillions: 1.2,
                    outFlowInBillions: 1.8
                )
            )
        }
    }
}
