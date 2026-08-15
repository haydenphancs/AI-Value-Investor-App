//
//  TrackingView.swift
//  ios
//
//  Main Tracking screen with Assets and Whales tabs
//

import SwiftUI

// MARK: - Navigation Models
/// Wrapper for trade group navigation. Carries the activity feed item so the
/// destination view can render its header immediately and then fetch the full
/// per-ticker trades from `GET /whales/{whaleId}/trade-groups/{groupId}`.
struct TradeGroupNavigation: Identifiable, Hashable {
    let activity: WhaleTradeGroupActivity

    var id: String { activity.id }
    var whaleName: String { activity.entityName }

    init(activity: WhaleTradeGroupActivity) {
        self.activity = activity
    }

    static func == (lhs: TradeGroupNavigation, rhs: TradeGroupNavigation) -> Bool {
        lhs.id == rhs.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
}

// MARK: - TrackingContentView (Used in TabView)
struct TrackingContentView: View {
    @Environment(\.appState) private var appState
    @StateObject private var viewModel = TrackingViewModel()
    @State private var showProfile = false
    @State private var showSearch = false

    var body: some View {
        NavigationStack {
            ZStack {
                // Background
                AppColors.background
                    .ignoresSafeArea()

                // Main Content
                VStack(spacing: 0) {
                    // Header with Search and Tab Control
                    TrackingHeader(
                        selectedTab: $viewModel.selectedTab,
                        onSearchTapped: handleSearchTapped,
                        onProfileTapped: handleProfileTapped
                    )

                    // Tab Content
                    Group {
                        switch viewModel.selectedTab {
                        case .assets:
                            AssetsTabContent(viewModel: viewModel)
                        case .whales:
                            WhalesTabContent(viewModel: viewModel)
                        }
                    }
                }

                // Loading overlay
                if viewModel.isLoading {
                    LoadingOverlay()
                }
            }
            .sheet(isPresented: $viewModel.showAddAssetSheet) {
                AddAssetSheet(
                    onAssetAdded: { _ in
                        Task { await viewModel.refresh() }
                    },
                    onDismiss: {
                        viewModel.showAddAssetSheet = false
                    }
                )
            }
            .sheet(isPresented: $viewModel.showSortSheet) {
                SortOptionsSheet(
                    selectedOption: viewModel.sortOption,
                    onSelect: { option in
                        viewModel.selectSortOption(option)
                    },
                    onDismiss: {
                        viewModel.showSortSheet = false
                    }
                )
            }
            .sheet(isPresented: $viewModel.showPortfolioConfigSheet) {
                PortfolioConfigSheet(viewModel: viewModel)
            }
            .sheet(isPresented: $viewModel.showNewPortfolioSheet) {
                NewPortfolioSheet(viewModel: viewModel)
            }
            .sheet(isPresented: $viewModel.showEditPortfolioSheet) {
                EditPortfolioSheet(viewModel: viewModel)
            }
            .sheet(isPresented: $viewModel.showManageTickersSheet) {
                ManageTickersSheet(viewModel: viewModel)
            }
            .navigationDestination(item: $viewModel.selectedAssetNavigation) { selection in
                AssetDetailRouter(selection: selection)
            }
            .navigationDestination(item: $viewModel.selectedSearchResult) { selection in
                AssetDetailRouter(selection: selection)
            }
            .navigationDestination(item: $viewModel.selectedWhaleId) { whaleId in
                WhaleProfileView(whaleId: whaleId)
            }
            .navigationDestination(item: $viewModel.selectedTradeGroup) { tradeData in
                TradeGroupDetailView(
                    activity: tradeData.activity,
                    whaleName: tradeData.whaleName
                )
            }
            .sheet(item: $viewModel.selectedAlert) { alert in
                NavigationStack {
                    AlertDetailView(alert: alert)
                }
            }
            .fullScreenCover(isPresented: $showProfile) {
                ProfileView()
                    .environment(appState)
                    .environment(\.appState, appState)
            }
            .sheet(isPresented: $showSearch) {
                TickerLiveSearchSheet(
                    onTickerSelected: { selection in
                        showSearch = false
                        viewModel.selectedSearchResult = selection
                    },
                    onDismiss: {
                        showSearch = false
                    },
                    isInWatchlist: { ticker in viewModel.isOnWatchlist(ticker) },
                    onAddToWatchlist: { result in viewModel.addTickerFromSearch(result) }
                )
            }
        }
    }

    // MARK: - Action Handlers
    private func handleSearchTapped() {
        showSearch = true
    }

    private func handleProfileTapped() {
        showProfile = true
    }
}

// MARK: - TrackingContentViewWithBinding (Used when tab navigation needed)
struct TrackingContentViewWithBinding: View {
    @Environment(\.appState) private var appState
    @StateObject private var viewModel = TrackingViewModel()
    @Binding var selectedTab: HomeTab
    @Binding var researchTickerSymbol: String?
    @State private var showProfile = false
    @State private var showSearch = false

