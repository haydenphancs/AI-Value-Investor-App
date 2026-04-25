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
                    .preferredColorScheme(.dark)
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
                    .preferredColorScheme(.dark)
            }
            .sheet(isPresented: $showSearch) {
                TickerLiveSearchSheet(
                    onTickerSelected: { selection in
                        showSearch = false
                        viewModel.selectedSearchResult = selection
                    },
                    onDismiss: {
                        showSearch = false
                    }
                )
                .preferredColorScheme(.dark)
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
                    .preferredColorScheme(.dark)
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
                    .preferredColorScheme(.dark)
            }
            .sheet(isPresented: $showSearch) {
                TickerLiveSearchSheet(
                    onTickerSelected: { selection in
                        showSearch = false
                        viewModel.selectedSearchResult = selection
                    },
                    onDismiss: {
                        showSearch = false
                    }
                )
                .preferredColorScheme(.dark)
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

// MARK: - Assets Tab Content
struct AssetsTabContent: View {
    @ObservedObject var viewModel: TrackingViewModel

    var body: some View {
        ScrollView(showsIndicators: false) {
            // Tight spacing between sections. Previously used negative padding
            // on children to shorten just this one gap, but that overlapped
            // the inner List's gesture recognizer with the next section and
            // froze the outer scroll. A smaller uniform spacing is safer.
            LazyVStack(spacing: AppSpacing.md) {
                // Assets List Section
                AssetsListSection(
                    assets: viewModel.filteredAssets,
                    onSortTapped: { viewModel.openSortOptions() },
                    onAssetTapped: { asset in viewModel.viewAssetDetail(asset) },
                    onRemoveAsset: { asset in viewModel.removeAsset(asset) }
                )
                .padding(.top, AppSpacing.sm)

                // Alerts & Upcoming Events Section
                AlertsEventsSection(
                    alerts: viewModel.alerts,
                    onAlertTapped: { alert in viewModel.viewAlertDetail(alert) }
                )

                // Portfolio Insights Section
                PortfolioInsightsSection(
                    score: viewModel.diversificationScore,
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
        // Auto-open the config sheet the first time the user enables the
        // section without any holding data — saves them a tap.
        .onChange(of: viewModel.isInsightsEnabled) { _, isOn in
            if isOn && viewModel.diversificationScore == nil {
                viewModel.openPortfolioConfigSheet()
            }
        }
    }
}

// MARK: - Whales Tab Content
struct WhalesTabContent: View {
    @ObservedObject var viewModel: TrackingViewModel

    var body: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xl) {
                // 1. Followed Whale Profiles (horizontal scroll)
                if !viewModel.trackedWhales.isEmpty {
                    FollowedWhalesRow(
                        whales: viewModel.trackedWhales,
                        onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                    )
                    .padding(.top, AppSpacing.sm)
                }

                // 2. Recent Trades Timeline
                if !viewModel.groupedWhaleTrades.isEmpty {
                    WhaleTradesTimelineSection(
                        groupedTrades: viewModel.groupedWhaleTrades,
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
    }
}

// MARK: - Followed Whales Row (Horizontal Scroll)
struct FollowedWhalesRow: View {
    let whales: [TrendingWhale]
    var onWhaleTapped: ((TrendingWhale) -> Void)?

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: AppSpacing.lg) {
                ForEach(whales) { whale in
                    Button {
                        onWhaleTapped?(whale)
                    } label: {
                        VStack(spacing: AppSpacing.sm) {
                            // Avatar
                            WhaleAvatarView(
                                name: whale.name,
                                avatarURL: whale.avatarName.isEmpty ? nil : whale.avatarName,
                                size: 64,
                                category: whale.category
                            )

                            // Name
                            Text(whale.name.components(separatedBy: " ").last ?? whale.name)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textPrimary)
                                .lineLimit(1)

                            // Trade count
                            Text(whale.formattedTradeCount)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textMuted)
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
    var onActivityTapped: ((WhaleTradeGroupActivity) -> Void)?
    var onMoreTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Section Header with "more" button
            HStack {
                Text("Recent Trades")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button {
                    onMoreTapped?()
                } label: {
                    Text("more")
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
                        let isLast = groupIndex == groupedTrades.count - 1
                            && activityIndex == group.activities.count - 1

                        WhaleTradeTimelineRow(
                            activity: activity,
                            showDate: activityIndex == 0,
                            isFirst: isFirst,
                            isLast: isLast,
                            onTapped: { onActivityTapped?(activity) }
                        )
                        .padding(.horizontal, AppSpacing.lg)
                    }
                }
            }
        }
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
                        // Name and trade count
                        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                            Text(activity.entityName)
                                .font(AppTypography.bodyEmphasis)
                                .foregroundColor(AppColors.textPrimary)

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
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
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
            // Header with "more" button
            HStack {
                Text("Most Popular")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button {
                    onMoreTapped?()
                } label: {
                    Text("more")
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
                        .shadow(color: Color.black.opacity(0.3), radius: 8, x: 0, y: 4)
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

                // Info
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(whale.name)
                        .font(AppTypography.bodyEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Text(whale.formattedFollowers)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                // Follow Button
                Button {
                    onFollowToggle?()
                } label: {
                    Text(whale.isFollowing ? "Following" : "Follow")
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(whale.isFollowing ? AppColors.textSecondary : .white)
                        .padding(.horizontal, AppSpacing.lg)
                        .padding(.vertical, AppSpacing.sm)
                        .background(whale.isFollowing ? AppColors.cardBackgroundLight : AppColors.primaryBlue)
                        .cornerRadius(AppCornerRadius.pill)
                }
                .buttonStyle(.plain)
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(.plain)
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
                                        .background(AppColors.cardBackground)
                                        .cornerRadius(AppCornerRadius.medium)
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
                onAssetAdded?(result.ticker)
                onDismiss?()
            } catch {
                print("[AddAsset] ❌ Failed to add \(result.ticker): \(error)")
                addError = "Failed to add \(result.ticker). It may already be in your watchlist."
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
            .toolbarColorScheme(.dark, for: .navigationBar)
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
        .preferredColorScheme(.dark)
    }
}

// MARK: - Portfolio Insights Config Sheet
//
// One-screen editor for the user's Portfolio Insights data. Lists every ticker
// on their watchlist (Assets tab) with an editable shares-or-dollars input.
// Saving fires a single bulk PUT to /tracking/assets/holdings.
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

    init(asset: TrackedAsset) {
        self.id = asset.ticker
        self.ticker = asset.ticker
        self.companyName = asset.companyName
        if let s = asset.shares, s > 0 {
            self.inputMode = .shares
            self.sharesInput = Self.formatNumber(s)
            self.dollarsInput = ""
        } else if let v = asset.marketValue, v > 0 {
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

                if viewModel.trackedAssets.isEmpty {
                    emptyState
                } else {
                    ScrollView {
                        VStack(alignment: .leading, spacing: AppSpacing.md) {
                            Text("Enter shares or dollar amount for each ticker. Leave empty to skip — it stays on your watchlist but won't count toward the score.")
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                                .padding(.horizontal, AppSpacing.lg)

                            VStack(spacing: AppSpacing.sm) {
                                ForEach($rows) { $row in
                                    PortfolioConfigRowView(row: $row)
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
                            .foregroundColor(.white)
                            .padding(AppSpacing.md)
                            .background(AppColors.bearish)
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
        // The sheet can auto-open before the first /tracking/assets fetch
        // resolves (toggle flips → sheet shows immediately), so re-sync when
        // the asset list arrives. Watching the count keeps the dependency
        // Equatable without making TrackedAsset itself Equatable.
        .onChange(of: viewModel.trackedAssets.count) { _, _ in
            syncRows()
        }
    }

    private var emptyState: some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "list.bullet.rectangle")
                .font(AppTypography.iconHero)
                .foregroundColor(AppColors.textMuted)

            Text("Add tickers to your Assets first")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, AppSpacing.xl)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// Reconcile `rows` with `viewModel.trackedAssets`. Adds rows for new
    /// tickers and drops rows for tickers that vanished from the watchlist;
    /// preserves any in-progress input the user already typed for tickers
    /// that still exist.
    private func syncRows() {
        let existingByTicker = Dictionary(uniqueKeysWithValues: rows.map { ($0.ticker, $0) })
        rows = viewModel.trackedAssets.map { asset in
            existingByTicker[asset.ticker] ?? PortfolioConfigRow(asset: asset)
        }
    }

    private func save() {
        isSubmitting = true
        saveError = nil
        let items = rows.map { $0.toUpdateItem() }
        Task { @MainActor in
            do {
                try await viewModel.saveHoldingsConfig(items)
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

                Picker("Input mode", selection: $row.inputMode) {
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
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

// MARK: - Preview
#Preview {
    TrackingContentView()
        .preferredColorScheme(.dark)
}
