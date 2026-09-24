//
//  ThemePerformanceCard.swift
//  ios
//
//  Molecule: "Performance vs S&P 500" on the theme detail — the theme's return over 1M /
//  YTD / 1Y beside an S&P 500 ETF (the index itself is not licensed), plus a 1-year line.
//
//  ⚠️ The basis is the theme's CURRENT stocks, equal-weighted, looked back — not a track
//  record of past picks. The card says so in words, because a "since we added it" style
//  history would read as a performance record for a recommendation list.
//

import Charts
import SwiftUI

struct ThemePerformanceCard: View {
    let performance: ThemePerformance
    let accent: Color

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Performance vs S&P 500")
                    .font(AppTypography.headingSmall)
                    .foregroundColor(AppColors.textPrimary)
                Text(basisLine)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textMuted)
                    .fixedSize(horizontal: false, vertical: true)
            }

            HStack(alignment: .top, spacing: AppSpacing.sm) {
                ForEach(performance.periods) { period in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(period.period)
                            .font(AppTypography.captionEmphasis)
                            .foregroundColor(AppColors.textMuted)
                        Text(period.themeText)
                            .font(AppTypography.bodyEmphasis)
                            .foregroundColor(!period.hasTheme ? AppColors.textMuted
                                             : (period.themeIsPositive ? AppColors.gain : AppColors.loss))
                        Text("S&P \(period.benchmarkText)")
                            .font(AppTypography.caption)
                            .foregroundColor(AppColors.textSecondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("\(period.period): theme \(period.themeText), S and P 500 E T F \(period.benchmarkText)")
                }
            }

            if performance.themeSeries.count >= 2 {
                chart
                    .frame(height: 120)
                    .accessibilityHidden(true)   // the three numbers above carry the content
            }
        }
        .padding(AppSpacing.lg)
        .frame(maxWidth: .infinity, alignment: .leading)
        .cardSurface()
    }

    private var basisLine: String {
        var line = "Current stocks, equal-weighted · vs \(performance.benchmarkLabel)"
        if let asOf = performance.asOf { line += " · \(ThemeReviewDate.short(asOf))" }
        return line
    }

    private var yDomain: ClosedRange<Double> {
        let values = performance.themeSeries + performance.benchmarkSeries
        guard let lo = values.min(), let hi = values.max(), hi.isFinite, lo.isFinite else {
            return 90...110
        }
        let pad = max((hi - lo) * 0.04, 0.5)
        return (lo - pad)...(hi + pad)
    }

    private var chart: some View {
        Chart {
            ForEach(Array(performance.themeSeries.enumerated()), id: \.offset) { i, v in
                LineMark(x: .value("Day", i), y: .value("Index", v), series: .value("Series", "Theme"))
                    .foregroundStyle(accent)
                    .lineStyle(StrokeStyle(lineWidth: 2))
            }
            ForEach(Array(performance.benchmarkSeries.enumerated()), id: \.offset) { i, v in
                LineMark(x: .value("Day", i), y: .value("Index", v), series: .value("Series", "S&P 500 ETF"))
                    .foregroundStyle(AppColors.textMuted)
                    .lineStyle(StrokeStyle(lineWidth: 1.5, dash: [4, 3]))
            }
        }
        .chartXAxis(.hidden)
        .chartYAxis {
            AxisMarks(position: .trailing, values: .automatic(desiredCount: 3)) { _ in
                AxisGridLine().foregroundStyle(AppColors.textPrimary.opacity(0.06))
            }
        }
        // The data's own range (+4% headroom), not an automatic "nice" domain: that padded
        // to round numbers and left a third of the card empty under the lines.
        .chartYScale(domain: yDomain)
        .chartLegend(.hidden)
    }
}

#Preview {
    ThemePerformanceCard(
        performance: ThemePerformance(dto: ThemePerformanceDTO(
            asOf: "2026-09-23", benchmarkLabel: "S&P 500 ETF",
            periods: [ThemePeriodReturnDTO(period: "1M", theme: 0.042, benchmark: 0.011),
                      ThemePeriodReturnDTO(period: "YTD", theme: 0.183, benchmark: 0.097),
                      ThemePeriodReturnDTO(period: "1Y", theme: nil, benchmark: 0.142)],
            themeSeries: [100, 103, 101, 107, 112, 110, 118],
            benchmarkSeries: [100, 101, 102, 101, 104, 106, 107]))!,
        accent: AppColors.accentCyan)
        .padding()
        .background(AppColors.background)
}
