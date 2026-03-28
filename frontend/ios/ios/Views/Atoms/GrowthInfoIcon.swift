//
//  GrowthInfoIcon.swift
//  ios
//
//  Atom: Info icon button that displays growth explanation sheet
//

import SwiftUI

struct GrowthInfoIcon: View {
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(systemName: "info.circle")
                .font(AppTypography.iconSmall).fontWeight(.medium)
                .foregroundColor(AppColors.textMuted)
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        HStack(spacing: AppSpacing.sm) {
            Text("Growth")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)

            GrowthInfoIcon(action: {})
        }
        .padding()
    }
}
