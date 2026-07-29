//
//  HomeDashboardViewModel.swift
//  ios
//
//  ViewModel for the redesigned Home dashboard — MVVM + Repository.
//
//  Owns data fetching via `HomeRepositoryProtocol` and exposes view-ready state.
//  Defaults to `MockHomeRepository` (UI only, no backend) but accepts any
//  conforming repository via init for testing / a future live implementation.
//  Matches the codebase convention: `ObservableObject` + `@Published`, with
//  boolean loading / error flags (see HomeViewModel).
//

import Foundation
import Combine

@MainActor
final class HomeDashboardViewModel: ObservableObject {

    // MARK: - Published state
    @Published private(set) var data: HomeDashboardData?
    @Published private(set) var isLoading: Bool = false
    @Published private(set) var errorMessage: String?

    /// True once a load has been ATTEMPTED (successfully or not).
    ///
    /// Gates the full-screen `LoadingOverlay`, which dims the screen and — like
    /// the copies removed from the detail views — swallows every touch including
    /// the tab bar. Without this, the 60s auto-refresh re-raised that overlay on
    /// every poll for as long as the first load kept failing (`isLoading &&
    /// data == nil` stays true), locking the user out of the app in ~30s bursts
    /// during an outage. After the first attempt the error banner carries the
    /// message instead and the UI stays interactive.
    @Published private(set) var hasAttemptedLoad: Bool = false

    // MARK: - Dependencies
    private let repository: HomeRepositoryProtocol

    /// When the last SUCCESSFUL load landed. Nil after a failure, so a screen
    /// that never got data retries on the next trigger instead of staying blank
    /// for the whole process lifetime.
    private var lastLoadedAt: Date?

    /// Auto-refresh loop. Home is opacity-mounted (it never leaves the view
    /// hierarchy), so `.task` fires exactly once per process — without this the
    /// strip showed launch-time prices and a launch-time "Pre-Market" label for
    /// the rest of the session.
    private var refreshTask: Task<Void, Never>?

    /// Matches the backend's 5-minute pulse cache: anything fresher than this is
    /// served from that cache anyway, so re-fetching sooner buys nothing.
    static let stalenessWindow: TimeInterval = 300

    /// Poll cadence. Deliberately shorter than `stalenessWindow` so the
    /// market-status header (recomputed fresh on every backend request, never
    /// cached) flips within a minute of an open/close boundary, while the tile
    /// prices still cost at most one upstream fan-out per 5 minutes.
    private static let refreshInterval: UInt64 = 60_000_000_000  // 60s

    deinit { refreshTask?.cancel() }

    // MARK: - Init
    // Optional + nil-coalesce (matches the codebase's repository-injection idiom,
    // e.g. SearchViewModel) so the default LIVE repository is built inside the
    // @MainActor init rather than in a nonisolated default-argument context.
    // Pass `MockHomeRepository()` for offline previews / tests.
    init(repository: HomeRepositoryProtocol? = nil) {
        self.repository = repository ?? HomeRepository()
    }

    // MARK: - Loading

    // NOTE: the old `loadIfNeeded()` (guard on `data == nil`) was deliberately
    // REMOVED rather than kept alongside `loadIfStale`. "Load once and never
    // again" is the exact bug this screen had — Home is opacity-mounted, so that
    // guard froze the prices and the market-status header for the whole process.
    // Don't reintroduce it; use `loadIfStale` from the tab/foreground triggers.

    /// Re-fetch only when the data is older than `maxAge`.
    ///
    /// Used by the tab-activation and foreground triggers, which can fire far
    /// more often than the data actually changes. A failed load leaves
    /// `lastLoadedAt` nil, so a screen that is still blank always retries.
    func loadIfStale(maxAge: TimeInterval = HomeDashboardViewModel.stalenessWindow) async {
        if let last = lastLoadedAt, Date().timeIntervalSince(last) < maxAge { return }
        await load()
    }

    func load() async {
        isLoading = true
        errorMessage = nil
        do {
            data = try await repository.fetchHomeDashboard()
            lastLoadedAt = Date()
        } catch {
            // Route through AppError like every other surface — a single
            // hardcoded string can't tell "you're offline" from "you're signed
            // out" from "we're rate-limited", and the user gets no actionable
            // hint. Existing data is deliberately kept on screen (stale beats
            // blank); only the banner changes.
            errorMessage = AppError.from(error).message
            #if DEBUG
            print("❌ [HomeDashboardVM] load failed: \(type(of: error)): \(error)")
            #endif
        }
        isLoading = false
        hasAttemptedLoad = true
    }

    /// Pull-to-refresh — always re-fetches, regardless of staleness.
    func refresh() async {
        await load()
    }

    // MARK: - Auto refresh

    /// Start (or restart) the polling loop. Safe to call repeatedly; the previous
    /// loop is cancelled first. Call `stopAutoRefresh()` when Home stops being the
    /// active tab so a hidden screen isn't polling in the background.
    func startAutoRefresh() {
        refreshTask?.cancel()
        refreshTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: Self.refreshInterval)
                guard !Task.isCancelled, let self else { break }
                // Cheap when nothing has aged out: the backend's 5-minute pulse
                // cache absorbs it, and the market-status header is recomputed
                // fresh server-side on every request.
                await self.load()
            }
        }
    }

    func stopAutoRefresh() {
        refreshTask?.cancel()
        refreshTask = nil
    }
}
