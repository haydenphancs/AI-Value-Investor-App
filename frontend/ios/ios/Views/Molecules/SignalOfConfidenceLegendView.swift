//
//  SignalOfConfidenceLegendView.swift
//  ios
//
//  Molecule: Legend view displaying all Signal of Confidence metrics
//

import SwiftUI

struct SignalOfConfidenceLegendView: View {
    var body: some View {
        HStack(spacing: AppSpacing.xl) {
            ForEach(SignalOfConfidenceMetricType.allCases) { metricType in
                SignalOfConfidenceLegendItem(metricType: metricType)
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        SignalOfConfidenceLegendView()
            .padding()
    }
}
