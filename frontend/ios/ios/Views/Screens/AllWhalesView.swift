//
//  AllWhalesView.swift
//  ios
//
//  All Whales screen — shown when user taps "more" on Most Popular Whales
//  Horizontal category filter + categorized whale sections
//

import SwiftUI

// MARK: - Filter Option
private enum WhaleCategoryFilter: String, CaseIterable {
    case all = "All"
    case investors = "Investors"
    case institutions = "Institutions"
    case politicians = "Politicians"

    var matchedCategory: WhaleCategory? {
        switch self {
        case .all: return nil
        case .investors: return .investors
        case .institutions: return .institutions
        case .politicians: return .politicians
        }
    }
}

// MARK: - Sort Option (only for "All" filter)
private enum WhaleSortOption: String, CaseIterable {
    case alphabetical = "A–Z"
    case followers = "Followers"

    var icon: String {
        switch self {
        case .alphabetical: return "textformat.abc"
        case .followers: return "person.2.fill"
        }
    }
}

// MARK: - AllWhalesView
struct AllWhalesView: View {
    @ObservedObject var viewModel: TrackingViewModel
    @Environment(\.appState) private var appState
    @State private var selectedFilter: WhaleCategoryFilter = .all
    @State private var sortOption: WhaleSortOption = .followers
    @State private var isSearching: Bool = false
    @State private var searchText: String = ""
    @FocusState private var isSearchFocused: Bool

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Expandable search bar
                if isSearching {
                    HStack(spacing: AppSpacing.sm) {
                        HStack(spacing: AppSpacing.sm) {
                            Image(systemName: "magnifyingglass")
                                .font(AppTypography.iconSmall)
                                .foregroundColor(AppColors.textMuted)

                            TextField("Search whales...", text: $searchText)
                                .font(AppTypography.body)
                                .foregroundColor(AppColors.textPrimary)
                                .focused($isSearchFocused)

                            if !searchText.isEmpty {
                                Button {
                                    searchText = ""
                                } label: {
                                    Image(systemName: "xmark.circle.fill")
                                        .font(AppTypography.iconSmall)
                                        .foregroundColor(AppColors.textMuted)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal, AppSpacing.md)
                        .padding(.vertical, AppSpacing.sm)
                        .cardSurface(cornerRadius: AppCornerRadius.pill)

                        Button {
                            withAnimation(.easeInOut(duration: 0.25)) {
                                isSearching = false
                                searchText = ""
                                isSearchFocused = false
                            }
                        } label: {
                            Text("Cancel")
                                .font(AppTypography.bodySmall)
                                .foregroundColor(AppColors.primaryBlue)
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.vertical, AppSpacing.sm)
                    .transition(.opacity.combined(with: .move(edge: .top)))
                }

                // Category filter chips (horizontal scroll) — hidden while searching
                if !isSearching {
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: AppSpacing.sm) {
                            ForEach(WhaleCategoryFilter.allCases, id: \.self) { filter in
                                Button {
                                    withAnimation(.easeInOut(duration: 0.2)) {
                                        selectedFilter = filter
                                    }
                                } label: {
                                    Text(filter.rawValue)
                                        .font(AppTypography.bodySmallEmphasis)
                                        .foregroundColor(
                                            selectedFilter == filter
                                                ? AppColors.textPrimary
                                                : AppColors.textSecondary
                                        )
                                        .padding(.horizontal, AppSpacing.lg)
                                        .padding(.vertical, AppSpacing.sm)
                                        .background(
                                            selectedFilter == filter
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
                    .padding(.top, AppSpacing.md)
                    .padding(.bottom, AppSpacing.sm)
                }

                // Sort control — only visible when "All" is selected and not searching
                if selectedFilter == .all && !isSearching {
                    HStack {
                        Button {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                sortOption = sortOption == .alphabetical ? .followers : .alphabetical
                            }
                        } label: {
                            HStack(spacing: 4) {
                                Text("Sort by:")
                                    .font(AppTypography.caption)
                                
                                Text(sortOption == .alphabetical ? "A-Z" : "Followed")
                                    .font(AppTypography.caption)
                            }
                            .foregroundColor(AppColors.textMuted)
                            .padding(.horizontal, AppSpacing.md)
                            .padding(.vertical, AppSpacing.xs)
                            .cardSurface(cornerRadius: AppCornerRadius.pill)
                        }
                        .buttonStyle(.plain)

                        Spacer()
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.sm)
                }

                // Whale sections
                ScrollView(showsIndicators: false) {
                    // A plain VStack, NOT LazyVStack - see HomeDashboardView.content for the full write-up.
                    // The direct children here are a fixed, hand-written list, so laziness bought nothing,
                    // while a lazy stack whose child RESIZES IN PLACE re-walks its predecessor chain and can
                    // wedge the main thread at 100% inside LazySubviewPlacements -> _ViewList_Node.applyNodes.
                    //
                    // A 3-way swap between a full list and a short empty state as the user types.
                    VStack(spacing: AppSpacing.xxl) {
                        if isSearching {
                            // Search results — flat list
                            if searchResults.isEmpty && !searchText.isEmpty {
                                VStack(spacing: AppSpacing.md) {
                                    Image(systemName: "magnifyingglass")
                                        .font(AppTypography.iconXXL)
                                        .foregroundColor(AppColors.textMuted)

                                    Text("No results for \"\(searchText)\"")
                                        .font(AppTypography.body)
                                        .foregroundColor(AppColors.textSecondary)

                                    Text("Try a different name or institution")
                                        .font(AppTypography.caption)
                                        .foregroundColor(AppColors.textMuted)
                                }
                                .frame(maxWidth: .infinity)
                                .padding(.top, AppSpacing.xxxl)
                            } else {
                                AllWhalesFlatList(
                                    whales: searchResults,
                                    onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                                    onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                                )
                            }
                        } else if selectedFilter == .all {
                            // Flat sorted list
                            AllWhalesFlatList(
                                whales: allWhalesSorted,
                                onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                                onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                            )
                        } else {
                            // Category-specific section
                            if let category = selectedFilter.matchedCategory {
                                AllWhalesCategorySection(
                                    title: category.rawValue,
                                    whales: whalesForCategory(category),
                                    onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                                    onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                                )
                            }
                        }

                        // Bottom spacing
                        Spacer()
                            .frame(height: 100)
                    }
                    .padding(.top, AppSpacing.sm)
                }
            }
        }
        .navigationTitle("Popular Whales")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .navigationBarTrailing) {
                if !isSearching {
                    Button {
                        withAnimation(.easeInOut(duration: 0.25)) {
                            isSearching = true
                        }
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                            isSearchFocused = true
                        }
                    } label: {
                        Image(systemName: "magnifyingglass")
                            .font(AppTypography.iconDefault).fontWeight(.medium)
                            .foregroundColor(AppColors.textPrimary)
                    }
                }
            }
        }
        // Same VM as the Tracking tab, so the same flag drives the sheet here — the whole
        // reason the locked-tap decision lives on the ViewModel rather than in a closure.
        .sheet(isPresented: $viewModel.showWhalePaywall) {
            PaywallView(context: .whaleFollowLimit)
                .environment(\.appState, appState)
        }
    }

    // MARK: - Search

    private var searchResults: [TrendingWhale] {
        guard !searchText.isEmpty else { return syncFollowState(viewModel.allPopularWhales) }
        let query = searchText.lowercased()
        return syncFollowState(
            viewModel.allPopularWhales.filter { whale in
                whale.name.lowercased().contains(query) ||
                whale.title.lowercased().contains(query) ||
                // Searching a firm ("Bridgewater") finds its person-fronted
                // whale (Ray Dalio) — one merged profile per 13F filer.
                (whale.firmName?.lowercased().contains(query) ?? false) ||
                whale.category.rawValue.lowercased().contains(query)
            }
        )
    }

    // MARK: - Data

    private var allWhalesSorted: [TrendingWhale] {
        let whales = syncFollowState(viewModel.allPopularWhales)
        switch sortOption {
        case .alphabetical:
            return whales.sorted { $0.name < $1.name }
        case .followers:
            return whales.sorted { $0.followersCount > $1.followersCount }
        }
    }

    private func whalesForCategory(_ category: WhaleCategory) -> [TrendingWhale] {
        syncFollowState(viewModel.allPopularWhales.filter { $0.category == category })
    }

    private func syncFollowState(_ whales: [TrendingWhale]) -> [TrendingWhale] {
        let trackedNames = Set(viewModel.trackedWhales.map(\.name))
        return whales.map { whale in
            if trackedNames.contains(whale.name) && !whale.isFollowing {
                // `withFollowing` rather than a hand-written rebuild: this site has already
                // dropped `id` once (breaking profile navigation for followed rows) and
                // would have dropped `firmName` the same way. The helper exists so a field
                // added to TrendingWhale — `isLocked` being the latest — cannot go missing
                // here silently. It also clears the lock, which is correct: a whale you
                // follow is never locked, or you could not unfollow it.
                return whale.withFollowing(true)
            }
            return whale
        }
    }
}

// MARK: - Flat List (for "All" with sorting)
private struct AllWhalesFlatList: View {
    let whales: [TrendingWhale]
    var onFollowToggle: ((TrendingWhale) -> Void)?
    var onWhaleTapped: ((TrendingWhale) -> Void)?

    var body: some View {
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

// MARK: - Category Section
struct AllWhalesCategorySection: View {
    let title: String
    let whales: [TrendingWhale]
    var onFollowToggle: ((TrendingWhale) -> Void)?
    var onWhaleTapped: ((TrendingWhale) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section Header
            Text(title)
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Whale Cards
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

// MARK: - Preview
#Preview {
    NavigationStack {
        AllWhalesView(viewModel: TrackingViewModel())
    }
}
