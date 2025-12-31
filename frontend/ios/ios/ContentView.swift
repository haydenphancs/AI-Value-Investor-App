//
//  ContentView.swift
//  ios
//
//  Created by Hai Phan on 12/30/25.
//

import SwiftUI

struct ContentView: View {
    @State private var selectedTab: HomeTab = .home

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            Group {
                switch selectedTab {
                case .home:
                    HomeViewWithBinding(selectedTab: $selectedTab)
                case .updates:
                    UpdatesView(selectedTab: $selectedTab)
                case .research:
                    PlaceholderView(title: "Research", selectedTab: $selectedTab)
                case .tracking:
                    PlaceholderView(title: "Tracking", selectedTab: $selectedTab)
                case .wiser:
                    PlaceholderView(title: "Wiser", selectedTab: $selectedTab)
                }
            }
        }
        .preferredColorScheme(.dark)
    }
}

// MARK: - HomeView with Binding Support
struct HomeViewWithBinding: View {
    @StateObject private var viewModel = HomeViewModel()
    @Binding var selectedTab: HomeTab
    @State private var searchText = ""

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                HomeHeader(
                    searchText: $searchText,
                    onProfileTapped: {},
                    onSearchSubmit: {}
                )

                ScrollView(showsIndicators: false) {
                    VStack(spacing: AppSpacing.xl) {
                        MarketTickersRow(tickers: viewModel.marketTickers)
                            .padding(.top, AppSpacing.sm)

                        if let insight = viewModel.marketInsight {
                            MarketInsightsCard(insight: insight) {
                                selectedTab = .updates
                            }
                            .padding(.horizontal, AppSpacing.lg)
                        }

                        DailyBriefingSection(
                            items: viewModel.dailyBriefings,
                            onItemTapped: { _ in }
                        )

                        RecentResearchSection(
                            reports: viewModel.recentResearch,
                            onSeeAllTapped: { selectedTab = .research },
                            onReportTapped: { _ in },
                            onAskOrReadTapped: { _ in }
                        )

                        NewAnalysisButton {
                            selectedTab = .research
                        }

                        Spacer()
                            .frame(height: 100)
                    }
                }
                .refreshable {
                    await viewModel.refresh()
                }

                CustomTabBar(selectedTab: $selectedTab)
            }

            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
    }
}

// MARK: - Placeholder View for Other Tabs
struct PlaceholderView: View {
    let title: String
    @Binding var selectedTab: HomeTab

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack {
                Spacer()

                VStack(spacing: AppSpacing.md) {
                    Image(systemName: "hammer.fill")
                        .font(.system(size: 48))
                        .foregroundColor(AppColors.textMuted)

                    Text(title)
                        .font(AppTypography.title)
                        .foregroundColor(AppColors.textPrimary)

                    Text("Coming Soon")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                CustomTabBar(selectedTab: $selectedTab)
            }
        }
    }
}

#Preview {
    ContentView()
}
