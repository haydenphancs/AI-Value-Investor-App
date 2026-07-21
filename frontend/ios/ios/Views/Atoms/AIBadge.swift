//
//  AIBadge.swift
//  ios
//
//  Atom: AI Summary badge indicator
//

import SwiftUI

struct AIBadge: View {
    let text: String

    var body: some View {
        Text(text)
            .font(AppTypography.captionEmphasis)
            // Same indigo→cyan ramp as the "Insights" label it sits beside, from
            // one shared token so restyling either cannot break the pairing.
            .foregroundStyle(AppGradients.ai)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(AppGradients.aiSubtle)
            .clipShape(Capsule())
    }
}

#Preview {
    VStack(alignment: .leading, spacing: 16) {
        // The real pairing: label and badge on one row, which is the only way
        // to see whether they actually read as the same colour.
        HStack {
            AIInsightLabel(
                text: "Insights",
                font: AppTypography.bodyEmphasis,
                iconFont: AppTypography.iconSmall
            )
            Spacer()
            AIBadge(text: "24h · AI Summary")
        }

        AIBadge(text: "AI Generated")
    }
    .padding()
    .background(AppColors.cardBackground)
}
