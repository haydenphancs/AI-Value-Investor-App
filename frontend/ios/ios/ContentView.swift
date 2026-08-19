//
//  ContentView.swift
//  ios
//
//  Created by Hai Phan on 12/30/25.
//

import SwiftUI

struct ContentView: View {
    // Needed to observe a notification tap (pendingPushTicker) and bring Home forward.
    @Environment(AppState.self) private var appState
    @State private var selectedTab: HomeTab = .home
    @State private var researchTickerSymbol: String? = nil
    @State private var researchSubTab: ResearchTab = .research

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            // Keep all tab views alive to avoid re-creating ViewModels on every tab switch.
            // Only the selected tab is visible; the others are hidden but retained in memory
            // so their @StateObject instances persist and don't re-trigger data loading.
            //
            // New Caydex Home dashboard — backend-connected via the live
            // `HomeRepository` (the ViewModel's default; `MockHomeRepository` is
            // previews-only). All four sections fetch `GET /home/dashboard`. The
            // legacy `HomeViewWithBinding` is kept below for reference/rollback —
            // swap this line back to it to restore the previous Home.
            HomeDashboardView(selectedTab: $selectedTab)
            .opacity(selectedTab == .home ? 1 : 0)
            .allowsHitTesting(selectedTab == .home)
            .environment(\.isActiveTab, selectedTab == .home)

            UpdatesView(selectedTab: $selectedTab)
                .opacity(selectedTab == .updates ? 1 : 0)
                .allowsHitTesting(selectedTab == .updates)
                .environment(\.isActiveTab, selectedTab == .updates)

            ResearchViewWithBinding(
                selectedTab: $selectedTab,  
                prefilledTicker: researchTickerSymbol,
                initialSubTab: researchSubTab
            )
            .opacity(selectedTab == .research ? 1 : 0)
            .allowsHitTesting(selectedTab == .research)
            .environment(\.isActiveTab, selectedTab == .research)

            TrackingViewWithBinding(
                selectedTab: $selectedTab,
                researchTickerSymbol: $researchTickerSymbol
            )
            .opacity(selectedTab == .tracking ? 1 : 0)
            .allowsHitTesting(selectedTab == .tracking)
            .environment(\.isActiveTab, selectedTab == .tracking)

            WiserViewWithBinding(selectedTab: $selectedTab)
                .opacity(selectedTab == .wiser ? 1 : 0)
                .allowsHitTesting(selectedTab == .wiser)
                .environment(\.isActiveTab, selectedTab == .wiser)
        }
        .onChange(of: appState.pendingPushRoute, initial: true) { _, route in
            // `initial: true` for the same reason as HomeDashboardView: a cold launch
            // from a tap sets this before either view exists.
            // Bring Home forward FIRST. Tabs are opacity-mounted, so without this the
            // destination cover would present over a tab the user isn't looking at.
            // HomeDashboardView consumes and clears the value.
            if route != nil { selectedTab = .home }
        }
        .onChange(of: selectedTab) { oldValue, newValue in
            // Which tabs actually get used. `HomeTab` is a fixed 5-case enum, so this
            // is a low-cardinality dimension, not free text.
            Analytics.shared.track(.screenView, ["tab": .string(newValue.rawValue)])

            // Clear the research ticker when leaving research tab
            if oldValue == .research && newValue != .research {
                researchTickerSymbol = nil
                researchSubTab = .research
            }
        }
    }
}

