//
//  ChatInputBar.swift
//  ios
//
//  Molecule: Chat input bar with text field, attachment and send buttons
//

import SwiftUI

struct ChatInputBar: View {
    @Binding var text: String
    var placeholder: String = "Ask Cay AI anything..."
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
                TextField(placeholder, text: $text, axis: .vertical)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)
                    .autocapitalization(.sentences)
                    .disableAutocorrection(false)
                    .focused($isTextFieldFocused)
                    .lineLimit(1...5)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.vertical, AppSpacing.md)
            .cardSurface(cornerRadius: AppCornerRadius.large, elevation: .raised)
            .contentShape(Rectangle())
            .onTapGesture {
                isTextFieldFocused = true
            }

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
}
