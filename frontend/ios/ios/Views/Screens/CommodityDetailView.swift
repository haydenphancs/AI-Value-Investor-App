//
//  CommodityDetailView.swift
//  ios
//
//  Main Commodity Detail screen displaying commodity information
//  Tabs: Overview, News
//

import SwiftUI

struct CommodityDetailView: View {
    @StateObject private var viewModel: CommodityDetailViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showSearch = false
    @State private var showShareSheet = false
    @State private var showAIChat = false
    @State private var isTabBarPinned: Bool = false

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
                                chartData: commodityData.chartData,
                                isPositive: commodityData.isPositive,
                                selectedRange: $viewModel.selectedChartRange
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

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .preferredColorScheme(.dark)
        .navigationBarHidden(true)
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
        .fullScreenCover(isPresented: $showSearch) {
            SearchView()
                .preferredColorScheme(.dark)
        }
        .fullScreenCover(isPresented: $showAIChat) {
            NavigationView {
                ZStack {
                    AppColors.background
                        .ignoresSafeArea()
                    ChatTabView(initialPrompt: "Tell me about \(commoditySymbol)")
                }
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) {
                        Button("Close") {
                            showAIChat = false
                        }
                        .foregroundColor(AppColors.primaryBlue)
                    }
                }
            }
            .environmentObject(AudioManager.shared)
            .preferredColorScheme(.dark)
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
                onArticleTap: viewModel.handleNewsArticleTap,
                onExternalLinkTap: viewModel.handleNewsExternalLink,
                onRelatedTickerTap: viewModel.handleNewsTickerTap
            )
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
