//
//  EarningsDataTypeToggle.swift
//  ios
//
//  Atom: Toggle button for switching between EPS and Revenue data types
//

import SwiftUI

struct EarningsDataTypeToggle: View {
    @Binding var selectedType: EarningsDataType

    var body: some View {
        HStack(spacing: 0) {
            ForEach(EarningsDataType.allCases, id: \.rawValue) { type in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedType = type
                    }
                } label: {
                    Text(type.rawValue)
                        .font(AppTypography.footnoteBold)
                        .foregroundColor(selectedType == type ? AppColors.textPrimary : AppColors.textSecondary)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            selectedType == type ?
                            AppColors.primaryBlue : Color.clear
                        )
                        .cornerRadius(AppCornerRadius.medium)
                }
                .buttonStyle(PlainButtonStyle())
            }
        }
        .background(AppColors.cardBackgroundLight)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        EarningsDataTypeToggle(selectedType: .constant(.eps))
    }
}