    var body: some View {
        NavigationStack {
            ZStack {
                // Background
                AppColors.background
                    .ignoresSafeArea()

                // Main Content
                VStack(spacing: 0) {
                    // Header with Search and Tab Control
                    TrackingHeader(
                        selectedTab: $viewModel.selectedTab,
                        onSearchTapped: handleSearchTapped,
                        onProfileTapped: handleProfileTapped
                    )

                    // Tab Content
                    Group {
                        switch viewModel.selectedTab {
                        case .assets:
                            AssetsTabContent(viewModel: viewModel)
                        case .whales:
                            WhalesTabContent(viewModel: viewModel)
                        }
                    }
                }

                // Loading overlay
                if viewModel.isLoading {
                    LoadingOverlay()
                }
            }
            .sheet(isPresented: $viewModel.showAddAssetSheet) {
                AddAssetSheet(
                    onAssetAdded: { _ in
                        Task { await viewModel.refresh() }
                    },
                    onDismiss: {
                        viewModel.showAddAssetSheet = false
                    }
                )
            }
            .sheet(isPresented: $viewModel.showSortSheet) {
                SortOptionsSheet(
                    selectedOption: viewModel.sortOption,
                    onSelect: { option in
                        viewModel.selectSortOption(option)
                    },
                    onDismiss: {
                        viewModel.showSortSheet = false
                    }
                )
            }
            .sheet(isPresented: $viewModel.showPortfolioConfigSheet) {
                PortfolioConfigSheet(viewModel: viewModel)
            }
            .sheet(isPresented: $viewModel.showNewPortfolioSheet) {
                NewPortfolioSheet(viewModel: viewModel)
            }
            .sheet(isPresented: $viewModel.showEditPortfolioSheet) {
                EditPortfolioSheet(viewModel: viewModel)
            }
            .sheet(isPresented: $viewModel.showManageTickersSheet) {
                ManageTickersSheet(viewModel: viewModel)
            }
            .navigationDestination(item: $viewModel.selectedAssetNavigation) { selection in
                AssetDetailRouter(selection: selection, onNavigateToResearch: {
                    researchTickerSymbol = selection.symbol
                    selectedTab = .research
                })
            }
            .navigationDestination(item: $viewModel.selectedSearchResult) { selection in
                AssetDetailRouter(selection: selection, onNavigateToResearch: {
                    researchTickerSymbol = selection.symbol
                    selectedTab = .research
                })
            }
            .navigationDestination(item: $viewModel.selectedWhaleId) { whaleId in
                WhaleProfileView(whaleId: whaleId)
            }
            .navigationDestination(item: $viewModel.selectedTradeGroup) { tradeData in
                TradeGroupDetailView(
                    activity: tradeData.activity,
                    whaleName: tradeData.whaleName
                )
            }
            .sheet(item: $viewModel.selectedAlert) { alert in
                NavigationStack {
                    AlertDetailView(alert: alert)
                }
            }
            .fullScreenCover(isPresented: $showProfile) {
                ProfileView()
                    .environment(appState)
                    .environment(\.appState, appState)
            }
            .sheet(isPresented: $showSearch) {
                TickerLiveSearchSheet(
                    onTickerSelected: { selection in
                        showSearch = false
                        viewModel.selectedSearchResult = selection
                    },
                    onDismiss: {
                        showSearch = false
                    },
                    isInWatchlist: { ticker in viewModel.isOnWatchlist(ticker) },
                    onAddToWatchlist: { result in viewModel.addTickerFromSearch(result) }
                )
            }
        }
        // This tab is the most exposed of the four. Watchlist and portfolios are
        // `.guestAllowed` and partitioned PER INSTALL, so a guest and an account hold
        // genuinely DIFFERENT rows on the same device — and this screen had no reload
        // trigger of any kind: it reads `isActiveTab` nowhere, and AppState's session-end
        // teardown does not reach into ViewModels. Signing in or out left the previous
        // identity's holdings on screen until the user happened to pull-to-refresh.
        .reloadOnIdentityChange { await viewModel.reloadForIdentityChange() }
    }

    // MARK: - Action Handlers
    private func handleSearchTapped() {
        showSearch = true
    }

    private func handleProfileTapped() {
        showProfile = true
    }
}

// MARK: - Assets Tab Content
struct AssetsTabContent: View {
    @ObservedObject var viewModel: TrackingViewModel

    // Which custom header popup (portfolio switcher / sort+manage) is open.
    // Hosted here, above the list, via an anchor-preference overlay so the
    // popup floats over the scroll content and a tap anywhere dismisses it.
    @State private var activeHeaderMenu: PortfolioHeaderMenu?

    var body: some View {
        ScrollView(showsIndicators: false) {
            // Tight spacing between sections. Previously used negative padding
            // on children to shorten just this one gap, but that overlapped
            // the inner List's gesture recognizer with the next section and
            // froze the outer scroll. A smaller uniform spacing is safer.
            LazyVStack(spacing: AppSpacing.md) {
                // Active portfolio name (left) + "..." management menu (right).
                // Replaces the old Sort button — sort now lives inside the menu.
                PortfolioHeaderBar(viewModel: viewModel, activeMenu: $activeHeaderMenu)
                    .padding(.top, AppSpacing.sm)

                // Assets List Section — scoped to the active portfolio.
                // An empty list is ambiguous on its own ("you own nothing" vs
                // "we couldn't load it"), so the two states are told apart here:
                // a load failure shows the AppError copy + Retry, a genuinely
                // empty portfolio invites the user to add a ticker.
                if viewModel.filteredAssets.isEmpty {
                    AssetsPlaceholderCard(
                        errorMessage: viewModel.assetsErrorMessage,
                        isLoading: viewModel.isLoading,
                        onRetry: { Task { await viewModel.refresh() } },
                        onAdd: { viewModel.addNewAsset() }
                    )
                } else {
                    AssetsListSection(
                        assets: viewModel.filteredAssets,
                        onAssetTapped: { asset in viewModel.viewAssetDetail(asset) },
                        onRemoveAsset: { asset in viewModel.removeAsset(asset) },
                        onRemoveFromAll: { asset in viewModel.removeAssetFromAll(asset) }
                    )
                }

                // Alerts & Upcoming Events — filtered to this portfolio's tickers.
                AlertsEventsSection(
                    alerts: viewModel.filteredAlerts,
                    onAlertTapped: { alert in viewModel.viewAlertDetail(alert) }
                )
                // Extra breathing room between the holdings cards and this section.
                .padding(.top, AppSpacing.sm)

                // Portfolio Insights — computed locally from the active portfolio.
                PortfolioInsightsSection(
                    score: viewModel.displayedDiversificationScore,
                    coverageNote: viewModel.portfolioInsightsCoverageNote,
                    enteredHoldingsCount: viewModel.enteredHoldingsCount,
                    isEnabled: $viewModel.isInsightsEnabled,
                    onConfigureTapped: { viewModel.openPortfolioConfigSheet() }
                )

                // Bottom spacing for tab bar
                Spacer()
                    .frame(height: 100)
            }
        }
        .refreshable {
            await viewModel.refresh()
        }
        // Floating header popups (portfolio switcher + sort/manage). Anchored
        // to each trigger's bounds and drawn above the scroll content.
        .overlayPreferenceValue(PortfolioHeaderMenuAnchorKey.self) { anchors in
            GeometryReader { proxy in
                PortfolioHeaderMenuOverlay(
                    viewModel: viewModel,
                    activeMenu: $activeHeaderMenu,
                    anchors: anchors,
                    proxy: proxy
                )
            }
        }
        // Auto-open the config sheet the first time the user enables the
        // section without any holding data — saves them a tap.
        .onChange(of: viewModel.isInsightsEnabled) { _, isOn in
            if isOn && viewModel.displayedDiversificationScore == nil {
                viewModel.openPortfolioConfigSheet()
            }
        }
    }
}

// MARK: - Whales Tab Content
struct WhalesTabContent: View {
    @ObservedObject var viewModel: TrackingViewModel
    @Environment(\.appState) private var appState

