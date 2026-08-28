//
//  CommodityDetailView.swift
//  ios
//
//  Main Commodity Detail screen displaying commodity information
//  Tabs: Overview, News, Analysis
//

import SwiftUI

struct CommodityDetailView: View {
    /// The bell in `TickerDetailHeader` renders ONLY when `onNotificationTapped`
    /// is non-nil. Every detail screen passed `nil`, so it had never rendered —
    /// this is what it was waiting for.
    @State private var showPriceAlerts = false

    @StateObject private var viewModel: CommodityDetailViewModel
    @StateObject private var chatViewModel = ChatViewModel()
    @Environment(\.dismiss) private var dismiss
    @State private var showSearch = false
    @State private var showShareSheet = false
    @State private var showTechnicalAnalysisDetail = false
    @State private var showAIChat = false
    @State private var isTabBarPinned: Bool = false
    @State private var selectedSearchResult: SearchSelection?
    /// Stable token keying this screen's compact-mode request + audio overlay host registration.
    @State private var compactToken = UUID().uuidString

    let commoditySymbol: String

    init(commoditySymbol: String) {
        self.commoditySymbol = commoditySymbol
        self._viewModel = StateObject(wrappedValue: CommodityDetailViewModel(commoditySymbol: commoditySymbol))
    }

