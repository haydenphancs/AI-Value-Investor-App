import Foundation

struct APIConfig {
  var baseURL: URL
  var supabaseToken: String?

  static var `default`: APIConfig {
    .init(baseURL: URL(string: "https://api.your-backend.com")!, supabaseToken: nil)
  }
}

enum APIError: Error { case invalidResponse, decoding, networking(Error), unauthorized, notFound }

final class ApiClient {
  private let config: APIConfig
  private let urlSession: URLSession

  init(config: APIConfig = .default, session: URLSession = .shared) {
    self.config = config
    self.urlSession = session
  }

  func get<T: Decodable>(_ path: String, query: [URLQueryItem] = []) async throws -> T {
    var components = URLComponents(url: config.baseURL.appendingPathComponent(path), resolvingAgainstBaseURL: false)!
    components.queryItems = query.isEmpty ? nil : query
    var req = URLRequest(url: components.url!)
    req.httpMethod = "GET"
    if let token = config.supabaseToken { req.addValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
    let (data, resp) = try await urlSession.data(for: req)
    guard let http = resp as? HTTPURLResponse else { throw APIError.invalidResponse }
    switch http.statusCode {
    case 200...299:
      do { return try JSONDecoder.api.decode(T.self, from: data) } catch { throw APIError.decoding }
    case 401: throw APIError.unauthorized
    case 404: throw APIError.notFound
    default: throw APIError.invalidResponse
    }
  }

  func post<T: Decodable, B: Encodable>(_ path: String, body: B) async throws -> T {
    var req = URLRequest(url: config.baseURL.appendingPathComponent(path))
    req.httpMethod = "POST"
    req.addValue("application/json", forHTTPHeaderField: "Content-Type")
    if let token = config.supabaseToken { req.addValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
    req.httpBody = try JSONEncoder.api.encode(body)
    let (data, resp) = try await urlSession.data(for: req)
    guard let http = resp as? HTTPURLResponse else { throw APIError.invalidResponse }
    switch http.statusCode {
    case 200...299:
      do { return try JSONDecoder.api.decode(T.self, from: data) } catch { throw APIError.decoding }
    case 401: throw APIError.unauthorized
    case 404: throw APIError.notFound
    default: throw APIError.invalidResponse
    }
  }

  func delete(_ path: String) async throws {
    var req = URLRequest(url: config.baseURL.appendingPathComponent(path))
    req.httpMethod = "DELETE"
    if let token = config.supabaseToken { req.addValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
    _ = try await urlSession.data(for: req)
  }
}

extension JSONDecoder {
  static var api: JSONDecoder { let d = JSONDecoder(); d.dateDecodingStrategy = .iso8601; return d }
}
extension JSONEncoder {
  static var api: JSONEncoder { let e = JSONEncoder(); e.dateEncodingStrategy = .iso8601; return e }
}