// MARK: - HomeView with Binding Support
struct HomeViewWithBinding: View {
    @Environment(AppState.self) private var appState
    @StateObject private var viewModel = HomeViewModel()
    @Binding var selectedTab: HomeTab
    @Binding var researchSubTab: ResearchTab
    @Binding var researchTickerSymbol: String?
    @State private var showSearch = false
    @State private var showProfile = false
    @State private var selectedNewsArticle: NewsArticle?
    @State private var selectedReportTicker: ReportTickerNavigation?
    @State private var selectedMarketTicker: MarketTicker?
    @State private var selectedTrendingAnalysis: TrendingAnalysis?

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                HomeHeader(
                    onProfileTapped: {
                        showProfile = true
                    },
                    onSearchTapped: {
                        showSearch = true
                    }
                )

                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xl) {
                        // Error banner — visible when backend is unreachable
                        if let errorMessage = viewModel.error {
                            HStack(spacing: 8) {
                                Image(systemName: "wifi.exclamationmark")
                                    .foregroundColor(AppColors.neutral)
                                Text(errorMessage)
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

                        MarketTickersRow(
                            tickers: viewModel.marketTickers,
                            onTickerTap: { ticker in
                                selectedMarketTicker = ticker
                            }
                        )
                        .padding(.top, AppSpacing.sm)

                        if let insight = viewModel.marketInsight {
                            MarketInsightsCard(insight: insight) {
                                selectedTab = .updates
                            }
                            .padding(.horizontal, AppSpacing.lg)
                        }

                        DailyBriefingSection(
                            items: viewModel.dailyBriefings,
                            onItemTapped: handleBriefingItemTapped
                        )

                        RecentResearchSection(
                            reports: viewModel.recentResearch,
                            onSeeAllTapped: {
                                researchSubTab = .reports
                                selectedTab = .research
                            },
                            onReportTapped: { _ in },
                            onAskOrReadTapped: { report in
                                selectedReportTicker = ReportTickerNavigation(ticker: report.stockTicker)
                            }
                        )

                        NewAnalysisButton {
                            selectedTab = .research
                        }

                        Spacer()
                            .frame(height: 100)
                    }
                }
                .refreshable {
                    await viewModel.refresh()
                }

                CustomTabBar(
                    selectedTab: $selectedTab,
                    unreadNotifications: appState.unreadNotificationCount
                )
            }

            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .sheet(isPresented: $showSearch) {
            SearchView()
        }
        .fullScreenCover(isPresented: $showProfile) {
            ProfileView()
                .environment(appState)
                .environment(\.appState, appState)
        }
        .fullScreenCover(item: $selectedNewsArticle) { article in
            NewsDetailView(article: article)
        }
        .fullScreenCover(item: $selectedReportTicker) { nav in
            NavigationStack {
                TickerReportView(ticker: nav.ticker)
            }
        }
        .fullScreenCover(item: $selectedMarketTicker) { ticker in
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
        }
        .fullScreenCover(item: $selectedTrendingAnalysis) { analysis in
            NavigationStack {
                TrendingAnalysisDetailView(analysis: analysis) { ticker in
                    researchTickerSymbol = ticker
                    selectedTab = .research
                }
            }
        }
    }

    // MARK: - Action Handlers
    private func handleBriefingItemTapped(_ item: DailyBriefingItem) {
        // Convert DailyBriefingItem to NewsArticle for navigation
        selectedNewsArticle = NewsArticle(
            headline: item.title,
            summary: item.subtitle,
            source: NewsSource(name: "Market Alert", iconName: nil),
            sentiment: .neutral,
            publishedAt: item.date ?? Date(),
            thumbnailName: nil,
            relatedTickers: []
        )
    }
}

// MARK: - ResearchView with Binding Support
struct ResearchViewWithBinding: View {
    @Environment(AppState.self) private var appState
    /// Injected at ContentView.swift's tab ZStack. It was already being provided for this tab
    /// and simply never read here, which is why Home and Updates recovered from a raced load
    /// and Research did not.
    @Environment(\.isActiveTab) private var isActiveTab
    @StateObject private var viewModel: ResearchViewModel
    @Binding var selectedTab: HomeTab
    let prefilledTicker: String?
    let initialSubTab: ResearchTab
    // Carry the full AnalysisReport (not just ticker) so the detail view
    // receives backendId + persona and can short-circuit to the cached
    // ticker_report_data JSONB instead of regenerating.
    @State private var selectedReport: AnalysisReport?
    @State private var selectedTrendingAnalysis: TrendingAnalysis?
    @State private var showProfile = false

    init(selectedTab: Binding<HomeTab>, prefilledTicker: String? = nil, initialSubTab: ResearchTab = .research) {
        self._selectedTab = selectedTab
        self.prefilledTicker = prefilledTicker
        self.initialSubTab = initialSubTab
        self._viewModel = StateObject(wrappedValue: ResearchViewModel(prefilledTicker: prefilledTicker))
    }

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Header (pinned outside scroll)
                ResearchHeader(
                    selectedTab: $viewModel.selectedTab,
                    onProfileTapped: handleProfileTapped
                )

                // Tab Content
                if viewModel.selectedTab == .research {
                    researchTabContent
                } else {
                    reportsTabContent
                }

