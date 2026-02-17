//
//  TickerDetailPerformanceSection.swift
//  ios
//
//  Organism: Performance section for Ticker Detail
//

import SwiftUI

struct TickerDetailPerformanceSection: View {
    let periods: [PerformancePeriod]
    var benchmarkSummary: PerformanceBenchmarkSummary?

    // Grid columns - 3 columns layout
    private let columns = Array(repeating: GridItem(.flexible(), spacing: AppSpacing.sm), count: 3)

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title inside card styling
            Text("Performance")
                .font(AppTypography.title3)
                .foregroundColor(AppColors.textPrimary)

            // Performance grid
            LazyVGrid(columns: columns, spacing: AppSpacing.sm) {
                ForEach(periods) { period in
                    PerformanceItem(period: period)
                }
            }

            // Benchmark summary
            if let summary = benchmarkSummary {
                Rectangle()
                    .fill(AppColors.cardBackgroundLight)
                    .frame(height: 1)

                PerformanceBenchmarkRow(summary: summary)
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
        )
    }
}

// MARK: - Performance Benchmark Summary

struct PerformanceBenchmarkSummary {
    let avgAnnualReturn: Double
    let spBenchmark: Double
    let benchmarkName: String

    init(avgAnnualReturn: Double, spBenchmark: Double, benchmarkName: String = "S&P 500 Benchmark") {
        self.avgAnnualReturn = avgAnnualReturn
        self.spBenchmark = spBenchmark
        self.benchmarkName = benchmarkName
    }

    var isOutperforming: Bool {
        avgAnnualReturn >= spBenchmark
    }

    var formattedAvgReturn: String {
        String(format: "%.1f%%", avgAnnualReturn)
    }

    var formattedBenchmark: String {
        String(format: "%.1f%%", spBenchmark)
    }

    var badgeLabel: String {
        isOutperforming ? "Outperforming" : "Underperforming"
    }
}

struct PerformanceBenchmarkRow: View {
    let summary: PerformanceBenchmarkSummary

    private var badgeColor: Color {
        summary.isOutperforming ? AppColors.bullish : AppColors.bearish
    }

    var body: some View {
        VStack(spacing: AppSpacing.md) {
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                    Text("Average Annual Return")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text(summary.formattedAvgReturn)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.textPrimary)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: AppSpacing.xxs) {
                    Text(summary.benchmarkName)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                    Text(summary.formattedBenchmark)
                        .font(AppTypography.calloutBold)
                        .foregroundColor(AppColors.textSecondary)
                }
            }

            // Badge
            HStack {
                Text(summary.badgeLabel)
                    .font(AppTypography.caption)
                    .fontWeight(.semibold)
                    .foregroundColor(badgeColor)
                    .padding(.horizontal, AppSpacing.md)
                    .padding(.vertical, AppSpacing.xs)
                    .background(
                        Capsule()
                            .fill(badgeColor.opacity(0.15))
                    )

                Spacer()
            }
        }
    }
}

#Preview {
    ScrollView {
        TickerDetailPerformanceSection(periods: PerformancePeriod.sampleData)
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
