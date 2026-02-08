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
    let backgroundColor: Color
    var fontSize: Font = AppTypography.caption

    var body: some View {
        Text(text)
            .font(fontSize)
            .fontWeight(.semibold)
            .foregroundColor(textColor)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.small)
                    .fill(backgroundColor)
            )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        ReportSentimentBadge(
            text: "Overpriced",
            textColor: AppColors.bearish,
            backgroundColor: AppColors.bearish.opacity(0.15)
        )
        ReportSentimentBadge(
            text: "Underpriced",
            textColor: AppColors.bullish,
            backgroundColor: AppColors.bullish.opacity(0.15)
        )
        ReportSentimentBadge(
            text: "RAISED",
            textColor: AppColors.bullish,
            backgroundColor: AppColors.bullish.opacity(0.15)
        )
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
