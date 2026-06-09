//
//  SignalOfConfidenceLegendItem.swift
//  ios
//
//  Atom: Single legend item with colored indicator and label for Signal of Confidence chart
//

import SwiftUI

struct SignalOfConfidenceLegendItem: View {
    let metricType: SignalOfConfidenceMetricType

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Circle()
                .fill(metricType.color)
                // 8×8 to match the other report legend dots (Bought/Sold in
                // Insider Activity, Short float/Days to cover in Short Selling).
                .frame(width: 8, height: 8)

            Text(metricType.rawValue)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
                .lineLimit(1)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ForEach(SignalOfConfidenceMetricType.allCases) { metricType in
                SignalOfConfidenceLegendItem(metricType: metricType)
            }
        }
        .padding()
    }
}
