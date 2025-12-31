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

                // Scrollable Content
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

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
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
}

// MARK: - Reports Tab Content (Placeholder)
struct ReportsContentView: View {
    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: AppSpacing.lg) {
                Image(systemName: "doc.text.fill")
                    .font(.system(size: 48))
                    .foregroundColor(AppColors.textMuted)

                Text("Your Reports")
                    .font(AppTypography.title2)
                    .foregroundColor(AppColors.textPrimary)

                Text("Generated analyses will appear here")
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textSecondary)
            }
        }
    }
}

// MARK: - Preview
#Preview {
    ResearchContentView()
        .preferredColorScheme(.dark)
}
