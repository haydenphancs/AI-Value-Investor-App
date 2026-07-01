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

    /// Transient error for history-row actions (pin / rename / delete). Kept separate from
    /// `errorMessage` so a failed row action never disturbs the main chat surface.
    @Published var historyActionError: String?

    /// Whether we're in an active conversation (vs. empty state)
    var isInConversation: Bool {
        currentSessionId != nil && !messages.isEmpty
    }

    // MARK: - Private

    private var currentSessionType: String = "NORMAL"
    private var currentStockId: String?
    private var pendingContext: String?

    // MARK: - Session Management

    /// Create a new chat session and optionally send the first message.
    func startNewConversation(
        firstMessage: String,
        stockId: String? = nil,
        context: String? = nil
    ) {
        // One seed in flight at a time: block a second synchronous seed (rapid double-tap on the
        // Deep Research / AI Analyst / report-chat buttons, which have no text-field empty-guard)
        // from creating a duplicate backend session and overwriting `messages`. isAITyping is reset
        // by resetConversation()/loadConversation(), so a genuine new chat from a settled state passes.
        guard !isAITyping else { return }

        errorMessage = nil
        currentStockId = stockId
        currentSessionType = stockId != nil ? "STOCK" : "NORMAL"
        pendingContext = context

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
                // If the user navigated to another conversation (loadConversation) while createSession
                // was in flight, abandon this seed so it doesn't clobber the now-active session.
                guard currentSessionId == nil else {
                    print("⚠️ [ChatVM] Abandoning stale seed; active session is \(currentSessionId ?? "nil")")
                    return
                }
                currentSessionId = session.id
                print("✅ [ChatVM] Session created: \(session.id)")

                // Step 2: Send first message with context as separate field
                await sendMessageToSession(sessionId: session.id, message: firstMessage, context: pendingContext)
                pendingContext = nil

            } catch {
                print("❌ [ChatVM] Failed to start conversation: \(error)")
                // Only surface the error if this seed is still the active context (the user did not
                // navigate to another conversation during the createSession round-trip).
                guard currentSessionId == nil else { return }
                isAITyping = false
                errorMessage = "Failed to start conversation. Please try again."
            }
        }
    }

    /// Send a message in the current conversation.
    func sendMessage(_ text: String) {
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        // One message in flight at a time. `isAITyping` is true both while the AI is replying AND
        // during the createSession round-trip of a freshly seeded conversation (when currentSessionId
        // is still nil). Guarding here prevents (a) a second send firing a parallel AI request and
        // (b) re-entering startNewConversation mid-seed, which would double-create a session and wipe
        // the seeded first message. The UI also greys the send button while busy; this is the
        // load-bearing guard.
        guard !isAITyping else { return }

        guard let sessionId = currentSessionId else {
            // No session yet — start a new conversation
            startNewConversation(firstMessage: text)
            return
        }
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
        // Switching conversations cancels any prior "thinking" indicator; a still-in-flight send
        // for the previous session is dropped by the sessionId guard in sendMessageToSession.
        isAITyping = false
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
                historyActionError = nil

                // If we deleted the current session, clear the conversation
                if currentSessionId == sessionId {
                    resetConversation()
                }

                print("✅ [ChatVM] Deleted session \(sessionId)")

            } catch {
                print("❌ [ChatVM] Failed to delete session: \(error)")
                // A 404 means it's already gone server-side — that IS the desired end state, so
                // reconcile locally (no error banner) rather than nagging the user to retry.
                if case APIError.notFound = error {
                    removeSessionLocally(sessionId)
                    historyActionError = nil
                } else {
                    historyActionError = "Couldn't delete chat — please try again."
                }
            }
        }
    }

    /// Pin / unpin a session (persists `is_saved`). Replaces the local row with the server's
    /// authoritative updated session so the history list reflects the change immediately.
    func setPinned(_ sessionId: String, pinned: Bool) {
        Task {
            do {
                print("📡 [ChatVM] \(pinned ? "Pinning" : "Unpinning") session \(sessionId)...")
                let updated = try await APIClient.shared.request(
                    endpoint: .updateChatSession(sessionId: sessionId, title: nil, isSaved: pinned),
                    responseType: ChatSessionDTO.self
                )
                if let idx = historySessions.firstIndex(where: { $0.id == sessionId }) {
                    historySessions[idx] = updated
                    historyGroups = groupSessionsByDate(historySessions)
                }
                historyActionError = nil
                print("✅ [ChatVM] \(pinned ? "Pinned" : "Unpinned") session \(sessionId)")
            } catch {
                print("❌ [ChatVM] Failed to set pin on \(sessionId): \(error)")
                reconcileHistoryActionError(error, sessionId: sessionId, message: "Couldn't update chat — please try again.")
            }
        }
    }

    /// Rename a session's title (persists `title`). No-op on empty input. Updates the local row
    /// from the server's authoritative response.
    func renameSession(_ sessionId: String, title: String) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        Task {
            do {
                print("📡 [ChatVM] Renaming session \(sessionId)...")
                let updated = try await APIClient.shared.request(
                    endpoint: .updateChatSession(sessionId: sessionId, title: trimmed, isSaved: nil),
                    responseType: ChatSessionDTO.self
                )
                if let idx = historySessions.firstIndex(where: { $0.id == sessionId }) {
                    historySessions[idx] = updated
                    historyGroups = groupSessionsByDate(historySessions)
                }
                historyActionError = nil
                print("✅ [ChatVM] Renamed session \(sessionId)")
            } catch {
                print("❌ [ChatVM] Failed to rename \(sessionId): \(error)")
                reconcileHistoryActionError(error, sessionId: sessionId, message: "Couldn't rename chat — please try again.")
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

    /// Drop a session from the local history list + regroup, resetting the conversation if it was active.
    private func removeSessionLocally(_ sessionId: String) {
        historySessions.removeAll { $0.id == sessionId }
        historyGroups = groupSessionsByDate(historySessions)
        if currentSessionId == sessionId {
            resetConversation()
        }
    }

    /// On a pin/rename failure: if the session is gone server-side (404), drop the ghost row and stay
    /// silent (its state can't be updated); otherwise surface a transient, retryable banner.
    private func reconcileHistoryActionError(_ error: Error, sessionId: String, message: String) {
        if case APIError.notFound = error {
            removeSessionLocally(sessionId)
            historyActionError = nil
        } else {
            historyActionError = message
        }
    }

    // MARK: - Private Helpers

    /// Send a message to an existing session and handle the AI response.
    private func sendMessageToSession(sessionId: String, message: String, context: String? = nil) async {
        let startTime = CFAbsoluteTimeGetCurrent()
        print("📡 [ChatVM] Sending message to session \(sessionId): \"\(message.prefix(50))...\"")

        do {
            let response = try await APIClient.shared.request(
                endpoint: .sendChatMessage(sessionId: sessionId, message: message, context: context),
                responseType: ChatMessageDTO.self
            )

            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)
            // If the user navigated to another conversation (or reset) while this was in flight,
            // drop the result so it doesn't append into / clear the typing state of the wrong session.
            guard sessionId == currentSessionId else {
                print("⚠️ [ChatVM] Discarding stale response for \(sessionId); active is \(currentSessionId ?? "nil")")
                return
            }
            let richMessage = response.toRichChatMessage()
            messages.append(richMessage)
            isAITyping = false

            let hasWidget = response.widget != nil
            print("✅ [ChatVM] AI response received in \(elapsed)s (widget: \(hasWidget), tokens: \(response.tokensUsed ?? 0))")

        } catch {
            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)
            print("❌ [ChatVM] Send failed after \(elapsed)s: \(error)")
            // Only surface the error on the conversation that is still active.
            guard sessionId == currentSessionId else { return }
            isAITyping = false
            errorMessage = "Failed to get AI response. Please try again."
        }
    }

    /// Group sessions into TODAY / YESTERDAY / OLDER for the history panel.
    private func groupSessionsByDate(_ sessions: [ChatSessionDTO]) -> [ChatHistoryGroup] {
        let calendar = Calendar.current
        var pinnedItems: [ChatHistoryItem] = []
        var todayItems: [ChatHistoryItem] = []
        var yesterdayItems: [ChatHistoryItem] = []
        var olderItems: [ChatHistoryItem] = []

        for session in sessions {
            let item = session.toChatHistoryItem()
            if item.isSaved {
                // Pinned chats float out of their date bucket into a top "PINNED" section.
                pinnedItems.append(item)
            } else if calendar.isDateInToday(item.timestamp) {
                todayItems.append(item)
            } else if calendar.isDateInYesterday(item.timestamp) {
                yesterdayItems.append(item)
            } else {
                olderItems.append(item)
            }
        }

        var groups: [ChatHistoryGroup] = []
        if !pinnedItems.isEmpty {
            groups.append(ChatHistoryGroup(section: .pinned, items: pinnedItems))
        }
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
