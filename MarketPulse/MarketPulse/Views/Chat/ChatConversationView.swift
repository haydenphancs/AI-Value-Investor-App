import SwiftUI

struct ChatConversationView: View {
    @StateObject private var viewModel: ChatConversationViewModel
    @FocusState private var isInputFocused: Bool
    @State private var scrollProxy: ScrollViewProxy?

    init(sessionId: String) {
        _viewModel = StateObject(wrappedValue: ChatConversationViewModel(sessionId: sessionId))
    }

    var body: some View {
        VStack(spacing: 0) {
            // Messages
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(spacing: AppConstants.paddingMedium) {
                        if viewModel.isLoading {
                            LoadingView(message: "Loading conversation...")
                                .frame(maxWidth: .infinity, minHeight: 400)
                        } else if viewModel.messages.isEmpty {
                            VStack(spacing: AppConstants.paddingMedium) {
                                Text(viewModel.session?.sessionEmoji ?? "💬")
                                    .font(.system(size: 60))

                                Text("Start the conversation")
                                    .font(.headline)

                                Text("Ask anything about \(viewModel.session?.displayTitle ?? "investing")")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                                    .multilineTextAlignment(.center)
                            }
                            .frame(maxWidth: .infinity, minHeight: 400)
                        } else {
                            ForEach(viewModel.messages) { message in
                                ChatMessageBubble(message: message)
                                    .id(message.id)
                            }

                            if viewModel.isSending {
                                TypingIndicator()
                            }
                        }
                    }
                    .padding()
                }
                .onAppear {
                    scrollProxy = proxy
                }
                .onChange(of: viewModel.messages.count) { _ in
                    scrollToBottom(proxy: proxy)
                }
            }

            Divider()

            // Input Bar
            HStack(spacing: AppConstants.paddingMedium) {
                TextField("Type a message...", text: $viewModel.messageInput, axis: .vertical)
                    .textFieldStyle(.plain)
                    .padding(AppConstants.paddingSmall)
                    .background(Color(.systemGray6))
                    .cornerRadius(AppConstants.cornerRadiusMedium)
                    .focused($isInputFocused)
                    .lineLimit(1...5)

                Button(action: {
                    Task {
                        await viewModel.sendMessage()
                        isInputFocused = true
                    }
                }) {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 30))
                        .foregroundColor(viewModel.messageInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? .gray : .accentColor)
                }
                .disabled(viewModel.messageInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || viewModel.isSending)
            }
            .padding()
            .background(Color(.systemBackground))
        }
        .navigationTitle(viewModel.session?.displayTitle ?? "Chat")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await viewModel.loadConversation()
        }
    }

    private func scrollToBottom(proxy: ScrollViewProxy) {
        guard let lastMessage = viewModel.messages.last else { return }
        withAnimation {
            proxy.scrollTo(lastMessage.id, anchor: .bottom)
        }
    }
}

struct ChatMessageBubble: View {
    let message: ChatMessage

    var body: some View {
        HStack(alignment: .top, spacing: AppConstants.paddingMedium) {
            if message.isUser {
                Spacer()
            }

            VStack(alignment: message.isUser ? .trailing : .leading, spacing: 4) {
                Text(message.content)
                    .font(.body)
                    .foregroundColor(message.isUser ? .white : .primary)
                    .padding(AppConstants.paddingMedium)
                    .background(message.isUser ? Color.accentColor : Color(.secondarySystemBackground))
                    .cornerRadius(AppConstants.cornerRadiusMedium)
                    .frame(maxWidth: UIScreen.main.bounds.width * 0.75, alignment: message.isUser ? .trailing : .leading)

                // Citations (for assistant messages)
                if !message.isUser, let citations = message.citations, !citations.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Sources:")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                            .fontWeight(.medium)

                        ForEach(citations) { citation in
                            CitationView(citation: citation)
                        }
                    }
                    .padding(.leading, AppConstants.paddingMedium)
                }

                Text(message.createdAt.timeAgo())
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .padding(.horizontal, AppConstants.paddingSmall)
            }

            if !message.isUser {
                Spacer()
            }
        }
    }
}

struct CitationView: View {
    let citation: Citation

    var body: some View {
        HStack(alignment: .top, spacing: 4) {
            Image(systemName: "doc.text")
                .font(.caption2)
                .foregroundColor(.secondary)

            VStack(alignment: .leading, spacing: 2) {
                Text(citation.source)
                    .font(.caption2)
                    .fontWeight(.medium)

                if let excerpt = citation.excerpt {
                    Text(excerpt)
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .lineLimit(2)
                }
            }
        }
        .padding(6)
        .background(Color(.systemGray6))
        .cornerRadius(6)
    }
}

struct TypingIndicator: View {
    @State private var numberOfDots = 1

    var body: some View {
        HStack {
            HStack(spacing: 4) {
                ForEach(0..<3) { index in
                    Circle()
                        .fill(Color.secondary)
                        .frame(width: 6, height: 6)
                        .opacity(index < numberOfDots ? 1 : 0.3)
                }
            }
            .padding()
            .background(Color(.secondarySystemBackground))
            .cornerRadius(AppConstants.cornerRadiusMedium)

            Spacer()
        }
        .onAppear {
            startAnimation()
        }
    }

    private func startAnimation() {
        Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in
            numberOfDots = (numberOfDots % 3) + 1
        }
    }
}

struct ChatConversationView_Previews: PreviewProvider {
    static var previews: some View {
        ChatConversationView(sessionId: "sample-id")
    }
}
