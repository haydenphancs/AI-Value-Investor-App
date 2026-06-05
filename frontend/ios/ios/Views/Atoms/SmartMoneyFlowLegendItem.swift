//
//  SmartMoneyFlowLegendItem.swift
//  ios
//
//  Atom: Single legend item for smart money flow chart
//  Shows colored dot and label
//

import SwiftUI

struct SmartMoneyFlowLegendItem: View {
    let color: Color
    let label: String
    var font: Font = AppTypography.caption
    var labelColor: Color = AppColors.textSecondary

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            // Color indicator dot
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)

            // Label
            Text(label)
                .font(font)
                .foregroundColor(labelColor)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.lg) {
            SmartMoneyFlowLegendItem(
                color: HoldersColors.buyVolume,
                label: "Buy Volume"
            )

            SmartMoneyFlowLegendItem(
                color: HoldersColors.sellVolume,
                label: "Sell Volume"
            )
        }
        .padding()
    }
}
