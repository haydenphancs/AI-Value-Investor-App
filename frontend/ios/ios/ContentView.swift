//
//  ContentView.swift
//  ios
//
//  Created by Hai Phan on 12/30/25.
//

import SwiftUI

struct ContentView: View {
    @State private var selectedTab: HomeTab = .home
    @State private var researchTickerSymbol: String? = nil
    @State private var researchSubTab: ResearchTab = .research

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            // Keep all tab views alive to avoid re-creating ViewModels on every tab switch.
            // Only the selected tab is visible; the others are hidden but retained in memory
            // so their @StateObject instances persist and don't re-trigger data loading.
            HomeViewWithBinding(
                selectedTab: $selectedTab,
                researchSubTab: $researchSubTab,
                researchTickerSymbol: $researchTickerSymbol
            )
            .opacity(selectedTab == .home ? 1 : 0)
            .allowsHitTesting(selectedTab == .home)

            UpdatesView(selectedTab: $selectedTab)
                .opacity(selectedTab == .updates ? 1 : 0)
                .allowsHitTesting(selectedTab == .updates)

            ResearchViewWithBinding(
                selectedTab: $selectedTab,
                prefilledTicker: researchTickerSymbol,
                initialSubTab: researchSubTab
            )
            .opacity(selectedTab == .research ? 1 : 0)
            .allowsHitTesting(selectedTab == .research)

            TrackingViewWithBinding(
                selectedTab: $selectedTab,
                researchTickerSymbol: $researchTickerSymbol
            )
            .opacity(selectedTab == .tracking ? 1 : 0)
            .allowsHitTesting(selectedTab == .tracking)

            WiserViewWithBinding(selectedTab: $selectedTab)
                .opacity(selectedTab == .wiser ? 1 : 0)
                .allowsHitTesting(selectedTab == .wiser)
        }
        .preferredColorScheme(.dark)
        .onChange(of: selectedTab) { oldValue, newValue in
            // Clear the research ticker when leaving research tab
            if oldValue == .research && newValue != .research {
                researchTickerSymbol = nil
                researchSubTab = .research
            }
        }
    }
}

// MARK: - HomeView with Binding Support
struct HomeViewWithBinding: View {
    @Environment(AppState.self) private var appState
    @StateObject private var viewModel = HomeViewModel()
    @Binding var selectedTab: HomeTab
    @Binding var researchSubTab: ResearchTab
    @Binding var researchTickerSymbol: String?
    @State private var showSearch = false
    @State private var showProfile = false
    @State private var selectedNewsArticle: NewsArticle?
    @State private var selectedReportTicker: ReportTickerNavigation?
    @State private var selectedMarketTicker: MarketTicker?
    @State private var selectedTrendingAnalysis: TrendingAnalysis?

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                HomeHeader(
                    onProfileTapped: {
                        showProfile = true
                    },
                    onSearchTapped: {
                        showSearch = true
                    }
                )

                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xl) {
                        // Error banner — visible when backend is unreachable
                        if let errorMessage = viewModel.error {
                            HStack(spacing: 8) {
                                Image(systemName: "wifi.exclamationmark")
                                    .foregroundColor(AppColors.neutral)
                                Text(errorMessage)
                                    .font(AppTypography.bodySmall)
                                    .foregroundColor(AppColors.textSecondary)
                                Spacer()
                            }
                            .padding(.horizontal, AppSpacing.lg)
                            .padding(.vertical, AppSpacing.sm)
                            .background(AppColors.cardBackground.opacity(0.6))
                            .cornerRadius(AppCornerRadius.medium)
                            .padding(.horizontal, AppSpacing.lg)
                            .padding(.top, AppSpacing.xs)
                        }

                        MarketTickersRow(
                            tickers: viewModel.marketTickers,
                            onTickerTap: { ticker in
                                selectedMarketTicker = ticker
                            }
                        )
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
                            onSeeAllTapped: {
                                researchSubTab = .reports
                                selectedTab = .research
                            },
                            onReportTapped: { _ in },
                            onAskOrReadTapped: { report in
                                selectedReportTicker = ReportTickerNavigation(ticker: report.stockTicker)
                            }
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
        .fullScreenCover(isPresented: $showProfile) {
            ProfileView()
                .environment(appState)
                .environment(\.appState, appState)
                .preferredColorScheme(.dark)
        }
        .fullScreenCover(item: $selectedNewsArticle) { article in
            NewsDetailView(article: article)
                .preferredColorScheme(.dark)
        }
        .fullScreenCover(item: $selectedReportTicker) { nav in
            NavigationStack {
                TickerReportView(ticker: nav.ticker)
            }
            .preferredColorScheme(.dark)
        }
        .fullScreenCover(item: $selectedMarketTicker) { ticker in
            NavigationStack {
                Group {
                    switch ticker.type {
                    case .index:
                        IndexDetailView(indexSymbol: ticker.symbol)
                    case .stock:
                        TickerDetailView(tickerSymbol: ticker.symbol)
                    case .crypto:
                        CryptoDetailView(cryptoSymbol: ticker.symbol)
                    case .commodity:
                        CommodityDetailView(commoditySymbol: ticker.symbol)
                    case .etf:
                        ETFDetailView(etfSymbol: ticker.symbol)
                    }
                }
                .navigationBarHidden(true)
            }
            .preferredColorScheme(.dark)
        }
        .fullScreenCover(item: $selectedTrendingAnalysis) { analysis in
            NavigationStack {
                TrendingAnalysisDetailView(analysis: analysis) { ticker in
                    researchTickerSymbol = ticker
                    selectedTab = .research
                }
            }
            .preferredColorScheme(.dark)
        }
    }

    // MARK: - Action Handlers
    private func handleBriefingItemTapped(_ item: DailyBriefingItem) {
        // Convert DailyBriefingItem to NewsArticle for navigation
        selectedNewsArticle = NewsArticle(
            headline: item.title,
            summary: item.subtitle,
            source: NewsSource(name: "Market Alert", iconName: nil),
            sentiment: .neutral,
            publishedAt: item.date ?? Date(),
            thumbnailName: nil,
            relatedTickers: []
        )
    }
}

