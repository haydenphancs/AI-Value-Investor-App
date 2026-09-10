//
//  TickerDetailView.swift
//  ios
//
//  Main Ticker Detail screen displaying stock information
//

import SwiftUI

struct TickerDetailView: View {
    /// The bell in `TickerDetailHeader` renders ONLY when `onNotificationTapped`
    /// is non-nil. Every detail screen passed `nil`, so it had never rendered —
    /// this is what it was waiting for.
    @State private var showPriceAlerts = false
    /// Shared with Tracking → Alerts and the bell sheet, so the bell badges the
    /// moment a rule exists anywhere. See PriceAlertStore.
    @ObservedObject private var priceAlerts = PriceAlertStore.shared

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

    /// Today's rotating question set for the AI bar.
    ///
    /// Read HERE rather than in the ViewModel: `ChatStartersStore` is `@Observable`, and
    /// only a read inside a `View` body registers the dependency that redraws the row when
    /// the day's fetch lands.
    ///
    /// ⚠️ Only the DEFAULT tab rotates. `.financials`, `.analysis`, `.news` and `.holders`
    /// each carry a hand-written set aimed at what that tab is showing ("Is margin
    /// improving?" under Financials), which is better targeted than any general rotation —
    /// so those are left alone deliberately.
    private var rotatingAISuggestions: [TickerAISuggestion] {
        guard viewModel.usesDefaultSuggestions else { return viewModel.aiSuggestions }
        let rotated = ChatStartersStore.shared.detailStarters(for: .ticker, symbol: tickerSymbol)
        guard !rotated.isEmpty else { return viewModel.aiSuggestions }
        return rotated.map { TickerAISuggestion(text: $0) }
    }

    /// The Research route is parked on AppState rather than injected as a closure — see
    /// `AppState.pendingResearchTicker`. The closure form only ever worked from Tracking.
    @Environment(AppState.self) private var appState

    /// `destination` is the notification deep link: which tab, and which sub-tab inside
    /// it. Defaults to `.default` so every non-notification call site is unchanged.
    init(
        tickerSymbol: String,
        destination: TickerDestination = .default
    ) {
        self.tickerSymbol = tickerSymbol
        self._viewModel = StateObject(wrappedValue: TickerDetailViewModel(
            tickerSymbol: tickerSymbol,
            initialTab: destination.tab ?? .overview,
            initialHoldersSection: destination.section
        ))
    }
    
