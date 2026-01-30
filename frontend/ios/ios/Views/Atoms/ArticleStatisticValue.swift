//
//  ArticleStatisticValue.swift
//  ios
//
//  Atom: Large statistic display with value and label
//

import SwiftUI

struct ArticleStatisticValue: View {
    let value: String
    let label: String
    var trend: StatisticTrend?
    var trendValue: String?
    var alignment: HorizontalAlignment = .leading

    var body: some View {
        VStack(alignment: alignment, spacing: AppSpacing.xs) {
            // Value with optional trend
            HStack(spacing: AppSpacing.xs) {
                Text(value)
                    .font(.system(size: 28, weight: .bold, design: .rounded))
                    .foregroundColor(AppColors.textPrimary)

                if let trend = trend, let trendValue = trendValue {
                    HStack(spacing: 2) {
                        Image(systemName: trend.icon)
                            .font(.system(size: 10, weight: .bold))
                        Text(trendValue)
                            .font(.system(size: 11, weight: .semibold))
                    }
                    .foregroundColor(trend.color)
                    .padding(.horizontal, AppSpacing.xs)
                    .padding(.vertical, 2)
                    .background(
                        Capsule()
                            .fill(trend.color.opacity(0.15))
                    )
                }
            }

            // Label
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textSecondary)
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.xl) {
        ArticleStatisticValue(
            value: "$180B",
            label: "Total Value Locked",
            trend: .up,
            trendValue: "340%"
        )

        ArticleStatisticValue(
            value: "4.2M",
            label: "Daily Active Users",
            trend: .up,
            trendValue: "127%",
            alignment: .center
        )

        ArticleStatisticValue(
            value: "2,400+",
            label: "DeFi Protocols"
        )
    }
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
