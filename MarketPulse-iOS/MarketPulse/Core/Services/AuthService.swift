import Foundation

final class AuthService: ObservableObject {
  @Published var isLoading = false
  @Published var error: String?

  private let client: ApiClient

  init(client: ApiClient) { self.client = client }

  struct TokenResponse: Decodable { let success: Bool }
  struct TokenRequest: Encodable { let supabase_token: String }

  @MainActor
  func signInWithSupabase(token: String) async {
    isLoading = true
    defer { isLoading = false }
    do {
      let _: TokenResponse = try await client.post("/api/v1/auth/token", body: TokenRequest(supabase_token: token))
    } catch {
      self.error = "Unable to sign in"
    }
  }
}
