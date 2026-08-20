//
//  Analytics.swift
//  ios
//
//  First-party product analytics. Buffers events and flushes them to
//  `POST /api/v1/events`.
//
//  WHY FIRST-PARTY (no PostHog / Amplitude / TelemetryDeck)
//  --------------------------------------------------------
//  The App Privacy questionnaire and both privacy-policy surfaces were finalised
//  before launch (documents/legal/app-privacy-answers.md — 8 data types,
//  tracking = No). Adding a third-party analytics SDK reopens all of that and adds
//  a processor to disclose. Events into our own backend disclose nothing new.
//
//  RULES THIS FILE ENFORCES
//  ------------------------
//  1. **Never affects the app.** Every call is fire-and-forget. Failures are
//     swallowed. `track` returns instantly — it only appends to an in-memory buffer.
//  2. **Never sends user-typed text.** Props are literals chosen at the call site:
//     tickers, persona keys, screen names, tiers. Never a chat message, a search
//     query, an email, or an error body. The backend independently enforces an
//     allowlist and size caps, but the first line of defence is here.
//  3. **Batched, not chatty.** Events flush on background, on a full buffer, or on a
//     timer — not one request per event.
//
//  Identity is server-side: the backend derives the bucket from the `X-Guest-Id`
//  header APIClient already sends (or the Bearer token when signed in), so nothing
//  identifying is assembled here.
//

import Foundation
import OSLog
import UIKit

/// The fixed analytics vocabulary. Must stay in sync with `ALLOWED_EVENTS` in
/// `backend/app/schemas/analytics.py` — the backend DROPS unknown names (it does not
/// reject the batch), so a drift here shows up as a silently empty metric.
enum AnalyticsEventName: String {
    case appOpen = "app_open"
    case screenView = "screen_view"
    case reportRequested = "report_requested"
    case reportCompleted = "report_completed"
    case reportFailed = "report_failed"
    case chatSent = "chat_sent"
    case paywallShown = "paywall_shown"
    case paywallPurchaseStarted = "paywall_purchase_started"
    case purchaseCompleted = "purchase_completed"
    /// Consumable credit packs ("Buy Credits"). Separate from the `paywall*` trio on purpose:
    /// a top-up and a subscription are different decisions, and merging their funnels would
    /// make the subscription conversion rate — the number the pricing model rests on —
    /// unreadable. Carry `product_id`, and `count` for the credits granted.
    case creditPackShown = "credit_pack_shown"
    case creditPackPurchaseStarted = "credit_pack_purchase_started"
    case creditPackPurchased = "credit_pack_purchased"
    case watchlistAdded = "watchlist_added"
    case lessonCompleted = "lesson_completed"
    case audioPlayed = "audio_played"
    /// First-run funnel. `onboardingCompleted` carries `count` — the activation
    /// metric, since a populated watchlist is what makes Updates, the Home strip,
    /// and push relevant at all.
    case onboardingCompleted = "onboarding_completed"
    case onboardingSkipped = "onboarding_skipped"
    /// Personalized explanations opted in / out. Deliberately property-less: the point
    /// is the RATE and the consent audit trail, and the answers themselves are the
    /// reader's own reading interests, which have no business in the analytics stream.
    case personalizationConsentGranted = "personalization_consent_granted"
    case personalizationConsentWithdrawn = "personalization_consent_withdrawn"
    /// A user-initiated write that failed and was reverted. Carries `action` (what they were
    /// doing) and `code` (`AppError.analyticsCode`). Exists because this whole class of failure
    /// used to be a `print` — so in production there was no way to know how often a tap on
    /// Follow, a star, or a portfolio edit quietly did nothing.
    case mutationFailed = "mutation_failed"

    /// Push funnel. Nothing in the app measured notifications at all, so "did anyone tap
    /// it?" and "how many people turned them off?" — the two questions that decide whether
    /// a category is worth sending — were unanswerable.
    ///
    /// `kind` is the fixed registry key ("earnings_upcoming"), `route` the destination
    /// family. NEVER a ticker: `props` is for low-cardinality dimensions, and a per-user
    /// value there is both useless as a metric and a privacy footgun.
    case pushReceived = "push_received"
    case pushOpened = "push_opened"
    case pushPermissionResult = "push_permission_result"
    case pushRegisterFailed = "push_register_failed"
    case notificationInboxOpened = "notification_inbox_opened"
    case priceAlertCreated = "price_alert_created"
    /// A best-effort background sync that failed (settings hydrate/push, device registration,
    /// guest-data claim). Same rationale: these were `#if DEBUG` prints, i.e. invisible in the
    /// builds that matter.
    case backgroundSyncFailed = "background_sync_failed"
}

