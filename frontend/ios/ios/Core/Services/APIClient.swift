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

    /// Enable debug logging
    var isDebugLoggingEnabled: Bool = false

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

    // MARK: - Request Methods

    /// Make a request and decode the response
    func request<T: Decodable>(
        endpoint: APIEndpoint,
        responseType: T.Type,
        retryCount: Int = 2
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
            // Retry on server errors
            if retryCount > 0, case .serverError = error {
                try await Task.sleep(nanoseconds: 1_000_000_000) // 1 second
                return try await self.request(endpoint: endpoint, responseType: responseType, retryCount: retryCount - 1)
            }
            throw error
        } catch let error as DecodingError {
            throw APIError.decodingError(error)
        } catch {
            // Connection failed — try failover to the other server
            #if DEBUG
            if let failoverResult: T = try? await attemptFailover(endpoint: endpoint, originalError: error) {
                return failoverResult
            }
            #endif
            throw APIError.networkError(error)
        }
    }

    /// Make a request without expecting a response body
    func request(endpoint: APIEndpoint) async throws {
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
            throw apiError
        } catch {
            // Connection failed — try failover
            #if DEBUG
            do {
                try await attemptFailoverVoid(endpoint: endpoint, originalError: error)
                return
            } catch {}
            #endif
            throw APIError.networkError(error)
        }
    }

    /// Download raw bytes (e.g. a PDF) without JSON decoding. Reuses the same
    /// request building, validation, auth, and structured-error contract as
    /// `request` — on a non-2xx status `validateResponse` still decodes the
    /// backend's APIError body (e.g. REPORT_NOT_READY) into an `APIError`.
    func downloadData(endpoint: APIEndpoint, retryCount: Int = 1) async throws -> Data {
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
            if retryCount > 0, case .serverError = error {
                try await Task.sleep(nanoseconds: 1_000_000_000) // 1 second
                return try await downloadData(endpoint: endpoint, retryCount: retryCount - 1)
            }
            throw error
        } catch {
            throw APIError.networkError(error)
        }
    }

    // MARK: - Failover

    #if DEBUG
    /// When a connection error occurs (localhost died), switch to the other server and retry once.
    private func attemptFailover<T: Decodable>(
        endpoint: APIEndpoint,
        originalError: Error
    ) async throws -> T {
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

        // Auth token — always send when available (supports optional-auth endpoints)
        if let token = authToken {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }

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
            throw APIError.unauthorized

        case 403:
            // Check for specific error codes (Phase 3 contract)
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
            throw APIError.unknown(message: "HTTP \(response.statusCode)")
        }
    }

    // MARK: - Logging

    private func logRequest(_ request: URLRequest, endpoint: APIEndpoint) {
        guard isDebugLoggingEnabled else { return }

        print("🌐 [\(endpoint.method.rawValue)] \(request.url?.absoluteString ?? "nil")")
        if endpoint.requiresAuth {
            let hasToken = request.value(forHTTPHeaderField: "Authorization") != nil
            print("   🔑 Auth: \(hasToken ? "Bearer token attached" : "⚠️ NO TOKEN (endpoint requires auth)")")
        }
        if let body = request.httpBody,
           let bodyString = String(data: body, encoding: .utf8) {
            print("   📦 Body: \(bodyString.prefix(500))")
        }
    }

    private func logResponse(_ response: HTTPURLResponse, data: Data) {
        guard isDebugLoggingEnabled else { return }

        let emoji = (200...299).contains(response.statusCode) ? "✅" : "❌"
        print("\(emoji) Response \(response.statusCode) from \(response.url?.path ?? "")")

        if let bodyString = String(data: data, encoding: .utf8) {
            print("   📄 Body: \(bodyString.prefix(1000))")
        }

        if !(200...299).contains(response.statusCode) {
            print("   ⚠️ HTTP error \(response.statusCode) — check backend logs for details")
        }
    }
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

extension Bundle {
    nonisolated var appVersion: String {
        infoDictionary?["CFBundleShortVersionString"] as? String ?? "1.0"
    }
}
