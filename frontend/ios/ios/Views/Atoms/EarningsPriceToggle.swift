//
//  EarningsPriceToggle.swift
//  ios
//
//  Atom: Bordered pill toggle for an overlay series on the earnings chart
//  (Price, FCF, …). `label` + `activeColor` default to "Price"/blue for the
//  original call site; pass them to reuse it for another series.
//

import SwiftUI

struct EarningsPriceToggle: View {
    @Binding var isEnabled: Bool
    var label: String = "Price"
    var activeColor: Color = AppColors.primaryBlue

    var body: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.2)) {
                isEnabled.toggle()
            }
        } label: {
            Text(label)
                .font(AppTypography.labelSmallEmphasis)
                .foregroundColor(isEnabled ? activeColor : AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.small)
                        .stroke(isEnabled ? activeColor : AppColors.cardBackgroundLight, lineWidth: 1)
                )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            EarningsPriceToggle(isEnabled: .constant(false))
            EarningsPriceToggle(isEnabled: .constant(true))
            EarningsPriceToggle(isEnabled: .constant(true), label: "FCF",
                                activeColor: AppColors.profitFCFMargin)
        }
    }
}
