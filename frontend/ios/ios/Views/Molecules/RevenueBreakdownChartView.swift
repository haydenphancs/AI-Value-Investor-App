//
//  RevenueBreakdownChartView.swift
//  ios
//
//  Molecule: Waterfall chart showing revenue sources and cost breakdown
//  Handles both profitable and loss-making companies with proper scaling
//

import SwiftUI

struct RevenueBreakdownChartView: View {
    let data: RevenueBreakdownData

    private let chartHeight: CGFloat = 320
    private let leftAxisWidth: CGFloat = 50
    private let rightAxisWidth: CGFloat = 50

    // Use the larger of revenue or costs for scaling
    private var maxValue: Double {
        max(data.totalRevenue, data.totalCosts) * 1.1
    }

    // Grid line values - based on max value
    private var gridValues: [Double] {
        return [0, maxValue * 0.25, maxValue * 0.5, maxValue * 0.75, maxValue]
    }

    // Percentage labels for right axis - adjust for loss companies
    private var percentageLabels: [String] {
        if data.costsExceedRevenue {
            // Show higher percentages when costs exceed revenue
            let maxPercent = Int((maxValue / data.totalRevenue) * 100)
            let step = maxPercent / 4
            return ["0%", "\(step)%", "\(step * 2)%", "\(step * 3)%", "\(maxPercent)%"]
        } else {
            return ["0%", "25%", "50%", "75%", "100%"]
        }
    }

    // Position of 100% revenue line (for break-even indicator)
    private var revenueLinePosition: CGFloat {
        CGFloat(data.totalRevenue / maxValue)
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
            let scale = height / maxValue

            ZStack(alignment: .bottom) {
                // Grid lines
                gridLines(height: height)

                // Break-even line (100% revenue) for loss companies
                if data.costsExceedRevenue {
                    breakEvenLine(height: height, scale: scale)
                }

                // Revenue stacked bar (left side)
                revenueStackedBar(height: height, barWidth: barWidth, scale: scale)
                    .position(x: width * 0.23, y: height / 2)

                // Cost waterfall bars (middle)
                costWaterfallBar(height: height, barWidth: barWidth, scale: scale)
                    .position(x: width * 0.53, y: height / 2)

                // Net profit/loss bar (far right)
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

    // MARK: - Break-Even Line (for loss companies)

    private func breakEvenLine(height: CGFloat, scale: CGFloat) -> some View {
        let revenueHeight = CGFloat(data.totalRevenue) * scale
        let yPosition = height - revenueHeight

        return VStack(spacing: 0) {
            Spacer()
                .frame(height: yPosition)

            HStack(spacing: 4) {
                Rectangle()
                    .fill(AppColors.neutral.opacity(0.6))
                    .frame(height: 1.5)

                Text("Revenue")
                    .font(.system(size: 9, weight: .medium))
                    .foregroundColor(AppColors.neutral)
                    .padding(.horizontal, 4)
                    .background(AppColors.cardBackground)
            }

            Spacer()
        }
        .frame(height: height)
    }

    // MARK: - Revenue Stacked Bar

    private func revenueStackedBar(height: CGFloat, barWidth: CGFloat, scale: CGFloat) -> some View {
        let revenueBarHeight = CGFloat(data.totalRevenue) * scale

        // Calculate segment heights proportionally within the revenue bar
        let segments: [(color: Color, height: CGFloat)] = data.revenueSources.map { source in
            (source.color, CGFloat(source.value / data.totalRevenue) * revenueBarHeight)
        }

        return VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 0) {
                // Stack from top to bottom
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
        }
        .frame(height: height, alignment: .bottom)
    }

    // MARK: - Cost Waterfall Bar

    private func costWaterfallBar(height: CGFloat, barWidth: CGFloat, scale: CGFloat) -> some View {
        // Calculate heights based on max value (not revenue)
        let costOfSalesHeight = CGFloat(data.costOfSales) * scale
        let opExpenseHeight = CGFloat(data.operatingExpense) * scale
        let taxHeight = CGFloat(data.tax) * scale
        let totalCostHeight = costOfSalesHeight + opExpenseHeight + taxHeight

        return VStack(spacing: 0) {
            Spacer()

            ZStack(alignment: .bottom) {
                VStack(spacing: 0) {
                    // Cost of Sales (top)
                    Rectangle()
                        .fill(Color(hex: "EF4444"))
                        .frame(width: barWidth, height: costOfSalesHeight)

                    // Operating Expense (middle)
                    Rectangle()
                        .fill(Color(hex: "F87171"))
                        .frame(width: barWidth, height: opExpenseHeight)

                    // Tax (bottom)
                    Rectangle()
                        .fill(Color(hex: "FCA5A5"))
                        .frame(width: barWidth, height: taxHeight)
                }
                .clipShape(
                    UnevenRoundedRectangle(
                        topLeadingRadius: 6,
                        bottomLeadingRadius: 0,
                        bottomTrailingRadius: 0,
                        topTrailingRadius: 6
                    )
                )
            }
            .frame(height: totalCostHeight)
        }
        .frame(height: height, alignment: .bottom)
    }

    // MARK: - Net Profit/Loss Bar

    private func netProfitBar(height: CGFloat, barWidth: CGFloat, scale: CGFloat) -> some View {
        let netProfit = data.netProfit
        let netProfitHeight = CGFloat(abs(netProfit)) * scale

        return VStack(spacing: 0) {
            Spacer()

            VStack(spacing: 2) {
                if data.isProfit {
                    // Profit label above bar
                    Text("Net Profit")
                        .font(.system(size: 10))
                        .foregroundColor(AppColors.textMuted)

                    // Green bar from bottom
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
                } else {
                    // Loss - dark red bar with label below
                    Rectangle()
                        .fill(Color(hex: "8B0000"))
                        .frame(width: barWidth, height: netProfitHeight)
                        .clipShape(
                            UnevenRoundedRectangle(
                                topLeadingRadius: 6,
                                bottomLeadingRadius: 0,
                                bottomTrailingRadius: 0,
                                topTrailingRadius: 6
                            )
                        )

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
