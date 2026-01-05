//
//  NewsDetailMetaRow.swift
//  ios
//
//  Molecule: Meta information row showing date, read time, and sentiment
//

import SwiftUI

struct NewsDetailMetaRow: View {
    let date: String
    let readTimeMinutes: Int
    let sentiment: NewsSentiment

    var body: some View {
        HStack(spacing: AppSpacing.lg) {
            // Calendar Icon and Date
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "calendar")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(AppColors.textSecondary)

                Text(date)
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)
            }

            // Read Time
            HStack(spacing: AppSpacing.xs) {
                Image(systemName: "clock")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(AppColors.textSecondary)

                Text("\(readTimeMinutes) min read")
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)
            }

            Spacer()

            // Sentiment Badge
            NewsSentimentBadge(sentiment: sentiment)
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
