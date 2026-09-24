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
                .font(AppTypography.iconXL).fontWeight(.medium)
                .foregroundColor(AppColors.bullish)

            // Quote text
            Text("\"\(quote.text)\"")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textPrimary)
                .multilineTextAlignment(.center)
                .lineSpacing(4)

            // Author + primary source
            VStack(spacing: AppSpacing.xs) {
                Text("— \(quote.author)")
                    .font(AppTypography.labelSmall)
                    .foregroundColor(AppColors.textMuted)

                if let citation = quote.citation {
                    Text(verbatim: citation)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .multilineTextAlignment(.center)
                }
            }
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
        // One VoiceOver element: quote, author and source, instead of four fragments.
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(quote.accessibilityLabel)
    }
}

#Preview {
    InvestorQuoteCard(quote: .buffettQuote)
        .padding()
        .background(AppColors.background)
}
