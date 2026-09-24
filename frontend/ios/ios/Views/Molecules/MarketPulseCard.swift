//
//  MarketPulseCard.swift
//  ios
//
//  Molecule: one compact tile in the Home "Markets Open" pulse strip —
//  name, price, mini sparkline, and change %. Both strips that use it (Market Pulse,
//  Holdings) lay tiles out with `EqualWidthHStack`, so a tile FILLS the cell it is given.
//

import SwiftUI

struct MarketPulseCard: View {
    let item: MarketPulseItem
    var onTap: (() -> Void)? = nil

    private var changeColor: Color {
        // NEUTRAL when the move was never measured. `isPositive` is false for an unknown
        // change, and without this branch the tile would paint a RED "—" — a fabricated
        // decline, which is the same trap a `*_known` flag introduced on the index header
        // in the 2026-09-11 pass: the flag needs a neutral state in EVERY reader, not just
        // the one that formats the text.
        guard item.changeKnown else { return AppColors.textSecondary }
        return item.isPositive ? AppColors.bullish : AppColors.bearish
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
                        // Direction of the SERIES when today's change is unknown, so the
                        // line is not painted red under a dash. Same rule as
                        // `IndexHeaderRenderable.chartIsPositive`.
                        isPositive: item.changeKnown
                            ? item.isPositive
                            : ((item.spark.last ?? 0) >= (item.spark.first ?? 0)),
                        // No dashed reference either: it is the line the colour is judged
                        // against, and an unknown change means we cannot say which side of
                        // it today sits on. `showReference: false` is what removes it —
                        // `referencePrice: nil` alone means "anchor to the first point".
                        referencePrice: item.changeKnown ? item.previousClose : nil,
                        showReference: item.changeKnown,
                        // Bitcoin and the S&P fill different fractions at the same
                        // instant — their sessions are 00:00-24:00 and 09:30-16:00.
                        spanFrom: item.sparkFrom,
                        spanTo: item.sparkTo
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
            // `minWidth: 88` is a FLOOR, never a hard width: the strip is a horizontal
            // ScrollView that constrains nothing, so larger text costs a little scroll
            // rather than shrinking every glyph (the scale factors above are the backstop).
            //
            // `maxWidth`/`maxHeight: .infinity` make the tile take the WHOLE cell
            // `EqualWidthHStack` gives it — the widest/tallest tile in the row — which is
            // what makes every tile the same size (TestFlight 2026-09-23). A flexible frame
            // reports clamp(proposed, min ?? child, max ?? child): with `minWidth` alone a
            // tile with narrower content stays narrower than the cell. Tiles WITH a
            // sparkline looked fine without it only because `SparklineView` is a
            // GeometryReader that takes any width offered; Holdings tiles have no series.
            // `.topLeading` keeps a shorter tile's text on the same lines as its
            // neighbours. With no proposal (how the layout measures) both maxima are inert.
            .frame(minWidth: 88, maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
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
            // A Button hit-tests what its label DRAWS; declare the shape so the whole
            // tile stays tappable whatever the background becomes.
            .contentShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
        .buttonStyle(.plain)
    }
}

#Preview {
    // The tile fills whatever it is offered, so preview it in the layout that ships.
    // The third tile has no sparkline: it must still match the others' height.
    EqualWidthHStack(spacing: 10) {
        MarketPulseCard(item: MockHomeRepository.pulse[0])
        MarketPulseCard(item: MockHomeRepository.pulse[3])
        MarketPulseCard(item: MarketPulseItem(
            name: "ORCL", symbol: "ORCL", type: .stock,
            priceText: "229.87", changeText: "+0.41%", isPositive: true, spark: []
        ))
    }
    .padding()
    .background(AppColors.background)
}
