//
//  LivePriceWebSocketManager.swift
//  ios
//
//  Manages a WebSocket connection to the Caydex backend for
//  real-time stock price streaming. Connects when market is active,
//  publishes price updates via @Published for SwiftUI reactivity.
//

import Foundation
import Combine
import SwiftUI

@MainActor
final class LivePriceWebSocketManager: ObservableObject {

    // MARK: - Published State

    @Published private(set) var livePrice: Double?
    @Published private(set) var livePriceChange: Double?
    @Published private(set) var livePriceChangePercent: Double?
    @Published private(set) var liveTimestamp: Int?
    @Published private(set) var liveVolume: Int?
    @Published private(set) var isConnected: Bool = false

    // MARK: - Private

    private var webSocketTask: URLSessionWebSocketTask?
    private let session: URLSession
    private var ticker: String = ""
    private var isIntentionalDisconnect = false
    private var reconnectAttempts = 0
    private let maxReconnectAttempts = 3
    private var reconnectTask: Task<Void, Never>?
    private var authToken: String = ""

    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        return d
    }()

    // MARK: - Init

    init() {
        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = true
        self.session = URLSession(configuration: config)
    }

    // MARK: - Public API

    /// Connect to the live price WebSocket for a given ticker.
    /// Auth token is optional — crypto symbols can connect without login.
    func connect(ticker: String, authToken: String? = nil) {
        // Tear down any existing socket + pending reconnect first. Without this a
        // second connect() (pull-to-refresh, ticker switch) leaks the prior
        // URLSessionWebSocketTask — it's never cancelled, so leaked sockets
        // accumulate against the backend's per-user connection cap.
        reconnectTask?.cancel()
        reconnectTask = nil
        // Latch BEFORE the close, not after. `closeConnection()` cancels the old task, whose
        // outstanding `receive` then completes with an error and hops back to the main actor —
        // by which point the old code had already set `isIntentionalDisconnect = false`, so that
        // dead task's failure handler scheduled a reconnect. One second later it opened a THIRD
        // socket over `webSocketTask`, orphaning the one this call just created: never cancelled,
        // never referenced again, and still counting against the backend's per-user cap.
        // `disconnect()` sets the flag first for exactly this reason; `connect()` did not.
        isIntentionalDisconnect = true
        closeConnection()

        self.ticker = ticker.uppercased()
        self.authToken = authToken ?? ""
        isIntentionalDisconnect = false
        reconnectAttempts = 0

        openConnection()
    }

    /// Disconnect from the WebSocket. Prevents auto-reconnect.
    func disconnect() {
        isIntentionalDisconnect = true
        reconnectTask?.cancel()
        reconnectTask = nil
        closeConnection()
    }

    // MARK: - Connection Lifecycle

    private func openConnection() {
        // Build the WebSocket URL from the existing API config
        // REST base: http://127.0.0.1:8000 → ws://127.0.0.1:8000/api/v1/ws/price/AAPL?token=...
        let baseURL = APIConfig.baseURL
        let wsScheme = baseURL.scheme == "https" ? "wss" : "ws"

        guard var components = URLComponents(
            url: baseURL.appendingPathComponent("api/v1/ws/price/\(ticker)"),
            resolvingAgainstBaseURL: false
        ) else { return }

        components.scheme = wsScheme
        if !authToken.isEmpty {
            components.queryItems = [URLQueryItem(name: "token", value: authToken)]
        }
        // NOTE: the token is captured once in `connect()` and reused here on every reconnect.
        // `refreshCurrentAuthToken()` below re-reads it from `APIClient` before each reconnect
        // so a mid-session refresh doesn't leave this socket authenticating with a dead token.

        guard let url = components.url else { return }

        let task = session.webSocketTask(with: url)
        task.resume()
        self.webSocketTask = task
        // isConnected deferred until first successful message to avoid UI flicker
        // on handshake rejection (4001 Unauthorized, network error, etc.)

        receiveMessage()
    }

    private func closeConnection() {
        webSocketTask?.cancel(with: .normalClosure, reason: nil)
        webSocketTask = nil
        isConnected = false
    }

    // MARK: - Receive Loop

    private func receiveMessage() {
        webSocketTask?.receive { [weak self] result in
            Task { @MainActor [weak self] in
                guard let self = self else { return }

                switch result {
                case .success(let message):
                    self.handleMessage(message)
                    // Continue listening
                    self.receiveMessage()

                case .failure:
                    self.isConnected = false
                    if !self.isIntentionalDisconnect {
                        self.scheduleReconnect()
                    }
                }
            }
        }
    }

    // MARK: - Message Handling

    private func handleMessage(_ message: URLSessionWebSocketTask.Message) {
        let data: Data
        switch message {
        case .string(let text):
            guard let d = text.data(using: .utf8) else { return }
            data = d
        case .data(let d):
            data = d
        @unknown default:
            return
        }

        guard let msg = try? decoder.decode(LivePriceMessage.self, from: data) else { return }

        switch msg.type {
        case "price_update":
            guard let price = msg.price else { return }

            if !isConnected {
                isConnected = true
                // A successful price_update confirms the (re)connection is live, so
                // reset the lifetime attempt counter — each future transient drop then
                // gets a fresh set of retries. Without this the counter accumulated
                // across the whole screen session and permanently gave up after 3
                // TOTAL drops even on a healthy network (the socket froze silently
                // until the user left and re-entered). Mirrors the backend reader,
                // which resets its own counter on a successful reconnect.
                reconnectAttempts = 0
            }

            withAnimation(.snappy(duration: 0.3)) {
                self.livePrice = price
                self.livePriceChange = msg.change
                self.livePriceChangePercent = msg.changePercent
                self.liveTimestamp = msg.timestamp
                self.liveVolume = msg.volume
            }

        case "market_closed":
            // Server says market is closed — disconnect gracefully
            self.isIntentionalDisconnect = true
            self.closeConnection()

        case "error":
            // Live feed unavailable — fall back to static REST price
            self.closeConnection()

        default:
            break
        }
    }

    // MARK: - Reconnect

    private func scheduleReconnect() {
        guard reconnectAttempts < maxReconnectAttempts else {
            return
        }

        let delay = pow(2.0, Double(reconnectAttempts)) // 1s, 2s, 4s
        reconnectAttempts += 1

        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard let self = self, !Task.isCancelled, !self.isIntentionalDisconnect else { return }
            await self.refreshCurrentAuthToken()
            self.openConnection()
        }
    }

    /// Re-read the access token from `APIClient` before reconnecting.
    ///
    /// The token was captured ONCE in `connect()` and reused for the life of the screen. If it
    /// was refreshed in the meantime — which it will be on any screen left open past the 24-hour
    /// access-token lifetime — every reconnect presented the dead one and was rejected, all
    /// three attempts burned, and then nothing. The price froze silently: `isConnected` had
    /// already gone true on the first tick, and `TickerDetailViewModel`'s REST poll returns
    /// PERMANENTLY once that happens, so there is no fallback left to catch it. No toast, no
    /// error, no analytics — just a stale quote until the user leaves and re-enters.
    ///
    /// `APIClient.currentAuthToken()` is the single source of truth (never the Keychain, which
    /// deliberately diverges during `.restoring`). An empty token means we reconnect as an
    /// anonymous caller, which is the correct behaviour for crypto and for a signed-out user.
    private func refreshCurrentAuthToken() async {
        guard !authToken.isEmpty else { return }  // was anonymous; stay anonymous
        if let current = await APIClient.shared.currentAuthToken(), !current.isEmpty {
            authToken = current
        }
    }
}
