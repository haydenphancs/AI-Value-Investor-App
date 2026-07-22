//
//  NewsCardBulletPoint.swift
//  ios
//
//  Atom: Bullet point item for news card expanded content
//

import SwiftUI

struct NewsCardBulletPoint: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            // Bullet dot
            Circle()
                .fill(AppColors.textMuted)
                .frame(width: 5, height: 5)
                .padding(.top, 6)

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
    .preferredColorScheme(.dark)
}
