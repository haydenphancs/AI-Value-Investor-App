//
//  EqualWidthGeometry.swift
//  ios
//
//  The arithmetic behind `EqualWidthHStack`: every tile in a row gets the width of the
//  widest and the height of the tallest.
//
//  ⚠️ NO `import SwiftUI` — CoreGraphics only. There is no XCTest target, so the only way to
//  EXECUTE this in CI is to pipe the file into `xcrun swift -` from
//  `backend/tests/test_ios_market_pulse_equal_width.py`; a SwiftUI import makes it
//  unrunnable there. The `Layout` itself stays a thin wrapper that calls these.
//
//  `nonisolated`: the app target defaults to MainActor isolation, but `Layout`'s methods
//  are not main-actor isolated, so helpers they call must not be either.
//

import CoreGraphics

nonisolated enum EqualWidthGeometry {

    /// The largest FINITE width and, independently, the largest finite height, each rounded
    /// UP to a whole point. Rounding up can never clip (every tile gets the same cell), and
    /// whole points keep tile edges on the pixel grid. Non-finite or negative sizes — a
    /// flexible view answering an infinite probe — are ignored, never propagated.
    static func cellSize(fitting sizes: [CGSize]) -> CGSize {
        var width: CGFloat = 0
        var height: CGFloat = 0
        for size in sizes {
            if size.width.isFinite, size.width > width { width = size.width }
            if size.height.isFinite, size.height > height { height = size.height }
        }
        return CGSize(width: width.rounded(.up), height: height.rounded(.up))
    }

    /// The whole row: `count` cells plus the gaps BETWEEN them. `.zero` for an empty row —
    /// not `-spacing`, which is what `n·w + (n−1)·s` gives at n = 0.
    static func rowSize(cell: CGSize, count: Int, spacing: CGFloat) -> CGSize {
        guard count > 0 else { return .zero }
        let width = sanitized(cell.width)
        let height = sanitized(cell.height)
        let gap = sanitized(spacing)
        let n = CGFloat(count)
        return CGSize(width: width * n + gap * (n - 1), height: height)
    }

    /// The leading x of cell `index`, relative to the row's leading edge.
    static func originX(ofCell index: Int, cellWidth: CGFloat, spacing: CGFloat) -> CGFloat {
        CGFloat(max(index, 0)) * (sanitized(cellWidth) + sanitized(spacing))
    }

    private static func sanitized(_ value: CGFloat) -> CGFloat {
        value.isFinite ? max(value, 0) : 0
    }
}
