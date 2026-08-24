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
    /// Column header for the asset side of the comparison table. Every one of the five
    /// detail screens already holds its own symbol, so this costs nothing to thread.
    var symbol: String = ""

    // Grid columns - 3 columns layout
    private let columns = Array(repeating: GridItem(.flexible(), spacing: AppSpacing.sm), count: 3)

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section title inside card styling
            Text("Performance")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)

            // Performance grid
            LazyVGrid(columns: columns, spacing: AppSpacing.sm) {
                ForEach(periods) { period in
                    PerformanceItem(period: period)
                }
            }

            // Benchmark summary
            if let summary = benchmarkSummary {
                // `divider`, not `cardBackgroundLight`: that is a SURFACE token (#EDF0F5
                // light / #252B3B dark), so the separator was drawn in a colour barely
                // distinguishable from the card itself. "Or add lines?" — the line was
                // there all along, just invisible.
                Rectangle()
                    .fill(AppColors.divider)
                    .frame(height: 1)

                PerformanceBenchmarkRow(summary: summary, symbol: symbol)
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
    }
}

// MARK: - Performance Benchmark Summary

/// One or two rows of "annualised return, asset vs benchmark".
///
/// ⚠️ EVERY ROW'S TWO NUMBERS COVER THE SAME WINDOW, and `windowLabel` + `sinceDate` name
/// it. That is a backend guarantee (`benchmark_math.overlapping_cagrs`), and the table
/// below depends on it: it prints ONE window per row spanning both columns. If a service
/// ever starts measuring its two sides differently, the table becomes a false statement,
/// not merely an imprecise one.
///
/// `benchmarkSinceDate` is gone — it was always either a duplicate of `sinceDate` or nil,
/// and printing it is what produced the two identical "Since Aug 2021" labels a TestFlight
/// tester reported as hard to compare.
struct PerformanceBenchmarkSummary {
    let avgAnnualReturn: Double
    let spBenchmark: Double
    let benchmarkName: String
    let sinceDate: String?
    let badgeThreshold: Double
    /// "5-year" | "All-time". Nil on an older backend — the row then shows its date alone.
    let windowLabel: String?
    /// `false` means the backend could not measure the benchmark. `spBenchmark` is then a
    /// placeholder 0.0 that must NOT be rendered: it used to show as "S&P 500 0.0%" with
    /// an "Outperforming" badge beside it whenever an upstream fetch failed.
    let benchmarkAvailable: Bool
    let alltimeAnnualReturn: Double?
    let alltimeBenchmark: Double?
    let alltimeSinceDate: String?

    init(
        avgAnnualReturn: Double,
        spBenchmark: Double,
        benchmarkName: String = "S&P 500",
        sinceDate: String? = nil,
        badgeThreshold: Double = 0,
        windowLabel: String? = nil,
        benchmarkAvailable: Bool = true,
        alltimeAnnualReturn: Double? = nil,
        alltimeBenchmark: Double? = nil,
        alltimeSinceDate: String? = nil
    ) {
        self.avgAnnualReturn = avgAnnualReturn
        self.spBenchmark = spBenchmark
        self.benchmarkName = benchmarkName
        self.sinceDate = sinceDate
        self.badgeThreshold = badgeThreshold
        self.windowLabel = windowLabel
        self.benchmarkAvailable = benchmarkAvailable
        self.alltimeAnnualReturn = alltimeAnnualReturn
        self.alltimeBenchmark = alltimeBenchmark
        self.alltimeSinceDate = alltimeSinceDate
    }

    /// The rows the table draws, primary first. Built here rather than in the view so the
    /// "same window on both sides" rule has exactly one place to live.
    var rows: [BenchmarkComparisonRow] {
        var out = [
            BenchmarkComparisonRow(
                label: windowLabel ?? "Average annual",
                since: sinceDate,
                assetValue: avgAnnualReturn,
                benchmarkValue: benchmarkAvailable ? spBenchmark : nil
            )
        ]
        // The secondary row only exists when the backend measured a genuinely different
        // window; it sends nil rather than repeating the primary one.
        if let alltime = alltimeAnnualReturn {
            out.append(
                BenchmarkComparisonRow(
                    label: "All-time",
                    since: alltimeSinceDate,
                    assetValue: alltime,
                    benchmarkValue: alltimeBenchmark
                )
            )
        }
        return out
    }

    var isOutperforming: Bool {
        avgAnnualReturn >= spBenchmark
    }

    /// The verdict pill describes the PRIMARY row only, which is why its text names that
    /// row's window. Without the window it read "Underperforming" directly above an
    /// all-time row where the asset was ahead — the contradiction in the tester's
    /// screenshot (5-year 8.7 vs 11.4, all-time 10.0 vs 9.1).
    var shouldShowBadge: Bool {
        benchmarkAvailable && abs(avgAnnualReturn - spBenchmark) > badgeThreshold
    }

    var badgeLabel: String {
        let verdict = isOutperforming ? "Outperforming" : "Underperforming"
        guard let window = windowLabel?.lowercased(), !window.isEmpty else { return verdict }
        return window == "all-time"
            ? "\(verdict) all-time"
            : "\(verdict) over \(window == "5-year" ? "5 years" : window)"
    }
}

struct PerformanceBenchmarkRow: View {
    let summary: PerformanceBenchmarkSummary
    var symbol: String = ""

    private var badgeColor: Color {
        summary.isOutperforming ? AppColors.gain : AppColors.loss
    }

    /// Falls back to the benchmark's own wording when the caller has no symbol to show,
    /// so the header never renders an empty column.
    private var assetLabel: String {
        let trimmed = symbol.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? "This asset" : trimmed.uppercased()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            Text("Average annual return")
                .font(AppTypography.caption)
                .foregroundColor(AppColors.textMuted)

            BenchmarkComparisonTable(
                assetLabel: assetLabel,
                // "S&P 500 Benchmark" was the old default and the word "Benchmark" is
                // dead weight in a column header — the column IS the benchmark. Crypto
                // sends "Bitcoin (BTC)" here, so this is not always the S&P.
                benchmarkLabel: summary.benchmarkName,
                rows: summary.rows
            )

            if summary.shouldShowBadge {
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
}

#Preview {
    ScrollView {
        TickerDetailPerformanceSection(
            periods: PerformancePeriod.sampleData,
            benchmarkSummary: PerformanceBenchmarkSummary(
                avgAnnualReturn: 8.7,
                spBenchmark: 11.4,
                benchmarkName: "S&P 500",
                sinceDate: "Aug 2021",
                windowLabel: "5-year",
                alltimeAnnualReturn: 10.0,
                alltimeBenchmark: 9.1,
                alltimeSinceDate: "Oct 05, 2006"
            ),
            symbol: "BRK.B"
        )
        .padding()
    }
    .background(AppColors.background)
}
