//
//  CreditHistoryViewModel.swift
//  ios
//
//  Owns the credit statement: paging and the day grouping the screen renders.
//
//  Shaped after `NotificationInboxViewModel` — same six-case state machine, same keyset
//  cursor, same cancel-and-replace load. That is deliberate: both are a user-scoped,
//  append-only-at-the-head list behind a `.signInRequired` route, and the auth edge cases
//  below are the ones that took several releases to get right there.
//
//  PAGING IS A BUTTON (developer request, 2026-09-24), not scroll-triggered: 50 rows a page,
//  `loadMore()` on "Load more". The ledger is never trimmed, so a heavy user's statement goes
//  back to their first credit — the screen says where it ends instead of just stopping.
//

import Combine
import Foundation
import os

@MainActor
final class CreditHistoryViewModel: ObservableObject {

    enum State: Equatable {
        case loading
        case loaded
        case empty
        case error(String)
        /// A stored credential that has not been validated yet. NOT `signedOut` — this user is
        /// signed in as far as they are concerned, and `AppState.requestSignIn` declines to
        /// prompt during a restore, so a Sign In button here would be inert. (auth.md §5)
        case reconnecting
        /// No account. Distinct from `.empty`: their history may well exist, just not for a
        /// signed-out caller — "No credit activity yet" would be a lie by omission.
        case signedOut
    }

    @Published private(set) var state: State = .loading
    @Published private(set) var items: [CreditTransactionDTO] = []
    @Published private(set) var isLoadingMore = false
    /// The last "Load more" failed — the button offers a retry instead of silently staying put.
    @Published private(set) var loadMoreFailed = false

    /// There is an older page to fetch.
    var hasMore: Bool { nextCursor != nil }

    /// Day-grouped view of `items`.
    ///
    /// STORED, not computed. As a computed property this regrouped the entire accumulated
    /// list on every `body` evaluation — O(n) with dictionary churn, on the one screen that
    /// scrolls and whose list grows without bound as pages append. Recomputed in exactly
    /// every place `items` changes, via `regroup()`, so it cannot drift out of sync.
    @Published private(set) var days: [CreditHistoryDay] = []

    private func regroup() { days = CreditHistoryDay.group(items) }

    private let repository: CreditHistoryRepositoryProtocol
    private let log = Logger(subsystem: "com.phan.caydex", category: "credits")
    /// Keyset cursor. `nil` after a load means there is no next page.
    private var nextCursor: String?
    private var loadTask: Task<Void, Never>?
    private var loadMoreTask: Task<Void, Never>?
    /// Bumped by every `load()` and `reset()`. A "Load more" that started under an older
    /// generation lands on a list that has since been REPLACED (a refresh) or CLEARED (a
    /// sign-out) — appending it would splice a stale page, or another account's rows, into
    /// the new list. It is discarded instead.
    private var generation = 0

    /// ≤ the backend's `MAX_PAGE` (100, `credit_history_service.py`) — pinned by
    /// `test_ios_credit_history_compact.py`, since a larger ask is silently clamped there.
    private static let defaultPageSize = 50

    private static var pageSize: Int {
        #if DEBUG
        // The demo account may hold fewer than 50 movements, which leaves "Load more"
        // unreachable on the Simulator: `SIMCTL_CHILD_CAYDEX_CREDIT_PAGE_SIZE=5`.
        if let raw = ProcessInfo.processInfo.environment["CAYDEX_CREDIT_PAGE_SIZE"],
           let size = Int(raw) {
            return min(max(size, 1), defaultPageSize)
        }
        #endif
        return defaultPageSize
    }

    /// Optional + nil-coalesce, matching the codebase's injection idiom. The live default is
    /// constructed HERE, inside this `@MainActor` init, because a default argument would be
    /// evaluated at the call site under nonisolated checking.
    init(repository: CreditHistoryRepositoryProtocol? = nil) {
        self.repository = repository ?? CreditHistoryRepository()
    }

    // MARK: - Loading

    /// Fetch the first page, replacing whatever is on screen.
    ///
    /// Cancel-and-replace: a pull-to-refresh landing while a load is in flight must not let
    /// the older response win and show a stale balance history.
    func load() {
        invalidateLoadMore()
        loadTask?.cancel()
        loadTask = Task { [weak self] in
            await self?.performLoad()
        }
    }

    /// `load()` the caller can await, for `.refreshable`.
    func loadAndWait() async {
        load()
        await loadTask?.value
    }

