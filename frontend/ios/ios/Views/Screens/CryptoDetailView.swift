//
//  CryptoDetailView.swift
//  ios
//
//  Main Crypto Detail screen displaying cryptocurrency information
//  Tabs: Overview, News, Analysis
//

import SwiftUI

struct CryptoDetailView: View {
    @StateObject private var viewModel: CryptoDetailViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showMoreOptions = false
    @State private var showTechnicalAnalysisDetail = false
    @State private var showSearch = false
    @State private var showShareSheet = false
    @State private var showAIChat = false
    @State private var isTabBarPinned: Bool = false

    let cryptoSymbol: String
    var onNavigateToResearch: (() -> Void)?

    init(cryptoSymbol: String, onNavigateToResearch: (() -> Void)? = nil) {
        self.cryptoSymbol = cryptoSymbol
        self.onNavigateToResearch = onNavigateToResearch
        self._viewModel = StateObject(wrappedValue: CryptoDetailViewModel(cryptoSymbol: cryptoSymbol))
    }

    // Share sheet items
    private var shareItems: [Any] {
        var items: [Any] = []

        if let cryptoData = viewModel.cryptoData {
            let shareText = """
            \(cryptoData.name) (\(cryptoData.symbol))
            \(cryptoData.formattedPrice) \(cryptoData.formattedChange) \(cryptoData.formattedChangePercent)

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
                    tickerSymbol: cryptoSymbol,
                    tickerPrice: isTabBarPinned ? viewModel.cryptoData?.formattedPrice : nil
                )

                // Scrollable Content with pinned tab bar
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
                        // Content above tab bar (scrolls away)
                        if let cryptoData = viewModel.cryptoData {
                            // Crypto Price Header
                            CryptoPriceHeader(
                                cryptoName: cryptoData.name,
                                symbol: cryptoData.symbol,
                                price: cryptoData.formattedPrice,
                                priceChange: cryptoData.formattedChange,
                                priceChangePercent: cryptoData.formattedChangePercent,
                                isPositive: cryptoData.isPositive,
                                marketStatus: cryptoData.marketStatus
                            )
                            .padding(.top, AppSpacing.sm)

                            // Chart
                            TickerChartView(
                                chartData: cryptoData.chartData,
                                isPositive: cryptoData.isPositive,
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
                                CryptoDetailTabBar(selectedTab: $viewModel.selectedTab)
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
            CryptoDetailAIBar(
                inputText: $viewModel.aiInputText,
                cryptoSymbol: cryptoSymbol,
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
            viewModel.loadCryptoData()
        }
        .gesture(
            DragGesture()
                .onEnded { value in
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
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(items: shareItems)
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
        .fullScreenCover(isPresented: $showAIChat) {
            NavigationView {
                ZStack {
                    AppColors.background
                        .ignoresSafeArea()
                    ChatTabView(initialPrompt: "Deep Analysis \(cryptoSymbol)")
                }
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Close") {
                            showAIChat = false
                        }
                        .foregroundColor(AppColors.primaryBlue)
                    }
                }
            }
            .environmentObject(AudioManager.shared)
            .preferredColorScheme(.dark)
        }
    }

    // MARK: - Tab Content

    @ViewBuilder
    private var tabContent: some View {
        switch viewModel.selectedTab {
        case .overview:
            if let cryptoData = viewModel.cryptoData {
                CryptoDetailOverviewContent(
                    cryptoData: cryptoData,
                    onDeepResearchTap: {
                        handleDeepResearchTap()
                    },
                    onWebsiteTap: viewModel.handleWebsiteTap,
                    onWhitepaperTap: viewModel.handleWhitepaperTap,
                    onRelatedCryptoTap: viewModel.handleRelatedCryptoTap
                )
            }
        case .news:
            TickerNewsContent(
                articles: viewModel.newsArticles,
                currentTicker: cryptoSymbol,
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
                    onAnalystActionsTap: {},
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

    private func handleDeepResearchTap() {
        showAIChat = true
    }

    private func handleSearchTapped() {
        showSearch = true
    }

    private func handleShareTapped() {
        showShareSheet = true
    }

    private func handleShare() {
        print("Share \(cryptoSymbol)")
    }

    private func handleAddToWatchlist() {
        print("Add \(cryptoSymbol) to watchlist")
    }

    private func handleSetPriceAlert() {
        print("Set price alert for \(cryptoSymbol)")
    }

    private func handleCompare() {
        print("Compare \(cryptoSymbol)")
    }
}

// MARK: - Preview
#Preview {
    CryptoDetailView(cryptoSymbol: "ETH")
}
