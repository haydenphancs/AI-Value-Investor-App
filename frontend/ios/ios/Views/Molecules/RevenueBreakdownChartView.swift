//
//  RevenueBreakdownChartView.swift
//  ios
//
//  Molecule: Waterfall chart showing revenue sources and cost breakdown
//  Costs descend from top of revenue; loss companies show negative values
//

import SwiftUI

struct RevenueBreakdownChartView: View {
    let data: RevenueBreakdownData

    private let chartHeight: CGFloat = 320
    private let leftAxisWidth: CGFloat = 50
    private let rightAxisWidth: CGFloat = 50

    // Calculate chart bounds
    private var chartTopValue: Double {
        // Top is always total revenue (or slightly above)
        data.totalRevenue * 1.1
    }

    private var chartBottomValue: Double {
        if data.isProfit {
            // Profitable: bottom is 0
            0
        } else {
            // Loss: bottom extends to show net loss (negative)
            data.netProfit * 1.2 // netProfit is negative, so this goes below 0
        }
    }

    private var chartRange: Double {
        chartTopValue - chartBottomValue
    }

    // Where is the zero line (as fraction from bottom)
    private var zeroLinePosition: CGFloat {
        if data.isProfit {
            return 0 // Zero is at bottom
        } else {
            return CGFloat(abs(chartBottomValue) / chartRange)
        }
    }

    // Grid values for Y-axis
    private var gridValues: [Double] {
        if data.isProfit {
            let maxVal = chartTopValue
            return [0, maxVal * 0.25, maxVal * 0.5, maxVal * 0.75, maxVal]
        } else {
            // Include negative values
            let step = chartRange / 4
            return [
                chartBottomValue,
                chartBottomValue + step,
                chartBottomValue + step * 2,
                chartBottomValue + step * 3,
                chartTopValue
            ]
        }
    }

