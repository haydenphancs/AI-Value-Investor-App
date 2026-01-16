//
//  SignalOfConfidenceChartView.swift
//  ios
//
//  Molecule: Combined bar and line chart for Signal of Confidence using Swift Charts
//  Displays dividends (bars), buybacks (bars), and shares outstanding (line)
//

import SwiftUI
import Charts

struct SignalOfConfidenceChartView: View {
    let dataPoints: [SignalOfConfidenceDataPoint]
    let viewType: SignalOfConfidenceViewType

    // Chart configuration
    private let chartHeight: CGFloat = 280
    private let yAxisWidth: CGFloat = 40
    private let rightYAxisWidth: CGFloat = 45

    // MARK: - Computed Properties

    private var maxBarValue: Double {
        switch viewType {
        case .yield:
            let maxDividend = dataPoints.map { $0.dividendYield }.max() ?? 1
            let maxBuyback = dataPoints.map { $0.buybackYield }.max() ?? 1
            return max(maxDividend, maxBuyback) * 1.15
        case .capital:
            let maxDividend = dataPoints.map { $0.dividendAmount }.max() ?? 1
            let maxBuyback = dataPoints.map { $0.buybackAmount }.max() ?? 1
            return max(maxDividend, maxBuyback) * 1.15
        }
    }

    private var sharesRange: (min: Double, max: Double) {
        let shares = dataPoints.map { $0.sharesOutstanding }
        let minShares = (shares.min() ?? 0) * 0.9
        let maxShares = (shares.max() ?? 1) * 1.1
        return (minShares, maxShares)
    }

    // Grid line values (4 horizontal lines)
    private var gridValues: [Double] {
        let step = maxBarValue / 4
        return [step, step * 2, step * 3]
    }

    var body: some View {
        VStack(spacing: 0) {
            // Chart with dual Y-axes
            HStack(alignment: .top, spacing: 0) {
                // Left Y-axis labels (yield % or capital $)
                leftYAxisLabels
                    .frame(width: yAxisWidth)

                // Main chart area
                chartContent
                    .frame(height: chartHeight)

                // Right Y-axis labels (shares outstanding)
                rightYAxisLabels
                    .frame(width: rightYAxisWidth)
            }

            // X-axis labels (periods)
            xAxisLabels
        }
    }

    // MARK: - Chart Content

    private var chartContent: some View {
        Chart {
            // Horizontal grid lines
            ForEach(gridValues, id: \.self) { value in
                RuleMark(y: .value("Grid", value))
                    .foregroundStyle(AppColors.cardBackgroundLight.opacity(0.5))
                    .lineStyle(StrokeStyle(lineWidth: 0.5))
            }

            // Dividend bars
            ForEach(dataPoints) { dataPoint in
                BarMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Dividends", viewType == .yield ? dataPoint.dividendYield : dataPoint.dividendAmount),
                    width: .fixed(18)
                )
                .foregroundStyle(AppColors.confidenceDividends)
                .cornerRadius(3)
                .position(by: .value("Type", "Dividends"))
            }

            // Buyback bars
            ForEach(dataPoints) { dataPoint in
                BarMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Buybacks", viewType == .yield ? dataPoint.buybackYield : dataPoint.buybackAmount),
                    width: .fixed(18)
                )
                .foregroundStyle(AppColors.confidenceBuybacks)
                .cornerRadius(3)
                .position(by: .value("Type", "Buybacks"))
            }

            // Shares outstanding line
            ForEach(dataPoints) { dataPoint in
                LineMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Shares", normalizeShares(dataPoint.sharesOutstanding))
                )
                .foregroundStyle(AppColors.confidenceSharesOutstanding)
                .lineStyle(StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.catmullRom)
            }

            // Shares outstanding points
            ForEach(dataPoints) { dataPoint in
                PointMark(
                    x: .value("Period", dataPoint.period),
                    y: .value("Shares", normalizeShares(dataPoint.sharesOutstanding))
                )
                .foregroundStyle(AppColors.confidenceSharesOutstanding)
                .symbolSize(50)
            }
        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartYScale(domain: 0...maxBarValue)
        .chartPlotStyle { plotArea in
            plotArea
                .background(Color.clear)
        }
    }

    // MARK: - Y-Axis Labels

    private var leftYAxisLabels: some View {
        VStack {
            Text(formatLeftAxisValue(maxBarValue * 0.9))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(formatLeftAxisValue(maxBarValue * 0.6))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(formatLeftAxisValue(maxBarValue * 0.3))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(viewType == .yield ? "0%" : "$0")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.xs)
    }

    private var rightYAxisLabels: some View {
        VStack {
            Text(formatSharesValue(sharesRange.max))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            let midValue = (sharesRange.max + sharesRange.min) / 2
            Text(formatSharesValue(midValue))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            Spacer()

            Text(formatSharesValue(sharesRange.min))
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
        }
        .frame(height: chartHeight)
        .padding(.leading, AppSpacing.xs)
    }

    // MARK: - X-Axis Labels

    private var xAxisLabels: some View {
        HStack(spacing: 0) {
            Spacer()
                .frame(width: yAxisWidth)

            ForEach(dataPoints) { dataPoint in
                Text(dataPoint.period)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .frame(maxWidth: .infinity)
                    .lineLimit(1)
            }

            Spacer()
                .frame(width: rightYAxisWidth)
        }
        .padding(.top, AppSpacing.sm)
    }

    // MARK: - Helper Functions

    /// Normalize shares outstanding to fit within the bar chart's value range
    private func normalizeShares(_ shares: Double) -> Double {
        let range = sharesRange.max - sharesRange.min
        guard range > 0 else { return maxBarValue * 0.5 }

        let normalizedShares = (shares - sharesRange.min) / range // 0 to 1
        let targetMin = maxBarValue * 0.15
        let targetMax = maxBarValue * 0.85
        return targetMin + normalizedShares * (targetMax - targetMin)
    }

    private func formatLeftAxisValue(_ value: Double) -> String {
        switch viewType {
        case .yield:
            return String(format: "%.1f%%", value)
        case .capital:
            return formatLargeNumber(value)
        }
    }

    private func formatSharesValue(_ value: Double) -> String {
        if value >= 1000 {
            return String(format: "%.0fB", value / 1000)
        } else {
            return String(format: "%.0fM", value)
        }
    }

    private func formatLargeNumber(_ number: Double) -> String {
        let absNumber = abs(number)
        if absNumber >= 1_000_000 {
            return String(format: "$%.0fT", number / 1_000_000)
        } else if absNumber >= 1_000 {
            return String(format: "$%.0fB", number / 1_000)
        } else {
            return String(format: "$%.0fM", number)
        }
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack(spacing: AppSpacing.xl) {
            Text("Yield View")
                .foregroundColor(.white)
            SignalOfConfidenceChartView(
                dataPoints: SignalOfConfidenceSectionData.sampleData.dataPoints,
                viewType: .yield
            )

            Divider()

            Text("Capital View")
                .foregroundColor(.white)
            SignalOfConfidenceChartView(
                dataPoints: SignalOfConfidenceSectionData.sampleData.dataPoints,
                viewType: .capital
            )
        }
        .padding()
    }
}
