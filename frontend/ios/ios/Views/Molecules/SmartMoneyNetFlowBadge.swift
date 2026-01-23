//
//  SmartMoneyNetFlowBadge.swift
//  ios
//
//  Molecule: Badge showing net informative flow for Smart Money
//  Displays the total net flow with directional indicator
//

import SwiftUI

struct SmartMoneyNetFlowBadge: View {
    let summary: SmartMoneyFlowSummary

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            // AI/Smart Money icon
            Image(systemName: "brain.head.profile")
                .font(.system(size: 16))
                .foregroundColor(AppColors.primaryBlue)
                .frame(width: 28, height: 28)
                .background(
                    Circle()
                        .fill(AppColors.primaryBlue.opacity(0.15))
                )

            Text("Net Informative Flow:")
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)

            HStack(spacing: AppSpacing.xxs) {
                Image(systemName: summary.flowIcon)
                    .font(.system(size: 12, weight: .bold))
                    .foregroundColor(summary.flowColor)

                Text(summary.formattedNetFlow)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(summary.flowColor)
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            SmartMoneyNetFlowBadge(
                summary: SmartMoneyFlowSummary(
                    totalNetFlow: 8.27,
                    isPositive: true,
                    periodDescription: "12-Month"
                )
            )

            SmartMoneyNetFlowBadge(
                summary: SmartMoneyFlowSummary(
                    totalNetFlow: -15.5,
                    isPositive: false,
                    periodDescription: "12-Month"
                )
            )

            SmartMoneyNetFlowBadge(
                summary: SmartMoneyFlowSummary(
                    totalNetFlow: 1250,
                    isPositive: true,
                    periodDescription: "12-Month"
                )
            )
        }
        .padding()
    }
}