    var body: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xl) {
                // 1. Followed Whale Profiles (horizontal scroll)
                if !viewModel.trackedWhales.isEmpty {
                    FollowedWhalesRow(
                        whales: viewModel.trackedWhales,
                        onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) },
                        onInactiveTapped: { whale in viewModel.viewInactiveWhale(whale) }
                    )
                    .padding(.top, AppSpacing.sm)
                }

                // 2. Recent Trades Timeline
                if !viewModel.groupedWhaleTrades.isEmpty {
                    WhaleTradesTimelineSection(
                        groupedTrades: viewModel.groupedWhaleTrades,
                        hiddenCount: viewModel.hiddenRecentTradeCount,
                        onActivityTapped: { activity in viewModel.viewTradeGroupDetail(activity) },
                        onMoreTapped: { viewModel.viewMoreRecentTrades() }
                    )
                }

                // 3. Most Popular Whales
                MostPopularWhalesSection(
                    heroWhales: viewModel.heroWhales,
                    whales: viewModel.popularWhales,
                    onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                    onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) },
                    onMoreTapped: { viewModel.viewMorePopularWhales() }
                )

                // Bottom spacing
                Spacer()
                    .frame(height: 100)
            }
        }
        .onAppear {
            viewModel.retryWhaleListIfNeeded()
        }
        .refreshable {
            await viewModel.refresh()
        }
        .navigationDestination(isPresented: $viewModel.showAllWhales) {
            AllWhalesView(viewModel: viewModel)
        }
        .navigationDestination(isPresented: $viewModel.showAllTrades) {
            AllRecentTradesView(viewModel: viewModel)
        }
        // A tapped LOCKED Follow pill. A PLAN gate, so the plan sheet — buying credits
        // would not free a tracking slot. `.environment(\.appState, appState)` is REQUIRED:
        // PaywallView reads the custom `\.appState` key, which a sheet does not inherit.
        .sheet(isPresented: $viewModel.showWhalePaywall) {
            PaywallView(context: .whaleFollowLimit)
                .environment(\.appState, appState)
        }
    }
}

