//
//  CryptoDetailView.swift
//  ios
//
//  Main Crypto Detail screen displaying cryptocurrency information
//  Tabs: Overview, News, Analysis
//

import SwiftUI

struct CryptoDetailView: View {
    /// The bell in `TickerDetailHeader` renders ONLY when `onNotificationTapped`
    /// is non-nil. Every detail screen passed `nil`, so it had never rendered —
    /// this is what it was waiting for.
    @State private var showPriceAlerts = false
    /// Shared with Tracking → Alerts and the bell sheet, so the bell badges the
    /// moment a rule exists anywhere. See PriceAlertStore.
    @ObservedObject private var priceAlerts = PriceAlertStore.shared

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

    init(cryptoSymbol: String) {
        // Bare form everywhere on screen; `CryptoSymbol.pair` builds the FMP
        // pair where one is needed. Home passes "BTCUSD", search passes "BTC".
        self.cryptoSymbol = CryptoSymbol.bare(cryptoSymbol)
        self._viewModel = StateObject(wrappedValue: CryptoDetailViewModel(cryptoSymbol: CryptoSymbol.bare(cryptoSymbol)))
    }

    // Share sheet items
    // Share sheet items.
    //
    // The body is built OUTSIDE the data binding on purpose. This used to return an EMPTY
    // array while the screen was still loading, which presents UIActivityViewController
    // with zero activity items — a blank share sheet. The symbol alone is a poor share but
    // an honest one, and the download link ShareContent appends is the part that matters.
    private var shareItems: [Any] {
        guard let cryptoData = viewModel.cryptoData else {
            return ShareContent.items(cryptoSymbol)
        }
        let body = """
        \(cryptoData.name) (\(cryptoData.symbol))
        \(cryptoData.formattedPrice) \(cryptoData.formattedChange) \(cryptoData.formattedChangePercent)
        """
        return ShareContent.items(body)
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
                    // Bell glyph must stay identical to PriceAlertRuleRow — see
                    // TickerDetailHeader.hasActiveAlerts.
                    onNotificationTapped: { showPriceAlerts = true },
                    onFavoriteTapped: viewModel.toggleFavorite,
                    onMoreTapped: handleShareTapped,
                    isFavorite: viewModel.isFavorite,
                    hasActiveAlerts: priceAlerts.hasActiveAlerts(ticker: cryptoSymbol),
                    tickerSymbol: cryptoSymbol,
                    tickerPrice: isTabBarPinned ? viewModel.cryptoData?.formattedPrice : nil
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
                    if let cryptoData = viewModel.headerData {
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
                    } else if let errorMessage = viewModel.errorMessage {
                        // The ViewModel has been writing this message all along and
                        // nothing rendered it: on failure the screen fell through to
                        // a skeleton that never resolved, so the user sat on a
                        // permanent shimmer with no error and no retry.
                        DetailLoadFailureCard(
                            message: errorMessage,
                            isRetrying: viewModel.isLoading,
                            onRetry: { viewModel.loadCryptoData() }
                        )
                    } else {
                        DetailHeaderChartSkeleton(symbol: cryptoSymbol)
                            .padding(.top, AppSpacing.sm)
                    }
                } tabs: {
                    CryptoDetailTabBar(selectedTab: $viewModel.selectedTab)
                } content: {
                    tabContent
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
        .navigationBarHidden(true)
        .sheet(isPresented: $showPriceAlerts) {
            PriceAlertsSheet(ticker: cryptoSymbol, assetType: "crypto")
        }
        // Audio collapses to the top status island while this asset screen is open, keeping the
        // bottom clear for "Ask Cay AI". Also keeps the player visible above this fullScreenCover.
        .globalAudioOverlay(token: compactToken, forceCompact: true)
        .task {
            viewModel.loadCryptoData()
            // Lazy on purpose. Hooking AppState.onAuthenticated would add a request
            // to every cold launch of a signed-in user for a feature most never use;
            // one ≤40-row response here serves the bell on every screen for 5 min.
            Task { await priceAlerts.loadIfStale() }
        }
        .onDisappear {
            viewModel.disconnectLivePrice()
        }
        .backSwipe { handleBackTapped() }
        .confirmationDialog("Options", isPresented: $showMoreOptions) {
            Button("Share") {
                handleShare()
            }
            Button("Add to Watchlist") {
                handleAddToWatchlist()
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
                    stockId: CryptoSymbol.pair(cryptoSymbol),
                    context: viewModel.contextForCurrentTab,
                    contextType: .crypto,
                    referenceId: cryptoSymbol
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
                // screen's own "crypto" sent every news related-ticker chip
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
            } else if let errorMessage = viewModel.errorMessage {
                DetailLoadFailureCard(
                    message: errorMessage,
                    isRetrying: viewModel.isLoading,
                    onRetry: { viewModel.loadCryptoData() }
                )
            } else {
                DetailTabSkeleton()
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
            // Defer AI enrichment to when the News tab is actually viewed.
            .onAppear { viewModel.newsTabAppeared() }
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

    /// Crypto stays on Cay AI: the research report needs an FMP company profile and income
    /// statements, which a coin has none of.
    ///
    /// Guarded on the return value — `false` means `startNewConversation` bailed on its
    /// `guard !isAITyping` and seeded NOTHING, so presenting would show the previous
    /// conversation. Same guard the `pendingAIQuery` handler already uses.
    private func handleDeepResearchTap() {
        let seeded = chatViewModel.startNewConversation(
            firstMessage: "Give me a comprehensive Deep Analysis of \(cryptoSymbol). Analyze the current price action, market position, key risks, and outlook.",
            stockId: CryptoSymbol.pair(cryptoSymbol),
            context: viewModel.contextForCurrentTab,
            contextType: .crypto,
            referenceId: cryptoSymbol
        )
        if seeded { showAIChat = true }
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

    private func handleCompare() {
        print("Compare \(cryptoSymbol)")
    }
}

// MARK: - Preview
#Preview {
    CryptoDetailView(cryptoSymbol: "ETH")
}
