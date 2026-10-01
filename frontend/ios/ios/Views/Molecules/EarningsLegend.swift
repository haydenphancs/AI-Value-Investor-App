//
//  EarningsLegend.swift
//  ios
//
//  Molecule: Legend row for the earnings chart showing all result types
//

import SwiftUI

struct EarningsLegend: View {
    /// Show the "Reported" (no analyst consensus) entry. Only when the chart actually
    /// draws such a dot: a fifth item does not fit one row on a narrow phone.
    var showsReported: Bool = false

    var body: some View {
        // One row when it fits, otherwise two centred rows — never a squeezed or clipped
        // legend (the four-item row was already ~300pt of a ~311pt card on a 375pt phone).
        ViewThatFits(in: .horizontal) {
            HStack(spacing: AppSpacing.xl) {
                EarningsLegendItem(type: .surprised)
                EarningsLegendItem(type: .estimate)
                EarningsLegendItem(type: .beat)
                EarningsLegendItem(type: .missed)
                if showsReported {
                    EarningsLegendItem(type: .reported)
                }
            }

            VStack(spacing: AppSpacing.sm) {
                HStack(spacing: AppSpacing.xl) {
                    EarningsLegendItem(type: .surprised)
                    EarningsLegendItem(type: .estimate)
                    EarningsLegendItem(type: .beat)
                }
                HStack(spacing: AppSpacing.xl) {
                    EarningsLegendItem(type: .missed)
                    if showsReported {
                        EarningsLegendItem(type: .reported)
                    }
                }
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            EarningsLegend()
            EarningsLegend(showsReported: true)
        }
        .padding()
    }
}
