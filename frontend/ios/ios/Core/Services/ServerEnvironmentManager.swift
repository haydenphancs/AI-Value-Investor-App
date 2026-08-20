//
//  ServerEnvironmentManager.swift
//  ios
//
//  Auto-detects whether a local backend is running and dynamically
//  routes API traffic to localhost or Railway.
//
//  Behavior:
//    - Probes localhost:8000/health/live on app launch and each foreground event
//    - If localhost responds, all traffic routes locally
//    - If localhost goes down mid-session, automatically falls back to Railway
//    - When localhost comes back, next re-probe picks it up
//
//  Manual overrides (Xcode scheme → Environment Variables):
//    USE_LOCAL=1    → always use localhost (skip probe)
//    USE_RAILWAY=1  → always use Railway (skip probe)
//

import Foundation

/// Single-flight coordinator for `resolve()`.
///
/// `resolve()` had no guard of any kind, and TWO launch triggers call it: the root `.task` in
/// `iosApp` and the `didBecomeActive` observer — which also fires on a cold launch. Every
/// launch therefore paid for two identical 1-second `health/live` probes, and a launch log
/// showed the pair of `-1004`s to prove it.
private actor ResolveCoordinator {
    private var inFlight: Task<Void, Never>?

    func run(_ body: @escaping @Sendable () async -> Void) async {
        if let running = inFlight, !running.isCancelled {
            await running.value
            return
        }
        let task = Task { await body() }
        inFlight = task
        await task.value
        inFlight = nil
    }
}

final class ServerEnvironmentManager: @unchecked Sendable {

    private let resolveCoordinator = ResolveCoordinator()

    // MARK: - Singleton

    nonisolated static let shared = ServerEnvironmentManager()

    // MARK: - State (nonisolated for cross-actor access from APIClient)

    /// The resolved backend URL. `nil` until `resolve()` completes.
    nonisolated(unsafe) private(set) var resolvedBaseURL: URL?

    /// Whether the resolved URL points to localhost.
    nonisolated(unsafe) private(set) var isLocal: Bool = false

    /// Whether a manual override is active (skips probing).
    nonisolated(unsafe) private(set) var isManualOverride: Bool = false

    // MARK: - Constants

    let localURL = URL(string: "http://127.0.0.1:8000")!
    let railwayURL = URL(string: "https://ai-value-investor-app-production.up.railway.app")!

    /// Timeout for the localhost liveness probe (seconds).
    ///
    /// Probes `health/live`, which is instant and dependency-free — NOT `/health`, which
    /// round-trips Supabase and measured 0.34-0.52s against the old 0.5s budget. That made
    /// the probe a coin flip, and losing it silently pointed a DEBUG build at PRODUCTION
    /// while the developer believed they were testing locally.
    ///
    /// 1.0s rather than 0.5s for margin on a cold `--reload` restart. It is only ever paid by
    /// someone with no local server running, once per launch/foreground.
    private let probeTimeout: TimeInterval = 1.0

    // MARK: - Init

    private init() {}

    // MARK: - Resolution

    /// Probes the local backend and sets `resolvedBaseURL`.
    /// Called at app launch and on each foreground event.
    ///
    /// Single-flight: concurrent callers JOIN the running probe rather than starting another.
    /// See `ResolveCoordinator`.
    func resolve() async {
        await resolveCoordinator.run { [weak self] in
            await self?.performResolve()
        }
    }

    private func performResolve() async {
        #if DEBUG
        // ── Manual overrides ────────────────────────────────────────
        if ProcessInfo.processInfo.environment["USE_LOCAL"] == "1" {
            resolvedBaseURL = localURL
            isLocal = true
            isManualOverride = true
            print("🟡 [ServerEnv] USE_LOCAL override — using localhost:8000")
            return
        }
        if ProcessInfo.processInfo.environment["USE_RAILWAY"] == "1" {
            resolvedBaseURL = railwayURL
            isLocal = false
            isManualOverride = true
            print("🟡 [ServerEnv] USE_RAILWAY override — using Railway")
            return
        }

        // ── Auto-detect: probe localhost ────────────────────────────
        let wasLocal = isLocal
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = probeTimeout
        config.timeoutIntervalForResource = probeTimeout
        let session = URLSession(configuration: config)

        do {
            let healthURL = localURL.appendingPathComponent("health/live")
            let (_, response) = try await session.data(from: healthURL)
            if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                resolvedBaseURL = localURL
                isLocal = true
                if !wasLocal {
                    print("🟢 [ServerEnv] Local backend detected — switching to localhost:8000")
                }
                return
            }
        } catch {
            // Timeout or connection refused — localhost not running
        }

        resolvedBaseURL = railwayURL
        isLocal = false
        if wasLocal {
            print("🔵 [ServerEnv] Local backend unavailable — switching to Railway")
        } else if resolvedBaseURL == nil {
            print("🔵 [ServerEnv] Using Railway backend")
        }
        #else
        // Production builds always use Railway — zero overhead.
        resolvedBaseURL = railwayURL
        isLocal = false
        #endif
    }

    // MARK: - Localhost Health Check (lightweight)

    /// Quick probe to check if localhost is still alive.
    /// Used by APIClient for failover decisions.
    func isLocalhostAvailable() async -> Bool {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = probeTimeout
        config.timeoutIntervalForResource = probeTimeout
        let session = URLSession(configuration: config)

        do {
            let healthURL = localURL.appendingPathComponent("health/live")
            let (_, response) = try await session.data(from: healthURL)
            if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                return true
            }
        } catch {}
        return false
    }
}
