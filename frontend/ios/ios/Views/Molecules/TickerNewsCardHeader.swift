//
//  TickerNewsCardHeader.swift
//  ios
//
//  Molecule: Header row for ticker news card with sentiment, time, and source
//

import SwiftUI

struct TickerNewsCardHeader: View {
    let sentiment: NewsSentiment
    let timeAgo: String
    let sourceName: String

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            // Sentiment badge
            NewsSentimentBadge(sentiment: sentiment)

            // Time ago
            Text(timeAgo)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            // Source name
            Text(sourceName)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)

            Spacer()
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.lg) {
        TickerNewsCardHeader(
            sentiment: .positive,
            timeAgo: "2h ago",
            sourceName: "Bloomberg"
        )

        TickerNewsCardHeader(
            sentiment: .neutral,
            timeAgo: "4h ago",
            sourceName: "TechCrunch"
        )

        TickerNewsCardHeader(
            sentiment: .negative,
            timeAgo: "8h ago",
            sourceName: "Financial Times"
        )
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
