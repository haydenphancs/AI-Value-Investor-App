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

    init(data: [AnalystMomentumMonth]) {
        self.data = data
        // Calculate max for scaling (considering both positive and negative)
        let maxPositive = data.map { $0.positiveCount }.max() ?? 10
        let maxNegative = data.map { $0.negativeCount }.max() ?? 10
        self.maxValue = max(maxPositive, maxNegative, 1)
    }

    var body: some View {
        VStack(spacing: AppSpacing.sm) {
            // Y-axis labels and chart
            HStack(alignment: .bottom, spacing: AppSpacing.sm) {
                // Y-axis labels
                VStack {
                    Text("10")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Spacer()
                    Text("0")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Spacer()
                    Text("-5")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Spacer()
                    Text("-10")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                }
                .frame(width: 24)
                .frame(height: 140)

                // Bars container
                VStack(spacing: 0) {
                    HStack(alignment: .center, spacing: 0) {
                        ForEach(data) { month in
                            MomentumBar(month: month, maxValue: maxValue)
                                .frame(maxWidth: .infinity)
                        }
                    }
                    .frame(height: 140)
                }
            }

            // X-axis labels (months)
            HStack(spacing: 0) {
                Spacer()
                    .frame(width: 24 + AppSpacing.sm)
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

    var body: some View {
        GeometryReader { geometry in
            let midY = geometry.size.height / 2
            let scale = (geometry.size.height / 2) / CGFloat(max(maxValue, 1))
            let barWidth: CGFloat = 24

            ZStack {
                // Zero line (implicit - bars grow from center)

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
