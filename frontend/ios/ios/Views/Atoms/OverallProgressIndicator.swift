//
//  OverallProgressIndicator.swift
//  ios
//
//  Atom: Shows overall lesson progress with segmented bar
//

import SwiftUI

struct OverallProgressIndicator: View {
    let completed: Int
    let total: Int
    var segmentCount: Int = 27

    private var progress: Double {
        guard total > 0 else { return 0 }
        return Double(completed) / Double(total)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Segmented progress bar
            GeometryReader { geometry in
                HStack(spacing: 2) {
                    ForEach(0..<segmentCount, id: \.self) { index in
                        let isCompleted = index < completed
                        RoundedRectangle(cornerRadius: 2)
                            .fill(isCompleted ? AppColors.bullish : AppColors.cardBackgroundLight)
                            .frame(height: 6)
                    }
                }
            }
            .frame(height: 6)
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        OverallProgressIndicator(completed: 1, total: 27)
        OverallProgressIndicator(completed: 10, total: 27)
        OverallProgressIndicator(completed: 27, total: 27)
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
