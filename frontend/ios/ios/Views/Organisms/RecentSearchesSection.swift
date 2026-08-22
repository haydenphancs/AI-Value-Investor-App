//
//  RecentSearchesSection.swift
//  ios
//
//  Organism: the user's own search history — tickers they opened and questions they asked
//  Cay AI — shown when the search field is empty.
//
//  ⚠️ This used to be handed `SearchViewModel.recentSearches`, which was the LIVE results array.
//  It was reassigned on every keystroke and emptied the moment the field cleared, so at rest
//  this section could only ever render its own empty state. It now takes real
//  `SearchHistoryEntry` values from `SearchHistoryStore`; live results have their own section.
//

import SwiftUI

struct RecentSearchesSection: View {
    let entries: [SearchHistoryEntry]
    var onClearAll: (() -> Void)?
    var onEntryTapped: ((SearchHistoryEntry) -> Void)?
    var onEntryRemoved: ((SearchHistoryEntry) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            HStack {
                Text("Recent Searches")
                    .font(AppTypography.heading)
                    .foregroundColor(AppColors.textPrimary)

                Spacer()

                if !entries.isEmpty {
                    ClearAllButton { onClearAll?() }
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            if entries.isEmpty {
                emptyStateView
            } else {
                VStack(spacing: 0) {
                    ForEach(entries) { entry in
                        SearchHistoryRow(
                            entry: entry,
                            onTap: { onEntryTapped?(entry) },
                            onRemove: { onEntryRemoved?(entry) }
                        )

                        if entry.id != entries.last?.id {
                            Divider()
                                .overlay(AppColors.cardBackgroundLight.opacity(0.5))
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
                .font(AppTypography.iconDisplay)
                .foregroundColor(AppColors.textMuted)

            // Says what will fill it. The old copy was a bare "No recent searches", which was
            // permanently true and told the reader nothing about how to change that.
            Text("Tickers you open and questions you ask Cay AI show up here")
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .multilineTextAlignment(.center)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.horizontal, AppSpacing.xl)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, AppSpacing.xxl)
    }
}

#Preview {
    ScrollView {
        VStack(spacing: AppSpacing.xl) {
            RecentSearchesSection(entries: [
                SearchHistoryEntry(kind: .ticker, text: "AAPL", subtitle: "Apple Inc.", rawType: "stock"),
                SearchHistoryEntry(kind: .question, text: "Why did the S&P fall this week?"),
                SearchHistoryEntry(kind: .ticker, text: "BTC", subtitle: "Crypto", rawType: "crypto"),
            ])

            RecentSearchesSection(entries: [])
        }
    }
    .background(AppColors.background)
}