    // Share sheet items.
    //
    // The body is built OUTSIDE the data binding on purpose. This used to return an EMPTY
    // array while the screen was still loading, which presents UIActivityViewController
    // with zero activity items — a blank share sheet. The symbol alone is a poor share but
    // an honest one, and the download link ShareContent appends is the part that matters.
    private var shareItems: [Any] {
        guard let tickerData = viewModel.tickerData else {
            return ShareContent.items(tickerSymbol)
        }
        let body = """
        \(tickerData.companyName) (\(tickerData.symbol))
        \(tickerData.formattedPrice) \(tickerData.formattedChange) \(tickerData.formattedChangePercent)
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
                // Navigation Header (always visible - back, ticker symbol, search, bell, star, more)
                // Shows price alongside ticker symbol when tab bar is pinned
                TickerDetailHeader(
                    onBackTapped: handleBackTapped,
                    onSearchTapped: handleSearchTapped,
                    // Bell glyph must stay identical to PriceAlertRuleRow — see
                    // TickerDetailHeader.hasActiveAlerts.
                    onNotificationTapped: { showPriceAlerts = true },
                    onFavoriteTapped: { viewModel.toggleFavorite() },
                    onMoreTapped: handleShareTapped,
                    isFavorite: viewModel.isFavorite,
                    hasActiveAlerts: priceAlerts.hasActiveAlerts(ticker: tickerSymbol),
                    tickerSymbol: tickerSymbol,
                    tickerPrice: isTabBarPinned ? viewModel.tickerData?.formattedPrice : nil
                )

                // Eager container + an overlay-pinned tab bar. The LazyVStack that used
                // to be here re-walked its predecessors every frame to place the pinned
                // section header, and a live-price tick resizes its first child — so the
                // walk restarted continuously while scrolling. See DetailScrollContainer.
                DetailScrollContainer(
                    isTabBarPinned: $isTabBarPinned,
                    onRefresh: { await viewModel.refresh() }
                ) {
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
                    } else if let errorMessage = viewModel.errorMessage {
                        DetailLoadFailureCard(
                            message: errorMessage,
                            isRetrying: viewModel.isLoading,
                            onRetry: { viewModel.loadTickerData() }
                        )
                    } else {
                        DetailHeaderChartSkeleton(symbol: tickerSymbol)
                            .padding(.top, AppSpacing.sm)
                    }
                } tabs: {
                    TickerDetailTabBar(selectedTab: $viewModel.selectedTab)
                } content: {
                    tabContent
                }
            }

            // Bottom AI Chat Bar (always visible)
            TickerDetailAIBar(
                inputText: $viewModel.aiInputText,
                tickerSymbol: tickerSymbol,
                suggestions: rotatingAISuggestions,
                onSuggestionTap: viewModel.handleSuggestionTap,
                onSend: viewModel.handleAISend
            )

            // NOTE: no blocking LoadingOverlay — it covered the header and ate the
            // back tap. The header/tabs/AI bar render instantly; the price+chart
            // area shows a shimmer skeleton (see the content gate above) until the
            // fast core, then the full overview, arrives.
        }
        .navigationBarHidden(true)
        .sheet(isPresented: $showPriceAlerts) {
            PriceAlertsSheet(ticker: tickerSymbol, assetType: "stock")
        }
        // Audio collapses to the top status island while this stock screen is open, keeping the
        // bottom clear for "Ask Cay AI". Also keeps the player visible above this fullScreenCover.
        .globalAudioOverlay(token: compactToken, forceCompact: true)
        .task {
            // Today's starter questions. One shared request per ET day across all six
            // surfaces that show a chip row — the store joins, caches and date-gates.
            Task { await ChatStartersStore.shared.prefetch() }
            viewModel.loadTickerData()
            // Lazy on purpose. Hooking AppState.onAuthenticated would add a request
            // to every cold launch of a signed-in user for a feature most never use;
            // one ≤40-row response here serves the bell on every screen for 5 min.
            Task { await priceAlerts.loadIfStale() }
        }
        .onDisappear {
            viewModel.stopLivePriceUpdates()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.willResignActiveNotification)) { _ in
            viewModel.stopLivePriceUpdates()
        }
        .onReceive(NotificationCenter.default.publisher(for: UIApplication.didBecomeActiveNotification)) { _ in
            if let status = viewModel.tickerData?.marketStatus,
               MarketHoursUtil.shouldStreamLivePrice(for: status) {
                viewModel.startLivePriceUpdates()
            }
        }
        .backSwipe { handleBackTapped() }
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
        .navigationDestination(item: $selectedSearchResult) { selection in
            AssetDetailRouter(selection: selection)
        }
        .aiChatCover(isPresented: $showAIChat, viewModel: chatViewModel)
        // News articles, company websites and whitepapers open INSIDE the app
        // instead of ejecting to Safari (matches Webull / Robinhood).
        .inAppBrowser(link: $viewModel.browserLink)
        .onChange(of: viewModel.pendingAIQuery) { oldValue, newValue in
            if let query = newValue {
                // Only present the cover if the conversation was actually SEEDED.
                // `startNewConversation` returns false when a previous turn is still
                // streaming (`guard !isAITyping`), and the result was discarded — so the
                // cover opened on the PREVIOUS conversation and the question the user
                // just typed was silently thrown away. Put it back in the input box
                // instead, so nothing is lost.
                if chatViewModel.startNewConversation(firstMessage: query, stockId: tickerSymbol, context: viewModel.contextForCurrentTab, contextType: .stock, referenceId: tickerSymbol) {
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
            if let tickerData = viewModel.tickerData {
                TickerDetailOverviewContent(
                    tickerData: tickerData,
                    onDeepResearchTap: {
                        handleDeepResearchTap()
                    },
                    onWebsiteTap: viewModel.handleWebsiteTap,
                    onRelatedTickerTap: viewModel.handleRelatedTickerTap
                )
            } else if let errorMessage = viewModel.errorMessage {
                // Settled and failed. Without this the Overview tab body was blank —
                // indistinguishable from "still loading" and from "no data exists".
                DetailLoadFailureCard(
                    message: errorMessage,
                    isRetrying: viewModel.isLoading,
                    onRetry: { viewModel.loadTickerData() }
                )
            } else {
                DetailTabSkeleton()
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
            // Enrichment is deferred to here: this view is in the tree only when
            // the News tab is selected, so AI spend happens when news is read,
            // not on every ticker open.
            .onAppear { viewModel.newsTabAppeared() }
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
                isLoaded: viewModel.isFinancialsLoaded,
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
                    holdersData: holdersData,
                    initialActivitiesTab: viewModel.initialHoldersSection
                )
            } else if !viewModel.isHoldersLoaded {
                placeholderContent(title: "Holders", description: "Loading holders data...")
            } else {
                // Settled with no data. Distinguishing this from "still loading"
                // is the whole point of `isHoldersLoaded`: a failed fetch used to
                // sit on "Loading holders data..." forever with no way to retry.
                holdersUnavailableContent(
                    message: viewModel.holdersError
                        ?? "Ownership data isn't available for this symbol."
                )
            }
        }
    }

    private func holdersUnavailableContent(message: String) -> some View {
        VStack(spacing: AppSpacing.lg) {
            Image(systemName: "chart.bar.doc.horizontal")
                .font(AppTypography.iconHero)
                .foregroundColor(AppColors.textMuted)

            Text("Holders")
                .font(AppTypography.titleCompact)
                .foregroundColor(AppColors.textPrimary)

            Text(message)
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)

            Button {
                Task { await viewModel.reloadHolders() }
            } label: {
                Text("Try Again")
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.sm)
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(AppColors.primaryBlue.opacity(0.5), lineWidth: 1)
                    )
            }

            Spacer()
                .frame(height: 150)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, AppSpacing.xxxl)
        .padding(.horizontal, AppSpacing.lg)
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

    /// A stock is the ONE asset class the research report supports, so this always goes to the
    /// Research tab — never to chat.
    ///
    /// It used to go to Research only when an `onNavigateToResearch` closure had been injected,
    /// and exactly one of ~14 call sites injected it (Tracking). Reaching NOC from Home, Search,
    /// Themes, Signals or a notification hit the `else` and opened a chat instead, which is what
    /// the TestFlight report described. Parking the intent on AppState works from all of them.
    ///
    /// Getting to the Research tab is only half of it — the user has to be able to SEE it.
    ///
    /// `dismiss()` closes exactly ONE presentation level, and this screen is presented from ~14
    /// places of which only the Tracking tab reaches it by a push. Everywhere else it sits under
    /// one or two `fullScreenCover`s (Home has no root `NavigationStack`, so theme → ticker,
    /// signals → ticker and search → ticker are all two covers deep). Dismissing one level
    /// switched the tab BEHIND a cover that was still on screen, which is indistinguishable from
    /// the button doing nothing — the second TestFlight report on this button, after the first
    /// one fixed the route.
    ///
    /// So: park the intent, then ask every tab root to take its own presentations down.
    ///
    /// ⚠️ **No `dismiss()` here, deliberately.** It looks like it belongs — it was here — and it
    /// is both redundant and actively harmful:
    ///
    ///  * Redundant, because the reset already covers BOTH shapes. A presented ticker screen
    ///    goes when its root's cover binding is nil'd; a PUSHED one (Tracking, the only tab that
    ///    pushes) goes when that tab's `.navigationDestination` item is nil'd, which its reset
    ///    block does for all four.
    ///  * Harmful, because it is a SECOND dismissal request, issued from inside, in the same
    ///    runloop as the root's. UIKit refuses a dismissal on a controller whose transition is
    ///    already in flight, and the request that loses is the outer one — stranding e.g.
    ///    `ThemeDetailView` on screen with its binding already nil, which is unrecoverable
    ///    (re-tapping the same card assigns an equal `item`, so it is not a change).
    ///
    /// Order is load-bearing: park the ticker FIRST so `ContentView` has switched to Research
    /// before the covers animate away, leaving no frame where the user sees Home.
    private func handleDeepResearchTap() {
        appState.pendingResearchTicker = tickerSymbol
        appState.dismissAllPresentations()
    }

    private func handleSearchTapped() {
        showSearch = true
    }

    private func handleShareTapped() {
        showShareSheet = true
    }
}

// MARK: - Tab Bar Position Preference Key

// MARK: - Preview
#Preview {
    TickerDetailView(tickerSymbol: "AAPL")
}