// MARK: - Followed Whales Row (Horizontal Scroll)
struct FollowedWhalesRow: View {
    let whales: [TrendingWhale]
    var onWhaleTapped: ((TrendingWhale) -> Void)?
    /// Tapped a follow the current plan doesn't surface. The caller presents the paywall.
    var onInactiveTapped: ((TrendingWhale) -> Void)?

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: AppSpacing.lg) {
                ForEach(whales) { whale in
                    Button {
                        // A follow the plan doesn't cover leads to the offer, not the
                        // profile — tapping through to a whale whose trades the feed will
                        // not serve is the confusing half of this inconsistency.
                        if whale.isFollowingInactive {
                            onInactiveTapped?(whale)
                        } else {
                            onWhaleTapped?(whale)
                        }
                    } label: {
                        VStack(spacing: AppSpacing.sm) {
                            // Avatar. Follows are truncated on read, never deleted, so a
                            // Free account still HAS these rows — showing them at full
                            // strength while the feed below serves only the covered whale
                            // read as a bug. Dimmed + locked says "your plan, not a glitch".
                            ZStack(alignment: .bottomTrailing) {
                                WhaleAvatarView(
                                    name: whale.name,
                                    avatarURL: whale.avatarName.isEmpty ? nil : whale.avatarName,
                                    size: 64,
                                    category: whale.category
                                )
                                .opacity(whale.isFollowingInactive ? 0.45 : 1)

                                if whale.isFollowingInactive {
                                    Image(systemName: "lock.fill")
                                        .font(AppTypography.iconXS).fontWeight(.bold)
                                        // Text-role token — must clear 4.5:1 in both
                                        // appearances. A *Graphic one fails the launch audit.
                                        .foregroundColor(AppColors.primaryBlue)
                                        .padding(5)
                                        .background(Circle().fill(AppColors.cardBackground))
                                        .overlay(Circle().stroke(AppColors.cardEdge, lineWidth: 1))
                                }
                            }
                            .accessibilityLabel(
                                whale.isFollowingInactive
                                ? "\(whale.name), locked — upgrade to track"
                                : whale.name
                            )

                            // Name
                            Text(whale.name.components(separatedBy: " ").last ?? whale.name)
                                .font(AppTypography.caption)
                                .foregroundColor(whale.isFollowingInactive
                                                 ? AppColors.textMuted : AppColors.textPrimary)
                                .lineLimit(1)

                            // Trade count — or why this one is dark.
                            Text(whale.isFollowingInactive ? "Upgrade" : whale.formattedTradeCount)
                                .font(AppTypography.caption)
                                .foregroundColor(whale.isFollowingInactive
                                                 ? AppColors.primaryBlue : AppColors.textMuted)
                        }
                        .frame(width: 72)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

// MARK: - Whale Trades Timeline Section
struct WhaleTradesTimelineSection: View {
    let groupedTrades: [GroupedWhaleTrades]
    /// Trades withheld by the preview cap. `> 0` draws the "+N more trades" tail;
    /// 0 means this IS the whole feed and the timeline ends at its last card.
    var hiddenCount: Int = 0
    var onActivityTapped: ((WhaleTradeGroupActivity) -> Void)?
    var onMoreTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Section Header with "See All" button
            HStack {
                Text("Recent Trades")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button {
                    onMoreTapped?()
                } label: {
                    Text("See All")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.bottom, AppSpacing.md)

            // Timeline
            VStack(spacing: 0) {
                ForEach(Array(groupedTrades.enumerated()), id: \.element.id) { groupIndex, group in
                    ForEach(Array(group.activities.enumerated()), id: \.element.id) { activityIndex, activity in
                        let isFirst = groupIndex == 0 && activityIndex == 0
                        let isFinalCard = groupIndex == groupedTrades.count - 1
                            && activityIndex == group.activities.count - 1

                        WhaleTradeTimelineRow(
                            activity: activity,
                            showDate: activityIndex == 0,
                            isFirst: isFirst,
                            // The rail is drawn PER ROW and suppressed on the last one,
                            // so the final card must keep its connector when a tail
                            // follows — otherwise the tail's dot floats detached.
                            isLast: isFinalCard && hiddenCount == 0,
                            onTapped: { onActivityTapped?(activity) }
                        )
                        .padding(.horizontal, AppSpacing.lg)
                    }
                }

                if hiddenCount > 0 {
                    moreTradesRow
                        .padding(.horizontal, AppSpacing.lg)
                }
            }
        }
    }

    /// Tail of a truncated timeline: an open dot continuing the rail, then a tappable
    /// "+N more trades". Tappable rather than a passive "…" so the affordance is also
    /// the shortcut — the user doesn't have to scroll back up to the header's See All.
    ///
    /// Mirrors `WhaleTradeTimelineRow`'s geometry (20pt rail column, `AppSpacing.md`
    /// gutter) so the dot lands on the same vertical line as the cards' dots above it.
    private var moreTradesRow: some View {
        Button {
            onMoreTapped?()
        } label: {
            HStack(alignment: .top, spacing: AppSpacing.md) {
                // No connector: this row ends the rail.
                TimelineDot(isHollow: true)
                    .frame(width: 20, alignment: .top)

                HStack(spacing: AppSpacing.xs) {
                    Text("+\(hiddenCount) more trade\(hiddenCount == 1 ? "" : "s")")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.primaryBlue)

                    Image(systemName: "chevron.right")
                        .font(AppTypography.iconXS)
                        .foregroundColor(AppColors.primaryBlue)
                }
                // Nudge the label onto the dot's centre line — the dot is 8pt inside a
                // 20pt column, top-aligned like every row above.
                .offset(y: -2)

                Spacer(minLength: 0)
            }
            .padding(.bottom, AppSpacing.md)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("\(hiddenCount) more trades")
        .accessibilityHint("Shows all recent trades")
    }
}

// MARK: - Whale Trade Timeline Row
struct WhaleTradeTimelineRow: View {
    let activity: WhaleTradeGroupActivity
    let showDate: Bool
    let isFirst: Bool
    let isLast: Bool
    var onTapped: (() -> Void)?

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Timeline Column
            ZStack(alignment: .top) {
                // Background connector line (full height)
                if !isLast {
                    VStack(spacing: 0) {
                        Spacer()
                            .frame(height: 4) // Half of dot size to start from center
                        Rectangle()
                            .fill(AppColors.textMuted.opacity(0.3))
                            .frame(width: 1)
                    }
                }

                // Dot on top
                VStack(spacing: 0) {
                    if !isFirst {
                        Spacer()
                            .frame(height: 0)
                    }
                    TimelineDot()
                    Spacer()
                }
            }
            .frame(width: 20)

            // Content Column
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Date label — only on the first row of each date bucket
                if showDate {
                    Text(activity.formattedDate)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                // Trade Card
                WhaleTradeCard(activity: activity, onTapped: onTapped)
            }
            .padding(.bottom, AppSpacing.md)
        }
    }
}

// MARK: - Whale Trade Card
struct WhaleTradeCard: View {
    let activity: WhaleTradeGroupActivity
    var onTapped: (() -> Void)?

    var body: some View {
        Button {
            onTapped?()
        } label: {
            HStack(spacing: AppSpacing.md) {
                // Avatar
                Circle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 48, height: 48)
                    .overlay(
                        Image(systemName: "person.fill")
                            .font(AppTypography.iconXL)
                            .foregroundColor(AppColors.textMuted)
                    )

                // Info
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    HStack {
                        // Name (with firm for person-fronted whales) and trade count
                        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                            Text(activity.entityName)
                                .font(AppTypography.bodyEmphasis)
                                .foregroundColor(AppColors.textPrimary)

                            if let firm = activity.entityFirmName, !firm.isEmpty {
                                Text(firm)
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textSecondary)
                                    .lineLimit(1)
                            }

                            Text(activity.formattedTradeCount)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }

                        Spacer()

                        // Amount + Action badge
                        // For politicians, STOCK Act dollar midpoints are
                        // misleading — show only the action badge. Allocation
                        // arrows appear on the trade detail rows instead.
                        VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                            if activity.category != .politicians {
                                Text(activity.formattedAmount)
                                    .font(AppTypography.bodySmallEmphasis)
                                    .foregroundColor(activity.action.color)
                            }

                            Text(activity.action.rawValue)
                                .font(AppTypography.captionSmall).fontWeight(.bold)
                                .foregroundColor(activity.action.color)
                        }
                    }

                    // Summary (if available)
                    if let summary = activity.summary {
                        Text(summary)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                            .lineLimit(2)
                    }
                }

                // Chevron
                Image(systemName: "chevron.right")
                    .font(AppTypography.iconSmall).fontWeight(.medium)
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(AppSpacing.lg)
            .cardSurface(cornerRadius: AppCornerRadius.large)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Most Popular Whales Section