    private func performLoad() async {
        // THREE outcomes, not two. `GET /users/me/credits/history` is `.signInRequired`, so a
        // signed-out caller is refused PRE-FLIGHT by APIClient and the raw failure would render
        // as a generic error blob. And "not armed right now" is not "signed out": at launch this
        // can run while session restore is still in flight.
        guard AppActions.shared.isSignedIn else {
            invalidateLoadMore()
            items = []
            regroup()
            nextCursor = nil
            state = AppActions.shared.isRestoringSession ? .reconnecting : .signedOut
            return
        }
        do {
            let page = try await repository.fetchCreditHistory(limit: Self.pageSize, before: nil)
            guard !Task.isCancelled else { return }
            // A "Load more" tapped WHILE this refresh was in flight holds a cursor from the
            // OLD list; landing after this, it would append rows older than that cursor onto
            // the fresh first page and leave a hole between them. Discard it.
            invalidateLoadMore()
            items = page.items
            regroup()
            nextCursor = page.nextCursor
            state = page.items.isEmpty ? .empty : .loaded
        } catch {
            guard !Task.isCancelled else { return }
            // ⚠️ `catch is CancellationError` does NOT work against APIClient — it wraps
            // anything unknown into `APIError.networkError`, so cancellation arrives nested.
            // `Task.isCancelled` is the reliable check.
            let appError = AppError.from(error)
            log.error("load credit history failed: \(String(describing: type(of: error))): \(appError.message, privacy: .public)")
            // An EMPTY statement and a BROKEN statement must not look alike — the backend
            // answers SYSTEM_BUSY rather than an empty 200 precisely so this branch can exist.
            // This is the screen someone opens when they ALREADY believe their credits are
            // wrong, so "No credit activity yet" over a read failure is the worst possible lie.
            //
            // Never an EMPTY message either: `AppError.message` passes some backend strings
            // through verbatim, and a blank one renders as a warning triangle with no sentence.
            let text = appError.message.trimmingCharacters(in: .whitespacesAndNewlines)
            invalidateLoadMore()
            state = .error(text.isEmpty ? "We couldn't load your credit history." : text)
        }
    }

    /// "Load more": append the next page. No-op when there is none, or one is in flight.
    func loadMore() {
        guard let cursor = nextCursor, !isLoadingMore else { return }
        isLoadingMore = true
        loadMoreFailed = false
        let started = generation
        loadMoreTask = Task { [weak self] in
            await self?.performLoadMore(cursor: cursor, generation: started)
        }
    }

    private func performLoadMore(cursor: String, generation started: Int) async {
        // Only the generation that started this may clear the spinner — a newer `load()` or
        // `reset()` has already reset it and may have a load of its own showing.
        defer { if started == generation { isLoadingMore = false } }
        do {
            let page = try await repository.fetchCreditHistory(limit: Self.pageSize, before: cursor)
            guard !Task.isCancelled, started == generation else { return }
            // De-duplicate by id. The cursor is on a strictly-unique bigserial so a repeat
            // should be impossible, but a duplicate `Identifiable` id inside a SwiftUI
            // ForEach is a runtime problem rather than a cosmetic one — too cheap not to guard.
            let known = Set(items.map(\.id))
            items.append(contentsOf: page.items.filter { !known.contains($0.id) })
            regroup()
            // A cursor that does not move would leave "Load more" re-fetching the same page
            // forever. The backend derives it from the page's last row so this should not
            // happen — stop cleanly (and say so in the log) if it ever does.
            if let next = page.nextCursor, next == cursor {
                log.warning("credit history: next_cursor did not advance (\(cursor, privacy: .public)) — stopping pagination")
                nextCursor = nil
            } else {
                nextCursor = page.nextCursor
            }
        } catch {
            guard !Task.isCancelled, started == generation else { return }
            loadMoreFailed = true
            let appError = AppError.from(error)
            log.error("load more credit history failed: \(String(describing: type(of: error))): \(appError.message, privacy: .public)")
            // Also reported, so an auth failure routes to sign-in and nothing is silent
            // (auth.md §6). The button itself turns into "Try again".
            AppActions.shared.reportMutationFailure(appError, action: "load more credit history")
        }
    }

    /// Drop any in-flight "Load more" and its UI state — see `generation`.
    private func invalidateLoadMore() {
        generation += 1
        loadMoreTask?.cancel()
        loadMoreTask = nil
        isLoadingMore = false
        loadMoreFailed = false
    }

    /// Clear everything when the session ends.
    ///
    /// auth.md §7: any store not keyed by user id must be reset when a session ends, or the
    /// next account to sign in on this device inherits the previous user's rows — which here
    /// would be showing one person another person's spending.
    func reset() {
        invalidateLoadMore()
        loadTask?.cancel()
        items = []
        regroup()
        nextCursor = nil
        state = .loading
    }
}
