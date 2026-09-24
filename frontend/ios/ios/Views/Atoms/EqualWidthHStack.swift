//
//  EqualWidthHStack.swift
//  ios
//
//  Atom: a horizontal row in which every subview takes the width of the WIDEST and the
//  height of the TALLEST. Built for the Home Market Pulse / Holdings strips (TestFlight
//  2026-09-23: "I need all these card should have a same width") — an `HStack` sized each
//  tile to its own label, so "Nasdaq Composite ETF" stood out, and at larger text sizes
//  "Russell 2000 ETF" or a six-figure Bitcoin price did too.
//
//  Still CONTENT-derived, never a hard width: a hard `width: 88` truncated 9-character
//  prices at larger Dynamic Type, so the row grows with the text instead of clipping it.
//
//  Contract for children: a child must FILL the cell it is given (e.g.
//  `.frame(minWidth:…, maxWidth: .infinity, maxHeight: .infinity)`). With `minWidth` alone a
//  narrower child keeps its own width inside the cell and the row still looks uneven.
//
//  Scroll content only: the layout ignores the size its parent proposes and always reports
//  n × cell + gaps, so inside a fixed-width container it overflows rather than compressing.
//  That is the point inside a horizontal `ScrollView`, which proposes no width at all.
//
//  Not lazy, like the `HStack` it replaces: every tile is measured to find the widest —
//  never swap in a `LazyHStack` (off-screen tiles cannot be measured).
//
//  Right-to-left: SwiftUI mirrors a custom Layout's placements automatically, so
//  `placeSubviews` writes left-to-right positions and must NOT read `layoutDirection`
//  (doing so would mirror twice). The arithmetic lives in `EqualWidthGeometry`.
//

import SwiftUI

struct EqualWidthHStack: Layout {
    /// Gap between neighbouring cells.
    var spacing: CGFloat = AppSpacing.sm

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) -> CGSize {
        EqualWidthGeometry.rowSize(cell: cell(for: subviews), count: subviews.count, spacing: spacing)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) {
        let cell = cell(for: subviews)
        for (index, subview) in subviews.enumerated() {
            subview.place(
                at: CGPoint(
                    x: bounds.minX + EqualWidthGeometry.originX(ofCell: index, cellWidth: cell.width, spacing: spacing),
                    y: bounds.minY
                ),
                anchor: .topLeading,
                proposal: ProposedViewSize(cell)
            )
        }
    }

    /// Measured at each child's IDEAL size, never with the incoming proposal: a
    /// `maxWidth: .infinity` child answers an infinite probe with infinity, and a horizontal
    /// ScrollView proposes no width anyway.
    private func cell(for subviews: Subviews) -> CGSize {
        EqualWidthGeometry.cellSize(fitting: subviews.map { $0.sizeThatFits(.unspecified) })
    }
}

#Preview("Equal widths") {
    ScrollView(.horizontal) {
        EqualWidthHStack(spacing: 10) {
            ForEach(["S&P 500 ETF", "Nasdaq ETF", "Russell 2000 ETF"], id: \.self) { label in
                Text(label)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .padding(10)
                    .background(AppColors.cardBackground)
            }
        }
        .padding()
    }
    .background(AppColors.background)
}

#Preview("Right-to-left") {
    ScrollView(.horizontal) {
        EqualWidthHStack(spacing: 10) {
            ForEach(["One", "Two, longer", "Three"], id: \.self) { label in
                Text(label)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .padding(10)
                    .background(AppColors.cardBackground)
            }
        }
        .padding()
    }
    .environment(\.layoutDirection, .rightToLeft)
    .background(AppColors.background)
}
