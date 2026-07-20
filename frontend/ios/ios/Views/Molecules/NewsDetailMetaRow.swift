//
//  NewsDetailMetaRow.swift
//  ios
//
//  Molecule: Meta information row showing date, read time, and sentiment
//

import SwiftUI

struct NewsDetailMetaRow: View {
    let date: String
    /// Nil when the article body wasn't available to measure — the chip is
    /// hidden rather than showing an invented duration.
    let readTimeMinutes: Int?
    /// Nil until AI enrichment has produced a sentiment.
    let sentiment: NewsSentiment?

    var body: some View {
        HStack(spacing: AppSpacing.lg) {
            // Calendar Icon and Date
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "calendar")
                    .font(AppTypography.iconXS).fontWeight(.medium)
                    .foregroundColor(AppColors.textSecondary)

                Text(date)
                    .font(AppTypography.label)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Read Time
            if let readTimeMinutes {
                HStack(spacing: AppSpacing.xs) {
                    Image(systemName: "clock")
                        .font(AppTypography.iconXS).fontWeight(.medium)
                        .foregroundColor(AppColors.textSecondary)

                    Text("\(readTimeMinutes) min read")
                        .font(AppTypography.label)
                        .foregroundColor(AppColors.textSecondary)
                }
            }

            Spacer()

            // Sentiment Badge
            if let sentiment {
                NewsSentimentBadge(sentiment: sentiment)
            }
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        NewsDetailMetaRow(
            date: "Dec 15, 2024",
            readTimeMinutes: 4,
            sentiment: .negative
        )

        NewsDetailMetaRow(
            date: "Dec 14, 2024",
            readTimeMinutes: 6,
            sentiment: .positive
        )

        NewsDetailMetaRow(
            date: "Dec 13, 2024",
            readTimeMinutes: 3,
            sentiment: .neutral
        )
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
