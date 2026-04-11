//
//  ServerEnvironmentManager.swift
//  ios
//
//  Auto-detects whether a local backend is running and dynamically
//  routes API traffic to localhost or Railway.
//
//  Behavior:
//    - Probes localhost:8000/health on app launch and each foreground event
//    - If localhost responds, all traffic routes locally
//    - If localhost goes down mid-session, automatically falls back to Railway
//    - When localhost comes back, next re-probe picks it up
//
//  Manual overrides (Xcode scheme → Environment Variables):
//    USE_LOCAL=1    → always use localhost (skip probe)
//    USE_RAILWAY=1  → always use Railway (skip probe)
//

import Foundation

final class ServerEnvironmentManager: @unchecked Sendable {

    // MARK: - Singleton

    nonisolated(unsafe) static let shared = ServerEnvironmentManager()

    // MARK: - State (nonisolated for cross-actor access from APIClient)

    /// The resolved backend URL. `nil` until `resolve()` completes.
    nonisolated(unsafe) private(set) var resolvedBaseURL: URL?

    /// Whether the resolved URL points to localhost.
    nonisolated(unsafe) private(set) var isLocal: Bool = false

    /// Whether a manual override is active (skips probing).
    nonisolated(unsafe) private(set) var isManualOverride: Bool = false

    // MARK: - Constants

    nonisolated(unsafe) let localURL = URL(string: "http://127.0.0.1:8000")!
    nonisolated(unsafe) let railwayURL = URL(string: "https://ai-value-investor-app-production.up.railway.app")!

    /// Timeout for the localhost health probe (seconds).
    private let probeTimeout: TimeInterval = 0.5

    // MARK: - Init

    private init() {}

    // MARK: - Resolution

    /// Probes the local backend and sets `resolvedBaseURL`.
    /// Called at app launch and on each foreground event.
    func resolve() async {
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
            let healthURL = localURL.appendingPathComponent("health")
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
            let healthURL = localURL.appendingPathComponent("health")
            let (_, response) = try await session.data(from: healthURL)
            if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                return true
            }
        } catch {}
        return false
    }
}
