//
//  ChatInputBar.swift
//  ios
//
//  Molecule: Chat input bar with text field, attachment and send buttons
//

import SwiftUI

struct ChatInputBar: View {
    @Binding var text: String
    var placeholder: String = "Ask Caudex anything..."
    var onAttachmentTap: (() -> Void)?
    var onSend: (() -> Void)?
    var onFocusChange: ((Bool) -> Void)?

    @FocusState private var isTextFieldFocused: Bool

    private var canSend: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Attachment button
            AttachmentButton {
                onAttachmentTap?()
            }

            // Text field
            HStack {
                TextField(placeholder, text: $text)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)
                    .autocapitalization(.none)
                    .disableAutocorrection(true)
                    .focused($isTextFieldFocused)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)

            // Send button
            SendButton(isEnabled: canSend) {
                if canSend {
                    onSend?()
                }
            }
        }
        .onChange(of: isTextFieldFocused) { oldValue, newValue in
            onFocusChange?(newValue)
        }
    }
}

#Preview {
    struct PreviewWrapper: View {
        @State private var text = ""

        var body: some View {
            VStack {
                Spacer()
                ChatInputBar(text: $text)
                    .padding()
            }
            .background(AppColors.background)
        }
    }

    return PreviewWrapper()
        .preferredColorScheme(.dark)
}
