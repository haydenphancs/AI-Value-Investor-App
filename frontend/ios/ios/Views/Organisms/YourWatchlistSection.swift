//
//  YourWatchlistSection.swift
//  ios
//
//  Organism: "Your Watchlist" — the only USER-SCOPED section on Home.
//
//  Every other section of this screen is byte-identical for every caller, which is
//  why a day-1 user and someone with 40 tracked tickers saw exactly the same Home.
//  This strip is what makes the screen theirs.
//
//  Reuses `MarketPulseCard` deliberately: the backend serves these tiles as the same
//  `MarketPulseItemResponse` shape, so a second near-identical card would be pure
//  duplication (see the atom/molecule reuse rule in .claude/rules/ios-swiftui.md).
//  The one difference is `spark`, which arrives empty here — a per-ticker intraday
//  series would cost one API call each on the most-visited screen — and the card
//  already renders without it.
//
//  Hidden entirely when empty, unlike Market Pulse: that section keeps its header
//  because "Markets Open/Closed" is still true when quotes fail, whereas an empty
//  watchlist has nothing true left to say.
//

import SwiftUI

struct YourWatchlistSection: View {
    let items: [MarketPulseItem]
    var onTap: ((MarketPulseItem) -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 7) {
                Image(systemName: "star.fill")
                    .font(AppTypography.iconXS)
                    .foregroundColor(AppColors.primaryBlue)
                Text("Your Watchlist")
                    .font(AppTypography.labelEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                Spacer()
            }
            .padding(.horizontal, AppSpacing.lg)
            .padding(.bottom, 10)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 10) {
                    ForEach(items) { item in
                        MarketPulseCard(item: item) { onTap?(item) }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

#Preview {
    YourWatchlistSection(items: MockHomeRepository.pulse)
        .background(AppColors.background)
}
