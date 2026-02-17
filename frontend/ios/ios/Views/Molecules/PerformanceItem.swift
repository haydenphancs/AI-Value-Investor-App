//
//  PerformanceItem.swift
//  ios
//
//  Molecule: Individual performance period display with label and percentage
//

import SwiftUI

struct PerformanceItem: View {
    let period: PerformancePeriod

    private var color: Color {
        period.isPositive ? AppColors.bullish : AppColors.bearish
    }

    private var vsMarketColor: Color {
        period.isBeatingMarket ? AppColors.bullish : AppColors.bearish
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            // Period label
            Text(period.label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            // Percentage change
            Text(period.formattedChange)
                .font(AppTypography.calloutBold)
                .foregroundColor(color)

            // vs S&P 500 comparison (only shown when data exists)
            if let vsText = period.formattedVsMarket {
                Text(vsText)
                    .font(AppTypography.caption)
                    .foregroundColor(vsMarketColor)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.vertical, AppSpacing.sm)
        .padding(.horizontal, AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                .fill(color.opacity(0.1))
        )
    }
}

#Preview {
    LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: AppSpacing.sm), count: 3), spacing: AppSpacing.sm) {
        PerformanceItem(period: PerformancePeriod(label: "1 Month", changePercent: 8.42, vsMarketPercent: 4.1))
        PerformanceItem(period: PerformancePeriod(label: "YTD", changePercent: -3.15, vsMarketPercent: -7.5))
        PerformanceItem(period: PerformancePeriod(label: "1 Year", changePercent: 18.67, vsMarketPercent: -3.5))
        PerformanceItem(period: PerformancePeriod(label: "3 Years", changePercent: 42.89, vsMarketPercent: 12.3))
        PerformanceItem(period: PerformancePeriod(label: "5 Years", changePercent: 38.24))
        PerformanceItem(period: PerformancePeriod(label: "10 Years", changePercent: 287.45, vsMarketPercent: 95.2))
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
