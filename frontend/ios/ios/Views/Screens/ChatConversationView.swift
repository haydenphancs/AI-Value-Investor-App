//
//  ChatConversationView.swift
//  ios
//
//  Standalone chat conversation view with messages and input.
//  Can be used with a shared ChatViewModel or as a preview with sample data.
//

import SwiftUI

struct ChatConversationView: View {
    @ObservedObject var viewModel: ChatViewModel
    @State private var inputText: String = ""

    var body: some View {
        VStack(spacing: 0) {
            // Messages list
            ChatMessagesList(messages: viewModel.messages)

            // Typing indicator
            if viewModel.isAITyping {
                typingIndicator
            }

            // Error banner
            if let error = viewModel.errorMessage {
                errorBanner(error)
            }

            // Input section
            ChatInputSection(
                inputText: $inputText,
                onAttachmentTap: { print("Attachment tapped") },
                onSend: handleSend,
                onVoiceTap: { print("Voice input tapped") },
                onImageTap: { print("Image input tapped") }
            )
        }
    }

    // MARK: - Typing Indicator

    private var typingIndicator: some View {
        HStack(spacing: AppSpacing.sm) {
            TypingDot(delay: 0.0)
            TypingDot(delay: 0.2)
            TypingDot(delay: 0.4)
            Text("Cay AI is thinking...")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: - Error Banner

    private func errorBanner(_ message: String) -> some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(AppColors.bearish)
            Text(message)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
            Spacer()
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
        .background(AppColors.bearish.opacity(0.1))
    }

    // MARK: - Action Handlers

    private func handleSend() {
        guard !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        let message = inputText
        inputText = ""

        viewModel.sendMessage(message)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        // Preview with sample data using a mock ViewModel
        ChatConversationView(viewModel: {
            let vm = ChatViewModel()
            return vm
        }())
    }
    .preferredColorScheme(.dark)
}
