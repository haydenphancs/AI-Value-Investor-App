//
//  SmartMoneyFlowChart.swift
//  ios
//
//  Molecule: Bar chart with line overlay for Smart Money flow
//  Shows buy volume (green bars), sell volume (red bars), and cumulative flow (blue line)
//  Uses native Swift Charts
//

import SwiftUI
import Charts

struct SmartMoneyFlowChart: View {
    let dataPoints: [SmartMoneyFlowDataPoint]

    // Chart configuration
    private let chartHeight: CGFloat = 200
    private let barWidth: CGFloat = 24

    // MARK: - Computed Properties

    private var maxVolume: Double {
        let maxBuy = dataPoints.map { $0.buyVolume }.max() ?? 1
        let maxSell = dataPoints.map { $0.sellVolume }.max() ?? 1
        return max(maxBuy, maxSell) * 1.15
    }

    private var flowRange: (min: Double, max: Double) {
        let flows = dataPoints.map { $0.cumulativeFlow }
        let minFlow = (flows.min() ?? 0)
        let maxFlow = (flows.max() ?? 1)
        // Add some padding
        let padding = (maxFlow - minFlow) * 0.2
        return (minFlow - padding, maxFlow + padding)
    }

    var body: some View {
        VStack(spacing: 0) {
            // Main chart
            Chart {
                // Buy volume bars (positive, green)
                ForEach(dataPoints) { point in
                    BarMark(
                        x: .value("Month", point.month),
                        y: .value("Buy", point.buyVolume),
                        width: .fixed(barWidth)
                    )
                    .foregroundStyle(HoldersColors.buyVolume)
                    .cornerRadius(3)
                    .position(by: .value("Type", "Buy"))
                }

                // Sell volume bars (negative direction visually, red)
                ForEach(dataPoints) { point in
                    BarMark(
                        x: .value("Month", point.month),
                        y: .value("Sell", -point.sellVolume),
                        width: .fixed(barWidth)
                    )
                    .foregroundStyle(HoldersColors.sellVolume)
                    .cornerRadius(3)
                    .position(by: .value("Type", "Sell"))
                }

                // Zero line
                RuleMark(y: .value("Zero", 0))
                    .foregroundStyle(AppColors.cardBackgroundLight)
                    .lineStyle(StrokeStyle(lineWidth: 0.5))

                // Cumulative flow line (overlaid, uses normalized values)
                ForEach(dataPoints) { point in
                    LineMark(
                        x: .value("Month", point.month),
                        y: .value("Flow", normalizedFlowValue(point.cumulativeFlow))
                    )
                    .foregroundStyle(HoldersColors.flowLine)
                    .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                    .interpolationMethod(.catmullRom)
                }

                // Flow line points
                ForEach(dataPoints) { point in
                    PointMark(
                        x: .value("Month", point.month),
                        y: .value("Flow", normalizedFlowValue(point.cumulativeFlow))
                    )
                    .foregroundStyle(HoldersColors.flowLine)
                    .symbolSize(30)
                }
            }
            .chartXAxis {
                AxisMarks(position: .bottom) { _ in
                    AxisValueLabel()
                        .font(AppTypography.caption)
                        .foregroundStyle(AppColors.textMuted)
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading) { value in
                    AxisGridLine()
                        .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.5))
                    AxisValueLabel {
                        if let doubleValue = value.as(Double.self) {
                            Text(formatYAxisValue(doubleValue))
                                .font(AppTypography.caption)
                                .foregroundStyle(AppColors.textMuted)
                        }
                    }
                }
            }
            .chartYScale(domain: -maxVolume...maxVolume)
            .chartPlotStyle { plotArea in
                plotArea
                    .background(Color.clear)
            }
            .frame(height: chartHeight)
        }
    }

    // MARK: - Helper Functions

    /// Normalize cumulative flow to fit within the bar chart's y-axis range
    private func normalizedFlowValue(_ flow: Double) -> Double {
        let range = flowRange.max - flowRange.min
        guard range > 0 else { return 0 }

        // Map flow to the upper half of the chart (0 to maxVolume)
        let normalized = (flow - flowRange.min) / range // 0 to 1
        let targetMin = maxVolume * 0.3
        let targetMax = maxVolume * 0.9
        return targetMin + normalized * (targetMax - targetMin)
    }

    private func formatYAxisValue(_ value: Double) -> String {
        let absValue = abs(value)
        if absValue >= 1000 {
            return String(format: "%.0fB", value / 1000)
        } else if absValue >= 1 {
            return String(format: "%.0f", value)
        } else if absValue > 0 {
            return String(format: "%.1f", value)
        }
        return "0"
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            Text("Insider Flow")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            SmartMoneyFlowChart(
                dataPoints: SmartMoneyFlowDataPoint.insiderSampleData
            )

            Divider()
                .background(AppColors.cardBackgroundLight)

            Text("Hedge Funds Flow")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            SmartMoneyFlowChart(
                dataPoints: SmartMoneyFlowDataPoint.hedgeFundsSampleData
            )
        }
        .padding()
    }
}