/// A single buffered event. `props` is deliberately `[String: AnalyticsValue]` rather
/// than `[String: Any]` so a non-scalar can't be passed by accident.
struct BufferedEvent: Encodable, Sendable {
    let event: String
    let props: [String: AnalyticsValue]
    let clientTs: String

    enum CodingKeys: String, CodingKey {
        case event, props
        case clientTs = "client_ts"
    }
}

/// Scalar-only prop values. The type system is the enforcement: there is no case for
/// a dictionary or array, so a nested payload cannot be attached to an event.
enum AnalyticsValue: Encodable, Sendable {
    case string(String)
    case int(Int)
    case double(Double)
    case bool(Bool)

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let v): try c.encode(v)
        case .int(let v):    try c.encode(v)
        case .double(let v): try c.encode(v)
        case .bool(let v):   try c.encode(v)
        }
    }
}

extension AnalyticsValue: ExpressibleByStringLiteral,
                          ExpressibleByIntegerLiteral,
                          ExpressibleByFloatLiteral,
                          ExpressibleByBooleanLiteral {
    init(stringLiteral value: String)  { self = .string(value) }
    init(integerLiteral value: Int)    { self = .int(value) }
    init(floatLiteral value: Double)   { self = .double(value) }
    init(booleanLiteral value: Bool)   { self = .bool(value) }
}

/// `nonisolated`: encoded by `APIClient` (an actor) off the main actor, so its `Encodable`
/// conformance must not be main-actor-isolated. Already `Sendable`; this makes it honest.
nonisolated struct AnalyticsBatchRequest: Encodable, Sendable {
    let events: [BufferedEvent]
    let sessionId: String

    enum CodingKeys: String, CodingKey {
        case events
        case sessionId = "session_id"
    }
}

