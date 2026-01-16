//
//  GrowthPeriodToggle.swift
//  ios
//
//  Atom: Toggle between Annual and Quarterly period views
//

import SwiftUI

struct GrowthPeriodToggle: View {
    @Binding var selectedPeriod: GrowthPeriodType

    var body: some View {
        HStack(spacing: 0) {
            ForEach(GrowthPeriodType.allCases) { period in
                Button(action: {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedPeriod = period
                    }
                }) {
                    Text(period.rawValue)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(selectedPeriod == period ? AppColors.textPrimary : AppColors.textMuted)
                        .padding(.horizontal, AppSpacing.md)
                        .padding(.vertical, AppSpacing.xs + 2)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                .fill(selectedPeriod == period ? AppColors.toggleSelectedBackground : Color.clear)
                        )
                }
                .buttonStyle(.plain)
            }
        }
        .padding(AppSpacing.xs)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.toggleBackground)
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            GrowthPeriodToggle(selectedPeriod: .constant(.annual))
            GrowthPeriodToggle(selectedPeriod: .constant(.quarterly))
        }
        .padding()
    }
}
