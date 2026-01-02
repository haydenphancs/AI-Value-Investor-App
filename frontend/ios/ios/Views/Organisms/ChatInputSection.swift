//
//  ChatInputSection.swift
//  ios
//
//  Organism: Complete chat input section with input bar and options
//

import SwiftUI

struct ChatInputSection: View {
    @Binding var inputText: String
    var onAttachmentTap: (() -> Void)?
    var onSend: (() -> Void)?
    var onVoiceTap: (() -> Void)?
    var onImageTap: (() -> Void)?

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // Input bar
            ChatInputBar(
                text: $inputText,
                onAttachmentTap: onAttachmentTap,
                onSend: onSend
            )

            // Voice and Image options
            ChatInputOptions(
                onVoiceTap: onVoiceTap,
                onImageTap: onImageTap
            )
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.bottom, AppSpacing.md)
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var text = ""

        var body: some View {
            VStack {
                Spacer()
                ChatInputSection(inputText: $text)
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
