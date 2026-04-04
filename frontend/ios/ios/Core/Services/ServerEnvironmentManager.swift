//
//  ServerEnvironmentManager.swift
//  ios
//
//  Auto-detects whether a local backend is running and resolves
//  the base URL accordingly. In DEBUG builds, probes localhost:8000/health
//  with a 0.5s timeout — if it responds, all API traffic routes locally.
//  Otherwise, falls back to the Railway production server.
//
//  Usage:
//    await ServerEnvironmentManager.shared.resolve()   // call once at app launch
//    APIConfig.baseURL  // automatically returns the resolved URL
//
//  Manual overrides (Xcode scheme → Environment Variables):
//    USE_LOCAL=1    → always use localhost (skip probe)
//    USE_RAILWAY=1  → always use Railway (skip probe)
//

import Foundation

final class ServerEnvironmentManager: @unchecked Sendable {

    // MARK: - Singleton

    static let shared = ServerEnvironmentManager()

    // MARK: - State

    /// The resolved backend URL. `nil` until `resolve()` completes.
    private(set) var resolvedBaseURL: URL?

    /// Whether the resolved URL points to localhost.
    private(set) var isLocal: Bool = false

    // MARK: - Constants

    private let localURL = URL(string: "http://127.0.0.1:8000")!
    private let railwayURL = URL(string: "https://ai-value-investor-app-production.up.railway.app")!

    /// Timeout for the localhost health probe (seconds).
    private let probeTimeout: TimeInterval = 0.5

    // MARK: - Init

    private init() {}

    // MARK: - Resolution

    /// Probes the local backend and sets `resolvedBaseURL`.
    /// Must be called **before** `APIClient.shared` is first accessed
    /// so the singleton picks up the correct URL.
    func resolve() async {
        #if DEBUG
        // ── Manual overrides ────────────────────────────────────────
        if ProcessInfo.processInfo.environment["USE_LOCAL"] == "1" {
            resolvedBaseURL = localURL
            isLocal = true
            print("🟡 [ServerEnv] USE_LOCAL override — using localhost:8000")
            return
        }
        if ProcessInfo.processInfo.environment["USE_RAILWAY"] == "1" {
            resolvedBaseURL = railwayURL
            isLocal = false
            print("🟡 [ServerEnv] USE_RAILWAY override — using Railway")
            return
        }

        // ── Auto-detect: probe localhost ────────────────────────────
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
                print("🟢 [ServerEnv] Local backend detected — using localhost:8000")
                return
            }
        } catch {
            // Timeout or connection refused — expected when local isn't running
        }

        resolvedBaseURL = railwayURL
        isLocal = false
        print("🔵 [ServerEnv] Using Railway backend")
        #else
        // Production builds always use Railway — zero overhead.
        resolvedBaseURL = railwayURL
        isLocal = false
        #endif
    }
}
