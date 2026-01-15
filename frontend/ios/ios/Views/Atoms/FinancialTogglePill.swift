//
//  FinancialTogglePill.swift
//  ios
//
//  Atom: Toggle pill button for on/off states (e.g., Price toggle in Earnings)
//

import SwiftUI

struct FinancialTogglePill: View {
    let label: String
    @Binding var isOn: Bool

    var body: some View {
        Button {
            withAnimation(.easeInOut(duration: 0.15)) {
                isOn.toggle()
            }
        } label: {
            HStack(spacing: AppSpacing.xs) {
                Text(label)
                    .font(AppTypography.caption)
                    .foregroundColor(isOn ? AppColors.textPrimary : AppColors.textSecondary)

                // Custom toggle indicator
                ZStack {
                    Capsule()
                        .fill(isOn ? AppColors.primaryBlue : AppColors.cardBackgroundLight)
                        .frame(width: 32, height: 18)

                    Circle()
                        .fill(AppColors.textPrimary)
                        .frame(width: 14, height: 14)
                        .offset(x: isOn ? 6 : -6)
                }
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.sm)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                    .fill(isOn ? AppColors.primaryBlue.opacity(0.2) : AppColors.cardBackground)
            )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: 16) {
            FinancialTogglePill(label: "Price", isOn: .constant(true))
            FinancialTogglePill(label: "Price", isOn: .constant(false))
        }
    }
}
