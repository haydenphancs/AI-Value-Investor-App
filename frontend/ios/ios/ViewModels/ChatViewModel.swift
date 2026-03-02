//
//  ChatViewModel.swift
//  ios
//
//  ViewModel for Chat — MVVM Architecture
//
//  Manages:
//  - Session creation (POST /chat/sessions)
//  - Sending messages & receiving AI responses (POST /chat/sessions/{id}/messages)
//  - Loading session history (GET /chat/sessions/{id})
//  - Listing all sessions for the history panel (GET /chat/sessions)
//  - Session deletion (DELETE /chat/sessions/{id})
//  - Mapping backend DTOs → UI-facing RichChatMessage
//  - Loading, typing-indicator, and error states
//

import Foundation
import Combine

@MainActor
class ChatViewModel: ObservableObject {

    // MARK: - Published State

    /// Messages in the current conversation (UI-facing)
    @Published var messages: [RichChatMessage] = []

    /// All user sessions for the history panel
    @Published var historySessions: [ChatSessionDTO] = []

    /// Grouped history for the history panel UI
    @Published var historyGroups: [ChatHistoryGroup] = []

    /// Current active session ID
    @Published var currentSessionId: String?

    /// Whether a message is being sent and AI is generating
    @Published var isAITyping: Bool = false

    /// Whether sessions are loading
    @Published var isLoadingHistory: Bool = false

    /// Whether the initial session is loading
    @Published var isLoadingSession: Bool = false

    /// Error message (nil if no error)
    @Published var errorMessage: String?

    /// Whether we're in an active conversation (vs. empty state)
    var isInConversation: Bool {
        currentSessionId != nil && !messages.isEmpty
    }

    // MARK: - Private

    private var currentSessionType: String = "NORMAL"
    private var currentStockId: String?

    // MARK: - Session Management

    /// Create a new chat session and optionally send the first message.
    func startNewConversation(
        firstMessage: String,
        stockId: String? = nil
    ) {
        errorMessage = nil
        currentStockId = stockId
        currentSessionType = stockId != nil ? "STOCK" : "NORMAL"

        // Add user message immediately for instant feedback
        let userMessage = RichChatMessage(
            role: .user,
            content: [.text(firstMessage)],
            timestamp: Date()
        )
        messages = [userMessage]
        isAITyping = true

        Task {
            do {
                // Step 1: Create session
                print("📡 [ChatVM] Creating chat session (stockId: \(stockId ?? "nil"))...")
                let session = try await APIClient.shared.request(
                    endpoint: .createChatSession(stockId: stockId),
                    responseType: ChatSessionDTO.self
                )
                currentSessionId = session.id
                print("✅ [ChatVM] Session created: \(session.id)")

                // Step 2: Send first message
                await sendMessageToSession(sessionId: session.id, message: firstMessage)

            } catch {
                print("❌ [ChatVM] Failed to start conversation: \(error)")
                isAITyping = false
                errorMessage = "Failed to start conversation. Please try again."
            }
        }
    }

    /// Send a message in the current conversation.
    func sendMessage(_ text: String) {
        guard let sessionId = currentSessionId else {
            // No session yet — start a new conversation
            startNewConversation(firstMessage: text)
            return
        }

        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }
        errorMessage = nil

        // Add user message immediately
        let userMessage = RichChatMessage(
            role: .user,
            content: [.text(text)],
            timestamp: Date()
        )
        messages.append(userMessage)
        isAITyping = true

