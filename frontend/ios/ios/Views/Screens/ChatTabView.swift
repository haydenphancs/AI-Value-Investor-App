//
//  ChatTabView.swift
//  ios
//
//  Chat tab content view within the Learn/Wiser section.
//  Connected to ChatViewModel for real AI conversations via the backend.
//

import SwiftUI

struct ChatTabView: View {
    @EnvironmentObject private var audioManager: AudioManager
    @StateObject private var viewModel = ChatViewModel()
    @State private var inputText: String = ""
    @State private var suggestions: [SuggestionChip] = SuggestionChip.sampleData
    @State private var showingHistory: Bool = false
    @State private var dragOffset: CGFloat = 0

    var initialPrompt: String?
    var initialStockId: String?
    var onHistoryTap: (() -> Void)?

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                // Chat content (hidden when history is shown)
                chatContent
                    .opacity(showingHistory ? 0 : 1)

                // History panel (slides in from left)
                if showingHistory {
                    historyPanel(width: geometry.size.width)
                        .offset(x: dragOffset)
                        .transition(.move(edge: .leading))
                        .gesture(
                            DragGesture()
                                .onChanged { value in
                                    if value.translation.width < 0 {
                                        dragOffset = value.translation.width
                                    }
                                }
                                .onEnded { value in
                                    if value.translation.width < -100 {
                                        withAnimation(.easeInOut(duration: 0.15)) {
                                            showingHistory = false
                                            dragOffset = 0
                                        }
                                    } else {
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
            audioManager.enterCompactMode()
            // Pre-fill prompt if provided (e.g., from AI Analyst button)
            if let prompt = initialPrompt, inputText.isEmpty {
                inputText = prompt
            }
            // Load history for the sidebar
            viewModel.loadHistory()
        }
        .onDisappear {
            audioManager.exitCompactMode()
        }
    }

    // MARK: - Chat Content

    private var chatContent: some View {
        VStack(spacing: 0) {
            // History button header
            ChatHistoryHeader(
                showingHistory: showingHistory,
                onHistoryTap: { handleHistoryTap() },
                onChevronTap: { handleHistoryTap() }
            )

            if viewModel.isInConversation || viewModel.isAITyping {
                // Active conversation: show messages
                conversationArea
            } else if viewModel.isLoadingSession {
                // Loading a conversation from history
                Spacer()
                ProgressView()
                    .tint(AppColors.primaryBlue)
                Spacer()
            } else {
                // Empty state: Spacer pushes chat bar to bottom
                Spacer()
            }

            // Error banner
            if let error = viewModel.errorMessage {
                errorBanner(error)
            }

            // AI Chat Bar with suggestion pills
            CaydexAIChatBar(
                inputText: $inputText,
                suggestions: viewModel.isInConversation ? [] : suggestions.map(\.text),
                onSuggestionTap: { text in
                    if let chip = suggestions.first(where: { $0.text == text }) {
                        handleSuggestionTap(chip)
                    }
                },
                onSend: handleSend
            )
        }
    }

    // MARK: - Conversation Area

    private var conversationArea: some View {
        VStack(spacing: 0) {
            // Message list
            ChatMessagesList(messages: viewModel.messages)

            // Typing indicator
            if viewModel.isAITyping {
                typingIndicator
            }
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
            Button {
                viewModel.errorMessage = nil
            } label: {
                Image(systemName: "xmark")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
        .background(AppColors.bearish.opacity(0.1))
    }

    // MARK: - History Panel

    private func historyPanel(width: CGFloat) -> some View {
        HStack(spacing: 0) {
            VStack(spacing: 0) {
                // Header with close button
                HStack {
                    Text("History")
                        .font(AppTypography.headingSmall)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    Button {
                        handleHistoryTap()
                    } label: {
                        Image(systemName: "chevron.right")
                            .font(AppTypography.iconDefault).fontWeight(.semibold)
                            .foregroundColor(AppColors.textSecondary)
                    }
                    .buttonStyle(.plain)
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)

                // History list — connected to real data
                ChatHistoryView(
                    historyGroups: viewModel.historyGroups,
                    isLoading: viewModel.isLoadingHistory,
                    onItemTap: { item in
                        if let sessionId = item.sessionId {
                            viewModel.loadConversation(sessionId: sessionId)
                        }
                        withAnimation(.easeInOut(duration: 0.15)) {
                            showingHistory = false
                        }
                        print("📖 [ChatTab] Selected conversation: \(item.title)")
                    },
                    onItemDelete: { item in
                        if let sessionId = item.sessionId {
                            viewModel.deleteSession(sessionId)
                        }
                    },
                    onDismiss: {
                        withAnimation(.easeInOut(duration: 0.15)) {
                            showingHistory = false
                        }
                    }
                )
            }
            .frame(width: width)
            .background(AppColors.background)
        }
    }

    // MARK: - Action Handlers

    private func handleHistoryTap() {
        withAnimation(.easeInOut(duration: 0.15)) {
            showingHistory.toggle()
        }
        if showingHistory {
            viewModel.loadHistory()
        }
        onHistoryTap?()
    }

    private func handleSuggestionTap(_ chip: SuggestionChip) {
        inputText = chip.text
        handleSend()
    }

    private func handleSend() {
        guard !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        let message = inputText
        inputText = ""

        viewModel.sendMessage(message)
    }
}

// MARK: - Typing Dot Animation

struct TypingDot: View {
    let delay: Double
    @State private var isAnimating = false

    var body: some View {
        Circle()
            .fill(AppColors.primaryBlue)
            .frame(width: 8, height: 8)
            .opacity(isAnimating ? 1.0 : 0.3)
            .animation(
                .easeInOut(duration: 0.6)
                .repeatForever(autoreverses: true)
                .delay(delay),
                value: isAnimating
            )
            .onAppear { isAnimating = true }
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
