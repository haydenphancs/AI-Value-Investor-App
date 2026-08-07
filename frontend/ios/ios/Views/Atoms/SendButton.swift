//
//  SendButton.swift
//  ios
//
//  Atom: Send button for chat input
//

import SwiftUI

struct SendButton: View {
    let isEnabled: Bool
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            ZStack {
                Circle()
                    .fill(isEnabled ? AppColors.primaryFill : AppColors.cardBackgroundLight)
                    .frame(width: 44, height: 44)

                Image(systemName: "arrow.up")
                    .font(AppTypography.iconMedium).fontWeight(.bold)
                    .foregroundColor(isEnabled ? AppColors.textOnAccent : AppColors.textMuted)
            }
        }
        .buttonStyle(PlainButtonStyle())
        .disabled(!isEnabled)
    }
}

#Preview {
    HStack(spacing: AppSpacing.lg) {
        SendButton(isEnabled: false)
        SendButton(isEnabled: true)
    }
    .padding()
    .background(AppColors.background)
}
