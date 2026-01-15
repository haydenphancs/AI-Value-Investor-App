//
//  EarningsPriceToggle.swift
//  ios
//
//  Atom: Toggle button for showing/hiding price line on earnings chart
//

import SwiftUI

struct EarningsPriceToggle: View {
    @Binding var isEnabled: Bool

    var body: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.2)) {
                isEnabled.toggle()
            }
        } label: {
            Text("Price")
                .font(AppTypography.footnoteBold)
                .foregroundColor(isEnabled ? AppColors.primaryBlue : AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.md)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    RoundedRectangle(cornerRadius: AppCornerRadius.small)
                        .stroke(isEnabled ? AppColors.primaryBlue : AppColors.cardBackgroundLight, lineWidth: 1)
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
        }
    }
}
