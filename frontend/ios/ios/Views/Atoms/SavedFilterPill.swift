//
//  SavedFilterPill.swift
//  ios
//
//  Atom: Filter pill button for saved items filter row
//

import SwiftUI

struct SavedFilterPill: View {
    let filter: SavedFilterType
    let isSelected: Bool
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            Text(filter.rawValue)
                .font(AppTypography.calloutBold)
                .foregroundColor(isSelected ? AppColors.textPrimary : AppColors.textSecondary)
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.sm)
                .background(
                    Capsule()
                        .fill(isSelected ? AppColors.primaryBlue : AppColors.cardBackground)
                )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    HStack(spacing: AppSpacing.sm) {
        SavedFilterPill(filter: .all, isSelected: true)
        SavedFilterPill(filter: .books, isSelected: false)
        SavedFilterPill(filter: .concepts, isSelected: false)
        SavedFilterPill(filter: .reports, isSelected: false)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
