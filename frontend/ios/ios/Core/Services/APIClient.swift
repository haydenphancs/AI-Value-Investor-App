//
//  APIClient.swift
//  ios
//
//  Network Layer - Connects to Python FastAPI Backend
//
//  Features:
//  - Type-safe endpoint definitions
//  - Automatic JSON encoding/decoding
//  - Auth token injection
//  - Retry with exponential backoff
//  - Request/response logging (debug mode)
//  - Dynamic server switching: localhost ↔ Railway with auto-failover
//

import Foundation

// MARK: - API Client

/// Main networking client for the application.
/// Handles all HTTP communication with the FastAPI backend.
actor APIClient {

    // MARK: - Configuration

    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder
    private var authToken: String?

    /// Refreshes the auth token on a 401. Wired from the app root to
    /// `AuthService.refreshToken()`. Returns the new access token, or nil when the
    /// refresh fails (the caller then surfaces `.unauthorized`).
    private var tokenRefresher: (@Sendable () async -> TokenRefreshOutcome)?

    /// Single-flight guard: a burst of concurrent 401s triggers ONE refresh, not a
    /// storm. Followers await the in-flight refresh instead of starting their own.
    private var refreshInFlight: Task<TokenRefreshOutcome, Never>?

    /// Enable debug logging. `private(set)`: it gates the body dumps below, so a caller
    /// flipping it on in a Release build would start printing bearer tokens and signed URLs.
    private(set) var isDebugLoggingEnabled: Bool = false

    // MARK: - Singleton

    nonisolated static let shared = APIClient()

    // MARK: - Dynamic Base URL

    /// Returns the current base URL from ServerEnvironmentManager.
    /// This is read on every request so server switches take effect immediately.
    private var currentBaseURL: URL {
        ServerEnvironmentManager.shared.resolvedBaseURL ?? APIConfig.baseURL
    }

    // MARK: - Initialization

    init(session: URLSession = .shared) {
        self.session = session

        // Configure decoder
        // NOTE: Do NOT use .convertFromSnakeCase here — all DTOs define explicit
        // CodingKeys with snake_case raw values. Combining both causes a
        // double-conversion bug where JSON "company_name" → "companyName" but
        // the CodingKey expects "company_name", resulting in key-not-found errors.
        self.decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601

        // Configure encoder
        self.encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601

        #if DEBUG
        self.isDebugLoggingEnabled = true
        #endif
    }

    // MARK: - Auth Token

    func setAuthToken(_ token: String?) {
        self.authToken = token
    }

    /// Install the token-refresh hook (called once from the app root after auth is
    /// configured). Enables the 401 → refresh → retry-once behavior below.
    func setTokenRefresher(_ refresher: @escaping @Sendable () async -> TokenRefreshOutcome) {
        self.tokenRefresher = refresher
    }

    /// Run the refresher at most once concurrently. Concurrent 401s share the same
    /// in-flight refresh; on success `authToken` is updated for all subsequent calls.
    private func refreshTokenSingleFlight() async -> TokenRefreshOutcome {
        if let inFlight = refreshInFlight {
            return await inFlight.value
        }
        guard let refresher = tokenRefresher else { return .credentialRejected }
        // The new token is applied INSIDE the task, not after `await task.value`.
        //
        // Both the leader and every follower await the same task, and when it finishes
        // their continuations are resumed in an unspecified order. With the assignment
        // in the leader's frame, a follower could resume FIRST, see `.refreshed`, and
        // immediately retry with `allowAuthRetry: false` — using the OLD `authToken`,
        // because the leader had not reached its assignment yet. That request 401s and
        // has no retry left, so it fails hard while every other request succeeds.
        //
        // Reachable on any burst of concurrent 401s, which is the normal shape here: a
        // detail screen fires ~11 requests in parallel, so an access token expiring
        // mid-session produces exactly that burst. Assigning inside the task body makes
        // the write happen-before any awaiter resumes. `Task {}` created in an
        // actor-isolated method inherits this actor, so the write stays serialised.
        let task = Task { () -> TokenRefreshOutcome in
            let outcome = await refresher()
            if case .refreshed(let newToken) = outcome { self.authToken = newToken }
            return outcome
        }
        refreshInFlight = task
        let outcome = await task.value
        refreshInFlight = nil
        return outcome
    }

    /// Called when a request failed for an auth reason the transports could not recover from.
    /// Wired to `AppState` at the app root; lets the client clear a dead credential, drop to
    /// signed-out, and kick off a session-restore attempt.
    private var authFailureHandler: (@Sendable (AuthFailure) async -> Void)?

    func setAuthFailureHandler(_ handler: @escaping @Sendable (AuthFailure) async -> Void) {
        self.authFailureHandler = handler
    }

    /// The current access token, for the one consumer that genuinely cannot go through
    /// `request`: the live-price WebSocket, which passes it as a `?token=` query parameter
    /// because `URLSessionWebSocketTask` can't set headers from iOS.
    ///
    /// Exists so those call sites stop reading the Keychain directly. Four ViewModels did, and
    /// the Keychain copy is deliberately NOT the same value — `restoreAuthState` disarms the
    /// client token on a transient failure while leaving the Keychain entry intact, so a
    /// Keychain reader authenticates as the real account while the whole UI says "guest". They
    /// also never observed a mid-session refresh.
    func currentAuthToken() -> String? { authToken }

    /// Whether the client currently holds a credential at all.
    nonisolated static let authTokenClearedNotification = Notification.Name("caydexAuthTokenCleared")

    /// Decide what to do about an auth failure, once, in one place.
    ///
    /// Returns true when the caller should retry the request WITHOUT a credential. That is the
    /// self-heal for a dead session on a guest-capable surface: rather than showing an error for
    /// a screen that works fine signed-out, drop the dead token and render guest content.
    private func handleUnrecoverableAuthFailure(
        _ error: APIError, endpoint: APIEndpoint
    ) async -> Bool {
        if error.isSignInRequired {
            // No credential was sent. If we nonetheless hold a stored one, the client is running
            // tokenless when it shouldn't be — that is the self-heal trigger, not a sign-out.
            await authFailureHandler?(.signInRequired)
            return false
        }

        // The refresh already ran and failed: the credential is genuinely dead.
        await authFailureHandler?(.credentialRejected)
        authToken = nil

        return endpoint.authPolicy.isUsableWithoutCredential
    }

    // MARK: - Request Methods

    /// Make a request and decode the response
    func request<T: Decodable>(
        endpoint: APIEndpoint,
        responseType: T.Type,
        retryCount: Int = 2,
        allowAuthRetry: Bool = true
    ) async throws -> T {
        let request = try buildRequest(for: endpoint)

        logRequest(request, endpoint: endpoint)

        do {
            let (data, response) = try await session.data(for: request)

            guard let httpResponse = response as? HTTPURLResponse else {
                throw APIError.unknown(message: "Invalid response type")
            }

            logResponse(httpResponse, data: data)

            try validateResponse(httpResponse, data: data)

            return try decoder.decode(T.self, from: data)

        } catch let error as APIError {
            // 401 → single-flight token refresh → retry ONCE. Skipped for the auth
            // endpoints themselves (a failed login must surface, not loop) and when
            // this call is already the post-refresh retry.
            //
            // The predicate is `triggersTokenRefresh`, NOT `if case .unauthorized`. A 401 now
            // decodes into `.authError(code:)`, so the old pattern match would have stopped
            // firing the moment that landed — killing token refresh app-wide, silently, with
            // every session dying at the 24-hour mark and no test to catch it.
            if error.triggersTokenRefresh,
               allowAuthRetry,
               tokenRefresher != nil,
               !endpoint.isAuthEndpoint {
                let outcome = await refreshTokenSingleFlight()
                if case .transientFailure = outcome {
                    // The refresh could not be COMPLETED — rate limited, 5xx, offline. That
                    // says nothing about the credential, so keep it and surface the original
                    // error. Treating this as a dead session is how a shared NAT tripping the
                    // per-IP refresh limiter signed a user out with a perfectly good refresh
                    // token. `AppState.performRestore` already got this right; this path did not.
                    throw error
                }
                if case .refreshed = outcome {
                    return try await self.request(
                        endpoint: endpoint, responseType: responseType,
                        retryCount: retryCount, allowAuthRetry: false
                    )
                }
                // Refresh failed: the credential is dead. On a guest-capable endpoint, retry
                // tokenless so the screen renders guest content instead of an error.
                if await handleUnrecoverableAuthFailure(error, endpoint: endpoint) {
                    return try await self.request(
                        endpoint: endpoint, responseType: responseType,
                        retryCount: retryCount, allowAuthRetry: false
                    )
                }
            } else if error.isSignInRequired, allowAuthRetry {
                _ = await handleUnrecoverableAuthFailure(error, endpoint: endpoint)
            }
            // Retry on server errors — GET ONLY.
            //
            // `endpoint.method.isSafeToRetryAfterServerError` is load-bearing: a 5xx does
            // not tell us whether the origin committed, so re-sending a write repeats its
            // side effects. This used to be unconditional, which meant a dropped response
            // on `POST /research/generate` re-precharged 20 credits per retry — up to 60
            // for one tap. See the doc comment on `HTTPMethod`.
            if retryCount > 0,
               case .serverError = error,
               endpoint.method.isSafeToRetryAfterServerError {
                try await Task.sleep(nanoseconds: 1_000_000_000) // 1 second
                return try await self.request(endpoint: endpoint, responseType: responseType, retryCount: retryCount - 1, allowAuthRetry: allowAuthRetry)
            }
            throw error
        } catch let error as DecodingError {
            throw APIError.decodingError(error)
        } catch {
            // Connection failed — try failover to the other server. NOT on a cancellation:
            // the caller went away, the server is fine, and `attemptFailover` would spend a
            // further 1s on a `health/live` probe before re-sending a request nobody wants.
            #if DEBUG
            if !Self.isCancellation(error),
               let failoverResult: T = try? await attemptFailover(endpoint: endpoint, originalError: error) {
                return failoverResult
            }
            #endif
            throw APIError.networkError(error)
        }
    }

    /// Make a request without expecting a response body
    func request(endpoint: APIEndpoint, allowAuthRetry: Bool = true) async throws {
        let request = try buildRequest(for: endpoint)

        logRequest(request, endpoint: endpoint)

        do {
            let (data, response) = try await session.data(for: request)

            guard let httpResponse = response as? HTTPURLResponse else {
                throw APIError.unknown(message: "Invalid response type")
            }

            logResponse(httpResponse, data: data)

            try validateResponse(httpResponse, data: data)

        } catch let apiError as APIError {
            // 401 → single-flight refresh → retry once (see request<T> for the full rationale,
            // including why this must ask `triggersTokenRefresh` rather than match
            // `.unauthorized`).
            if apiError.triggersTokenRefresh,
               allowAuthRetry,
               tokenRefresher != nil,
               !endpoint.isAuthEndpoint {
                let outcome = await refreshTokenSingleFlight()
                if case .transientFailure = outcome {
                    // The refresh could not be COMPLETED — rate limited, 5xx, offline. That
                    // says nothing about the credential, so keep it and surface the original
                    // error. Treating this as a dead session is how a shared NAT tripping the
                    // per-IP refresh limiter signed a user out with a perfectly good refresh
                    // token. `AppState.performRestore` already got this right; this path did not.
                    throw apiError
                }
                if case .refreshed = outcome {
                    return try await self.request(endpoint: endpoint, allowAuthRetry: false)
                }
                if await handleUnrecoverableAuthFailure(apiError, endpoint: endpoint) {
                    return try await self.request(endpoint: endpoint, allowAuthRetry: false)
                }
            } else if apiError.isSignInRequired, allowAuthRetry {
                _ = await handleUnrecoverableAuthFailure(apiError, endpoint: endpoint)
            }
            throw apiError
        } catch {
            // Connection failed — try failover. Not on a cancellation; see `request` above.
            #if DEBUG
            if !Self.isCancellation(error) {
                do {
                    try await attemptFailoverVoid(endpoint: endpoint, originalError: error)
                    return
                } catch {}
            }
            #endif
            throw APIError.networkError(error)
        }
    }

    /// Download raw bytes (e.g. a PDF) without JSON decoding. Reuses the same
    /// request building, validation, auth, and structured-error contract as
    /// `request` — on a non-2xx status `validateResponse` still decodes the
    /// backend's APIError body (e.g. REPORT_NOT_READY) into an `APIError`.
    func downloadData(endpoint: APIEndpoint, retryCount: Int = 1, allowAuthRetry: Bool = true) async throws -> Data {
        let request = try buildRequest(for: endpoint)
        logRequest(request, endpoint: endpoint)

        do {
            let (data, response) = try await session.data(for: request)

            guard let httpResponse = response as? HTTPURLResponse else {
                throw APIError.unknown(message: "Invalid response type")
            }

            if isDebugLoggingEnabled {
                let emoji = (200...299).contains(httpResponse.statusCode) ? "✅" : "❌"
                print("\(emoji) Response \(httpResponse.statusCode) (\(data.count) bytes) from \(httpResponse.url?.path ?? "")")
            }

            try validateResponse(httpResponse, data: data)
            return data

        } catch let error as APIError {
            // 401 → single-flight refresh → retry once (same as request<T>), so an
            // authed download (e.g. report PDF) survives an expired access token.
            if error.triggersTokenRefresh,
               allowAuthRetry,
               tokenRefresher != nil,
               !endpoint.isAuthEndpoint {
                let outcome = await refreshTokenSingleFlight()
                if case .transientFailure = outcome {
                    // The refresh could not be COMPLETED — rate limited, 5xx, offline. That
                    // says nothing about the credential, so keep it and surface the original
                    // error. Treating this as a dead session is how a shared NAT tripping the
                    // per-IP refresh limiter signed a user out with a perfectly good refresh
                    // token. `AppState.performRestore` already got this right; this path did not.
                    throw error
                }
                if case .refreshed = outcome {
                    return try await downloadData(endpoint: endpoint, retryCount: retryCount, allowAuthRetry: false)
                }
                if await handleUnrecoverableAuthFailure(error, endpoint: endpoint) {
                    return try await downloadData(endpoint: endpoint, retryCount: retryCount, allowAuthRetry: false)
                }
            } else if error.isSignInRequired, allowAuthRetry {
                _ = await handleUnrecoverableAuthFailure(error, endpoint: endpoint)
            }
            // GET only — same rule as `request<T>` above (see `HTTPMethod`).
            if retryCount > 0,
               case .serverError = error,
               endpoint.method.isSafeToRetryAfterServerError {
                try await Task.sleep(nanoseconds: 1_000_000_000) // 1 second
                return try await downloadData(endpoint: endpoint, retryCount: retryCount - 1, allowAuthRetry: allowAuthRetry)
            }
            throw error
        } catch {
            throw APIError.networkError(error)
        }
    }

    // MARK: - Server-Sent Events (SSE) streaming

    /// Stream an SSE endpoint (e.g. chat `/messages/stream`) as an async sequence
    /// of `SSEEvent`. `nonisolated` so the returned stream is built synchronously;
    /// the actor hop (auth/headers/session) happens inside `openStream`, and SSE
    /// line-parsing runs off the actor. Throws on a non-2xx status or transport
    /// error — the caller (ChatViewModel) falls back to the non-streaming endpoint.
    nonisolated func stream(endpoint: APIEndpoint) -> AsyncThrowingStream<SSEEvent, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let bytes = try await self.openStream(endpoint: endpoint)
                    var eventName = ""
                    var dataLines: [String] = []

                    func flush() {
                        guard !dataLines.isEmpty || !eventName.isEmpty else { return }
                        continuation.yield(SSEEvent(
                            event: eventName.isEmpty ? "message" : eventName,
                            data: dataLines.joined(separator: "\n")
                        ))
                        eventName = ""
                        dataLines = []
                    }

                    for try await line in bytes.lines {
                        if line.isEmpty {           // blank line = end of one SSE frame
                            flush()
                        } else if line.hasPrefix(":") {
                            continue                // comment / heartbeat
                        } else if line.hasPrefix("event:") {
                            eventName = String(line.dropFirst(6)).trimmingCharacters(in: .whitespaces)
                        } else if line.hasPrefix("data:") {
                            let raw = line.dropFirst(5)
                            dataLines.append(raw.hasPrefix(" ") ? String(raw.dropFirst()) : String(raw))
                        }
                    }
                    flush()                          // frame without a trailing blank line
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Actor-isolated: build the authed request (Accept: text/event-stream) and
    /// open the byte stream, validating the status line before returning.
    /// Open the stream, and on an auth failure do exactly what the other three transports do:
    /// single-flight refresh, then reopen once.
    ///
    /// This path had NO refresh at all, so chat streaming hard-failed on an expired token while
    /// every other request in the app silently recovered — the same session working everywhere
    /// except the one screen the user was typing into.
    private func openStream(endpoint: APIEndpoint, allowAuthRetry: Bool = true) async throws -> URLSession.AsyncBytes {
        do {
            return try await openStreamOnce(endpoint: endpoint)
        } catch let error as APIError {
            if error.triggersTokenRefresh,
               allowAuthRetry,
               tokenRefresher != nil,
               !endpoint.isAuthEndpoint {
                let outcome = await refreshTokenSingleFlight()
                if case .transientFailure = outcome {
                    // The refresh could not be COMPLETED — rate limited, 5xx, offline. That
                    // says nothing about the credential, so keep it and surface the original
                    // error. Treating this as a dead session is how a shared NAT tripping the
                    // per-IP refresh limiter signed a user out with a perfectly good refresh
                    // token. `AppState.performRestore` already got this right; this path did not.
                    throw error
                }
                if case .refreshed = outcome {
                    return try await openStreamOnce(endpoint: endpoint)
                }
                if await handleUnrecoverableAuthFailure(error, endpoint: endpoint) {
                    return try await openStreamOnce(endpoint: endpoint)
                }
            } else if error.isSignInRequired, allowAuthRetry {
                _ = await handleUnrecoverableAuthFailure(error, endpoint: endpoint)
            }
            throw error
        }
    }

    private func openStreamOnce(endpoint: APIEndpoint) async throws -> URLSession.AsyncBytes {
        var request = try buildRequest(for: endpoint)
        request.setValue("text/event-stream", forHTTPHeaderField: "Accept")

        logRequest(request, endpoint: endpoint)
        let (bytes, response) = try await session.bytes(for: request)

        guard let http = response as? HTTPURLResponse else {
            throw APIError.unknown(message: "Invalid response type")
        }
        guard (200...299).contains(http.statusCode) else {
            if isDebugLoggingEnabled {
                print("❌ SSE stream \(http.statusCode) from \(http.url?.path ?? "")")
            }
            switch http.statusCode {
            case 401:
                // `session.bytes` yields no Data, so read a bounded prefix of the body to
                // recover the structured code. Capped so a misbehaving endpoint streaming an
                // error body can't grow unbounded here.
                var raw = Data()
                for try await byte in bytes {
                    raw.append(byte)
                    if raw.count >= 8192 { break }
                }
                if let errorResponse = try? decoder.decode(APIErrorResponse.self, from: raw) {
                    throw APIError.authError(
                        code: errorResponse.errorCode,
                        message: errorResponse.userMessage
                    )
                }
                throw APIError.unauthorized
            case 404: throw APIError.notFound
            case 429:
                let retryAfter = http.value(forHTTPHeaderField: "Retry-After").flatMap { Int($0) } ?? 60
                throw APIError.rateLimited(retryAfter: retryAfter)
            default: throw APIError.serverError(statusCode: http.statusCode)
            }
        }
        return bytes
    }

    // MARK: - Failover

    /// A cancelled task, in either of the two shapes it arrives in. Deliberately NOT
    /// `error is CancellationError`: `URLSession.data(for:)` reports cancellation as
    /// `URLError.cancelled` (-999), not as `CancellationError`.
    private static func isCancellation(_ error: Error) -> Bool {
        if error is CancellationError { return true }
        return (error as? URLError)?.code == .cancelled
    }

    #if DEBUG
    /// When a connection error occurs (localhost died), switch to the other server and retry once.
    private func attemptFailover<T: Decodable>(
        endpoint: APIEndpoint,
        originalError: Error
    ) async throws -> T {
        // A failover is a RE-SEND, so it is bound by the SAME money rule as the 5xx retry
        // path above: only GET. `POST /research/generate` precharges 20 credits, inserts a
        // row and spawns a worker before it returns, so re-issuing one whose response was
        // merely lost bills the user twice for one tap. This guard was missing entirely —
        // `attemptFailover` rebuilt and re-sent whatever it was handed, which made the
        // careful GET-only rule on the retry path bypassable through the DEBUG failover.
        guard endpoint.method.isSafeToRetryAfterServerError else { throw originalError }

        let env = ServerEnvironmentManager.shared

        // Don't failover if manual override is set
        guard !env.isManualOverride else { throw originalError }

        let failoverURL: URL
        if env.isLocal {
            // Localhost failed → try Railway
            failoverURL = env.railwayURL
            print("⚡ [APIClient] Localhost unreachable — failing over to Railway")
        } else {
            // Railway failed → try localhost (maybe user just started it)
            guard await env.isLocalhostAvailable() else { throw originalError }
            failoverURL = env.localURL
            print("⚡ [APIClient] Railway unreachable — failing over to localhost")
        }

        // Build request against the failover URL
        let request = try buildRequest(for: endpoint, baseURL: failoverURL)
        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.unknown(message: "Invalid response type")
        }

        logResponse(httpResponse, data: data)
        try validateResponse(httpResponse, data: data)

        let result = try decoder.decode(T.self, from: data)

        // Failover succeeded — update the resolved URL so future requests use it directly
        await env.resolve()
        return result
    }

    /// Void version of failover for requests without response body.
    private func attemptFailoverVoid(
        endpoint: APIEndpoint,
        originalError: Error
    ) async throws {
        // A failover is a RE-SEND, so it is bound by the SAME money rule as the 5xx retry
        // path above: only GET. `POST /research/generate` precharges 20 credits, inserts a
        // row and spawns a worker before it returns, so re-issuing one whose response was
        // merely lost bills the user twice for one tap. This guard was missing entirely —
        // `attemptFailover` rebuilt and re-sent whatever it was handed, which made the
        // careful GET-only rule on the retry path bypassable through the DEBUG failover.
        guard endpoint.method.isSafeToRetryAfterServerError else { throw originalError }

        let env = ServerEnvironmentManager.shared
        guard !env.isManualOverride else { throw originalError }

        let failoverURL: URL
        if env.isLocal {
            failoverURL = env.railwayURL
            print("⚡ [APIClient] Localhost unreachable — failing over to Railway")
        } else {
            guard await env.isLocalhostAvailable() else { throw originalError }
            failoverURL = env.localURL
            print("⚡ [APIClient] Railway unreachable — failing over to localhost")
        }

        let request = try buildRequest(for: endpoint, baseURL: failoverURL)
        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.unknown(message: "Invalid response type")
        }

        logResponse(httpResponse, data: data)
        try validateResponse(httpResponse, data: data)

        await env.resolve()
    }
    #endif

    // MARK: - Request Building

    private func buildRequest(for endpoint: APIEndpoint, baseURL: URL? = nil) throws -> URLRequest {
        let base = baseURL ?? currentBaseURL
        var components = URLComponents(url: base, resolvingAgainstBaseURL: true)!
        components.path = endpoint.path

        // Add query parameters
        if let queryParams = endpoint.queryParameters, !queryParams.isEmpty {
            components.queryItems = queryParams.map { URLQueryItem(name: $0.key, value: $0.value) }
        }

        guard let url = components.url else {
            throw APIError.unknown(message: "Invalid URL")
        }

        var request = URLRequest(url: url)
        request.httpMethod = endpoint.method.rawValue
        request.timeoutInterval = endpoint.timeout

        // Headers
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("iOS", forHTTPHeaderField: "X-Platform")
        request.setValue(Bundle.main.appVersion, forHTTPHeaderField: "X-App-Version")

        // Pre-flight auth gate.
        //
        // `authPolicy` existed for a long time (as `requiresAuth`) and was consulted by exactly
        // one debug `print`. So a tokenless call to a sign-in-only route went out anyway, came
        // back refused, and — at a call site that only logged — vanished. That is the reported
        // bug in one line: tapping Follow while signed out spent a round trip to be told 403 and
        // then silently reverted the button.
        //
        // Refusing here instead means the UI can offer sign-in immediately, and it cannot be
        // bypassed: `buildRequest` is the single funnel behind `request<T>`, `request`,
        // `downloadData` AND `openStream`.
        //
        // `.guestAllowed` and `.public` are deliberately untouched — the backend resolves a
        // signed-out caller to a per-install guest for those, and gating them on a token would
        // delete working features for every guest.
        if endpoint.authPolicy == .signInRequired, authToken == nil {
            throw APIError.authRequired
        }

        // Auth token — always send when available (supports optional-auth endpoints)
        if let token = authToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

        // Per-install identity. Until real login ships, every install otherwise
        // authenticates as one shared guest on the backend — which made the Learn
        // stores union one user's completed lessons and bookmarks into every
        // other user's app. The backend hashes this into a UUID5 and prefers a
        // valid Bearer token when one exists, so it is inert once auth lands.
        request.setValue(GuestIdentity.current, forHTTPHeaderField: "X-Guest-Id")

        // Body
        if let body = endpoint.body {
            request.httpBody = try encoder.encode(body)
        }

        return request
    }

    // MARK: - Response Validation

    private func validateResponse(_ response: HTTPURLResponse, data: Data) throws {
        switch response.statusCode {
        case 200...299:
            return // Success

        case 401:
            // The body used to be thrown away here, which is why an auth failure could never say
            // anything more specific than "Session Expired" — the client had no way to tell "you
            // were never signed in" from "your token died" from "your password changed
            // elsewhere". The backend now sends a structured `{error_code, ...}` on every 401
            // (`auth_error` in app/api/error_response.py), so decode it.
            if let errorResponse = try? decoder.decode(APIErrorResponse.self, from: data) {
                throw APIError.authError(
                    code: errorResponse.errorCode,
                    message: errorResponse.userMessage
                )
            }
            // Legacy / non-contract 401 (an older backend, or a proxy's own response).
            throw APIError.unauthorized

        case 403:
            // 403 no longer means "no credential" — that is a 401 now. What remains is genuine
            // authorization failure, which `mapAPIError` gives a typed case rather than letting
            // it look like a session problem: AUTH_FORBIDDEN → `.forbidden`, and
            // EMAIL_NOT_CONFIRMED → `.emailNotConfirmed`. (This comment used to claim BOTH went
            // to `.forbidden`; EMAIL_NOT_CONFIRMED had no branch at all and fell through to the
            // generic `.apiError`, so it rendered as "Error" with a retry button.)
            if let errorResponse = try? decoder.decode(APIErrorResponse.self, from: data) {
                throw APIError.businessError(
                    code: errorResponse.errorCode,
                    message: errorResponse.userMessage
                )
            }
            throw APIError.forbidden

        case 404:
            // Phase 3: backend may return structured REPORT_NOT_FOUND /
            // TICKER_NOT_FOUND. Fall back to .notFound when the body
            // is a plain {"detail": "..."} (legacy endpoints).
            if let errorResponse = try? decoder.decode(APIErrorResponse.self, from: data) {
                throw APIError.businessError(
                    code: errorResponse.errorCode,
                    message: errorResponse.userMessage
                )
            }
            throw APIError.notFound

        case 422:
            // Validation error
            if let errorResponse = try? decoder.decode(APIErrorResponse.self, from: data) {
                throw APIError.businessError(
                    code: errorResponse.errorCode,
                    message: errorResponse.userMessage
                )
            }
            throw APIError.unknown(message: "Validation failed")

        case 429:
            let retryAfter = response.value(forHTTPHeaderField: "Retry-After")
                .flatMap { Int($0) } ?? 60
            throw APIError.rateLimited(retryAfter: retryAfter)

        case 500...599:
            // Phase 3: report-pipeline endpoints emit
            // {error_code, user_message, details, …} on 5xx so the UI can
            // route to a specific message (FMP_RATE_LIMITED,
            // GEMINI_QUOTA_EXCEEDED, DATA_INCOMPLETE, etc.) instead of
            // a generic "Server error". Fall back to .serverError when
            // the body is plain text or empty (legacy responses).
            if let errorResponse = try? decoder.decode(APIErrorResponse.self, from: data) {
                throw APIError.businessError(
                    code: errorResponse.errorCode,
                    message: errorResponse.userMessage
                )
            }
            throw APIError.serverError(statusCode: response.statusCode)

        default:
            // 400 / 409 land here. Phase 3 backend may include a
            // structured body on these too (REPORT_NOT_READY, etc.).
            if let errorResponse = try? decoder.decode(APIErrorResponse.self, from: data) {
                throw APIError.businessError(
                    code: errorResponse.errorCode,
                    message: errorResponse.userMessage
                )
            }
            // FALL BACK TO FastAPI's `{"detail": "..."}` BEFORE giving up.
            //
            // ~100 backend raise sites pass a plain string detail rather than the structured
            // `APIErrorResponse` shape (`main.py`'s HTTPException handler passes a dict
            // through verbatim and renders anything else as `{"detail": ...}`), and this arm
            // used to discard that message entirely and surface the literal text
            // "HTTP 400" to the user. The live path is the account-recovery flow:
            // `auth.py` raises `HTTPException(400, "That code is invalid or has expired.
            // Request a new one.")`, and `ForgotPasswordView` displayed **HTTP 400**.
            // `.unknown` already renders its message verbatim (AppError.swift:148); the bug
            // was only ever that we passed "HTTP 400" instead of the message the backend
            // actually wrote for the user.
            throw APIError.unknown(
                message: Self.detailMessage(from: data) ?? "HTTP \(response.statusCode)"
            )
        }
    }

    // MARK: - Logging

    /// Extract FastAPI's plain `{"detail": "..."}` message.
    ///
    /// The backend has TWO error shapes and both are deliberate: the structured
    /// `{error_code, user_message, ...}` contract, and — at roughly a hundred raise sites —
    /// a bare string detail that `main.py`'s handler renders as `{"detail": "..."}`. Only the
    /// first was ever decoded, so every one of those hundred sites reached the user as the
    /// literal text "HTTP 400".
    ///
    /// Returns nil when `detail` is absent or is NOT a string — FastAPI's own 422 validation
    /// errors put an ARRAY there, and showing a user a serialised array of field errors is
    /// worse than the generic fallback.
    static func detailMessage(from data: Data) -> String? {
        guard
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let detail = object["detail"] as? String
        else { return nil }
        let trimmed = detail.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    /// Keys whose VALUE must never be printed, even in a debug build.
    ///
    /// These are not hypothetical. A launch log routinely contained a Supabase Storage signed
    /// URL complete with its `token=` query parameter (`/learn/books/audio`, `/learn/money-moves`)
    /// and the account's email address (`/users/me`); a sign-in would have printed the
    /// password in clear, since `SignInRequest` is `httpBody` like any other. Console output
    /// gets pasted into bug reports and chat windows.
    private static let redactedKeys = [
        "password", "current_password", "new_password",
        "token", "access_token", "refresh_token", "id_token", "identity_token",
        "authorization", "api_key", "secret",
        "email", "audio_url", "avatar_url", "url",
        // Not a secret, but a ~120 KB base64 blob in the console buries every other logged
        // request — and the avatar upload is the only route that carries one.
        "image_base64",
    ]

    /// Replace the value of any sensitive key with `***`, and defang a signed URL wherever it
    /// appears. Deliberately a regex over the raw string rather than a JSON re-encode: the body
    /// may not be JSON, and a logger must never be able to throw or mutate what it reports.
    private static func redact(_ raw: String) -> String {
        var out = raw
        for key in redactedKeys {
            out = out.replacingOccurrences(
                of: "\"\(key)\"\\s*:\\s*\"[^\"]*\"",
                with: "\"\(key)\":\"***\"",
                options: [.regularExpression, .caseInsensitive]
            )
        }
        // Query-string credentials (`?token=…`, `&signature=…`) survive the JSON pass above
        // when they are embedded inside a URL value that itself was not a redacted key.
        out = out.replacingOccurrences(
            of: "([?&](?:token|signature|sig|key|apikey)=)[^&\"\\s]+",
            with: "$1***",
            options: [.regularExpression, .caseInsensitive]
        )
        return out
    }

    private func logRequest(_ request: URLRequest, endpoint: APIEndpoint) {
        guard isDebugLoggingEnabled else { return }

        #if DEBUG
        print("🌐 [\(endpoint.method.rawValue)] \(Self.redact(request.url?.absoluteString ?? "nil"))")
        if endpoint.requiresAuth {
            let hasToken = request.value(forHTTPHeaderField: "Authorization") != nil
            print("   🔑 Auth: \(hasToken ? "Bearer token attached" : "⚠️ NO TOKEN (endpoint requires auth)")")
        }
        if let body = request.httpBody,
           let bodyString = String(data: body, encoding: .utf8) {
            print("   📦 Body: \(Self.redact(bodyString).prefix(500))")
        }
        #endif
    }

    private func logResponse(_ response: HTTPURLResponse, data: Data) {
        guard isDebugLoggingEnabled else { return }

        let emoji = (200...299).contains(response.statusCode) ? "✅" : "❌"
        print("\(emoji) Response \(response.statusCode) from \(response.url?.path ?? "")")

        // Body dumps are `#if DEBUG` in addition to the runtime flag. Two independent gates,
        // because the flag is a stored property on a shared actor: compiling the dump out
        // means no build can print a token even if the flag is somehow set.
        #if DEBUG
        if let bodyString = String(data: data, encoding: .utf8) {
            print("   📄 Body: \(Self.redact(bodyString).prefix(1000))")
        }
        #endif

        if !(200...299).contains(response.statusCode) {
            print("   ⚠️ HTTP error \(response.statusCode) — check backend logs for details")
        }
    }
}

