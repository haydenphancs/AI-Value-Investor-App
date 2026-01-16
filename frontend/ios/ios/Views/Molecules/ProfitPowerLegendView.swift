//
//  ProfitPowerLegendView.swift
//  ios
//
//  Molecule: Complete legend showing all margin types for Profit Power chart
//  Displays in a two-row layout matching the design reference
//

import SwiftUI

struct ProfitPowerLegendView: View {
    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // First row: Gross Margin, Operating Margin, FCF Margin
            HStack(spacing: AppSpacing.xl) {
                ProfitPowerLegendItem(marginType: .grossMargin)
                ProfitPowerLegendItem(marginType: .operatingMargin)
                ProfitPowerLegendItem(marginType: .fcfMargin)
            }

            // Second row: Net Margin, Sector Average
            HStack(spacing: AppSpacing.xl) {
                ProfitPowerLegendItem(marginType: .netMargin)
                ProfitPowerLegendItem(marginType: .sectorAverage)
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ProfitPowerLegendView()
            .padding()
    }
}
