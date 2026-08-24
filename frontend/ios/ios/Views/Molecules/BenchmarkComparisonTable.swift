//
//  BenchmarkComparisonTable.swift
//  ios
//
//  Molecule: the "annualised return, asset vs benchmark" table on the Performance card.
//
//  WHY THIS EXISTS. A TestFlight tester on build 1.0 (2): "It's hard to read or compare
//  the average annual return and sp500 benchmark. Make a grid? Or add lines?" They were
//  reading two numbers rendered as two independent `VStack`s shoved apart by a `Spacer()`
//  — one leading-aligned, one trailing-aligned, no shared baseline, no column, no rule.
//  Four more things compounded it:
//
//    * the values used a READING-tier font, so the digits were proportional and did not
//      line up vertically even by accident;
//    * the asset was `textPrimary` and the benchmark `textSecondary`, implying a hierarchy
//      between two peers;
//    * "Since Aug 2021" was printed TWICE, once under each column;
//    * the right-hand all-time figure had no label at all — the left baked its label into
//      the value string ("All-time: 10.0%") and the right just said "9.1%".
//
//  A `Grid` fixes the first one properly: columns align by construction, which an
//  `HStack` + `Spacer` cannot do across rows no matter how the padding is tuned.
//
//  ⚠️ ONE WINDOW PER ROW, SPANNING BOTH COLUMNS. That is only honest because the backend
//  guarantees it (`benchmark_math.overlapping_cagrs` measures both sides over the window
//  they share and returns its start). Do not add a per-column date here — the reason the
//  old layout could show two of them is that the two sides were free to disagree, and on
//  the ETF screen they did, by eighteen years.
//

import SwiftUI

/// One comparison row. `benchmarkValue` is nil when the backend could not measure it —
/// rendered as an em dash, never as the placeholder 0.0 that travels on the wire.
struct BenchmarkComparisonRow: Identifiable {
    let id = UUID()
    let label: String
    let since: String?
    let assetValue: Double
    let benchmarkValue: Double?

    /// Percentage POINTS of annualised return, asset minus benchmark. Nil when there is
    /// no benchmark to subtract.
    var delta: Double? {
        guard let benchmarkValue else { return nil }
        return assetValue - benchmarkValue
    }
}

struct BenchmarkComparisonTable: View {
    /// Column header for the asset side — the ticker symbol. Before this the asset column
    /// had no header at all; "Average Annual Return" was really a row label doing double duty.
    let assetLabel: String
    let benchmarkLabel: String
    let rows: [BenchmarkComparisonRow]

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    /// At the accessibility sizes four columns stop fitting on a 402pt screen, so the
    /// delta moves under the benchmark value instead of truncating. Measured against
    /// `AppTypography.dataCap` (1.25x) — the point where the header row starts to wrap.
    private var stacksDelta: Bool { dynamicTypeSize >= .accessibility1 }

    private var hasAnyDelta: Bool { rows.contains { $0.delta != nil } }

