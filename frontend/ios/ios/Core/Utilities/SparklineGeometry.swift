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

    /// The drawable rect once room is reserved for marks centred ON a data point.
    ///
    /// A sparkline's y-mapping sends the series MINIMUM to `y = height` and the
    /// maximum to `y = 0`, and the end dot is centred on the last point — so any
    /// series that closes at its own low or high lost half that dot to the canvas
    /// edge. Measured on-device: the Dow tile's dot rendered 17px tall (a full 6pt
    /// dot at 3×) while the S&P's rendered 9px with every column bottoming out on
    /// the exact last row of the canvas, because the S&P closed at its session low.
    /// The same applies horizontally to the right edge once a completed session
    /// puts the last point at `x = width`.
    ///
    /// `markInset` is the radius of the largest thing drawn at a point (the dot,
    /// or half the line width when there is no dot). Clamped to a third of the
    /// smaller dimension so a very short tile cannot collapse to zero height.
    ///
    /// Only the RIGHT edge is reserved horizontally: nothing is drawn past the
    /// last point, and keeping x=0 flush means a partial session still starts at
    /// the very left, matching the asset-detail chart.
    static func plotRect(in size: CGSize, markInset: CGFloat) -> CGRect {
        let inset = max(0, min(markInset, min(size.width, size.height) / 3))
        return CGRect(
            x: 0,
            y: inset,
            width: max(size.width - inset, .ulpOfOne),
            height: max(size.height - inset * 2, .ulpOfOne)
        )
    }

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
    /// `markInset` reserves room for whatever is drawn AT a point (see `plotRect`)
    /// so an end dot on the series' own high or low is not half-clipped by the
    /// canvas edge. Pass the dot's radius, or 0 when the caller draws no marks.
    ///
    /// A flat (zero-range) series is centered vertically rather than pinned to
    /// the bottom edge.
    static func normalizedPoints(
        _ values: [Double],
        in size: CGSize,
        spanFrom: Double = 0,
        spanTo: Double = 1,
        markInset: CGFloat = 0
    ) -> [CGPoint] {
        guard values.count > 1, size.width > 0, size.height > 0 else { return [] }
        guard values.allSatisfy({ $0.isFinite }) else { return [] }
        guard let minValue = values.min(), let maxValue = values.max() else { return [] }

        let range = maxValue - minValue
        let plot = plotRect(in: size, markInset: markInset)
        let span = clampedSpan(from: spanFrom, to: spanTo)
        let originX = plot.minX + plot.width * span.from
        let stepX = (plot.width * (span.to - span.from)) / CGFloat(values.count - 1)

        return values.enumerated().map { index, value in
            let x = originX + CGFloat(index) * stepX
            // Degenerate (flat) series → center the line instead of pinning it
            // to the bottom; otherwise scale into the plot rect.
            let fraction: CGFloat = range > 0 ? CGFloat((value - minValue) / range) : 0.5
            let y = plot.maxY - fraction * plot.height
            return CGPoint(x: x, y: y)
        }
    }
}
