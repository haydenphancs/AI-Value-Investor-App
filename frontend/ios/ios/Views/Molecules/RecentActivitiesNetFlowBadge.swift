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
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.sm)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(summary.isNetPositive
                    ? AppColors.bullish.opacity(0.1)
                    : AppColors.bearish.opacity(0.1)
                )
        )
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