struct MostPopularWhalesSection: View {
    let heroWhales: [TrendingWhale]
    let whales: [TrendingWhale]
    var onFollowToggle: ((TrendingWhale) -> Void)?
    var onWhaleTapped: ((TrendingWhale) -> Void)?
    var onMoreTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Header with "See All" button
            HStack {
                Text("Most Popular")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button {
                    onMoreTapped?()
                } label: {
                    Text("See All")
                        .font(AppTypography.bodySmall)
                        .foregroundColor(AppColors.primaryBlue)
                }
                .buttonStyle(.plain)
            }
            .padding(.horizontal, AppSpacing.lg)

            // Hero Carousel
            if !heroWhales.isEmpty {
                WhaleHeroCarousel(
                    whales: heroWhales,
                    onWhaleTapped: onWhaleTapped
                )
            }

            // List below hero
            VStack(spacing: AppSpacing.md) {
                ForEach(whales) { whale in
                    WhaleCard(
                        whale: whale,
                        onFollowToggle: { onFollowToggle?(whale) },
                        onTap: { onWhaleTapped?(whale) }
                    )
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

// MARK: - Whale Hero Carousel
struct WhaleHeroCarousel: View {
    let whales: [TrendingWhale]
    var onWhaleTapped: ((TrendingWhale) -> Void)?
    @State private var currentIndex: Int = 0

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            TabView(selection: $currentIndex) {
                ForEach(Array(whales.enumerated()), id: \.element.id) { index, whale in
                    WhaleHeroCard(whale: whale) {
                        onWhaleTapped?(whale)
                    }
                    .tag(index)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: .never))
            .frame(height: 200)

            // Page indicators
            HStack(spacing: AppSpacing.sm) {
                ForEach(0..<whales.count, id: \.self) { index in
                    Circle()
                        .fill(currentIndex == index ? AppColors.primaryBlue : AppColors.textMuted.opacity(0.4))
                        .frame(width: 7, height: 7)
                        .animation(.easeInOut(duration: 0.2), value: currentIndex)
                }
            }
        }
    }
}

// MARK: - Whale Hero Card
struct WhaleHeroCard: View {
    let whale: TrendingWhale
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            ZStack(alignment: .bottomLeading) {
                // Background gradient
                LinearGradient(
                    colors: [
                        AppColors.primaryBlue.opacity(0.6),
                        AppColors.cardBackground
                    ],
                    startPoint: .topTrailing,
                    endPoint: .bottomLeading
                )

                // Content
                HStack(spacing: AppSpacing.lg) {
                    // Left side - text info
                    VStack(alignment: .leading, spacing: AppSpacing.sm) {
                        Spacer()

                        Text(whale.name)
                            .font(AppTypography.titleCompact)
                            .foregroundColor(AppColors.textPrimary)

                        if !whale.title.isEmpty {
                            Text(whale.title)
                                .font(AppTypography.bodySmall)
                                .foregroundColor(AppColors.accentCyan)
                        }

                        if !whale.description.isEmpty {
                            Text(whale.description)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                                .lineLimit(2)
                        }

                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "person.2.fill")
                                .font(AppTypography.iconXS)
                                .foregroundColor(AppColors.textMuted)

                            Text(whale.formattedFollowers)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
                        }
                        .padding(.top, AppSpacing.xs)
                    }
                    .padding(AppSpacing.lg)

                    Spacer()

                    // Right side - avatar
                    VStack {
                        Spacer()
                        WhaleAvatarView(
                            name: whale.name,
                            avatarURL: whale.avatarName.isEmpty ? nil : whale.avatarName,
                            size: 80,
                            category: whale.category
                        )
                        .shadow(color: AppColors.shadowKey, radius: 8, x: 0, y: 4)
                        Spacer()
                    }
                    .padding(.trailing, AppSpacing.xl)
                }
            }
            .cornerRadius(AppCornerRadius.extraLarge)
            .padding(.horizontal, AppSpacing.lg)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Whale Card
struct WhaleCard: View {
    let whale: TrendingWhale
    var onFollowToggle: (() -> Void)?
    var onTap: (() -> Void)?

