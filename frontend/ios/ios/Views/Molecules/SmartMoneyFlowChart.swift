//
//  SmartMoneyFlowChart.swift
//  ios
//
//  Molecule: Two-chart view for Smart Money tracking
//  Top: Stock price line chart to show price movement
//  Bottom: Buy/Sell volume bar chart to show smart money activity
//  Users can compare when smart money bought/sold relative to price movements
//

import SwiftUI
import Charts

struct SmartMoneyFlowChart: View {
    let priceData: [StockPriceDataPoint]
    let flowData: [SmartMoneyFlowDataPoint]

    // Chart configuration
    private let priceChartHeight: CGFloat = 100
    private let volumeChartHeight: CGFloat = 140
    private let barWidth: CGFloat = 20

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            // Top: Stock Price Line Chart
            priceChart

            // Bottom: Buy/Sell Volume Bar Chart
            volumeChart
        }
    }

    // MARK: - Price Chart (Top)

    private var priceChart: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xs) {
            Chart {
                // Price line
                ForEach(priceData) { point in
                    LineMark(
                        x: .value("Month", point.month),
                        y: .value("Price", point.price)
                    )
                    .foregroundStyle(HoldersColors.flowLine)
                    .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                    .interpolationMethod(.catmullRom)
                }

                // Area under the line
                ForEach(priceData) { point in
                    AreaMark(
                        x: .value("Month", point.month),
                        y: .value("Price", point.price)
                    )
                    .foregroundStyle(
                        LinearGradient(
                            colors: [
                                HoldersColors.flowLine.opacity(0.3),
                                HoldersColors.flowLine.opacity(0.05)
                            ],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                    .interpolationMethod(.catmullRom)
                }
            }
            .chartXAxis(.hidden)
            .chartYAxis {
                AxisMarks(position: .trailing, values: .automatic(desiredCount: 3)) { value in
                    AxisGridLine()
                        .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.3))
                    AxisValueLabel {
                        if let doubleValue = value.as(Double.self) {
                            Text(formatPriceValue(doubleValue))
                                .font(AppTypography.caption)
                                .foregroundStyle(AppColors.textMuted)
                        }
                    }
                }
            }
            .chartYScale(domain: priceRange.min...priceRange.max)
            .chartPlotStyle { plotArea in
                plotArea.background(Color.clear)
            }
            .frame(height: priceChartHeight)
        }
    }

    // MARK: - Volume Chart (Bottom)

    private var volumeChart: some View {
        Chart {
            // Buy volume bars (positive, green)
            ForEach(flowData) { point in
                BarMark(
                    x: .value("Month", point.month),
                    y: .value("Buy", point.buyVolume),
                    width: .fixed(barWidth)
                )
                .foregroundStyle(HoldersColors.buyVolume)
                .cornerRadius(3)
                .position(by: .value("Type", "Buy"))
            }

            // Sell volume bars (negative direction, red)
            ForEach(flowData) { point in
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
        }
        .chartXAxis {
            AxisMarks(position: .bottom) { _ in
                AxisValueLabel()
                    .font(AppTypography.caption)
                    .foregroundStyle(AppColors.textMuted)
            }
        }
        .chartYAxis {
            AxisMarks(position: .trailing, values: .automatic(desiredCount: 4)) { value in
                AxisGridLine()
                    .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.3))
                AxisValueLabel {
                    if let doubleValue = value.as(Double.self) {
                        Text(formatVolumeValue(doubleValue))
                            .font(AppTypography.caption)
                            .foregroundStyle(AppColors.textMuted)
                    }
                }
            }
        }
        .chartYScale(domain: -maxVolume...maxVolume)
        .chartPlotStyle { plotArea in
            plotArea.background(Color.clear)
        }
        .frame(height: volumeChartHeight)
    }

    // MARK: - Computed Properties

    private var priceRange: (min: Double, max: Double) {
        let prices = priceData.map { $0.price }
        let minPrice = (prices.min() ?? 0)
        let maxPrice = (prices.max() ?? 1)
        // Add padding for visual breathing room
        let padding = (maxPrice - minPrice) * 0.15
        return (minPrice - padding, maxPrice + padding)
    }

    private var maxVolume: Double {
        let maxBuy = flowData.map { $0.buyVolume }.max() ?? 1
        let maxSell = flowData.map { $0.sellVolume }.max() ?? 1
        return max(maxBuy, maxSell) * 1.15
    }

    // MARK: - Formatting

    private func formatPriceValue(_ value: Double) -> String {
        if value >= 1000 {
            return String(format: "$%.0fK", value / 1000)
        }
        return String(format: "$%.0f", value)
    }

    private func formatVolumeValue(_ value: Double) -> String {
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
            Text("Smart Money vs Price")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)

            SmartMoneyFlowChart(
                priceData: StockPriceDataPoint.sampleData,
                flowData: SmartMoneyFlowDataPoint.insiderSampleData
            )
        }
        .padding()
    }
}
