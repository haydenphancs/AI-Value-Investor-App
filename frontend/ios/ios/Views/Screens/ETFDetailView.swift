//
//  ETFDetailView.swift
//  ios
//
//  Main ETF Detail screen displaying exchange-traded fund information
//  Tabs: Overview, News
//

import SwiftUI

struct ETFDetailView: View {
    @StateObject private var viewModel: ETFDetailViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showSearch = false
    @State private var showShareSheet = false
    @State private var isTabBarPinned: Bool = false

    let etfSymbol: String
    var onNavigateToResearch: (() -> Void)?

    init(etfSymbol: String, onNavigateToResearch: (() -> Void)? = nil) {
        self.etfSymbol = etfSymbol
        self.onNavigateToResearch = onNavigateToResearch
        self._viewModel = StateObject(wrappedValue: ETFDetailViewModel(etfSymbol: etfSymbol))
    }

    // Share sheet items
    private var shareItems: [Any] {
        var items: [Any] = []

        if let etfData = viewModel.etfData {
            let shareText = """
            \(etfData.name) (\(etfData.symbol))
            \(etfData.formattedPrice) \(etfData.formattedChange) \(etfData.formattedChangePercent)

            Check it out on Caudex!
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
                                chartData: etfData.chartData,
                                isPositive: etfData.isPositive,
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

            // Loading overlay
            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .preferredColorScheme(.dark)
        .navigationBarHidden(true)
        .task {
            viewModel.loadETFData()
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
            }
        case .news:
            TickerNewsContent(
                articles: viewModel.newsArticles,
                currentTicker: etfSymbol,
                onArticleTap: viewModel.handleNewsArticleTap,
                onExternalLinkTap: viewModel.handleNewsExternalLink,
                onRelatedTickerTap: viewModel.handleNewsTickerTap
            )
        }
    }

    private func placeholderContent(title: String, description: String) -> some View {
        VStack(spacing: AppSpacing.lg) {
            Image(systemName: "chart.bar.doc.horizontal")
                .font(.system(size: 48))
                .foregroundColor(AppColors.textMuted)

            Text(title)
                .font(AppTypography.title2)
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
        }
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
