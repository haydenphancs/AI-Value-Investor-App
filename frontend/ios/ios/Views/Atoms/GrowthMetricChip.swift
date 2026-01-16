//
//  GrowthMetricChip.swift
//  ios
//
//  Atom: Selectable chip for growth metric type (EPS, Revenue, etc.)
//

import SwiftUI

struct GrowthMetricChip: View {
    let metricType: GrowthMetricType
    let isSelected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(metricType.rawValue)
                .font(AppTypography.calloutBold)
                .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                        .fill(isSelected ? AppColors.chipSelectedBackground : AppColors.chipUnselectedBackground)
                )
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.sm) {
            GrowthMetricChip(
                metricType: .eps,
                isSelected: false,
                action: {}
            )

            GrowthMetricChip(
                metricType: .revenue,
                isSelected: true,
                action: {}
            )

            GrowthMetricChip(
                metricType: .netIncome,
                isSelected: false,
                action: {}
            )
        }
        .padding()
    }
}