    var body: some View {
        Button {
            onTap?()
        } label: {
            HStack(spacing: AppSpacing.md) {
                // Avatar
                WhaleAvatarView(
                    name: whale.name,
                    avatarURL: whale.avatarName.isEmpty ? nil : whale.avatarName,
                    size: 44,
                    category: whale.category
                )

                // Info — a person-fronted whale always shows their firm with
                // the name ("Ray Dalio / Bridgewater Associates").
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(whale.name)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    if let firm = whale.firmName, !firm.isEmpty {
                        Text(firm)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                            .lineLimit(1)
                    }

                    Text(whale.formattedFollowers)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer()

                // Follow Button — or a lock, when the plan doesn't allow tracking this
                // whale. The row itself stays fully browsable and tappable through to the
                // profile; only this control changes.
                //
                // A locked tap still calls `onFollowToggle`: `TrackingViewModel` owns the
                // decision and raises the paywall, so the three list layers between here
                // and the screen don't each need a second closure threaded through them.
                Button {
                    onFollowToggle?()
                } label: {
                    WhaleFollowPillLabel(whale: whale)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(
                    whale.isLocked
                        ? "Follow \(whale.name), locked"
                        : (whale.isFollowing ? "Following \(whale.name)" : "Follow \(whale.name)")
                )
                .accessibilityHint(whale.isLocked ? "Shows upgrade options" : "")
            }
            .padding(AppSpacing.lg)
            .cardSurface(cornerRadius: AppCornerRadius.large)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Follow Pill Label

/// The Follow / Following / 🔒 pill, shared by `WhaleCard` and `AllWhalesView` so the three
/// states cannot drift between the two lists that show them side by side.
///
/// Locked renders as a lock glyph + "Follow" on the muted surface rather than the saturated
/// primary fill: a locked control that still looks like the primary call to action invites
/// a tap that can only be refused.
struct WhaleFollowPillLabel: View {
    let whale: TrendingWhale

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            if whale.isLocked {
                // Text-role token — this glyph must clear 4.5:1 in both appearances.
                Image(systemName: "lock.fill")
                    .font(AppTypography.iconXS)
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.primaryBlue)
            }
            Text(whale.isFollowing ? "Following" : "Follow")
                .font(AppTypography.bodySmallEmphasis)
                .foregroundColor(foreground)
        }
        .padding(.horizontal, AppSpacing.lg)
        .padding(.vertical, AppSpacing.sm)
        .background(background)
        .cornerRadius(AppCornerRadius.pill)
    }

    private var foreground: Color {
        if whale.isLocked { return AppColors.primaryBlue }
        return whale.isFollowing ? AppColors.textSecondary : AppColors.textOnAccent
    }

    private var background: Color {
        if whale.isLocked || whale.isFollowing { return AppColors.cardBackgroundLight }
        return AppColors.primaryFill
    }
}

// MARK: - Add Asset Sheet
struct AddAssetSheet: View {
    @State private var searchText = ""
    @State private var searchResults: [StockSearchResult] = []
    @State private var isSearching = false
    @State private var isAdding = false
    @State private var addError: String?
    @State private var searchTask: Task<Void, Never>?

    var onAssetAdded: ((String) -> Void)?
    var onDismiss: (() -> Void)?

    private let stockRepository = StockRepository.shared

    var body: some View {
        NavigationView {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                VStack(spacing: AppSpacing.lg) {
                    SearchBar(
                        text: $searchText,
                        placeholder: "Search ticker symbol..."
                    )
                    .padding(.horizontal, AppSpacing.lg)

                    if isAdding {
                        VStack(spacing: AppSpacing.md) {
                            ProgressView()
                            Text("Adding to watchlist...")
                                .font(AppTypography.bodySmall)
                                .foregroundColor(AppColors.textSecondary)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if searchText.isEmpty {
                        VStack(spacing: AppSpacing.md) {
                            Image(systemName: "magnifyingglass")
                                .font(AppTypography.iconHero)
                                .foregroundColor(AppColors.textMuted)

                            Text("Search for a stock to add to your watchlist")
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textSecondary)
                                .multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if isSearching {
                        ProgressView()
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else if searchResults.isEmpty {
                        VStack(spacing: AppSpacing.md) {
                            Image(systemName: "magnifyingglass")
                                .font(AppTypography.iconLarge)
                                .foregroundColor(AppColors.textMuted)

                            Text("No results found for \"\(searchText)\"")
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textSecondary)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        ScrollView {
                            LazyVStack(spacing: AppSpacing.sm) {
                                ForEach(searchResults) { result in
                                    Button {
                                        addAsset(result)
                                    } label: {
                                        HStack(spacing: AppSpacing.md) {
                                            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                                                Text(result.ticker)
                                                    .font(AppTypography.bodyEmphasis)
                                                    .foregroundColor(AppColors.textPrimary)

                                                Text(result.companyName)
                                                    .font(AppTypography.caption)
                                                    .foregroundColor(AppColors.textSecondary)
                                                    .lineLimit(1)
                                            }

                                            Spacer()

                                            if let exchange = result.exchange {
                                                Text(exchange)
                                                    .font(AppTypography.captionSmall)
                                                    .foregroundColor(AppColors.textMuted)
                                                    .padding(.horizontal, AppSpacing.sm)
                                                    .padding(.vertical, AppSpacing.xxs)
                                                    .background(AppColors.cardBackgroundLight)
                                                    .cornerRadius(AppCornerRadius.small)
                                            }

                                            Image(systemName: "plus.circle.fill")
                                                .font(AppTypography.iconLarge)
                                                .foregroundColor(AppColors.primaryBlue)
                                        }
                                        .padding(AppSpacing.md)
                                        .cardSurface(cornerRadius: AppCornerRadius.medium)
                                    }
                                    .buttonStyle(.plain)
                                }
                            }
                            .padding(.horizontal, AppSpacing.lg)
                        }
                    }

                    if let error = addError {
                        Text(error)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.bearish)
                            .padding(.horizontal, AppSpacing.lg)
                    }

                    Spacer()
                }
                .padding(.top, AppSpacing.lg)
            }
            .navigationTitle("Add Asset")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        onDismiss?()
                    }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .onChange(of: searchText) { _, newValue in
            debounceSearch(newValue)
        }
    }

    private func debounceSearch(_ query: String) {
        searchTask?.cancel()
        addError = nil

        guard !query.isEmpty else {
            searchResults = []
            return
        }

        searchTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 300_000_000) // 300ms debounce
            guard !Task.isCancelled else { return }

            isSearching = true
            do {
                searchResults = try await stockRepository.searchStocks(query: query, limit: 10)
                print("[AddAsset] ✅ Search returned \(searchResults.count) results for '\(query)'")
            } catch {
                print("[AddAsset] ❌ Search failed: \(error)")
                searchResults = []
            }
            isSearching = false
        }
    }

