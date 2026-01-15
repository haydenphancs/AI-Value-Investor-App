//
//  EarningsChartView.swift
//  ios
//
//  Molecule: Interactive chart displaying EPS/Revenue with estimates, actuals, and optional price overlay
//

import SwiftUI
import Charts

struct EarningsChartView: View {
    let quarters: [EarningsQuarterData]
    let priceHistory: [EarningsPricePoint]
    let showPriceLine: Bool

    // Calculate chart bounds
    private var allValues: [Double] {
        var values: [Double] = []
        for quarter in quarters {
            if let actual = quarter.actualValue {
                values.append(actual)
            }
            values.append(quarter.estimateValue)
        }
        if showPriceLine {
            values.append(contentsOf: priceHistory.map { $0.price })
        }
        return values
    }

    private var minValue: Double {
        (allValues.min() ?? 0) * 0.9
    }

    private var maxValue: Double {
        (allValues.max() ?? 1) * 1.1
    }

    private var chartHeight: CGFloat { 200 }

    var body: some View {
        VStack(spacing: 0) {
            GeometryReader { geometry in
                let width = geometry.size.width
                let height = geometry.size.height
                let quarterCount = quarters.count
                let stepX = width / CGFloat(quarterCount)
                let range = max(maxValue - minValue, 0.01)

                ZStack {
                    // Horizontal grid lines
                    gridLines(height: height, range: range)

                    // Y-axis labels
                    yAxisLabels(height: height, range: range)
                        .offset(x: -width/2 + 15)

                    // Price line (optional, rendered first so it's behind)
                    if showPriceLine && !priceHistory.isEmpty {
                        priceLine(width: width, height: height, stepX: stepX, range: range)
                    }

                    // Estimate dots (gray)
                    ForEach(Array(quarters.enumerated()), id: \.element.id) { index, quarter in
                        let x = CGFloat(index) * stepX + stepX / 2
                        let y = height - normalizedY(quarter.estimateValue, height: height, range: range)

                        Circle()
                            .fill(AppColors.textSecondary)
                            .frame(width: 14, height: 14)
                            .position(x: x, y: y)
                    }

                    // Actual result dots (colored based on result)
                    ForEach(Array(quarters.enumerated()), id: \.element.id) { index, quarter in
                        if let actual = quarter.actualValue {
                            let x = CGFloat(index) * stepX + stepX / 2
                            let y = height - normalizedY(actual, height: height, range: range)

                            // Dot with appropriate styling
                            ZStack {
                                Circle()
                                    .fill(quarter.result.dotColor)
                                    .frame(width: 14, height: 14)

                                // Dashed border for matched results
                                if quarter.result.hasDashedBorder {
                                    Circle()
                                        .stroke(
                                            AppColors.textPrimary,
                                            style: StrokeStyle(lineWidth: 2, dash: [3, 2])
                                        )
                                        .frame(width: 18, height: 18)
                                }
                            }
                            .position(x: x, y: y)
                        }
                    }
                }
            }
            .frame(height: chartHeight)
            .padding(.leading, 30) // Space for Y-axis labels

            // X-axis labels (quarters)
            xAxisLabels()
        }
    }

    // MARK: - Helper Views

    private func gridLines(height: CGFloat, range: Double) -> some View {
        VStack(spacing: 0) {
            ForEach(0..<4) { index in
                Rectangle()
                    .fill(AppColors.cardBackgroundLight.opacity(0.5))
                    .frame(height: 1)
                if index < 3 {
                    Spacer()
                }
            }
        }
    }

    private func yAxisLabels(height: CGFloat, range: Double) -> some View {
        VStack {
            Text(formatYValue(maxValue))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
            Spacer()
            Text(formatYValue((maxValue + minValue) / 2))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
            Spacer()
            Text(formatYValue(minValue))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(width: 30)
    }

    private func xAxisLabels() -> some View {
        HStack(spacing: 0) {
            ForEach(quarters) { quarter in
                Text(quarter.quarter)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity)
            }
        }
        .padding(.leading, 30)
        .padding(.top, AppSpacing.sm)
    }

    private func priceLine(width: CGFloat, height: CGFloat, stepX: CGFloat, range: Double) -> some View {
        Path { path in
            for (index, pricePoint) in priceHistory.enumerated() {
                guard pricePoint.price > 0 else { continue }
                let x = CGFloat(index) * stepX + stepX / 2
                let y = height - normalizedY(pricePoint.price, height: height, range: range)

                if index == 0 || (index > 0 && priceHistory[index - 1].price == 0) {
                    path.move(to: CGPoint(x: x, y: y))
                } else {
                    path.addLine(to: CGPoint(x: x, y: y))
                }
            }
        }
        .stroke(
            AppColors.accentCyan,
            style: StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round)
        )
    }

    // MARK: - Helper Functions

    private func normalizedY(_ value: Double, height: CGFloat, range: Double) -> CGFloat {
        let normalized = (value - minValue) / range
        return CGFloat(normalized) * height * 0.85 + height * 0.075
    }

    private func formatYValue(_ value: Double) -> String {
        if value >= 1000 {
            return String(format: "%.0f", value)
        } else if value >= 100 {
            return String(format: "%.0f", value)
        } else if value >= 10 {
            return String(format: "%.1f", value)
        } else {
            return String(format: "%.2f", value)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            EarningsChartView(
                quarters: EarningsData.sampleData.epsQuarters,
                priceHistory: EarningsData.sampleData.priceHistory,
                showPriceLine: true
            )
            .padding()
        }
    }
}
