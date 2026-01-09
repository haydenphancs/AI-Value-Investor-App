//
//  IndicatorSignalBadge.swift
//  ios
//
//  Badge showing indicator signal (Buy/Sell/Neutral) with optional arrow
//

import SwiftUI

struct IndicatorSignalBadge: View {
    let signal: IndicatorSignal

    var body: some View {
        HStack(spacing: AppSpacing.xxs) {
            Text(signal.rawValue)
                .font(AppTypography.caption)
                .foregroundColor(signal.color)

            if let icon = signal.arrowIcon {
                Image(systemName: icon)
                    .font(.system(size: 8, weight: .bold))
                    .foregroundColor(signal.color)
            }
        }
    }
}

// MARK: - Indicator Summary Badges (Buy: X | Neutral: X | Sell: X)
struct IndicatorSummaryBadges: View {
    let summary: IndicatorSummary

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            SummaryBadge(label: "Buy:", count: summary.buyCount, color: AppColors.bullish)
            SummaryBadge(label: "Neutral:", count: summary.neutralCount, color: AppColors.textSecondary)
            SummaryBadge(label: "Sell:", count: summary.sellCount, color: AppColors.bearish)
        }
    }
}

struct SummaryBadge: View {
    let label: String
    let count: Int
    let color: Color

    var body: some View {
        HStack(spacing: AppSpacing.xxs) {
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(color)

            Text("\(count)")
                .font(AppTypography.captionBold)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.xs)
                .padding(.vertical, 2)
                .background(color.opacity(0.2))
                .cornerRadius(AppCornerRadius.small)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            HStack(spacing: AppSpacing.lg) {
                IndicatorSignalBadge(signal: .buy)
                IndicatorSignalBadge(signal: .sell)
                IndicatorSignalBadge(signal: .neutral)
            }

            IndicatorSummaryBadges(
                summary: IndicatorSummary(buyCount: 8, neutralCount: 2, sellCount: 1)
            )
        }
        .padding()
    }
}
