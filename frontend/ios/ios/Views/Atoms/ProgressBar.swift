//
//  ProgressBar.swift
//  ios
//
//  Atom: Horizontal progress bar with percentage
//

import SwiftUI

struct ProgressBar: View {
    let progress: Double // 0.0 to 1.0
    var height: CGFloat = 6
    var showPercentage: Bool = true
    var color: Color = AppColors.primaryBlue

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Progress bar
            GeometryReader { geometry in
                ZStack(alignment: .leading) {
                    // Background track
                    RoundedRectangle(cornerRadius: height / 2)
                        .fill(AppColors.cardBackgroundLight)
                        .frame(height: height)

                    // Progress fill
                    RoundedRectangle(cornerRadius: height / 2)
                        .fill(color)
                        .frame(width: geometry.size.width * CGFloat(min(max(progress, 0), 1)), height: height)
                }
            }
            .frame(height: height)

            // Percentage text
            if showPercentage {
                Text("\(Int(progress * 100))%")
                    .font(AppTypography.footnote)
                    .fontWeight(.medium)
                    .foregroundColor(AppColors.textSecondary)
                    .frame(width: 40, alignment: .trailing)
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        ProgressBar(progress: 0.67)
        ProgressBar(progress: 0.33, color: AppColors.neutral)
        ProgressBar(progress: 1.0, color: AppColors.bullish)
        ProgressBar(progress: 0.0)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
