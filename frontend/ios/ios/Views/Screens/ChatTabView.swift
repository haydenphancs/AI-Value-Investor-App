//
//  ChatTabView.swift
//  ios
//
//  Chat tab content view within the Learn/Wiser section
//

import SwiftUI

struct ChatTabView: View {
    @EnvironmentObject private var audioManager: AudioManager
    @State private var inputText: String = ""
    @State private var suggestions: [SuggestionChip] = SuggestionChip.sampleData
    @State private var conversationMessages: [RichChatMessage] = []
    @State private var showingHistory: Bool = false
    @State private var isInConversation: Bool = false
    @State private var dragOffset: CGFloat = 0

    var onHistoryTap: (() -> Void)?

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                // Chat content (slides right when history is shown)
                chatContent
                    .offset(x: showingHistory ? geometry.size.width * 0.85 : 0)

                // History panel (slides in from left)
                if showingHistory {
                    historyPanel(width: geometry.size.width)
                        .offset(x: dragOffset)
                        .transition(.move(edge: .leading))
                        .gesture(
                            DragGesture()
                                .onChanged { value in
                                    // Only allow dragging left (negative)
                                    if value.translation.width < 0 {
                                        dragOffset = value.translation.width
                                    }
                                }
                                .onEnded { value in
                                    // If dragged more than 100 points left, close the panel
                                    if value.translation.width < -100 {
                                        withAnimation(.easeInOut(duration: 0.15)) {
                                            showingHistory = false
                                            dragOffset = 0
                                        }
                                    } else {
                                        // Snap back
                                        withAnimation(.easeInOut(duration: 0.15)) {
                                            dragOffset = 0
                                        }
                                    }
                                }
                        )
                }
            }
            .animation(.easeInOut(duration: 0.15), value: showingHistory)
        }
        .onAppear {
            // Move audio player to Dynamic Island when entering chat
            audioManager.enterCompactMode()
        }
        .onDisappear {
            // Move audio player back to bottom when leaving chat
            audioManager.exitCompactMode()
        }
    }

    // MARK: - Chat Content
    private var chatContent: some View {
        VStack(spacing: 0) {
            // History button header
            ChatHistoryHeader(
                showingHistory: showingHistory,
                onHistoryTap: {
                    handleHistoryTap()
                },
                onChevronTap: {
                    handleHistoryTap()
                }
            )

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
                onImageTap: handleImageTap,
                onFocusChange: handleInputFocusChange
            )
        }
    }

    // MARK: - History Panel
    private func historyPanel(width: CGFloat) -> some View {
        HStack(spacing: 0) {
            // History content
            VStack(spacing: 0) {
                // Header with chevron to close
                HStack {
                    Text("History")
                        .font(AppTypography.headline)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    Button {
                        handleHistoryTap()
                    } label: {
                        Image(systemName: "chevron.right")
                            .font(.system(size: 16, weight: .semibold))
                            .foregroundColor(AppColors.textSecondary)
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)

                // History list
                ChatHistoryView(
                    onItemTap: { item in
                        showingHistory = false
                        print("Selected conversation: \(item.title)")
                    },
                    onDismiss: {
                        showingHistory = false
                    }
                )
            }
            .frame(width: width * 0.85)
            .background(AppColors.background)

            Spacer()
        }
    }

    // MARK: - Action Handlers
    private func handleHistoryTap() {
        withAnimation(.easeInOut(duration: 0.15)) {
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

    private func handleInputFocusChange(_ isFocused: Bool) {
        // Enter/exit compact mode for audio player when chat keyboard is active
        if isFocused {
            audioManager.enterCompactMode()
        } else {
            audioManager.exitCompactMode()
        }
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

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ChatTabView()
    }
    .environmentObject(AudioManager.shared)
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
    .environmentObject(AudioManager.shared)
    .preferredColorScheme(.dark)
}
