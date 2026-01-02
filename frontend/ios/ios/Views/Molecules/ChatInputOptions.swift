//
//  ChatInputOptions.swift
//  ios
//
//  Molecule: Voice and Image input option buttons for chat
//

import SwiftUI

struct ChatInputOptions: View {
    var onVoiceTap: (() -> Void)?
    var onImageTap: (() -> Void)?

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            InputOptionButton(type: .voice) {
                onVoiceTap?()
            }

            InputOptionButton(type: .image) {
                onImageTap?()
            }
        }
    }
}

#Preview {
    ChatInputOptions()
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
