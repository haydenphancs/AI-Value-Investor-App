//
//  RevenueBreakdownChartView.swift
//  ios
//
//  Molecule: Waterfall chart showing revenue sources and cost breakdown
//

import SwiftUI

struct RevenueBreakdownChartView: View {
    let data: RevenueBreakdownData

    private let chartHeight: CGFloat = 320
    private let leftAxisWidth: CGFloat = 50
    private let rightAxisWidth: CGFloat = 50

    // Grid line values
    private var gridValues: [Double] {
        let maxVal = data.totalRevenue
        return [0, maxVal * 0.25, maxVal * 0.5, maxVal * 0.75, maxVal]
    }

    // Percentage labels for right axis
    private var percentageLabels: [String] {
        ["0%", "25%", "50%", "75%", "100%"]
    }

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            // Left Y-axis (absolute values)
            leftYAxis
                .frame(width: leftAxisWidth)

            // Main chart
            chartContent
                .frame(height: chartHeight)

            // Right Y-axis (percentages)
            rightYAxis
                .frame(width: rightAxisWidth)
        }
    }

    // MARK: - Left Y-Axis

    private var leftYAxis: some View {
        VStack {
            ForEach(gridValues.reversed(), id: \.self) { value in
                Text(formatLargeNumber(value))
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                if value != gridValues.first {
                    Spacer()
                }
            }
        }
        .frame(height: chartHeight)
        .padding(.trailing, AppSpacing.xs)
    }

    // MARK: - Right Y-Axis

    private var rightYAxis: some View {
        VStack {
            ForEach(percentageLabels.reversed(), id: \.self) { label in
                Text(label)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                if label != percentageLabels.first {
                    Spacer()
                }
            }
        }
        .frame(height: chartHeight)
        .padding(.leading, AppSpacing.xs)
    }

    // MARK: - Chart Content

    private var chartContent: some View {
        GeometryReader { geometry in
            let width = geometry.size.width
            let height = geometry.size.height
            let barWidth: CGFloat = 55
            let totalRevenue = data.totalRevenue
            let scale = height / totalRevenue

            ZStack(alignment: .bottom) {
                // Grid lines
                gridLines(height: height)

                // Revenue stacked bar (left side)
                revenueStackedBar(height: height, barWidth: barWidth, scale: scale)
                    .position(x: width * 0.23, y: height / 2)

                // Cost waterfall bars (middle)
                costWaterfallBar(height: height, barWidth: barWidth, scale: scale)
                    .position(x: width * 0.53, y: height / 2)

                // Net profit bar (right side)
                netProfitBar(height: height, barWidth: barWidth, scale: scale)
                    .position(x: width * 0.83, y: height / 2)
            }
        }
    }

    // MARK: - Grid Lines

    private func gridLines(height: CGFloat) -> some View {
        VStack(spacing: 0) {
            ForEach(0..<5) { index in
                Rectangle()
                    .fill(AppColors.cardBackgroundLight.opacity(0.5))
                    .frame(height: 0.5)
                if index < 4 {
                    Spacer()
                }
            }
        }
        .frame(height: height)
    }

    // MARK: - Revenue Stacked Bar (with colors)

    private func revenueStackedBar(height: CGFloat, barWidth: CGFloat, scale: CGFloat) -> some View {
        let totalRevenue = data.totalRevenue

        // Calculate segment heights
        let segments: [(color: Color, height: CGFloat)] = data.revenueSources.map { source in
            (source.color, CGFloat(source.value / totalRevenue) * height)
        }

        return VStack(spacing: 0) {
            // Stack from top to bottom (iPhone at top, Other at bottom)
            ForEach(0..<segments.count, id: \.self) { index in
                Rectangle()
                    .fill(segments[index].color)
                    .frame(width: barWidth, height: segments[index].height)
            }
        }
        .clipShape(
            UnevenRoundedRectangle(
                topLeadingRadius: 6,
                bottomLeadingRadius: 0,
                bottomTrailingRadius: 0,
                topTrailingRadius: 6
            )
        )
        .frame(height: height, alignment: .bottom)
    }

    // MARK: - Cost Waterfall Bar

    private func costWaterfallBar(height: CGFloat, barWidth: CGFloat, scale: CGFloat) -> some View {
        let totalRevenue = data.totalRevenue

        // Calculate heights
        let costOfSalesHeight = CGFloat(data.costOfSales / totalRevenue) * height
        let opExpenseHeight = CGFloat(data.operatingExpense / totalRevenue) * height
        let taxHeight = CGFloat(data.tax / totalRevenue) * height

        // Positions from top
        _ = costOfSalesHeight / 2
        _ = costOfSalesHeight + opExpenseHeight / 2
        _ = costOfSalesHeight + opExpenseHeight + taxHeight / 2

        return ZStack(alignment: .top) {
            // Cost of Sales (starts at top)
            Rectangle()
                .fill(Color(hex: "EF4444"))
                .frame(width: barWidth, height: costOfSalesHeight)
                .clipShape(
                    UnevenRoundedRectangle(
                        topLeadingRadius: 6,
                        bottomLeadingRadius: 0,
                        bottomTrailingRadius: 0,
                        topTrailingRadius: 6
                    )
                )
                .offset(y: 0)

            // Operating Expense (below cost of sales)
            Rectangle()
                .fill(Color(hex: "F87171"))
                .frame(width: barWidth, height: opExpenseHeight)
                .offset(y: costOfSalesHeight)

            // Tax (below operating expense)
            Rectangle()
                .fill(Color(hex: "FCA5A5"))
                .frame(width: barWidth, height: taxHeight)
                .offset(y: costOfSalesHeight + opExpenseHeight)
        }
        .frame(height: height, alignment: .top)
    }

    // MARK: - Net Profit/Loss Bar

    private func netProfitBar(height: CGFloat, barWidth: CGFloat, scale: CGFloat) -> some View {
        let totalRevenue = data.totalRevenue
        let netProfit = data.netProfit
        let netProfitHeight = CGFloat(abs(netProfit) / totalRevenue) * height

        return ZStack(alignment: .bottom) {
            if data.isProfit {
                // Profit - green bar from bottom
                VStack(spacing: 2) {
                    Text("Net Profit")
                        .font(.system(size: 10))
                        .foregroundColor(AppColors.textMuted)

                    Rectangle()
                        .fill(AppColors.bullish)
                        .frame(width: barWidth, height: netProfitHeight)
                        .clipShape(
                            UnevenRoundedRectangle(
                                topLeadingRadius: 6,
                                bottomLeadingRadius: 0,
                                bottomTrailingRadius: 0,
                                topTrailingRadius: 6
                            )
                        )
                }
            } else {
                // Loss - dark red bar below baseline
                VStack(spacing: 2) {
                    Rectangle()
                        .fill(Color(hex: "8B0000"))
                        .frame(width: barWidth, height: min(netProfitHeight, height * 0.3))
                        .clipShape(RoundedRectangle(cornerRadius: 4))

                    Text("-Net Loss")
                        .font(.system(size: 10))
                        .foregroundColor(AppColors.textMuted)
                }
            }
        }
        .frame(height: height, alignment: .bottom)
    }

    // MARK: - Helper Functions

    private func formatLargeNumber(_ number: Double) -> String {
        let absNumber = abs(number)
        if absNumber >= 1_000_000_000_000 {
            return String(format: "%.0fT", number / 1_000_000_000_000)
        } else if absNumber >= 1_000_000_000 {
            return String(format: "%.0fB", number / 1_000_000_000)
        } else if absNumber >= 1_000_000 {
            return String(format: "%.0fM", number / 1_000_000)
        } else if absNumber >= 1_000 {
            return String(format: "%.0fK", number / 1_000)
        }
        return String(format: "%.0f", number)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        VStack {
            RevenueBreakdownChartView(data: RevenueBreakdownData.sampleApple)
                .padding()

            Divider()

            RevenueBreakdownChartView(data: RevenueBreakdownData.sampleLossCompany)
                .padding()
        }
    }
}
