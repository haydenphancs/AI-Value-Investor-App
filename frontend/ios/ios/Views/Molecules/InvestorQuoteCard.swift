//
//  InvestorQuoteCard.swift
//  ios
//
//  Molecule: Card displaying an inspirational investor quote
//

import SwiftUI

struct InvestorQuoteCard: View {
    let quote: InvestorQuote

    var body: some View {
        VStack(spacing: AppSpacing.lg) {
            // Quote icon
            Image(systemName: "quote.opening")
                .font(.system(size: 24, weight: .medium))
                .foregroundColor(AppColors.bullish)

            // Quote text
            Text("\"\(quote.text)\"")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
                .multilineTextAlignment(.center)
                .lineSpacing(4)

            // Author
            Text("— \(quote.author)")
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textMuted)
        }
        .padding(AppSpacing.xxl)
        .frame(maxWidth: .infinity)
        .background(
            LinearGradient(
                colors: [
                    AppColors.bullish.opacity(0.15),
                    AppColors.bullish.opacity(0.05)
                ],
                startPoint: .top,
                endPoint: .bottom
            )
        )
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    InvestorQuoteCard(quote: .buffettQuote)
        .padding()
        .background(AppColors.background)
        .preferredColorScheme(.dark)
}
