//
//  AddAssetButton.swift
//  ios
//
//  Molecule: Add new asset button
//

import SwiftUI

struct AddAssetButton: View {
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "plus")
                    .font(.system(size: 14, weight: .semibold))

                Text("Add New")
                    .font(AppTypography.bodyBold)
            }
            .foregroundColor(AppColors.textPrimary)
            .padding(.horizontal, AppSpacing.xl)
            .padding(.vertical, AppSpacing.md)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.pill)
            .overlay(
                RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                    .stroke(AppColors.cardBackgroundLight, lineWidth: 1)
            )
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    AddAssetButton()
        .padding()
        .background(AppColors.background)
}
