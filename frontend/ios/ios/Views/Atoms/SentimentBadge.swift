//
//  SentimentBadge.swift
//  ios
//
//  Atom: Displays market sentiment as a badge
//

import SwiftUI

struct SentimentBadge: View {
    let sentiment: MarketSentiment

    private var backgroundColor: Color {
        switch sentiment {
        case .bullish:
            return AppColors.bullish.opacity(0.2)
        case .bearish:
            return AppColors.bearish.opacity(0.2)
        case .neutral:
            return AppColors.neutral.opacity(0.2)
        }
    }

    private var textColor: Color {
        switch sentiment {
        case .bullish:
            return AppColors.bullish
        case .bearish:
            return AppColors.bearish
        case .neutral:
            return AppColors.neutral
        }
    }

    private var icon: String {
        switch sentiment {
        case .bullish:
            return "arrow.up.right"
        case .bearish:
            return "arrow.down.right"
        case .neutral:
            return "minus"
        }
    }

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: icon)
                .font(.system(size: 10, weight: .bold))

            Text(sentiment.rawValue)
                .font(AppTypography.captionBold)
        }
        .foregroundColor(textColor)
        .padding(.horizontal, AppSpacing.sm)
        .padding(.vertical, AppSpacing.xs)
        .background(backgroundColor)
        .clipShape(Capsule())
    }
}

#Preview {
    VStack(spacing: 10) {
        SentimentBadge(sentiment: .bullish)
        SentimentBadge(sentiment: .bearish)
        SentimentBadge(sentiment: .neutral)
    }
    .padding()
    .background(AppColors.background)
}
