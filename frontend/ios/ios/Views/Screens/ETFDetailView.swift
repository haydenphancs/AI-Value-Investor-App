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
    private var shareItems: [Any] {
        var items: [Any] = []

        if let etfData = viewModel.etfData {
            let shareText = """
            \(etfData.name) (\(etfData.symbol))
            \(etfData.formattedPrice) \(etfData.formattedChange) \(etfData.formattedChangePercent)

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
                    tickerSymbol: etfSymbol,
                    tickerPrice: isTabBarPinned ? viewModel.etfData?.formattedPrice : nil
                )

                // Scrollable Content with pinned tab bar
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
                        // Content above tab bar (scrolls away)
                        if let etfData = viewModel.etfData {
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
                                ETFDetailTabBar(selectedTab: $viewModel.selectedTab)
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
