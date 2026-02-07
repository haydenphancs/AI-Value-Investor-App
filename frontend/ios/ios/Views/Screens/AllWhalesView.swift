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
    case crypto = "Crypto"

    var matchedCategory: WhaleCategory? {
        switch self {
        case .all: return nil
        case .investors: return .investors
        case .institutions: return .institutions
        case .politicians: return .politicians
        case .crypto: return .cryptoWhales
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
                                .font(.system(size: 14))
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
                                        .font(.system(size: 14))
                                        .foregroundColor(AppColors.textMuted)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding(.horizontal, AppSpacing.md)
                        .padding(.vertical, AppSpacing.sm)
                        .background(AppColors.cardBackground)
                        .cornerRadius(AppCornerRadius.pill)

                        Button {
                            withAnimation(.easeInOut(duration: 0.25)) {
                                isSearching = false
                                searchText = ""
                                isSearchFocused = false
                            }
                        } label: {
                            Text("Cancel")
                                .font(AppTypography.callout)
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
                                        .font(AppTypography.calloutBold)
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
                            .background(AppColors.cardBackground)
                            .cornerRadius(AppCornerRadius.pill)
                        }
                        .buttonStyle(.plain)

                        Spacer()
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.sm)
                }

                // Whale sections
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
                        if isSearching {
                            // Search results — flat list
                            if searchResults.isEmpty && !searchText.isEmpty {
                                VStack(spacing: AppSpacing.md) {
                                    Image(systemName: "magnifyingglass")
                                        .font(.system(size: 40))
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
                            .font(.system(size: 16, weight: .medium))
                            .foregroundColor(AppColors.textPrimary)
                    }
                }
            }
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
                return TrendingWhale(
                    name: whale.name,
                    category: whale.category,
                    avatarName: whale.avatarName,
                    followersCount: whale.followersCount,
                    isFollowing: true,
                    title: whale.title,
                    description: whale.description,
                    recentTradeCount: whale.recentTradeCount
                )
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
                .font(AppTypography.title3)
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
    .preferredColorScheme(.dark)
}
