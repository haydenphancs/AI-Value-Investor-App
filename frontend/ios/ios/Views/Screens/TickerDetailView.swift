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
    @State private var scrollOffset: CGFloat = 0
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
                // Navigation Header (always visible)
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
                        // Compact ticker info
                        TickerStickyHeader(
                            companyName: tickerData.companyName,
                            symbol: tickerData.symbol,
                            price: tickerData.formattedPrice,
                            priceChange: tickerData.formattedChange,
                            priceChangePercent: tickerData.formattedChangePercent,
                            isPositive: tickerData.isPositive
                        )

                        // Tab Bar
                        TickerDetailTabBar(selectedTab: $viewModel.selectedTab)

                        // Divider
                        Rectangle()
                            .fill(AppColors.cardBackgroundLight)
                            .frame(height: 1)
                    }
                    .background(AppColors.background)
                    .transition(.move(edge: .top).combined(with: .opacity))
                }

                // Scrollable Content
                ScrollView(showsIndicators: false) {
                    VStack(spacing: 0) {
                        // Scroll offset tracker
                        GeometryReader { geometry in
                            Color.clear
                                .preference(
                                    key: ScrollOffsetPreferenceKey.self,
                                    value: geometry.frame(in: .named("scroll")).minY
                                )
                        }
                        .frame(height: 0)

                        // Ticker Price Header (scrolls away)
                        if let tickerData = viewModel.tickerData {
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
                                chartData: tickerData.chartData,
                                isPositive: tickerData.isPositive,
                                selectedRange: $viewModel.selectedChartRange
                            )
                            .padding(.top, AppSpacing.lg)
                        }

                        // Tab Bar (scrolls away, replaced by sticky version)
                        if !showStickyHeader {
                            TickerDetailTabBar(selectedTab: $viewModel.selectedTab)
                                .padding(.top, AppSpacing.lg)

                            // Divider
                            Rectangle()
                                .fill(AppColors.cardBackgroundLight)
                                .frame(height: 1)
                        } else {
                            // Spacer to account for sticky header
                            Spacer()
                                .frame(height: AppSpacing.lg)
                        }

                        // Tab Content
                        tabContent
                    }
                }
                .coordinateSpace(name: "scroll")
                .onPreferenceChange(ScrollOffsetPreferenceKey.self) { value in
                    withAnimation(.easeInOut(duration: 0.2)) {
                        showStickyHeader = value < -stickyThreshold
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
            // Placeholder for News tab
            placeholderContent(title: "News", description: "Latest news about \(tickerSymbol)")
        case .analysis:
            // Placeholder for Analysis tab
            placeholderContent(title: "Analysis", description: "AI-powered analysis for \(tickerSymbol)")
        case .financials:
            // Placeholder for Financials tab
            placeholderContent(title: "Financials", description: "Financial statements for \(tickerSymbol)")
        case .insiders:
            // Placeholder for Insiders tab
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

            // Bottom spacing for AI bar
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
        // TODO: Implement share functionality
        print("Share \(tickerSymbol)")
    }

    private func handleAddToWatchlist() {
        // TODO: Implement add to watchlist
        print("Add \(tickerSymbol) to watchlist")
    }

    private func handleSetPriceAlert() {
        // TODO: Implement price alert
        print("Set price alert for \(tickerSymbol)")
    }

    private func handleCompare() {
        // TODO: Implement compare feature
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
