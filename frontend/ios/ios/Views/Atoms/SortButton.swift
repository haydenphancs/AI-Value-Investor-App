//
//  SortButton.swift
//  ios
//
//  Atom: Sort button with icon
//

import SwiftUI

struct SortButton: View {
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: AppSpacing.xs) {
                Text("Sort")
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)

                Image(systemName: "arrow.up.arrow.down")
                    .font(AppTypography.iconXS).fontWeight(.medium)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    SortButton()
        .padding()
        .background(AppColors.background)
}
