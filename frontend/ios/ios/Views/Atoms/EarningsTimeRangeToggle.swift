//
//  EarningsTimeRangeToggle.swift
//  ios
//
//  Atom: Toggle button for switching between 1Y and 3Y time ranges
//

import SwiftUI

struct EarningsTimeRangeToggle: View {
    @Binding var selectedRange: EarningsTimeRange

    var body: some View {
        HStack(spacing: 0) {
            ForEach(EarningsTimeRange.allCases, id: \.rawValue) { range in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedRange = range
                    }
                } label: {
                    Text(range.rawValue)
                        .font(AppTypography.footnoteBold)
                        .foregroundColor(selectedRange == range ? AppColors.textPrimary : AppColors.textSecondary)
                        .padding(.horizontal, AppSpacing.md)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            selectedRange == range ?
                            AppColors.cardBackgroundLight : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.small)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.small)
        .overlay(
            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                .stroke(AppColors.cardBackgroundLight, lineWidth: 1)
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        EarningsTimeRangeToggle(selectedRange: .constant(.oneYear))
    }
}
