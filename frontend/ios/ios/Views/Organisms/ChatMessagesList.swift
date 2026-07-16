//
//  ChatMessagesList.swift
//  ios
//
//  Organism: Scrollable list of chat messages
//

import SwiftUI

struct ChatMessagesList: View {
    let messages: [RichChatMessage]
    /// The id of the message currently streaming (shows a blinking caret). nil = none.
    var streamingMessageId: UUID? = nil
    /// Tapping a follow-up suggestion under the LATEST answer sends it as a new message.
    var onFollowUpTap: ((String) -> Void)? = nil

    /// Changes on a new message AND as the streaming message grows, so the view
    /// stays pinned to the newest content while tokens arrive (the in-place update
    /// of the streaming bubble doesn't change `messages.count`).
    private var scrollKey: Int {
        let last = messages.last
        let lastTextCount = last?.content.reduce(0) { acc, item in
            if case .text(let t) = item { return acc + t.count }
            return acc
        } ?? 0
        // Also track the reasoning card's growth: during the pre-token phase the answer text is empty
        // and only `thinking.reasoning` grows, so keying on text length alone left the view un-pinned
        // (no auto-scroll) until the first answer token arrived.
        let lastReasonCount = last?.thinking?.reasoning?.count ?? 0
        return messages.count &* 1_000_003 &+ lastTextCount &+ lastReasonCount
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: AppSpacing.xl) {
                    ForEach(messages) { message in
                        ChatMessageRow(
                            message: message,
                            isStreaming: message.id == streamingMessageId,
                            isLast: message.id == messages.last?.id,
                            onFollowUpTap: onFollowUpTap
                        )
                        .id(message.id)
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)
            }
            .onChange(of: scrollKey) { _, _ in
                // Follow new messages AND streaming growth to the bottom.
                if let lastMessage = messages.last {
                    withAnimation {
                        proxy.scrollTo(lastMessage.id, anchor: .bottom)
                    }
                }
            }
        }
    }
}

// MARK: - Chat Message Row
struct ChatMessageRow: View {
    let message: RichChatMessage
    var isStreaming: Bool = false
    /// The last message in the list — only it shows follow-up suggestion chips.
    var isLast: Bool = false
    var onFollowUpTap: ((String) -> Void)? = nil

    var body: some View {
        switch message.role {
        case .user:
            userMessage
        case .assistant:
            assistantMessage
        }
    }

    private var userMessage: some View {
        Group {
            if case .text(let text) = message.content.first {
                UserMessageBubble(text: text, timestamp: message.formattedTime)
            }
        }
    }

    private var assistantMessage: some View {
        AIMessageContent(
            content: message.content,
            timestamp: message.formattedTime,
            isStreaming: isStreaming,
            thinking: message.thinking,
            sources: message.sources,
            suggestions: message.suggestions,
            showFollowUps: isLast,
            onFollowUpTap: onFollowUpTap
        )
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ChatMessagesList(messages: RichChatMessage.sampleConversation)
    }
    .preferredColorScheme(.dark)
}