// MARK: - Auth Failure

/// Why a request could not be authenticated, after `APIClient` exhausted its own recovery.
///
/// Two cases, because the correct response to each is opposite and conflating them is what made
/// auth feel unreliable: one means "restore the session you have", the other means "the session
/// you have is gone".
enum AuthFailure: Sendable, Equatable {
    /// The request went out with no credential (or was refused before being made) on a route
    /// that needs one. If a stored credential exists, the client is running tokenless and should
    /// try to restore — it must NOT sign anybody out.
    case signInRequired

    /// A credential was presented, rejected, and the refresh failed. This one is terminal:
    /// clear it.
    case credentialRejected
}

/// What happened when the client tried to refresh the access token.
///
/// THREE states, not two. This was `String?`, where `nil` meant "dead" — so a refresh that
/// merely could not be COMPLETED cleared the Keychain, wiped watchlist and research state, and
/// discarded device-global Learn data, for a user whose refresh token was perfectly valid.
///
/// The concrete case: the per-IP `refresh:ip:` limiter (60/min) is shared by everyone behind one
/// NAT — a campus, an office, a carrier CGNAT pool. Trip it and every user behind that address
/// whose 24-hour access token happens to expire is signed out. `.claude/rules/auth.md` §3 allows
/// exactly three codes to destroy a credential, and a rate limit is not one of them; §5 says a
/// transient failure must keep the token. `AppState.performRestore` already got this right, so
/// the outcome depended purely on which path noticed the 401 first.
enum TokenRefreshOutcome: Sendable {
    /// A new access token. Retry the original request with it.
    case refreshed(String)
    /// Could not complete, for a reason that says nothing about the credential — rate limited,
    /// 5xx, offline. KEEP the token and surface the original error.
    case transientFailure
    /// The refresh token itself was rejected. The session is genuinely over.
    case credentialRejected
}

