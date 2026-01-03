//
//  TipCard.swift
//  ios
//
//  Molecule: Tip/recommendation card
//

import SwiftUI

struct TipCard: View {
    let tip: TipData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            Text(tip.title)
                .font(AppTypography.captionBold)
                .foregroundColor(AppColors.textSecondary)

            Text(tip.content)
                .font(AppTypography.callout)
                .foregroundColor(AppColors.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

#Preview {
    TipCard(
        tip: TipData(
            title: "RISK MITIGATION TIP",
            content: "Consider diversifying your portfolio and maintaining a long-term investment horizon to weather short-term volatility."
        )
    )
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
