//
//  GrowthLegendView.swift
//  ios
//
//  Molecule: Legend showing YoY, Value, and Sector Average indicators
//

import SwiftUI

struct GrowthLegendView: View {
    var body: some View {
        HStack(spacing: AppSpacing.xl) {
            // YoY Legend
            HStack(spacing: AppSpacing.xs) {
                GrowthLegendDot(color: AppColors.growthYoYYellow)
                Text("YoY")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Value Legend
            HStack(spacing: AppSpacing.xs) {
                GrowthLegendDot(color: AppColors.growthBarBlue)
                Text("Value")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Sector Average Legend
            HStack(spacing: AppSpacing.xs) {
                GrowthLegendDot(color: AppColors.growthSectorGray, style: .dashed)
                VStack(spacing: 0) {
                    Text("Sector Average")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                    Text("(YoY)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        GrowthLegendView()
            .padding()
    }
}
