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

/// Failure modes of the SSE streaming path — each triggers the non-streaming fallback.
private enum ChatStreamError: Error {
    case serverError    // server emitted an `error` frame
    case incomplete     // stream ended without a terminal `done` frame
    case malformedDone  // the `done` frame payload didn't decode
}

@MainActor
class ChatViewModel: ObservableObject {

    /// Master switch for SSE streaming. The non-streaming endpoint is always the
    /// fallback, so flipping this to false disables streaming app-wide instantly.
    static var streamingEnabled = true

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

    /// True once the first streamed token arrives (hides the "thinking" dots and shows
    /// the live streaming text). Distinct from `isAITyping`, which stays true for the
    /// WHOLE response so the one-in-flight guard still blocks a second send.
    @Published var isStreaming: Bool = false

    /// Id of the assistant message currently streaming — drives the blinking caret.
    @Published var streamingMessageId: UUID?

    /// Whether we're in an active conversation (vs. empty state)
    var isInConversation: Bool {
        currentSessionId != nil && !messages.isEmpty
    }

    // MARK: - Private

    private var currentSessionType: String = "NORMAL"
    private var currentStockId: String?
    /// Client-sent context string — used only for BOOK grounding (book text is
    /// bundled in the app, not on the backend) and legacy callers. Re-sent on
    /// every message so a BOOK chat stays grounded across turns.
    private var currentContext: String?

    /// The screen this chat is grounded on + its reference id. Persisted on the
    /// session server-side (so a history reload re-grounds) and surfaced in the
    /// "Grounded on …" chip. @Published so the chip updates on start/load.
    @Published var currentContextType: ChatContextType?
    @Published private(set) var currentReferenceId: String?

    // MARK: - Session Management

    /// Create a new chat session and optionally send the first message.
    func startNewConversation(
        firstMessage: String,
        stockId: String? = nil,
        context: String? = nil,
        contextType: ChatContextType? = nil,
        referenceId: String? = nil
    ) {
        // One seed in flight at a time: block a second synchronous seed (rapid double-tap on the
        // Deep Research / AI Analyst / report-chat buttons, which have no text-field empty-guard)
        // from creating a duplicate backend session and overwriting `messages`. isAITyping is reset
        // by resetConversation()/loadConversation(), so a genuine new chat from a settled state passes.
        guard !isAITyping else { return }

        errorMessage = nil
        currentStockId = stockId
        currentSessionType = stockId != nil ? "STOCK" : "NORMAL"
        currentContext = context
        currentContextType = contextType
        currentReferenceId = referenceId

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
                // Step 1: Create session (persists context_type + reference_id so a
                // history reload re-grounds on the same cached data).
                print("📡 [ChatVM] Creating chat session (stockId: \(stockId ?? "nil"), context: \(contextType?.rawValue ?? "none"))...")
                let session = try await APIClient.shared.request(
                    endpoint: .createChatSession(
                        stockId: stockId,
                        contextType: contextType?.rawValue,
                        referenceId: referenceId
                    ),
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

                // Step 2: Send the first message (streams when enabled; context
                // type/ref are read from instance state).
                await respond(sessionId: session.id, message: firstMessage)

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
            await respond(sessionId: sessionId, message: text)
        }
    }

