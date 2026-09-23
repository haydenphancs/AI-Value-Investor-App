//
//  PriceLevelFormat.swift
//  ios
//
//  Magnitude-aware formatting for the price LEVELS on the Technical Analysis sheet.
//

import Foundation

extension Double {
    /// A price level — pivot, Fibonacci, support/resistance — as text, with the decimals
    /// its magnitude needs.
    ///
    /// The mirror of the backend's `_round_price` (`technical_analysis_service.py`), and it
    /// has to be: that helper already sends 6 or 10 decimals for a sub-dollar asset, and a
    /// flat `String(format: "%.2f", …)` here threw every one of them away. DOGE's pivots
    /// rendered "0.12 / 0.11 / 0.11 / 0.10 / 0.09 / 0.08 / 0.08" — three pairs of identical
    /// levels and a support band indistinguishable from the pivot — and SHIB's whole table
    /// would read 0.00. The backend fix (2026-08-21) never reached the display.
    ///
    /// 2 dp at/above $1, 6 dp down to $0.0001, 10 dp below that. Non-finite reads "—",
    /// never "0.00": a missing level must not look like a real one at zero.
    var asPriceLevel: String {
        guard isFinite else { return "—" }
        let magnitude = Swift.abs(self)
        let decimals: Int
        if magnitude >= 1 {
            decimals = 2
        } else if magnitude >= 0.0001 {
            decimals = 6
        } else {
            decimals = 10
        }
        return String(format: "%.\(decimals)f", self)
    }

    /// The same number with a leading `$`.
    var asPriceLevelCurrency: String {
        isFinite ? "$" + asPriceLevel : "—"
    }
}
