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
    @State private var conversationMessages: [RichChatMessage] = []
    @State private var showingHistory: Bool = false
    @State private var isInConversation: Bool = false

    var onHistoryTap: (() -> Void)?

    var body: some View {
        VStack(spacing: 0) {
            // History button header
            ChatHistoryHeader(onHistoryTap: {
                handleHistoryTap()
            })

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
        .sheet(isPresented: $showingHistory) {
            ChatHistorySheet(onDismiss: {
                showingHistory = false
            }, onItemTap: { item in
                showingHistory = false
                // Could load the selected conversation here
                print("Selected conversation: \(item.title)")
            })
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
    
    // MARK: - Helper Methods
    private func generateMockResponse(for query: String) -> RichChatMessage {
        let responseText = "This is a mock response to: \"\(query)\". In a real implementation, this would connect to your AI service."
        return RichChatMessage(
            role: .assistant,
            content: [.text(responseText)],
            timestamp: Date()
        )
    }
}

// MARK: - Chat History Sheet
struct ChatHistorySheet: View {
    var onDismiss: (() -> Void)?
    var onItemTap: ((ChatHistoryItem) -> Void)?

    var body: some View {
        NavigationView {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                ChatHistoryView(
                    onItemTap: { item in
                        onItemTap?(item)
                    },
                    onDismiss: onDismiss
                )
            }
            .navigationTitle("Chat History")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Close") {
                        onDismiss?()
                    }
                }
            }
        }
        .presentationDetents([.large])
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
