//
//  ResearchView.swift
//  ios
//
//  Main Research screen combining all organisms
//

import SwiftUI

struct ResearchContentView: View {
    @Environment(AppState.self) private var appState
    @StateObject private var viewModel: ResearchViewModel
    @State private var navigationPath = NavigationPath()

    init(prefilledTicker: String? = nil) {
        // Note: appState isn't available yet in init; auth check is injected as a closure
        // that captures nothing — it will be replaced in .onAppear
        _viewModel = StateObject(wrappedValue: ResearchViewModel(prefilledTicker: prefilledTicker))
    }

    var body: some View {
        NavigationStack(path: $navigationPath) {
            ZStack {
                // Background
                AppColors.background
                    .ignoresSafeArea()

                // Main Content
                VStack(spacing: 0) {
                    // Header
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
                }

                // Loading overlay
                if viewModel.isLoading {
                    LoadingOverlay()
                }

                // Generation progress overlay
                if viewModel.isGeneratingAnalysis {
                    generationProgressOverlay
                }
            }
            .navigationDestination(for: String.self) { ticker in
                TickerReportView(ticker: ticker)
            }
            .navigationDestination(for: TrendingAnalysis.self) { analysis in
                TrendingAnalysisDetailView(analysis: analysis) { ticker in
                    viewModel.searchText = ticker
                }
            }
            .alert("Error", isPresented: Binding(
                get: { viewModel.error != nil },
                set: { if !$0 { viewModel.error = nil } }
            )) {
                Button("OK") { viewModel.error = nil }
            } message: {
                Text(viewModel.error ?? "")
            }
            .alert("Sign In Required", isPresented: $viewModel.showSignInPrompt) {
                Button("OK") { }
            } message: {
                Text("Please sign in to generate research reports.")
            }
            .onAppear {
                viewModel.setAuthCheck { [weak appState] in
                    appState?.auth.isAuthenticated ?? false
                }
            }
        }
    }

    // MARK: - Generation Progress Overlay
    private var generationProgressOverlay: some View {
        VStack(spacing: AppSpacing.lg) {
            Spacer()

            VStack(spacing: AppSpacing.md) {
                // Animated sparkle icon
                Image(systemName: "sparkles")
                    .font(.system(size: 32))
                    .foregroundColor(.cyan)
                    .symbolEffect(.pulse)

                Text("Generating Analysis")
                    .font(.headline)
                    .foregroundColor(.white)

                // Progress bar
                ProgressView(value: Double(viewModel.generationProgress), total: 100)
                    .progressViewStyle(LinearProgressViewStyle(tint: .cyan))
                    .frame(maxWidth: 240)

                // Progress percentage
                Text("\(viewModel.generationProgress)%")
                    .font(.title2.bold())
                    .foregroundColor(.cyan)

                // Current step
                Text(viewModel.generationStep)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
            }
            .padding(AppSpacing.xxl)
            .background(
                RoundedRectangle(cornerRadius: 20)
                    .fill(.ultraThinMaterial)
            )
            .padding(.horizontal, AppSpacing.xxl)

            Spacer()
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color.black.opacity(0.5))
        .ignoresSafeArea()
        .animation(.easeInOut, value: viewModel.generationProgress)
    }

    // MARK: - Research Tab Content
    private var researchTabContent: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xxl) {
                // Target Selection Section
                TargetSelectionSection(
                    searchText: $viewModel.searchText,
                    quickTickers: viewModel.quickTickers,
                    searchResults: viewModel.searchResults,
                    isSearching: viewModel.isSearching,
                    showSearchResults: viewModel.showSearchResults,
                    onTickerSelected: handleTickerSelected,
                    onSearchSubmit: handleSearchSubmit,
                    onResultSelected: handleSearchResultSelected
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

    // MARK: - Research Tab Action Handlers
    private func handleProfileTapped() {
        print("Profile tapped")
    }

    private func handleTickerSelected(_ ticker: QuickTicker) {
        viewModel.selectQuickTicker(ticker)
    }

    private func handleSearchSubmit() {
        viewModel.dismissSearchResults()
    }

    private func handleSearchResultSelected(_ result: StockSearchResult) {
        viewModel.selectSearchResult(result)
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
        navigationPath.append(analysis)
    }

    // MARK: - Reports Tab Action Handlers
    private func handleReportTapped(_ report: AnalysisReport) {
        guard report.status == .ready else { return }
        navigationPath.append(report.ticker)
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

// MARK: - Preview
#Preview {
    ResearchContentView()
        .preferredColorScheme(.dark)
}
