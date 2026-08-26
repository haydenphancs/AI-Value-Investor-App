//
//  RelatedTickerCard.swift
//  ios
//
//  Molecule: Card for related/similar tickers in horizontal scroll ("People Also Check").
//
//  ⚠️ HEIGHT IS A MINIMUM, NOT A FIXED SIZE. Do not put `height:` back.
//
//  This card carried `.frame(width: 100, height: 120)`, which left a 96pt content box for
//  four lines of text plus 32pt of unscaled `AppSpacing` gaps — 64pt of room for text that
//  measures ~62pt at the default content size. It fit by 2pt, and overflowed at every step
//  above default. A `.frame(height:)` CENTRES an oversized child, so the overflow split
//  evenly and bled off the top AND bottom edges; `.cardSurface()` is a background, not a
//  clip, so the text visibly overran the card rather than being masked. A TestFlight tester
//  on an ordinary (non-accessibility) larger Text Size reported exactly that symptom.
//
//  `.dynamicTypeSize(...)` is NOT the fix and would be inert: `AppTypography` resolves
//  through `UIFontMetrics`, which reads a PROCESS-level UIKit trait and cannot see a SwiftUI
//  per-view environment override. The caps in `AppTheme.swift` are this app's clamp, and
//  `readingCap` 1.4x alone is enough to overflow a 120pt box.
//
//  Shared by the Ticker, ETF, Crypto and Commodity detail screens.
//

import SwiftUI

struct RelatedTickerCard: View {
    let ticker: RelatedTicker
    var onTap: (() -> Void)?

    private var changeColor: Color {
        ticker.isPositive ? AppColors.bullish : AppColors.bearish
    }

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Symbol and chevron.
                //
                // Explicit `xs` spacing rather than the default: a `Spacer` between two views
                // takes the stack spacing on BOTH sides, so the default ~8pt cost the symbol
                // 16pt of the 76pt content width. It is invisible at default size (the Spacer
                // absorbs it) and only pays off once the text grows.
                HStack(spacing: AppSpacing.xs) {
                    Text(ticker.symbol)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.8)

                    Spacer(minLength: 0)

                    Image(systemName: "chevron.right")
                        .font(AppTypography.iconTiny).fontWeight(.semibold)
                        .foregroundColor(AppColors.textMuted)
                }

                // Company name.
                //
                // `lineLimit(1)` with NO `minimumScaleFactor`, deliberately. `caption` is 11pt,
                // which is the stated floor for dense data, and 0.85 would resolve it to 9.35pt.
                // A company name is unbounded ("Alibaba Group Holding Limited"), so it truncates
                // either way — the scale factor would buy roughly one character in exchange for
                // breaking the floor. Truncation is the better degradation.
                Text(ticker.name)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(1)

                // Price. The widest string in the card, and the crypto detail screen reuses this
                // card with an unseparated `$119332.11` — ~110pt of glyphs at the 1.4x cap in a
                // 76pt box. 0.7 is derived from that worst case (110 x 0.7 = 77pt), and still
                // resolves to 14.7pt, clear of the 11pt floor.
                //
                // Had NO `lineLimit` at all, so under pressure it broke onto a second line and
                // added a whole line of growth on top of the per-line growth.
                Text(ticker.formattedPrice)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
                    .allowsTightening(true)

                // Change percentage
                Text(ticker.formattedChange)
                    .font(AppTypography.labelSmall).fontWeight(.semibold)
                    .foregroundColor(changeColor)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                    .allowsTightening(true)
            }
            .padding(AppSpacing.md)
            // WIDTH is pinned, HEIGHT is a floor. Three parts, all load-bearing:
            //
            //  • `minWidth == maxWidth` PINS the width. It reads oddly, but `width:` belongs to
            //    the other `frame` overload and cannot be combined with `minHeight:` — mixing
            //    them is a compile error, not a style choice. A bare `minWidth` would be wrong
            //    anyway: the company name is `lineLimit(1)` with no upper bound and a horizontal
            //    ScrollView proposes unbounded width, so the card would stretch to fit
            //    "Alibaba Group Holding Limited" in full. Truncating it is the intent.
            //  • `minHeight: 120` — preserves today's card size, so nothing moves at the default
            //    content size.
            //  • `maxHeight: .infinity` — lets the card accept the height the parent HStack
            //    resolves, so every card in the row stays the same height once they can grow.
            //    The parent uses `HStack(alignment: .top)` to match.
            //
            // The `Spacer()` that used to sit between the name and the price is gone: inside a
            // fixed 120pt box it was ~34pt of dead air that collapsed to nothing under exactly
            // the pressure it should have absorbed. Centring the block instead gives the same
            // card footprint with real breathing room top and bottom.
            .frame(minWidth: 100, maxWidth: 100,
                   minHeight: 120, maxHeight: .infinity, alignment: .leading)
            .cardSurface(cornerRadius: AppCornerRadius.medium)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            ForEach(RelatedTicker.sampleData) { ticker in
                RelatedTickerCard(ticker: ticker)
            }
        }
        .padding()
    }
    .background(AppColors.background)
}
