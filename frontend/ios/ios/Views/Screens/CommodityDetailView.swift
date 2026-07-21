//
//  CommodityDetailView.swift
//  ios
//
//  Main Commodity Detail screen displaying commodity information
//  Tabs: Overview, News, Analysis
//

import SwiftUI

struct CommodityDetailView: View {
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
    var onNavigateToResearch: (() -> Void)?

    init(commoditySymbol: String, onNavigateToResearch: (() -> Void)? = nil) {
        self.commoditySymbol = commoditySymbol
        self.onNavigateToResearch = onNavigateToResearch
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
                    onNotificationTapped: viewModel.handleNotificationTap,
                    onFavoriteTapped: viewModel.toggleFavorite,
                    onMoreTapped: handleShareTapped,
                    isFavorite: viewModel.isFavorite,
                    tickerSymbol: commoditySymbol,
                    tickerPrice: isTabBarPinned ? viewModel.commodityData?.formattedPrice : nil
                )

                // Scrollable Content with pinned tab bar
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: 0, pinnedViews: [.sectionHeaders]) {
                        // Content above tab bar (scrolls away)
                        if let commodityData = viewModel.commodityData {
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
                                CommodityDetailTabBar(selectedTab: $viewModel.selectedTab)
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
        .preferredColorScheme(.dark)
        .navigationBarHidden(true)
        // Audio collapses to the top status island while this asset screen is open, keeping the
        // bottom clear for "Ask Cay AI". Also keeps the player visible above this fullScreenCover.
        .globalAudioOverlay(token: compactToken, forceCompact: true)
        .task {
            viewModel.loadCommodityData()
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
                .preferredColorScheme(.dark)
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
                chatViewModel.startNewConversation(
                    firstMessage: query,
                    stockId: commoditySymbol,
                    context: viewModel.contextForCurrentTab,
                    contextType: .commodity,
                    referenceId: commoditySymbol
                )
                viewModel.pendingAIQuery = nil
                showAIChat = true
            }
        }
        .onChange(of: viewModel.pendingTickerNavigation) { oldValue, newValue in
            if let ticker = newValue {
                selectedSearchResult = SearchSelection(symbol: ticker, type: "commodity")
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
                        .fill(AppColors.cardBackground)
                        .frame(height: 180)
                        .shimmer()
                }

                Spacer()
                    .frame(height: 120)
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
