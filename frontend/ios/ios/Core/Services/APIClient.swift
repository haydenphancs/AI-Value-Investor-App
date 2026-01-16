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
//

import Foundation

// MARK: - API Client

/// Main networking client for the application.
/// Handles all HTTP communication with the FastAPI backend.
actor APIClient {

    // MARK: - Configuration

    private let baseURL: URL
    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder
    private var authToken: String?

    /// Enable debug logging
    var isDebugLoggingEnabled: Bool = false

    // MARK: - Singleton

    static let shared = APIClient()

    // MARK: - Initialization

    init(
        baseURL: URL = APIConfig.baseURL,
        session: URLSession = .shared
    ) {
        self.baseURL = baseURL
        self.session = session

        // Configure decoder for backend snake_case
        self.decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
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
                return try await request(endpoint: endpoint, responseType: responseType, retryCount: retryCount - 1)
            }
            throw error
        } catch is DecodingError {
            throw APIError.decodingError(error)
        } catch {
            throw APIError.networkError(error)
        }
    }

    /// Make a request without expecting a response body
    func request(endpoint: APIEndpoint) async throws {
        let request = try buildRequest(for: endpoint)

        logRequest(request, endpoint: endpoint)

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.unknown(message: "Invalid response type")
        }

        logResponse(httpResponse, data: data)

        try validateResponse(httpResponse, data: data)
    }

    // MARK: - Request Building

    private func buildRequest(for endpoint: APIEndpoint) throws -> URLRequest {
        var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: true)!
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

        // Auth token
        if endpoint.requiresAuth, let token = authToken {
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
            // Check for specific error codes
            if let errorResponse = try? decoder.decode(APIErrorResponse.self, from: data) {
                throw APIError.businessError(
                    code: errorResponse.errorCode,
                    message: errorResponse.userMessage
                )
            }
            throw APIError.forbidden

        case 404:
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
            throw APIError.serverError(statusCode: response.statusCode)

        default:
            throw APIError.unknown(message: "HTTP \(response.statusCode)")
        }
    }

    // MARK: - Logging

    private func logRequest(_ request: URLRequest, endpoint: APIEndpoint) {
        guard isDebugLoggingEnabled else { return }

        print("🌐 API Request: \(endpoint.method.rawValue) \(request.url?.absoluteString ?? "")")
        if let body = request.httpBody,
           let bodyString = String(data: body, encoding: .utf8) {
            print("   Body: \(bodyString.prefix(500))")
        }
    }

    private func logResponse(_ response: HTTPURLResponse, data: Data) {
        guard isDebugLoggingEnabled else { return }

        let emoji = (200...299).contains(response.statusCode) ? "✅" : "❌"
        print("\(emoji) API Response: \(response.statusCode)")

        if let bodyString = String(data: data, encoding: .utf8) {
            print("   Body: \(bodyString.prefix(500))")
        }
    }
}

// MARK: - API Error Response (Backend Format)

/// Matches the backend's APIError schema
struct APIErrorResponse: Decodable, Sendable {
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