    // Percentage labels
    private var percentageLabels: [String] {
        if data.isProfit {
            return ["0%", "25%", "50%", "75%", "100%"]
        } else {
            // Calculate percentage relative to revenue
            let bottomPercent = Int((chartBottomValue / data.totalRevenue) * 100)
            return [
                "\(bottomPercent)%",
                "\(bottomPercent / 4 * 3)%",
                "\(bottomPercent / 2)%",
                "\(bottomPercent / 4)%",
                "100%"
            ]
        }
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
            let barWidth: CGFloat = 70

            ZStack(alignment: .topLeading) {
                // Grid lines
                gridLines(height: height)

                // Zero line for loss companies
                if !data.isProfit {
                    zeroLine(height: height)
                }

                // Revenue stacked bar (left side) - grows UP from zero/bottom
                revenueStackedBar(height: height, barWidth: barWidth)
                    .position(x: width * 0.25, y: height / 2)

                // Cost waterfall bar (center) - descends FROM TOP
                costWaterfallBar(height: height, barWidth: barWidth)
                    .position(x: width * 0.55, y: height / 2)

                // Net profit/loss bar (right side)
                netProfitBar(height: height, barWidth: barWidth)
                    .position(x: width * 0.82, y: height / 2)
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

    // MARK: - Zero Line (for loss companies)

    private func zeroLine(height: CGFloat) -> some View {
        let zeroY = height * (1 - zeroLinePosition)

        return Rectangle()
            .fill(AppColors.textMuted.opacity(0.8))
            .frame(height: 1)
            .offset(y: zeroY)
    }

    // MARK: - Revenue Stacked Bar

    private func revenueStackedBar(height: CGFloat, barWidth: CGFloat) -> some View {
        let revenueBarHeight = CGFloat(data.totalRevenue / chartRange) * height
        let zeroOffset = zeroLinePosition * height // Distance from bottom to zero line

        // Calculate segment heights proportionally within the revenue bar
        let segments: [(color: Color, height: CGFloat)] = data.revenueSources.map { source in
            (source.color, CGFloat(source.value / data.totalRevenue) * revenueBarHeight)
        }

        return VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 0) {
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

            // Offset for zero line position
            if !data.isProfit {
                Spacer().frame(height: zeroOffset)
            }
        }
        .frame(height: height)
    }

    // MARK: - Cost Waterfall Bar (descends from top)

    private func costWaterfallBar(height: CGFloat, barWidth: CGFloat) -> some View {
        let pixelsPerUnit = height / chartRange

        // Calculate heights
        let costOfSalesHeight = CGFloat(data.costOfSales) * pixelsPerUnit
        let opExpenseHeight = CGFloat(data.operatingExpense) * pixelsPerUnit
        let taxHeight = CGFloat(data.tax) * pixelsPerUnit

        // Top of cost bar aligns with top of revenue bar
        let revenueTopY = CGFloat(chartTopValue - data.totalRevenue) * pixelsPerUnit

        return VStack(spacing: 0) {
            // Space above the cost bar (aligns with revenue top)
            Spacer().frame(height: revenueTopY)

            // Cost segments descending from revenue top
            VStack(spacing: 0) {
                // Cost of Sales (starts at top, where revenue ends)
                Rectangle()
                    .fill(Color(hex: "EF4444"))
                    .frame(width: barWidth, height: costOfSalesHeight)

                // Operating Expense
                Rectangle()
                    .fill(Color(hex: "F87171"))
                    .frame(width: barWidth, height: opExpenseHeight)

                // Tax
                Rectangle()
                    .fill(Color(hex: "FCA5A5"))
                    .frame(width: barWidth, height: taxHeight)
            }
            .clipShape(
                UnevenRoundedRectangle(
                    topLeadingRadius: 6,
                    bottomLeadingRadius: data.isProfit ? 0 : 6,
                    bottomTrailingRadius: data.isProfit ? 0 : 6,
                    topTrailingRadius: 6
                )
            )

            Spacer()
        }
        .frame(height: height)
    }

    // MARK: - Net Profit/Loss Bar

    private func netProfitBar(height: CGFloat, barWidth: CGFloat) -> some View {
        let pixelsPerUnit = height / chartRange
        let netProfitHeight = CGFloat(abs(data.netProfit)) * pixelsPerUnit

        if data.isProfit {
            // Profit bar - grows up from zero (bottom)
            return AnyView(
                VStack(spacing: 0) {
                    Spacer()

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
                }
                .frame(height: height)
            )
        } else {
            // Loss bar - goes below zero line
            let zeroY = height * (1 - zeroLinePosition)

            return AnyView(
                VStack(spacing: 0) {
                    Spacer().frame(height: zeroY)

                    VStack(spacing: 2) {
                        Rectangle()
                            .fill(Color(hex: "8B0000"))
                            .frame(width: barWidth, height: netProfitHeight)
                            .clipShape(
                                UnevenRoundedRectangle(
                                    topLeadingRadius: 0,
                                    bottomLeadingRadius: 6,
                                    bottomTrailingRadius: 6,
                                    topTrailingRadius: 0
                                )
                            )

                        Text("-Net Loss")
                            .font(.system(size: 10))
                            .foregroundColor(AppColors.textMuted)
                    }

                    Spacer()
                }
                .frame(height: height)
            )
        }
    }

    // MARK: - Helper Functions

    private func formatLargeNumber(_ number: Double) -> String {
        let absNumber = abs(number)
        let sign = number < 0 ? "-" : ""

        if absNumber >= 1_000_000_000_000 {
            return sign + String(format: "%.0fT", absNumber / 1_000_000_000_000)
        } else if absNumber >= 1_000_000_000 {
            return sign + String(format: "%.0fB", absNumber / 1_000_000_000)
        } else if absNumber >= 1_000_000 {
            return sign + String(format: "%.0fM", absNumber / 1_000_000)
        } else if absNumber >= 1_000 {
            return sign + String(format: "%.0fK", absNumber / 1_000)
        }
        return String(format: "%.0f", number)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            VStack(spacing: AppSpacing.xxl) {
                Text("Profitable Company (Apple)")
                    .foregroundColor(.white)
                    .font(AppTypography.headline)

                RevenueBreakdownChartView(data: RevenueBreakdownData.sampleApple)
                    .padding()
                    .background(AppColors.cardBackground)
                    .cornerRadius(AppCornerRadius.large)

                Divider()
                    .background(AppColors.textMuted)

                Text("Loss-Making Company (Rivian)")
                    .foregroundColor(.white)
                    .font(AppTypography.headline)

                RevenueBreakdownChartView(data: RevenueBreakdownData.sampleLossCompany)
                    .padding()
                    .background(AppColors.cardBackground)
                    .cornerRadius(AppCornerRadius.large)
            }
            .padding()
        }
    }
}
