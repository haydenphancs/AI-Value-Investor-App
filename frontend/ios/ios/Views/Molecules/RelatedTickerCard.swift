//
//  RelatedTickerCard.swift
//  ios
//
//  Molecule: Card for related/similar tickers in horizontal scroll
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
                // Symbol and chevron
                HStack {
                    Text(ticker.symbol)
                        .font(AppTypography.bodySmallEmphasis)
                        .foregroundColor(AppColors.textPrimary)

                    Spacer()

                    Image(systemName: "chevron.right")
                        .font(AppTypography.iconTiny).fontWeight(.semibold)
                        .foregroundColor(AppColors.textMuted)
                }

                // Company name
                Text(ticker.name)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(1)

                Spacer()

                // Price
                Text(ticker.formattedPrice)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                // Change percentage
                Text(ticker.formattedChange)
                    .font(AppTypography.labelSmall)
                    .fontWeight(.semibold)
                    .foregroundColor(changeColor)
            }
            .padding(AppSpacing.md)
            .frame(width: 100, height: 120)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.medium)
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: AppSpacing.md) {
            ForEach(RelatedTicker.sampleData) { ticker in
                RelatedTickerCard(ticker: ticker)
            }
        }
        .padding()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
