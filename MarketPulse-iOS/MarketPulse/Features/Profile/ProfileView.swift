import SwiftUI

struct ProfileView: View {
  @State private var profile: UserProfile?
  @State private var usage: Usage?
  @State private var stats: UserStats?
  @State private var isLoading = true

  var body: some View {
    NavigationStack {
      Form {
        if let p = profile {
          Section("Account") {
            Text(p.email)
            Text(p.tier.rawValue.capitalized)
          }
        }
        if let u = usage {
          Section("Usage") {
            Text("Deep research used: \(u.deep_research.used)")
            Text("Resets: \(u.reset_at, style: .date)")
          }
        }
        if let s = stats {
          Section("Stats") {
            Text("Watchlist: \(s.watchlist_count)")
            Text("Reports: \(s.reports_generated)")
            Text("Chats: \(s.chat_sessions)")
          }
        }
        Section { Button("Sign Out") {}.foregroundColor(.red) }
      }
      .navigationTitle("Profile")
      .task { await load() }
    }
  }

  private func load() async { try? await Task.sleep(nanoseconds: 300_000_000); isLoading = false }
}
