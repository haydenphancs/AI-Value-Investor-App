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

    /// One point per value, fitted to `size`. Returns `[]` when there is nothing
    /// meaningful to draw — fewer than 2 points, a non-positive size, or any
    /// non-finite value (NaN / ±∞). Callers should render nothing in that case.
    ///
    /// A flat (zero-range) series is centered vertically rather than pinned to
    /// the bottom edge.
    static func normalizedPoints(_ values: [Double], in size: CGSize) -> [CGPoint] {
        guard values.count > 1, size.width > 0, size.height > 0 else { return [] }
        guard values.allSatisfy({ $0.isFinite }) else { return [] }
        guard let minValue = values.min(), let maxValue = values.max() else { return [] }

        let range = maxValue - minValue
        let stepX = size.width / CGFloat(values.count - 1)

        return values.enumerated().map { index, value in
            let x = CGFloat(index) * stepX
            // Degenerate (flat) series → center the line instead of pinning it
            // to the bottom; otherwise scale into [0, height].
            let fraction: CGFloat = range > 0 ? CGFloat((value - minValue) / range) : 0.5
            let y = size.height - fraction * size.height
            return CGPoint(x: x, y: y)
        }
    }
}
