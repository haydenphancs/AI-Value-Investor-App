import SwiftUI

struct ChatSessionsView: View {
  @State private var sessions: [ChatSession] = []
  @State private var isLoading = true

  var body: some View {
    NavigationStack {
      List {
        ForEach(sessions) { s in
          NavigationLink(destination: ChatConversationView(sessionID: s.id)) { Text(s.title) }
        }
      }
      .navigationTitle("Chats")
      .toolbar { Button("New Chat") {} }
      .task { await load() }
    }
  }
  private func load() async { try? await Task.sleep(nanoseconds: 300_000_000); isLoading = false }
}

struct ChatConversationView: View {
  let sessionID: String
  @State private var messages: [ChatMessage] = []
  @State private var text = ""

  var body: some View {
    VStack {
      ScrollView {
        LazyVStack(alignment: .leading, spacing: 8) {
          ForEach(messages) { m in
            HStack { if m.role == "assistant" { Spacer() }; Text(m.content).padding(10).background(.thinMaterial).clipShape(RoundedRectangle(cornerRadius: 10)); if m.role == "user" { Spacer() } }
          }
        }
        .padding()
      }
      HStack {
        TextField("Message", text: $text)
        Button("Send") { send() }
      }
      .padding()
    }
    .navigationTitle("Conversation")
    .task { await load() }
  }

  private func load() async { try? await Task.sleep(nanoseconds: 200_000_000) }
  private func send() { text = "" }
}
