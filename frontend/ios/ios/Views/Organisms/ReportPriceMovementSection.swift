//
//  ReportPriceMovementSection.swift
//  ios
//
//  Organism: Recent Price Movement deep dive content.
//  Gradient area chart with timeframe pill tabs (1D/1W/1M),
//  stats strip with price delta, and period high/low indicators.
//

import SwiftUI

struct ReportPriceMovementSection: View {
    let data: ReportPriceMovementData
    @Binding var selectedTimeframe: PriceTimeframe

    private var currentStats: PriceMovementStats? {
        data.stats[selectedTimeframe]
    }

    private var currentPoints: [PricePoint] {
        data.points[selectedTimeframe] ?? []
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Timeframe tabs
            timeframePills

            if let stats = currentStats {
                // Price + Change header
                priceHeader(stats)

                // Chart
                ReportPriceChart(points: currentPoints, stats: stats)

                // Stats strip
                statsStrip(stats)
            }
        }
    }

    // MARK: - Timeframe Pills

    private var timeframePills: some View {
        HStack(spacing: AppSpacing.sm) {
            ForEach(PriceTimeframe.allCases) { timeframe in
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) {
                        selectedTimeframe = timeframe
                    }
                } label: {
                    Text(timeframe.rawValue)
                        .font(AppTypography.captionBold)
                        .foregroundColor(
                            selectedTimeframe == timeframe
                                ? AppColors.textPrimary
                                : AppColors.textMuted
                        )
                        .padding(.horizontal, AppSpacing.md)
                        .padding(.vertical, AppSpacing.sm)
                        .background(
                            RoundedRectangle(cornerRadius: AppCornerRadius.small)
                                .fill(
                                    selectedTimeframe == timeframe
                                        ? AppColors.cardBackgroundLight
                                        : Color.clear
                                )
                        )
                }
                .buttonStyle(PlainButtonStyle())
            }

            Spacer()
        }
    }

    // MARK: - Price Header

    private func priceHeader(_ stats: PriceMovementStats) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: AppSpacing.md) {
            Text(stats.formattedPrice)
                .font(.system(size: 28, weight: .bold, design: .rounded))
                .foregroundColor(AppColors.textPrimary)

            HStack(spacing: AppSpacing.xs) {
                Image(systemName: stats.isPositive ? "arrow.up.right" : "arrow.down.right")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundColor(stats.trendColor)

                Text(stats.formattedChange)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(stats.trendColor)

                Text("(\(stats.formattedPercent))")
                    .font(AppTypography.callout)
                    .foregroundColor(stats.trendColor)
            }
        }
    }

    // MARK: - Stats Strip

    private func statsStrip(_ stats: PriceMovementStats) -> some View {
        HStack(spacing: 0) {
            statItem(label: "High", value: String(format: "$%.2f", stats.periodHigh), color: AppColors.bullish)
            Spacer()
            divider
            Spacer()
            statItem(label: "Low", value: String(format: "$%.2f", stats.periodLow), color: AppColors.bearish)
            Spacer()
            divider
            Spacer()
            statItem(label: "Avg Vol", value: stats.avgVolume, color: AppColors.textSecondary)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
        )
    }

    private func statItem(label: String, value: String, color: Color) -> some View {
        VStack(spacing: AppSpacing.xxs) {
            Text(label)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
            Text(value)
                .font(AppTypography.footnoteBold)
                .foregroundColor(color)
        }
    }

    private var divider: some View {
        Rectangle()
            .fill(AppColors.textMuted.opacity(0.2))
            .frame(width: 1, height: 28)
    }
}

#Preview {
    ReportPriceMovementSection(
        data: TickerReportData.sampleOracle.priceMovement,
        selectedTimeframe: .constant(.oneWeek)
    )
    .padding()
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
