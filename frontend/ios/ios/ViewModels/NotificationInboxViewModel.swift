//
//  NotificationInboxViewModel.swift
//  ios
//
//  Owns the notification inbox: paging, read state, and the unread count that drives the
//  tab-bar badge.
//

import Combine
import Foundation

@MainActor
final class NotificationInboxViewModel: ObservableObject {

    enum State: Equatable {
        case loading
        case loaded
        case empty
        case error(String)
    }

    @Published private(set) var state: State = .loading
    @Published private(set) var items: [NotificationEventDTO] = []
    @Published private(set) var unreadCount: Int = 0
    @Published private(set) var isLoadingMore = false

    private let repository: NotificationRepositoryProtocol
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

    private func performLoad() async {
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
            // An EMPTY inbox and a BROKEN inbox must not look alike: the backend answers
            // 503 NOTIFICATIONS_UNAVAILABLE rather than an empty 200 precisely so this
            // branch can exist.
            state = .error(appError.message)
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
        state = .loading
    }
}