actor Analytics {
    static let shared = Analytics()

    /// Matches `MAX_EVENTS_PER_BATCH` on the backend. Flushing at the cap keeps a busy
    /// session from ever exceeding what the server will accept in one request.
    private static let batchSize = 50
    /// Hard ceiling on the buffer so a long offline stretch can't grow memory without
    /// bound. Oldest events are dropped first — recent behaviour is the useful part.
    private static let maxBuffered = 500
    private static let flushInterval: TimeInterval = 30

    /// Per-launch, not per-install: lets events be grouped into a session without
    /// carrying any identity of its own (identity is server-derived).
    private let sessionId = UUID().uuidString

    private static let log = Logger(subsystem: "com.phan.caydex", category: "analytics")

    private var buffer: [BufferedEvent] = []
    private var flushTask: Task<Void, Never>?
    private var isFlushing = false

    private let iso: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    // MARK: - Public API

    /// Record an event. Returns immediately; nothing here can throw or block.
    nonisolated func track(
        _ event: AnalyticsEventName,
        _ props: [String: AnalyticsValue] = [:]
    ) {
        Task { await self.enqueue(event, props) }
    }

    /// Flush now. Called on `scenePhase == .background` — the moment the app is most
    /// likely to be suspended with buffered events still pending.
    ///
    /// Held open by a background-task assertion, because "about to be suspended" is exactly
    /// when an un-asserted request loses its race: the buffer is an in-memory actor property
    /// with NO disk persistence, so a process frozen mid-flush loses the batch outright, with
    /// nothing to resume from on next launch.
    nonisolated func flushNow() {
        Task { @MainActor in
            let app = UIApplication.shared
            var assertion: UIBackgroundTaskIdentifier = .invalid
            assertion = app.beginBackgroundTask(withName: "caydex.analytics.flush") {
                guard assertion != .invalid else { return }
                app.endBackgroundTask(assertion)
                assertion = .invalid
            }
            await self.flush()
            guard assertion != .invalid else { return }
            app.endBackgroundTask(assertion)
            assertion = .invalid
        }
    }

    // MARK: - Internals

    private func enqueue(_ event: AnalyticsEventName, _ props: [String: AnalyticsValue]) {
        buffer.append(BufferedEvent(
            event: event.rawValue,
            props: props,
            clientTs: iso.string(from: Date())
        ))

        if buffer.count > Self.maxBuffered {
            // Drop OLDEST. A long offline stretch should cost the stale tail, not the
            // events that just happened.
            buffer.removeFirst(buffer.count - Self.maxBuffered)
        }

        if buffer.count >= Self.batchSize {
            Task { await self.flush() }
        } else {
            scheduleFlush()
        }
    }

    private func scheduleFlush() {
        guard flushTask == nil else { return }
        flushTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.flushInterval * 1_000_000_000))
            guard !Task.isCancelled else { return }
            // MUST be `flushFromTimer`, never `flush()` directly — see below.
            await self?.flushFromTimer()
        }
    }

    /// The timer's OWN entry point into `flush()`.
    ///
    /// `flush()` opens by cancelling `flushTask` to kill a pending timer. That is right for the
    /// other two callers — the batch-cap path and `flushNow()` each run in a FRESH task, so the
    /// only thing cancelled is the sleeping timer. From inside the timer task it was suicide:
    /// `flushTask` still referenced the task that was executing, so `flush()` cancelled itself,
    /// and `URLSession.data(for:)` — which is cancellation-aware — failed instantly with
    /// `URLError.cancelled` (-999) before the request ever left the device.
    ///
    /// Every 30-second flush therefore failed, and the batch was dropped by the `catch` below.
    /// Since a normal session buffers well under `batchSize` events and is killed rather than
    /// backgrounded cleanly, that was very nearly ALL analytics — while looking healthy, because
    /// the drop was reported only in DEBUG.
    ///
    /// Releasing the slot instead of cancelling it is the whole fix.
    private func flushFromTimer() async {
        flushTask = nil
        await flush()
    }

    private func flush() async {
        flushTask?.cancel()
        flushTask = nil

        // One flush at a time: two concurrent flushes would either double-send the
        // same events or interleave and lose some on failure.
        guard !isFlushing, !buffer.isEmpty else { return }
        isFlushing = true
        defer { isFlushing = false }

        let batch = Array(buffer.prefix(Self.batchSize))
        buffer.removeFirst(batch.count)

        do {
            let _: AnalyticsBatchResponse = try await APIClient.shared.request(
                endpoint: .trackEvents(
                    AnalyticsBatchRequest(events: batch, sessionId: sessionId)
                ),
                responseType: AnalyticsBatchResponse.self
            )
        } catch {
            // Cancellation is NOT a delivery failure — the request never reached the network,
            // so the retry-amplifier argument below does not apply to it. Put the batch back.
            // Re-inserting at the FRONT restores the original order, which means the bound
            // must now trim the TAIL (the stale end), the opposite end from `enqueue`.
            if Task.isCancelled || (error as? URLError)?.code == .cancelled
                || (AppError.from(error)).isCancellation {
                buffer.insert(contentsOf: batch, at: 0)
                // Same overflow policy as `enqueue`: drop the OLDEST. Re-inserting at the
                // front restores chronological order, so the oldest are once again at the
                // front — which means an overflow here discards part of the batch we just put
                // back. That is correct and deliberate: 500 buffered events means a long
                // offline stretch, and recent behaviour is the useful part.
                if buffer.count > Self.maxBuffered {
                    buffer.removeFirst(buffer.count - Self.maxBuffered)
                }
                Self.log.warning("analytics flush cancelled — \(batch.count, privacy: .public) event(s) re-queued")
                return
            }
            // Deliberately NOT re-queued. Retrying telemetry during a backend outage
            // turns every client into a retry amplifier against an already-struggling
            // server, and stale events aren't worth that. Analytics is lossy by design.
            //
            // Logged at `warning` in EVERY configuration, not just DEBUG. The self-cancelling
            // timer above went unnoticed precisely because this was a `#if DEBUG print`:
            // in production, analytics failing 100% of the time looked exactly like analytics
            // working. Silent degradation is the hardest bug to find.
            Self.log.warning(
                "analytics flush of \(batch.count, privacy: .public) event(s) dropped — \(String(describing: type(of: error)), privacy: .public)"
            )
        }
    }
}

/// `nonisolated`: decoded inside the `Analytics` actor — see `AnalyticsBatchRequest`.
nonisolated struct AnalyticsBatchResponse: Decodable, Sendable {
    let accepted: Int
    let dropped: Int
}
