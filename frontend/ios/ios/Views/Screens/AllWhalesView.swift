//
//  AllWhalesView.swift
//  ios
//
//  All Whales screen — shown when user taps "more" on Most Popular Whales
//  Contains category filter chips + scrollable whale list
//

import SwiftUI

// MARK: - AllWhalesView
struct AllWhalesView: View {
    @ObservedObject var viewModel: TrackingViewModel
    @State private var selectedCategory: WhaleCategory = .following

    var body: some View {
        ZStack {
            AppColors.background
                .ignoresSafeArea()

            VStack(spacing: 0) {
                // Category filter chips
                WhaleCategoryFilter(
                    categories: WhaleCategory.allCases,
                    selectedCategory: $selectedCategory
                )
                .padding(.top, AppSpacing.md)
                .padding(.bottom, AppSpacing.lg)

                // Whale list
                ScrollView(showsIndicators: false) {
                    LazyVStack(spacing: AppSpacing.md) {
                        ForEach(filteredWhales) { whale in
                            WhaleCard(
                                whale: whale,
                                onFollowToggle: { viewModel.toggleFollowWhale(whale) },
                                onTap: { viewModel.viewWhaleProfile(whale) }
                            )
                        }

                        if filteredWhales.isEmpty {
                            VStack(spacing: AppSpacing.md) {
                                Image(systemName: "person.3.fill")
                                    .font(.system(size: 40))
                                    .foregroundColor(AppColors.textMuted)

                                Text("No whales found")
                                    .font(AppTypography.body)
                                    .foregroundColor(AppColors.textSecondary)

                                Text("Try selecting a different category")
                                    .font(AppTypography.caption)
                                    .foregroundColor(AppColors.textMuted)
                            }
                            .frame(maxWidth: .infinity)
                            .padding(.top, AppSpacing.xxxl)
                        }

                        // Bottom spacing
                        Spacer()
                            .frame(height: 100)
                    }
                    .padding(.horizontal, AppSpacing.lg)
                }
            }
        }
        .navigationTitle("Popular Whales")
        .navigationBarTitleDisplayMode(.inline)
    }

    // MARK: - Filtered Whales
    private var filteredWhales: [TrendingWhale] {
        let allWhales = viewModel.popularWhales + viewModel.trackedWhales + viewModel.heroWhales
        // Remove duplicates by name
        var seen = Set<String>()
        let unique = allWhales.filter { whale in
            guard !seen.contains(whale.name) else { return false }
            seen.insert(whale.name)
            return true
        }

        if selectedCategory == .following {
            return unique.filter { $0.isFollowing }
        }
        return unique.filter { $0.category == selectedCategory }
    }
}

// MARK: - Preview
#Preview {
    NavigationStack {
        AllWhalesView(viewModel: TrackingViewModel())
    }
    .preferredColorScheme(.dark)
}
