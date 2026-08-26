//
//  TickerDetailRelatedSection.swift
//  ios
//
//  Organism: People Also Check section for Ticker Detail
//

import SwiftUI

struct TickerDetailRelatedSection: View {
    let relatedTickers: [RelatedTicker]
    var onTickerTap: ((RelatedTicker) -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header
            Text("People Also Check")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Horizontal scroll of ticker cards
            ScrollView(.horizontal, showsIndicators: false) {
                // `alignment: .top` pairs with the card's `minHeight`/`maxHeight: .infinity` frame:
                // cards can now grow with the text, and a taller one must not vertically offset
                // its neighbours. Without it the HStack centres them and the row looks ragged.
                HStack(alignment: .top, spacing: AppSpacing.md) {
                    ForEach(relatedTickers) { ticker in
                        RelatedTickerCard(ticker: ticker) {
                            onTickerTap?(ticker)
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }
        }
    }
}

#Preview {
    TickerDetailRelatedSection(relatedTickers: RelatedTicker.sampleData)
        .padding(.vertical)
        .background(AppColors.background)
}
