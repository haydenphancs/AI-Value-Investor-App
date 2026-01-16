//
//  GrowthMetricSelector.swift
//  ios
//
//  Molecule: Horizontal scrolling selector for growth metric types
//

import SwiftUI

struct GrowthMetricSelector: View {
    @Binding var selectedMetric: GrowthMetricType

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: AppSpacing.sm) {
                ForEach(GrowthMetricType.allCases) { metric in
                    GrowthMetricChip(
                        metricType: metric,
                        isSelected: selectedMetric == metric,
                        action: {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                selectedMetric = metric
                            }
                        }
                    )
                }
            }
            .padding(.horizontal, AppSpacing.xs)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            GrowthMetricSelector(selectedMetric: .constant(.revenue))
        }
        .padding()
    }
}
