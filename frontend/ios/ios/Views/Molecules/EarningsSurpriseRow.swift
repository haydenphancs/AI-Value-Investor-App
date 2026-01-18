//
//  EarningsSurpriseRow.swift
//  ios
//
//  Molecule: Row displaying surprise percentages for each quarter with a leading indicator
//

import SwiftUI

struct EarningsSurpriseRow: View {
    let quarters: [EarningsQuarterData]
    
    private var yAxisWidth: CGFloat { 40 }

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
                            .font(AppTypography.footnote)
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