    /// Load a previous conversation from the history panel.
    func loadConversation(sessionId: String) {
        currentSessionId = sessionId
        messages = []
        isLoadingSession = true
        // Switching conversations cancels any prior "thinking"/streaming indicator; a
        // still-in-flight send for the previous session is dropped by the sessionId guard.
        isAITyping = false
        isStreaming = false
        streamingMessageId = nil
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
                currentContextType = history.session.chatContextType
                currentReferenceId = history.session.referenceId
                // The BOOK client-context string isn't persisted server-side; a
                // resumed book chat stays grounded via its message history instead.
                currentContext = nil
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
        currentContext = nil
        currentContextType = nil
        currentReferenceId = nil
        messages = []
        isAITyping = false
        isStreaming = false
        streamingMessageId = nil
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
    private func sendMessageToSession(sessionId: String, message: String) async {
        let startTime = CFAbsoluteTimeGetCurrent()
        print("📡 [ChatVM] Sending message to session \(sessionId): \"\(message.prefix(50))...\"")

        do {
            let response = try await APIClient.shared.request(
                endpoint: .sendChatMessage(
                    sessionId: sessionId, message: message,
                    context: currentContext,
                    contextType: currentContextType?.rawValue,
                    referenceId: currentReferenceId
                ),
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

    // MARK: - Streaming (SSE)

    /// Route a send to the streaming or non-streaming path. Streaming falls back
    /// to the non-streaming endpoint automatically on any error.
    private func respond(sessionId: String, message: String) async {
        if Self.streamingEnabled {
            await streamMessageToSession(sessionId: sessionId, message: message)
        } else {
            await sendMessageToSession(sessionId: sessionId, message: message)
        }
    }

    /// Stream the AI response token-by-token over SSE. On ANY failure (server
    /// `error` frame, transport error, incomplete stream, malformed `done`) the
    /// partial live message is removed and we fall back to the non-streaming
    /// endpoint, which regenerates + persists the turn (the stream persists nothing
    /// on failure, so there's no duplication).
    private func streamMessageToSession(sessionId: String, message: String) async {
        let liveTimestamp = Date()
        var liveId: UUID?
        var buffer = ""

        do {
            let events = APIClient.shared.stream(
                endpoint: .streamChatMessage(
                    sessionId: sessionId, message: message,
                    context: currentContext,
                    contextType: currentContextType?.rawValue,
                    referenceId: currentReferenceId
                )
            )

            for try await event in events {
                // Drop the stream if the user navigated to another conversation.
                guard sessionId == currentSessionId else { return }

                switch event.event {
                case "token":
                    guard let delta = Self.decodeDelta(event.data), !delta.isEmpty else { continue }
                    buffer += delta
                    if let id = liveId {
                        updateStreamingMessage(id: id, text: buffer, timestamp: liveTimestamp)
                    } else {
                        // First token: create the live bubble + hide the thinking dots.
                        let id = UUID()
                        liveId = id
                        streamingMessageId = id
                        isStreaming = true
                        messages.append(RichChatMessage(
                            id: id, role: .assistant, content: [.text(buffer)], timestamp: liveTimestamp
                        ))
                    }

                case "reset":
                    // Server fell back to full generation — discard partial tokens.
                    buffer = ""
                    if let id = liveId { updateStreamingMessage(id: id, text: "", timestamp: liveTimestamp) }

                case "done":
                    guard let dto = Self.decodeDoneMessage(event.data) else {
                        throw ChatStreamError.malformedDone
                    }
                    let base = dto.toRichChatMessage()
                    if let id = liveId {
                        // Keep the SAME id so the row updates in place (no ForEach
                        // tear-down/flicker) as the live text becomes the final message
                        // (with widget + citations).
                        let final = RichChatMessage(
                            id: id, role: base.role, content: base.content, timestamp: base.timestamp
                        )
                        replaceMessage(id: id, with: final)
                    } else {
                        messages.append(base)
                    }
                    finishStreaming()
                    return

                case "error":
                    throw ChatStreamError.serverError

                default:
                    continue  // "meta" and any unknown frames
                }
            }
            // Stream ended without a terminal `done` frame.
            throw ChatStreamError.incomplete

        } catch {
            print("⚠️ [ChatVM] Stream failed (\(error)); falling back to non-streaming")
            if let id = liveId { messages.removeAll { $0.id == id } }  // drop the partial bubble
            streamingMessageId = nil
            isStreaming = false
            guard sessionId == currentSessionId else {
                isAITyping = false
                return
            }
            // Show the thinking indicator again while the fallback regenerates.
            isAITyping = true
            await sendMessageToSession(sessionId: sessionId, message: message)
        }
    }

    /// Replace the streaming message in place (same id → no ForEach re-insert).
    private func updateStreamingMessage(id: UUID, text: String, timestamp: Date) {
        guard let idx = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[idx] = RichChatMessage(id: id, role: .assistant, content: [.text(text)], timestamp: timestamp)
    }

    private func replaceMessage(id: UUID, with message: RichChatMessage) {
        if let idx = messages.firstIndex(where: { $0.id == id }) {
            messages[idx] = message
        } else {
            messages.append(message)
        }
    }

    private func finishStreaming() {
        streamingMessageId = nil
        isStreaming = false
        isAITyping = false
    }

    // MARK: - SSE payload decoding

    private static func decodeDelta(_ json: String) -> String? {
        struct Token: Decodable { let delta: String }
        guard let data = json.data(using: .utf8) else { return nil }
        return (try? JSONDecoder().decode(Token.self, from: data))?.delta
    }

    private static func decodeDoneMessage(_ json: String) -> ChatMessageDTO? {
        struct Done: Decodable { let message: ChatMessageDTO }
        guard let data = json.data(using: .utf8) else { return nil }
        // ChatMessageDTO uses explicit snake_case CodingKeys — no keyDecodingStrategy.
        return (try? JSONDecoder().decode(Done.self, from: data))?.message
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
