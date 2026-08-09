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
    /// The heading, SERVER-supplied: the name of the user's active group, so renaming
    /// "Holdings" → "Tech" on the Tracking tab retitles this section too. It was a
    /// hardcoded literal here, which is precisely how Home could say "Your Watchlist"
    /// over a set of tickers the Tracking tab called something else.
    ///
    /// Defaulted so previews and any caller that has not been updated still compile to
    /// the exact string this section always showed.
    var title: String = "Your Watchlist"
    let items: [MarketPulseItem]
    var onTap: ((MarketPulseItem) -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 7) {
                Image(systemName: "star.fill")
                    .font(AppTypography.iconXS)
                    .foregroundColor(AppColors.primaryBlue)
                Text(title)
                    .font(AppTypography.labelEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    // A user-named group can be up to 60 characters; without this a long
                    // name wraps the header and pushes the card strip down the screen.
                    .lineLimit(1)
                    .truncationMode(.tail)
                Spacer(minLength: AppSpacing.sm)
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

#Preview("Default title") {
    YourWatchlistSection(items: MockHomeRepository.pulse)
        .background(AppColors.background)
}

#Preview("Named group") {
    YourWatchlistSection(title: "Tech", items: MockHomeRepository.pulse)
        .background(AppColors.background)
}

#Preview("Long group name") {
    YourWatchlistSection(
        title: "Dividend Aristocrats I Am Watching Very Closely",
        items: MockHomeRepository.pulse
    )
    .background(AppColors.background)
}
