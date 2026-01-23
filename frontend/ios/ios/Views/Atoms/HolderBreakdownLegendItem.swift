//
//  HolderBreakdownLegendItem.swift
//  ios
//
//  Atom: Single legend item for shareholder breakdown
//  Shows colored dot, label, and percentage
//

import SwiftUI

struct HolderBreakdownLegendItem: View {
    let color: Color
    let label: String
    let percentage: String

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            // Color indicator dot
            Circle()
                .fill(color)
                .frame(width: 10, height: 10)

            // Label
            Text(label)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            // Percentage value
            Text(percentage)
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.md) {
            HolderBreakdownLegendItem(
                color: HoldersColors.insiders,
                label: "Insiders",
                percentage: "12%"
            )

            HolderBreakdownLegendItem(
                color: HoldersColors.institutions,
                label: "Institutions",
                percentage: "55%"
            )

            HolderBreakdownLegendItem(
                color: HoldersColors.publicOther,
                label: "Public/Other",
                percentage: "33%"
            )
        }
        .padding()
    }
}
