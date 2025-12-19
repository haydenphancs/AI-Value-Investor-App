import SwiftUI

struct ChatSessionListView: View {
    @StateObject private var viewModel = ChatListViewModel()
    @State private var showingNewChat = false

    var body: some View {
        NavigationView {
            ZStack {
                if viewModel.isLoading {
                    LoadingView(message: "Loading chats...")
                } else if viewModel.sessions.isEmpty {
                    EmptyStateView(
                        icon: "bubble.left.and.bubble.right",
                        title: "No Chat Sessions",
                        message: "Start a conversation to get AI-powered insights on stocks or investment education.",
                        actionTitle: "New Chat",
                        action: { showingNewChat = true }
                    )
                } else {
                    List {
                        ForEach(viewModel.sessions) { session in
                            NavigationLink(destination: ChatConversationView(sessionId: session.id)) {
                                ChatSessionRow(session: session)
                            }
                        }
                        .onDelete { indexSet in
                            for index in indexSet {
                                let session = viewModel.sessions[index]
                                Task {
                                    await viewModel.deleteSession(session)
                                }
                            }
                        }
                    }
                    .refreshable {
                        await viewModel.loadSessions()
                    }
                }
            }
            .navigationTitle("Chats")
            .toolbar {
                ToolbarItem(placement: .navigationBarTrailing) {
                    Button(action: { showingNewChat = true }) {
                        Image(systemName: "plus")
                    }
                }
            }
            .sheet(isPresented: $showingNewChat) {
                ChatTypeSelectionView()
            }
            .task {
                await viewModel.loadSessions()
            }
        }
    }
}

struct ChatSessionRow: View {
    let session: ChatSession

    var body: some View {
        HStack(spacing: AppConstants.paddingMedium) {
            // Icon
            Text(session.sessionEmoji)
                .font(.title2)
                .frame(width: AppConstants.logoMedium, height: AppConstants.logoMedium)
                .background(Color(.secondarySystemBackground))
                .cornerRadius(AppConstants.cornerRadiusSmall)

            // Content
            VStack(alignment: .leading, spacing: 4) {
                Text(session.displayTitle)
                    .font(.headline)
                    .lineLimit(1)

                if let preview = session.previewMessage {
                    Text(preview)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(2)
                }

                HStack {
                    Text(session.sessionType.displayName)
                        .font(.caption2)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.accentColor.opacity(0.2))
                        .foregroundColor(.accentColor)
                        .cornerRadius(4)

                    if let lastMessage = session.lastMessageAt {
                        Text(lastMessage.timeAgo())
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }

                    Spacer()

                    Text("\(session.messageCount)")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }
        }
        .padding(.vertical, 4)
    }
}

struct ChatSessionListView_Previews: PreviewProvider {
    static var previews: some View {
        ChatSessionListView()
    }
}