    // Share sheet items
    private var shareItems: [Any] {
        var items: [Any] = []

        if let commodityData = viewModel.commodityData {
            let shareText = """
            \(commodityData.name) (\(commodityData.symbol))
            \(commodityData.formattedPrice) \(commodityData.formattedChange) \(commodityData.formattedChangePercent)

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
                    // nil until price alerts ship — hides the bell rather than
                    // showing a control whose handler was a print().
                    onNotificationTapped: { showPriceAlerts = true },
                    onFavoriteTapped: viewModel.toggleFavorite,
                    onMoreTapped: handleShareTapped,
                    isFavorite: viewModel.isFavorite,
                    tickerSymbol: commoditySymbol,
                    tickerPrice: isTabBarPinned ? viewModel.commodityData?.formattedPrice : nil
                )

                // Eager container + an overlay-pinned tab bar. The LazyVStack that used
                // to be here re-walked its predecessors every frame to place the pinned
                // section header, and a live-price tick resizes its first child — so the
                // walk restarted continuously while scrolling. See DetailScrollContainer.
                DetailScrollContainer(
                    isTabBarPinned: $isTabBarPinned,
                    onRefresh: { await viewModel.refresh() }
                ) {
                    // Content above tab bar (scrolls away)
                    // `headerData` is the full model once it lands and the fast-core
                    // slice until then — the header and chart are the only things
                    // core can fill, so ONLY this gate takes it. The tabs below keep
                    // waiting for the full response, exactly as the stock screen does.
                    if let commodityData = viewModel.headerData {
                        // Commodity Price Header
                        CommodityPriceHeader(
                            commodityName: commodityData.name,
                            symbol: commodityData.symbol,
                            price: commodityData.formattedPrice,
                            priceChange: commodityData.formattedChange,
                            priceChangePercent: commodityData.formattedChangePercent,
                            isPositive: commodityData.isPositive,
                            marketStatus: commodityData.marketStatus
                        )
                        .padding(.top, AppSpacing.sm)

                        // Chart
                        TickerChartView(
                            pricePoints: commodityData.chartPricePoints,
                            isPositive: commodityData.isPositive,
                            selectedRange: $viewModel.selectedChartRange,
                            chartSettings: viewModel.chartSettings,
                            assetContext: .commodity,
                            chartDataVersion: viewModel.chartDataVersion,
                            previousClose: commodityData.previousClose
                        )
                        .padding(.top, AppSpacing.lg)
                    } else if let errorMessage = viewModel.errorMessage {
                        // The ViewModel has been writing this message all along and
                        // nothing rendered it: on failure the screen fell through to
                        // a skeleton that never resolved, so the user sat on a
                        // permanent shimmer with no error and no retry.
                        DetailLoadFailureCard(
                            message: errorMessage,
                            isRetrying: viewModel.isLoading,
                            onRetry: { viewModel.loadCommodityData() }
                        )
                    } else {
                        DetailHeaderChartSkeleton(symbol: commoditySymbol)
                            .padding(.top, AppSpacing.sm)
                    }
                } tabs: {
                    CommodityDetailTabBar(selectedTab: $viewModel.selectedTab)
                } content: {
                    tabContent
                }
            }

            // Bottom AI Chat Bar
            CommodityDetailAIBar(
                inputText: $viewModel.aiInputText,
                commoditySymbol: commoditySymbol,
                suggestions: viewModel.aiSuggestions,
                onSuggestionTap: viewModel.handleSuggestionTap,
                onSend: viewModel.handleAISend
            )

            // No blocking LoadingOverlay — it covered the header + ate the back tap.
            // The price+chart area shows a shimmer skeleton until data loads.
        }
        .navigationBarHidden(true)
        .sheet(isPresented: $showPriceAlerts) {
            PriceAlertsSheet(ticker: commoditySymbol, assetType: "commodity")
        }
        // Audio collapses to the top status island while this asset screen is open, keeping the
        // bottom clear for "Ask Cay AI". Also keeps the player visible above this fullScreenCover.
        .globalAudioOverlay(token: compactToken, forceCompact: true)
        .task {
            viewModel.loadCommodityData()
        }
        // Socket lifecycle, mirroring IndexDetailView. Without these the connection
        // outlives the screen and keeps ticking in the background.
        .onDisappear {
            viewModel.disconnectLivePrice()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in
            viewModel.disconnectLivePrice()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            // Commodities are continuously-quoted futures, so unlike the equity screens
            // there is no market-status gate here — reconnect whenever we come forward.
            viewModel.connectLivePrice()
        }
        .backSwipe { handleBackTapped() }
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(items: shareItems)
        }
        .sheet(isPresented: $showTechnicalAnalysisDetail) {
            if let detailData = viewModel.technicalAnalysisDetailData {
                TechnicalAnalysisDetailView(detailData: detailData)
            } else {
                Group {
                    if viewModel.isTechnicalDetailLoading {
                        ProgressView("Loading technical analysis...")
                    } else {
                        VStack(spacing: 10) {
                            Image(systemName: "chart.bar.xaxis")
                                .font(.system(size: 30))
                                .foregroundColor(AppColors.textMuted)
                            Text("Technical details are unavailable right now.\nPlease try again later.")
                                .font(.subheadline)
                                .foregroundColor(AppColors.textSecondary)
                                .multilineTextAlignment(.center)
                        }
                        .padding()
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(AppColors.background)
                .onAppear { viewModel.fetchTechnicalAnalysisDetail() }
            }
        }
        .fullScreenCover(isPresented: $showSearch) {
            SearchView()
        }
        .aiChatCover(isPresented: $showAIChat, viewModel: chatViewModel)
        // News articles, company websites and whitepapers open INSIDE the app
        // instead of ejecting to Safari (matches Webull / Robinhood).
        .inAppBrowser(link: $viewModel.browserLink)
        .navigationDestination(item: $selectedSearchResult) { selection in
            AssetDetailRouter(selection: selection)
        }
        .onChange(of: viewModel.pendingAIQuery) { oldValue, newValue in
            if let query = newValue {
                // Only present the cover if the conversation was actually SEEDED.
                // `startNewConversation` returns false when a previous turn is still
                // streaming (`guard !isAITyping`), and the result was discarded — so the
                // cover opened on the PREVIOUS conversation and the question the user
                // just typed was silently thrown away. Put it back in the input box
                // instead, so nothing is lost.
                if chatViewModel.startNewConversation(
                    firstMessage: query,
                    stockId: commoditySymbol,
                    context: viewModel.contextForCurrentTab,
                    contextType: .commodity,
                    referenceId: commoditySymbol
                ) {
                    viewModel.pendingAIQuery = nil
                    showAIChat = true
                } else {
                    viewModel.aiInputText = query
                    viewModel.pendingAIQuery = nil
                }
            }
        }
        .onChange(of: viewModel.pendingTickerNavigation) { oldValue, newValue in
            if let selection = newValue {
                // The type travels WITH the symbol now — hardcoding this
                // screen's own "commodity" sent every news related-ticker chip
                // (an equity symbol) to the wrong detail screen.
                selectedSearchResult = selection
                viewModel.pendingTickerNavigation = nil
            }
        }
    }

    // MARK: - Tab Content

    @ViewBuilder
    private var tabContent: some View {
        switch viewModel.selectedTab {
        case .overview:
            if let commodityData = viewModel.commodityData {
                CommodityDetailOverviewContent(
                    commodityData: commodityData,
                    onRelatedCommodityTap: viewModel.handleRelatedCommodityTap
                )
            } else if let errorMessage = viewModel.errorMessage {
                DetailLoadFailureCard(
                    message: errorMessage,
                    isRetrying: viewModel.isLoading,
                    onRetry: { viewModel.loadCommodityData() }
                )
            } else {
                DetailTabSkeleton()
            }

        case .news:
            TickerNewsContent(
                articles: viewModel.newsArticles,
                currentTicker: commoditySymbol,
                isLoading: viewModel.isNewsLoading,
                hasMoreNews: viewModel.hasMoreNews,
                onArticleTap: viewModel.handleNewsArticleTap,
                onExternalLinkTap: viewModel.handleNewsExternalLink,
                onRelatedTickerTap: viewModel.handleNewsTickerTap,
                onLoadMore: viewModel.loadMoreNews
            )
            // Defer AI enrichment to when the News tab is actually viewed.
            .onAppear { viewModel.newsTabAppeared() }

        case .analysis:
            VStack(spacing: AppSpacing.lg) {
                if let technicalData = viewModel.technicalAnalysisData {
                    TechnicalAnalysisSection(
                        technicalData: technicalData,
                        onDetailTapped: {
                            showTechnicalAnalysisDetail = true
                        }
                    )
                } else if !viewModel.isTechnicalLoaded {
                    RoundedRectangle(cornerRadius: 12)
                        .cardFill()
                        .frame(height: 180)
                        .shimmer()
                } else if let message = viewModel.technicalUnavailableMessage {
                    // Loaded, but there is nothing to show. This branch did not exist:
                    // the tab rendered an entirely BLANK screen, which reads as a broken
                    // app rather than as absent data, and offered no way to recover.
                    if viewModel.technicalIsRetryable {
                        InlineRetryNotice(message: message) {
                            Task { await viewModel.retryTechnicalAnalysis() }
                        }
                    } else {
                        // Permanent for this asset — a Try Again button would promise
                        // something that can never succeed.
                        ChartUnavailableView(message: message)
                            .frame(height: 180)
                    }
                }

                // Clears the floating Ask Cay AI bar. Token, not a literal: the bar and its
                // chips are text and grow with the content size (see AppSpacing.aiBarReserve).
                Spacer()
                    .frame(height: AppSpacing.aiBarReserve)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.top, AppSpacing.lg)
        }
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
    CommodityDetailView(commoditySymbol: "GCUSD")
}
