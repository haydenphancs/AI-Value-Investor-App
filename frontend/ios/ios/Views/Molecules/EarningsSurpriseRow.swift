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

    // MUST match EarningsChartView's gutter so each % sits under its dot — read from the
    // one shared function rather than a copy (EarningsChartLayout).
    private var yAxisWidth: CGFloat { EarningsChartLayout.yAxisWidth(for: dataType) }

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
                        // One line, always: an unbounded "+1300.0%" in a ~48pt column broke
                        // across two lines.
                        Text(surprise)
                            .font(AppTypography.labelSmall)
                            .foregroundColor(quarter.surpriseColor)
                            .lineLimit(1)
                            .minimumScaleFactor(0.75)
                            .frame(maxWidth: .infinity)
                    } else if quarter.result == .noEstimate {
                        // Reported, but there was no consensus to be surprised against. A
                        // blank slot here read as a future quarter.
                        Text("—")
                            .font(AppTypography.labelSmall)
                            .foregroundColor(AppColors.textMuted)
                            .lineLimit(1)
                            .frame(maxWidth: .infinity)
                            .accessibilityLabel("\(quarter.quarter): no analyst consensus")
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

        VStack(spacing: AppSpacing.lg) {
            EarningsSurpriseRow(quarters: EarningsData.sampleData.epsQuarters)

            // Edge cases: an exploded surprise, a beat that rounds to 0.0, a match, and a
            // reported quarter with no consensus.
            EarningsSurpriseRow(quarters: { () -> [EarningsQuarterData] in
                var reported = EarningsQuarterData(quarter: "Q4 '24", actualValue: 0.31, estimateValue: 0.31, surprisePercent: nil)
                reported.hasEstimate = false
                return [
                    EarningsQuarterData(quarter: "Q1 '24", actualValue: 0.14, estimateValue: 0.01, surprisePercent: 1300),
                    EarningsQuarterData(quarter: "Q2 '24", actualValue: 10.0004, estimateValue: 10.0, surprisePercent: 0.0),
                    EarningsQuarterData(quarter: "Q3 '24", actualValue: 0.25, estimateValue: 0.25, surprisePercent: 0),
                    reported,
                ]
            }())
        }
        .padding()
    }
}
