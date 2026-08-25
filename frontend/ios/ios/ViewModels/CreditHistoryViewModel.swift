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

    private static let pageSize = 30

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
            items = []
            regroup()
            nextCursor = nil
            state = AppActions.shared.isRestoringSession ? .reconnecting : .signedOut
            return
        }
        do {
            let page = try await repository.fetchCreditHistory(limit: Self.pageSize, before: nil)
            guard !Task.isCancelled else { return }
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
            state = .error(text.isEmpty ? "We couldn't load your credit history." : text)
        }
    }

    /// Append the next page. No-op when there is none, or one is already in flight.
    func loadMoreIfNeeded(currentItem item: CreditTransactionDTO) async {
        guard let cursor = nextCursor, !isLoadingMore else { return }
        // Trigger a page ahead of the true end so the list does not visibly stall.
        guard items.suffix(5).contains(item) else { return }

        isLoadingMore = true
        defer { isLoadingMore = false }
        do {
            let page = try await repository.fetchCreditHistory(limit: Self.pageSize, before: cursor)
            guard !Task.isCancelled else { return }
            // De-duplicate by id. The cursor is on a strictly-unique bigserial so a repeat
            // should be impossible, but a duplicate `Identifiable` id inside a SwiftUI
            // ForEach is a runtime problem rather than a cosmetic one — too cheap not to guard.
            let known = Set(items.map(\.id))
            items.append(contentsOf: page.items.filter { !known.contains($0.id) })
            regroup()
            nextCursor = page.nextCursor
        } catch {
            guard !Task.isCancelled else { return }
            // Non-fatal: the rows already on screen stay. Reported so it is not silent — a
            // pagination failure that says nothing looks like "that's all there is", which on
            // a statement means "you were never charged for that".
            AppActions.shared.reportMutationFailure(
                AppError.from(error), action: "load more credit history"
            )
        }
    }

    /// Clear everything when the session ends.
    ///
    /// auth.md §7: any store not keyed by user id must be reset when a session ends, or the
    /// next account to sign in on this device inherits the previous user's rows — which here
    /// would be showing one person another person's spending.
    func reset() {
        loadTask?.cancel()
        items = []
        regroup()
        nextCursor = nil
        state = .loading
    }
}
