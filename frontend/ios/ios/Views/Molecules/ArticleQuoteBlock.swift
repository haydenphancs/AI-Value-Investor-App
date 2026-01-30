//
//  ArticleQuoteBlock.swift
//  ios
//
//  Molecule: Styled quote block with attribution
//

import SwiftUI

struct ArticleQuoteBlock: View {
    let text: String
    let attribution: String?

    var body: some View {
        HStack(spacing: 0) {
            // Quote line
            Rectangle()
                .fill(
                    LinearGradient(
                        colors: [
                            AppColors.primaryBlue,
                            AppColors.alertPurple
                        ],
                        startPoint: .top,
                        endPoint: .bottom
                    )
                )
                .frame(width: 3)

            // Content
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Quote icon
                Image(systemName: "quote.opening")
                    .font(.system(size: 20, weight: .bold))
                    .foregroundColor(AppColors.primaryBlue.opacity(0.5))

                // Quote text
                Text(text)
                    .font(AppTypography.body)
                    .foregroundColor(AppColors.textPrimary)
                    .italic()
                    .lineSpacing(6)
                    .fixedSize(horizontal: false, vertical: true)

                // Attribution
                if let attribution = attribution {
                    Text("— \(attribution)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .padding(.top, AppSpacing.xs)
                }
            }
            .padding(.leading, AppSpacing.lg)
        }
        .padding(.vertical, AppSpacing.md)
    }
}

#Preview {
    VStack(spacing: AppSpacing.xl) {
        ArticleQuoteBlock(
            text: "The future of finance isn't about going to the bank—it's about banking coming to you, wherever you are.",
            attribution: "Industry Analyst"
        )

        ArticleQuoteBlock(
            text: "Price is what you pay. Value is what you get.",
            attribution: "Warren Buffett"
        )

        ArticleQuoteBlock(
            text: "In the short run, the market is a voting machine but in the long run, it is a weighing machine.",
            attribution: nil
        )
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
