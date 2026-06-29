//
//  AIChatScreen.swift
//  ios
//
//  The unified full-screen "Ask Cay AI" chat. Presented as a `.fullScreenCover` from every
//  "Ask Cay AI" bar (asset detail / report / reading screens) and from the Wiser "Chat" tab.
//
//  Design goals:
//   • Maximum space for the conversation — NO bottom tab bar, NO top header/logo/profile.
//   • Close ✕ on the LEFT, history (past conversations) icon on the RIGHT.
//   • Dismiss by ✕ or by swiping DOWN on the top bar (`.fullScreenCover` has no native
//     interactive dismiss, so we drive it from a DragGesture on the top bar only — it must
//     not fight the messages ScrollView or the history panel's horizontal swipe).
//   • "Resume the last conversation": the CALLER owns the `@StateObject ChatViewModel` and
//     passes it in here as an `@ObservedObject`, so conversation state survives open/close.
//     This screen NEVER calls `resetConversation()`.
//   • Audio collapses to the Dynamic Island / status island via `.globalAudioOverlay` so the
//     bottom stays clear for the input bar and the player persists above the cover.
//

import SwiftUI

struct AIChatScreen: View {
    @ObservedObject var viewModel: ChatViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var inputText: String = ""
    @State private var suggestions: [SuggestionChip] = SuggestionChip.sampleData
    @State private var showingHistory: Bool = false
    /// Horizontal drag for closing the history panel (matches the old ChatTabView panel).
    @State private var historyDragOffset: CGFloat = 0
    /// Vertical drag for swipe-down-to-dismiss (applied to the whole screen).
    @State private var dismissOffset: CGFloat = 0
    /// Stable token keying this screen's audio compact reason.
    @State private var chatToken = UUID().uuidString

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                AppColors.background.ignoresSafeArea()

                // Chat content (hidden when history is shown)
                chatContent
                    .opacity(showingHistory ? 0 : 1)

                // History panel (slides in from the left)
                if showingHistory {
                    historyPanel(width: geometry.size.width)
                        .offset(x: historyDragOffset)
                        .transition(.move(edge: .leading))
                        .gesture(
                            DragGesture()
                                .onChanged { value in
                                    if value.translation.width < 0 {
                                        historyDragOffset = value.translation.width
                                    }
                                }
                                .onEnded { value in
                                    if value.translation.width < -100 {
                                        withAnimation(.easeInOut(duration: 0.15)) {
                                            showingHistory = false
                                            historyDragOffset = 0
                                        }
                                    } else {
                                        withAnimation(.easeInOut(duration: 0.15)) {
                                            historyDragOffset = 0
                                        }
                                    }
                                }
                        )
                }
            }
            .animation(.easeInOut(duration: 0.15), value: showingHistory)
        }
        .offset(y: dismissOffset)
        // Keep the audio player visible above this cover and collapsed to the island so the
        // bottom stays clear for the chat bar. Released on dismiss via the modifier's onDisappear.
        .globalAudioOverlay(token: chatToken, forceCompact: true)
        .onAppear {
            // Clear any stale transient error from a prior failed send so a freshly reopened chat
            // doesn't show a leftover banner. Conversation/session/messages are intentionally
            // preserved (resume) — only the transient error resets.
            viewModel.errorMessage = nil
            // Load the history list so the right-hand history icon is ready. Does NOT touch the
            // active conversation — reopening resumes whatever the caller's ViewModel holds.
            viewModel.loadHistory()
        }
    }

    // MARK: - Top Bar

    private var topBar: some View {
        HStack {
            // Close (left)
            Button { dismiss() } label: {
                Image(systemName: "xmark")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(AppColors.textSecondary)
            }
            .buttonStyle(.plain)

            Spacer()

            // History (right)
            HistoryButton { handleHistoryTap() }
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
        .contentShape(Rectangle())
        // Swipe DOWN on the top bar to dismiss. Simultaneous so the ✕ / history buttons still
        // receive their taps. Only the top bar carries this — never the messages ScrollView.
        .simultaneousGesture(swipeDownToDismiss)
    }

    private var swipeDownToDismiss: some Gesture {
        DragGesture()
            .onChanged { value in
                // Only react to downward drags; ignore upward/diagonal noise.
                if value.translation.height > 0 && !showingHistory {
                    dismissOffset = value.translation.height
                }
            }
            .onEnded { value in
                if value.translation.height > 120 && !showingHistory {
                    // Animate the content back to rest as the cover dismisses, so it doesn't begin
                    // the system slide-down already translated ~130pt (a visible jump/over-travel).
                    withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                        dismissOffset = 0
                    }
                    dismiss()
                } else {
                    withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) {
                        dismissOffset = 0
                    }
                }
            }
    }

    // MARK: - Chat Content

    private var chatContent: some View {
        VStack(spacing: 0) {
            topBar

            if !viewModel.messages.isEmpty || viewModel.isAITyping {
                // Active conversation: show messages. Gating on `messages` (not `isInConversation`)
                // keeps a seeded/orphaned user bubble visible even before the session id lands, and
                // after a createSession failure (currentSessionId stays nil) — so the typed message
                // never silently vanishes into the empty state.
                conversationArea
            } else if viewModel.isLoadingSession {
                // Loading a conversation from history
                Spacer()
                ProgressView()
                    .tint(AppColors.primaryBlue)
                Spacer()
            } else {
                // Empty state: Spacer pushes the chat bar to the bottom
                Spacer()
            }

            // Error banner
            if let error = viewModel.errorMessage {
                errorBanner(error)
            }

            // AI Chat Bar with suggestion pills (suggestions hidden once a conversation starts)
            CaydexAIChatBar(
                inputText: $inputText,
                // Show suggestion chips ONLY on a truly empty chat — hide them the instant a
                // conversation is seeded (messages non-empty) or the AI is replying, even before
                // the session id lands. Mirrors the conversation-area gate above.
                suggestions: (viewModel.messages.isEmpty && !viewModel.isAITyping) ? suggestions.map(\.text) : [],
                onSuggestionTap: { text in
                    if let chip = suggestions.first(where: { $0.text == text }) {
                        handleSuggestionTap(chip)
                    }
                },
                onSend: handleSend,
                // Grey out / block send while a reply is in flight (matches the ViewModel guard).
                isBusy: viewModel.isAITyping
            )
        }
    }

    // MARK: - Conversation Area

    private var conversationArea: some View {
        VStack(spacing: 0) {
            ChatMessagesList(messages: viewModel.messages)

            if viewModel.isAITyping {
                typingIndicator
            }
        }
    }

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

    // MARK: - Action Handlers

    private func handleHistoryTap() {
        withAnimation(.easeInOut(duration: 0.15)) {
            showingHistory.toggle()
        }
        if showingHistory {
            viewModel.loadHistory()
        }
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

// MARK: - Presentation modifier

extension View {
    /// Present the unified full-screen AI chat. The caller owns the `ChatViewModel` (as a
    /// `@StateObject`) so the conversation resumes when reopened. Audio is re-injected across
    /// the cover boundary by `AIChatScreen`'s `.globalAudioOverlay`.
    func aiChatCover(isPresented: Binding<Bool>, viewModel: ChatViewModel) -> some View {
        fullScreenCover(isPresented: isPresented) {
            AIChatScreen(viewModel: viewModel)
                .preferredColorScheme(.dark)
        }
    }
}

#Preview {
    AIChatScreen(viewModel: ChatViewModel())
        .preferredColorScheme(.dark)
}
