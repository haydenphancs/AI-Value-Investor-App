//
//  RecentSearchesSection.swift
//  ios
//
//  Organism: Section displaying recent search items
//

import SwiftUI

struct RecentSearchesSection: View {
    let items: [SearchResultItem]
    var onClearAll: (() -> Void)?
    var onItemTapped: ((SearchResultItem) -> Void)?
    var onFollowTapped: ((SearchResultItem) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            HStack {
                Text("Recent Searches")
                    .font(AppTypography.title3)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                if !items.isEmpty {
                    ClearAllButton {
                        onClearAll?()
                    }
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            // Search results list
            if items.isEmpty {
                emptyStateView
            } else {
                VStack(spacing: 0) {
                    ForEach(items) { item in
                        SearchResultRow(
                            item: item,
                            onTap: { onItemTapped?(item) },
                            onFollowTap: { onFollowTapped?(item) }
                        )

                        if item.id != items.last?.id {
                            Divider()
                                .background(AppColors.cardBackgroundLight.opacity(0.5))
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }

    private var emptyStateView: some View {
        VStack(spacing: AppSpacing.md) {
            Image(systemName: "clock.arrow.circlepath")
                .font(.system(size: 32))
                .foregroundColor(AppColors.textMuted)

            Text("No recent searches")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xxl)
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.xl) {
            RecentSearchesSection(items: SearchResultItem.sampleData)

            RecentSearchesSection(items: [])
        }
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
