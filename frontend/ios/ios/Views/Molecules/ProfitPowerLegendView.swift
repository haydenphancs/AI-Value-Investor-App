//
//  ProfitPowerLegendView.swift
//  ios
//
//  Molecule: Complete legend showing all margin types for Profit Power chart
//  Displays in a two-row layout matching the design reference
//

import SwiftUI

struct ProfitPowerLegendView: View {
    /// "Industry" / "Sector" — the peer group the dashed benchmark line actually
    /// represents. The backend has always sent `peer_group_level`, but the label
    /// was hardcoded to "Sector", so an industry-level benchmark was mislabelled.
    var peerWord: String = "Sector"

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // First row: Gross Margin, Operating Margin, FCF Margin
            HStack(spacing: AppSpacing.xl) {
                ProfitPowerLegendItem(marginType: .grossMargin)
                ProfitPowerLegendItem(marginType: .operatingMargin)
                ProfitPowerLegendItem(marginType: .fcfMargin)
            }

            // Second row: Net Margin, peer-group Average
            HStack(spacing: AppSpacing.xl) {
                ProfitPowerLegendItem(marginType: .netMargin)
                ProfitPowerLegendItem(
                    marginType: .sectorAverage,
                    labelOverride: "\(peerWord) Average\nNet Margin"
                )
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            ProfitPowerLegendView()
            ProfitPowerLegendView(peerWord: "Industry")
        }
        .padding()
    }
}
