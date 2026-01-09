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
            ForEach(Array(bullets.enumerated()), id: \.offset) { _, bullet in
                NewsCardBulletPoint(text: bullet)
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
    .preferredColorScheme(.dark)
}
