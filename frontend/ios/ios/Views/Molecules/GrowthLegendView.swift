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
                    .offset(y: 1)
                Text("YoY")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Value Legend
            HStack(spacing: AppSpacing.xs) {
                GrowthLegendDot(color: AppColors.growthBarBlue)
                    .offset(y: 1)
                Text("Value")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Sector Average Legend
            HStack(spacing: AppSpacing.xs) {
                GrowthLegendDot(color: AppColors.growthSectorGray, style: .dashed)
                    .offset(y: 1)
                Text("Sector Average (YoY)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
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
