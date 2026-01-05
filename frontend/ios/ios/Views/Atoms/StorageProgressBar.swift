//
//  StorageProgressBar.swift
//  ios
//
//  Atom: Progress bar for storage usage display
//

import SwiftUI

struct StorageProgressBar: View {
    let progress: Double // 0.0 to 1.0

    private var progressColor: Color {
        if progress > 0.9 {
            return AppColors.bearish
        } else if progress > 0.7 {
            return AppColors.neutral
        } else {
            return AppColors.primaryBlue
        }
    }

    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                // Background track
                Capsule()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 6)

                // Progress fill
                Capsule()
                    .fill(progressColor)
                    .frame(width: geometry.size.width * CGFloat(min(progress, 1.0)), height: 6)
            }
        }
        .frame(height: 6)
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        StorageProgressBar(progress: 0.3)
        StorageProgressBar(progress: 0.73)
        StorageProgressBar(progress: 0.85)
        StorageProgressBar(progress: 0.95)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
