//
//  RelatedTickerChip.swift
//  ios
//
//  Atom: Small chip displaying a related ticker symbol
//

import SwiftUI

struct RelatedTickerChip: View {
    let symbol: String
    var isHighlighted: Bool = false

    var body: some View {
        Text(symbol)
            .font(AppTypography.captionEmphasis)
            .foregroundColor(isHighlighted ? AppColors.primaryBlue : AppColors.textSecondary)
            // A ticker symbol is atomic — never let it wrap to "SC\nHW". With the
            // flow layout each chip gets its natural width, so this just keeps the
            // glyphs on one line.
            .lineLimit(1)
            .fixedSize()
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(
                isHighlighted
                    ? AppColors.primaryBlue.opacity(0.15)
                    : AppColors.cardBackgroundLight
            )
            .cornerRadius(AppCornerRadius.small)
    }
}

#Preview {
    HStack(spacing: AppSpacing.sm) {
        RelatedTickerChip(symbol: "AAPL", isHighlighted: true)
        RelatedTickerChip(symbol: "MSFT")
        RelatedTickerChip(symbol: "GOOGL")
        RelatedTickerChip(symbol: "META")
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
