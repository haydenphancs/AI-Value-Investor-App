//
//  SignalDollarFormat.swift
//  ios
//
//  The dollar figure on the Home "CEO Buys" signal card ("$46.8M bought") and its
//  drill-down rows.
//
//  ⚠️ FOUNDATION ONLY — no `import SwiftUI`. There is no XCTest target, so this is executed
//  by piping it into `xcrun swift -` from `backend/tests/test_ios_signal_kinds_parity.py`.
//
//  Why not an existing formatter (home E2, 2026-09-23):
//   * `CompactNumberFormat.string` drops to WHOLE units at ≥ 10, so FOX ($10.3M) and UBER
//     ($10.0M) — adjacent rows of a list ranked BY dollars — would both read "$10M".
//   * `CongressActivity.formatDollarCompact` prints "$nan" for NaN, and both helpers print
//     "$1000K" for $999,999 because the unit is chosen before rounding.
//  So: one decimal (a trailing ".0" dropped), and the unit is chosen AFTER rounding.
//

import Foundation

/// `nonisolated`: the target defaults to MainActor isolation, and the drill-down's DTO
/// mapping (`SignalHolderDTO.toDisplay`) calls this from a nonisolated context.
nonisolated enum SignalDollarFormat {

    private static let units: [(scale: Double, suffix: String)] = [
        (1_000, "K"), (1_000_000, "M"), (1_000_000_000, "B"), (1_000_000_000_000, "T"),
    ]

    /// `$46.8M`, `$10M`, `$100K`, `$1.2K`, `$512`; `"—"` for a non-finite or
    /// non-positive amount (a buy is never ≤ 0 — showing "$0" would be a fabrication).
    static func compact(_ value: Double) -> String {
        guard value.isFinite, value > 0 else { return "—" }
        // Step up a unit only when the value, rounded as it would PRINT in the current
        // unit, reaches 1,000 of it: $999,940 stays "$999.9K", $999,960 becomes "$1M".
        // (Rounding in the LARGER unit instead promotes too early: 0.99994M → "1.0M".)
        var scaled = value
        var suffix = ""
        for unit in units {
            guard roundedOneDecimal(value / (unit.scale / 1_000)) >= 1_000 else { break }
            scaled = value / unit.scale
            suffix = unit.suffix
        }
        let r = roundedOneDecimal(scaled)
        let digits = r == r.rounded() ? String(format: "%.0f", r) : String(format: "%.1f", r)
        return "$\(digits)\(suffix)"
    }

    /// "1.2M sh @ $24.10" — the drill-down's secondary line. nil when either side is
    /// unusable, so the row shows nothing rather than "@ $inf" or a division by zero.
    static func sharesAtPrice(shares: Double?, amount: Double?) -> String? {
        guard let shares, let amount, shares.isFinite, amount.isFinite, shares > 0, amount > 0
        else { return nil }
        let average = amount / shares
        guard average.isFinite, average > 0 else { return nil }
        let count = compact(shares).dropFirst()   // reuse the unit logic, minus the "$"
        return "\(count) sh @ \(String(format: "$%.2f", average))"
    }

    private static func roundedOneDecimal(_ v: Double) -> Double {
        (v * 10).rounded() / 10
    }
}