                CustomTabBar(
                    selectedTab: $selectedTab,
                    unreadNotifications: appState.unreadNotificationCount
                )
            }

            if viewModel.isLoading {
                LoadingOverlay()
            }
        }
        .onAppear {
            viewModel.selectedTab = initialSubTab
        }
        // Live-poll the Reports list while anything is generating.
        //
        // This was wired ONLY in `Views/Screens/ResearchView.swift`, the preview-only
        // copy of this screen that is never presented — so in the shipping app nothing
        // ever armed it. The generation stream refreshes the list at 25% boundaries,
        // which hid the gap while a stream was alive, but a report started in a
        // previous app run, or one whose stream ended on a network error, left its
        // card frozen at whatever percentage the last load returned until the user
        // pulled to refresh. `startReportsPolling` self-terminates once nothing is
        // `.processing`, so arming it on tab entry costs one Supabase query per 5s
        // only while work is genuinely in flight.
        .task(id: isActiveTab) {
            guard isActiveTab else { return }
            viewModel.startReportsPolling()
        }
        .onChange(of: viewModel.selectedTab) { _, tab in
            if tab == .reports { viewModel.startReportsPolling() }
        }
        .onDisappear { viewModel.stopReportsPolling() }
        // Heals a load that raced session restore. The ViewModel's only unconditional load is
        // in `init`, and all five tabs mount eagerly in one ZStack — so it runs at launch, while
        // auth is still `.restoring`, and `requiresSignInForReports` latched `true` with nothing
        // to clear it. `loadIfStale` makes re-entry cheap; a signed-out/reconnecting pass is
        // never marked fresh, so arriving here after signing in always refetches.
        // Same `.task(id: isActiveTab)` idiom as HomeDashboardView and UpdatesView.
        .task(id: isActiveTab) {
            guard isActiveTab else { return }
            await viewModel.loadIfStale()
        }
        // The direct case, and the one that has no other cure: signing in or out from THIS
        // tab. `SignInRequiredSheet` dismisses itself on `.authenticated`, and
        // `onAuthenticated()`'s fan-out hydrates credits, settings and the Learn stores but
        // nothing research-related — so without this the tab behind the sheet keeps rendering
        // "Sign in to see your analyses" to a user who just signed in.
        .reloadOnIdentityChange { await viewModel.reloadForIdentityChange() }
        .fullScreenCover(item: $selectedReport) { report in
            NavigationStack {
                TickerReportView(report: report)
            }
        }
        .fullScreenCover(isPresented: $showProfile) {
            ProfileView()
                .environment(appState)
                .environment(\.appState, appState)
        }
        .fullScreenCover(item: $selectedTrendingAnalysis) { analysis in
            NavigationStack {
                TrendingAnalysisDetailView(analysis: analysis) { ticker in
                    viewModel.searchText = ticker
                }
            }
        }
        // "Add More Credits" now sells credits instead of opening the subscription paywall.
        // A user who ran out mid-task wants to finish it, not commit to a monthly plan;
        // BuyCreditsView carries a "See plans" button so the paywall stays one tap away.
        .sheet(isPresented: $viewModel.showCreditsSheet) {
            BuyCreditsView()
                .environment(\.appState, appState)
        }
        .sheet(isPresented: $viewModel.showPersonasSheet) {
            PersonasSheet(
                personas: viewModel.personas,
                selectedPersona: $viewModel.selectedPersona
            )
        }
        .sheet(isPresented: $viewModel.showTargetSearchSheet) {
            TargetSearchSheet { result in
                viewModel.selectTarget(result)
            }
        }
        .alert("Delete \(viewModel.selectedReportCount) report\(viewModel.selectedReportCount == 1 ? "" : "s")?",
               isPresented: $viewModel.showDeleteConfirm) {
            Button("Delete", role: .destructive) {
                Task { await viewModel.deleteSelectedReports() }
            }
            Button("Cancel", role: .cancel) { }
        } message: {
            Text("This can't be undone.")
        }
        .alert("Error", isPresented: Binding(
            get: { viewModel.error != nil },
            set: { if !$0 { viewModel.error = nil } }
        )) {
            Button("OK") { viewModel.error = nil }
        } message: {
            Text(viewModel.error ?? "")
        }
    }

    // MARK: - Research Tab Content
    /// The balance to display, preferring the ViewModel's own copy and falling back to
    /// `AppState`.
    ///
    /// The ViewModel deliberately keeps its OWN `creditBalance` (it has no AppState
    /// reference) so it can adopt a purchase the instant `.caydexEntitlementChanged`
    /// fires. That copy is authoritative when present — but it is nil until
    /// `loadCredits()` returns, and it STAYS nil if that one request fails. The card and
    /// the Generate badge then vanish entirely, while the Wiser tab — which reads
    /// `appState.user.credits`, the documented single source of truth — shows the real
    /// number on the same screen-swipe. Two surfaces disagreeing about the user's
    /// balance reads as a bug in the credits system, not as a failed fetch.
    ///
    /// Falling back rather than replacing keeps the purchase-adoption path intact.
    private var effectiveCreditBalance: CreditBalance? {
        // `.map { … }`, not `.map(CreditBalance.from)`: an unapplied method reference
        // becomes a *nonisolated* function type, and `from` reads the @MainActor
        // `purchasedCredits`. A closure literal here inherits this view's isolation.
        viewModel.creditBalance ?? appState.user.credits.map { CreditBalance.from($0) }
    }

    private var researchTabContent: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xxl) {
                // Target Selection Section
                TargetSelectionSection(
                    selectedTarget: viewModel.selectedTarget,
                    fallbackTicker: viewModel.searchText,
                    onTapSearch: { viewModel.openTargetSearch() },
                    onClearTarget: { viewModel.clearTarget() }
                )
                .padding(.top, AppSpacing.md)

                // Persona Selection Section
                PersonaSelectionSection(
                    personas: viewModel.personas,
                    selectedPersona: $viewModel.selectedPersona,
                    onViewAllTapped: handleViewAllPersonas
                )

                // Generate Analysis Section
                GenerateAnalysisSection(
                    cost: viewModel.analysisCost,
                    remainingCredits: effectiveCreditBalance?.credits,
                    isEnabled: viewModel.canStartNewGeneration,
                    isLoading: viewModel.isAtConcurrencyCap,
                    onGenerate: handleGenerateAnalysis
                )

                // What You'll Get Section
                WhatYouGetSection(features: viewModel.features)

                // Credits Balance Card — only once a real balance is known.
                if let balance = effectiveCreditBalance {
                    CreditsBalanceCard(
                        balance: balance,
                        onAddCredits: handleAddCredits
                    )
                    .padding(.horizontal, AppSpacing.lg)
                }

                // Bottom padding for tab bar
                Spacer()
                    .frame(height: AppSpacing.xxxl)
            }
        }
        .refreshable {
            await viewModel.refresh()
        }
    }

    // MARK: - Reports Tab Content
    private var reportsTabContent: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xxl) {
                // Reports List Section
                ReportsListSection(
                    sections: viewModel.groupedReports,
                    sortOption: $viewModel.reportSortOption,
                    searchText: $viewModel.reportSearchText,
                    isSearchActive: $viewModel.isReportSearchActive,
                    isSelecting: $viewModel.isSelectingReports,
                    selectedIds: viewModel.selectedReportIds,
                    personaTags: viewModel.personas,
                    selectedPersonaKeys: viewModel.selectedPersonaKeys,
                    onReportTapped: handleReportTapped,
                    onRetryTapped: handleRetryTapped,
                    onToggleSelect: handleToggleSelect,
                    onToggleSelectingMode: handleToggleSelectingMode,
                    onTogglePersonaTag: { viewModel.togglePersonaTag($0) },
                    // First-run CTA: send them to the tab that can actually make one.
                    onGenerateFirst: { viewModel.selectedTab = .research },
                    // Reports are account-scoped now, so "you have none" and "you're signed
                    // out" are different situations and get different copy + CTA.
                    requiresSignIn: viewModel.requiresSignInForReports,
                    onSignIn: { appState.requestSignIn(for: "see your analyses") },
                    isReconnecting: viewModel.isReconnectingReports
                )
                .padding(.top, AppSpacing.sm)

                // Community Insights — deferred. Backend feature pending; the
                // mock data + stub handlers are kept in the codebase for the
                // future read/write feed.

                // Bottom padding for tab bar
                Spacer()
                    .frame(height: AppSpacing.xxxl)

                // Extra inset while selecting so the last card scrolls clear
                // of the floating selection bar.
                if viewModel.isSelectingReports {
                    Spacer().frame(height: 72)
                }
            }
        }
        .refreshable {
            await viewModel.refresh()
        }
        // Floating selection bar — pinned to the bottom of the reports list,
        // which sits just above the CustomTabBar.
        .overlay(alignment: .bottom) {
            if viewModel.isSelectingReports {
                ReportsSelectionBar(
                    selectedCount: viewModel.selectedReportCount,
                    isDeleting: viewModel.isDeletingReports,
                    onDelete: { viewModel.showDeleteConfirm = true }
                )
                .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.easeInOut(duration: 0.22), value: viewModel.isSelectingReports)
    }

    // MARK: - Action Handlers
    private func handleProfileTapped() {
        showProfile = true
    }

    private func handleViewAllPersonas() {
        viewModel.viewAllPersonas()
    }

    private func handleGenerateAnalysis() {
        viewModel.generateAnalysis()
    }

    private func handleAddCredits() {
        viewModel.addMoreCredits()
    }

    private func handleTrendingAnalysisTapped(_ analysis: TrendingAnalysis) {
        selectedTrendingAnalysis = analysis
    }

    private func handleReportTapped(_ report: AnalysisReport) {
        guard report.status == .ready else { return }
        // Pass the full report so the detail view receives backendId
        // (cached JSONB lookup) and persona (correct agent selection).
        selectedReport = report
    }

    private func handleRetryTapped(_ report: AnalysisReport) {
        viewModel.retryReport(report)
    }

    private func handleToggleSelect(_ report: AnalysisReport) {
        viewModel.toggleReportSelection(report)
    }

    private func handleToggleSelectingMode() {
        if viewModel.isSelectingReports {
            viewModel.exitSelectionMode()
        } else {
            viewModel.isSelectingReports = true
        }
    }
}

