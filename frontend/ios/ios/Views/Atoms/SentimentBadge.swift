//
//  SentimentBadge.swift
//  ios
//
//  Atom: Displays market sentiment as a badge
//

import SwiftUI

struct SentimentBadge: View {
    let sentiment: MarketSentiment

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

    /// nil for neutral: the "minus" glyph reads as a stray "—" dash next to the
    /// label. Bullish/bearish keep their directional arrows; neutral shows the
    /// word alone.
    private var icon: String? {
        switch sentiment {
        case .bullish:
            return "arrow.up.right"
        case .bearish:
            return "arrow.down.right"
        case .neutral:
            return nil
        }
    }

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            if let icon {
                Image(systemName: icon)
                    .font(AppTypography.iconTiny).fontWeight(.bold)
            }

            Text(sentiment.rawValue)
                .font(AppTypography.captionEmphasis)
        }
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
        SentimentBadge(sentiment: .bullish)
        SentimentBadge(sentiment: .bearish)
        SentimentBadge(sentiment: .neutral)
    }
    .padding()
    .background(AppColors.background)
}
