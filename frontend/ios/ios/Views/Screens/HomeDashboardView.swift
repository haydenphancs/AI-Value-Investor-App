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
    /// Which Daily Scanner card is expanded (nil = none). Owned here so a tap
    /// ANYWHERE outside the card (in the scroll content) collapses it.
    @State private var expandedScannerID: DailyScanner.ID?
    /// A tapped whale/congress signal ticker → the per-ticker drill-down screen.
    @State private var signalDetailTarget: SignalDetailTarget?

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
        .fullScreenCover(item: $signalDetailTarget) { target in
            NavigationStack {
                SignalTickerDetailView(kind: target.kind, ticker: target.symbol)
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
                            onEntryTap: openStock,
                            expandedCardID: $expandedScannerID
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
            // Tap ANYWHERE outside an expanded Daily Scanner card collapses it: a
            // non-consuming tap in the scroll content bubbles up here, while taps
            // on a card body (swallowed in ScannerCard) and on its buttons/tickers
            // (child buttons) do NOT. It's a TapGesture, so scrolling never
            // triggers it; guarded so it's a no-op when nothing is expanded.
            .contentShape(Rectangle())
            .onTapGesture {
                if expandedScannerID != nil {
                    withAnimation(.easeInOut(duration: 0.25)) { expandedScannerID = nil }
                }
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

    private func openLeader(_ kind: String, _ leader: SignalLeader) {
        // Whale & Congress → the per-ticker drill-down (who bought/added it).
        // Earnings has no "who bought" list → open the ticker detail directly.
        if kind == "whale" || kind == "congress" {
            signalDetailTarget = SignalDetailTarget(kind: kind, symbol: leader.symbol)
        } else {
            presentStock(symbol: leader.symbol, name: leader.symbol)
        }
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

/// A tapped whale/congress signal ticker, presented as the per-ticker drill-down.
private struct SignalDetailTarget: Identifiable {
    let id = UUID()
    let kind: String        // "whale" | "congress"
    let symbol: String
}

#Preview {
    HomeDashboardView(selectedTab: .constant(.home), repository: MockHomeRepository())
        .environment(AppState())
        .environmentObject(AudioManager.shared)
        .preferredColorScheme(.dark)
}
