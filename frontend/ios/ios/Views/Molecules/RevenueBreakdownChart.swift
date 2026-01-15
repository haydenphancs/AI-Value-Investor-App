//
//  RevenueBreakdownChart.swift
//  ios
//
//  Molecule: Revenue breakdown visualization showing sources and cost structure
//

import SwiftUI
import Charts

struct RevenueBreakdownChart: View {
    let data: RevenueBreakdownData

    private let barHeight: CGFloat = 36

    var body: some View {
        VStack(spacing: AppSpacing.xl) {
            // Stacked horizontal bar chart
            VStack(spacing: AppSpacing.xs) {
                // Revenue bar
                GeometryReader { geometry in
                    HStack(spacing: 0) {
                        ForEach(data.segments) { segment in
                            Rectangle()
                                .fill(segment.color)
                                .frame(width: geometry.size.width * (segment.percentage / 100))
                        }
                    }
                    .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.small))
                }
                .frame(height: barHeight)

                // Percentage labels
                HStack(spacing: 0) {
                    Text("0%")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Spacer()
                    Text("100%")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }

                // Cost/Profit breakdown bar
                GeometryReader { geometry in
                    HStack(spacing: 0) {
                        // Cost of Sales
                        Rectangle()
                            .fill(data.costOfSales.color)
                            .frame(width: geometry.size.width * (data.costOfSales.percentage / 100))

                        // Operating Expenses
                        Rectangle()
                            .fill(data.operatingExpenses.color)
                            .frame(width: geometry.size.width * (data.operatingExpenses.percentage / 100))

                        // Tax
                        Rectangle()
                            .fill(data.tax.color)
                            .frame(width: geometry.size.width * (data.tax.percentage / 100))

                        // Net Profit
                        Rectangle()
                            .fill(data.netProfit.color)
                            .frame(width: geometry.size.width * (data.netProfit.percentage / 100))
                    }
                    .clipShape(RoundedRectangle(cornerRadius: AppCornerRadius.small))
                }
                .frame(height: barHeight)
                .padding(.top, AppSpacing.sm)

                // Net Profit Margin indicator
                HStack {
                    Spacer()
                    Text("Net Profit")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text(data.formattedNetProfitMargin)
                        .font(AppTypography.captionBold)
                        .foregroundColor(AppColors.bullish)
                }
            }

            // Revenue Sources Legend
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Revenue Sources")
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)

                VStack(spacing: AppSpacing.xs) {
                    ForEach(data.segments) { segment in
                        RevenueSourceRow(
                            color: segment.color,
                            name: segment.name,
                            value: segment.formattedValue,
                            percentage: segment.formattedPercentage
                        )
                    }
                }
            }

            // Costs & Profit Legend
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text("Costs & Profit")
                    .font(AppTypography.subheadline)
                    .foregroundColor(AppColors.textSecondary)

                VStack(spacing: AppSpacing.xs) {
                    RevenueSourceRow(
                        color: data.costOfSales.color,
                        name: data.costOfSales.name,
                        value: data.costOfSales.formattedValue,
                        percentage: data.costOfSales.formattedPercentage
                    )
                    RevenueSourceRow(
                        color: data.operatingExpenses.color,
                        name: data.operatingExpenses.name,
                        value: data.operatingExpenses.formattedValue,
                        percentage: data.operatingExpenses.formattedPercentage
                    )
                    RevenueSourceRow(
                        color: data.tax.color,
                        name: data.tax.name,
                        value: data.tax.formattedValue,
                        percentage: data.tax.formattedPercentage
                    )
                    RevenueSourceRow(
                        color: data.netProfit.color,
                        name: data.netProfit.name,
                        value: data.netProfit.formattedValue,
                        percentage: data.netProfit.formattedPercentage
                    )
                }
            }
        }
    }
}

// MARK: - Revenue Source Row
struct RevenueSourceRow: View {
    let color: Color
    let name: String
    let value: String
    let percentage: String

    var body: some View {
        HStack(spacing: AppSpacing.sm) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)

            Text(name)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textPrimary)

            Spacer()

            Text(value)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)

            Text(percentage)
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)
                .frame(width: 40, alignment: .trailing)
        }
    }
}

// MARK: - Preview
#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        ScrollView {
            RevenueBreakdownChart(data: RevenueBreakdownData.sampleApple)
                .padding()
        }
    }
}
