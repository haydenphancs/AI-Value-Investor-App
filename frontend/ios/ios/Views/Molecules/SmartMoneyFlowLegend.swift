//
//  SmartMoneyFlowLegend.swift
//  ios
//
//  Molecule: Legend component for Smart Money flow chart
//  Shows Buy Volume and Sell Volume indicators
//

import SwiftUI

struct SmartMoneyFlowLegend: View {
    // Defaults match the dollar-denominated tabs (Insider/Congress). The
    // hedge-fund tab is in shares, so it passes "Shares Bought/Sold".
    var buyLabel: String = "Buy Volume"
    var sellLabel: String = "Sell Volume"

    var body: some View {
        HStack(spacing: AppSpacing.lg) {
            SmartMoneyFlowLegendItem(
                color: HoldersColors.buyVolume,
                label: buyLabel
            )

            SmartMoneyFlowLegendItem(
                color: HoldersColors.sellVolume,
                label: sellLabel
            )
        }
        .frame(maxWidth: .infinity)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        SmartMoneyFlowLegend()
            .padding()
    }
}
