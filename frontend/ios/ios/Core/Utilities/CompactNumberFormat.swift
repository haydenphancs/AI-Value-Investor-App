//
//  CompactNumberFormat.swift
//  ios
//
//  One compact "1.2B" / "950M" formatter for chart axes and value labels.
//

import Foundation

/// Compact magnitude formatting for chart axis ticks and bar value labels.
///
/// This exists because at least eight private `formatLargeNumber` copies had grown across
/// the view and model layers, **all with different tier rules** — and two of them sat on
/// the same screen and disagreed with each other:
///
///  * `RevenueBreakdownChartView` used `%.0f` in every tier, so an axis whose gridlines are
///    spaced at 0.25·max rendered *duplicate* labels: with a $2B maximum the five ticks read
///    `0, 0B, 1B, 2B, 2B`. Two pairs collide, and the chart appears to have a broken scale.
///  * `RevenueBreakdownModels` — the copy the axis was supposed to match — was itself
///    inconsistent (`%.1fT`, `%.0fB`, `%.1fM`, `%.1fK`): a decimal everywhere *except*
///    billions, which is the tier most large caps actually land in.
///  * `GrowthChartView` printed whole `B`/`M`/`K` directly above a YoY row that keeps one
///    decimal, so the two rows of the same chart contradicted each other on screen.
///
/// The rule here is a single one: **keep one decimal while the scaled value is below 10, and
/// drop to whole units at or above it.** That is where precision actually matters — `1.2B`
/// vs `1B` is a 20% difference, while `847B` vs `847.3B` is noise — and it is exactly the
/// threshold that stops adjacent gridlines from rounding onto the same string.
enum CompactNumberFormat {

    /// `1.2B`, `847M`, `9.5K`, `-3.4B`, `0`.
    ///
    /// Returns `"—"` for a non-finite input: `nan`/`inf` reach chart code from upstream data
    /// often enough that formatting them as text ("nan") would ship a broken label rather
    /// than an honest blank.
    static func string(_ value: Double) -> String {
        guard value.isFinite else { return "—" }

        let magnitude = abs(value)
        let sign = value < 0 ? "-" : ""

        if magnitude >= 1_000_000_000_000 {
            return sign + scaled(magnitude / 1_000_000_000_000) + "T"
        }
        if magnitude >= 1_000_000_000 {
            return sign + scaled(magnitude / 1_000_000_000) + "B"
        }
        if magnitude >= 1_000_000 {
            return sign + scaled(magnitude / 1_000_000) + "M"
        }
        if magnitude >= 1_000 {
            return sign + scaled(magnitude / 1_000) + "K"
        }
        // Sub-thousand values are already short; one decimal below 10 keeps a small axis
        // from collapsing several ticks onto "0".
        return sign + scaled(magnitude)
    }

    /// One decimal below 10, whole units at or above it — and never a trailing `.0`,
    /// so `2.0B` prints as `2B` and sits flush with its neighbours on an axis.
    private static func scaled(_ v: Double) -> String {
        if v >= 10 {
            return String(format: "%.0f", v)
        }
        let oneDecimal = (v * 10).rounded() / 10
        if oneDecimal == oneDecimal.rounded() {
            return String(format: "%.0f", oneDecimal)
        }
        return String(format: "%.1f", oneDecimal)
    }

    /// A percentage that may be unbounded. Percent surprise explodes toward a near-zero
    /// consensus estimate, so beyond ±1000% this switches to a compact form (`-15.8k%`)
    /// rather than clamping — a clamped caption over an *unclamped* plot makes the number
    /// and the bar geometry disagree, which is worse than an unusual-looking label.
    static func percentString(_ value: Double) -> String {
        guard value.isFinite else { return "—" }

        let magnitude = abs(value)
        let sign = value < 0 ? "-" : ""

        if magnitude >= 1_000_000 {
            return sign + scaled(magnitude / 1_000_000) + "M%"
        }
        if magnitude >= 1_000 {
            return sign + scaled(magnitude / 1_000) + "k%"
        }
        return "\(Int(value.rounded()))%"
    }
}
