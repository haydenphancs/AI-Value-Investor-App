//
//  RecentActivitiesFlowLegend.swift
//  ios
//
//  Molecule: Legend for the In Flow / Out Flow bar
//  Shows green for In Flow and red for Out Flow
//

import SwiftUI

struct RecentActivitiesFlowLegend: View {
    var body: some View {
        HStack(spacing: AppSpacing.lg) {
            legendItem(color: AppColors.bullish, label: "In Flow")
            legendItem(color: AppColors.bearish, label: "Out Flow")
        }
    }

    private func legendItem(color: Color, label: String) -> some View {
        HStack(spacing: AppSpacing.xs) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)

            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        RecentActivitiesFlowLegend()
    }
}
