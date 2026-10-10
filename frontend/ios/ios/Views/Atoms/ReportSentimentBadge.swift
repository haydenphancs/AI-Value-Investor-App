//
//  ReportSentimentBadge.swift
//  ios
//
//  Atom: Colored badge for sentiment labels (Overpriced, Underpriced, RAISED, etc.)
//

import SwiftUI

struct ReportSentimentBadge: View {
    let text: String
    let textColor: Color
    var fontSize: Font = AppTypography.caption

    var body: some View {
        Text(text)
            .font(fontSize)
            .fontWeight(.semibold)
            .foregroundColor(textColor)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            // Opaque. Callers used to pass `color.opacity(0.15)` of the ink's own hue, which
            // no text token survives in light (gain 4.39, loss 4.34, caution 4.20 even on a
            // white card — test_ios_theme_parity §6c); every text token is audited ≥ 4.5 here.
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(AppColors.cardBackgroundLight)
            )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ReportSentimentBadge(
            text: "Overpriced",
            textColor: AppColors.bearish
        )
        ReportSentimentBadge(
            text: "Underpriced",
            textColor: AppColors.bullish
        )
        ReportSentimentBadge(
            text: "RAISED",
            textColor: AppColors.bullish
        )
    }
    .padding()
    .background(AppColors.background)
}
