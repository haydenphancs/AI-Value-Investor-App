//
//  SmartMoneyFlowLegend.swift
//  ios
//
//  Molecule: Legend component for Smart Money flow chart
//  Shows Buy Volume and Sell Volume indicators
//

import SwiftUI

struct SmartMoneyFlowLegend: View {
    var body: some View {
        HStack(spacing: AppSpacing.lg) {
            SmartMoneyFlowLegendItem(
                color: HoldersColors.buyVolume,
                label: "Buy Volume"
            )

            SmartMoneyFlowLegendItem(
                color: HoldersColors.sellVolume,
                label: "Sell Volume"
            )

            Spacer()
        }
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
