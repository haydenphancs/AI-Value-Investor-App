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
    /// Search query for the history panel's bottom search bar.
    @State private var historySearchText: String = ""
    /// Horizontal drag for closing the history panel (matches the old ChatTabView panel).
    @State private var historyDragOffset: CGFloat = 0
    /// Vertical drag for swipe-down-to-dismiss (applied to the whole screen).
    @State private var dismissOffset: CGFloat = 0
    /// Stable token keying this screen's audio compact reason.
    @State private var chatToken = UUID().uuidString
    /// The history row whose Pin/Rename/Delete popup is open (nil = closed).
    @State private var menuItem: ChatHistoryItem?
    /// The history row being renamed (drives the rename alert) + its editable text.
    @State private var renamingItem: ChatHistoryItem?
    @State private var renameText: String = ""

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                AppColors.background.ignoresSafeArea()

                // Futuristic aura — soft blue→cyan glow behind the top bar and the input bar.
                // Sibling/overlay layer only (never wraps interactive content); hidden with history.
                if !showingHistory {
                    VStack {
                        ChatAuraGlow()
                            .frame(height: 240)
                        Spacer()
                        ChatAuraGlow(intensity: 0.9)
                            .frame(height: 220)
                    }
                    .ignoresSafeArea()
                    .allowsHitTesting(false)
                }

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
            // Load the history list so the top-left history icon is ready. Does NOT touch the
            // active conversation — reopening resumes whatever the caller's ViewModel holds.
            viewModel.loadHistory()
        }
    }

    // MARK: - Top Bar

    private var topBar: some View {
        HStack {
            // History (left)
            HistoryButton { handleHistoryTap() }

            Spacer()

            // Close (right)
            Button { dismiss() } label: {
                Image(systemName: "xmark")
                    .font(AppTypography.iconDefault).fontWeight(.semibold)
                    .foregroundColor(AppColors.textSecondary)
            }
            .buttonStyle(.plain)
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
            // "Grounded on …" chip — shows what Cay AI is reading for this chat.
            if let ctx = viewModel.currentContextType, ctx != .none {
                GroundedContextChip(contextType: ctx, referenceLabel: groundingReferenceLabel)
                    .padding(.top, AppSpacing.sm)
                    .padding(.bottom, AppSpacing.xs)
            }

            ChatMessagesList(
                messages: viewModel.messages,
                streamingMessageId: viewModel.streamingMessageId,
                onFollowUpTap: handleFollowUpTap
            )

            // Brief "thinking" dots only in the tiny window BEFORE the assistant bubble appears
            // (its thinking card then conveys progress). This also covers the non-streaming
            // fallback path, which emits no thinking events.
            if viewModel.isAITyping && viewModel.messages.last?.role == .user {
                typingIndicator
            }
        }
    }

    /// A user-friendly reference for the grounding chip (a ticker for asset/report
    /// contexts; hidden for slug/order-based contexts, which aren't readable).
    private var groundingReferenceLabel: String? {
        guard let ref = viewModel.currentReferenceId, !ref.isEmpty else { return nil }
        switch viewModel.currentContextType {
        case .tickerReport, .stock, .etf, .crypto, .index, .commodity:
            return ref.split(separator: "|").first.map(String.init)?.uppercased()
        default:
            return nil
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
                Text("Caydex")
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

            // History list — connected to real data (filtered by the search bar below).
            ChatHistoryView(
                historyGroups: filteredHistoryGroups,
                isLoading: viewModel.isLoadingHistory,
                onItemTap: { item in
                    if let sessionId = item.sessionId {
                        viewModel.loadConversation(sessionId: sessionId)
                    }
                    withAnimation(.easeInOut(duration: 0.15)) {
                        showingHistory = false
                    }
                },
                onItemMoreOptions: { item in
                    menuItem = item
                },
                onDismiss: {
                    withAnimation(.easeInOut(duration: 0.15)) {
                        showingHistory = false
                    }
                },
                searchQuery: historySearchText
            )

            // Transient error from a failed pin / rename / delete (dismissable).
            if let actionError = viewModel.historyActionError {
                historyActionBanner(actionError)
            }

            // Bottom bar: search the history (left) + start a new chat (right, white box).
            historyBottomBar
        }
        .frame(width: width)
        .background(AppColors.background)
        // Floating Liquid-Glass options popup, anchored under the tapped row's 3-dot.
        .overlayPreferenceValue(ChatRowMenuAnchorKey.self) { anchors in
            chatRowMenuOverlay(anchors)
        }
        .alert("Rename chat", isPresented: renameAlertPresented) {
            TextField("Name", text: $renameText)
            Button("Cancel", role: .cancel) { renamingItem = nil }
            Button("Save") {
                if let item = renamingItem, let sid = item.sessionId {
                    viewModel.renameSession(sid, title: renameText)
                }
                renamingItem = nil
            }
            .disabled(renameText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        } message: {
            Text("Enter a new name for this conversation.")
        }
    }

    // MARK: - History Row Options Menu (Liquid Glass)

    private let menuWidth: CGFloat = 200

    private var renameAlertPresented: Binding<Bool> {
        Binding(get: { renamingItem != nil }, set: { if !$0 { renamingItem = nil } })
    }

    @ViewBuilder
    private func chatRowMenuOverlay(_ anchors: [String: Anchor<CGRect>]) -> some View {
        // The open menu's anchor, looked up by STABLE sessionId.
        let activeAnchor: Anchor<CGRect>? = menuItem.flatMap { $0.sessionId }.flatMap { anchors[$0] }
        GeometryReader { proxy in
            if menuItem != nil {
                ZStack(alignment: .topLeading) {
                    // Dismiss scrim is drawn whenever a menu is logically open — even if its row
                    // scrolled out of the LazyVStack or got filtered out by search (anchor gone) — so
                    // the user can always tap to dismiss instead of being stuck with an invisible menu.
                    Color.black.opacity(0.001)
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                        .contentShape(Rectangle())
                        .onTapGesture { menuItem = nil }

                    // The popup itself only renders while its row is on-screen (anchor resolvable).
                    if let item = menuItem, let anchor = activeAnchor {
                        let rect = proxy[anchor]
                        chatRowMenuPanel(for: item)
                            .frame(width: menuWidth, alignment: .leading)
                            .padding(.vertical, AppSpacing.xs)
                            // Same iOS Liquid Glass material as the portfolio header popup.
                            .glassEffect(.regular, in: RoundedRectangle(cornerRadius: AppCornerRadius.large))
                            .offset(
                                x: menuXOffset(rect: rect, in: proxy.size.width),
                                y: menuYOffset(rect: rect, in: proxy.size.height)
                            )
                    }
                }
            }
        }
        // Intercept touches only while a menu is open — gated on menuItem (NOT the anchor) so the
        // dismiss scrim stays tappable after the popup's anchor is lost; closed => list interactive.
        .allowsHitTesting(menuItem != nil)
    }

    /// Trailing-align the popup to the 3-dot, clamped so it never spills past a panel edge.
    private func menuXOffset(rect: CGRect, in totalWidth: CGFloat) -> CGFloat {
        let edgeInset: CGFloat = 8
        let desired = rect.maxX - menuWidth
        let maxX = totalWidth - menuWidth - edgeInset
        return min(max(edgeInset, desired), max(edgeInset, maxX))
    }

    /// Estimated popup height (3 rows + divider + vertical padding).
    private let menuEstimatedHeight: CGFloat = 124

    /// Place the popup below the 3-dot, but flip it ABOVE the row when below would overflow the
    /// bottom (reserving room for the search / new-chat bar) so it never covers the bottom bar.
    private func menuYOffset(rect: CGRect, in totalHeight: CGFloat) -> CGFloat {
        let below = rect.maxY + AppSpacing.xs
        let bottomReserve: CGFloat = 96
        if below + menuEstimatedHeight > totalHeight - bottomReserve {
            return max(AppSpacing.xs, rect.minY - menuEstimatedHeight - AppSpacing.xs)
        }
        return below
    }

    private func chatRowMenuPanel(for item: ChatHistoryItem) -> some View {
        VStack(spacing: 0) {
            ChatMenuRow(
                title: item.isSaved ? "Unpin" : "Pin",
                systemImage: item.isSaved ? "pin.slash" : "pin"
            ) { handlePin(item) }

            ChatMenuRow(title: "Rename", systemImage: "pencil") { handleRename(item) }

            ChatMenuDivider()

            ChatMenuRow(title: "Delete", systemImage: "trash", isDestructive: true) { handleDelete(item) }
        }
    }

    private func handlePin(_ item: ChatHistoryItem) {
        menuItem = nil
        if let sid = item.sessionId {
            viewModel.setPinned(sid, pinned: !item.isSaved)
        }
    }

    private func handleRename(_ item: ChatHistoryItem) {
        menuItem = nil
        renameText = item.title
        renamingItem = item
    }

    private func handleDelete(_ item: ChatHistoryItem) {
        menuItem = nil
        if let sid = item.sessionId {
            viewModel.deleteSession(sid)
        }
    }

    private func historyActionBanner(_ message: String) -> some View {
        HStack(spacing: AppSpacing.sm) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(AppColors.bearish)
            Text(message)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
            Spacer()
            Button {
                viewModel.historyActionError = nil
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

    /// Search history (left, fills the row) + a white-boxed "new chat" button (right).
    private var historyBottomBar: some View {
        HStack(spacing: AppSpacing.xxxl) {
            // Search field
            HStack(spacing: AppSpacing.sm) {
                Image(systemName: "magnifyingglass")
                    .font(AppTypography.iconSmall)
                    .foregroundColor(AppColors.textMuted)

                TextField("Search history", text: $historySearchText)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)
                    .submitLabel(.search)

                if !historySearchText.isEmpty {
                    Button {
                        historySearchText = ""
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(AppTypography.iconSmall)
                            .foregroundColor(AppColors.textMuted)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, AppSpacing.sm)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
            .frame(maxWidth: .infinity)

            // New chat — bigger icon in a smaller white box.
            Button {
                handleNewChat()
            } label: {
                Image(systemName: "square.and.pencil")
                    .font(AppTypography.iconLarge).fontWeight(.semibold)
                    .foregroundColor(.black)
                    // `square.and.pencil`'s pencil tip extends the glyph box upward, so plain
                    // frame-centering renders it visually low. Nudge the icon up (box unchanged).
                    .offset(y: -2)
                    .frame(width: 36, height: 36)
                    .background(Color.white)
                    .cornerRadius(AppCornerRadius.medium)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.md)
    }

    /// Search-filtered copy of the history groups (matches title or preview, case-insensitive).
    /// Empty query returns the full list unchanged; empty groups are dropped.
    private var filteredHistoryGroups: [ChatHistoryGroup] {
        let query = historySearchText.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        guard !query.isEmpty else { return viewModel.historyGroups }
        return viewModel.historyGroups.compactMap { group in
            let items = group.items.filter {
                $0.title.lowercased().contains(query) || $0.preview.lowercased().contains(query)
            }
            return items.isEmpty ? nil : ChatHistoryGroup(section: group.section, items: items)
        }
    }

    // MARK: - Action Handlers

    private func handleHistoryTap() {
        withAnimation(.easeInOut(duration: 0.15)) {
            showingHistory.toggle()
        }
        if showingHistory {
            // Fresh, unfiltered open every time — a stale search filter must not hide conversations.
            historySearchText = ""
            viewModel.historyActionError = nil
            viewModel.loadHistory()
        }
    }

    /// Start a fresh conversation: clear the current thread + inputs and close the history panel,
    /// returning to the empty state (suggestion chips). The backend session for the prior thread is
    /// preserved server-side and still reachable from the history list.
    private func handleNewChat() {
        viewModel.resetConversation()
        historySearchText = ""
        inputText = ""
        withAnimation(.easeInOut(duration: 0.15)) {
            showingHistory = false
        }
    }

    private func handleSuggestionTap(_ chip: SuggestionChip) {
        inputText = chip.text
        handleSend()
    }

    /// A follow-up chip under the latest answer → send it as the next message.
    /// `sendMessage`'s `guard !isAITyping` keeps a double-tap from firing two requests.
    private func handleFollowUpTap(_ question: String) {
        viewModel.sendMessage(question)
    }

    private func handleSend() {
        guard !inputText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        let message = inputText
        inputText = ""

        viewModel.sendMessage(message)
    }
}

// MARK: - Liquid Glass menu row / divider (mirrors the portfolio header popup style)

private struct ChatMenuRow: View {
    let title: String
    var systemImage: String? = nil
    var isDestructive: Bool = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: AppSpacing.sm) {
                // Reserved leading slot keeps every label aligned.
                ZStack {
                    if let systemImage {
                        Image(systemName: systemImage)
                            .font(.system(size: 13, weight: .medium))
                            .foregroundColor(isDestructive ? AppColors.bearish : AppColors.textSecondary)
                    }
                }
                .frame(width: 18)

                Text(title)
                    .font(.system(size: 14))
                    .foregroundColor(isDestructive ? AppColors.bearish : AppColors.textPrimary)

                Spacer(minLength: 0)
            }
            .padding(.horizontal, AppSpacing.md)
            .padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

private struct ChatMenuDivider: View {
    var body: some View {
        Rectangle()
            .fill(Color.white.opacity(0.08))
            .frame(height: 0.5)
            .padding(.vertical, AppSpacing.xxs)
    }
}

// MARK: - Presentation modifier

/// Identifiable token so the chat presents via `.fullScreenCover(item:)` rather than `(isPresented:)`.
/// This matters because the host screens (asset details, the report, the book/article readers) are
/// THEMSELVES presented as `.fullScreenCover`s, and SwiftUI's `.fullScreenCover(isPresented:)` silently
/// fails to present when nested inside another cover — while the `item:` variant presents reliably
/// (the book's "Read"/chapter covers are item-based and work). Hosts keep their simple `Bool` binding;
/// `AIChatCoverModifier` bridges it to a stable token internally.
private struct ChatCoverToken: Identifiable, Equatable {
    let id = UUID()
}

private struct AIChatCoverModifier: ViewModifier {
    @Binding var isPresented: Bool
    let viewModel: ChatViewModel
    @State private var token: ChatCoverToken?

    func body(content: Content) -> some View {
        content
            .fullScreenCover(item: $token) { _ in
                AIChatScreen(viewModel: viewModel)
                    .preferredColorScheme(.dark)
            }
            .onChange(of: isPresented) { _, present in
                // Create a stable token on present (don't replace an existing one — that would
                // re-present/flicker); clear it on dismiss.
                if present {
                    if token == nil { token = ChatCoverToken() }
                } else {
                    token = nil
                }
            }
            .onChange(of: token) { _, newToken in
                // Cover dismissed itself (swipe / ✕ → item set to nil) → sync the host's Bool back.
                if newToken == nil && isPresented { isPresented = false }
            }
    }
}

extension View {
    /// Present the unified full-screen AI chat. The caller owns the `ChatViewModel` (as a
    /// `@StateObject`) so the conversation resumes when reopened. Audio is re-injected across
    /// the cover boundary by `AIChatScreen`'s `.globalAudioOverlay`.
    func aiChatCover(isPresented: Binding<Bool>, viewModel: ChatViewModel) -> some View {
        modifier(AIChatCoverModifier(isPresented: isPresented, viewModel: viewModel))
    }
}

#Preview {
    AIChatScreen(viewModel: ChatViewModel())
        .preferredColorScheme(.dark)
}
