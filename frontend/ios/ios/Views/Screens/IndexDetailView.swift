//
//  IndexDetailView.swift
//  ios
//
//  Main Index Detail screen displaying index information
//

import SwiftUI

struct IndexDetailView: View {
    @StateObject private var viewModel: IndexDetailViewModel
    @StateObject private var chatViewModel = ChatViewModel()
    @Environment(\.dismiss) private var dismiss
    @State private var showSearch = false
    @State private var showShareSheet = false
    @State private var showUpgradesDowngrades = false
    @State private var showTechnicalAnalysisDetail = false
    @State private var showAIChat = false
    @State private var isTabBarPinned: Bool = false
    @State private var selectedSearchResult: SearchSelection?

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
                    onNotificationTapped: viewModel.handleNotificationTap,
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
                                pricePoints: indexData.chartPricePoints,
                                isPositive: indexData.isPositive,
                                selectedRange: $viewModel.selectedChartRange,
                                chartSettings: viewModel.chartSettings,
                                assetContext: .index,
                                chartDataVersion: viewModel.chartDataVersion,
                                chartEventDates: viewModel.chartEventDates
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
        .gesture(
            DragGesture()
                .onEnded { value in
                    if value.translation.width > 100 {
                        handleBackTapped()
                    }
                }
        )
        .navigationDestination(item: $selectedSearchResult) { selection in
            AssetDetailRouter(selection: selection)
        }
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
        }
        .sheet(isPresented: $showAIChat) {
            NavigationStack {
                ChatConversationView(viewModel: chatViewModel)
                    .navigationTitle("Ask Cay AI")
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .topBarLeading) {
                            Button("Close") { showAIChat = false }
                        }
                    }
            }
            .preferredColorScheme(.dark)
        }
        .onChange(of: viewModel.pendingAIQuery) { oldValue, newValue in
            if let query = newValue {
                print("🤖 IndexDetailView: Opening AI chat for \(indexSymbol) with query: \(query)")
                chatViewModel.startNewConversation(
                    firstMessage: query,
                    stockId: indexSymbol,
                    context: viewModel.contextForCurrentTab
                )
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
            if let indexData = viewModel.indexData {
                IndexDetailOverviewContent(
                    indexData: indexData,
                    onAIAnalystTap: {
                        chatViewModel.startNewConversation(
                            firstMessage: "Give me a comprehensive Market Deep Dive. Analyze the current market valuation, sector rotation patterns, and macroeconomic risks. Include actionable takeaways and what to watch this week.",
                            stockId: indexSymbol,
                            context: viewModel.contextForCurrentTab
                        )
                        showAIChat = true
                    },
                    onWebsiteTap: viewModel.handleWebsiteTap
                )
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
