import SwiftUI

struct ContentView: View {
    @EnvironmentObject var authViewModel: AuthViewModel

    var body: some View {
        Group {
            if authViewModel.isAuthenticated {
                MainTabView()
            } else {
                LoginView()
            }
        }
    }
}

struct MainTabView: View {
    @State private var selectedTab = 0

    var body: some View {
        TabView(selection: $selectedTab) {
            DashboardView()
                .tabItem {
                    Label("Home", systemImage: "house.fill")
                }
                .tag(0)

            NewsFeedView()
                .tabItem {
                    Label("News", systemImage: "newspaper.fill")
                }
                .tag(1)

            StockSearchView()
                .tabItem {
                    Label("Search", systemImage: "magnifyingglass")
                }
                .tag(2)

            ResearchListView()
                .tabItem {
                    Label("Research", systemImage: "chart.bar.doc.horizontal.fill")
                }
                .tag(3)

            ProfileView()
                .tabItem {
                    Label("Profile", systemImage: "person.fill")
                }
                .tag(4)
        }
    }
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
            .environmentObject(AuthViewModel())
    }
}
