//
//  RevenueBreakdownChartView.swift
//  ios
//
//  Molecule: Waterfall chart showing revenue sources and cost breakdown
//

import SwiftUI
import Charts

struct RevenueBreakdownChartView: View {
    let data: RevenueBreakdownData

    private let chartHeight: CGFloat = 300
    private let leftAxisWidth: CGFloat = 45
    private let rightAxisWidth: CGFloat = 45

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
            let barWidth = width * 0.25
            let spacing = width * 0.1

            ZStack(alignment: .bottomLeading) {
                // Grid lines
                gridLines(height: height)

                // Revenue stacked bar (left)
                revenueBar(height: height, barWidth: barWidth, xOffset: spacing)

                // Cost waterfall bars (center-right)
                costWaterfallBars(height: height, barWidth: barWidth, xOffset: spacing + barWidth + spacing * 1.5)

                // Net profit/loss bar
                netProfitBar(height: height, barWidth: barWidth, xOffset: width - spacing - barWidth)

                // Net profit label
                netProfitLabel(height: height, xOffset: width - spacing - barWidth / 2)
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

    // MARK: - Revenue Stacked Bar

    private func revenueBar(height: CGFloat, barWidth: CGFloat, xOffset: CGFloat) -> some View {
        let totalRevenue = data.totalRevenue
        var cumulativeHeight: CGFloat = 0

        return ZStack(alignment: .bottom) {
            ForEach(data.revenueSources.reversed()) { source in
                let segmentHeight = CGFloat(source.value / totalRevenue) * height

                RoundedRectangle(cornerRadius: cumulativeHeight == 0 ? 0 : 0)
                    .fill(source.color)
                    .frame(width: barWidth, height: segmentHeight)
                    .offset(y: -cumulativeHeight)
                    .onAppear {
                        // This is a workaround - we'll use a different approach
                    }
            }
        }
        .frame(width: barWidth, height: height, alignment: .bottom)
        .clipShape(
            UnevenRoundedRectangle(
                topLeadingRadius: 6,
                bottomLeadingRadius: 0,
                bottomTrailingRadius: 0,
                topTrailingRadius: 6
            )
        )
        .offset(x: xOffset)
    }

    // MARK: - Cost Waterfall Bars

    private func costWaterfallBars(height: CGFloat, barWidth: CGFloat, xOffset: CGFloat) -> some View {
        let totalRevenue = data.totalRevenue
        let scale = height / totalRevenue

        // Calculate positions
        let costOfSalesTop = totalRevenue
        let costOfSalesBottom = totalRevenue - data.costOfSales

        let opExpenseTop = costOfSalesBottom
        let opExpenseBottom = costOfSalesBottom - data.operatingExpense

        let taxTop = opExpenseBottom
        let taxBottom = opExpenseBottom - data.tax

        return ZStack(alignment: .bottomLeading) {
            // Cost of Sales bar (starts from top of revenue)
            Rectangle()
                .fill(Color(hex: "EF4444"))
                .frame(width: barWidth, height: CGFloat(data.costOfSales) * scale)
                .offset(y: -CGFloat(costOfSalesBottom) * scale)
                .clipShape(
                    UnevenRoundedRectangle(
                        topLeadingRadius: 6,
                        bottomLeadingRadius: 0,
                        bottomTrailingRadius: 0,
                        topTrailingRadius: 6
                    )
                )

            // Operating Expense bar
            Rectangle()
                .fill(Color(hex: "F87171"))
                .frame(width: barWidth, height: CGFloat(data.operatingExpense) * scale)
                .offset(x: barWidth * 0.15, y: -CGFloat(opExpenseBottom) * scale)

            // Tax bar
            Rectangle()
                .fill(Color(hex: "FCA5A5"))
                .frame(width: barWidth, height: CGFloat(data.tax) * scale)
                .offset(x: barWidth * 0.3, y: -CGFloat(taxBottom) * scale)
        }
        .frame(height: height, alignment: .bottom)
        .offset(x: xOffset)
    }

    // MARK: - Net Profit/Loss Bar

    private func netProfitBar(height: CGFloat, barWidth: CGFloat, xOffset: CGFloat) -> some View {
        let totalRevenue = data.totalRevenue
        let scale = height / totalRevenue
        let netProfit = data.netProfit

        return Group {
            if data.isProfit {
                // Profit - green bar from bottom
                Rectangle()
                    .fill(AppColors.bullish)
                    .frame(width: barWidth, height: CGFloat(netProfit) * scale)
                    .clipShape(
                        UnevenRoundedRectangle(
                            topLeadingRadius: 6,
                            bottomLeadingRadius: 0,
                            bottomTrailingRadius: 0,
                            topTrailingRadius: 6
                        )
                    )
                    .offset(x: xOffset)
            } else {
                // Loss - dark red bar below zero line
                Rectangle()
                    .fill(Color(hex: "8B0000"))
                    .frame(width: barWidth, height: CGFloat(abs(netProfit)) * scale)
                    .offset(x: xOffset, y: 0)
            }
        }
        .frame(height: height, alignment: .bottom)
    }

    // MARK: - Net Profit Label

    private func netProfitLabel(height: CGFloat, xOffset: CGFloat) -> some View {
        let totalRevenue = data.totalRevenue
        let scale = height / totalRevenue
        let netProfit = data.netProfit

        return Group {
            if data.isProfit {
                Text("Net Profit")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .offset(x: xOffset - 25, y: -(CGFloat(netProfit) * scale + 15))
            } else {
                Text("-Net Loss")
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .offset(x: xOffset - 25, y: CGFloat(abs(netProfit)) * scale / 2)
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
