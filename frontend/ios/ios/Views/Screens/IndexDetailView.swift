//
//  IndexDetailView.swift
//  ios
//
//  Main Index Detail screen displaying index information
//

import SwiftUI

struct IndexDetailView: View {
    @StateObject private var viewModel: IndexDetailViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showSearch = false
    @State private var showShareSheet = false
    @State private var showUpgradesDowngrades = false
    @State private var showTechnicalAnalysisDetail = false
    @State private var isTabBarPinned: Bool = false

    let indexSymbol: String

    init(indexSymbol: String) {
        self.indexSymbol = indexSymbol
        self._viewModel = StateObject(wrappedValue: IndexDetailViewModel(indexSymbol: indexSymbol))
    }

    // Share sheet items
    private var shareItems: [Any] {
        var items: [Any] = []

        if let indexData = viewModel.indexData {
            let shareText = """
            \(indexData.indexName) (\(indexData.symbol))
            \(indexData.formattedPrice) \(indexData.formattedChange) \(indexData.formattedChangePercent)

            Check it out on Caudex!
            """
            items.append(shareText)
        }

        return items
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main Content
            VStack(spacing: 0) {
                // Navigation Header
                TickerDetailHeader(
                    onBackTapped: handleBackTapped,
                    onSearchTapped: handleSearchTapped,
                    onNotificationTapped: viewModel.handleNotificationTap,
                    onFavoriteTapped: viewModel.toggleFavorite,
                    onMoreTapped: handleShareTapped,
                    isFavorite: viewModel.isFavorite,
                    tickerSymbol: indexSymbol,
                    tickerPrice: isTabBarPinned ? viewModel.indexData?.formattedPrice : nil
                )

                // Scrollable Content with pinned tab bar
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
                        // Content above tab bar (scrolls away)
                        if let indexData = viewModel.indexData {
                            // Full Price Header
                            TickerPriceHeader(
                                companyName: indexData.indexName,
                                symbol: indexData.symbol,
                                price: indexData.formattedPrice,
                                priceChange: indexData.formattedChange,
                                priceChangePercent: indexData.formattedChangePercent,
                                isPositive: indexData.isPositive,
                                marketStatus: indexData.marketStatus
                            )
                            .padding(.top, AppSpacing.sm)

                            // Chart
                            TickerChartView(
                                chartData: indexData.chartData,
                                isPositive: indexData.isPositive,
                                selectedRange: $viewModel.selectedChartRange
                            )
                            .padding(.top, AppSpacing.lg)
                        }

                        // Section with pinned tab bar header
                        Section {
                            // Tab Content
                            tabContent
                        } header: {
                            // Tab Bar - sticks at top when scrolling
                            VStack(spacing: 0) {
                                IndexDetailTabBar(selectedTab: $viewModel.selectedTab)
                                    .padding(.top, AppSpacing.lg)

                                // Divider
                                Rectangle()
                                    .fill(AppColors.cardBackgroundLight)
                                    .frame(height: 1)
                            }
                            .background(AppColors.background)
                            .background(
                                GeometryReader { geometry in
                                    Color.clear
                                        .preference(
                                            key: TabBarPositionPreferenceKey.self,
                                            value: geometry.frame(in: .named("scroll")).minY
                                        )
                                }
                            )
                        }
                    }
                }
                .coordinateSpace(name: "scroll")
                .onPreferenceChange(TabBarPositionPreferenceKey.self) { position in
                    let shouldPin = position <= 0
                    if shouldPin != isTabBarPinned {
                        isTabBarPinned = shouldPin
                    }
                }
                .refreshable {
                    await viewModel.refresh()
                }
            }

            // Bottom AI Chat Bar
            IndexDetailAIBar(
                inputText: $viewModel.aiInputText,
                indexSymbol: indexSymbol,
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
            viewModel.loadIndexData()
        }
        .gesture(
            DragGesture()
                .onEnded { value in
                    if value.translation.width > 100 {
                        handleBackTapped()
                    }
                }
        )
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(items: shareItems)
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
        .fullScreenCover(isPresented: $showSearch) {
            SearchView()
                .preferredColorScheme(.dark)
        }
    }

    // MARK: - Tab Content

    @ViewBuilder
    private var tabContent: some View {
        switch viewModel.selectedTab {
        case .overview:
            if let indexData = viewModel.indexData {
                IndexDetailOverviewContent(
                    indexData: indexData,
                    onWebsiteTap: viewModel.handleWebsiteTap
                )
            }
        case .news:
            TickerNewsContent(
                articles: viewModel.newsArticles,
                currentTicker: indexSymbol,
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

    private func handleSearchTapped() {
        showSearch = true
    }

    private func handleShareTapped() {
        showShareSheet = true
    }
}

// MARK: - Preview
#Preview {
    IndexDetailView(indexSymbol: "^GSPC")
}
