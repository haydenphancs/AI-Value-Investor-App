import SwiftUI

struct RootView: View {
  @EnvironmentObject private var session: Session

  var body: some View {
    Group {
      if session.isAuthenticated { MainTabView() } else { LoginView() }
    }
  }
}

struct MainTabView: View {
  var body: some View {
    TabView {
      DashboardView()
        .tabItem { Label("Home", systemImage: "house.fill") }
      NewsFeedView()
        .tabItem { Label("News", systemImage: "newspaper.fill") }
      WatchlistView()
        .tabItem { Label("Watchlist", systemImage: "star.fill") }
      ReportsListView()
        .tabItem { Label("Reports", systemImage: "doc.text.fill") }
      ProfileView()
        .tabItem { Label("Profile", systemImage: "person.fill") }
    }
  }
}
