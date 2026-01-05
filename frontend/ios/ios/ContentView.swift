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
                    ResearchViewWithBinding(selectedTab: $selectedTab)
                case .tracking:
                    TrackingViewWithBinding(selectedTab: $selectedTab)
                case .wiser:
                    WiserViewWithBinding(selectedTab: $selectedTab)
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
    @State private var showSearch = false
    @State private var selectedArticle: NewsArticle?
    @State private var showNewsDetail = false

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                HomeHeader(
                    onProfileTapped: {},
                    onSearchTapped: {
                        showSearch = true
                    }
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
                            onItemTapped: handleBriefingItemTapped
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
        .sheet(isPresented: $showSearch) {
            SearchView()
        }
        .fullScreenCover(isPresented: $showNewsDetail) {
            if let article = selectedArticle {
                NewsDetailView(article: article)
                    .preferredColorScheme(.dark)
            }
        }
    }
    
    // MARK: - Action Handlers
    private func handleBriefingItemTapped(_ item: DailyBriefingItem) {
        // Convert DailyBriefingItem to NewsArticle for navigation
        let article = NewsArticle(
            headline: item.title,
            summary: item.subtitle,
            source: NewsSource(name: "Market Alert", iconName: nil),
            sentiment: .neutral,
            publishedAt: item.date ?? Date(),
            thumbnailName: nil,
            relatedTickers: []
        )
        selectedArticle = article
        showNewsDetail = true
    }
}

// MARK: - ResearchView with Binding Support
struct ResearchViewWithBinding: View {
    @StateObject private var viewModel = ResearchViewModel()
    @Binding var selectedTab: HomeTab

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Main Content
                if viewModel.selectedTab == .research {
                    researchTabContent
                } else {
                    reportsTabContent
                }

                CustomTabBar(selectedTab: $selectedTab)
            }

            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
    }

    // MARK: - Research Tab Content
    private var researchTabContent: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xxl) {
                // Header
                ResearchHeader(
                    selectedTab: $viewModel.selectedTab,
                    onProfileTapped: handleProfileTapped
                )

                // Target Selection Section
                TargetSelectionSection(
                    searchText: $viewModel.searchText,
                    quickTickers: viewModel.quickTickers,
                    onTickerSelected: handleTickerSelected,
                    onSearchSubmit: handleSearchSubmit
                )
                .padding(.top, AppSpacing.sm)

                // Persona Selection Section
                PersonaSelectionSection(
                    personas: viewModel.personas,
                    selectedPersona: $viewModel.selectedPersona,
                    onViewAllTapped: handleViewAllPersonas
                )

                // Generate Analysis Section
                GenerateAnalysisSection(
                    cost: viewModel.analysisCost,
                    remainingCredits: viewModel.creditBalance.credits,
                    isEnabled: viewModel.canGenerateAnalysis,
                    isLoading: viewModel.isGeneratingAnalysis,
                    onGenerate: handleGenerateAnalysis
                )

                // What You'll Get Section
                WhatYouGetSection(features: viewModel.features)

                // Credits Balance Card
                CreditsBalanceCard(
                    balance: viewModel.creditBalance,
                    onAddCredits: handleAddCredits
                )
                .padding(.horizontal, AppSpacing.lg)

                // Trending Analyses Section
                TrendingAnalysesSection(
                    analyses: viewModel.trendingAnalyses,
                    onExploreTapped: handleExploreTrending,
                    onAnalysisTapped: handleTrendingAnalysisTapped
                )

                // Bottom padding for tab bar
                Spacer()
                    .frame(height: AppSpacing.xxxl)
            }
        }
        .refreshable {
            await viewModel.refresh()
        }
    }

    // MARK: - Reports Tab Content
    private var reportsTabContent: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xxl) {
                // Header
                ResearchHeader(
                    selectedTab: $viewModel.selectedTab,
                    onProfileTapped: handleProfileTapped
                )

                // Reports List Section
                ReportsListSection(
                    reports: viewModel.reports,
                    sortOption: $viewModel.reportSortOption,
                    onReportTapped: handleReportTapped,
                    onRetryTapped: handleRetryTapped
                )
                .padding(.top, AppSpacing.sm)

                // Community Insights Section
                CommunityInsightsSection(
                    insights: viewModel.communityInsights,
                    onJoinDiscussion: handleJoinDiscussion,
                    onLike: handleLikeInsight,
                    onComment: handleCommentInsight,
                    onShare: handleShareInsight
                )

                // Bottom padding for tab bar
                Spacer()
                    .frame(height: AppSpacing.xxxl)
            }
        }
        .refreshable {
            await viewModel.refresh()
        }
    }

    // MARK: - Action Handlers
    private func handleProfileTapped() {
        print("Profile tapped")
    }

    private func handleTickerSelected(_ ticker: QuickTicker) {
        viewModel.selectQuickTicker(ticker)
    }

    private func handleSearchSubmit() {
        print("Search submitted: \(viewModel.searchText)")
    }

    private func handleViewAllPersonas() {
        viewModel.viewAllPersonas()
    }

    private func handleGenerateAnalysis() {
        viewModel.generateAnalysis()
    }

    private func handleAddCredits() {
        viewModel.addMoreCredits()
    }

    private func handleExploreTrending() {
        viewModel.exploreTrending()
    }

    private func handleTrendingAnalysisTapped(_ analysis: TrendingAnalysis) {
        viewModel.selectTrendingAnalysis(analysis)
    }

    private func handleReportTapped(_ report: AnalysisReport) {
        viewModel.openReport(report)
    }

    private func handleRetryTapped(_ report: AnalysisReport) {
        viewModel.retryReport(report)
    }

    private func handleJoinDiscussion() {
        viewModel.joinDiscussion()
    }

    private func handleLikeInsight(_ insight: CommunityInsight) {
        viewModel.likeInsight(insight)
    }

    private func handleCommentInsight(_ insight: CommunityInsight) {
        viewModel.commentOnInsight(insight)
    }

    private func handleShareInsight(_ insight: CommunityInsight) {
        viewModel.shareInsight(insight)
    }
}

// MARK: - TrackingView with Binding Support
struct TrackingViewWithBinding: View {
    @Binding var selectedTab: HomeTab

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                TrackingContentView()

                CustomTabBar(selectedTab: $selectedTab)
            }
        }
    }
}

// MARK: - WiserView with Binding Support (Learn)
struct WiserViewWithBinding: View {
    @Binding var selectedTab: HomeTab

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                LearnContentView()

                CustomTabBar(selectedTab: $selectedTab)
            }
        }
    }
}

// MARK: - Placeholder View for Other Tabs
struct TabPlaceholderView: View {
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
