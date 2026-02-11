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
            // In Flow: dot on left
            legendItem(color: AppColors.bullish, label: "In Flow", dotOnRight: false)
            Spacer()
            // Out Flow: dot on right
            legendItem(color: AppColors.bearish, label: "Out Flow", dotOnRight: true)
        }
    }

    private func legendItem(color: Color, label: String, dotOnRight: Bool) -> some View {
        HStack(spacing: AppSpacing.xs) {
            if !dotOnRight {
                Circle()
                    .fill(color)
                    .frame(width: 8, height: 8)
            }

            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            if dotOnRight {
                Circle()
                    .fill(color)
                    .frame(width: 8, height: 8)
            }
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
