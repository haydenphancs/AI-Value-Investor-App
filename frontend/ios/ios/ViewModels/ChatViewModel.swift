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

    /// Global state, injected by `AIChatScreen` on appear.
    ///
    /// `weak` and optional because the ViewModel is owned by whichever screen presented the
    /// chat (that ownership is what makes "resume last conversation" work), and it outlives
    /// any single presentation. Chat is the one metered surface that never told AppState a
    /// credit had moved, so its balance went stale the moment a user sent a message.
    weak var appState: AppState?

    /// Backend refusals that happen BEFORE any generation starts, and cannot be retried
    /// into a different answer.
    ///
    /// The distinction matters because every other stream failure is recoverable by the
    /// non-streaming fallback — but these three re-fail identically, and re-POSTing one is
    /// at best a wasted round trip and at worst a second charge.
    ///
    /// Matched on the backend's `ErrorCode` values (see `error_response.py`), not on the
    /// HTTP status: 402/403/409 all carry codes, and the code is the contract.
    private static let terminalPreflightCodes: Set<String> = [
        "INSUFFICIENT_CREDITS",       // 402 — no credits; the user must top up
        "CHAT_DAILY_LIMIT_REACHED",   // 409 — the guest daily-turn budget
        "SYSTEM_BUSY",                // 409 — admission gate; retrying now re-fails
        "CHAT_MESSAGE_TOO_LONG",      // 400 — the message itself is the problem
    ]

    static func isTerminalPreflightRefusal(_ error: Error) -> Bool {
        if case APIError.businessError(let code, _) = error {
            return terminalPreflightCodes.contains(code)
        }
        return false
    }

    /// Surface a failed turn BOTH in the chat's own banner and through the global error
    /// host, and always log it.
    ///
    /// Two surfaces on purpose: the banner keeps the explanation next to the conversation
    /// it belongs to, while `handleError` is what attaches the ACTION — `.insufficientCredits`
    /// carries `.upgrade`, which is what opens Buy Credits. Setting only `errorMessage`, as
    /// this used to, produced a dead-end red banner with an ✕ and no way to fix the problem.
    private func reportTurnFailure(_ error: Error) {
        let appError = AppError.from(error)
        guard !appError.isCancellation else { return }
        print("⚠️ [ChatVM] Turn failed: \(appError.analyticsCode) — \(appError.message)")
        errorMessage = appError.message
        appState?.handleError(error)
    }

    /// Pull a fresh balance after a turn that MOVED credits.
    ///
    /// Skipped when nothing moved (a free follow-up, a guest turn) so the answer path does
    /// not pay for a request that cannot change anything. `refreshCredits` single-flights
    /// internally, and this is deliberately fire-and-forget: a balance refresh must never
    /// delay the answer the user is reading.
    private func refreshCreditsIfMoved(_ cost: ChatTurnCostDTO?) {
        guard cost?.movedCredits ?? true else { return }
        Task { [weak appState] in await appState?.refreshCredits() }
    }

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
    /// The history fetch failed, as distinct from "this account has no chats".
    /// Separate from `errorMessage`, which drives the MAIN chat's banner — a failure
    /// in the history sheet must not paint an error over a healthy conversation.
    @Published var historyLoadFailed: Bool = false

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

    // MARK: - Third-party AI consent (App Review 5.1.2(i))

    /// A send that was held back pending consent, captured so it can be replayed verbatim.
    ///
    /// BOTH entry points have to be captured. `sendMessage` is NOT a single funnel:
    /// `startNewConversation(firstMessage:)` creates the session and sends directly
    /// without re-entering `sendMessage`, and it is the primary path (Deep Research,
    /// AI Analyst, report chat, suggestion chips). Gating only `sendMessage` would leave
    /// the gate bypassed on almost every real entry point.
    struct PendingChatSend {
        let message: String
        /// true → replay via startNewConversation, false → replay via sendMessage.
        let startsNewConversation: Bool
        let stockId: String?
        let context: String?
        let contextType: ChatContextType?
        let referenceId: String?
    }

    /// Set when a send is blocked awaiting consent; the view presents the consent sheet.
    @Published var needsAIConsent: Bool = false
    private var pendingConsentSend: PendingChatSend?

    /// Consent granted → replay the held send exactly as it was issued.
    func grantAIConsentAndResume() {
        AIConsentStore.shared.grant()
        needsAIConsent = false
        guard let pending = pendingConsentSend else { return }
        pendingConsentSend = nil
        if pending.startsNewConversation {
            startNewConversation(
                firstMessage: pending.message,
                stockId: pending.stockId,
                context: pending.context,
                contextType: pending.contextType,
                referenceId: pending.referenceId
            )
        } else {
            sendMessage(pending.message)
        }
    }

    /// Consent declined → discard the held send. Nothing was transmitted.
    func declineAIConsent() {
        needsAIConsent = false
        pendingConsentSend = nil
    }

    /// Records a blocked send. Returns true when the caller must stop.
    private func holdForConsent(_ pending: PendingChatSend) -> Bool {
        guard !AIConsentStore.shared.hasConsented else { return false }
        pendingConsentSend = pending
        needsAIConsent = true
        return true
    }

    // MARK: - Line-by-line reveal buffer
    //
    // Gemini streams a FEW LARGE chunks (not per-token), so appending each chunk verbatim makes
    // the answer jump in big blocks. We buffer the raw network text and meter it out one line at
    // a time on a timer for a smooth reveal. `revealShown` is what's visible; `revealPending` is
    // received-but-not-yet-shown. On `done` the reveal drains, then the authoritative content
    // replaces it (so no text is ever lost).
    private var revealPending = ""
    private var revealShown = ""
    private var revealFinished = false            // network stream ended → drain the rest, then stop
    private var revealTask: Task<Void, Never>?
    private static let revealStepNanos: UInt64 = 45_000_000  // ~45ms per revealed line

    // Second reveal channel for the streamed REASONING preamble → meters into the thinking card.
    private var reasonPending = ""
    private var reasonShown = ""
    private var reasonFinished = false
    private var reasonTask: Task<Void, Never>?

    /// The in-flight turn Task (create-session + stream/non-stream response). Stored so navigating
    /// away, resetting, or deleting the current session can CANCEL it. Without this the abandoned SSE
    /// stream keeps running and its late resume/error clobbers the now-active session's shared reveal
    /// buffers + `isAITyping` one-in-flight guard (mid-stream bubble collapse + duplicate persisted turn).
    private var respondTask: Task<Void, Never>?

    /// Monotonic seed counter — the staleness test for `startNewConversation`.
    ///
    /// Bumped by every operation that makes an in-flight seed obsolete: starting a new one,
    /// resetting, and loading a conversation from history. The seed captures it before its
    /// `createChatSession` await and compares afterwards.
    ///
    /// It exists because `currentSessionId == nil` cannot express "is this seed still the
    /// current one" once ANY conversation has been created. This view model is owned by the
    /// host screen as a `@StateObject` and survives dismissal, so from the second "Ask Cay AI"
    /// onward that test was always false and the seed aborted after already setting
    /// `isAITyping = true` and clearing `messages` — a chat stuck typing forever, with no way
    /// out but killing the app. `&+=` because wrap-around is harmless: only equality is ever
    /// tested, and a collision needs 2^64 seeds in one view-model lifetime.
    private var seedGeneration: UInt64 = 0

    // MARK: - Session Management

    /// Ground a chat on a subject and OPEN IT EMPTY — no session, no message, no credit.
    ///
    /// The Learn "Ask the Agent" buttons used to call the seeding path with a synthesised
    /// first message ("Tell me about ..."), so tapping the card spent a turn the user never
    /// typed and dropped them into a wall of text instead of an invitation to ask. A
    /// TestFlight tester reported exactly that: "should open the chat only".
    ///
    /// This is a pure state setter — deliberately no network. The session is created lazily
    /// on the user's first real send: `sendMessage` finds no session id and re-issues the
    /// grounding fields retained here, which is the case its own fallback was written for.
    /// So the backend still receives `context_type` and `reference_id` on turn one and the
    /// book voice fires normally.
    ///
    /// No consent gate here either, and that is a fix rather than an omission: nothing is
    /// transmitted, so there is nothing to consent to. The gate still fires on the first
    /// send. Previously the consent sheet appeared the instant a book card was tapped.
    func prepareGroundedConversation(
        stockId: String? = nil,
        context: String? = nil,
        contextType: ChatContextType? = nil,
        referenceId: String? = nil
    ) {
        // Re-tapping the SAME subject resumes the conversation rather than destroying it —
        // the host owns this view model as a @StateObject precisely so a chat survives
        // dismissal. Only the grounding text is refreshed (the digest can change as the
        // reader moves through the guide). A DIFFERENT subject starts clean.
        if !messages.isEmpty,
           currentContextType == contextType,
           currentReferenceId == referenceId {
            currentContext = context
            return
        }

        // Cancel any in-flight turn AND invalidate its seed: cancellation is cooperative, so
        // a seed already past its await would otherwise adopt this screen and paint the
        // previous subject's answer into the chat we are about to present as empty.
        respondTask?.cancel()
        seedGeneration &+= 1
        cancelReveal()

        currentSessionId = nil
        messages = []
        isAITyping = false
        isStreaming = false
        streamingMessageId = nil
        errorMessage = nil

        currentStockId = stockId
        currentSessionType = stockId != nil ? "STOCK" : "NORMAL"
        currentContext = context
        currentContextType = contextType
        currentReferenceId = referenceId
    }

    /// Create a new chat session and optionally send the first message.
    ///
    /// Returns whether the caller should PRESENT the chat. Every caller used to set
    /// `showChat = true` on the next line unconditionally, which is wrong for the one path
    /// that leaves the conversation untouched: the in-flight guard below. Abandoning a
    /// streaming answer (swipe down) does not clear `isAITyping` — `AIChatScreen` never
    /// calls `resetConversation()`, by design, so it can resume — so a seed attempted while
    /// that answer was still generating silently did nothing and then presented the PREVIOUS
    /// conversation, complete with its grounding chip. On the Journey screen, where the book
    /// card and the lesson action share one view model, tapping "Ask Cay AI about this
    /// lesson" would open the book chat and discard the lesson entirely.
    ///
    /// `@discardableResult` so the eight callers that cannot hit that race are unchanged.
    @discardableResult
    func startNewConversation(
        firstMessage: String,
        stockId: String? = nil,
        context: String? = nil,
        contextType: ChatContextType? = nil,
        referenceId: String? = nil
    ) -> Bool {
        // One seed in flight at a time: block a second synchronous seed (rapid double-tap on the
        // Deep Research / AI Analyst / report-chat buttons, which have no text-field empty-guard)
        // from creating a duplicate backend session and overwriting `messages`. isAITyping is reset
        // by resetConversation()/loadConversation(), so a genuine new chat from a settled state passes.
        // FALSE: nothing was seeded, so presenting would show the previous conversation.
        guard !isAITyping else { return false }

        // Third-party AI consent gate (App Review 5.1.2(i)). Placed AFTER the in-flight
        // guard but BEFORE any state mutation or network call, so a held send leaves the
        // conversation exactly as it was.
        // TRUE: the send is held, but the chat must still present — that is where the
        // consent prompt lives, and the pending send replays once it is granted.
        if holdForConsent(PendingChatSend(
            message: firstMessage, startsNewConversation: true,
            stockId: stockId, context: context,
            contextType: contextType, referenceId: referenceId
        )) { return true }

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

        // Claim this seed. Everything after the `createChatSession` await compares against
        // this snapshot, so a newer seed (or a reset / history load) invalidates it.
        seedGeneration &+= 1
        let seed = seedGeneration

        respondTask?.cancel()
        respondTask = Task {
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
                // Abandon this seed if the turn Task was cancelled (New chat / reset / navigate
                // away during createSession) OR a NEWER seed has since started.
                //
                // ⚠️ This used to read `currentSessionId == nil`, which permanently STRANDED
                // every conversation after the first. The host screen owns this view model as
                // a `@StateObject` and `AIChatScreen` never resets it, so on the second and
                // every later "Ask Cay AI" from the same screen `currentSessionId` was still
                // set from the previous conversation — the guard failed, the seed returned
                // early, and `isAITyping` (set to true just above, at line ~212) was never
                // cleared. Result: messages wiped, spinner forever, send bar dead, on the
                // app's flagship surface with ten entry points into it.
                //
                // A generation token separates the two questions the old test conflated:
                // "am I still the newest seed?" (this) versus "is there a session?" (which
                // says nothing about staleness once one already exists).
                guard !Task.isCancelled, seed == seedGeneration else {
                    print("⚠️ [ChatVM] Abandoning stale/cancelled seed \(seed); current generation is \(seedGeneration)")
                    return
                }
                currentSessionId = session.id
                print("✅ [ChatVM] Session created: \(session.id)")

                // Step 2: Send the first message (streams when enabled; context
                // type/ref are read from instance state).
                await respond(sessionId: session.id, message: firstMessage)

            } catch {
                print("❌ [ChatVM] Failed to start conversation: \(error)")
                // A cancelled seed (reset / New chat / navigate mid-createSession — cancelling the Task
                // makes the in-flight request throw) must NOT paint an error on the now-cleared chat.
                // Mirror the streaming catch's `!Task.isCancelled` guard; only surface a genuine failure
                // that happened while this seed is still the active, un-cancelled context.
                // Same generation test as the success path — a cancelled or superseded seed
                // must not paint an error onto a chat that has already moved on. (`isAITyping`
                // is cleared by whichever seed IS current.)
                guard !Task.isCancelled, seed == seedGeneration else { return }
                isAITyping = false
                errorMessage = "Failed to start conversation. Please try again."
            }
        }
        // Seeded: `messages` now holds this conversation's first turn, so presenting shows
        // what the caller asked for. (The network work above is detached — a later failure
        // surfaces inside the chat, which is the right place for it.)
        return true
    }

    /// Send a message in the current conversation.
    func sendMessage(_ text: String) {
        // Count only — the message body is user-typed text and must NEVER become
        // an analytics prop. `contextType` is a fixed enum, so it is safe.
        Analytics.shared.track(.chatSent, [
            "context": .string(currentContextType?.rawValue ?? "none"),
        ])
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return }

        // Third-party AI consent gate (App Review 5.1.2(i)). Note startNewConversation
        // carries its own gate — this is not a funnel.
        if holdForConsent(PendingChatSend(
            message: text, startsNewConversation: false,
            stockId: nil, context: nil, contextType: nil, referenceId: nil
        )) { return }

        // One message in flight at a time. `isAITyping` is true both while the AI is replying AND
        // during the createSession round-trip of a freshly seeded conversation (when currentSessionId
        // is still nil). Guarding here prevents (a) a second send firing a parallel AI request and
        // (b) re-entering startNewConversation mid-seed, which would double-create a session and wipe
        // the seeded first message. The UI also greys the send button while busy; this is the
        // load-bearing guard.
        guard !isAITyping else { return }

        guard let sessionId = currentSessionId else {
            // No session yet — start a new conversation.
            //
            // Re-pass the grounding we are still holding. `createChatSession` failing
            // leaves `currentSessionId` nil but the grounding fields intact, so a bare
            // `startNewConversation(firstMessage:)` did not merely omit them — it let
            // their `nil` DEFAULTS overwrite the retained values and flipped
            // `currentSessionType` to "NORMAL". Retrying a failed "Ask Cay AI" about a
            // ticker therefore silently produced a generic chat about nothing.
            startNewConversation(
                firstMessage: text,
                stockId: currentStockId,
                context: currentContext,
                contextType: currentContextType,
                referenceId: currentReferenceId
            )
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

        respondTask?.cancel()
        respondTask = Task {
            await respond(sessionId: sessionId, message: text)
        }
    }

    /// Load a previous conversation from the history panel.
    func loadConversation(sessionId: String) {
        // Cancel any in-flight turn for the PREVIOUS session so its abandoned SSE stream actually
        // stops (cancelling the consumer trips APIClient.stream's onTermination → the byte-task
        // cancels) and can't later resume to clobber this session's reveal/typing state. The
        // stream's for-await throws CancellationError, which its catch drops via the stale guard.
        respondTask?.cancel()
        // Invalidate any in-flight seed: the user has navigated into a different conversation,
        // so a late-returning createChatSession must not adopt this screen.
        seedGeneration &+= 1
        currentSessionId = sessionId
        // Clear the OUTGOING conversation's grounding here, not in the success branch.
        // `currentSessionId` moves to the new session immediately, but these six fields
        // were only ever rewritten on success — so a failed history load left session B
        // selected while still carrying session A's stock/context/reference. The next
        // message then went out grounded to the PREVIOUS conversation's ticker.
        // Mirrors the reset in `resetConversation()`.
        currentSessionType = "NORMAL"
        currentStockId = nil
        currentContext = nil
        currentContextType = nil
        currentReferenceId = nil
        messages = []
        isLoadingSession = true
        isAITyping = false
        isStreaming = false
        streamingMessageId = nil
        errorMessage = nil
        cancelReveal()

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
        // Clear any previous failure so a retry can show the spinner, not the error.
        historyLoadFailed = false

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
                // A FAILED load is not an empty account. Leaving both flags false
                // rendered the "No conversations yet" empty state, which is
                // indistinguishable from a genuinely new user and offers no retry —
                // the user simply believed their chat history was gone.
                //
                // Deliberately NOT `errorMessage`: that drives the main chat's error
                // banner, and a history-sheet failure must not paint an error over an
                // otherwise-healthy conversation. Hence a separate flag.
                historyLoadFailed = true
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
        // Cancel any in-flight turn so a stream from the session being cleared (e.g. deleteSession of
        // the current chat) can't resume and mutate the reset state.
        respondTask?.cancel()
        // Invalidate any in-flight seed so it cannot revive the chat we are clearing.
        // `respondTask.cancel()` alone is not enough: cancellation is cooperative, and the
        // seed can already be past its await when this runs.
        seedGeneration &+= 1
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
        cancelReveal()
    }

    /// Clear EVERYTHING this view model holds for the ended session — the thread *and* the
    /// history list.
    ///
    /// `resetConversation()` alone is not enough: it leaves `historySessions` / `historyGroups`
    /// populated, so the next account to open the history panel sees the previous user's
    /// conversation titles until `loadHistory()` returns. That is the auth.md §7 leak class
    /// (`AppState.discardDataForEndedSession`) — a device-global store outliving its session —
    /// and a fetch landing later does not excuse showing it in the meantime.
    ///
    /// Called from `ContentView` on a real identity change. It matters more since the chat view
    /// model was hoisted there: it is a `@StateObject` on a permanently opacity-mounted view, so
    /// nothing tears it down between accounts.
    func resetForIdentityChange() {
        resetConversation()
        historySessions = []
        historyGroups = []
        historyLoadFailed = false
        historyActionError = nil
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
            // The non-streaming response carries the PERSISTED cost (no `balance`), so this
            // always refreshes rather than trusting a number that isn't there.
            refreshCreditsIfMoved(response.credit)

        } catch {
            let elapsed = String(format: "%.2f", CFAbsoluteTimeGetCurrent() - startTime)
            print("❌ [ChatVM] Send failed after \(elapsed)s: \(error)")
            // Only surface the error on the conversation that is still active.
            guard sessionId == currentSessionId else { return }
            isAITyping = false
            // Route through AppError so backend business codes (CHAT_MESSAGE_TOO_LONG,
            // CHAT_DAILY_LIMIT_REACHED, rate limits, …) surface their SPECIFIC user_message
            // instead of a generic string. Without this the typed AppError branches added for
            // chat are dead — this catch is also the terminus of the stream→non-stream fallback,
            // so it's where those backend messages actually reach the user.
            //
            // `reportTurnFailure` also publishes to the GLOBAL error host, which is what
            // attaches the action: `.insufficientCredits` carries `.upgrade`, i.e. the route
            // to Buy Credits. Setting only `errorMessage`, as this did, left a user who had
            // run out of credits staring at a red banner with an ✕ and no way to fix it.
            reportTurnFailure(error)
            // A 402 means our local balance was already stale — that is exactly what the
            // server just corrected us on.
            if Self.isTerminalPreflightRefusal(error) {
                Task { [weak appState] in await appState?.refreshCredits() }
            }
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
        /// The server's NORMALIZED copy of this turn's user message, from the `meta`
        /// frame. Used instead of the raw typed text when reconciling a failed stream —
        /// see the `case "meta"` handler below.
        var serverNormalizedMessage: String?
        /// This turn's cost, from the `credits` frame. Held until `done` so the chip and
        /// the answer appear together rather than the chip flashing in a beat early.
        var turnCost: ChatTurnCostDTO?

        // Fresh reveal buffer for this turn.
        cancelReveal()

        // Create (or reuse) the assistant bubble. It's created on the FIRST thinking/sources/token
        // event so the thinking card can render at the top of the bubble immediately (empty text
        // until tokens flow). Same id throughout → the row never re-inserts.
        func ensureBubble() -> UUID {
            if let id = liveId { return id }
            let id = UUID()
            liveId = id
            messages.append(RichChatMessage(
                id: id, role: .assistant, content: [], timestamp: liveTimestamp,
                thinking: ChatThinking(stages: [], sourceCount: 0, elapsedMs: nil)
            ))
            return id
        }

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
                // Drop the stream if the user navigated to another conversation. Do NOT touch shared
                // reveal state here — it now belongs to the active session (navigation already reset
                // it); cancelReveal() would wipe the CURRENT session's live reveal.
                guard sessionId == currentSessionId else { return }

                switch event.event {
                case "thinking":
                    // Synthesized progress stage → grow the active thinking card.
                    //
                    // NOTE: the backend does not currently emit `thinking` — kept because it is
                    // the generic shape, and because `routing` / `tool_step` below feed the same
                    // sink. The two events the backend ACTUALLY sends were unhandled, so this
                    // branch never ran and `ChatThinking.stages` was empty for every message.
                    guard let stage = Self.decodeThinkingStage(event.data), !stage.isEmpty else { continue }
                    appendThinkingStage(id: ensureBubble(), stage: stage)

                case "routing":
                    // Which specialist(s) the router picked — the first real progress the user
                    // can see, and the reason the thinking card exists.
                    struct Routing: Decodable { let labels: [String]?; let mode: String? }
                    guard
                        let data = event.data.data(using: .utf8),
                        let routing = try? JSONDecoder().decode(Routing.self, from: data),
                        let labels = routing.labels, !labels.isEmpty
                    else { continue }
                    appendThinkingStage(
                        id: ensureBubble(),
                        stage: labels.count == 1
                            ? "Consulting \(labels[0])"
                            : "Consulting \(labels.joined(separator: ", "))"
                    )

                case "tool_step":
                    // A real tool call completing — genuine progress, not a synthesized label.
                    struct ToolStep: Decodable { let name: String? }
                    guard
                        let data = event.data.data(using: .utf8),
                        let step = try? JSONDecoder().decode(ToolStep.self, from: data),
                        let name = step.name, !name.isEmpty
                    else { continue }
                    appendThinkingStage(id: ensureBubble(), stage: Self.thinkingLabel(forTool: name))

                case "sources":
                    // Grounded-context source pills for the thinking card.
                    guard let srcs = Self.decodeSources(event.data), !srcs.isEmpty else { continue }
                    applyLiveSources(id: ensureBubble(), sources: srcs)

                case "reasoning":
                    // The model's REAL streamed reasoning (thought parts) → meter it into the thinking
                    // card. It now arrives as MULTIPLE incremental frames, so DON'T mark reasoning
                    // finished here — it's complete once the answer tokens begin (see "token").
                    guard let delta = Self.decodeDelta(event.data), !delta.isEmpty else { continue }
                    let id = ensureBubble()
                    reasonPending += delta
                    ensureReasonRevealRunning(id: id)

                case "token":
                    guard let delta = Self.decodeDelta(event.data), !delta.isEmpty else { continue }
                    let id = ensureBubble()
                    if streamingMessageId == nil {
                        // First token: show the caret + start metering the reveal. Thoughts precede
                        // the answer, so reasoning is complete now — let its reveal drain + stop.
                        streamingMessageId = id
                        isStreaming = true
                        reasonFinished = true
                    }
                    revealPending += delta
                    ensureRevealRunning(id: id)

                case "reset":
                    // Server fell back to full generation — discard partial tokens + both reveal buffers.
                    revealTask?.cancel(); revealTask = nil
                    revealPending = ""; revealShown = ""; revealFinished = false
                    reasonTask?.cancel(); reasonTask = nil
                    reasonPending = ""; reasonShown = ""; reasonFinished = false
                    if let id = liveId { setMessageText(id: id, text: "") }

                case "done":
                    guard let dto = Self.decodeDoneMessage(event.data) else {
                        throw ChatStreamError.malformedDone
                    }
                    let base = dto.toRichChatMessage()
                    // Let the reveal drain any remaining buffered lines smoothly (bounded), THEN
                    // finalize with the authoritative content + thinking/sources/suggestions so no
                    // text is ever lost even if the reveal lagged the network.
                    revealFinished = true
                    reasonFinished = true
                    await revealTask?.value
                    await reasonTask?.value
                    revealTask = nil
                    reasonTask = nil
                    // User navigated away DURING the drain await → the active session owns the reveal
                    // state now; return without cancelReveal (which would wipe the active session's).
                    guard sessionId == currentSessionId else { return }
                    if let id = liveId, let idx = messages.firstIndex(where: { $0.id == id }) {
                        // Same id → the row updates in place (no ForEach tear-down/flicker).
                        messages[idx] = RichChatMessage(
                            id: id, role: base.role, content: base.content, timestamp: base.timestamp,
                            thinking: base.thinking, sources: base.sources, suggestions: base.suggestions,
                            // Prefer the live frame: the `done` message carries the PERSISTED
                            // copy, which deliberately omits `balance`.
                            credit: turnCost ?? base.credit
                        )
                    } else {
                        messages.append(base)
                    }
                    finishStreaming()
                    // After the answer is on screen, never before — a balance refresh must
                    // not delay what the user is reading.
                    refreshCreditsIfMoved(turnCost)
                    return

                case "error":
                    throw ChatStreamError.serverError

                case "meta":
                    // Capture the server's own NORMALIZED copy of the message. The
                    // backend persists `normalize_text(raw)` — NFKC, invisible/bidi and
                    // control stripping — so reconciling a failed stream against the RAW
                    // typed string never matched for anything normalisation touched (a
                    // smart quote from the iOS keyboard, an emoji variation selector, a
                    // full-width character). The reconcile then concluded the turn had
                    // NOT persisted and re-sent it: a duplicated Q+A in history AND a
                    // second credit charged for one message.
                    if let data = event.data.data(using: .utf8),
                       let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                       let serverMessage = obj["user_message"] as? String,
                       !serverMessage.isEmpty {
                        serverNormalizedMessage = serverMessage
                    }
                    continue

                case "credits":
                    // What this turn cost + the balance it left behind. Arrives immediately
                    // before `done` on the delivered path only. Held here and attached to the
                    // message in the `done` arm, so the chip and the answer land together.
                    if let data = event.data.data(using: .utf8) {
                        turnCost = try? JSONDecoder().decode(ChatTurnCostDTO.self, from: data)
                    }
                    continue

                default:
                    continue  // "suggestions" and any unknown frames — final state lands on `done`
                }
            }
            // Stream ended without a terminal `done` frame.
            throw ChatStreamError.incomplete

        } catch {
            // If this stream belongs to a session the user already navigated away from — or the turn
            // Task was cancelled (navigation/reset cancels respondTask, which surfaces here as a
            // CancellationError) — do NOT touch any shared reveal/typing state: it belongs to the
            // now-active session. Just stop; the active session owns its own lifecycle.
            guard sessionId == currentSessionId, !Task.isCancelled else { return }
            cancelReveal()
            if let id = liveId { messages.removeAll { $0.id == id } }  // drop the partial bubble
            streamingMessageId = nil
            isStreaming = false

            // A PRE-FLIGHT refusal is terminal: the server rejected the turn before starting
            // generation, so nothing was persisted and nothing is recoverable. Reconciling
            // would spend a history GET and then re-POST to the non-streaming endpoint, which
            // fails identically — and on any code where it did NOT, it would charge a second
            // time. This is the path a user out of credits actually takes, and until the SSE
            // reader learned to decode 402 it ended as a bare red banner with no way out.
            if Self.isTerminalPreflightRefusal(error) {
                isAITyping = false
                reportTurnFailure(error)
                return
            }

            print("⚠️ [ChatVM] Stream failed (\(error)); falling back to non-streaming")
            // Show the thinking indicator again while we reconcile / regenerate.
            isAITyping = true
            // Prefer the server's normalized copy when the meta frame arrived; fall back
            // to the raw text when the stream failed before it (nothing was persisted
            // then either, so a raw comparison is safe).
            await reconcileAfterStreamFailure(
                sessionId: sessionId, message: serverNormalizedMessage ?? message
            )
        }
    }

    /// After a stream failure, reconcile with server truth BEFORE re-sending. The
    /// backend persists the turn immediately before the terminal `done` frame, so a
    /// transport drop in that window has already saved it — re-POSTing would store
    /// the turn twice (visible as a duplicated Q+A on the next history reload). So:
    /// reload history; if the turn is already there, adopt it; only regenerate when
    /// it is genuinely absent (the common early-failure case).
    private func reconcileAfterStreamFailure(sessionId: String, message: String) async {
        // How many user messages with THIS exact text should exist server-side IF the stream
        // persisted this turn. Local `messages` still holds the optimistic user bubble for this send
        // (the catch removed only the assistant bubble) and mirrors the server for every prior turn,
        // so this count already includes the message we're reconciling. Requiring the server to hold
        // at LEAST this many — not merely one — distinguishes a freshly-persisted turn from a
        // pre-existing identical one, so a repeated message (e.g. "why?" / "continue") isn't mistaken
        // for already-answered and silently dropped.
        let target = message.trimmingCharacters(in: .whitespacesAndNewlines)
        let expectedUserMatches = messages.filter {
            $0.role == .user && $0.plainText.trimmingCharacters(in: .whitespacesAndNewlines) == target
        }.count
        do {
            let history = try await APIClient.shared.request(
                endpoint: .getChatHistory(sessionId: sessionId),
                responseType: ChatHistoryDTO.self
            )
            guard sessionId == currentSessionId else { isAITyping = false; return }
            if Self.historyContainsTurn(history.messages, userMessage: message,
                                        expectedUserMatches: expectedUserMatches) {
                // The stream DID persist this turn — adopt server state, don't re-send.
                messages = history.messages.map { $0.toRichChatMessage() }
                isAITyping = false
                return
            }
        } catch {
            // History unavailable — fall through to a best-effort regenerate.
            print("⚠️ [ChatVM] Reconcile history fetch failed: \(error)")
            guard sessionId == currentSessionId else { isAITyping = false; return }
        }
        // Turn was not persisted — safe to regenerate via the non-streaming endpoint.
        await sendMessageToSession(sessionId: sessionId, message: message)
    }

    /// True when the streamed turn was already persisted server-side, so the caller should ADOPT
    /// server state instead of regenerating (which would duplicate the turn). Requires BOTH a
    /// trailing assistant message AND at least `expectedUserMatches` user messages equal to the sent
    /// text. The count guard is what makes a REPEATED message safe: presence alone would match an
    /// earlier identical turn and silently drop the new one. The backend persists user+assistant
    /// atomically, so a persisted turn bumps the matching-user count by exactly one.
    private static func historyContainsTurn(
        _ messages: [ChatMessageDTO], userMessage: String, expectedUserMatches: Int
    ) -> Bool {
        guard messages.last?.role == "assistant" else { return false }
        let target = userMessage.trimmingCharacters(in: .whitespacesAndNewlines)
        let matchCount = messages.filter {
            $0.role == "user" && $0.content.trimmingCharacters(in: .whitespacesAndNewlines) == target
        }.count
        return matchCount >= expectedUserMatches
    }

    // MARK: - Streaming helpers (thinking card + line-by-line reveal)

    /// Append a synthesized thinking stage to the (active) assistant bubble.
    /// Human-readable label for a backend tool name. Falls back to a de-snake-cased form so a
    /// newly added tool still reads sensibly instead of showing a raw identifier.
    static func thinkingLabel(forTool name: String) -> String {
        switch name {
        case "get_stock_quote", "get_quote":      return "Checking the latest price"
        case "get_company_profile":               return "Reading the company profile"
        case "get_financials", "get_income_statement": return "Pulling the financials"
        case "get_ticker_report":                 return "Reading the research report"
        case "search_news":                       return "Scanning recent news"
        default:
            let words = name.replacingOccurrences(of: "_", with: " ")
            return words.prefix(1).uppercased() + words.dropFirst()
        }
    }

    private func appendThinkingStage(id: UUID, stage: String) {
        guard let idx = messages.firstIndex(where: { $0.id == id }) else { return }
        var stages = messages[idx].thinking?.stages ?? []
        if stages.last != stage { stages.append(stage) }
        messages[idx].thinking = ChatThinking(
            stages: stages, sourceCount: messages[idx].sources?.count ?? 0, elapsedMs: nil
        )
    }

    /// Attach the grounded source pills to the (active) assistant bubble.
    private func applyLiveSources(id: UUID, sources: [ChatSource]) {
        guard let idx = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[idx].sources = sources
        let stages = messages[idx].thinking?.stages ?? []
        messages[idx].thinking = ChatThinking(stages: stages, sourceCount: sources.count, elapsedMs: nil)
    }

    /// Set the visible text of the streaming bubble in place (preserves thinking/sources).
    private func setMessageText(id: UUID, text: String) {
        guard let idx = messages.firstIndex(where: { $0.id == id }) else { return }
        messages[idx].content = [.text(text)]
    }

    /// Set the streaming REASONING text on the thinking card in place (preserves stages/sources).
    private func setMessageReasoning(id: UUID, text: String) {
        guard let idx = messages.firstIndex(where: { $0.id == id }) else { return }
        let t = messages[idx].thinking
        messages[idx].thinking = ChatThinking(
            stages: t?.stages ?? [], sourceCount: t?.sourceCount, elapsedMs: t?.elapsedMs, reasoning: text
        )
    }

    /// Start the line-by-line reveal loop if it isn't already running. Runs on the main actor
    /// (this VM is @MainActor) interleaving with token arrival via its per-line sleep.
    private func ensureRevealRunning(id: UUID) {
        guard revealTask == nil else { return }
        revealTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if self.revealTick(id: id) { return }   // drained + network finished → stop
                try? await Task.sleep(nanoseconds: Self.revealStepNanos)
            }
        }
    }

    /// Reveal the next chunk of the ANSWER. Returns true when the buffer is fully drained AND the
    /// network stream has finished (so the loop can stop).
    private func revealTick(id: UUID) -> Bool {
        if revealPending.isEmpty { return revealFinished }
        let chunk = Self.nextRevealChunk(&revealPending, finished: revealFinished)
        guard !chunk.isEmpty else { return false }   // waiting for the current line/sentence to complete
        revealShown += chunk
        setMessageText(id: id, text: revealShown)
        return false
    }

    /// Reveal loop for the REASONING channel → writes into the thinking card.
    private func ensureReasonRevealRunning(id: UUID) {
        guard reasonTask == nil else { return }
        reasonTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if self.reasonTick(id: id) {
                    // Self-terminated (drained + finished). Clear the handle so a LATER reasoning
                    // frame can restart the reveal — in a multi-round agentic turn, more thoughts
                    // can arrive AFTER the first answer token set reasonFinished; without this the
                    // late delta would sit unrendered in reasonPending until the `done` frame.
                    self.reasonTask = nil
                    return
                }
                try? await Task.sleep(nanoseconds: Self.revealStepNanos)
            }
        }
    }

    private func reasonTick(id: UUID) -> Bool {
        if reasonPending.isEmpty { return reasonFinished }
        let chunk = Self.nextRevealChunk(&reasonPending, finished: reasonFinished)
        guard !chunk.isEmpty else { return false }
        reasonShown += chunk
        setMessageReasoning(id: id, text: reasonShown)
        return false
    }

    /// Pull the next reveal chunk off the front of `pending`:
    ///  • up to & including the earliest boundary — a newline OR a sentence end (". "/"! "/"? "/": ")
    ///    so even a short, single-line answer reveals progressively, else
    ///  • the whole remainder once the stream has finished, else
    ///  • a word-bounded chunk when a fragment grows long (so it doesn't stall), else
    ///  • "" to wait for the current sentence/line to complete.
    private static func nextRevealChunk(_ pending: inout String, finished: Bool) -> String {
        if let end = firstBoundaryIndex(in: pending) {
            let chunk = String(pending[..<end])
            pending.removeSubrange(pending.startIndex..<end)
            return chunk
        }
        if finished {
            let chunk = pending; pending = ""; return chunk
        }
        if pending.count > 140 {
            let soft = pending.index(pending.startIndex, offsetBy: min(80, pending.count))
            if let space = pending[soft...].firstIndex(of: " ") {
                let upto = pending.index(after: space)
                let chunk = String(pending[..<upto])
                pending.removeSubrange(pending.startIndex..<upto)
                return chunk
            }
        }
        return ""
    }

    /// Index just past the earliest reveal boundary: the first newline, or the first
    /// sentence terminator (`.` `!` `?` `:`) immediately followed by a space. nil = none yet.
    private static func firstBoundaryIndex(in s: String) -> String.Index? {
        var best: String.Index? = s.firstIndex(of: "\n").map { s.index(after: $0) }
        var i = s.startIndex
        while i < s.endIndex {
            let c = s[i]
            if c == "." || c == "!" || c == "?" || c == ":" {
                let next = s.index(after: i)
                if next < s.endIndex, s[next] == " " {
                    let boundary = s.index(after: next)   // include the punctuation + the space
                    if best == nil || boundary < best! { best = boundary }
                    break   // earliest sentence boundary found
                }
            }
            i = s.index(after: i)
        }
        return best
    }

    /// Cancel BOTH reveal loops (answer + reasoning) and clear their buffers.
    private func cancelReveal() {
        revealTask?.cancel(); revealTask = nil
        revealPending = ""; revealShown = ""; revealFinished = false
        reasonTask?.cancel(); reasonTask = nil
        reasonPending = ""; reasonShown = ""; reasonFinished = false
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
        cancelReveal()
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

    private static func decodeThinkingStage(_ json: String) -> String? {
        struct Stage: Decodable { let stage: String }
        guard let data = json.data(using: .utf8) else { return nil }
        return (try? JSONDecoder().decode(Stage.self, from: data))?.stage
    }

    private static func decodeSources(_ json: String) -> [ChatSource]? {
        struct Payload: Decodable { let sources: [ChatSource] }
        guard let data = json.data(using: .utf8) else { return nil }
        return (try? JSONDecoder().decode(Payload.self, from: data))?.sources
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
