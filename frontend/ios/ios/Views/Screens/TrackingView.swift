//
//  TrackingView.swift
//  ios
//
//  Main Tracking screen with Assets and Whales tabs
//

import SwiftUI

// MARK: - Navigation Models
/// Wrapper for trade group navigation to conform to Hashable
struct TradeGroupNavigation: Identifiable, Hashable {
    let id: String
    let tradeGroup: WhaleTradeGroup
    let whaleName: String
    
    init(tradeGroup: WhaleTradeGroup, whaleName: String) {
        self.id = tradeGroup.id
        self.tradeGroup = tradeGroup
        self.whaleName = whaleName
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
    @StateObject private var viewModel = TrackingViewModel()

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
                        searchText: $viewModel.searchText,
                        selectedTab: $viewModel.selectedTab,
                        onProfileTapped: handleProfileTapped,
                        onSearchSubmit: handleSearchSubmit
                    )

                    // Tab Content
                    TabView(selection: $viewModel.selectedTab) {
                        // Assets Tab
                        AssetsTabContent(viewModel: viewModel)
                            .tag(TrackingTab.assets)

                        // Whales Tab
                        WhalesTabContent(viewModel: viewModel)
                            .tag(TrackingTab.whales)
                    }
                    .tabViewStyle(.page(indexDisplayMode: .never))
                    .animation(.easeInOut(duration: 0.2), value: viewModel.selectedTab)
                }

                // Loading overlay
                if viewModel.isLoading {
                    LoadingOverlay()
                }
            }
            .sheet(isPresented: $viewModel.showAddAssetSheet) {
                AddAssetSheet(onDismiss: {
                    viewModel.showAddAssetSheet = false
                })
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
            .navigationDestination(item: $viewModel.selectedTickerSymbol) { ticker in
                TickerDetailView(tickerSymbol: ticker)
            }
            .navigationDestination(item: $viewModel.selectedWhaleId) { whaleId in
                WhaleProfileView(whaleId: whaleId)
            }
            .navigationDestination(item: $viewModel.selectedTradeGroup) { tradeData in
                TradeGroupDetailView(
                    tradeGroup: tradeData.tradeGroup,
                    whaleName: tradeData.whaleName
                )
            }
        }
    }

    // MARK: - Action Handlers
    private func handleProfileTapped() {
        print("Profile tapped")
    }

    private func handleSearchSubmit() {
        print("Search submitted: \(viewModel.searchText)")
    }
}

// MARK: - TrackingContentViewWithBinding (Used when tab navigation needed)
struct TrackingContentViewWithBinding: View {
    @StateObject private var viewModel = TrackingViewModel()
    @Binding var selectedTab: HomeTab
    @Binding var researchTickerSymbol: String?

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
                        searchText: $viewModel.searchText,
                        selectedTab: $viewModel.selectedTab,
                        onProfileTapped: handleProfileTapped,
                        onSearchSubmit: handleSearchSubmit
                    )

                    // Tab Content
                    TabView(selection: $viewModel.selectedTab) {
                        // Assets Tab
                        AssetsTabContent(viewModel: viewModel)
                            .tag(TrackingTab.assets)

                        // Whales Tab
                        WhalesTabContent(viewModel: viewModel)
                            .tag(TrackingTab.whales)
                    }
                    .tabViewStyle(.page(indexDisplayMode: .never))
                    .animation(.easeInOut(duration: 0.2), value: viewModel.selectedTab)
                }

                // Loading overlay
                if viewModel.isLoading {
                    LoadingOverlay()
                }
            }
            .sheet(isPresented: $viewModel.showAddAssetSheet) {
                AddAssetSheet(onDismiss: {
                    viewModel.showAddAssetSheet = false
                })
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
            .navigationDestination(item: $viewModel.selectedTickerSymbol) { ticker in
                TickerDetailView(tickerSymbol: ticker, onNavigateToResearch: {
                    researchTickerSymbol = ticker
                    selectedTab = .research
                })
            }
            .navigationDestination(item: $viewModel.selectedWhaleId) { whaleId in
                WhaleProfileView(whaleId: whaleId)
            }
            .navigationDestination(item: $viewModel.selectedTradeGroup) { tradeData in
                TradeGroupDetailView(
                    tradeGroup: tradeData.tradeGroup,
                    whaleName: tradeData.whaleName
                )
            }
        }
    }

    // MARK: - Action Handlers
    private func handleProfileTapped() {
        print("Profile tapped")
    }

    private func handleSearchSubmit() {
        print("Search submitted: \(viewModel.searchText)")
    }
}

// MARK: - Assets Tab Content
struct AssetsTabContent: View {
    @ObservedObject var viewModel: TrackingViewModel

    var body: some View {
        ScrollView(showsIndicators: false) {
            LazyVStack(spacing: AppSpacing.xxl) {
                // Assets List Section
                AssetsListSection(
                    assets: viewModel.filteredAssets,
                    onSortTapped: { viewModel.openSortOptions() },
                    onAddTapped: { viewModel.addNewAsset() },
                    onAssetTapped: { asset in viewModel.viewAssetDetail(asset) }
                )
                .padding(.top, AppSpacing.sm)

                // Alerts & Upcoming Events Section
                AlertsEventsSection(
                    alerts: viewModel.alertEvents,
                    smartMoneyAlert: viewModel.smartMoneyAlert,
                    onAlertTapped: { alert in viewModel.viewAlertDetail(alert) },
                    onSmartMoneyTapped: { print("Smart money tapped") }
                )

                // Portfolio Insights Section
                PortfolioInsightsSection(score: viewModel.diversificationScore)

                // Bottom spacing for tab bar
                Spacer()
                    .frame(height: 100)
            }
        }
        .refreshable {
            await viewModel.refresh()
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
                        onActivityTapped: { activity in viewModel.viewTradeGroupDetail(activity) }
                    )
                }

