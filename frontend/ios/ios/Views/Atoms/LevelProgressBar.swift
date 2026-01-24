//
//  LevelProgressBar.swift
//  ios
//
//  Atom: Progress bar with level color and fraction display
//

import SwiftUI

struct LevelProgressBar: View {
    let progress: Double
    let completed: Int
    let total: Int
    var color: Color = AppColors.primaryBlue
    var height: CGFloat = 4

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
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
                        .frame(
                            width: geometry.size.width * CGFloat(min(max(progress, 0), 1)),
                            height: height
                        )
                }
            }
            .frame(height: height)

            // Progress text
            Text("\(completed)/\(total)")
                .font(AppTypography.caption)
                .fontWeight(.medium)
                .foregroundColor(AppColors.textMuted)
                .frame(minWidth: 28, alignment: .trailing)
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        LevelProgressBar(progress: 0.14, completed: 1, total: 7, color: AppColors.bullish)
        LevelProgressBar(progress: 0.0, completed: 0, total: 7, color: AppColors.primaryBlue)
        LevelProgressBar(progress: 0.5, completed: 3, total: 6, color: AppColors.alertPurple)
        LevelProgressBar(progress: 1.0, completed: 6, total: 6, color: AppColors.neutral)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
