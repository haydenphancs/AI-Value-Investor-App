//
//  ChartDomain.swift
//  ios
//
//  Shared, degenerate-proof Y-domain math for the financial charts.
//
//  Every chart on the Financials tab used to build its own `min...max` domain
//  inline, and each got the same edge cases wrong in a slightly different way:
//
//   * An EMPTY series fell back to invented bounds (`?? 1`, `?? 50`) and drew a
//     fabricated axis — "1.1 / 0.8 / 0.4 / 0" — with no data behind it.
//   * A series that is legitimately ALL ZERO (a company that pays no dividend
//     and buys back no stock — very common) produced `max == min == 0`, which
//     yields `.chartYScale(domain: 0...0)`, duplicate grid values under
//     `ForEach(id: \.self)`, and a `range / 5` step of 0. `stride(by: 0)` is a
//     hard precondition failure — a crash, not a glitch.
//   * A series that is entirely NEGATIVE inverted the domain, because the
//     "add headroom" step multiplied: `-5 * 1.1 == -5.5`, which is BELOW the
//     minimum.
//
//  `ChartDomain.make` is the single place those cases are handled. It always
//  returns `lower < upper`, both finite.
//

import Foundation

enum ChartDomain {

    /// Smallest span we will ever hand to a chart. Anything tighter renders as
    /// a single line and makes normalisation divide by ~0.
    static let minimumSpan: Double = 1.0

    /// Build a safe closed range from raw values.
    ///
    /// - Parameters:
    ///   - values: the data. Non-finite entries (NaN/Inf) are discarded.
    ///   - includeZero: pin the domain to include 0 (bar charts must; line
    ///     charts of a tight percentage band need not).
    ///   - headroomFraction: proportional padding added OUTWARD at each open
    ///     end, applied additively so sign never flips the direction.
    ///   - fallback: domain to use when there is no usable data at all.
    static func make(
        _ values: [Double],
        includeZero: Bool = true,
        headroomFraction: Double = 0.15,
        fallback: ClosedRange<Double> = 0...1
    ) -> ClosedRange<Double> {
        let finite = values.filter { $0.isFinite }
        guard var lower = finite.min(), var upper = finite.max() else {
            return fallback
        }

        if includeZero {
            lower = Swift.min(lower, 0)
            upper = Swift.max(upper, 0)
        }

        // Additive headroom: `value * 1.1` moves a NEGATIVE bound the wrong way.
        let span = upper - lower
        let pad = Swift.max(span * headroomFraction, span == 0 ? minimumSpan / 2 : 0)
        if upper > 0 || span == 0 { upper += pad }
        if lower < 0 { lower -= pad }

        // Still degenerate (all values identical, e.g. every yield is 0)?
        // Open it up symmetrically so the scale, the grid step and any
        // normalisation denominator are all non-zero.
        if upper - lower < minimumSpan {
            let mid = (upper + lower) / 2
            lower = mid - minimumSpan / 2
            upper = mid + minimumSpan / 2
            if includeZero {
                lower = Swift.min(lower, 0)
                upper = Swift.max(upper, minimumSpan)
            }
        }

        return lower...upper
    }

    /// `count` evenly spaced interior grid values for a domain.
    ///
    /// Returns `[]` rather than crashing when the domain is degenerate — the
    /// old inline version did `stride(from:to:by: range/5)`, and a zero stride
    /// is a `_precondition` failure. Values are distinct, so they remain safe
    /// as `ForEach(id: \.self)` identities.
    static func gridValues(in domain: ClosedRange<Double>, count: Int) -> [Double] {
        guard count > 0 else { return [] }
        let span = domain.upperBound - domain.lowerBound
        guard span.isFinite, span > 0 else { return [] }
        let step = span / Double(count + 1)
        guard step > 0 else { return [] }
        return (1...count).map { domain.lowerBound + step * Double($0) }
    }

    /// Normalise `value` into 0...1 within `domain`, clamped. Returns
    /// `defaultValue` for a non-finite input or a degenerate domain rather than
    /// propagating a NaN into a `.frame`/`.offset` (which SwiftUI rejects).
    static func normalize(
        _ value: Double,
        in domain: ClosedRange<Double>,
        default defaultValue: Double = 0.5
    ) -> Double {
        let span = domain.upperBound - domain.lowerBound
        guard value.isFinite, span.isFinite, span > 0 else { return defaultValue }
        return Swift.min(Swift.max((value - domain.lowerBound) / span, 0), 1)
    }

    /// Bucket index for a tap at `x` across `width` split into `count` columns.
    /// Nil when the geometry or the count makes the answer meaningless — the
    /// call sites did `Int(x / pointWidth)` BEFORE range-checking, and
    /// `Int(.infinity)` / `Int(.nan)` traps.
    static func columnIndex(atX x: CGFloat, width: CGFloat, count: Int) -> Int? {
        guard count > 0, width.isFinite, width > 0, x.isFinite, x >= 0 else { return nil }
        let columnWidth = width / CGFloat(count)
        guard columnWidth > 0 else { return nil }
        let raw = (x / columnWidth).rounded(.down)
        guard raw.isFinite, raw >= 0, raw < CGFloat(count) else { return nil }
        return Int(raw)
    }
}
