//
//  InsightCatalystBullet.swift
//  ios
//
//  Molecule: the "why it moved" catalyst, rendered as the FIRST bullet of the
//  Insights card rather than as its own inset block.
//

import SwiftUI

/// The grounded price-move explanation, as one bullet in the insight body.
///
/// It used to be a separate boxed section under the bullets, which read as a
/// second card and — because the catalyst and the news bullets are produced by
/// two different model calls over the same day's evidence — usually said the same
/// thing twice. The backend now hands the catalyst to the roll-up prompt so the
/// bullets stay additive; this view is the other half of that fix, folding the
/// catalyst into one continuous body.
///
/// The text is rendered VERBATIM from the stored block. It is the only web-cited
/// content on the card (its sources are merged into the card's tappable source
/// list), so it is deliberately never paraphrased on the way to the screen.
struct InsightCatalystBullet: View {
    let move: InsightPriceMove

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            // Matches the sibling bullets' glyph exactly — same size, same
            // top padding — so the body reads as ONE list. The bolt lives in
            // the card header instead; repeating it here would put two bolts
            // three lines apart.
            Circle()
                .fill(AppColors.textSecondary)
                .frame(width: 5, height: 5)
                .padding(.top, 6)

            // Text + Text concatenation, not an HStack: the change and the
            // reason must reflow as ONE paragraph. An HStack would pin the
            // percentage to its own column and leave a ragged gap when the
            // reason wraps, which it almost always does.
            changeRun + Text(move.displayLine)
                .font(AppTypography.bodySmall)
                .foregroundColor(AppColors.textSecondary)
        }
        .fixedSize(horizontal: false, vertical: true)
        .accessibilityElement(children: .combine)
    }

    /// The signed change, colour-coded, with a separator — or nothing at all when
    /// the value is absent or non-finite (`formattedChange` guards that).
    ///
    /// Direction comes from `isPositive`, i.e. from the number itself, so the
    /// colour can never disagree with the sign that is printed beside it.
    private var changeRun: Text {
        guard let change = move.formattedChange else { return Text("") }
        return Text("\(change)  ")
            .font(AppTypography.bodySmall)
            .fontWeight(.semibold)
            .foregroundColor(move.isPositive ? AppColors.bullish : AppColors.bearish)
    }
}

#Preview("Positive, with tag") {
    InsightCatalystBullet(
        move: InsightPriceMove(
            tier: "Extreme",
            changePercent: 20.4,
            catalystTag: "Q2 Earnings Beat and Raised Guidance",
            reason: "Salesforce shares surged after reporting second-quarter adjusted earnings per share of $5.90, beating analyst estimates of $3.27."
        )
    )
    .padding()
    .background(AppColors.cardBackground)
}

#Preview("Negative, no tag, no change") {
    VStack(alignment: .leading, spacing: AppSpacing.sm) {
        InsightCatalystBullet(
            move: InsightPriceMove(
                tier: "Unusual",
                changePercent: -8.2,
                catalystTag: nil,
                reason: "A broad sector selloff on rising rates, with no company-specific news."
            )
        )
        InsightCatalystBullet(
            move: InsightPriceMove(
                tier: "Unusual",
                changePercent: nil,
                catalystTag: "Guidance Cut",
                reason: "Full-year revenue guidance was lowered."
            )
        )
    }
    .padding()
    .background(AppColors.cardBackground)
}
