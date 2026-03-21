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
            Image(systemName: "plus")
                .font(AppTypography.iconSmall).fontWeight(.semibold)
                .foregroundColor(AppColors.textPrimary)
                .padding(AppSpacing.md)
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
