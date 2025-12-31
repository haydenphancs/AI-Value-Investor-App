//
//  MainTabView.swift
//  ios
//
//  Main navigation using native SwiftUI TabView
//

import SwiftUI

struct MainTabView: View {
    @State private var selectedTab: HomeTab = .home

    init() {
        // Configure tab bar appearance
        let appearance = UITabBarAppearance()
        appearance.configureWithOpaqueBackground()
        appearance.backgroundColor = UIColor(AppColors.tabBarBackground)

        // Normal state
        appearance.stackedLayoutAppearance.normal.iconColor = UIColor(AppColors.tabBarUnselected)
        appearance.stackedLayoutAppearance.normal.titleTextAttributes = [
            .foregroundColor: UIColor(AppColors.tabBarUnselected)
        ]

        // Selected state
        appearance.stackedLayoutAppearance.selected.iconColor = UIColor(AppColors.tabBarSelected)
        appearance.stackedLayoutAppearance.selected.titleTextAttributes = [
            .foregroundColor: UIColor(AppColors.tabBarSelected)
        ]

        UITabBar.appearance().standardAppearance = appearance
        UITabBar.appearance().scrollEdgeAppearance = appearance
    }

    var body: some View {
        TabView(selection: $selectedTab) {
            HomeContentView()
                .tabItem {
                    Image(systemName: "house.fill")
                    Text("Home")
                }
                .tag(HomeTab.home)

            PlaceholderView(title: "Updates")
                .tabItem {
                    Image(systemName: "chart.bar.doc.horizontal")
                    Text("Updates")
                }
                .tag(HomeTab.updates)

            ResearchContentView()
                .tabItem {
                    Image(systemName: "magnifyingglass")
                    Text("Research")
                }
                .tag(HomeTab.research)

            TrackingContentView()
                .tabItem {
                    Image(systemName: "star.fill")
                    Text("Tracking")
                }
                .tag(HomeTab.tracking)

            PlaceholderView(title: "Wiser")
                .tabItem {
                    Image(systemName: "lightbulb.fill")
                    Text("Wiser")
                }
                .tag(HomeTab.wiser)
        }
        .tint(AppColors.tabBarSelected)
    }
}

// MARK: - Placeholder View for other tabs
struct PlaceholderView: View {
    let title: String

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            Text(title)
                .font(AppTypography.title)
                .foregroundColor(AppColors.textPrimary)
        }
    }
}

#Preview {
    MainTabView()
        .preferredColorScheme(.dark)
}
