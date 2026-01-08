//
//  TimeRangeButton.swift
//  ios
//
//  Atom: Time range selection button for chart
//

import SwiftUI

struct TimeRangeButton: View {
    let range: ChartTimeRange
    let isSelected: Bool
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Text(range.displayName)
                .font(AppTypography.footnoteBold)
                .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textMuted)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    isSelected
                        ? RoundedRectangle(cornerRadius: AppCornerRadius.small)
                            .fill(AppColors.cardBackgroundLight)
                        : nil
                )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    HStack(spacing: AppSpacing.xs) {
        TimeRangeButton(range: .oneDay, isSelected: false)
        TimeRangeButton(range: .oneWeek, isSelected: false)
        TimeRangeButton(range: .threeMonths, isSelected: true)
        TimeRangeButton(range: .oneYear, isSelected: false)
        TimeRangeButton(range: .fiveYears, isSelected: false)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
