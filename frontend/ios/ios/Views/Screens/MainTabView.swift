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

            UpdatesViewForTabView(selectedTab: $selectedTab)
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

            LearnContentView()
                .tabItem {
                    Image(systemName: "lightbulb.fill")
                    Text("Wiser")
                }
                .tag(HomeTab.wiser)
        }
        .tint(AppColors.tabBarSelected)
    }
}

// MARK: - UpdatesView wrapper for TabView (hides custom tab bar)
struct UpdatesViewForTabView: View {
    @StateObject private var viewModel = UpdatesViewModel()
    @Binding var selectedTab: HomeTab
    @State private var showManageAssetsSheet = false
    @State private var selectedNewsArticle: NewsArticle?

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                UpdatesHeader(
                    onProfileTapped: {}
                )

                UpdatesTabBar(
                    tabs: viewModel.filterTabs,
                    selectedTab: $viewModel.selectedTab,
                    onManageAssets: { showManageAssetsSheet = true }
                )

                LiveNewsHeader(onFilterTapped: { viewModel.openFilterOptions() })

                ScrollView(showsIndicators: false) {
                    if let summary = viewModel.insightSummary {
                        InsightsSummaryCard(summary: summary)
                            .padding(.horizontal, AppSpacing.lg)
                            .padding(.vertical, AppSpacing.sm)
                    }

                    LiveNewsTimeline(
                        groupedNews: viewModel.groupedNews,
                        onArticleTapped: { article in
                            selectedNewsArticle = article
                        }
                    )

                    Spacer()
                        .frame(height: 100)
                }
                .refreshable {
                    await viewModel.refresh()
                }
            }

            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .sheet(isPresented: $viewModel.showFilterSheet) {
            NewsFilterSheet(
                filterOptions: $viewModel.filterOptions,
                onApply: { viewModel.showFilterSheet = false }
            )
        }
        .sheet(isPresented: $showManageAssetsSheet) {
            ManageAssetsSheet(
                tickers: viewModel.filterTabs.filter { !$0.isMarketTab },
                onDismiss: { showManageAssetsSheet = false }
            )
        }
        .fullScreenCover(item: $selectedNewsArticle) { article in
            NewsDetailView(article: article)
                .preferredColorScheme(.dark)
        }
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
