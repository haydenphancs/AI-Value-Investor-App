//
//  InsightsSummaryCard.swift
//  ios
//
//  Organism: AI-generated insights summary card for Updates screen
//

import SwiftUI

struct InsightsSummaryCard: View {
    let summary: NewsInsightSummary
    /// Tapping the card opens the sources screen. Only wired/shown when the card
    /// actually has sources.
    var onOpenSources: (() -> Void)? = nil

    private var hasSources: Bool { !summary.sources.isEmpty }

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

    /// How many model-written bullets fit beside the catalyst.
    ///
    /// The catalyst occupies the first bullet slot when there is one, so the
    /// budget shrinks by one and the card can never grow past five rows. The
    /// backend still validates 2-5 bullets for every card, catalyst or not — a
    /// calm ticker is unaffected, so the cap lives here rather than in the
    /// shared MAX_BULLETS every scope is generated against.
    private var visibleBullets: ArraySlice<String> {
        summary.bulletPoints.prefix(catalyst == nil ? 5 : 4)
    }

    /// The "why it moved" catalyst, when this card has one. Never on the
    /// deterministic fallback card — nothing there is model-written or cited.
    private var catalyst: InsightPriceMove? {
        summary.isAIGenerated ? summary.priceMove : nil
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

                    // Signposts the catalyst bullet directly below. Shown only
                    // when there IS one — most tickers never move enough to earn
                    // a catalyst, and their header stays a plain "Insights".
                    // The label shrinks before the sentiment/window badges do:
                    // it is the one header element whose meaning survives being
                    // read at a glance from the bolt alone.
                    if catalyst != nil {
                        HStack(spacing: AppSpacing.xs) {
                            Image(systemName: "bolt.fill")
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.primaryBlue)

                            Text("Why it moved")
                                .font(AppTypography.caption)
                                .fontWeight(.semibold)
                                .foregroundColor(AppColors.textSecondary)
                                .lineLimit(1)
                                .minimumScaleFactor(0.8)
                        }
                        .layoutPriority(-1)
                        .accessibilityElement(children: .combine)
                    }
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

                // Sentiment sits top-right next to the window badge (Neutral · 24h).
                // Gated on isAIGenerated for the same reason the badge is: the
                // fallback card's sentiment is a tally of enriched articles, not a
                // model verdict, so we don't present it as one.
                if summary.isAIGenerated {
                    SentimentBadge(sentiment: summary.sentiment)
                }

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

            // Bullet Points — ONE body. The catalyst leads when there is one,
            // then the model's bullets, which the prompt has been told not to
            // restate it. It used to sit in its own inset box BELOW these, which
            // read as a second card and repeated whatever the bullets already said.
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                if let move = catalyst {
                    InsightCatalystBullet(move: move)
                }

                // Index-keyed, not `id: \.self` — two identical bullet lines
                // would collide and one would be dropped/glitched.
                ForEach(Array(visibleBullets.enumerated()), id: \.offset) { index, point in
                    // Final bullet = the takeaway; render its lead-in colon as a
                    // comma so it reads as a sentence, matching the article cards.
                    // Measured against the TRUNCATED list, so a card whose tail was
                    // dropped by the cap does not comma-splice a mid-list bullet.
                    let isLast = index == visibleBullets.count - 1
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

            // Footer: the timestamp on the LEFT, the sources affordance on the
            // SAME row to the right. Never swallow the timestamp — "checking for
            // updates"/"catching up" are claims about the SWEEPER, and "up to date"
            // means checked-and-unchanged; all three keep the content age.
            HStack {
                Text(footerText)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                Spacer()

                // Tap affordance — the whole card opens the sources screen. Shown
                // only when there are sources to open (older cards have none).
                if hasSources {
                    HStack(spacing: AppSpacing.xs) {
                        Text("\(summary.sources.count) source\(summary.sources.count == 1 ? "" : "s")")
                            .font(AppTypography.caption)
                            .fontWeight(.semibold)
                        Image(systemName: "chevron.right")
                            .font(AppTypography.caption)
                    }
                    .foregroundColor(AppColors.primaryBlue)
                }
            }
        }
        .padding(AppSpacing.lg)
        .cardSurface(cornerRadius: AppCornerRadius.large)
        .contentShape(Rectangle())
        .onTapGesture { if hasSources { onOpenSources?() } }
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
            summaryType: "48h"
        )
    )
    .padding()
    .background(AppColors.background)
}

/// AI card carrying a grounded "Why it moved" block — the big-move state. The
/// row is gated on `isAIGenerated && priceMove != nil`, so this preview is the
/// reliable visual check for it (a live Unusual/Extreme move + catalyst is not
/// reproducible on demand).
#Preview("AI card — why it moved") {
    InsightsSummaryCard(
        summary: NewsInsightSummary(
            headline: "Shares Slide After Surprise Guidance Cut",
            bulletPoints: [
                "The company trimmed full-year revenue guidance below the Street's estimate, citing softer enterprise demand.",
                "Several analysts flagged margin pressure into the next quarter. The takeaway, the reset lowers the near-term bar but the long-term thesis is intact."
            ],
            sentiment: .bearish,
            updatedAt: Date().addingTimeInterval(-1800),
            summaryType: "48h",
            priceMove: InsightPriceMove(
                tier: "Extreme",
                changePercent: -8.4,
                catalystTag: "Guidance Cut",
                reason: "Management lowered FY revenue guidance below consensus on softer enterprise demand."
            )
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
