//
//  ChatTabView.swift
//  ios
//
//  Chat tab content view within the Learn/Wiser section
//

import SwiftUI

struct ChatTabView: View {
    @State private var inputText: String = ""
    @State private var suggestions: [SuggestionChip] = SuggestionChip.sampleData

    var onHistoryTap: (() -> Void)?

    var body: some View {
        VStack(spacing: 0) {
            // History button header
            ChatHistoryHeader {
                handleHistoryTap()
            }

            // Main content area (empty state for now)
            Spacer()

            // Suggestions section
            ChatSuggestionsSection(suggestions: suggestions) { chip in
                handleSuggestionTap(chip)
            }
            .padding(.bottom, AppSpacing.lg)

            // Input section
            ChatInputSection(
                inputText: $inputText,
                onAttachmentTap: handleAttachmentTap,
                onSend: handleSend,
                onVoiceTap: handleVoiceTap,
                onImageTap: handleImageTap
            )
        }
    }

    // MARK: - Action Handlers
    private func handleHistoryTap() {
        print("History tapped")
        onHistoryTap?()
    }

    private func handleSuggestionTap(_ chip: SuggestionChip) {
        print("Suggestion tapped: \(chip.text)")
        inputText = chip.text
    }

    private func handleAttachmentTap() {
        print("Attachment tapped")
    }

    private func handleSend() {
        guard !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        print("Send message: \(inputText)")
        inputText = ""
    }

    private func handleVoiceTap() {
        print("Voice input tapped")
    }

    private func handleImageTap() {
        print("Image input tapped")
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ChatTabView()
    }
    .preferredColorScheme(.dark)
}
