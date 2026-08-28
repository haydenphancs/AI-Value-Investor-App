//
//  ETFDetailView.swift
//  ios
//
//  Main ETF Detail screen displaying exchange-traded fund information
//  Tabs: Overview, News
//

import SwiftUI

struct ETFDetailView: View {
    /// The bell in `TickerDetailHeader` renders ONLY when `onNotificationTapped`
    /// is non-nil. Every detail screen passed `nil`, so it had never rendered —
    /// this is what it was waiting for.
    @State private var showPriceAlerts = false
    /// Shared with Tracking → Alerts and the bell sheet, so the bell badges the
    /// moment a rule exists anywhere. See PriceAlertStore.
    @ObservedObject private var priceAlerts = PriceAlertStore.shared

    @StateObject private var viewModel: ETFDetailViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showSearch = false
    @State private var showShareSheet = false
    @State private var showAIChat = false
    @State private var isTabBarPinned: Bool = false
    @State private var selectedSearchResult: SearchSelection?
    @StateObject private var chatViewModel = ChatViewModel()
    /// Stable token keying this screen's compact-mode request + audio overlay host registration.
    @State private var compactToken = UUID().uuidString

    let etfSymbol: String

    init(etfSymbol: String) {
        self.etfSymbol = etfSymbol
        self._viewModel = StateObject(wrappedValue: ETFDetailViewModel(etfSymbol: etfSymbol))
    }

