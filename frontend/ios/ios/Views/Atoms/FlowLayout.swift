//
//  FlowLayout.swift
//  ios
//
//  Atom: a left-to-right layout that WRAPS its subviews onto new lines when they
//  run out of horizontal room — like text flowing in a paragraph. Use it for a
//  variable number of chips/tags (related tickers, filters) that an `HStack`
//  would otherwise squeeze onto one line until the labels became unreadable.
//
//  iOS 16+ `Layout`. Each subview is measured at its natural size, so a chip
//  keeps its own width instead of being compressed.
//

import SwiftUI

struct FlowLayout: Layout {
    /// Horizontal gap between chips on the same line.
    var spacing: CGFloat = AppSpacing.sm
    /// Vertical gap between wrapped lines.
    var lineSpacing: CGFloat = AppSpacing.sm

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) -> CGSize {
        let maxWidth = proposal.width ?? .greatestFiniteMagnitude
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var widestRow: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            // Wrap when this chip won't fit — but never wrap a row's first chip
            // (a single chip wider than the container just overflows rather than
            // vanishing).
            if x > 0, x + size.width > maxWidth {
                y += rowHeight + lineSpacing
                x = 0
                rowHeight = 0
            }
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
            widestRow = max(widestRow, x - spacing)
        }
        return CGSize(width: min(widestRow, maxWidth), height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) {
        var x = bounds.minX
        var y = bounds.minY
        var rowHeight: CGFloat = 0

        for subview in subviews {
            let size = subview.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX {
                y += rowHeight + lineSpacing
                x = bounds.minX
                rowHeight = 0
            }
            subview.place(
                at: CGPoint(x: x, y: y),
                anchor: .topLeading,
                proposal: ProposedViewSize(size)
            )
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
    }
}