// MARK: - ResearchView with Binding Support
struct ResearchViewWithBinding: View {
    @Environment(AppState.self) private var appState
    @StateObject private var viewModel: ResearchViewModel
    @Binding var selectedTab: HomeTab
    let prefilledTicker: String?
    let initialSubTab: ResearchTab
    @State private var selectedReportTicker: ReportTickerNavigation?
    @State private var selectedTrendingAnalysis: TrendingAnalysis?
    @State private var showProfile = false

    init(selectedTab: Binding<HomeTab>, prefilledTicker: String? = nil, initialSubTab: ResearchTab = .research) {
        self._selectedTab = selectedTab
        self.prefilledTicker = prefilledTicker
        self.initialSubTab = initialSubTab
        self._viewModel = StateObject(wrappedValue: ResearchViewModel(prefilledTicker: prefilledTicker))
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Header (pinned outside scroll)
                ResearchHeader(
                    selectedTab: $viewModel.selectedTab,
                    onProfileTapped: handleProfileTapped
                )

                // Tab Content
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
        .onAppear {
            viewModel.selectedTab = initialSubTab
        }
        .fullScreenCover(item: $selectedReportTicker) { nav in
            NavigationStack {
                TickerReportView(ticker: nav.ticker)
            }
            .preferredColorScheme(.dark)
        }
        .fullScreenCover(isPresented: $showProfile) {
            ProfileView()
                .environment(appState)
                .environment(\.appState, appState)
                .preferredColorScheme(.dark)
        }
        .fullScreenCover(item: $selectedTrendingAnalysis) { analysis in
            NavigationStack {
                TrendingAnalysisDetailView(analysis: analysis) { ticker in
                    viewModel.searchText = ticker
                }
            }
            .preferredColorScheme(.dark)
        }
    }

    // MARK: - Research Tab Content
    private var researchTabContent: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xxl) {
                // Target Selection Section
                TargetSelectionSection(
                    searchText: $viewModel.searchText,
                    quickTickers: viewModel.quickTickers,
                    onTickerSelected: handleTickerSelected,
                    onSearchSubmit: handleSearchSubmit
                )
                .padding(.top, AppSpacing.md)

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
        showProfile = true
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
        selectedTrendingAnalysis = analysis
    }

    private func handleReportTapped(_ report: AnalysisReport) {
        guard report.status == .ready else {
            viewModel.openReport(report)
            return
        }
        selectedReportTicker = ReportTickerNavigation(ticker: report.ticker)
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
    @Binding var researchTickerSymbol: String?

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                TrackingContentViewWithBinding(
                    selectedTab: $selectedTab,
                    researchTickerSymbol: $researchTickerSymbol
                )

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
                        .font(AppTypography.iconHero)
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
        .environment(AppState())
        .environmentObject(AudioManager.shared)
}
