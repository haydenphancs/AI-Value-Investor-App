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
    @StateObject private var chatViewModel = ChatViewModel()
    @Environment(\.dismiss) private var dismiss
    @State private var showMoreOptions = false
    @State private var showTechnicalAnalysisDetail = false
    @State private var showSearch = false
    @State private var showShareSheet = false
    @State private var showAIChat = false
    @State private var isTabBarPinned: Bool = false
    @State private var selectedSearchResult: SearchSelection?
    /// Stable token keying this screen's compact-mode request + audio overlay host registration.
    @State private var compactToken = UUID().uuidString

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

            Check it out on Caydex!
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
                                pricePoints: cryptoData.chartPricePoints,
                                isPositive: cryptoData.isPositive,
                                selectedRange: $viewModel.selectedChartRange,
                                chartSettings: viewModel.chartSettings,
                                assetContext: .crypto,
                                chartDataVersion: viewModel.chartDataVersion,
                                previousClose: cryptoData.previousClose
                            )
                            .padding(.top, AppSpacing.lg)
                        } else {
                            DetailHeaderChartSkeleton()
                                .padding(.top, AppSpacing.sm)
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

            // No blocking LoadingOverlay — it covered the header + ate the back tap.
            // The price+chart area shows a shimmer skeleton until data loads.
        }
        .preferredColorScheme(.dark)
        .navigationBarHidden(true)
        // Audio collapses to the top status island while this asset screen is open, keeping the
        // bottom clear for "Ask Cay AI". Also keeps the player visible above this fullScreenCover.
        .globalAudioOverlay(token: compactToken, forceCompact: true)
        .task {
            viewModel.loadCryptoData()
        }
        .onDisappear {
            viewModel.disconnectLivePrice()
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
            if let detailData = viewModel.technicalAnalysisDetailData {
                TechnicalAnalysisDetailView(detailData: detailData)
            } else {
                ProgressView("Loading technical analysis...")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(AppColors.background)
                    .onAppear { viewModel.fetchTechnicalAnalysisDetail() }
            }
        }
        .fullScreenCover(isPresented: $showSearch) {
            SearchView()
                .preferredColorScheme(.dark)
        }
        .aiChatCover(isPresented: $showAIChat, viewModel: chatViewModel)
        .navigationDestination(item: $selectedSearchResult) { selection in
            AssetDetailRouter(selection: selection)
        }
        .onChange(of: viewModel.pendingAIQuery) { oldValue, newValue in
            if let query = newValue {
                chatViewModel.startNewConversation(
                    firstMessage: query,
                    stockId: "\(cryptoSymbol)USD",
                    context: viewModel.contextForCurrentTab,
                    contextType: .crypto,
                    referenceId: cryptoSymbol
                )
                viewModel.pendingAIQuery = nil
                showAIChat = true
            }
        }
        .onChange(of: viewModel.pendingTickerNavigation) { oldValue, newValue in
            if let ticker = newValue {
                selectedSearchResult = SearchSelection(symbol: ticker, type: "crypto")
                viewModel.pendingTickerNavigation = nil
            }
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
                isLoading: viewModel.isNewsLoading,
                hasMoreNews: viewModel.hasMoreNews,
                onArticleTap: viewModel.handleNewsArticleTap,
                onExternalLinkTap: viewModel.handleNewsExternalLink,
                onRelatedTickerTap: viewModel.handleNewsTickerTap,
                onLoadMore: { viewModel.loadMoreNews() }
            )
        case .analysis:
            TickerAnalysisContent(
                analystRatingsData: nil,
                sentimentAnalysisData: viewModel.sentimentAnalysisData,
                technicalAnalysisData: viewModel.technicalAnalysisData,
                fearGreedData: viewModel.fearGreedData,
                isAnalystLoaded: true,
                isFearGreedLoaded: !viewModel.isFearGreedLoading,
                isSentimentLoaded: !viewModel.isSentimentLoading,
                isTechnicalLoaded: true,
                selectedMomentumPeriod: $viewModel.selectedMomentumPeriod,
                selectedSentimentTimeframe: $viewModel.selectedSentimentTimeframe,
                selectedFearGreedTimeframe: $viewModel.selectedFearGreedTimeframe,
                onSentimentMoreTap: viewModel.handleSentimentMore,
                onTechnicalDetailTap: {
                    showTechnicalAnalysisDetail = true
                }
            )
        }
    }

    private func placeholderContent(title: String, description: String) -> some View {
        VStack(spacing: AppSpacing.lg) {
            Image(systemName: "chart.bar.doc.horizontal")
                .font(AppTypography.iconHero)
                .foregroundColor(AppColors.textMuted)

            Text(title)
                .font(AppTypography.titleCompact)
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
        chatViewModel.startNewConversation(
            firstMessage: "Give me a comprehensive Deep Analysis of \(cryptoSymbol). Analyze the current price action, market position, key risks, and outlook.",
            stockId: "\(cryptoSymbol)USD",
            context: viewModel.contextForCurrentTab,
            contextType: .crypto,
            referenceId: cryptoSymbol
        )
        showAIChat = true
    }

    private func handleSearchTapped() {
        showSearch = true
    }

    private func handleShareTapped() {
        showShareSheet = true
    }

    private func handleShare() {
        showShareSheet = true
    }

    private func handleAddToWatchlist() {
        viewModel.toggleFavorite()
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
