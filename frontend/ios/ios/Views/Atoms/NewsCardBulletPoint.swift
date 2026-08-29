//
//  NewsCardBulletPoint.swift
//  ios
//
//  Atom: Bullet point item for news card expanded content
//

import SwiftUI

struct NewsCardBulletPoint: View {
    let text: String
    /// The final bullet — the conclusion. Marked with an arrow instead of a dot,
    /// which is what replaced the old "The takeaway," wording. Defaulted so the
    /// atom's other callers are unaffected.
    var isConclusion: Bool = false

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            SummaryBulletGlyph(
                isConclusion: isConclusion,
                color: AppColors.textMuted,
                // 12pt here, not the 14pt of the insight card — the glyph centres
                // itself on whichever it is given.
                textFont: AppTypography.labelSmall
            )

            // One uniform color for the whole bullet. The previous version
            // bolded + brightened any text before a colon (a generic "Label:"
            // treatment), which made the final "The takeaway:" line stand out as
            // bold — the exact thing that should read as a plain sentence. The
            // takeaway's colon→comma is handled by the list renderer for the last
            // bullet (see TickerNewsExpandedContent / InsightsSummaryCard).
            Text(text)
                .font(AppTypography.labelSmall)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.md) {
        NewsCardBulletPoint(text: "High Pre-Orders Abroad: Apple is seeing unusually strong pre-order numbers in Europe and Asia.")
        NewsCardBulletPoint(text: "Supply Chain Scaling: Apple is ramping up production and logistics overseas.")
        NewsCardBulletPoint(text: "An example: This is an explain.")
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
}
