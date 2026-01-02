//
//  AttachmentButton.swift
//  ios
//
//  Atom: Plus button for adding attachments in chat
//

import SwiftUI

struct AttachmentButton: View {
    var onTap: (() -> Void)?

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            ZStack {
                RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 44, height: 44)

                Image(systemName: "plus")
                    .font(.system(size: 20, weight: .medium))
                    .foregroundColor(AppColors.textSecondary)
            }
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    AttachmentButton()
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