    private func addAsset(_ result: StockSearchResult) {
        isAdding = true
        addError = nil

        Task { @MainActor in
            do {
                try await APIClient.shared.request(
                    endpoint: .addToWatchlist(stockId: result.ticker)
                )
                print("[AddAsset] ✅ Added \(result.ticker) to watchlist")
                // Watchlist add succeeded — also push the ticker into the
                // active portfolio so the user sees it immediately. A failure
                // here IS user-visible (the ticker is on the watchlist but not
                // in the portfolio they were looking at), so it is reported
                // rather than swallowed by a bare `try?`.
                do {
                    try await PortfolioStore.shared.addTicker(result.ticker)
                } catch {
                    AppActions.shared.reportMutationFailure(
                        error, action: "add \(result.ticker) to this portfolio"
                    )
                }
                onAssetAdded?(result.ticker)
                onDismiss?()
            } catch {
                // Most common failure: the ticker is already on the master
                // watchlist. Either way, push it into the active portfolio —
                // the user clearly wants it here. The store call is idempotent.
                do {
                    try await PortfolioStore.shared.addTicker(result.ticker)
                } catch {
                    AppActions.shared.reportMutationFailure(
                        error, action: "add \(result.ticker) to this portfolio"
                    )
                }
                // Don't assert "already in your watchlist" for every failure — that copy
                // actively misleads when the real cause was auth or connectivity. Use the
                // mapped message, and keep the reassuring line only for a genuine conflict.
                let mapped = AppError.from(error)
                if case .apiError(let code, _) = mapped, code == "ALREADY_EXISTS" {
                    addError = "\(result.ticker) is already in your watchlist."
                } else {
                    addError = "Couldn't add \(result.ticker). \(mapped.message)"
                }
                isAdding = false
            }
        }
    }
}

// MARK: - Sort Options Sheet
struct SortOptionsSheet: View {
    let selectedOption: AssetSortOption
    var onSelect: ((AssetSortOption) -> Void)?
    var onDismiss: (() -> Void)?

    var body: some View {
        NavigationView {
            List {
                ForEach(AssetSortOption.allCases, id: \.self) { option in
                    Button {
                        onSelect?(option)
                    } label: {
                        HStack {
                            Text(option.displayName)
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)

                            Spacer()

                            if selectedOption == option {
                                Image(systemName: "checkmark")
                                    .foregroundColor(AppColors.primaryBlue)
                            }
                        }
                    }
                    .listRowBackground(AppColors.cardBackground)
                }
            }
            .listStyle(InsetGroupedListStyle())
            .scrollContentBackground(.hidden)
            .background(AppColors.background)
            .navigationTitle("Sort By")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        onDismiss?()
                    }
                    .fontWeight(.semibold)
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .presentationDetents([.medium])
    }
}

// MARK: - Portfolio Insights Config Sheet
//
// One-screen editor for the active portfolio's per-portfolio holdings.
// Lists every ticker in that portfolio with an editable shares-or-dollars
// input; values are scoped to this portfolio only (the same ticker in a
// different portfolio carries its own values). Saving fires a single bulk
// PUT to /api/v1/portfolios/{id}/holdings.
//
// Inlined here (rather than in Views/Sheets/) so it ships in the same file
// Xcode already knows about — avoids the FS-sync gotcha that bit AddHoldingSheet.

private enum HoldingInputMode: String, CaseIterable {
    case shares = "Shares"
    case dollars = "Dollars"
}

private struct PortfolioConfigRow: Identifiable {
    let id: String      // ticker — stable, unique per row
    let ticker: String
    let companyName: String
    var inputMode: HoldingInputMode
    var sharesInput: String
    var dollarsInput: String

    init(item: PortfolioItem, companyName: String) {
        self.id = item.ticker
        self.ticker = item.ticker
        self.companyName = companyName
        if let s = item.shares, s > 0 {
            self.inputMode = .shares
            self.sharesInput = Self.formatNumber(s)
            self.dollarsInput = ""
        } else if let v = item.marketValue, v > 0 {
            self.inputMode = .dollars
            self.sharesInput = ""
            self.dollarsInput = Self.formatNumber(v)
        } else {
            self.inputMode = .shares
            self.sharesInput = ""
            self.dollarsInput = ""
        }
    }

    /// Render a number without a trailing ".0" so a clean integer round-trips
    /// as "100" instead of "100.0" in the text field.
    private static func formatNumber(_ value: Double) -> String {
        if value.truncatingRemainder(dividingBy: 1) == 0 {
            return String(Int(value))
        }
        return String(value)
    }

    /// Round to a fixed decimal precision before formatting, so a shares→
    /// dollars→shares round-trip lands cleanly on the original integer
    /// instead of accumulating floating-point dust like 9.99999999.
    private static func formatRounded(_ value: Double, places: Int) -> String {
        let multiplier = pow(10.0, Double(places))
        let rounded = (value * multiplier).rounded() / multiplier
        return formatNumber(rounded)
    }

    /// Switch input mode and convert the entered value across, using the
    /// live ticker price. We only convert when the price is usable and the
    /// source field actually parses to a positive number; otherwise the
    /// destination field is left untouched and the user just sees a mode
    /// flip — never a silent zeroing.
    mutating func setInputMode(_ newMode: HoldingInputMode, price: Double?) {
        guard newMode != inputMode else { return }
        if let price, price > 0 {
            switch newMode {
            case .dollars:
                if let shares = Double(sharesInput), shares > 0 {
                    dollarsInput = Self.formatRounded(shares * price, places: 2)
                }
            case .shares:
                if let dollars = Double(dollarsInput), dollars > 0 {
                    sharesInput = Self.formatRounded(dollars / price, places: 4)
                }
            }
        }
        inputMode = newMode
    }

