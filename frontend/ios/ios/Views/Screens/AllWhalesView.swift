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

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Category filter chips (horizontal scroll)
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

                // Sort control — only visible when "All" is selected
                if selectedFilter == .all {
                    HStack(spacing: AppSpacing.xs) {
                        Text("Sort by")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textMuted)

                        ForEach(WhaleSortOption.allCases, id: \.self) { option in
                            Button {
                                withAnimation(.easeInOut(duration: 0.2)) {
                                    sortOption = option
                                }
                            } label: {
                                HStack(spacing: 4) {
                                    Image(systemName: option.icon)
                                        .font(.system(size: 10))
                                    Text(option.rawValue)
                                        .font(AppTypography.caption)
                                }
                                .foregroundColor(
                                    sortOption == option
                                        ? AppColors.textPrimary
                                        : AppColors.textMuted
                                )
                                .padding(.horizontal, AppSpacing.md)
                                .padding(.vertical, AppSpacing.xs)
                                .background(
                                    sortOption == option
                                        ? AppColors.cardBackgroundLight
                                        : Color.clear
                                )
                                .cornerRadius(AppCornerRadius.pill)
                            }
                            .buttonStyle(.plain)
                        }

                        Spacer()
                    }
                    .padding(.horizontal, AppSpacing.lg)
                    .padding(.bottom, AppSpacing.sm)
                }

                // Whale sections
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
                        if selectedFilter == .all {
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
            HStack {
                Text(title)
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                Text("\(whales.count)")
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textMuted)
            }
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
