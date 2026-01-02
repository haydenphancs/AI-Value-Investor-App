//
//  InputOptionButton.swift
//  ios
//
//  Atom: Option button for Voice/Image input in chat
//

import SwiftUI

struct InputOptionButton: View {
    let type: ChatAttachmentType
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: type.iconName)
                    .font(.system(size: 14, weight: .medium))

                Text(type.rawValue)
                    .font(AppTypography.callout)
            }
            .foregroundColor(AppColors.textSecondary)
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.sm)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                    .fill(AppColors.cardBackground)
            )
            .overlay(
                RoundedRectangle(cornerRadius: AppCornerRadius.pill)
                    .stroke(AppColors.cardBackgroundLight, lineWidth: 1)
            )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    HStack(spacing: AppSpacing.md) {
        InputOptionButton(type: .voice)
        InputOptionButton(type: .image)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
