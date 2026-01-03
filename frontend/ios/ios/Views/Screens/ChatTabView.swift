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
        withAnimation(.easeInOut(duration: 0.2)) {
            showingHistory.toggle()
        }
        onHistoryTap?()
    }

    private func handleSuggestionTap(_ chip: SuggestionChip) {
        inputText = chip.text
        // Auto-send the suggestion
        handleSend()
    }

    private func handleAttachmentTap() {
        print("Attachment tapped")
    }

    private func handleSend() {
        guard !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        // Create user message
        let userMessage = RichChatMessage(
            role: .user,
            content: [.text(inputText)],
            timestamp: Date()
        )

        // Start conversation with user message
        conversationMessages = [userMessage]
        let query = inputText
        inputText = ""

        // Enter conversation mode
        withAnimation(.easeInOut(duration: 0.2)) {
            isInConversation = true
        }

        // Simulate AI response
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            let aiResponse = generateMockResponse(for: query)
            conversationMessages.append(aiResponse)
        }
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

#Preview("Chat View") {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ChatTabView()
            .onAppear {
                // This preview shows history by default
            }
    }
    .preferredColorScheme(.dark)
}
