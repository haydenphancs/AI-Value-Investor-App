//
//  PushNotificationDetailViewModel.swift
//  ios
//
//  The detail screen a TAPPED PUSH opens: the pushed copy at once, the full inbox row as soon
//  as it can be fetched.
//

import Combine
import Foundation
import os

/// Owns what `PushNotificationDetailScreen` shows.
///
/// WHY TWO VERSIONS OF ONE NOTIFICATION. The payload is on the device the instant the user taps,
/// so the detail renders from it with no spinner — but its body is the BANNER cut: the backend
/// trims it to 180 chars + "…" for APNs (`push_service.truncate_for_banner`) while the inbox row
/// keeps the full text, and for a `ticker_move` the part that was cut is the catalyst the alert is
/// about. The row is fetched by the payload's `dedup_key` and replaces the pushed copy.
///
/// The fetch is BEST-EFFORT by design: the pushed copy is already on screen, so a failure leaves
/// a readable screen and is logged, never toasted. The one miss worth retrying is the session not
/// being armed yet — a cold launch from the tap races `restoreSession` — and the screen retries
/// that on `.authenticated` (`awaitingSession`).
@MainActor
final class PushNotificationDetailViewModel: ObservableObject {

    /// Always one member: a push is one notification. `CollapsedGroup` because that is what
    /// `NotificationDetailView` renders, so both notification doors share one screen.
    @Published private(set) var group: NotificationInboxSection.CollapsedGroup

    /// The full row is still owed because the session was not armed when we asked. The screen
    /// re-runs `load()` when auth reaches `.authenticated`; nothing else would (a push can land
    /// the user here before any tab's own reload trigger has a reason to fire).
    @Published private(set) var awaitingSession = false

    let pushed: PushedNotification

    private let repository: NotificationRepositoryProtocol
    private let log = Logger(subsystem: "com.phan.caydex", category: "notifications")
    /// A definitive answer arrived (a row, or "no such row"). Retrying would change nothing.
    private var isSettled = false
    private var isLoading = false

    init(pushed: PushedNotification, repository: NotificationRepositoryProtocol? = nil) {
        self.pushed = pushed
        self.group = NotificationInboxSection.CollapsedGroup(items: [pushed.event])
        self.repository = repository ?? NotificationRepository()
    }

    func load() async {
        guard !isSettled, !isLoading else { return }
        guard let dedupKey = pushed.dedupKey, !dedupKey.isEmpty else {
            // Every live sender sends one (`push_dispatch_service._deliver`); a payload without
            // it can only show the pushed copy.
            log.warning("push detail: payload carried no dedup_key (kind=\(self.pushed.kind, privacy: .public)) — showing the pushed copy")
            isSettled = true
            return
        }
        isLoading = true
        defer { isLoading = false }
        do {
            let row = try await repository.fetchNotification(dedupKey: dedupKey)
            isSettled = true
            awaitingSession = false
            guard let row else {
                // A push can outlive its row (90-day retention). Keep the pushed copy.
                log.info("push detail: no inbox row for this push (kind=\(self.pushed.kind, privacy: .public)) — showing the pushed copy")
                return
            }
            group = NotificationInboxSection.CollapsedGroup(items: [row])
            // Opening it IS reading it — the same call an Alerts row makes on tap, so the
            // badge, the Alerts list and the server all agree.
            await NotificationInboxViewModel.shared.markRead(row)
        } catch {
            let appError = AppError.from(error)
            if case .signInRequired = appError {
                // Refused pre-flight: no token armed yet (cold launch racing `restoreSession`,
                // or `.restoring`). Not an answer — ask again once the session is back.
                awaitingSession = true
                log.info("push detail: session not armed yet — full row deferred until sign-in completes")
            } else {
                // Includes a backend that predates `/me/notifications/lookup` (404): the pushed
                // copy stays, which is the whole degraded mode.
                log.warning("push detail: full-row lookup failed (kind=\(self.pushed.kind, privacy: .public), \(String(describing: type(of: error)), privacy: .public)): \(appError.message, privacy: .public) — showing the pushed copy")
            }
        }
    }
}
