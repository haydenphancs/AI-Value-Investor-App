//
//  PriceAlertRulesViewModel.swift
//  ios
//
//  Every price-alert RULE the user has, across all tickers — the list half of the bell sheet,
//  without the create form.
//
//  WHY A SECOND VIEW MODEL. `PriceAlertsViewModel` is scoped to one ticker: `ticker` is
//  non-optional and drives the create form, the per-ticker cap and the "N alerts on AAPL"
//  copy. Widening it to an optional ticker would make every one of those meaningless-when-nil,
//  and the create form has no meaning at all without a ticker (you cannot set a price target
//  on nothing). So creating stays in the sheet and this only ever lists.
//
//  The transport already supported this: `fetchPriceAlerts(ticker:)` takes an OPTIONAL ticker
//  and `APIEndpoint.listPriceAlerts` omits the query parameter when it is nil. No backend or
//  repository change was needed.
//

import Combine
import Foundation
import os

@MainActor
final class PriceAlertRulesViewModel: ObservableObject {

    enum State: Equatable {
        case loading
        case loaded
        case error(String)
        /// A stored credential that has not been validated yet. NOT `signedOut` — this user is
        /// signed in as far as they are concerned, and `AppState.requestSignIn` declines to
        /// prompt during a restore, so a Sign In button here would be inert. (auth.md §5)
        case reconnecting
        case signedOut
    }

    @Published private(set) var state: State = .loading
    @Published private(set) var rules: [PriceAlertDTO] = []
    @Published private(set) var maxPerUser: Int = 20

    private let repository: NotificationRepositoryProtocol
    private let log = Logger(subsystem: "com.phan.caydex", category: "price-alerts")

    /// When the last load that actually SAW the account completed. Drives `loadIfStale` so
    /// re-entering the tab does not refetch on every switch.
    private var lastLoadedAt: Date?

    /// Matches `ResearchViewModel.stalenessWindow`'s shape: long enough that tab-flipping is
    /// free, short enough that a rule created on another device shows up without a manual pull.
    /// `nonisolated` because it is used as a DEFAULT ARGUMENT below, and a default argument is
    /// evaluated at the call site under nonisolated checking — the same rule that forces the
    /// repository above to be nil-coalesced inside `init` rather than defaulted.
    nonisolated static let stalenessWindow: TimeInterval = 5 * 60

    /// Optional + nil-coalesce: `NotificationRepository.init` is MainActor-isolated and a
    /// default argument is checked as nonisolated at the CALL SITE, so the live default is
    /// constructed here, inside this `@MainActor` init. Same note as
    /// `NotificationInboxViewModel.init`.
    init(repository: NotificationRepositoryProtocol? = nil) {
        self.repository = repository ?? NotificationRepository()
    }

    var isEmpty: Bool { rules.isEmpty }

    // MARK: - Loading

    func load() async {
        // THREE outcomes, not two. "Not armed right now" is not the same as "signed out": at
        // launch this runs while session restore is still in flight, and a restore that keeps
        // failing backs off forever. Collapsing that into the sign-in prompt is what once told
        // a signed-in user to sign in, with their own avatar loaded above it. Same guard shape
        // as `ResearchViewModel.loadReports`.
        guard AppActions.shared.isSignedIn else {
            rules = []
            state = AppActions.shared.isRestoringSession ? .reconnecting : .signedOut
            // Neither pass saw the account, so neither is ever "fresh" — otherwise the
            // staleness window would suppress the reload that heals it on sign-in.
            lastLoadedAt = nil
            return
        }
        do {
            let page = try await repository.fetchPriceAlerts(ticker: nil)
            rules = page.items
            maxPerUser = page.maxPerUser
            state = .loaded
            lastLoadedAt = Date()
        } catch {
            let appError = AppError.from(error)
            // A failure that is never logged is diagnosed from nothing. Type + operation, so
            // it is greppable without a repro.
            log.error("load price alert rules failed: \(String(describing: type(of: error))): \(appError.message, privacy: .public)")
            // Backstop for a credential that dies MID-FLIGHT — the guard above cannot catch
            // that. Signed out and broken must not look alike: one wants a Sign In button, the
            // other Try Again, and "you have no price alerts" over an auth failure is a lie.
            if case .signInRequired = appError {
                state = .signedOut
            } else {
                // NEVER an empty string. `AppError.message` passes some backend messages
                // through verbatim (`.apiError`, `.validationFailed`), and an empty one
                // rendered as a bare warning triangle over a "Try Again" button with no
                // sentence at all — observed live on this screen.
                let text = appError.message.trimmingCharacters(in: .whitespacesAndNewlines)
                state = .error(text.isEmpty ? "We couldn't load your price alerts." : text)
            }
            rules = []
            // A pass that never saw the account is NEVER fresh — otherwise the staleness
            // window suppresses the very reload that heals it once the user signs in.
            lastLoadedAt = nil
        }
    }

    /// Reload only if the last successful load has aged out. Mirrors
    /// `ResearchViewModel.loadIfStale` so tab re-entry is cheap.
    func loadIfStale(maxAge: TimeInterval = PriceAlertRulesViewModel.stalenessWindow) async {
        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }
        await load()
    }

    // MARK: - Mutate

    func toggleActive(_ rule: PriceAlertDTO) async {
        do {
            let updated = try await repository.updatePriceAlert(
                id: rule.id, threshold: nil, isActive: !rule.isActive, repeatMode: nil
            )
            replace(updated)
        } catch {
            AppActions.shared.reportMutationFailure(
                AppError.from(error),
                action: rule.isActive ? "turn off that alert" : "turn on that alert"
            )
            // Re-read rather than guess: the row's `armed` state also changes server-side when
            // it is re-enabled, and showing a stale one is how "my alert never fires" becomes
            // unexplainable.
            await load()
        }
    }

    func delete(_ rule: PriceAlertDTO) async {
        // Optimistic in MEMORY only — nothing is persisted before the server confirms, so a
        // kill mid-request cannot make a deletion the server never received durable.
        let snapshot = rules
        rules.removeAll { $0.id == rule.id }
        do {
            _ = try await repository.deletePriceAlert(id: rule.id)
        } catch {
            rules = snapshot
            AppActions.shared.reportMutationFailure(
                AppError.from(error), action: "delete that price alert"
            )
        }
    }

    /// auth.md §7 — this list is the caller's own data and must not survive a session end.
    func reset() {
        rules = []
        maxPerUser = 20
        lastLoadedAt = nil
        state = .loading
    }

    private func replace(_ updated: PriceAlertDTO) {
        if let index = rules.firstIndex(where: { $0.id == updated.id }) {
            rules[index] = updated
        }
    }
}
