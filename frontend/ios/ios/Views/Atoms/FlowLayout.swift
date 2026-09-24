//
//  FlowLayout.swift
//  ios
//
//  Atom: a left-to-right layout that WRAPS its subviews onto new lines when they
//  run out of horizontal room — like text flowing in a paragraph. Use it for a
//  variable number of chips/tags (related tickers, filters) that an `HStack`
//  would otherwise squeeze onto one line until the labels became unreadable.
//
//  iOS 16+ `Layout`. Each subview is offered its ideal (one-line) width, capped at the
//  row's width, with the height left free: a chip keeps its own width instead of being
//  compressed, and a child wider than the row (a long label, a sentence, anything at an
//  AX text size) wraps inside it and grows taller instead of overflowing the column.
//
//  ⚠️ Never measure or place a child with `.unspecified` alone. That is what this atom
//  did until 2026-09-24: every child was laid out at its one-line width, so a long chip
//  label ran past its card. `.unspecified` is used ONLY to read the ideal width, which is
//  then capped. Pinned by `backend/tests/test_ios_flow_layout_guards.py`.
//

import SwiftUI

struct FlowLayout: Layout {
    /// Horizontal gap between chips on the same line.
    var spacing: CGFloat = AppSpacing.sm
    /// Vertical gap between wrapped lines.
    var lineSpacing: CGFloat = AppSpacing.sm

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) -> CGSize {
        arrange(proposal: proposal, subviews: subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout Void) {
        // The same arrangement `sizeThatFits` reported, from the same proposal, so the rows
        // placed here are the rows the parent sized us for — never re-derived from `bounds`.
        let arrangement = arrange(proposal: proposal, subviews: subviews)
        for (subview, item) in zip(subviews, arrangement.items) {
            subview.place(
                at: CGPoint(x: bounds.minX + item.origin.x, y: bounds.minY + item.origin.y),
                anchor: .topLeading,
                proposal: item.proposal
            )
        }
    }

    // MARK: - Geometry

    private struct Item {
        /// Top-leading corner, relative to the layout's own origin.
        let origin: CGPoint
        /// What the child was measured with — and must be placed with.
        let proposal: ProposedViewSize
    }

    private struct Arrangement {
        let items: [Item]
        let size: CGSize
    }

    /// The one place the geometry is decided; `sizeThatFits` and `placeSubviews` both read it.
    private func arrange(proposal: ProposedViewSize, subviews: Subviews) -> Arrangement {
        // nil = "use your ideal width" (a horizontal ScrollView, `.fixedSize()`): one row.
        let maxWidth = proposal.width ?? .infinity
        var items: [Item] = []
        items.reserveCapacity(subviews.count)
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var widestRow: CGFloat = 0

        for subview in subviews {
            // Ideal width, but never wider than the row; height free, so a child that does
            // not fit on one line wraps and reports the taller size it actually needs.
            let childProposal = ProposedViewSize(
                width: min(subview.sizeThatFits(.unspecified).width, maxWidth),
                height: nil
            )
            let size = subview.sizeThatFits(childProposal)
            // Wrap when this chip won't fit — but never wrap a row's first chip (it would
            // leave an empty row above it).
            if x > 0, x + size.width > maxWidth {
                y += rowHeight + lineSpacing
                x = 0
                rowHeight = 0
            }
            items.append(Item(origin: CGPoint(x: x, y: y), proposal: childProposal))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
            widestRow = max(widestRow, x - spacing)
        }
        // Capped at the row width: only a child that cannot get narrower than the column
        // (one unbreakable word at an AX size) can still exceed it, and reporting that width
        // would push the whole parent stack past the screen edge.
        return Arrangement(items: items, size: CGSize(width: min(widestRow, maxWidth), height: y + rowHeight))
    }
}

#Preview {
    VStack(alignment: .leading, spacing: AppSpacing.lg) {
        // Short chips pack onto rows; the long label wraps inside the 220pt column.
        FlowLayout {
            ForEach(["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN"], id: \.self) { symbol in
                TintedTagBadge(text: symbol, color: AppColors.textSecondary, backgroundOpacity: 0.10)
            }
            TintedTagBadge(
                text: "A chip label long enough to need a second line in this column",
                color: AppColors.textSecondary,
                backgroundOpacity: 0.10,
                textLineLimit: 2
            )
        }
        .frame(width: 220, alignment: .leading)
        .border(AppColors.divider)
    }
    .padding()
    .background(AppColors.background)
}