// MARK: - TrackingView with Binding Support
struct TrackingViewWithBinding: View {
    // The tab bar renders on every one of these wrappers, so the unread badge
    // must be readable from all of them — otherwise it appears and vanishes as
    // the user switches tabs.
    @Environment(AppState.self) private var appState
    @Binding var selectedTab: HomeTab
    @Binding var researchTickerSymbol: String?

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                TrackingContentViewWithBinding(
                    selectedTab: $selectedTab,
                    researchTickerSymbol: $researchTickerSymbol
                )

                CustomTabBar(
                    selectedTab: $selectedTab,
                    unreadNotifications: appState.unreadNotificationCount
                )
            }
        }
    }
}

// MARK: - WiserView with Binding Support (Learn)
struct WiserViewWithBinding: View {
    // The tab bar renders on every one of these wrappers, so the unread badge
    // must be readable from all of them — otherwise it appears and vanishes as
    // the user switches tabs.
    @Environment(AppState.self) private var appState
    @Binding var selectedTab: HomeTab

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // The Wiser "Chat" tab now opens AIChatScreen as a full-screen cover that owns its
                // own audio compact/island via .globalAudioOverlay, so the Wiser screen no longer
                // needs an active-tab signal here.
                LearnContentView()

                CustomTabBar(
                    selectedTab: $selectedTab,
                    unreadNotifications: appState.unreadNotificationCount
                )
            }
        }
    }
}

// MARK: - Placeholder View for Other Tabs
struct TabPlaceholderView: View {
    // The tab bar renders on every one of these wrappers, so the unread badge
    // must be readable from all of them — otherwise it appears and vanishes as
    // the user switches tabs.
    @Environment(AppState.self) private var appState
    let title: String
    @Binding var selectedTab: HomeTab

    var body: some View {
        ZStack(alignment: .bottom) {
            AppColors.background
                .ignoresSafeArea()

            VStack {
                Spacer()

                VStack(spacing: AppSpacing.md) {
                    Image(systemName: "hammer.fill")
                        .font(AppTypography.iconHero)
                        .foregroundColor(AppColors.textMuted)

                    Text(title)
                        .font(AppTypography.title)
                        .foregroundColor(AppColors.textPrimary)

                    Text("Coming Soon")
                        .font(AppTypography.body)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                CustomTabBar(
                    selectedTab: $selectedTab,
                    unreadNotifications: appState.unreadNotificationCount
                )
            }
        }
    }
}

#Preview {
    ContentView()
        .environment(AppState())
        .environmentObject(AudioManager.shared)
}
