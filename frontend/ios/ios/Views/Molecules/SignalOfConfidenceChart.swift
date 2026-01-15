//
//  SignalOfConfidenceChart.swift
//  ios
//
//  Molecule: Chart showing dividend yields, buybacks, and shares outstanding
//

import SwiftUI
import Charts

struct SignalOfConfidenceChart: View {
    let data: SignalOfConfidenceData
    let viewType: SignalViewType

    private let chartHeight: CGFloat = 180

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // Stacked bar chart
            Chart {
                ForEach(data.quarterData) { quarter in
                    if viewType == .yield {
                        // Dividend yield bar
                        BarMark(
                            x: .value("Quarter", quarter.quarter),
                            y: .value("Dividend", quarter.dividendYield),
                            stacking: .standard
                        )
                        .foregroundStyle(Color(hex: "3B82F6"))
                        .cornerRadius(2, style: .continuous)

                        // Buyback yield bar (stacked)
                        BarMark(
                            x: .value("Quarter", quarter.quarter),
                            y: .value("Buyback", quarter.buybackYield),
                            stacking: .standard
                        )
                        .foregroundStyle(Color(hex: "22C55E"))
                        .cornerRadius(2, style: .continuous)
                    } else {
                        // Capital amounts
                        BarMark(
                            x: .value("Quarter", quarter.quarter),
                            y: .value("Dividend", quarter.dividendAmount),
                            stacking: .standard
                        )
                        .foregroundStyle(Color(hex: "3B82F6"))
                        .cornerRadius(2, style: .continuous)

                        BarMark(
                            x: .value("Quarter", quarter.quarter),
                            y: .value("Buyback", quarter.buybackAmount),
                            stacking: .standard
                        )
                        .foregroundStyle(Color(hex: "22C55E"))
                        .cornerRadius(2, style: .continuous)
                    }
                }

                // Shares outstanding line (secondary axis simulated by scaling)
                ForEach(data.quarterData) { quarter in
                    let scaledValue = viewType == .yield
                        ? quarter.sharesOutstanding / 10 // Scale to fit yield axis
                        : quarter.sharesOutstanding * 1.5 // Scale to fit capital axis

                    LineMark(
                        x: .value("Quarter", quarter.quarter),
                        y: .value("Shares", scaledValue)
                    )
                    .foregroundStyle(Color(hex: "F59E0B"))
                    .lineStyle(StrokeStyle(lineWidth: 2))
                    .symbol {
                        Circle()
                            .fill(Color(hex: "F59E0B"))
                            .frame(width: 6, height: 6)
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
                    AxisValueLabel {
                        if let doubleValue = value.as(Double.self) {
                            if viewType == .yield {
                                Text(String(format: "%.1f%%", doubleValue))
                                    .foregroundStyle(AppColors.textMuted)
                                    .font(AppTypography.caption)
                            } else {
                                Text(String(format: "$%.0fB", doubleValue))
                                    .foregroundStyle(AppColors.textMuted)
                                    .font(AppTypography.caption)
                            }
                        }
                    }
                }
            }
            .chartLegend(.hidden)
            .chartPlotStyle { plotArea in
                plotArea
                    .background(Color.clear)
            }
            .frame(height: chartHeight)

            // Right Y-axis label (Shares Outstanding)
            HStack {
                Spacer()
                Text("Shares (B)")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
            }
            .padding(.top, -AppSpacing.sm)

            // Legend
            HStack(spacing: AppSpacing.lg) {
                FinancialChartLegendItem(
                    color: Color(hex: "3B82F6"),
                    label: "Dividends",
                    style: .square
                )
                FinancialChartLegendItem(
                    color: Color(hex: "22C55E"),
                    label: "Buybacks",
                    style: .square
                )
                FinancialChartLegendItem(
                    color: Color(hex: "F59E0B"),
                    label: "Shares Outstanding",
                    style: .line
                )
            }

            // Summary text
            VStack(alignment: .leading, spacing: AppSpacing.xs) {
                HStack {
                    Text("Total Yield:")
                        .font(AppTypography.subheadline)
                        .foregroundColor(AppColors.textSecondary)
                    Text(data.formattedTotalYield)
                        .font(AppTypography.subheadline)
                        .fontWeight(.semibold)
                        .foregroundColor(AppColors.bullish)
                    Text("(\(data.yieldBreakdown))")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                HStack {
                    Text("Share count change:")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text(data.formattedShareChange)
                        .font(AppTypography.caption)
                        .fontWeight(.semibold)
                        .foregroundColor(data.shareChangeColor)
                }
            }
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: 32) {
            SignalOfConfidenceChart(
                data: SignalOfConfidenceData.sampleData,
                viewType: .yield
            )

            SignalOfConfidenceChart(
                data: SignalOfConfidenceData.sampleData,
                viewType: .capital
            )
        }
        .padding()
    }
}
