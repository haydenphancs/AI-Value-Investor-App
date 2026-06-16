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
            }
            // Tap a ready Reports-tab card → push the AnalysisReport,
            // which carries the backend report ID + persona so the
            // detail view can hit the cached ticker_report_data.
            .navigationDestination(for: AnalysisReport.self) { report in
                TickerReportView(report: report)
            }
            // Trending analyses still navigate by ticker String (no
            // backend report row exists for those entries).
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
            .alert("Delete \(viewModel.selectedReportCount) report\(viewModel.selectedReportCount == 1 ? "" : "s")?",
                   isPresented: $viewModel.showDeleteConfirm) {
                Button("Delete", role: .destructive) {
                    Task { await viewModel.deleteSelectedReports() }
                }
                Button("Cancel", role: .cancel) { }
            } message: {
                Text("This can't be undone.")
            }
            .sheet(isPresented: $viewModel.showCreditsSheet) {
                CreditsPricingSheet(currentBalance: viewModel.creditBalance.credits)
            }
            .sheet(isPresented: $viewModel.showPersonasSheet) {
                PersonasSheet(
                    personas: viewModel.personas,
                    selectedPersona: $viewModel.selectedPersona
                )
            }
            .sheet(isPresented: $viewModel.showTargetSearchSheet) {
                TargetSearchSheet { result in
                    viewModel.selectTarget(result)
                }
            }
            .fullScreenCover(isPresented: $viewModel.showProfileSheet) {
                ProfileView()
                    .environment(appState)
                    .environment(\.appState, appState)
            }
            .onAppear {
                viewModel.setAuthCheck { [weak appState] in
                    appState?.auth.isAuthenticated ?? false
                }
                // If we land directly on the Reports tab (e.g. via deep
                // link or preserved state), start polling immediately.
                if viewModel.selectedTab == .reports {
                    viewModel.startReportsPolling()
                }
            }
            .onDisappear {
                viewModel.stopReportsPolling()
            }
            .onChange(of: viewModel.selectedTab) { _, newTab in
                if newTab == .reports {
                    viewModel.startReportsPolling()
                } else {
                    viewModel.stopReportsPolling()
                }
            }
        }
    }

    // MARK: - Research Tab Content
    private var researchTabContent: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xxl) {
                // Target Selection Section
                TargetSelectionSection(
                    selectedTarget: viewModel.selectedTarget,
                    fallbackTicker: viewModel.searchText,
                    onTapSearch: { viewModel.openTargetSearch() },
                    onClearTarget: { viewModel.clearTarget() }
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
                    sections: viewModel.groupedReports,
                    sortOption: $viewModel.reportSortOption,
                    searchText: $viewModel.reportSearchText,
                    isSearchActive: $viewModel.isReportSearchActive,
                    isSelecting: $viewModel.isSelectingReports,
                    selectedIds: viewModel.selectedReportIds,
                    personaTags: viewModel.personas,
                    selectedPersonaKeys: viewModel.selectedPersonaKeys,
                    onReportTapped: handleReportTapped,
                    onRetryTapped: handleRetryTapped,
                    onToggleSelect: handleToggleSelect,
                    onToggleSelectingMode: handleToggleSelectingMode,
                    onTogglePersonaTag: { viewModel.togglePersonaTag($0) }
                )
                .padding(.top, AppSpacing.sm)

                // Community Insights — deferred. Backend feature pending; the
                // mock data + stub handlers are kept in the codebase for the
                // future read/write feed.

                // Bottom padding for tab bar
                Spacer()
                    .frame(height: AppSpacing.xxxl)

                // Extra inset while selecting so the last card scrolls clear
                // of the floating selection bar.
                if viewModel.isSelectingReports {
                    Spacer().frame(height: 72)
                }
            }
        }
        .refreshable {
            await viewModel.refresh()
        }
        // Floating selection bar — lives here (not in the scrolling organism)
        // so it pins to the bottom above the tab bar. Scoped to the Reports
        // tab because reportsTabContent is only built for that tab.
        .overlay(alignment: .bottom) {
            if viewModel.isSelectingReports {
                ReportsSelectionBar(
                    selectedCount: viewModel.selectedReportCount,
                    isDeleting: viewModel.isDeletingReports,
                    onDelete: { viewModel.showDeleteConfirm = true }
                )
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.22), value: viewModel.isSelectingReports)
    }

    // MARK: - Research Tab Action Handlers
    private func handleProfileTapped() {
        viewModel.showProfile()
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

    private func handleTrendingAnalysisTapped(_ analysis: TrendingAnalysis) {
        navigationPath.append(analysis)
    }

    // MARK: - Reports Tab Action Handlers
    private func handleReportTapped(_ report: AnalysisReport) {
        guard report.status == .ready else { return }
        // Push the full AnalysisReport (not just the ticker) so the
        // detail view receives backendId + persona and can short-circuit
        // to the cached ticker_report_data.
        navigationPath.append(report)
    }

    private func handleRetryTapped(_ report: AnalysisReport) {
        viewModel.retryReport(report)
    }

    private func handleToggleSelect(_ report: AnalysisReport) {
        viewModel.toggleReportSelection(report)
    }

    private func handleToggleSelectingMode() {
        if viewModel.isSelectingReports {
            viewModel.exitSelectionMode()
        } else {
            viewModel.isSelectingReports = true
        }
    }
}

// MARK: - Preview
#Preview {
    ResearchContentView()
        .preferredColorScheme(.dark)
}
