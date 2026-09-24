//
//  ThemeInsightCard.swift
//  ios
//
//  Molecule: the theme detail's "Why it's moving" card — a dated, one-paragraph summary of
//  what is driving the theme, written once per US trading day after the close from its
//  stocks' news (backend `theme_insights_service`). Always shows its DATE: a summary is
//  about a specific session, and undated text beside live numbers reads as a claim about
//  today.
//

import SwiftUI

struct ThemeInsightCard: View {
    let insight: ThemeInsight
    var onTickerTap: ((String) -> Void)? = nil

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            HStack(alignment: .firstTextBaseline) {
                Label("Why it's moving", systemImage: AppSymbols.ai)
                    .font(AppTypography.labelEmphasis)
                    .foregroundColor(AppColors.primaryBlue)
                Spacer(minLength: AppSpacing.sm)
                if let asOf = insight.asOf {
                    Text(ThemeReviewDate.short(asOf))
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            if !insight.headline.isEmpty {
                Text(insight.headline)
                    .font(AppTypography.bodyEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Text(insight.summary)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)

            if !insight.tickers.isEmpty {
                HStack(spacing: 6) {
                    ForEach(insight.tickers.prefix(4), id: \.self) { ticker in
                        Button { onTickerTap?(ticker) } label: {
                            Text(ticker)
                                .font(AppTypography.captionEmphasis)
                                .foregroundColor(AppColors.textPrimary)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 4)
                                .background(Capsule().fill(AppColors.textPrimary.opacity(0.06)))
                        }
                        .buttonStyle(.plain)
                        .accessibilityHint("Opens \(ticker)")
                    }
                }
            }
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardSurface()
    }
}

#Preview {
    ThemeInsightCard(insight: ThemeInsight(dto: ThemeInsightDTO(
        asOf: "2026-09-23",
        headline: "Chip stocks slip as memory prices cool",
        summary: "Memory makers led the theme lower after an industry tracker reported softer DRAM contract prices; equipment names were steadier.",
        tickers: ["MU", "AMAT"]))!)
        .padding()
        .background(AppColors.background)
}
