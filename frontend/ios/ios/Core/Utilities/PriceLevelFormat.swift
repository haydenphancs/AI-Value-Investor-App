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
    /// 2 dp at/above $1, 6 dp down to $0.0001, 10 dp below that — then trailing zeros are
    /// trimmed back to a 2-dp floor, so the extra places appear only where the number
    /// actually uses them. Without the trim a bounded reading that happens to sit at zero
    /// (Williams %R at the top of its range) printed "0.0000000000", and a level that is
    /// genuinely 0.08 printed "0.080000" — false precision in both directions.
    ///
    /// Non-finite reads "—", never "0.00": a missing level must not look like a real one
    /// at zero.
    var asPriceLevel: String {
        guard isFinite else { return "—" }
        // IEEE negative zero: Williams %R is 0 when price sits at the period high, and the
        // library hands back -0.0, which formats as "-0.00" and reads as a bug.
        let value = self == 0 ? 0 : self
        let magnitude = Swift.abs(value)
        let decimals: Int
        if magnitude >= 1 {
            decimals = 2
        } else if magnitude >= 0.0001 {
            decimals = 6
        } else {
            decimals = 10
        }
        var text = String(format: "%.\(decimals)f", value)
        guard decimals > 2, text.contains(".") else { return text }
        while text.hasSuffix("0"),
              text.distance(from: text.firstIndex(of: ".")!, to: text.endIndex) > 3 {
            text.removeLast()
        }
        return text
    }

    /// The same number with a leading `$`.
    var asPriceLevelCurrency: String {
        isFinite ? "$" + asPriceLevel : "—"
    }
}