    var body: some View {
        Grid(alignment: .leading, horizontalSpacing: AppSpacing.sm, verticalSpacing: 0) {
            header

            ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                // A rule between rows, not around them: the card already has an edge, and
                // a trailing rule would read as a cut-off table.
                if index > 0 { rule }
                dataRow(row)
            }
        }
    }

    // MARK: - Rows

    private var header: some View {
        GridRow {
            // Empty corner cell. The row labels below carry the window, so naming this
            // column would repeat them.
            Color.clear.frame(height: 0)

            Text(assetLabel)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textSecondary)
                .lineLimit(1)
                .gridColumnAlignment(.trailing)

            Text(benchmarkLabel)
                .font(AppTypography.captionEmphasis)
                .foregroundColor(AppColors.textSecondary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
                .gridColumnAlignment(.trailing)

            if hasAnyDelta && !stacksDelta {
                Text("vs")
                    .font(AppTypography.captionEmphasis)
                    .foregroundColor(AppColors.textMuted)
                    .gridColumnAlignment(.trailing)
            }
        }
        .padding(.bottom, AppSpacing.xs)
        .accessibilityHidden(true)   // each data row reads its own full sentence below
    }

    private var rule: some View {
        // `Divider()` would be inert here: it paints `UIColor.separator` over whatever you
        // give it (theme-lint rule 3). And `AppColors.divider`, not `cardBackgroundLight`
        // — the old separator used a SURFACE token, #EDF0F5 on a #FFFFFF card, which is
        // the "add lines?" half of the tester's report: the line was already there and
        // invisible in both appearances.
        Rectangle()
            .fill(AppColors.divider)
            .frame(height: 1)
            .gridCellColumns(columnCount)
            .padding(.vertical, AppSpacing.sm)
    }

    private var columnCount: Int { (hasAnyDelta && !stacksDelta) ? 4 : 3 }

    private func dataRow(_ row: BenchmarkComparisonRow) -> some View {
        GridRow {
            VStack(alignment: .leading, spacing: 2) {
                Text(row.label)
                    .font(AppTypography.labelEmphasis)
                    .foregroundColor(AppColors.textPrimary)
                if let since = row.since {
                    Text("since \(since)")
                        // A real token, so it SCALES. The two all-time lines this replaces
                        // were `.font(.system(size: 10))`, which is fixed at every text
                        // size, at `textMuted.opacity(0.7)` — dimmer than any audited value.
                        .font(AppTypography.captionSmall)
                        .foregroundColor(AppColors.textMuted)
                        .lineLimit(2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            value(row.assetValue, emphasised: true)

            VStack(alignment: .trailing, spacing: 2) {
                if let benchmark = row.benchmarkValue {
                    value(benchmark, emphasised: false)
                } else {
                    Text("—")
                        .font(AppTypography.dataMedium)
                        .foregroundColor(AppColors.textMuted)
                }
                if stacksDelta, let delta = row.delta {
                    deltaText(delta)
                }
            }
            .gridColumnAlignment(.trailing)

            if hasAnyDelta && !stacksDelta {
                Group {
                    if let delta = row.delta {
                        deltaText(delta)
                    } else {
                        Text("—")
                            .font(AppTypography.captionEmphasis)
                            .foregroundColor(AppColors.textMuted)
                    }
                }
                .gridColumnAlignment(.trailing)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityLabel(row))
    }

    // MARK: - Cells

    private func value(_ v: Double, emphasised: Bool) -> some View {
        // `dataMedium` is the DATA tier: monospaced digits, so 8.7 and 11.4 line up on the
        // decimal point down the column. `bodySmallEmphasis` (the old choice) is a reading
        // token with proportional digits.
        Text(Self.percent(v))
            .font(AppTypography.dataMedium)
            // Both columns get the SAME weight of colour. The asset used to be
            // `textPrimary` and the benchmark `textSecondary`, which reads as "this one
            // matters more" about two numbers whose entire job is to be peers.
            .foregroundColor(AppColors.textPrimary)
            .opacity(emphasised ? 1.0 : 0.85)
            .lineLimit(1)
            .gridColumnAlignment(.trailing)
    }

    private func deltaText(_ delta: Double) -> some View {
        Text(Self.signedPoints(delta))
            .font(AppTypography.dataSmall)
            // gain/loss are the TEXT-role tokens (4.5:1 in both appearances). The *Graphic
            // variants must not appear here — see the role table in ios-swiftui.md.
            .foregroundColor(delta >= 0 ? AppColors.gain : AppColors.loss)
            .lineLimit(1)
    }

    // MARK: - Formatting

    static func percent(_ v: Double) -> String {
        String(format: "%.1f%%", v)
    }

    /// Percentage points, always signed — the sign IS the message, so "+0.9" not "0.9".
    /// A true minus sign rather than a hyphen so it aligns with the digits above it.
    static func signedPoints(_ v: Double) -> String {
        let magnitude = String(format: "%.1f", abs(v))
        return (v < 0 ? "\u{2212}" : "+") + magnitude
    }

    private func accessibilityLabel(_ row: BenchmarkComparisonRow) -> String {
        var parts = [row.label]
        if let since = row.since { parts.append("since \(since)") }
        parts.append("\(assetLabel) \(Self.percent(row.assetValue))")
        if let benchmark = row.benchmarkValue {
            parts.append("\(benchmarkLabel) \(Self.percent(benchmark))")
            if let delta = row.delta {
                let word = delta >= 0 ? "ahead by" : "behind by"
                parts.append("\(word) \(String(format: "%.1f", abs(delta))) percentage points")
            }
        } else {
            parts.append("\(benchmarkLabel) unavailable")
        }
        return parts.joined(separator: ", ")
    }
}

#Preview("Both rows") {
    VStack(spacing: AppSpacing.xl) {
        BenchmarkComparisonTable(
            assetLabel: "BRK.B",
            benchmarkLabel: "S&P 500",
            rows: [
                BenchmarkComparisonRow(label: "5-year", since: "Aug 2021",
                                       assetValue: 8.7, benchmarkValue: 11.4),
                BenchmarkComparisonRow(label: "All-time", since: "Oct 05, 2006",
                                       assetValue: 10.0, benchmarkValue: 9.1),
            ]
        )

        BenchmarkComparisonTable(
            assetLabel: "GCUSD",
            benchmarkLabel: "S&P 500",
            rows: [
                BenchmarkComparisonRow(label: "All-time", since: "May 29, 2007",
                                       assetValue: 10.7, benchmarkValue: 8.8),
            ]
        )

        // Benchmark unmeasurable — an em dash, never the placeholder 0.0.
        BenchmarkComparisonTable(
            assetLabel: "ARKK",
            benchmarkLabel: "S&P 500",
            rows: [
                BenchmarkComparisonRow(label: "All-time", since: "Oct 31, 2014",
                                       assetValue: -2.4, benchmarkValue: nil),
            ]
        )
    }
    .padding()
    .background(AppColors.background)
}
