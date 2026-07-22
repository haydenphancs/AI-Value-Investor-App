//
//  InsightsSummaryCard.swift
//  ios
//
//  Organism: AI-generated insights summary card for Updates screen
//

import SwiftUI

struct InsightsSummaryCard: View {
    let summary: NewsInsightSummary

    /// Every branch keeps the timestamp. A bare "Catching up…" throws away the
    /// one fact the reader needs — how old this brief is — and both flags that
    /// can produce it are statements about the BACKGROUND SWEEPER, which is
    /// asleep outside market hours. Suppressing the date on the strength of a
    /// refresh that may be 60 hours away is how the original bug read.
    ///
    /// The `timeAgo` is the age of the CONTENT (when the summary was last
    /// generated). On a quiet ticker that can be many hours even though the card
    /// is perfectly current — the sweeper regenerates only when the underlying
    /// news actually changes and otherwise re-verifies the existing card as
    /// still correct. So the default branch appends "up to date": the brief has
    /// been checked and nothing has changed, which is the opposite of stale.
    /// `isStale` (the sweeper hasn't re-checked recently) and `isRefreshing` (a
    /// new AI card is being produced) still take precedence and say so.
    private var footerText: String {
        if summary.isRefreshing { return "\(summary.timeAgo) · catching up" }
        if summary.isStale { return "\(summary.timeAgo) · checking for updates" }
        return "\(summary.timeAgo) · up to date"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Header
            HStack {
                // The sparkle mark is the report's AI-provenance treatment, and
                // it is gated on `isAIGenerated` for the same reason the badge
                // beside it is: the fallback card is a deterministic list of
                // real headlines that no model wrote. Putting an AI mark on it
                // is the exact claim this screen was rebuilt to remove — so the
                // fallback keeps a plain, neutral header instead.
                if summary.isAIGenerated {
                    AIInsightLabel(
                        text: "Insights",
                        font: AppTypography.bodyEmphasis,
                        iconFont: AppTypography.iconSmall
                    )
                } else {
                    HStack(spacing: AppSpacing.sm) {
                        Image(systemName: "newspaper")
                            .font(AppTypography.iconSmall).fontWeight(.semibold)
                            .foregroundColor(AppColors.textSecondary)

                        Text("Latest")
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(AppColors.textPrimary)
                    }
                }

                Spacer()

                // The AI badge is shown ONLY for text a model actually wrote.
                // The deterministic fallback card is a list of real headlines;
                // badging it "AI Summary" would claim authorship that doesn't
                // exist. A plain label keeps it honest.
                if summary.isAIGenerated {
                    AIBadge(text: summary.summaryBadgeText)
                } else {
                    Text(summary.summaryBadgeText)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
            }

            // Headline
            Text(summary.headline)
                .font(AppTypography.titleCompact)
                .foregroundColor(AppColors.textPrimary)
                .fixedSize(horizontal: false, vertical: true)

            // Bullet Points
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                // Index-keyed, not `id: \.self` — two identical bullet lines
                // would collide and one would be dropped/glitched.
                ForEach(Array(summary.bulletPoints.enumerated()), id: \.offset) { index, point in
                    // Final bullet = the takeaway; render its lead-in colon as a
                    // comma so it reads as a sentence, matching the article cards.
                    let isLast = index == summary.bulletPoints.count - 1
                    HStack(alignment: .top, spacing: AppSpacing.sm) {
                        Circle()
                            .fill(AppColors.textSecondary)
                            .frame(width: 5, height: 5)
                            .padding(.top, 6)

                        Text(isLast ? point.normalizingLeadInColon() : point)
                            .font(AppTypography.bodySmall)
                            .foregroundColor(AppColors.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }

            // Footer
            HStack {
                // Sentiment on the fallback card is a tally of already-enriched
                // articles, not a model's judgement of the whole set — and with
                // nothing enriched it is just "Neutral" by default. Suppress it
                // rather than present a verdict nobody reached.
                if summary.isAIGenerated {
                    SentimentBadge(sentiment: summary.sentiment)
                }

                Spacer()

                // Never swallow the timestamp. "Catching up…" on its own hides
                // the one fact the reader needs — how old this brief is — and
                // it belongs ONLY to `isRefreshing`, which means a real AI card
                // is genuinely being produced right now (the deterministic
                // headline-list fallback is showing in the meantime).
                // `isStale` is weaker: the card is real and dated, the sweeper
                // just hasn't re-verified it. Say both, don't trade one away.
                Text(footerText)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview("AI card") {
    InsightsSummaryCard(
        summary: NewsInsightSummary(
            headline: "Tech Stocks Rally on Strong AI Earnings",
            bulletPoints: [
                "Major technology companies posted impressive Q4 results driven by AI infrastructure investments.",
                "Cloud computing revenue exceeded expectations, with Microsoft and Google leading the charge. Market sentiment remains bullish heading into 2024."
            ],
            sentiment: .bullish,
            updatedAt: Date().addingTimeInterval(-3600),
            summaryType: "48h · AI Summary"
        )
    )
    .padding()
    .background(AppColors.background)
}

/// The deterministic fallback — real headlines, no model. It must carry NO
/// sparkle mark, NO AI badge and NO sentiment verdict. Previewed alongside the
/// AI card so a future edit that drops one of those gates is visible here.
#Preview("Fallback card — no AI claim") {
    InsightsSummaryCard(
        summary: NewsInsightSummary(
            headline: "Latest Market headlines",
            bulletPoints: [
                "Jamie Dimon says markets underestimate risks",
                "Jim Cramer says it's time to look beyond tech"
            ],
            sentiment: .neutral,
            updatedAt: Date(),
            summaryType: "Latest headlines",
            isAIGenerated: false,
            isRefreshing: true
        )
    )
    .padding()
    .background(AppColors.background)
}
