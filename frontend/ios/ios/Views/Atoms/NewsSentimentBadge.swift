//
//  NewsSentimentBadge.swift
//  ios
//
//  Atom: Displays news sentiment as a colored badge
//

import SwiftUI

struct NewsSentimentBadge: View {
    let sentiment: NewsSentiment

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
            .font(AppTypography.captionEmphasis)
            .foregroundColor(textColor)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            // Opaque, not the ink's own 20% tint: gain/loss/caution measure 4.08/3.98/3.92
            // there in light even on a white card (test_ios_theme_parity §6c).
            .background(AppColors.cardBackgroundLight)
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
