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

// MARK: - AllWhalesView
struct AllWhalesView: View {
    @ObservedObject var viewModel: TrackingViewModel
    @State private var selectedFilter: WhaleCategoryFilter = .all

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
                .padding(.bottom, AppSpacing.lg)

                // Whale sections
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.xxl) {
                        if shouldShowCategory(.investors) {
                            AllWhalesCategorySection(
                                title: WhaleCategory.investors.rawValue,
                                whales: investorWhales,
                                onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                                onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                            )
                        }

                        if shouldShowCategory(.institutions) {
                            AllWhalesCategorySection(
                                title: WhaleCategory.institutions.rawValue,
                                whales: institutionWhales,
                                onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                                onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                            )
                        }

                        if shouldShowCategory(.politicians) {
                            AllWhalesCategorySection(
                                title: WhaleCategory.politicians.rawValue,
                                whales: politicianWhales,
                                onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                                onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                            )
                        }

                        if shouldShowCategory(.cryptoWhales) {
                            AllWhalesCategorySection(
                                title: WhaleCategory.cryptoWhales.rawValue,
                                whales: cryptoWhales,
                                onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                                onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                            )
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

    // MARK: - Helpers

    private func shouldShowCategory(_ category: WhaleCategory) -> Bool {
        selectedFilter == .all || selectedFilter.matchedCategory == category
    }

    // MARK: - Filtered by Category

    private var investorWhales: [TrendingWhale] {
        syncFollowState(viewModel.allPopularWhales.filter { $0.category == .investors })
    }

    private var institutionWhales: [TrendingWhale] {
        syncFollowState(viewModel.allPopularWhales.filter { $0.category == .institutions })
    }

    private var politicianWhales: [TrendingWhale] {
        syncFollowState(viewModel.allPopularWhales.filter { $0.category == .politicians })
    }

    private var cryptoWhales: [TrendingWhale] {
        syncFollowState(viewModel.allPopularWhales.filter { $0.category == .cryptoWhales })
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
