//
//  ProfitPowerLegendItem.swift
//  ios
//
//  Atom: Single legend item with colored dot/line and label for Profit Power chart
//

import SwiftUI

struct ProfitPowerLegendItem: View {
    let marginType: ProfitMarginType

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            legendIndicator

            Text(marginType.shortName)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.leading)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    @ViewBuilder
    private var legendIndicator: some View {
        if marginType.isDashed {
            // Dashed circle for sector average
            Circle()
                .stroke(marginType.color, style: StrokeStyle(lineWidth: 2, dash: [4, 3]))
                .frame(width: 10, height: 10)
        } else {
            // Filled circle for regular margins
            Circle()
                .fill(marginType.color)
                .frame(width: 10, height: 10)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ForEach(ProfitMarginType.allCases) { marginType in
                ProfitPowerLegendItem(marginType: marginType)
            }
        }
        .padding()
    }
}