    /// Build the wire payload for this row. A row with empty inputs becomes
    /// a clear (both fields nil) on the server.
    func toUpdateItem() -> HoldingUpdateItem {
        switch inputMode {
        case .shares:
            let parsed = Double(sharesInput)
            let value = (parsed ?? 0) > 0 ? parsed : nil
            return HoldingUpdateItem(ticker: ticker, shares: value, marketValue: nil)
        case .dollars:
            let parsed = Double(dollarsInput)
            let value = (parsed ?? 0) > 0 ? parsed : nil
            return HoldingUpdateItem(ticker: ticker, shares: nil, marketValue: value)
        }
    }
}

struct PortfolioConfigSheet: View {
    @ObservedObject var viewModel: TrackingViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var rows: [PortfolioConfigRow] = []
    @State private var isSubmitting: Bool = false
    @State private var saveError: String?

    var body: some View {
        NavigationView {
            ZStack {
                AppColors.background
                    .ignoresSafeArea()

                if rows.isEmpty {
                    emptyState
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            Text("Enter shares or dollar amount for each ticker in this portfolio. Leave empty to skip — it stays in the portfolio but won't count toward the score.")
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                                .padding(.horizontal, AppSpacing.lg)

                            VStack(spacing: AppSpacing.sm) {
                                let priceByTicker = Dictionary(uniqueKeysWithValues:
                                    viewModel.trackedAssets.map { ($0.ticker.uppercased(), $0.price) })
                                ForEach($rows) { $row in
                                    PortfolioConfigRowView(
                                        row: $row,
                                        price: priceByTicker[$row.wrappedValue.ticker.uppercased()]
                                    )
                                }
                            }
                            .padding(.horizontal, AppSpacing.lg)
                        }
                        .padding(.vertical, AppSpacing.lg)
                    }
                }

                if let error = saveError {
                    VStack {
                        Spacer()
                        Text(error)
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textOnFill)
                            .padding(AppSpacing.md)
                            .background(AppColors.lossFill)
                            .cornerRadius(AppCornerRadius.medium)
                            .padding(.bottom, AppSpacing.xl)
                    }
                }
            }
            .navigationTitle("Portfolio Insights")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") {
                        dismiss()
                    }
                    .disabled(isSubmitting)
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button(isSubmitting ? "Saving…" : "Save") {
                        save()
                    }
                    .fontWeight(.semibold)
                    .disabled(isSubmitting)
                }
            }
        }
        .onAppear { syncRows() }
        // The active portfolio is the source of truth — re-sync whenever its
        // membership changes (user added/removed a ticker while the sheet
        // was open, or the portfolio just finished loading).
        .onChange(of: viewModel.portfolioStore.activePortfolio?.items.count ?? 0) { _, _ in
            syncRows()
        }
        .onChange(of: viewModel.portfolioStore.activePortfolioId) { _, _ in
            syncRows()
        }
    }

    private var emptyState: some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "list.bullet.rectangle")
                .font(AppTypography.iconHero)
                .foregroundColor(AppColors.textMuted)

            Text("Add tickers to this portfolio first")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xl)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// Reconcile `rows` with the active portfolio's items. Reads directly
    /// from `PortfolioStore.activePortfolio.items` (which loads with the
    /// portfolio response) instead of `viewModel.filteredAssets` — the
    /// latter is async and previously caused the sheet to render blank when
    /// it opened before the trackedAssets fetch resolved. Joins with
    /// trackedAssets only for the company-name display label.
    private func syncRows() {
        guard let active = viewModel.portfolioStore.activePortfolio else {
            rows = []
            return
        }
        let companyByTicker = Dictionary(uniqueKeysWithValues:
            viewModel.trackedAssets.map { ($0.ticker.uppercased(), $0.companyName) })
        let existingByTicker = Dictionary(uniqueKeysWithValues: rows.map { ($0.ticker, $0) })
        rows = active.items.map { item in
            if let existing = existingByTicker[item.ticker] {
                return existing
            }
            return PortfolioConfigRow(
                item: item,
                companyName: companyByTicker[item.ticker.uppercased()] ?? item.ticker
            )
        }
    }

    private func save() {
        isSubmitting = true
        saveError = nil
        let items = rows.map { $0.toUpdateItem() }
        Task { @MainActor in
            do {
                try await viewModel.savePortfolioHoldings(items)
                isSubmitting = false
                dismiss()
            } catch {
                print("[PortfolioConfigSheet] ❌ Save failed: \(error)")
                saveError = "Couldn't save. Pull down and try again."
                isSubmitting = false
            }
        }
    }
}

private struct PortfolioConfigRowView: View {
    @Binding var row: PortfolioConfigRow
    let price: Double?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(spacing: AppSpacing.sm) {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(row.ticker)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Text(row.companyName)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(1)
                }

                Spacer()

                Picker("Input mode", selection: inputModeBinding) {
                    ForEach(HoldingInputMode.allCases, id: \.self) { mode in
                        Text(mode.rawValue).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .frame(width: 160)
            }

            if row.inputMode == .shares {
                TextField("Shares (e.g. 25)", text: $row.sharesInput)
                    .keyboardType(.decimalPad)
                    .textFieldStyle(.roundedBorder)
            } else {
                TextField("Dollars (e.g. 12500)", text: $row.dollarsInput)
                    .keyboardType(.decimalPad)
                    .textFieldStyle(.roundedBorder)
            }
        }
        .padding(AppSpacing.md)
        .cardSurface(cornerRadius: AppCornerRadius.medium)
    }

    /// Routes the picker through `setInputMode` so the destination field
    /// gets the converted value at the moment the user toggles.
    private var inputModeBinding: Binding<HoldingInputMode> {
        Binding(
            get: { row.inputMode },
            set: { row.setInputMode($0, price: price) }
        )
    }
}

// MARK: - Preview
#Preview {
    TrackingContentView()
}
