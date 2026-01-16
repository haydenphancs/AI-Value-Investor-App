//
//  ProfitPowerPeriodToggle.swift
//  ios
//
//  Atom: Toggle between Annual and Quarterly period views for Profit Power chart
//

import SwiftUI

struct ProfitPowerPeriodToggle: View {
    @Binding var selectedPeriod: ProfitPowerPeriodType

    var body: some View {
        HStack(spacing: 0) {
            ForEach(ProfitPowerPeriodType.allCases) { period in
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
            ProfitPowerPeriodToggle(selectedPeriod: .constant(.annual))
            ProfitPowerPeriodToggle(selectedPeriod: .constant(.quarterly))
        }
        .padding()
    }
}
