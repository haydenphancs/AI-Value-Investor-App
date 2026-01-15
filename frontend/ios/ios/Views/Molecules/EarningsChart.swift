//
//  EarningsChart.swift
//  ios
//
//  Molecule: Earnings bar chart showing actual vs estimate with price line overlay
//

import SwiftUI
import Charts

struct EarningsChart: View {
    let data: [EarningsQuarter]
    let showPrice: Bool
    let metricType: EarningsMetricType

    private let chartHeight: CGFloat = 200

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // Chart
            Chart {
                ForEach(data) { quarter in
                    // Estimate bar (background)
                    BarMark(
                        x: .value("Quarter", quarter.quarter),
                        y: .value("Estimate", quarter.estimate)
                    )
                    .foregroundStyle(AppColors.textMuted.opacity(0.3))
                    .cornerRadius(4)

                    // Actual bar (foreground, narrower)
                    BarMark(
                        x: .value("Quarter", quarter.quarter),
                        y: .value("Actual", quarter.actual),
                        width: .fixed(20)
                    )
                    .foregroundStyle(quarter.resultType.color)
                    .cornerRadius(4)

                    // Price line (if enabled)
                    if showPrice, let price = quarter.stockPrice {
                        LineMark(
                            x: .value("Quarter", quarter.quarter),
                            y: .value("Price", normalizedPrice(price))
                        )
                        .foregroundStyle(AppColors.primaryBlue)
                        .lineStyle(StrokeStyle(lineWidth: 2))
                        .symbol {
                            Circle()
                                .fill(AppColors.primaryBlue)
                                .frame(width: 8, height: 8)
                        }
                    }
                }
            }
            .chartXAxis {
                AxisMarks(values: .automatic) { value in
                    AxisValueLabel()
                        .foregroundStyle(AppColors.textMuted)
                        .font(AppTypography.caption)
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading) { value in
                    AxisGridLine()
                        .foregroundStyle(AppColors.cardBackgroundLight)
                    AxisValueLabel()
                        .foregroundStyle(AppColors.textMuted)
                        .font(AppTypography.caption)
                }
            }
            .chartPlotStyle { plotArea in
                plotArea
                    .background(Color.clear)
            }
            .frame(height: chartHeight)

            // Surprise percentages row
            HStack(spacing: 0) {
                ForEach(data) { quarter in
                    Text(quarter.formattedSurprise)
                        .font(AppTypography.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(quarter.surpriseColor)
                        .frame(maxWidth: .infinity)
                }
            }

            // Legend
            HStack(spacing: AppSpacing.lg) {
                FinancialChartLegendItem(color: AppColors.bullish, label: "Surprised", style: .dot)
                FinancialChartLegendItem(color: AppColors.textMuted.opacity(0.3), label: "Estimate", style: .dot)
                FinancialChartLegendItem(color: AppColors.bullish, label: "Beat", style: .dot)
                FinancialChartLegendItem(color: AppColors.bearish, label: "Missed", style: .dot)
            }
        }
    }

    // Normalize price to fit on the same scale as EPS
    private func normalizedPrice(_ price: Double) -> Double {
        let maxActual = data.compactMap { $0.actual }.max() ?? 1
        let maxPrice = data.compactMap { $0.stockPrice }.max() ?? 1
        return (price / maxPrice) * maxActual * 1.2
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            EarningsChart(
                data: EarningsQuarter.sampleData,
                showPrice: true,
                metricType: .eps
            )
            .padding()
        }
    }
}