                // 3. Whale Alert Banner
                if let alert = viewModel.whaleAlertBanner {
                    WhaleAlertBannerCard(
                        alert: alert,
                        onViewAlert: { viewModel.viewWhaleAlert() }
                    )
                    .padding(.horizontal, AppSpacing.lg)
                }

                // 4. Most Popular Whales (unchanged)
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
        .refreshable {
            await viewModel.refresh()
        }
        .navigationDestination(isPresented: $viewModel.showAllWhales) {
            AllWhalesView(viewModel: viewModel)
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
                            Circle()
                                .fill(AppColors.cardBackgroundLight)
                                .frame(width: 64, height: 64)
                                .overlay(
                                    Image(systemName: "person.fill")
                                        .font(.system(size: 28))
                                        .foregroundColor(AppColors.textMuted)
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

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            // Section Header
            Text("Recent Trades")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)
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
                // Date label
                Text(activity.formattedDate)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

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
                            .font(.system(size: 22))
                            .foregroundColor(AppColors.textMuted)
                    )

                // Info
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    HStack {
                        // Name and trade count
                        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                            Text(activity.entityName)
                                .font(AppTypography.bodyBold)
                                .foregroundColor(AppColors.textPrimary)

                            Text(activity.formattedTradeCount)
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }

                        Spacer()

                        // Amount + Action badge
                        VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                            Text(activity.formattedAmount)
                                .font(AppTypography.calloutBold)
                                .foregroundColor(activity.action.color)

                            Text(activity.action.rawValue)
                                .font(.system(size: 10, weight: .bold))
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
                    .font(.system(size: 14, weight: .medium))
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
        }
        .buttonStyle(.plain)
    }
}

// MARK: - Whale Alert Banner Card
struct WhaleAlertBannerCard: View {
    let alert: WhaleAlertBanner
    var onViewAlert: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack(spacing: AppSpacing.md) {
                // Bell icon
                Circle()
                    .fill(AppColors.alertOrange.opacity(0.2))
                    .frame(width: 44, height: 44)
                    .overlay(
                        Image(systemName: "bell.fill")
                            .font(.system(size: 20))
                            .foregroundColor(AppColors.alertOrange)
                    )

                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(alert.title)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)

                    Text(alert.description)
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.textSecondary)
                        .lineLimit(2)
                }
            }

            // View Full Alert button
            Button {
                onViewAlert?()
            } label: {
                Text(alert.actionTitle)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.alertOrange)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, AppSpacing.sm)
                    .background(
                        RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                            .stroke(AppColors.alertOrange.opacity(0.5), lineWidth: 1)
                    )
            }
            .buttonStyle(.plain)
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.large)
                        .stroke(AppColors.alertOrange.opacity(0.3), lineWidth: 1)
                )
        )
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
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Button {
                    onMoreTapped?()
                } label: {
                    Text("more")
                        .font(AppTypography.callout)
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
                            .font(AppTypography.title2)
                            .foregroundColor(AppColors.textPrimary)

                        if !whale.title.isEmpty {
                            Text(whale.title)
                                .font(AppTypography.callout)
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
                                .font(.system(size: 11))
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
                        Circle()
                            .fill(AppColors.cardBackgroundLight)
                            .frame(width: 80, height: 80)
                            .overlay(
                                Image(systemName: "person.fill")
                                    .font(.system(size: 36))
                                    .foregroundColor(AppColors.textMuted)
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
                Circle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(width: 44, height: 44)
                    .overlay(
                        Image(systemName: "person.fill")
                            .font(.system(size: 20))
                            .foregroundColor(AppColors.textMuted)
                    )

                // Info
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text(whale.name)
                        .font(AppTypography.bodyBold)
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
                        .font(AppTypography.calloutBold)
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
    var onDismiss: (() -> Void)?

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

                    // Search Results (placeholder)
                    if searchText.isEmpty {
                        VStack(spacing: AppSpacing.md) {
                            Image(systemName: "magnifyingglass")
                                .font(.system(size: 48))
                                .foregroundColor(AppColors.textMuted)

                            Text("Search for a stock to add to your watchlist")
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textSecondary)
                                .multilineTextAlignment(.center)
                        }
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                    } else {
                        // Results would go here
                        Text("Searching for \"\(searchText)\"...")
                            .foregroundColor(AppColors.textSecondary)
                            .frame(maxWidth: .infinity, maxHeight: .infinity)
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
                }
            }
            .listStyle(InsetGroupedListStyle())
            .navigationTitle("Sort By")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") {
                        onDismiss?()
                    }
                    .fontWeight(.semibold)
                }
            }
        }
        .presentationDetents([.medium])
    }
}

// MARK: - Preview
#Preview {
    TrackingContentView()
        .preferredColorScheme(.dark)
}
