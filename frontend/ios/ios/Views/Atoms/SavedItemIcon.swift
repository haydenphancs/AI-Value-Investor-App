//
//  SavedItemIcon.swift
//  ios
//
//  Atom: Icon displayed on the right side of saved item cards
//

import SwiftUI

struct SavedItemIcon: View {
    let type: SavedItemType

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(type.iconBackgroundColor)
                .frame(width: 36, height: 36)

            Image(systemName: type.iconName)
                .font(.system(size: 16, weight: .semibold))
                .foregroundColor(type.textColor)
        }
    }
}

#Preview {
    HStack(spacing: AppSpacing.lg) {
        ForEach(SavedItemType.allCases, id: \.rawValue) { type in
            SavedItemIcon(type: type)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
