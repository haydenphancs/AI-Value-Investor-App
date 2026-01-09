//
//  TickerDetailView.swift
//  ios
//
//  Main Ticker Detail screen displaying stock information
//

import SwiftUI

struct TickerDetailView: View {
    @StateObject private var viewModel: TickerDetailViewModel
    @Environment(\.dismiss) private var dismiss
    @State private var showMoreOptions = false
    @State private var showStickyHeader: Bool = false

    let tickerSymbol: String

    // Threshold for when sticky header appears
    private let stickyThreshold: CGFloat = 200

    init(tickerSymbol: String) {
        self.tickerSymbol = tickerSymbol
        self._viewModel = StateObject(wrappedValue: TickerDetailViewModel(tickerSymbol: tickerSymbol))
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            // Background
            AppColors.background
                .ignoresSafeArea()

            // Main Content
            VStack(spacing: 0) {
                // Navigation Header (always visible - back, bell, star, more buttons)
                TickerDetailHeader(
                    onBackTapped: handleBackTapped,
                    onNotificationTapped: viewModel.handleNotificationTap,
                    onFavoriteTapped: viewModel.toggleFavorite,
                    onMoreTapped: handleMoreTapped,
                    isFavorite: viewModel.isFavorite
                )

                // Sticky Header (appears when scrolling past threshold)
                if showStickyHeader, let tickerData = viewModel.tickerData {
                    VStack(spacing: 0) {
                        // Price Header
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

                        // Tab Bar (sticks with ticker info)
                        TickerDetailTabBar(selectedTab: $viewModel.selectedTab)
                            .padding(.top, AppSpacing.sm)

                        // Divider
                        Rectangle()
                            .fill(AppColors.cardBackgroundLight)
                            .frame(height: 1)
                    }
                    .background(AppColors.background)
                    .transition(.opacity)
                    .zIndex(1)
                }

                // Scrollable Content
                ScrollView(showsIndicators: false) {
                    VStack(spacing: 0) {
                        // Scroll offset tracker (placed at top of scroll content)
                        GeometryReader { geometry in
                            Color.clear
                                .preference(
                                    key: ScrollOffsetPreferenceKey.self,
                                    value: geometry.frame(in: .named("scroll")).minY
                                )
                        }
                        .frame(height: 0)

                        // Full Ticker Price Header (scrolls away)
                        if let tickerData = viewModel.tickerData {
                            if !showStickyHeader {
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
                            }

                            // Chart (always scrolls away)
                            TickerChartView(
                                chartData: tickerData.chartData,
                                isPositive: tickerData.isPositive,
                                selectedRange: $viewModel.selectedChartRange
                            )
                            .padding(.top, showStickyHeader ? AppSpacing.sm : AppSpacing.lg)
                        }

                        // Tab Bar in scroll (only visible when NOT sticky)
                        if !showStickyHeader {
                            TickerDetailTabBar(selectedTab: $viewModel.selectedTab)
                                .padding(.top, AppSpacing.lg)

                            // Divider
                            Rectangle()
                                .fill(AppColors.cardBackgroundLight)
                                .frame(height: 1)
                        }

                        // Tab Content
                        tabContent
                    }
                }
                .coordinateSpace(name: "scroll")
                .onPreferenceChange(ScrollOffsetPreferenceKey.self) { value in
                    let shouldShow = value < -stickyThreshold
                    if shouldShow != showStickyHeader {
                        withAnimation(.easeInOut(duration: 0.15)) {
                            showStickyHeader = shouldShow
                        }
                    }
                }
                .refreshable {
                    await viewModel.refresh()
                }
            }

            // Bottom AI Chat Bar (always visible)
            TickerDetailAIBar(
                inputText: $viewModel.aiInputText,
                tickerSymbol: tickerSymbol,
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
            viewModel.loadTickerData()
        }
        .gesture(
            DragGesture()
                .onEnded { value in
                    // Swipe right to dismiss
                    if value.translation.width > 100 {
                        handleBackTapped()
                    }
                }
        )
        .confirmationDialog("Options", isPresented: $showMoreOptions) {
            Button("Share") {
                handleShare()
            }
            Button("Add to Watchlist") {
                handleAddToWatchlist()
            }
            Button("Set Price Alert") {
                handleSetPriceAlert()
            }
            Button("Compare") {
                handleCompare()
            }
            Button("Cancel", role: .cancel) {}
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
                    onDeepResearchTap: viewModel.handleDeepResearch,
                    onWebsiteTap: viewModel.handleWebsiteTap,
                    onRelatedTickerTap: viewModel.handleRelatedTickerTap
                )
            }
        case .news:
            TickerNewsContent(
                articles: viewModel.newsArticles,
                currentTicker: tickerSymbol,
                onArticleTap: viewModel.handleNewsArticleTap,
                onExternalLinkTap: viewModel.handleNewsExternalLink,
                onRelatedTickerTap: viewModel.handleNewsTickerTap
            )
        case .analysis:
            placeholderContent(title: "Analysis", description: "AI-powered analysis for \(tickerSymbol)")
        case .financials:
            placeholderContent(title: "Financials", description: "Financial statements for \(tickerSymbol)")
        case .insiders:
            placeholderContent(title: "Insiders", description: "Insider trading activity for \(tickerSymbol)")
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

    private func handleMoreTapped() {
        showMoreOptions = true
    }

    private func handleShare() {
        print("Share \(tickerSymbol)")
    }

    private func handleAddToWatchlist() {
        print("Add \(tickerSymbol) to watchlist")
    }

    private func handleSetPriceAlert() {
        print("Set price alert for \(tickerSymbol)")
    }

    private func handleCompare() {
        print("Compare \(tickerSymbol)")
    }
}

// MARK: - Scroll Offset Preference Key
struct ScrollOffsetPreferenceKey: PreferenceKey {
    static var defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) {
        value = nextValue()
    }
}

// MARK: - Preview
#Preview {
    TickerDetailView(tickerSymbol: "AAPL")
}
