import SwiftUI
import Combine

@MainActor
class ChatListViewModel: ObservableObject {
    @Published var sessions: [ChatSession] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let apiService = APIService()

    func loadSessions() async {
        isLoading = true
        errorMessage = nil

        do {
            sessions = try await apiService.getChatSessions(limit: 50)
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func deleteSession(_ session: ChatSession) async {
        do {
            try await apiService.deleteChatSession(sessionId: session.id)
            sessions.removeAll { $0.id == session.id }
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

@MainActor
class ChatConversationViewModel: ObservableObject {
    @Published var session: ChatSession?
    @Published var messages: [ChatMessage] = []
    @Published var messageInput = ""
    @Published var isLoading = false
    @Published var isSending = false
    @Published var errorMessage: String?

    private let apiService = APIService()
    let sessionId: String

    init(sessionId: String) {
        self.sessionId = sessionId
    }

    func loadConversation() async {
        isLoading = true
        errorMessage = nil

        do {
            let data = try await apiService.getChatSessionDetail(sessionId: sessionId)
            session = data.session
            messages = data.messages
        } catch {
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    func sendMessage() async {
        guard !messageInput.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        let content = messageInput
        messageInput = "" // Clear immediately

        isSending = true
        errorMessage = nil

        do {
            let newMessage = try await apiService.sendMessage(sessionId: sessionId, content: content)
            messages.append(newMessage)
        } catch {
            errorMessage = error.localizedDescription
            messageInput = content // Restore on error
        }

        isSending = false
    }
}

@MainActor
class ChatCreationViewModel: ObservableObject {
    @Published var selectedType: SessionType?
    @Published var selectedStockId: String?
    @Published var selectedEducationContentId: String?
    @Published var createdSession: ChatSession?
    @Published var isCreating = false
    @Published var errorMessage: String?

    private let apiService = APIService()

    func createSession(type: SessionType, stockId: String? = nil, contentId: String? = nil) async {
        isCreating = true
        errorMessage = nil

        do {
            let request = ChatSessionCreate(
                sessionType: type,
                title: nil,
                stockId: stockId,
                educationContentId: contentId
            )
            createdSession = try await apiService.createChatSession(request)
        } catch {
            errorMessage = error.localizedDescription
        }

        isCreating = false
    }
}
