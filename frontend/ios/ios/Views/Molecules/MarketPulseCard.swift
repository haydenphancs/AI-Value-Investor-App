//
//  MarketPulseCard.swift
//  ios
//
//  Molecule: one compact tile in the Home "Markets Open" pulse strip —
//  name, price, mini sparkline, and change %.
//

import SwiftUI

struct MarketPulseCard: View {
    let item: MarketPulseItem
    var onTap: (() -> Void)? = nil

    private var changeColor: Color {
        item.isPositive ? AppColors.bullish : AppColors.bearish
    }

    var body: some View {
        Button { onTap?() } label: {
            VStack(alignment: .leading, spacing: 3) {
                Text(item.name)
                    .font(AppTypography.captionSmall)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)

                // A `lineLimit(1)` with no `minimumScaleFactor` TRUNCATES, and the real
                // values are 9 characters ("23,840.10"). At `dataMedium` × dataCap that
                // is ~88pt of monospaced digits in a 68pt box, which is why index prices
                // clipped. 0.75 is derived, not felt: 88 × 0.75 = 66pt, just inside.
                Text(item.priceText)
                    .font(AppTypography.dataMedium)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.75)
                    .allowsTightening(true)

                // Dual-tone sparkline with a dashed previous-close reference
                // line — green above / red below — matching the Holdings cards.
                //
                // COLLAPSED when there's no series, rather than reserving 22pt of
                // empty space. Two callers rely on this: Market Pulse when the
                // intraday series is briefly unavailable, and the Your Watchlist
                // strip, which never fetches one (a per-ticker series would be one
                // API call each on the most-visited screen). Reserving the gap made
                // those tiles look like a failed render.
                if !item.spark.isEmpty {
                    SparklineView(
                        data: item.spark,
                        isPositive: item.isPositive,
                        referencePrice: item.previousClose
                    )
                    .frame(height: 22)
                }

                // Had NO line limit at all, so it wrapped instead of truncating and grew
                // the tile vertically rather than clipping — a different symptom of the
                // same 68pt squeeze.
                Text(item.changeText)
                    .font(AppTypography.dataSmall)
                    .foregroundColor(changeColor)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
            // `minWidth`, not a hard `width`: the strip is a horizontal ScrollView that
            // constrains nothing, so letting the tile grow means larger text costs a
            // little scroll rather than shrinking every glyph. The scale factors above
            // are the backstop for when it cannot grow.
            .frame(minWidth: 88, alignment: .leading)
            .padding(.horizontal, 10)
            .padding(.vertical, 9)
            .background(AppColors.cardBackground)
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            // Home built its cards before `cardSurface` existed, so it was the one
            // screen with no edge in light — a #FFFFFF card on the #F4F5F8 page is
            // 1.09:1. The clip stays (it bounds the sparkline's gradient fill), so the
            // edge goes on as an overlay. `cardEdge` means light only; dark is
            // untouched, which is the look this screen already had.
            .cardBorder(cornerRadius: 12)
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    HStack(spacing: 10) {
        MarketPulseCard(item: MockHomeRepository.pulse[0])
        MarketPulseCard(item: MockHomeRepository.pulse[3])
    }
    .padding()
    .background(AppColors.background)
}
