//
//  NewsSentimentBadge.swift
//  ios
//
//  Atom: Displays news sentiment as a colored badge
//

import SwiftUI

struct NewsSentimentBadge: View {
    let sentiment: NewsSentiment

    private var backgroundColor: Color {
        switch sentiment {
        case .positive:
            return AppColors.bullish.opacity(0.2)
        case .negative:
            return AppColors.bearish.opacity(0.2)
        case .neutral:
            return AppColors.neutral.opacity(0.2)
        }
    }

    private var textColor: Color {
        switch sentiment {
        case .positive:
            return AppColors.bullish
        case .negative:
            return AppColors.bearish
        case .neutral:
            return AppColors.neutral
        }
    }

    var body: some View {
        Text(sentiment.displayName)
            .font(AppTypography.captionBold)
            .foregroundColor(textColor)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(backgroundColor)
            .clipShape(Capsule())
    }
}

#Preview {
    VStack(spacing: 10) {
        NewsSentimentBadge(sentiment: .positive)
        NewsSentimentBadge(sentiment: .negative)
        NewsSentimentBadge(sentiment: .neutral)
    }
    .padding()
    .background(AppColors.background)
}
