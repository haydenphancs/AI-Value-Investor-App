//
//  AnalystConsensusRow.swift
//  ios
//
//  Row displaying analyst consensus and target price
//

import SwiftUI

struct AnalystConsensusRow: View {
    let consensus: AnalystConsensus
    let targetPrice: String
    let targetUpside: String

    var body: some View {
        HStack {
            // Left side - Consensus
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text("Analyst Consensus")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                AnalysisConsensusBadge(consensus: consensus)
            }

            Spacer()

            // Right side - Target Price
            VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                Text("Target")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                Text(targetPrice)
                    .font(AppTypography.title2)
                    .fontWeight(.bold)
                    .foregroundColor(AppColors.textPrimary)

                Text(targetUpside)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.bullish)
            }
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        AnalystConsensusRow(
            consensus: .strongBuy,
            targetPrice: "$212.60",
            targetUpside: "+17.2% upside"
        )
        .padding()
    }
}
