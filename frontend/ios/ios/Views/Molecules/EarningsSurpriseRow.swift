//
//  EarningsSurpriseRow.swift
//  ios
//
//  Molecule: Row displaying surprise percentages for each quarter with a leading indicator
//

import SwiftUI

struct EarningsSurpriseRow: View {
    let quarters: [EarningsQuarterData]
    var dataType: EarningsDataType = .eps

    // MUST match EarningsChartView.yAxisWidth so each % sits under its dot.
    // Revenue labels ("23.3B") are wider than EPS ("2.49"), so the axis is wider.
    private var yAxisWidth: CGFloat { dataType == .revenue ? 50 : 40 }

    var body: some View {
        HStack(spacing: 0) {
            // Y-axis spacer to align with X-axis labels (NO padding, just width)
            Spacer()
                .frame(width: yAxisWidth)

            // Surprise percentages for each quarter
            // This HStack matches the xAxisLabels structure exactly
            HStack(spacing: 0) {
                ForEach(Array(quarters.enumerated()), id: \.element.id) { index, quarter in
                    if let surprise = quarter.formattedSurprise {
                        Text(surprise)
                            .font(AppTypography.labelSmall)
                            .foregroundColor(quarter.surpriseColor)
                            .frame(maxWidth: .infinity)
                    } else {
                        // Empty space for future quarters
                        Text("")
                            .frame(maxWidth: .infinity)
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

        EarningsSurpriseRow(quarters: EarningsData.sampleData.epsQuarters)
            .padding()
    }
}
