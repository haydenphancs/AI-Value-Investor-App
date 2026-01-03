//
//  StockPerformanceCard.swift
//  ios
//
//  Molecule: Stock performance card with price, chart, and stats
//

import SwiftUI

struct StockPerformanceCard: View {
    let performance: StockPerformance

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Price display
            StockPriceDisplay(
                price: performance.formattedPrice,
                change: performance.formattedChange,
                period: performance.period,
                isPositive: performance.isPositive
            )

            // Chart
            MiniStockChart(
                data: performance.chartData,
                isPositive: performance.isPositive
            )

            // Chart date range
            HStack {
                Text("Dec 15")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)

                Spacer()

                Text("Jan 15")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }

            // Stats grid
            VStack(spacing: AppSpacing.md) {
                HStack {
                    StatItem(label: "Day High", value: performance.formattedDayHigh)
                    Spacer()
                    StatItem(label: "Day Low", value: performance.formattedDayLow)
                }

                HStack {
                    StatItem(label: "Volume", value: performance.volume)
                    Spacer()
                    StatItem(label: "Avg Volume", value: performance.avgVolume)
                }
            }

            // Follow-up question
            if let question = performance.followUpQuestion {
                Text(question)
                    .font(AppTypography.callout)
                    .foregroundColor(AppColors.textSecondary)
                    .italic()
            }
        }
        .padding(AppSpacing.lg)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.large)
    }
}

// MARK: - Stat Item
private struct StatItem: View {
    let label: String
    let value: String

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxs) {
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Text(value)
                .font(AppTypography.bodyBold)
                .foregroundColor(AppColors.textPrimary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

#Preview {
    StockPerformanceCard(
        performance: StockPerformance(
            currentPrice: 242.84,
            changePercent: 8.7,
            period: "1 Month",
            dayHigh: 245.12,
            dayLow: 238.45,
            volume: "124.5M",
            avgVolume: "98.2M",
            chartData: [220, 225, 218, 230, 235, 228, 240, 238, 245, 242],
            followUpQuestion: "Would you like me to analyze any specific timeframe or technical indicators?"
        )
    )
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
