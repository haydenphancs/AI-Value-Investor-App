//
//  HomeView.swift
//  ios
//
//  Main Home screen combining all organisms
//

import SwiftUI

// MARK: - HomeContentView (Used in TabView)
struct HomeContentView: View {
    @StateObject private var viewModel = HomeViewModel()
    @State private var showingSearch = false

    var body: some View {
        NavigationStack {
            ZStack {
                // Background
                AppColors.background
                    .ignoresSafeArea()

                // Main Content
                VStack(spacing: 0) {
                    // Header
                    HomeHeader(
                        onProfileTapped: handleProfileTapped,
                        onSearchTapped: handleSearchTapped
                    )

                    // Scrollable Content with proper bounce behavior
                    ScrollView(showsIndicators: false) {
                        LazyVStack(spacing: AppSpacing.xl) {
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
                            .padding(.bottom, AppSpacing.lg)
                        }
                    }
                    .refreshable {
                        await viewModel.refresh()
                    }
                }

                // Loading overlay
                if viewModel.isLoading {
                    LoadingOverlay()
                }
            }
            .navigationBarHidden(true)
            .fullScreenCover(isPresented: $showingSearch) {
                SearchView()
            }
        }
    }

    // MARK: - Action Handlers
    private func handleProfileTapped() {
        print("Profile tapped")
    }

    private func handleSearchTapped() {
        showingSearch = true
    }

    private func handleSeeAllInsights() {
        print("See all insights tapped")
    }

    private func handleBriefingItemTapped(_ item: DailyBriefingItem) {
        print("Briefing item tapped: \(item.title)")
    }

    private func handleSeeAllResearch() {
        print("See all research tapped")
    }

    private func handleReportTapped(_ report: ResearchReport) {
        print("Report tapped: \(report.headline)")
    }

    private func handleAskOrRead(_ report: ResearchReport) {
        print("Ask or Read tapped for: \(report.stockTicker)")
    }

    private func handleNewAnalysis() {
        print("New analysis tapped")
    }
}

// MARK: - Legacy HomeView (for backward compatibility)
struct HomeView: View {
    var body: some View {
        MainTabView()
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
        .preferredColorScheme(.dark)
}
