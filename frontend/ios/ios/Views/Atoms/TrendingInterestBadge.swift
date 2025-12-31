//
//  TrendingInterestBadge.swift
//  ios
//
//  Atom: Displays trending interest percentage with chart icon
//

import SwiftUI

struct TrendingInterestBadge: View {
    let interestPercent: Int

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: "chart.line.uptrend.xyaxis")
                .font(.system(size: 10, weight: .semibold))

            Text("+\(interestPercent)% interest")
                .font(AppTypography.caption)
                .fontWeight(.medium)
        }
        .foregroundColor(AppColors.bullish)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        TrendingInterestBadge(interestPercent: 127)
        TrendingInterestBadge(interestPercent: 89)
        TrendingInterestBadge(interestPercent: 203)
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
