//
//  HomeDashboardView.swift
//  ios
//
//  Screen: the redesigned Caydex Home dashboard.
//
//  Composes the four home organisms (Market Pulse, Daily Scanners,
//  App-Exclusive Signals, Trending Themes) over a `HomeDashboardViewModel`
//  backed by a `MockHomeRepository` (UI only — no backend). Reuses the shared
//  `HomeHeader` (logo · search · avatar) and `CustomTabBar`, and routes taps to
//  the existing detail screens via the same fullScreenCover pattern as the rest
//  of the app.
//

import SwiftUI

struct HomeDashboardView: View {
    @Environment(AppState.self) private var appState
    @StateObject private var viewModel: HomeDashboardViewModel
    @Binding var selectedTab: HomeTab

    @State private var showSearch = false
    @State private var showProfile = false
    @State private var selectedTicker: MarketTicker?

    /// `repository == nil` → the live `HomeRepository` (the app default).
    /// Pass `MockHomeRepository()` for offline previews / tests.
    init(selectedTab: Binding<HomeTab>, repository: HomeRepositoryProtocol? = nil) {
        self._selectedTab = selectedTab
        self._viewModel = StateObject(
            wrappedValue: HomeDashboardViewModel(repository: repository)
        )
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                HomeHeader(
                    onProfileTapped: { showProfile = true },
                    onSearchTapped: { showSearch = true }
                )

                content

                CustomTabBar(selectedTab: $selectedTab)
            }

            if viewModel.isLoading && viewModel.data == nil {
                LoadingOverlay()
            }
        }
        .task { await viewModel.loadIfNeeded() }
        .sheet(isPresented: $showSearch) {
            SearchView()
        }
        .fullScreenCover(isPresented: $showProfile) {
            ProfileView()
                .environment(appState)
                .environment(\.appState, appState)
                .preferredColorScheme(.dark)
        }
        .fullScreenCover(item: $selectedTicker) { ticker in
            NavigationStack {
                Group {
                    switch ticker.type {
                    case .index:
                        IndexDetailView(indexSymbol: ticker.symbol)
                    case .stock:
                        TickerDetailView(tickerSymbol: ticker.symbol)
                    case .crypto:
                        CryptoDetailView(cryptoSymbol: ticker.symbol)
                    case .commodity:
                        CommodityDetailView(commoditySymbol: ticker.symbol)
                    case .etf:
                        ETFDetailView(etfSymbol: ticker.symbol)
                    }
                }
                .navigationBarHidden(true)
            }
            .preferredColorScheme(.dark)
        }
    }

    // MARK: - Scrollable content

    @ViewBuilder
    private var content: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xl) {
                if let errorMessage = viewModel.errorMessage {
                    errorBanner(errorMessage)
                }

                if let data = viewModel.data {
                    // Each section is hidden when its data is empty so an empty
                    // array (a future live repo could return one) never renders a
                    // header over dead space.
                    if !data.pulse.isEmpty {
                        MarketPulseSection(
                            statusText: data.marketStatusText,
                            isOpen: data.marketIsOpen,
                            items: data.pulse,
                            onTap: openPulse
                        )
                        .padding(.top, AppSpacing.sm)
                    }

                    if !data.scanners.isEmpty {
                        DailyScannersSection(
                            scanners: data.scanners,
                            onEntryTap: openStock
                        )
                    }

                    if !data.signals.isEmpty {
                        ExclusiveSignalsSection(
                            signals: data.signals,
                            onLeaderTap: openLeader
                        )
                    }

                    if !data.themes.isEmpty {
                        TrendingThemesSection(
                            themes: data.themes,
                            onThemeTap: { _ in selectedTab = .research }
                        )
                    }
                }

                Spacer()
                    .frame(height: 100)
            }
        }
        .refreshable {
            await viewModel.refresh()
        }
    }

    private func errorBanner(_ message: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: "wifi.exclamationmark")
                .foregroundColor(AppColors.neutral)
            Text(message)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
            Spacer()
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
        .background(AppColors.cardBackground.opacity(0.6))
        .cornerRadius(AppCornerRadius.medium)
        .padding(.horizontal, AppSpacing.lg)
        .padding(.top, AppSpacing.xs)
    }

    // MARK: - Navigation

    private func openPulse(_ item: MarketPulseItem) {
        selectedTicker = MarketTicker(
            name: item.name,
            symbol: item.symbol,
            type: item.type,
            price: 0,
            changePercent: item.isPositive ? 1 : -1,
            sparklineData: item.spark
        )
    }

    private func openStock(_ entry: ScannerEntry) {
        presentStock(symbol: entry.symbol, name: entry.name)
    }

    private func openLeader(_ leader: SignalLeader) {
        presentStock(symbol: leader.symbol, name: leader.symbol)
    }

    private func presentStock(symbol: String, name: String) {
        selectedTicker = MarketTicker(
            name: name,
            symbol: symbol,
            type: .stock,
            price: 0,
            changePercent: 0,
            sparklineData: []
        )
    }
}

#Preview {
    HomeDashboardView(selectedTab: .constant(.home), repository: MockHomeRepository())
        .environment(AppState())
        .environmentObject(AudioManager.shared)
        .preferredColorScheme(.dark)
}
