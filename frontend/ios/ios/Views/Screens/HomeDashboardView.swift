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
    /// True while Home is the visible tab. Tabs are opacity-mounted, so this is
    /// the only signal that the screen became (or stopped being) visible.
    @Environment(\.isActiveTab) private var isActiveTab
    @StateObject private var viewModel: HomeDashboardViewModel
    @Binding var selectedTab: HomeTab

    @State private var showSearch = false
    @State private var showProfile = false
    @State private var selectedTicker: MarketTicker?
    /// Which Daily Scanner card is expanded (nil = none). Owned here so a tap
    /// ANYWHERE outside the card (in the scroll content) collapses it.
    @State private var expandedScannerID: DailyScanner.ID?
    /// Which App-Exclusive Signals row is expanded (nil = none). Same
    /// tap-outside-collapses ownership as `expandedScannerID`.
    @State private var expandedSignalID: ExclusiveSignal.ID?
    /// A tapped whale/congress signal ticker → the per-ticker drill-down screen.
    @State private var signalDetailTarget: SignalDetailTarget?
    /// A tapped Emerging Frontiers theme → its detail screen (hero + companies).
    @State private var themeDetailTarget: ThemeDetailTarget?

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

            // FIRST attempt only. `LoadingOverlay` is a full-screen dim that
            // swallows every touch (the same tap-eater the detail views removed),
            // and `isLoading && data == nil` stays true for every retry while the
            // first load keeps failing — so without `!hasAttemptedLoad` the 60s
            // auto-refresh re-raised it once a minute during an outage and locked
            // the user out of the tab bar. Later failures surface via errorBanner.
            if viewModel.isLoading && viewModel.data == nil && !viewModel.hasAttemptedLoad {
                LoadingOverlay()
            }
        }
        // Home is opacity-mounted: it never leaves the view hierarchy, so a plain
        // `.task` fires ONCE per process and `onAppear`/`onDisappear` never fire
        // on a tab switch. Without a re-trigger the strip kept launch-time prices
        // and a launch-time "Pre-Market" header for the whole session.
        //
        // `.task(id:)` covers BOTH cases — it runs on first appearance with the
        // initial value AND again whenever the tab-activation flag flips — so it
        // replaces the plain `.task` outright. Keeping both would fire two
        // concurrent loads on launch.
        .task(id: isActiveTab) {
            guard isActiveTab else {
                viewModel.stopAutoRefresh()
                return
            }
            await viewModel.loadIfStale()
            viewModel.startAutoRefresh()
        }
        // Returning from the background is the other way a user arrives at hours
        // -old data. `scenePhase` is unused in this codebase; this is the
        // NotificationCenter idiom UpdatesView already uses.
        .onReceive(
            NotificationCenter.default.publisher(
                for: UIApplication.didBecomeActiveNotification
            )
        ) { _ in
            guard isActiveTab else { return }
            Task { await viewModel.loadIfStale() }
        }
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
        .fullScreenCover(item: $themeDetailTarget) { target in
            NavigationStack {
                ThemeDetailView(slug: target.slug)
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
                    // array never renders a header over dead space — EXCEPT Market
                    // Pulse. Its "Markets Open/Closed" header is computed fresh
                    // server-side and is still true when the tiles fail, so hiding
                    // the whole section on an upstream blip removed correct
                    // information (and made the outage look like a layout bug).
                    // The section renders its own honest placeholder instead.
                    MarketPulseSection(
                        statusText: data.marketStatusText,
                        isOpen: data.marketIsOpen,
                        items: data.pulse,
                        onTap: openPulse
                    )
                    .padding(.top, AppSpacing.sm)

                    if !data.scanners.isEmpty {
                        DailyScannersSection(
                            scanners: data.scanners,
                            onEntryTap: openStock,
                            // A tap swallowed by a scanner card's body is still
                            // OUTSIDE the signals section → collapse its row.
                            onBodyTap: collapseExpandedSignal,
                            expandedCardID: $expandedScannerID
                        )
                    }

                    if !data.signals.isEmpty {
                        ExclusiveSignalsSection(
                            signals: data.signals,
                            onLeaderTap: openLeader,
                            // A tap swallowed by the signals panel/row body is
                            // still OUTSIDE the carousel → collapse its card.
                            onBodyTap: collapseExpandedScanner,
                            expandedSignalID: $expandedSignalID
                        )
                    }

                    if !data.themes.isEmpty {
                        TrendingThemesSection(
                            themes: data.themes,
                            onThemeTap: { themeDetailTarget = ThemeDetailTarget(slug: $0.slug) }
                        )
                    }

                    // Scanners and App-Exclusive Signals surface per-ticker signals, so
                    // the dashboard carries the notice + a route to the full disclaimers.
                    // INSIDE `if let data` on purpose: outside it, this rendered above the
                    // loading spinner on a cold start, reading as a stray header.
                    InlineDisclaimerNotice()
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.top, AppSpacing.md)
                        .frame(maxWidth: .infinity, alignment: .center)
                }

                Spacer()
                    .frame(height: 100)
            }
            // Tap outside an expanded Daily Scanner card or an expanded
            // App-Exclusive Signals row collapses it: a non-consuming tap in the
            // scroll content bubbles up here. Taps swallowed by a card/row body
            // (ScannerCard / SignalDisclosureRow / ExclusiveSignalsSection) don't
            // reach this gesture — those sections forward them via onBodyTap so
            // the OTHER section still collapses. Taps on buttons/tickers collapse
            // nothing (child buttons win). It's a TapGesture, so scrolling never
            // triggers it; guarded so it's a no-op when nothing is expanded.
            .contentShape(Rectangle())
            .onTapGesture {
                if expandedScannerID != nil || expandedSignalID != nil {
                    withAnimation(.easeInOut(duration: 0.25)) {
                        expandedScannerID = nil
                        expandedSignalID = nil
                    }
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

    // MARK: - Cross-section collapse (taps swallowed by one section's body are
    // still "outside" the other section's expandable — see onBodyTap wiring).

    private func collapseExpandedScanner() {
        if expandedScannerID != nil {
            withAnimation(.easeInOut(duration: 0.25)) { expandedScannerID = nil }
        }
    }

    private func collapseExpandedSignal() {
        if expandedSignalID != nil {
            withAnimation(.easeInOut(duration: 0.25)) { expandedSignalID = nil }
        }
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

/// A tapped Emerging Frontiers theme, presented as its detail screen.
private struct ThemeDetailTarget: Identifiable {
    let id = UUID()
    let slug: String
}

#Preview {
    HomeDashboardView(selectedTab: .constant(.home), repository: MockHomeRepository())
        .environment(AppState())
        .environmentObject(AudioManager.shared)
        .preferredColorScheme(.dark)
}
