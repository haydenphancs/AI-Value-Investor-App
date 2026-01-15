//
//  EarningsLegend.swift
//  ios
//
//  Molecule: Legend row for the earnings chart showing all result types
//

import SwiftUI

struct EarningsLegend: View {
    var body: some View {
        HStack(spacing: AppSpacing.xl) {
            EarningsLegendItem(type: .surprised)
            EarningsLegendItem(type: .estimate)
            EarningsLegendItem(type: .beat)
            EarningsLegendItem(type: .missed)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        EarningsLegend()
    }
}
