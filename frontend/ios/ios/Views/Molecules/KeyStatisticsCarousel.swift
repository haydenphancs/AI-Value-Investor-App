//
//  KeyStatisticsCarousel.swift
//  ios
//
//  Molecule: the horizontally scrolling row of KeyStatisticsCards shared by every
//  asset-detail Key Statistics section.
//

import SwiftUI

/// The card row inside a "Key Statistics" section: one `KeyStatisticsCard` per group, a 1pt
/// divider between neighbours, scrolling sideways.
///
/// WHY THIS EXISTS
/// ---------------
/// `TickerDetailKeyStatsSection` (also used by Index and Commodity), `ETFDetailKeyStatsSection`
/// and `CryptoDetailKeyStatsSection` carried a byte-identical copy of this row, and all three
/// carried the same defect: two nested `HStack(spacing: 0)` with the default `.center`
/// alignment. Groups are not always the same height — the crypto supply group is four rows
/// unless "Total Supply" differs from "Circulating Supply", beside a five-row price group —
/// so the shorter card floated to the vertical MIDDLE of its neighbour and the rows stopped
/// reading as one table (TestFlight, build 1.0 (8), ETH). One row, fixed once.
///
/// Two things make the cards line up, and both are needed:
///
/// 1. `HStack(alignment: .top, …)` on BOTH stacks, so a shorter card starts at the top edge.
/// 2. `.fixedSize(horizontal: false, vertical: true)` on the outer row, which gives the row a
///    DEFINITE height (its ideal: the tallest card). A stack only re-proposes its own height to
///    its children when it has one, and that is what lets the card's `maxHeight: .infinity`
///    frame stretch a four-row card to the height of a five-row neighbour. Without it the
///    proposal is nil, every card keeps its ideal height, and the row is merely top-aligned.
///
/// Pinned by `backend/tests/test_ios_detail_layout_guards.py`.
struct KeyStatisticsCarousel: View {
    let statisticsGroups: [KeyStatisticsGroup]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: 0) {
                ForEach(Array(statisticsGroups.enumerated()), id: \.element.id) { index, group in
                    HStack(alignment: .top, spacing: 0) {
                        KeyStatisticsCard(statistics: group.statistics)

                        // Vertical divider between cards (except for last). No height of its
                        // own, so it spans whatever the row's height is.
                        if index < statisticsGroups.count - 1 {
                            Rectangle()
                                .fill(AppColors.cardBackgroundLight)
                                .frame(width: 1)
                                .padding(.vertical, AppSpacing.lg)
                        }
                    }
                }
            }
            .fixedSize(horizontal: false, vertical: true)
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text("Key Statistics")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // A five-row group beside a four-row one — the shape that exposed the defect.
            KeyStatisticsCarousel(statisticsGroups: [
                KeyStatisticsGroup(statistics: [
                    KeyStatistic(label: "Market Cap", value: "$299.19B"),
                    KeyStatistic(label: "24h Volume", value: "$15.29B"),
                    KeyStatistic(label: "Volume/Mkt Cap", value: "5.11%"),
                    KeyStatistic(label: "24h High", value: "$2,482.52"),
                    KeyStatistic(label: "24h Low", value: "$2,410.22")
                ]),
                KeyStatisticsGroup(statistics: [
                    KeyStatistic(label: "Circulating Supply", value: "122.04M ETH"),
                    KeyStatistic(label: "Max Supply", value: "No Cap"),
                    KeyStatistic(label: "Fully Diluted Val.", value: "$299.19B"),
                    KeyStatistic(label: "Avg. Volume (30D)", value: "$13.04B")
                ])
            ])
        }
        .padding(.top, AppSpacing.md)
        .padding(.bottom, AppSpacing.sm)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
    }
    .background(AppColors.background)
}
