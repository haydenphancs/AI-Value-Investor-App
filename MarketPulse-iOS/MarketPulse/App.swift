import SwiftUI

@main
struct MarketPulseApp: App {
  @StateObject private var session = Session()

  var body: some Scene {
    WindowGroup {
      RootView()
        .environmentObject(session)
    }
  }
}

final class Session: ObservableObject {
  @Published var isAuthenticated: Bool = false
}
