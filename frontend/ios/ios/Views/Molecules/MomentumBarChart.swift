//
//  MomentumBarChart.swift
//  ios
//
//  Bar chart showing analyst momentum over time
//

import SwiftUI

struct MomentumBarChart: View {
    let data: [AnalystMomentumMonth]
    let maxValue: Int

    private let chartHeight: CGFloat = 140

    init(data: [AnalystMomentumMonth]) {
        self.data = data
        let maxPositive = data.map { $0.positiveCount }.max() ?? 1
        let maxNegative = data.map { $0.negativeCount }.max() ?? 1
        self.maxValue = max(maxPositive, maxNegative, 1)
    }

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            // Y-axis labels and chart
            HStack(alignment: .top, spacing: AppSpacing.sm) {
                // Y-axis labels — top, center (0), bottom aligned to chart
                ZStack(alignment: .leading) {
                    // Top label (max positive)
                    Text("\(maxValue)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .frame(height: chartHeight, alignment: .top)

                    // Center label (0)
                    Text("0")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .frame(height: chartHeight, alignment: .center)

                    // Bottom label (max negative)
                    Text("-\(maxValue)")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .frame(height: chartHeight, alignment: .bottom)
                }
                .frame(width: 28, height: chartHeight)

                // Bars container
                ZStack {
                    // Zero line across the chart
                    VStack {
                        Spacer()
                        Rectangle()
                            .fill(AppColors.cardBackgroundLight)
                            .frame(height: 1)
                        Spacer()
                    }

                    HStack(alignment: .center, spacing: 0) {
                        ForEach(data) { month in
                            MomentumBar(month: month, maxValue: maxValue)
                                .frame(maxWidth: .infinity)
                        }
                    }
                }
                .frame(height: chartHeight)
            }

            // X-axis labels (months)
            HStack(spacing: 0) {
                Spacer()
                    .frame(width: 28 + AppSpacing.sm)
                ForEach(data) { month in
                    Text(month.month)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .frame(maxWidth: .infinity)
                }
            }
        }
    }
}

// MARK: - Single Momentum Bar
struct MomentumBar: View {
    let month: AnalystMomentumMonth
    let maxValue: Int

    private let chartHeight: CGFloat = 140

    private var isEmpty: Bool {
        month.positiveCount == 0 && month.negativeCount == 0
    }

    var body: some View {
        GeometryReader { geometry in
            let midY = geometry.size.height / 2
            let scale = (geometry.size.height / 2) / CGFloat(max(maxValue, 1))
            let barWidth: CGFloat = 20

            ZStack {
                if isEmpty {
                    // Thin gray dash for months with zero counts
                    RoundedRectangle(cornerRadius: 1)
                        .fill(AppColors.textMuted.opacity(0.4))
                        .frame(width: barWidth, height: 2)
                        .position(x: geometry.size.width / 2, y: midY)
                } else {
                    // Positive bar (grows up from center)
                    if month.positiveCount > 0 {
                        RoundedRectangle(cornerRadius: 3)
                            .fill(AppColors.bullish)
                            .frame(
                                width: barWidth,
                                height: CGFloat(month.positiveCount) * scale
                            )
                            .position(
                                x: geometry.size.width / 2,
                                y: midY - (CGFloat(month.positiveCount) * scale / 2)
                            )
                    }

                    // Negative bar (grows down from center)
                    if month.negativeCount > 0 {
                        RoundedRectangle(cornerRadius: 3)
                            .fill(AppColors.bearish)
                            .frame(
                                width: barWidth,
                                height: CGFloat(month.negativeCount) * scale
                            )
                            .position(
                                x: geometry.size.width / 2,
                                y: midY + (CGFloat(month.negativeCount) * scale / 2)
                            )
                    }
                }
            }
        }
        .frame(height: chartHeight)
    }
}

#Preview {
    ZStack {
        AppColors.background
            .ignoresSafeArea()

        MomentumBarChart(data: AnalystMomentumMonth.sampleData)
            .padding()
    }
}
