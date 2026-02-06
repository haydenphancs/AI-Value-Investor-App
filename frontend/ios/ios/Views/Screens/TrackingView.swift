//
//  TrackingView.swift
//  ios
//
//  Main Tracking screen with Assets and Whales tabs
//

import SwiftUI

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
                // Activity Feed
                WhaleActivitySection(
                    activities: viewModel.whaleActivities,
                    onActivityTapped: { activity in viewModel.viewWhaleDetail(activity) }
                )
                .padding(.top, AppSpacing.sm)

                // Tracked Whales (whales the user is following)
                if !viewModel.trackedWhales.isEmpty {
                    TrackedWhalesSection(
                        whales: viewModel.trackedWhales,
                        onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                        onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                    )
                }

                // Most Popular Whales (hero carousel + list)
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

// MARK: - Whale Category Filter
struct WhaleCategoryFilter: View {
    let categories: [WhaleCategory]
    @Binding var selectedCategory: WhaleCategory

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: AppSpacing.sm) {
                ForEach(categories, id: \.self) { category in
                    Button {
                        withAnimation(.easeInOut(duration: 0.2)) {
                            selectedCategory = category
                        }
                    } label: {
                        Text(category.rawValue)
                            .font(AppTypography.calloutBold)
                            .foregroundColor(
                                selectedCategory == category
                                    ? AppColors.textPrimary
                                    : AppColors.textSecondary
                            )
                            .padding(.horizontal, AppSpacing.lg)
                            .padding(.vertical, AppSpacing.sm)
                            .background(
                                selectedCategory == category
                                    ? AppColors.cardBackgroundLight
                                    : AppColors.cardBackground
                            )
                            .cornerRadius(AppCornerRadius.pill)
                    }
                    .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

// MARK: - Whale Activity Section
struct WhaleActivitySection: View {
    let activities: [WhaleActivity]
    var onActivityTapped: ((WhaleActivity) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Activity Feed")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            VStack(spacing: AppSpacing.md) {
                ForEach(activities) { activity in
                    WhaleActivityCard(activity: activity) {
                        onActivityTapped?(activity)
                    }
                }
            }
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

// MARK: - Whale Activity Card
struct WhaleActivityCard: View {
    let activity: WhaleActivity
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

                // Content
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    HStack(spacing: AppSpacing.sm) {
                        Text(activity.entityName)
                            .font(AppTypography.bodyBold)
                            .foregroundColor(AppColors.textPrimary)

                        // Action Badge
                        Text(activity.action.rawValue)
                            .font(.system(size: 10, weight: .bold))
                            .foregroundColor(.white)
                            .padding(.horizontal, AppSpacing.sm)
                            .padding(.vertical, 2)
                            .background(activity.action.color)
                            .cornerRadius(AppCornerRadius.small)
                    }

                    Text("\(activity.ticker) • \(activity.amount)")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.textSecondary)

                    Text("\(activity.source) • \(activity.timeAgo)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                Spacer()

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

// MARK: - Tracked Whales Section
struct TrackedWhalesSection: View {
    let whales: [TrendingWhale]
    var onFollowToggle: ((TrendingWhale) -> Void)?
    var onWhaleTapped: ((TrendingWhale) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Tracked Whales")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

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
