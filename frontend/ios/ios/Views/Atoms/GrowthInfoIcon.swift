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
            ZStack {
                Circle()
                    .stroke(AppColors.textMuted, lineWidth: 1.5)
                    .frame(width: 20, height: 20)

                Text("i")
                    .font(.system(size: 12, weight: .semibold, design: .serif))
                    .foregroundColor(AppColors.textMuted)
            }
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
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            GrowthInfoIcon(action: {})
        }
        .padding()
    }
}
