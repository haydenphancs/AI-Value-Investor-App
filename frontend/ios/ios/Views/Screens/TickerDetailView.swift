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
    @State private var showUpgradesDowngrades = false
    @State private var showTechnicalAnalysisDetail = false
    @State private var showSearch = false
    @State private var showShareSheet = false
    @State private var showAIChat = false
    @State private var isTabBarPinned: Bool = false
    @State private var scrollOffset: CGFloat = 0
    @State private var selectedSearchResult: SearchSelection?
    @StateObject private var chatViewModel = ChatViewModel()
    /// Stable token keying this screen's compact-mode request + audio overlay host registration.
    @State private var compactToken = UUID().uuidString

    let tickerSymbol: String
    var onNavigateToResearch: (() -> Void)?

    init(tickerSymbol: String, onNavigateToResearch: (() -> Void)? = nil) {
        self.tickerSymbol = tickerSymbol
        self.onNavigateToResearch = onNavigateToResearch
        self._viewModel = StateObject(wrappedValue: TickerDetailViewModel(tickerSymbol: tickerSymbol))
    }
    
    // Share sheet items
    private var shareItems: [Any] {
        var items: [Any] = []
        
        // Share text
        if let tickerData = viewModel.tickerData {
            let shareText = """
            \(tickerData.companyName) (\(tickerData.symbol))
            \(tickerData.formattedPrice) \(tickerData.formattedChange) \(tickerData.formattedChangePercent)
            
            Check it out on Caydex!
            """
            items.append(shareText)
        }
        
        // You can add more items like URLs, images, etc.
        // items.append(URL(string: "https://yourapp.com/stock/\(tickerSymbol)")!)
        
        return items
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main Content
            VStack(spacing: 0) {
                // Navigation Header (always visible - back, ticker symbol, search, bell, star, more)
                // Shows price alongside ticker symbol when tab bar is pinned
                TickerDetailHeader(
                    onBackTapped: handleBackTapped,
                    onSearchTapped: handleSearchTapped,
                    onNotificationTapped: viewModel.handleNotificationTap,
                    onFavoriteTapped: { viewModel.toggleFavorite() },
                    onMoreTapped: handleShareTapped,
                    isFavorite: viewModel.isFavorite,
                    tickerSymbol: tickerSymbol,
                    tickerPrice: isTabBarPinned ? viewModel.tickerData?.formattedPrice : nil
                )

                // Scrollable Content with pinned tab bar
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
                        // Content above tab bar (scrolls away). Prefer the full
                        // tickerData; fall back to the fast `coreData` (price+chart)
                        // the instant it lands; else an instant shimmer skeleton —
                        // so the screen never shows a blank/blocking state.
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
                                pricePoints: tickerData.chartPricePoints,
                                isPositive: tickerData.isPositive,
                                selectedRange: $viewModel.selectedChartRange,
                                chartSettings: viewModel.chartSettings,
                                assetContext: .stock,
                                chartDataVersion: viewModel.chartDataVersion,
                                chartEventDates: viewModel.chartEventDates,
                                previousClose: viewModel.stockQuote?.previousClose
                            )
                            .padding(.top, AppSpacing.lg)
                        } else if let core = viewModel.coreData {
                            // Fast core: real price + chart, before the full overview lands.
                            TickerPriceHeader(
                                companyName: core.companyName,
                                symbol: core.symbol,
                                price: core.formattedPrice,
                                priceChange: core.formattedChange,
                                priceChangePercent: core.formattedChangePercent,
                                isPositive: core.isPositive,
                                marketStatus: core.marketStatus
                            )
                            .padding(.top, AppSpacing.sm)

                            TickerChartView(
                                pricePoints: core.chartPricePoints,
                                isPositive: core.isPositive,
                                selectedRange: $viewModel.selectedChartRange,
                                chartSettings: viewModel.chartSettings,
                                assetContext: .stock,
                                chartDataVersion: viewModel.chartDataVersion,
                                chartEventDates: viewModel.chartEventDates,
                                previousClose: viewModel.stockQuote?.previousClose
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
                    // Tab bar is pinned when it reaches the top
                    let shouldPin = position <= 0
                    if shouldPin != isTabBarPinned {
                        isTabBarPinned = shouldPin
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

            // NOTE: no blocking LoadingOverlay — it covered the header and ate the
            // back tap. The header/tabs/AI bar render instantly; the price+chart
            // area shows a shimmer skeleton (see the content gate above) until the
            // fast core, then the full overview, arrives.
        }
        .preferredColorScheme(.dark)
        .navigationBarHidden(true)
        // Audio collapses to the top status island while this stock screen is open, keeping the
        // bottom clear for "Ask Cay AI". Also keeps the player visible above this fullScreenCover.
        .globalAudioOverlay(token: compactToken, forceCompact: true)
        .task {
            viewModel.loadTickerData()
        }
        .onDisappear {
            viewModel.disconnectLivePrice()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in
            viewModel.disconnectLivePrice()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            if let status = viewModel.tickerData?.marketStatus,
               MarketHoursUtil.shouldStreamLivePrice(for: status) {
                viewModel.connectLivePrice()
            }
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
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(items: shareItems)
        }
        .sheet(isPresented: $showUpgradesDowngrades) {
            if let ratingsData = viewModel.analystRatingsData {
                UpgradesDowngradesView(actions: ratingsData.actions)
            }
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
        .sheet(isPresented: $showSearch) {
            TickerLiveSearchSheet(
                onTickerSelected: { selection in
                    showSearch = false
                    selectedSearchResult = selection
                },
                onDismiss: {
                    showSearch = false
                }
            )
            .preferredColorScheme(.dark)
        }
        .navigationDestination(item: $selectedSearchResult) { selection in
            AssetDetailRouter(selection: selection)
        }
        .aiChatCover(isPresented: $showAIChat, viewModel: chatViewModel)
        .onChange(of: viewModel.pendingAIQuery) { oldValue, newValue in
            if let query = newValue {
                chatViewModel.startNewConversation(firstMessage: query, stockId: tickerSymbol, context: viewModel.contextForCurrentTab, contextType: .stock, referenceId: tickerSymbol)
                viewModel.pendingAIQuery = nil
                showAIChat = true
            }
        }
        .onChange(of: viewModel.pendingTickerNavigation) { oldValue, newValue in
            if let ticker = newValue {
                selectedSearchResult = SearchSelection(symbol: ticker, type: "stock")
                viewModel.pendingTickerNavigation = nil
            }
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
                    onDeepResearchTap: {
                        handleDeepResearchTap()
                    },
                    onWebsiteTap: viewModel.handleWebsiteTap,
                    onRelatedTickerTap: viewModel.handleRelatedTickerTap
                )
            }
        case .news:
            TickerNewsContent(
                articles: viewModel.newsArticles,
                currentTicker: tickerSymbol,
                isLoading: viewModel.isNewsLoading,
                hasMoreNews: viewModel.hasMoreNews,
                onArticleTap: { article in viewModel.handleNewsArticleTap(article) },
                onExternalLinkTap: { article in viewModel.handleNewsExternalLink(article) },
                onRelatedTickerTap: { ticker in viewModel.handleNewsTickerTap(ticker) },
                onLoadMore: { viewModel.loadMoreNews() }
            )
        case .analysis:
            TickerAnalysisContent(
                analystRatingsData: viewModel.analystRatingsData,
                sentimentAnalysisData: viewModel.sentimentAnalysisData,
                technicalAnalysisData: viewModel.technicalAnalysisData,
                isAnalystLoaded: viewModel.isAnalystLoaded,
                isSentimentLoaded: viewModel.isSentimentLoaded,
                isTechnicalLoaded: viewModel.isTechnicalLoaded,
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
        case .financials:
            TickerFinancialsContent(
                earningsData: viewModel.earningsData,
                growthData: viewModel.growthData,
                profitPowerData: viewModel.profitPowerData,
                signalOfConfidenceData: viewModel.signalOfConfidenceData,
                revenueBreakdownData: viewModel.revenueBreakdownData,
                healthCheckData: viewModel.healthCheckData,
                onEarningsDetailTap: viewModel.handleEarningsDetail,
                onGrowthDetailTap: viewModel.handleGrowthDetail,
                onProfitPowerDetailTap: viewModel.handleProfitPowerDetail,
                onSignalOfConfidenceDetailTap: viewModel.handleSignalOfConfidenceDetail,
                onRevenueBreakdownDetailTap: viewModel.handleRevenueBreakdownDetail,
                onHealthCheckDetailTap: viewModel.handleHealthCheckDetail
            )
        case .holders:
            if let holdersData = viewModel.holdersData {
                TickerHoldersContent(
                    holdersData: holdersData
                )
            } else {
                placeholderContent(title: "Holders", description: "Loading holders data...")
            }
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
        if let onNavigateToResearch = onNavigateToResearch {
            dismiss()
            onNavigateToResearch()
        } else {
            // Fallback: open AI chat with deep research prompt
            chatViewModel.startNewConversation(
                firstMessage: "Give me a comprehensive Deep Analysis of \(tickerSymbol). Analyze the fundamentals, valuation, competitive moat, key risks, and outlook.",
                stockId: tickerSymbol,
                context: viewModel.contextForCurrentTab,
                contextType: .stock,
                referenceId: tickerSymbol
            )
            showAIChat = true
        }
    }

    private func handleSearchTapped() {
        showSearch = true
    }

    private func handleShareTapped() {
        showShareSheet = true
    }
}

// MARK: - Tab Bar Position Preference Key
struct TabBarPositionPreferenceKey: PreferenceKey {
    static var defaultValue: CGFloat = .infinity
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

// MARK: - Preview
#Preview {
    TickerDetailView(tickerSymbol: "AAPL")
}
