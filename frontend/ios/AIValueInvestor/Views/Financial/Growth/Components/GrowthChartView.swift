import SwiftUI
import Charts

/// Combined bar and line chart displaying growth data
/// Shows value bars, YoY line, and sector average line
struct GrowthChartView: View {

    // MARK: - Properties

    /// Data points to display
    let dataPoints: [GrowthDataPoint]

    /// Metric type for formatting
    let metricType: GrowthMetricType

    /// Maximum value for Y-axis scaling
    let maxValue: Double

    // MARK: - Private Properties

    /// Computed Y-axis domain with padding
    private var yAxisDomain: ClosedRange<Double> {
        0...roundedMaxValue
    }

    /// Rounded max value for cleaner axis labels
    private var roundedMaxValue: Double {
        let padding = maxValue * 0.15
        let total = maxValue + padding

        // Round to nearest 50 for cleaner axis
        let roundTo: Double = metricType.showsInBillions ? 50 : 1
        return ceil(total / roundTo) * roundTo
    }

    /// Y-axis stride for labels
    private var yAxisStride: Double {
        roundedMaxValue / 5
    }

    // MARK: - Body

    var body: some View {
        Chart {
            // Bar marks for values
            ForEach(dataPoints) { point in
                BarMark(
                    x: .value("Period", point.period),
                    y: .value("Value", point.value)
                )
                .foregroundStyle(AppColors.chartValue)
                .cornerRadius(4)
            }

            // Line mark for YoY percentage (scaled to value range)
            ForEach(dataPoints) { point in
                LineMark(
                    x: .value("Period", point.period),
                    y: .value("YoY", scaleYoYToValue(point.yoyPercentage))
                )
                .foregroundStyle(AppColors.chartYoY)
                .lineStyle(StrokeStyle(lineWidth: 2))
                .symbol {
                    Circle()
                        .fill(AppColors.chartYoY)
                        .frame(width: 8, height: 8)
                }
            }

            // Line mark for sector average (dashed)
            ForEach(dataPoints) { point in
                LineMark(
                    x: .value("Period", point.period),
                    y: .value("Sector", scaleYoYToValue(point.sectorAverageYoY))
                )
                .foregroundStyle(AppColors.chartSectorAverage)
                .lineStyle(StrokeStyle(lineWidth: 2, dash: [5, 5]))
                .symbol {
                    Circle()
                        .fill(AppColors.chartSectorAverage)
                        .frame(width: 6, height: 6)
                }
            }
        }
        .chartXAxis {
            AxisMarks(position: .bottom) { _ in
                AxisValueLabel()
                    .font(AppFonts.chartAxis)
                    .foregroundStyle(AppColors.textSecondary)
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .stride(by: yAxisStride)) { value in
                AxisValueLabel {
                    if let doubleValue = value.as(Double.self) {
                        Text(formatYAxisValue(doubleValue))
                            .font(AppFonts.chartAxis)
                            .foregroundStyle(AppColors.textTertiary)
                    }
                }
                AxisGridLine()
                    .foregroundStyle(AppColors.divider.opacity(0.3))
            }
        }
        .chartYScale(domain: yAxisDomain)
        .frame(height: 220)
    }

    // MARK: - Private Methods

    /// Scales YoY percentage to fit within the value chart range
    /// Maps percentage (-100 to 100) to value range for visual overlay
    private func scaleYoYToValue(_ percentage: Double) -> Double {
        // Map percentage to approximately 20-80% of the chart height
        let baseHeight = roundedMaxValue * 0.5
        let scaleFactor = roundedMaxValue * 0.003
        return baseHeight + (percentage * scaleFactor)
    }

    /// Formats Y-axis values with appropriate suffix
    private func formatYAxisValue(_ value: Double) -> String {
        if metricType.showsInBillions {
            if value >= 1 {
                return "\(Int(value))B"
            } else {
                return "0"
            }
        } else {
            return String(format: "%.1f", value)
        }
    }
}

// MARK: - Preview

#Preview {
    GrowthChartView(
        dataPoints: GrowthMetricData.sampleRevenue.dataPoints,
        metricType: .revenue,
        maxValue: GrowthMetricData.sampleRevenue.maxValue
    )
    .padding()
    .background(AppColors.backgroundCard)
}
