//
//  NotificationInboxViewModel.swift
//  ios
//
//  Owns the notification inbox: paging, read state, and the unread count that drives the
//  tab-bar badge.
//

import Combine
import Foundation
import os

@MainActor
final class NotificationInboxViewModel: ObservableObject {

    enum State: Equatable {
        case loading
        case loaded
        case empty
        case error(String)
        /// A stored credential that has not been validated yet. NOT `signedOut` — this user is
        /// signed in as far as they are concerned, and `AppState.requestSignIn` declines to
        /// prompt during a restore, so a Sign In button here would be inert. (auth.md §5)
        case reconnecting
        /// No account. Distinct from `.empty`: their notifications may well exist, just not
        /// for this device — "No notifications yet" would be a lie by omission.
        case signedOut
    }

    @Published private(set) var state: State = .loading
    @Published private(set) var items: [NotificationEventDTO] = []
    @Published private(set) var unreadCount: Int = 0
    @Published private(set) var isLoadingMore = false

    private let repository: NotificationRepositoryProtocol
    private let log = Logger(subsystem: "com.phan.caydex", category: "notifications")
    /// Keyset cursor. `nil` after a load means there is no next page.
    private var nextCursor: String?
    private var loadTask: Task<Void, Never>?

    /// Optional + nil-coalesce, matching the codebase's injection idiom (`SearchViewModel`,
    /// `HomeDashboardViewModel`). `NotificationRepository.init` is MainActor-isolated, and a
    /// default argument is evaluated at the CALL SITE under nonisolated checking — so the
    /// live default is constructed here, inside this `@MainActor` init.
    init(repository: NotificationRepositoryProtocol? = nil) {
        self.repository = repository ?? NotificationRepository()
    }

    // MARK: - Loading

    /// Fetch the first page, replacing whatever is on screen.
    ///
    /// Cancel-and-replace: a pull-to-refresh landing while a load is in flight must not
    /// let the older response win and show stale rows.
    func load() {
        loadTask?.cancel()
        loadTask = Task { [weak self] in
            await self?.performLoad()
        }
    }

    /// `load()` that the caller can await.
    ///
    /// The Alerts tab has to mark everything read as soon as it has something to mark — that
    /// is what clears the badge — and it cannot do that before the first page lands.
    /// Sequencing on `state` instead would miss the case where the page is ALREADY loaded on
    /// re-entry, since `.onChange` does not fire when nothing changed.
    func loadAndWait() async {
        load()
        await loadTask?.value
    }

    private func performLoad() async {
        // THREE outcomes, not two. `GET /users/me/notifications` is `.signInRequired`, so a
        // signed-out caller is refused PRE-FLIGHT by APIClient and the raw failure would render
        // as a generic error blob. Worse, "not armed right now" is not "signed out": at launch
        // this can run while session restore is still in flight. Same guard shape as
        // `ResearchViewModel.loadReports`.
        //
        // This mattered less when Profile → Notification History was a second door to the same
        // list; it is the only door now.
        guard AppActions.shared.isSignedIn else {
            items = []
            nextCursor = nil
            state = AppActions.shared.isRestoringSession ? .reconnecting : .signedOut
            // Deliberately does NOT publish an unread count. A signed-out read proves nothing
            // about what is unread, and zeroing the badge here would be the second-writer bug
            // documented in `AlertsTabContent` wearing different clothes.
            return
        }
        do {
            let page = try await repository.fetchNotifications(limit: 30, before: nil)
            guard !Task.isCancelled else { return }
            items = page.items
            unreadCount = page.unreadCount
            nextCursor = page.nextCursor
            state = page.items.isEmpty ? .empty : .loaded
            AppState.notificationUnreadDidChange(unreadCount)
        } catch {
            guard !Task.isCancelled else { return }
            // ⚠️ `catch is CancellationError` does NOT work against APIClient — it wraps
            // anything unknown into `APIError.networkError`, so cancellation arrives
            // nested. `Task.isCancelled` is the reliable check (the same trap
            // SettingsSyncManager documents).
            let appError = AppError.from(error)
            log.error("load notifications failed: \(String(describing: type(of: error))): \(appError.message, privacy: .public)")
            // An EMPTY inbox and a BROKEN inbox must not look alike: the backend answers
            // 503 NOTIFICATIONS_UNAVAILABLE rather than an empty 200 precisely so this
            // branch can exist.
            //
            // And never an EMPTY message — `AppError.message` passes some backend strings
            // through verbatim, and a blank one renders as a warning triangle with no
            // sentence, which says less than saying nothing.
            let text = appError.message.trimmingCharacters(in: .whitespacesAndNewlines)
            state = .error(text.isEmpty ? "We couldn't load your notifications." : text)
        }
    }

