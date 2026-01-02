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
    @State private var showingHistory: Bool = true // Default to showing history

    var onHistoryTap: (() -> Void)?

    var body: some View {
        VStack(spacing: 0) {
            // History button header
            ChatHistoryHeader {
                handleHistoryTap()
            }

            if showingHistory {
                // History view
                ChatHistoryView(
                    onItemTap: handleHistoryItemTap,
                    onDismiss: { showingHistory = false }
                )
            } else {
                // Main chat content area
                chatContentView
            }
        }
    }

    // MARK: - Chat Content View
    private var chatContentView: some View {
        VStack(spacing: 0) {
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

    private func handleHistoryItemTap(_ item: ChatHistoryItem) {
        print("Opening chat: \(item.title)")
        // In the future, this would navigate to the specific chat
        withAnimation(.easeInOut(duration: 0.2)) {
            showingHistory = false
        }
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

#Preview("History View") {
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