    // Share sheet items
    // Share sheet items.
    //
    // The body is built OUTSIDE the data binding on purpose. This used to return an EMPTY
    // array while the screen was still loading, which presents UIActivityViewController
    // with zero activity items — a blank share sheet. The symbol alone is a poor share but
    // an honest one, and the download link ShareContent appends is the part that matters.
    private var shareItems: [Any] {
        guard let etfData = viewModel.etfData else {
            return ShareContent.items(etfSymbol)
        }
        let body = """
        \(etfData.name) (\(etfData.symbol))
        \(etfData.formattedPrice) \(etfData.formattedChange) \(etfData.formattedChangePercent)
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
                    hasActiveAlerts: priceAlerts.hasActiveAlerts(ticker: etfSymbol),
                    tickerSymbol: etfSymbol,
                    tickerPrice: isTabBarPinned ? viewModel.etfData?.formattedPrice : nil
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
                    if let etfData = viewModel.headerData {
                        // ETF Price Header
                        TickerPriceHeader(
                            companyName: etfData.name,
                            symbol: etfData.symbol,
                            price: etfData.formattedPrice,
                            priceChange: etfData.formattedChange,
                            priceChangePercent: etfData.formattedChangePercent,
                            isPositive: etfData.isPositive,
                            marketStatus: etfData.marketStatus
                        )
                        .padding(.top, AppSpacing.sm)

                        // Chart
                        TickerChartView(
                            pricePoints: etfData.chartPricePoints,
                            isPositive: etfData.isPositive,
                            selectedRange: $viewModel.selectedChartRange,
                            chartSettings: viewModel.chartSettings,
                            assetContext: .etf,
                            chartDataVersion: viewModel.chartDataVersion,
                            previousClose: etfData.previousClose
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
                            onRetry: { viewModel.loadETFData() }
                        )
                    } else {
                        DetailHeaderChartSkeleton(symbol: etfSymbol)
                            .padding(.top, AppSpacing.sm)
                    }
                } tabs: {
                    ETFDetailTabBar(selectedTab: $viewModel.selectedTab)
                } content: {
                    tabContent
                }
            }

            // Bottom AI Chat Bar
            ETFDetailAIBar(
                inputText: $viewModel.aiInputText,
                etfSymbol: etfSymbol,
                suggestions: viewModel.aiSuggestions,
                onSuggestionTap: viewModel.handleSuggestionTap,
                onSend: viewModel.handleAISend
            )

            // No blocking LoadingOverlay — it covered the header + ate the back tap.
            // The price+chart area shows a shimmer skeleton until data loads.
        }
        .navigationBarHidden(true)
        .sheet(isPresented: $showPriceAlerts) {
            PriceAlertsSheet(ticker: etfSymbol, assetType: "etf")
        }
        // Audio collapses to the top status island while this asset screen is open, keeping the
        // bottom clear for "Ask Cay AI". Also keeps the player visible above this fullScreenCover.
        .globalAudioOverlay(token: compactToken, forceCompact: true)
        .task {
            viewModel.loadETFData()
            // Lazy on purpose. Hooking AppState.onAuthenticated would add a request
            // to every cold launch of a signed-in user for a feature most never use;
            // one ≤40-row response here serves the bell on every screen for 5 min.
            Task { await priceAlerts.loadIfStale() }
        }
        .onDisappear {
            viewModel.disconnectLivePrice()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in
            viewModel.disconnectLivePrice()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            if let status = viewModel.etfData?.marketStatus,
               MarketHoursUtil.shouldStreamLivePrice(for: status) {
                viewModel.connectLivePrice()
            }
        }
        .backSwipe { handleBackTapped() }
        .sheet(isPresented: $showShareSheet) {
            ShareSheet(items: shareItems)
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
                print("[ETFDetailView] Opening AI chat for \(etfSymbol) with query: \(query)")
                // Only present the cover if the conversation was actually SEEDED.
                // `startNewConversation` returns false when a previous turn is still
                // streaming (`guard !isAITyping`), and the result was discarded — so the
                // cover opened on the PREVIOUS conversation and the question the user
                // just typed was silently thrown away. Put it back in the input box
                // instead, so nothing is lost.
                if chatViewModel.startNewConversation(firstMessage: query, stockId: etfSymbol, context: viewModel.contextForCurrentTab, contextType: .etf, referenceId: etfSymbol) {
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
                // screen's own "etf" sent every news related-ticker chip
                // (an equity symbol) to the wrong detail screen.
                selectedSearchResult = selection
                viewModel.pendingTickerNavigation = nil
            }
        }
        .fullScreenCover(isPresented: $showSearch) {
            SearchView()
        }
    }

    // MARK: - Tab Content

    @ViewBuilder
    private var tabContent: some View {
        switch viewModel.selectedTab {
        case .overview:
            if let etfData = viewModel.etfData {
                ETFDetailOverviewContent(
                    etfData: etfData,
                    onDeepResearchTap: {
                        handleDeepResearchTap()
                    },
                    onWebsiteTap: viewModel.handleWebsiteTap,
                    onRelatedETFTap: viewModel.handleRelatedETFTap
                )
            } else if let errorMessage = viewModel.errorMessage {
                DetailLoadFailureCard(
                    message: errorMessage,
                    isRetrying: viewModel.isLoading,
                    onRetry: { viewModel.loadETFData() }
                )
            } else {
                DetailTabSkeleton()
            }
        case .news:
            TickerNewsContent(
                articles: viewModel.newsArticles,
                currentTicker: etfSymbol,
                isLoading: viewModel.isNewsLoading,
                hasMoreNews: viewModel.hasMoreNews,
                onArticleTap: viewModel.handleNewsArticleTap,
                onExternalLinkTap: viewModel.handleNewsExternalLink,
                onRelatedTickerTap: viewModel.handleNewsTickerTap,
                onLoadMore: { viewModel.loadMoreNews() }
            )
            // Defer AI enrichment to when the News tab is actually viewed.
            .onAppear { viewModel.newsTabAppeared() }
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

    /// A fund is not a company, so this goes to Cay AI — never to the Research tab.
    ///
    /// The research report is built from an FMP company profile, income statements and peers
    /// (`ticker_report_data_collector` RAISES without a profile), and the report target picker
    /// says so outright: "ETFs and crypto aren't supported here". Chat, by contrast, already has
    /// a grounded ETF branch in `ChatContextResolver` that nothing was using.
    ///
    /// This handler was previously `if let onNavigateToResearch { … }` with NO else, and no call
    /// site ever passed that closure — so the button was inert in every shipped build.
    ///
    /// Guarded on the return value for the same reason as `pendingAIQuery` above: `false` means
    /// nothing was seeded, and presenting anyway shows the PREVIOUS conversation.
    private func handleDeepResearchTap() {
        let seeded = chatViewModel.startNewConversation(
            firstMessage: "Give me a comprehensive Deep Analysis of \(etfSymbol). Cover what it holds, how concentrated it is, cost, how it has tracked its benchmark, and the key risks.",
            stockId: etfSymbol,
            context: viewModel.contextForCurrentTab,
            contextType: .etf,
            referenceId: etfSymbol
        )
        if seeded { showAIChat = true }
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
    ETFDetailView(etfSymbol: "SPY")
}
