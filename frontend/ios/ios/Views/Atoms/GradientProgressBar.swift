//
//  GradientProgressBar.swift
//  ios
//
//  Atom: Progress bar with gradient fill
//

import SwiftUI

struct GradientProgressBar: View {
    let progress: Double
    var height: CGFloat = 8
    var gradientColors: [Color] = [AppColors.bullish, AppColors.neutral]

    private var clampedProgress: Double {
        min(max(progress, 0), 1)
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                // Background track
                RoundedRectangle(cornerRadius: height / 2)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: height)

                // Progress fill
                RoundedRectangle(cornerRadius: height / 2)
                    .fill(
                        LinearGradient(
                            colors: gradientColors,
                            startPoint: .leading,
                            endPoint: .trailing
                        )
                    )
                    .frame(width: geometry.size.width * clampedProgress, height: height)
            }
        }
        .frame(height: height)
    }
}

#Preview {
    VStack(spacing: 20) {
        GradientProgressBar(progress: 0.78)
        GradientProgressBar(progress: 0.5, gradientColors: [AppColors.primaryBlue, AppColors.accentCyan])
        GradientProgressBar(progress: 0.25, height: 12)
    }
    .padding()
    .background(AppColors.background)
}
