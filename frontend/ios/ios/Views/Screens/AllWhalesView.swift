//
//  AllWhalesView.swift
//  ios
//
//  All Whales screen — shown when user taps "more" on Most Popular Whales
//  Displays whales in categorized sections: Investors, Hedge Funds, Politicians, Crypto
//

import SwiftUI

// MARK: - AllWhalesView
struct AllWhalesView: View {
    @ObservedObject var viewModel: TrackingViewModel

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            ScrollView(showsIndicators: false) {
                LazyVStack(spacing: AppSpacing.xxl) {
                    // Investors Section
                    AllWhalesCategorySection(
                        title: WhaleCategory.investors.rawValue,
                        whales: investorWhales,
                        onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                        onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                    )

                    // Hedge Funds Section
                    AllWhalesCategorySection(
                        title: WhaleCategory.hedgeFunds.rawValue,
                        whales: hedgeFundWhales,
                        onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                        onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                    )

                    // Politicians Section
                    AllWhalesCategorySection(
                        title: WhaleCategory.politicians.rawValue,
                        whales: politicianWhales,
                        onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                        onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                    )

                    // Crypto Section
                    AllWhalesCategorySection(
                        title: WhaleCategory.cryptoWhales.rawValue,
                        whales: cryptoWhales,
                        onFollowToggle: { whale in viewModel.toggleFollowWhale(whale) },
                        onWhaleTapped: { whale in viewModel.viewWhaleProfile(whale) }
                    )

                    // Bottom spacing
                    Spacer()
                        .frame(height: 100)
                }
                .padding(.top, AppSpacing.md)
            }
        }
        .navigationTitle("Popular Whales")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: - Filtered by Category

    private var investorWhales: [TrendingWhale] {
        syncFollowState(viewModel.allPopularWhales.filter { $0.category == .investors })
    }

    private var hedgeFundWhales: [TrendingWhale] {
        syncFollowState(viewModel.allPopularWhales.filter { $0.category == .hedgeFunds })
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
