//
//  ProfitPowerChart.swift
//  ios
//
//  Molecule: Multi-line chart showing profit margins over time
//

import SwiftUI
import Charts

struct ProfitPowerChart: View {
    let data: [MarginDataPoint]
    let showSectorAverage: Bool

    private let chartHeight: CGFloat = 200

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            // Multi-line chart
            Chart {
                // Gross Margin line
                ForEach(Array(data.enumerated()), id: \.element.id) { index, point in
                    LineMark(
                        x: .value("Period", point.period),
                        y: .value("Gross", point.grossMargin),
                        series: .value("Type", "Gross")
                    )
                    .foregroundStyle(MarginType.grossMargin.color)
                    .lineStyle(StrokeStyle(lineWidth: 2))
                }

                // Operating Margin line
                ForEach(Array(data.enumerated()), id: \.element.id) { index, point in
                    LineMark(
                        x: .value("Period", point.period),
                        y: .value("Operating", point.operatingMargin),
                        series: .value("Type", "Operating")
                    )
                    .foregroundStyle(MarginType.operatingMargin.color)
                    .lineStyle(StrokeStyle(lineWidth: 2))
                }

                // FCF Margin line
                ForEach(Array(data.enumerated()), id: \.element.id) { index, point in
                    LineMark(
                        x: .value("Period", point.period),
                        y: .value("FCF", point.fcfMargin),
                        series: .value("Type", "FCF")
                    )
                    .foregroundStyle(MarginType.fcfMargin.color)
                    .lineStyle(StrokeStyle(lineWidth: 2))
                }

                // Net Margin line
                ForEach(Array(data.enumerated()), id: \.element.id) { index, point in
                    LineMark(
                        x: .value("Period", point.period),
                        y: .value("Net", point.netMargin),
                        series: .value("Type", "Net")
                    )
                    .foregroundStyle(MarginType.netMargin.color)
                    .lineStyle(StrokeStyle(lineWidth: 2))
                }

                // Sector Average line (dashed)
                if showSectorAverage {
                    ForEach(Array(data.enumerated()), id: \.element.id) { index, point in
                        if let sectorAvg = point.sectorAverageNetMargin {
                            LineMark(
                                x: .value("Period", point.period),
                                y: .value("Sector", sectorAvg),
                                series: .value("Type", "Sector")
                            )
                            .foregroundStyle(AppColors.textMuted)
                            .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [5, 3]))
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
                    AxisValueLabel {
                        if let intValue = value.as(Double.self) {
                            Text("\(Int(intValue))%")
                                .foregroundStyle(AppColors.textMuted)
                                .font(AppTypography.caption)
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

            // Legend
            VStack(spacing: AppSpacing.sm) {
                HStack(spacing: AppSpacing.lg) {
                    FinancialChartLegendItem(
                        color: MarginType.grossMargin.color,
                        label: "Gross Margin",
                        style: .line
                    )
                    FinancialChartLegendItem(
                        color: MarginType.operatingMargin.color,
                        label: "Operating Margin",
                        style: .line
                    )
                    FinancialChartLegendItem(
                        color: MarginType.fcfMargin.color,
                        label: "FCF Margin",
                        style: .line
                    )
                }

                HStack(spacing: AppSpacing.lg) {
                    FinancialChartLegendItem(
                        color: MarginType.netMargin.color,
                        label: "Net Margin",
                        style: .line
                    )
                    if showSectorAverage {
                        HStack(spacing: AppSpacing.xs) {
                            RoundedRectangle(cornerRadius: 1)
                                .stroke(AppColors.textMuted, style: StrokeStyle(lineWidth: 1.5, dash: [3, 2]))
                                .frame(width: 12, height: 3)
                            Text("Sector Average Net Margin")
                                .font(AppTypography.caption)
                                .foregroundColor(AppColors.textSecondary)
                        }
                    }
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

        VStack {
            ProfitPowerChart(
                data: MarginDataPoint.sampleData,
                showSectorAverage: true
            )
            .padding()
        }
    }
}
