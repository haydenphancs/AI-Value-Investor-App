//
//  SparklineGeometry.swift
//  ios
//
//  Pure, SwiftUI-free geometry for the Home sparklines so the render math is
//  unit-testable in isolation (no test target exists yet — this also lets the
//  logic be exercised by a standalone Swift harness without drift).
//
//  Maps a value series into view-space points (origin top-left, y grows
//  downward) where a LARGER value sits HIGHER on screen.
//

import CoreGraphics

enum SparklineGeometry {

    /// Clamp a server-supplied `(from, to)` session span into a drawable range.
    ///
    /// The pair says where an intraday series sits inside its own trading session,
    /// as fractions of the available width. Anything unusable — non-finite, out of
    /// `[0, 1]`, inverted, or zero-width — falls back to the FULL span, which is
    /// exactly how these charts drew before spans existed. That direction matters:
    /// a bad span must never SHRINK a chart that used to render fine.
    static func clampedSpan(from: Double, to: Double) -> (from: CGFloat, to: CGFloat) {
        guard from.isFinite, to.isFinite,
              from >= 0, to <= 1, to > from else {
            return (0, 1)
        }
        return (CGFloat(from), CGFloat(to))
    }

    /// One point per value, fitted to `size`. Returns `[]` when there is nothing
    /// meaningful to draw — fewer than 2 points, a non-positive size, or any
    /// non-finite value (NaN / ±∞). Callers should render nothing in that case.
    ///
    /// `spanFrom` / `spanTo` place the series inside its trading session: the
    /// points are laid out between `width * spanFrom` and `width * spanTo`, so a
    /// partial day occupies only the fraction it has actually traded and leaves
    /// the rest of the chart empty. Defaulting to the full width keeps every
    /// caller that has no session information rendering as before.
    ///
    /// Without this the points always spanned edge to edge, which made a 10:15
    /// morning pixel-identical to a completed session — the card claimed the day
    /// was over while the price beside it was still moving.
    ///
    /// A flat (zero-range) series is centered vertically rather than pinned to
    /// the bottom edge.
    static func normalizedPoints(
        _ values: [Double],
        in size: CGSize,
        spanFrom: Double = 0,
        spanTo: Double = 1
    ) -> [CGPoint] {
        guard values.count > 1, size.width > 0, size.height > 0 else { return [] }
        guard values.allSatisfy({ $0.isFinite }) else { return [] }
        guard let minValue = values.min(), let maxValue = values.max() else { return [] }

        let range = maxValue - minValue
        let span = clampedSpan(from: spanFrom, to: spanTo)
        let originX = size.width * span.from
        let stepX = (size.width * (span.to - span.from)) / CGFloat(values.count - 1)

        return values.enumerated().map { index, value in
            let x = originX + CGFloat(index) * stepX
            // Degenerate (flat) series → center the line instead of pinning it
            // to the bottom; otherwise scale into [0, height].
            let fraction: CGFloat = range > 0 ? CGFloat((value - minValue) / range) : 0.5
            let y = size.height - fraction * size.height
            return CGPoint(x: x, y: y)
        }
    }
}
