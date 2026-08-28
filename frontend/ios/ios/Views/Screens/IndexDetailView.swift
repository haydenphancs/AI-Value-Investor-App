//
//  IndexDetailView.swift
//  ios
//
//  Main Index Detail screen displaying index information
//

import SwiftUI

struct IndexDetailView: View {
    /// The bell in `TickerDetailHeader` renders ONLY when `onNotificationTapped`
    /// is non-nil. Every detail screen passed `nil`, so it had never rendered —
    /// this is what it was waiting for.
    @State private var showPriceAlerts = false

    @StateObject private var viewModel: IndexDetailViewModel
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
                    tickerSymbol: indexSymbol,
                    tickerPrice: isTabBarPinned ? viewModel.indexData?.formattedPrice : nil
                )

                // Error banner (shown when using fallback data)
                if let error = viewModel.errorMessage {
                    HStack(spacing: AppSpacing.sm) {
                        Image(systemName: "wifi.slash")
                            .font(.caption)
                        Text(error)
                            .font(AppTypography.caption)
                    }
                    .foregroundColor(AppColors.alertOrange)
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.xs)
                }

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
                    if let indexData = viewModel.headerData {
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
                            pricePoints: indexData.chartPricePoints,
                            isPositive: indexData.isPositive,
                            selectedRange: $viewModel.selectedChartRange,
                            chartSettings: viewModel.chartSettings,
                            assetContext: .index,
                            chartDataVersion: viewModel.chartDataVersion,
                            chartEventDates: viewModel.chartEventDates,
                            previousClose: indexData.previousClose
                        )
                        .padding(.top, AppSpacing.lg)
                    } else if let errorMessage = viewModel.errorMessage {
                        // The ViewModel has been writing this message all along and
                        // nothing rendered it: on failure the screen fell through to
                        // a skeleton that never resolved, so the user sat on a
                        // permanent shimmer with no error and no retry. The ETF screen
                        // got this fix; this one did not — which matters more here,
                        // because `snapshots_data` is a deep REQUIRED object graph and
                        // any drift in it fails the whole decode.
                        DetailLoadFailureCard(
                            message: errorMessage,
                            isRetrying: viewModel.isLoading,
                            onRetry: { viewModel.loadIndexData() }
                        )
                    } else {
                        DetailHeaderChartSkeleton(symbol: indexSymbol)
                            .padding(.top, AppSpacing.sm)
                    }
                } tabs: {
                    IndexDetailTabBar(selectedTab: $viewModel.selectedTab)
                } content: {
                    tabContent
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

            // No blocking LoadingOverlay — it covered the header + ate the back tap.
            // The price+chart area shows a shimmer skeleton until data loads.
        }
        .navigationBarHidden(true)
        .sheet(isPresented: $showPriceAlerts) {
            PriceAlertsSheet(ticker: indexSymbol, assetType: "index")
        }
        // Audio collapses to the top status island while this asset screen is open, keeping the
        // bottom clear for "Ask Cay AI". Also keeps the player visible above this fullScreenCover.
        .globalAudioOverlay(token: compactToken, forceCompact: true)
        .task {
            viewModel.loadIndexData()
        }
        .onDisappear {
            viewModel.disconnectLivePrice()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in
            viewModel.disconnectLivePrice()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            if let status = viewModel.indexData?.marketStatus,
               MarketHoursUtil.shouldStreamLivePrice(for: status) {
                viewModel.connectLivePrice()
            }
        }
        .backSwipe { handleBackTapped() }
        .navigationDestination(item: $selectedSearchResult) { selection in
            AssetDetailRouter(selection: selection)
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
        }
        .aiChatCover(isPresented: $showAIChat, viewModel: chatViewModel)
        // News articles, company websites and whitepapers open INSIDE the app
        // instead of ejecting to Safari (matches Webull / Robinhood).
        .inAppBrowser(link: $viewModel.browserLink)
        .onChange(of: viewModel.pendingAIQuery) { oldValue, newValue in
            if let query = newValue {
                print("🤖 IndexDetailView: Opening AI chat for \(indexSymbol) with query: \(query)")
                // Only present the cover if the conversation was actually SEEDED.
                // `startNewConversation` returns false when a previous turn is still
                // streaming (`guard !isAITyping`), and the result was discarded — so the
                // cover opened on the PREVIOUS conversation and the question the user
                // just typed was silently thrown away. Put it back in the input box
                // instead, so nothing is lost.
                if chatViewModel.startNewConversation(
                    firstMessage: query,
                    stockId: indexSymbol,
                    context: viewModel.contextForCurrentTab,
                    contextType: .index,
                    referenceId: indexSymbol
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
            if let indexData = viewModel.indexData {
                IndexDetailOverviewContent(
                    indexData: indexData,
                    onAIAnalystTap: {
                        // Guarded on the return value — `false` means nothing was seeded
                        // (`guard !isAITyping`) and presenting would show the PREVIOUS
                        // conversation. Same guard the `pendingAIQuery` handler uses.
                        if chatViewModel.startNewConversation(
                            firstMessage: "Give me a comprehensive Market Deep Dive of \(indexSymbol). Analyze the current valuation, breadth and sector rotation, and the macro risks. Include what to watch this week.",
                            stockId: indexSymbol,
                            context: viewModel.contextForCurrentTab,
                            contextType: .index,
                            referenceId: indexSymbol
                        ) {
                            showAIChat = true
                        }
                    },
                    onWebsiteTap: viewModel.handleWebsiteTap
                )
            } else if let errorMessage = viewModel.errorMessage {
                DetailLoadFailureCard(
                    message: errorMessage,
                    isRetrying: viewModel.isLoading,
                    onRetry: { viewModel.loadIndexData() }
                )
            } else {
                DetailTabSkeleton()
            }
        case .news:
            TickerNewsContent(
                articles: viewModel.newsArticles,
                currentTicker: indexSymbol,
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
