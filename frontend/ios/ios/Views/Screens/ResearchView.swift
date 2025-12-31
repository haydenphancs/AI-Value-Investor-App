//
//  ResearchView.swift
//  ios
//
//  Main Research screen combining all organisms
//

import SwiftUI

struct ResearchContentView: View {
    @StateObject private var viewModel = ResearchViewModel()

    var body: some View {
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

    // MARK: - Reports Tab Action Handlers
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

// MARK: - Preview
#Preview {
    ResearchContentView()
        .preferredColorScheme(.dark)
}
