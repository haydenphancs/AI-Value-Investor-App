//
//  EarningsSurpriseRow.swift
//  ios
//
//  Molecule: Row displaying surprise percentages for each quarter with a leading indicator
//

import SwiftUI

struct EarningsSurpriseRow: View {
    let quarters: [EarningsQuarterData]

    // Filter to only quarters with surprise data
    private var surpriseQuarters: [EarningsQuarterData] {
        quarters.filter { $0.surprisePercent != nil }
    }

    var body: some View {
        HStack(spacing: 0) {
            // Leading surprise indicator circle
            Circle()
                .fill(AppColors.neutral)
                .frame(width: 10, height: 10)
                .padding(.trailing, AppSpacing.sm)

            // Surprise percentages for each quarter
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

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        EarningsSurpriseRow(quarters: EarningsData.sampleData.epsQuarters)
            .padding()
    }
}
