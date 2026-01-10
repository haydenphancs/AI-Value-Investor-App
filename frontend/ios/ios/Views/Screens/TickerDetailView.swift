//
//  TickerDetailView.swift
//  ios
//
//  Main Ticker Detail screen displaying stock information
//

import SwiftUI

struct TickerDetailView: View {
    @StateObject private var viewModel: TickerDetailViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showMoreOptions = false
    @State private var showUpgradesDowngrades = false
    @State private var showTechnicalAnalysisDetail = false

    let tickerSymbol: String

    init(tickerSymbol: String) {
        self.tickerSymbol = tickerSymbol
        self._viewModel = StateObject(wrappedValue: TickerDetailViewModel(tickerSymbol: tickerSymbol))
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main Content
            VStack(spacing: 0) {
                // Navigation Header (always visible - back, bell, star, more buttons)
                TickerDetailHeader(
                    onBackTapped: handleBackTapped,
                    onNotificationTapped: viewModel.handleNotificationTap,
                    onFavoriteTapped: viewModel.toggleFavorite,
                    onMoreTapped: handleMoreTapped,
                    isFavorite: viewModel.isFavorite
                )

                // Scrollable Content with pinned tab bar
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
                        // Content above tab bar (scrolls away)
                        if let tickerData = viewModel.tickerData {
                            // Full Ticker Price Header
                            TickerPriceHeader(
                                companyName: tickerData.companyName,
                                symbol: tickerData.symbol,
                                price: tickerData.formattedPrice,
                                priceChange: tickerData.formattedChange,
                                priceChangePercent: tickerData.formattedChangePercent,
                                isPositive: tickerData.isPositive,
                                marketStatus: tickerData.marketStatus
                            )
                            .padding(.top, AppSpacing.sm)

                            // Chart
                            TickerChartView(
                                chartData: tickerData.chartData,
                                isPositive: tickerData.isPositive,
                                selectedRange: $viewModel.selectedChartRange
                            )
                            .padding(.top, AppSpacing.lg)
                        }

                        // Section with pinned tab bar header
                        Section {
                            // Tab Content
                            tabContent
                        } header: {
                            // Tab Bar - this will stick at the top when scrolling
                            VStack(spacing: 0) {
                                TickerDetailTabBar(selectedTab: $viewModel.selectedTab)
                                    .padding(.top, AppSpacing.lg)

                                // Divider
                                Rectangle()
                                    .fill(AppColors.cardBackgroundLight)
                                    .frame(height: 1)
                            }
                            .background(AppColors.background)
                        }
                    }
                }
                .refreshable {
                    await viewModel.refresh()
                }
            }

            // Bottom AI Chat Bar (always visible)
            TickerDetailAIBar(
                inputText: $viewModel.aiInputText,
                tickerSymbol: tickerSymbol,
                suggestions: viewModel.aiSuggestions,
                onSuggestionTap: viewModel.handleSuggestionTap,
                onSend: viewModel.handleAISend
            )

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .preferredColorScheme(.dark)
        .navigationBarHidden(true)
        .task {
            viewModel.loadTickerData()
        }
        .gesture(
            DragGesture()
                .onEnded { value in
                    // Swipe right to dismiss
                    if value.translation.width > 100 {
                        handleBackTapped()
                    }
                }
        )
        .confirmationDialog("Options", isPresented: $showMoreOptions) {
            Button("Share") {
                handleShare()
            }
            Button("Add to Watchlist") {
                handleAddToWatchlist()
            }
            Button("Set Price Alert") {
                handleSetPriceAlert()
            }
            Button("Compare") {
                handleCompare()
            }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(isPresented: $showUpgradesDowngrades) {
            if let analysisData = viewModel.analysisData {
                UpgradesDowngradesView(actions: analysisData.analystRatings.actions)
            }
        }
        .sheet(isPresented: $showTechnicalAnalysisDetail) {
            TechnicalAnalysisDetailView(
                detailData: TechnicalAnalysisDetailData.sampleData
            )
        }
    }

    // MARK: - Tab Content

    @ViewBuilder
    private var tabContent: some View {
        switch viewModel.selectedTab {
        case .overview:
            if let tickerData = viewModel.tickerData {
                TickerDetailOverviewContent(
                    tickerData: tickerData,
                    onDeepResearchTap: viewModel.handleDeepResearch,
                    onWebsiteTap: viewModel.handleWebsiteTap,
                    onRelatedTickerTap: viewModel.handleRelatedTickerTap
                )
            }
        case .news:
            TickerNewsContent(
                articles: viewModel.newsArticles,
                currentTicker: tickerSymbol,
                onArticleTap: viewModel.handleNewsArticleTap,
                onExternalLinkTap: viewModel.handleNewsExternalLink,
                onRelatedTickerTap: viewModel.handleNewsTickerTap
            )
        case .analysis:
            if let analysisData = viewModel.analysisData {
                TickerAnalysisContent(
                    analysisData: analysisData,
                    selectedMomentumPeriod: $viewModel.selectedMomentumPeriod,
                    selectedSentimentTimeframe: $viewModel.selectedSentimentTimeframe,
                    onAnalystRatingsMoreTap: viewModel.handleAnalystRatingsMore,
                    onAnalystActionsTap: {
                        showUpgradesDowngrades = true
                    },
                    onSentimentMoreTap: viewModel.handleSentimentMore,
                    onTechnicalDetailTap: {
                        showTechnicalAnalysisDetail = true
                    }
                )
            } else {
                placeholderContent(title: "Analysis", description: "Loading analysis data...")
            }
        case .financials:
            placeholderContent(title: "Financials", description: "Financial statements for \(tickerSymbol)")
        case .insiders:
            placeholderContent(title: "Insiders", description: "Insider trading activity for \(tickerSymbol)")
        }
    }

    private func placeholderContent(title: String, description: String) -> some View {
        VStack(spacing: AppSpacing.lg) {
            Image(systemName: "chart.bar.doc.horizontal")
                .font(.system(size: 48))
                .foregroundColor(AppColors.textMuted)

            Text(title)
                .font(AppTypography.title2)
                .foregroundColor(AppColors.textPrimary)

            Text(description)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)

            Spacer()
                .frame(height: 150)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.xxxl)
        .padding(.horizontal, AppSpacing.lg)
    }

    // MARK: - Action Handlers

    private func handleBackTapped() {
        dismiss()
    }

    private func handleMoreTapped() {
        showMoreOptions = true
    }

    private func handleShare() {
        print("Share \(tickerSymbol)")
    }

    private func handleAddToWatchlist() {
        print("Add \(tickerSymbol) to watchlist")
    }

    private func handleSetPriceAlert() {
        print("Set price alert for \(tickerSymbol)")
    }

    private func handleCompare() {
        print("Compare \(tickerSymbol)")
    }
}

// MARK: - Preview
#Preview {
    TickerDetailView(tickerSymbol: "AAPL")
}
