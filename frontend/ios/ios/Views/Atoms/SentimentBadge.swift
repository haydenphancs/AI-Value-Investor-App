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