    /// Append the next page. No-op when there is none, or one is already in flight.
    func loadMoreIfNeeded(currentItem item: NotificationEventDTO) async {
        guard let cursor = nextCursor, !isLoadingMore else { return }
        // Trigger a page ahead of the true end so the list does not visibly stall.
        guard items.suffix(5).contains(item) else { return }

        isLoadingMore = true
        defer { isLoadingMore = false }
        do {
            let page = try await repository.fetchNotifications(limit: 30, before: cursor)
            // De-duplicate by id: keyset paging on a non-unique timestamp can legitimately
            // repeat a row at a page boundary, and a duplicate `Identifiable` id inside a
            // SwiftUI List is a runtime problem, not a cosmetic one.
            let known = Set(items.map(\.id))
            items.append(contentsOf: page.items.filter { !known.contains($0.id) })
            unreadCount = page.unreadCount
            nextCursor = page.nextCursor
            AppState.notificationUnreadDidChange(unreadCount)
        } catch {
            // Non-fatal: the rows already on screen stay. Report so it is not silent —
            // a pagination failure that says nothing looks like "that's all there is".
            AppActions.shared.reportMutationFailure(
                AppError.from(error), action: "load more notifications"
            )
        }
    }

    // MARK: - Read state

    /// Mark one row read. Optimistic in memory, reconciled from the server response.
    ///
    /// Optimistic UI is fine; PERSISTING it before the server confirms is not — so
    /// nothing is written to disk here, and the authoritative unread count comes back
    /// from the same call.
    func markRead(_ item: NotificationEventDTO) async {
        guard !item.isRead else { return }
        applyOptimisticRead(ids: [item.id])
        do {
            let result = try await repository.markRead(ids: [item.id])
            unreadCount = result.unreadCount
            AppState.notificationUnreadDidChange(unreadCount)
        } catch {
            // Reload rather than guess: the badge is the one piece of state a user sees
            // without opening the app, and a wrong one is worse than a brief spinner.
            AppActions.shared.reportMutationFailure(
                AppError.from(error), action: "mark that notification read"
            )
            load()
        }
    }

    func markAllRead() async {
        guard unreadCount > 0 else { return }
        applyOptimisticRead(ids: items.filter { !$0.isRead }.map(\.id))
        do {
            let result = try await repository.markAllRead()
            unreadCount = result.unreadCount
            AppState.notificationUnreadDidChange(unreadCount)
        } catch {
            AppActions.shared.reportMutationFailure(
                AppError.from(error), action: "mark your notifications read"
            )
            load()
        }
    }

    /// In-memory only. `NotificationEventDTO` is immutable and decode-only, so the read
    /// stamp is applied by rebuilding the affected rows through a lightweight wrapper the
    /// view reads instead of mutating the DTO.
    private func applyOptimisticRead(ids: [String]) {
        guard !ids.isEmpty else { return }
        let marked = Set(ids)
        locallyRead.formUnion(marked)
        unreadCount = max(0, unreadCount - marked.count)
        AppState.notificationUnreadDidChange(unreadCount)
    }

    /// Ids marked read on this device but not yet confirmed by the server. The row view
    /// ORs this with `item.isRead`, so the dot disappears immediately on tap without
    /// pretending the DTO changed.
    @Published private(set) var locallyRead: Set<String> = []

    func isRead(_ item: NotificationEventDTO) -> Bool {
        item.isRead || locallyRead.contains(item.id)
    }

    /// Ids that were still unread when this viewing session began.
    ///
    /// The Alerts tab clears the badge by marking everything read the moment it is shown —
    /// which is the whole point, since the reported bug was a badge nothing could clear.
    /// Without this snapshot that would blank every row as it appeared, so the screen you
    /// opened to see what was new would tell you nothing.
    @Published private(set) var unreadOnOpen: Set<String> = []

    /// Draw the "new" dot for rows that were unread when you arrived, even once the
    /// mark-all-read below has landed.
    func showsUnreadDot(_ item: NotificationEventDTO) -> Bool {
        unreadOnOpen.contains(item.id) || !isRead(item)
    }

    /// Mark everything read because the user is LOOKING at the list.
    ///
    /// Separate from `markAllRead()` — that one is the explicit toolbar action and carries no
    /// snapshot. Safe to call on every appearance: `markAllRead` no-ops at `unreadCount == 0`,
    /// and the snapshot only ever grows within a viewing session.
    func markAllReadOnView() async {
        guard unreadCount > 0 else { return }
        unreadOnOpen.formUnion(items.filter { !isRead($0) }.map(\.id))
        await markAllRead()
    }

    /// Clear everything when the session ends.
    ///
    /// auth.md §7: any store not keyed by user id must be reset when a session ends, or
    /// the next account to sign in on this device inherits the previous user's rows.
    func reset() {
        loadTask?.cancel()
        items = []
        unreadCount = 0
        nextCursor = nil
        locallyRead = []
        unreadOnOpen = []
        state = .loading
    }
}
