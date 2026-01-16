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
                .frame(width: 10, height: 10)

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