        Task {
            await sendMessageToSession(sessionId: sessionId, message: text)
        }
    }

    /// Load a previous conversation from the history panel.
    func loadConversation(sessionId: String) {
        currentSessionId = sessionId
        messages = []
        isLoadingSession = true
        errorMessage = nil

        Task {
            do {
                print("📡 [ChatVM] Loading conversation \(sessionId)...")
                let history = try await APIClient.shared.request(
                    endpoint: .getChatHistory(sessionId: sessionId),
                    responseType: ChatHistoryDTO.self
                )

                messages = history.messages.map { $0.toRichChatMessage() }
                currentSessionType = history.session.sessionType ?? "NORMAL"
                currentStockId = history.session.stockId
                isLoadingSession = false

                print("✅ [ChatVM] Loaded \(messages.count) messages for session \(sessionId)")

            } catch {
                print("❌ [ChatVM] Failed to load conversation: \(error)")
                isLoadingSession = false
                errorMessage = "Failed to load conversation."
            }
        }
    }

    /// Load all user sessions for the history panel.
    func loadHistory() {
        isLoadingHistory = true

        Task {
            do {
                print("📡 [ChatVM] Loading chat history...")
                let response = try await APIClient.shared.request(
                    endpoint: .listChatSessions(limit: 50, offset: 0),
                    responseType: ChatSessionListDTO.self
                )

                historySessions = response.sessions
                historyGroups = groupSessionsByDate(response.sessions)
                isLoadingHistory = false

                print("✅ [ChatVM] Loaded \(response.sessions.count) sessions")

            } catch {
                print("❌ [ChatVM] Failed to load history: \(error)")
                isLoadingHistory = false
                // Use empty state — don't set errorMessage so the main chat isn't affected
            }
        }
    }

    /// Delete a chat session.
    func deleteSession(_ sessionId: String) {
        Task {
            do {
                print("📡 [ChatVM] Deleting session \(sessionId)...")
                try await APIClient.shared.request(
                    endpoint: .deleteChatSession(sessionId: sessionId)
                )

                // Remove from local state
                historySessions.removeAll { $0.id == sessionId }
                historyGroups = groupSessionsByDate(historySessions)

                // If we deleted the current session, clear the conversation
                if currentSessionId == sessionId {
                    resetConversation()
                }

                print("✅ [ChatVM] Deleted session \(sessionId)")

            } catch {
                print("❌ [ChatVM] Failed to delete session: \(error)")
            }
        }
    }

    /// Reset to the initial empty state (new chat).
    func resetConversation() {
        currentSessionId = nil
        currentStockId = nil
        currentSessionType = "NORMAL"
        messages = []
        isAITyping = false
        errorMessage = nil
    }

    // MARK: - Private Helpers

    /// Send a message to an existing session and handle the AI response.
    private func sendMessageToSession(sessionId: String, message: String) async {
        let startTime = CFAbsoluteTimeGetCurrent()
        print("📡 [ChatVM] Sending message to session \(sessionId): \"\(message.prefix(50))...\"")

        do {
            let response = try await APIClient.shared.request(
                endpoint: .sendChatMessage(sessionId: sessionId, message: message),
                responseType: ChatMessageDTO.self
            )

            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)
            let richMessage = response.toRichChatMessage()
            messages.append(richMessage)
            isAITyping = false

            let hasWidget = response.widget != nil
            print("✅ [ChatVM] AI response received in \(elapsed)s (widget: \(hasWidget), tokens: \(response.tokensUsed ?? 0))")

        } catch {
            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)
            print("❌ [ChatVM] Send failed after \(elapsed)s: \(error)")
            isAITyping = false
            errorMessage = "Failed to get AI response. Please try again."
        }
    }

    /// Group sessions into TODAY / YESTERDAY / OLDER for the history panel.
    private func groupSessionsByDate(_ sessions: [ChatSessionDTO]) -> [ChatHistoryGroup] {
        let calendar = Calendar.current
        var todayItems: [ChatHistoryItem] = []
        var yesterdayItems: [ChatHistoryItem] = []
        var olderItems: [ChatHistoryItem] = []

        for session in sessions {
            let item = session.toChatHistoryItem()
            if calendar.isDateInToday(item.timestamp) {
                todayItems.append(item)
            } else if calendar.isDateInYesterday(item.timestamp) {
                yesterdayItems.append(item)
            } else {
                olderItems.append(item)
            }
        }

        var groups: [ChatHistoryGroup] = []
        if !todayItems.isEmpty {
            groups.append(ChatHistoryGroup(section: .today, items: todayItems))
        }
        if !yesterdayItems.isEmpty {
            groups.append(ChatHistoryGroup(section: .yesterday, items: yesterdayItems))
        }
        if !olderItems.isEmpty {
            groups.append(ChatHistoryGroup(section: .older, items: olderItems))
        }
        return groups
    }
}
