//
//  HomeView.swift
//  ios
//
//  Main Home screen combining all organisms
//

import SwiftUI

struct HomeView: View {
    @StateObject private var viewModel = HomeViewModel()
    @State private var searchText = ""

    var body: some View {
        ZStack(alignment: .bottom) {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main Content
            VStack(spacing: 0) {
                // Header
                HomeHeader(
                    searchText: $searchText,
                    onProfileTapped: handleProfileTapped,
                    onSearchSubmit: handleSearchSubmit
                )

                // Scrollable Content
                ScrollView(showsIndicators: false) {
                    VStack(spacing: AppSpacing.xl) {
                        // Market Tickers Row
                        MarketTickersRow(tickers: viewModel.marketTickers)
                            .padding(.top, AppSpacing.sm)

                        // Market Insights Card
                        if let insight = viewModel.marketInsight {
                            MarketInsightsCard(insight: insight) {
                                handleSeeAllInsights()
                            }
                            .padding(.horizontal, AppSpacing.lg)
                        }

                        // Daily Briefing Section
                        DailyBriefingSection(
                            items: viewModel.dailyBriefings,
                            onItemTapped: handleBriefingItemTapped
                        )

                        // Recent Research Section
                        RecentResearchSection(
                            reports: viewModel.recentResearch,
                            onSeeAllTapped: handleSeeAllResearch,
                            onReportTapped: handleReportTapped,
                            onAskOrReadTapped: handleAskOrRead
                        )

                        // New Analysis Button
                        NewAnalysisButton {
                            handleNewAnalysis()
                        }

                        // Bottom spacing for tab bar
                        Spacer()
                            .frame(height: 100)
                    }
                }
                .refreshable {
                    await viewModel.refresh()
                }

                // Tab Bar
                CustomTabBar(selectedTab: $viewModel.selectedTab)
            }

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
    }

    // MARK: - Action Handlers
    private func handleProfileTapped() {
        // Navigate to profile
        print("Profile tapped")
    }

    private func handleSearchSubmit() {
        // Handle search
        print("Search submitted: \(searchText)")
    }

    private func handleSeeAllInsights() {
        // Navigate to Updates tab
        viewModel.selectedTab = .updates
    }

    private func handleBriefingItemTapped(_ item: DailyBriefingItem) {
        // Navigate based on item type
        print("Briefing item tapped: \(item.title)")
    }

    private func handleSeeAllResearch() {
        // Navigate to Research tab
        viewModel.selectedTab = .research
    }

    private func handleReportTapped(_ report: ResearchReport) {
        // Navigate to report detail
        print("Report tapped: \(report.headline)")
    }

    private func handleAskOrRead(_ report: ResearchReport) {
        // Open ask/read modal
        print("Ask or Read tapped for: \(report.stockTicker)")
    }

    private func handleNewAnalysis() {
        // Navigate to new analysis flow
        viewModel.selectedTab = .research
    }
}

// MARK: - Loading Overlay
struct LoadingOverlay: View {
    var body: some View {
        ZStack {
            Color.black.opacity(0.3)
                .ignoresSafeArea()

            ProgressView()
                .progressViewStyle(CircularProgressViewStyle(tint: .white))
                .scaleEffect(1.5)
        }
    }
}

#Preview {
    HomeView()
}
