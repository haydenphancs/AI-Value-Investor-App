//
//  AnalysisConsensusBadge.swift
//  ios
//
//  Badge displaying analyst consensus rating (STRONG BUY, BUY, etc.)
//

import SwiftUI

struct AnalysisConsensusBadge: View {
    let consensus: AnalystConsensus

    var body: some View {
        Text(consensus.rawValue)
            .font(AppTypography.title2)
            .fontWeight(.bold)
            .foregroundColor(consensus.color)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.lg) {
            AnalysisConsensusBadge(consensus: .strongBuy)
            AnalysisConsensusBadge(consensus: .buy)
            AnalysisConsensusBadge(consensus: .hold)
            AnalysisConsensusBadge(consensus: .sell)
            AnalysisConsensusBadge(consensus: .strongSell)
        }
    }
}
