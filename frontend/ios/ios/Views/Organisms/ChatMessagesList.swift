//
//  ChatMessagesList.swift
//  ios
//
//  Organism: Scrollable list of chat messages
//

import SwiftUI

struct ChatMessagesList: View {
    let messages: [RichChatMessage]

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: AppSpacing.xl) {
                    ForEach(messages) { message in
                        ChatMessageRow(message: message)
                            .id(message.id)
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
                .padding(.vertical, AppSpacing.md)
            }
            .onChange(of: messages.count) { _ in
                // Scroll to bottom when new message is added
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
        AIMessageContent(content: message.content, timestamp: message.formattedTime)
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
