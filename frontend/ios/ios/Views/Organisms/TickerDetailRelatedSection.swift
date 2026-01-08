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
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Horizontal scroll of ticker cards
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.md) {
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
        .preferredColorScheme(.dark)
}
