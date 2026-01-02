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
                    .fill(isEnabled ? AppColors.primaryBlue : AppColors.cardBackgroundLight)
                    .frame(width: 44, height: 44)

                Image(systemName: "paperplane.fill")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundColor(isEnabled ? .white : AppColors.textMuted)
                    .rotationEffect(.degrees(45))
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
    .preferredColorScheme(.dark)
}
