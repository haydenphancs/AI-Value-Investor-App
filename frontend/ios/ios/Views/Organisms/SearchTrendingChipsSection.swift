//
//  SearchTrendingChipsSection.swift
//  ios
//
//  Organism: the chips every search screen shows before the user types —
//  "Trending searches · 7 days", "Most added · 7 days", or the curated "Popular" list.
//
//  Placed at the TOP, directly under the search field: two of the five surfaces raise the
//  keyboard the moment they open, and it covers the bottom half of the screen. Symbol-only
//  chips in a wrapping row keep two sections to ~240 pt, which fits above it.
//
//  Data in, taps out — no store, no network. Each surface decides what a tap does (open the
//  detail, add to a watchlist, pick a research target), and a chip tap NEVER records a
//  search pick: the list would feed itself.
//
//  Neutral on purpose: no counts, no rank numbers, no "hot"/"top" wording, and a line
//  saying it is not a recommendation. The lists are general and impersonal (Terms §2), and
//  "most popular" lists draw gamification criticism when they read as a nudge.
//

import SwiftUI

struct SearchTrendingChipsSection: View {
    let sections: [SearchTrendingSection]
    let onItemTapped: (SearchTrendingItem) -> Void

    /// Symbols that appear under more than one type across the sections shown — BTC the
    /// coin and BTC the ETF. Their chips say which, or two identical "BTC" chips would open
    /// two different assets (the wrong-asset tap this app has shipped before).
    private var ambiguousSymbols: Set<String> {
        var typesBySymbol: [String: Set<String>] = [:]
        for item in sections.flatMap(\.items) {
            typesBySymbol[item.symbol, default: []].insert(item.type)
        }
        return Set(typesBySymbol.filter { $0.value.count > 1 }.keys)
    }

    private func chipTitle(_ item: SearchTrendingItem, ambiguous: Set<String>) -> String {
        guard ambiguous.contains(item.symbol) else { return item.symbol }
        let label: String
        switch item.type {
        case "crypto": label = "Crypto"
        case "etf": label = "ETF"
        case "fund": label = "Fund"
        default: label = "Stock"
        }
        return "\(item.symbol) · \(label)"
    }

    /// Honest about the source: "based on activity" only when a list is actually built
    /// from activity — the curated fallback is not.
    private var caption: String {
        sections.contains(where: \.isLive)
            ? "Based on activity in Caydex. Not a recommendation."
            : "Not a recommendation."
    }

    var body: some View {
        // A plain VStack, never Lazy*: the sections swap from the bundled list to the
        // fetched one in place, and a lazy stack whose child resizes in place can wedge the
        // main thread (see HomeDashboardView.content).
        let ambiguous = ambiguousSymbols
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            ForEach(sections) { section in
                VStack(alignment: .leading, spacing: AppSpacing.sm) {
                    Text(section.title)
                        .font(AppTypography.captionEmphasis)
                        .foregroundColor(AppColors.textSecondary)
                        .accessibilityAddTraits(.isHeader)

                    FlowOptionChips(
                        options: section.items,
                        title: { chipTitle($0, ambiguous: ambiguous) },
                        isSelected: { _ in false },
                        onTap: onItemTapped,
                        accessibilityLabel: { $0.accessibilityText }
                    )
                }
            }

            if !sections.isEmpty {
                Text(caption)
                    .font(AppTypography.captionSmall)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview("Live") {
    ScrollView {
        SearchTrendingChipsSection(sections: SearchTrendingSection.previewLive, onItemTapped: { _ in })
    }
    .background(AppColors.background)
}

#Preview("Popular fallback") {
    SearchTrendingChipsSection(sections: SearchTrendingSection.previewPopular, onItemTapped: { _ in })
        .background(AppColors.background)
}

#Preview("Narrow, large text") {
    SearchTrendingChipsSection(sections: SearchTrendingSection.previewLive, onItemTapped: { _ in })
        .frame(width: 220)
        .dynamicTypeSize(.accessibility2)
        .background(AppColors.background)
}

#Preview("Light") {
    SearchTrendingChipsSection(sections: SearchTrendingSection.previewLive, onItemTapped: { _ in })
        .background(AppColors.background)
        .environment(\.colorScheme, .light)
}
