//
//  TickerNewsExpandedContent.swift
//  ios
//
//  Molecule: Expanded content section for news card with bullet points
//

import SwiftUI

struct TickerNewsExpandedContent: View {
    let bullets: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ForEach(Array(bullets.enumerated()), id: \.offset) { index, bullet in
                // The final bullet is the "why investors care" conclusion. The
                // arrow glyph marks it now, so any lead-in ("The takeaway for
                // everyday investors, …") is stripped from the text. This surface
                // needs the strip most: per-article bullets have NO invalidation —
                // re-enrichment is gated on a boolean and `expires_at` is
                // re-stamped on every refresh — so an article that keeps
                // circulating keeps its old wording indefinitely.
                let isLast = index == bullets.count - 1
                NewsCardBulletPoint(
                    text: isLast ? bullet.strippingConclusionLeadIn() : bullet,
                    isConclusion: isLast
                )
            }
        }
    }
}

#Preview {
    TickerNewsExpandedContent(
        bullets: [
            "High Pre-Orders Abroad: Apple is seeing unusually strong pre-order numbers in Europe and Asia, indicating strong international interest before the official launch.",
            "Supply Chain Scaling: Apple is ramping up production and logistics overseas to meet anticipated demand and prevent stock shortages.",
            "Premium Market Appeal: Early excitement suggests that Apple's Vision Pro is resonating with tech enthusiasts and luxury consumers globally."
        ]
    )
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
}
