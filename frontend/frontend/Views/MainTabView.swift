//
//  MainTabView.swift
//  frontend
//
//  Created by Hai Phan on 12/23/25.
//

import SwiftUI

struct MainTabView: View {
    @State private var selectedTab: Int = 0

    var body: some View {
        TabView(selection: $selectedTab) {
            // Home Tab
            HomeView()
                .tabItem {
                    Image("navigation_home")
                        .renderingMode(.template)
                    Text("Home")
                }
                .tag(0)

            // Breaking Tab
            BreakingView()
                .tabItem {
                    Image("navigation_breaking_news")
                        .renderingMode(.template)
                    Text("Breaking")
                }
                .tag(1)

            // Research Tab
            ResearchView()
                .tabItem {
                    Image("navigation_research")
                        .renderingMode(.template)
                    Text("Research")
                }
                .tag(2)

            // Holding Tab
            HoldingView()
                .tabItem {
                    Image("navigation_holding")
                        .renderingMode(.template)
                    Text("Holding")
                }
                .tag(3)

            // Wiser Tab
            WiserView()
                .tabItem {
                    Image("navigation_wiser")
                        .renderingMode(.template)
                    Text("Wiser")
                }
                .tag(4)
        }
        .tint(AppColors.tabBarSelected) // Active tab color: #4A90E2 (blue)
        .onAppear {
            configureTabBarAppearance()
        }
    }

    // MARK: - Tab Bar Styling
    private func configureTabBarAppearance() {
        let appearance = UITabBarAppearance()
        appearance.configureWithOpaqueBackground()
        appearance.backgroundColor = UIColor(AppColors.tabBarBackground)

        // Inactive (unselected) state
        appearance.stackedLayoutAppearance.normal.iconColor = UIColor(AppColors.tabBarUnselected)
        appearance.stackedLayoutAppearance.normal.titleTextAttributes = [
            .foregroundColor: UIColor(AppColors.tabBarUnselected)
        ]

        // Active (selected) state
        appearance.stackedLayoutAppearance.selected.iconColor = UIColor(AppColors.tabBarSelected)
        appearance.stackedLayoutAppearance.selected.titleTextAttributes = [
            .foregroundColor: UIColor(AppColors.tabBarSelected)
        ]

        UITabBar.appearance().standardAppearance = appearance
        UITabBar.appearance().scrollEdgeAppearance = appearance
    }
}

#Preview {
    MainTabView()
}
