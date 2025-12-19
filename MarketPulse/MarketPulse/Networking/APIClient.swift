import Foundation
import Combine

@MainActor
class APIClient: ObservableObject {
    static let shared = APIClient()

    @Published var accessToken: String?
    @Published var refreshToken: String?

    private let baseURL: String
    private let session: URLSession

    private let jsonDecoder: JSONDecoder = {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let dateString = try container.decode(String.self)

            let formatters = [
                ISO8601DateFormatter(),
                { () -> DateFormatter in
                    let formatter = DateFormatter()
                    formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSSSSSZ"
                    return formatter
                }(),
                { () -> DateFormatter in
                    let formatter = DateFormatter()
                    formatter.dateFormat = "yyyy-MM-dd'T'HH:mm:ssZ"
                    return formatter
                }()
            ]

            for formatter in formatters {
                if let isoFormatter = formatter as? ISO8601DateFormatter,
                   let date = isoFormatter.date(from: dateString) {
                    return date
                } else if let dateFormatter = formatter as? DateFormatter,
                          let date = dateFormatter.date(from: dateString) {
                    return date
                }
            }

            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Cannot decode date string \(dateString)")
        }
        return decoder
    }()

    private let jsonEncoder: JSONEncoder = {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }()

    init(baseURL: String? = nil) {
        self.baseURL = baseURL ?? Config.baseURL

        let configuration = URLSessionConfiguration.default
        configuration.timeoutIntervalForRequest = Config.requestTimeout
        configuration.timeoutIntervalForResource = Config.requestTimeout * 2
        self.session = URLSession(configuration: configuration)

        self.loadTokens()
    }

    // MARK: - Request Methods

    func request<T: Decodable>(_ endpoint: APIEndpoint, body: Encodable? = nil) async throws -> T {
        let request = try buildRequest(endpoint, body: body)

        do {
            let (data, response) = try await session.data(for: request)
            try validateResponse(response)

            return try jsonDecoder.decode(T.self, from: data)
        } catch let error as APIError {
            throw error
        } catch let error as DecodingError {
            throw APIError.decodingError(error)
        } catch {
            throw APIError.networkError(error)
        }
    }

    func requestWithoutResponse(_ endpoint: APIEndpoint, body: Encodable? = nil) async throws {
        let request = try buildRequest(endpoint, body: body)

        do {
            let (_, response) = try await session.data(for: request)
            try validateResponse(response)
        } catch let error as APIError {
            throw error
        } catch {
            throw APIError.networkError(error)
        }
    }

    // MARK: - Helper Methods

    private func buildRequest(_ endpoint: APIEndpoint, body: Encodable?) throws -> URLRequest {
        var urlComponents = URLComponents(string: baseURL + endpoint.path)
        urlComponents?.queryItems = endpoint.queryItems

        guard let url = urlComponents?.url else {
            throw APIError.invalidURL
        }

        var request = URLRequest(url: url)
        request.httpMethod = endpoint.method.rawValue
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        // Add authorization header
        if let accessToken = accessToken {
            request.setValue("Bearer \(accessToken)", forHTTPHeaderField: "Authorization")
        }

        // Add body if present
        if let body = body {
            request.httpBody = try jsonEncoder.encode(body)
        }

        return request
    }

    private func validateResponse(_ response: URLResponse) throws {
        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIError.invalidResponse
        }

        switch httpResponse.statusCode {
        case 200...299:
            return
        case 401:
            throw APIError.unauthorized
        case 403:
            throw APIError.forbidden
        case 404:
            throw APIError.notFound
        case 500...599:
            throw APIError.serverError(httpResponse.statusCode)
        default:
            throw APIError.unknown("HTTP \(httpResponse.statusCode)")
        }
    }

    // MARK: - Token Management

    func setTokens(access: String, refresh: String?) {
        self.accessToken = access
        self.refreshToken = refresh
        saveTokens()
    }

    func clearTokens() {
        self.accessToken = nil
        self.refreshToken = nil
        UserDefaults.standard.removeObject(forKey: "access_token")
        UserDefaults.standard.removeObject(forKey: "refresh_token")
    }

    private func saveTokens() {
        if let accessToken = accessToken {
            UserDefaults.standard.set(accessToken, forKey: "access_token")
        }
        if let refreshToken = refreshToken {
            UserDefaults.standard.set(refreshToken, forKey: "refresh_token")
        }
    }

    private func loadTokens() {
        self.accessToken = UserDefaults.standard.string(forKey: "access_token")
        self.refreshToken = UserDefaults.standard.string(forKey: "refresh_token")
    }

    var isAuthenticated: Bool {
        accessToken != nil
    }
}