// MARK: - Server-Sent Event

/// One parsed SSE frame: an event name (e.g. "token", "done", "error") + its
/// raw JSON `data` payload, which the caller decodes per event type.
nonisolated struct SSEEvent: Sendable {
    let event: String
    let data: String
}

// MARK: - API Error Response (Backend Format)

/// Matches the backend's APIError schema
struct APIErrorResponse: Sendable {
    let errorCode: String
    let message: String
    let userMessage: String
    let action: String?
    let details: [String: AnyCodable]?

    enum CodingKeys: String, CodingKey {
        case errorCode = "error_code"
        case message
        case userMessage = "user_message"
        case action
        case details
    }
}

// Explicitly nonisolated Decodable conformance
extension APIErrorResponse: Decodable {
    nonisolated init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        self.errorCode = try container.decode(String.self, forKey: .errorCode)
        self.message = try container.decode(String.self, forKey: .message)
        self.userMessage = try container.decode(String.self, forKey: .userMessage)
        self.action = try container.decodeIfPresent(String.self, forKey: .action)
        self.details = try container.decodeIfPresent([String: AnyCodable].self, forKey: .details)
    }
}

/// Type-erased Codable for flexible JSON
/// @unchecked Sendable because it only stores immutable value types (String, Int, Double, Bool)
struct AnyCodable: Decodable, @unchecked Sendable {
    let value: Any

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let string = try? container.decode(String.self) {
            value = string
        } else if let int = try? container.decode(Int.self) {
            value = int
        } else if let double = try? container.decode(Double.self) {
            value = double
        } else if let bool = try? container.decode(Bool.self) {
            value = bool
        } else {
            value = ""
        }
    }
}

// MARK: - Bundle Extension
//
// `Bundle.appVersion` moved to `Core/Utilities/AppInfo.swift`, which is now the single home
// for build and device facts. It is still used here for the `X-App-Version` header; it just
// lives next to `buildNumber` and the device model instead of being split across two files.
