//
//  CreditHistoryRow.swift
//  ios
//
//  Molecule: one credit movement as a compact, two-line row that draws its own SEGMENT of a
//  rounded per-day group.
//
//  WHY (developer request, 2026-09-24, from a screenshot of this screen): each movement was a
//  full `ActivityRow` card — 40pt glyph circle, three lines, 16pt padding, ~100pt tall, ~7 to
//  a screen. Heavy users scroll this list the most. Now:
//
//      ⊖  Deep research report · AAPL      −20
//         4:12 PM · 20 purchased
//
//  ~52pt, ~13 to a screen. `ActivityRow` itself is unchanged — Alerts, notifications and the
//  price rules still render through it.
//
//  WHY A SEGMENT AND NOT A GROUP VIEW. The screen's rows must stay DIRECT children of its one
//  `LazyVStack`: a per-day `VStack` card would be a lazy child that GROWS IN PLACE when a
//  "Load more" appends rows to that day — the documented 100%-CPU hang (see the header of
//  `CreditHistoryView.swift`). So each row paints its own part of the card:
//
//  • FILL: `UnevenRoundedRectangle`, top corners rounded only for the first row of the day,
//    bottom corners only for the last.
//  • EDGE: light mode needs `cardEdge` (a white card on the #F4F5F8 page is 1.09:1). Each row
//    strokes its OWN shape, extended 1pt past any side that has a neighbour, then `.clipped()`
//    — so the shared top/bottom edges fall outside the frame and only the group's OUTLINE
//    survives. Dark draws no edge at all (`cardEdge` is transparent there), as everywhere.
//  • DIVIDER: an inset `AppColors.divider` hairline above every row but the first.
//
//  Copy is backend-authored and rendered verbatim (`title`, `subtitle`, `poolNote`); this only
//  composes it with " · ". See `Models/CreditHistoryModels.swift`.
//

import SwiftUI

struct CreditHistoryRow: View {
    let item: CreditTransactionDTO
    let position: RowGroupPosition

    /// Glyph column + gap, so the divider starts where the text does (the iOS inset style)
    /// and the titles of adjacent rows line up whatever their glyph.
    private static let glyphWidth: CGFloat = 20
    private static let dividerInset: CGFloat = AppSpacing.md + glyphWidth + AppSpacing.sm

    var body: some View {
        HStack(alignment: .center, spacing: AppSpacing.sm) {
            Image(systemName: item.iconName)
                .font(AppTypography.iconSmall)
                .foregroundColor(item.iconColor)
                .frame(width: Self.glyphWidth)
                .accessibilityHidden(true)

            // NO line limit on either line. The detail (the ticker) sits at the END of the
            // headline, so a cap puts the ellipsis exactly on the part the row exists to show —
            // measured: "Refund · report didn't finish · G…" at the 1.4x text cap on a 320pt
            // screen, and "15 monthly + 5 purc…" on the second line. At default size both stay
            // one line each; a taller row at large text is still a FIXED height per row (its
            // copy never changes after layout), so this adds no in-place resize.
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                headline
                    .fixedSize(horizontal: false, vertical: true)

                if let meta = item.metaLine {
                    Text(meta)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textMuted)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            Spacer(minLength: AppSpacing.sm)

            // Never truncated or squeezed: the amount is the one thing on a statement row that
            // must always be whole.
            TintedTagBadge(text: item.amountText, color: item.amountColor)
                .fixedSize()
                .layoutPriority(1)
        }
        .padding(.horizontal, AppSpacing.md)
        .padding(.vertical, AppSpacing.sm)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            segmentShape
                .fill(AppColors.cardBackground)
        )
        .overlay(
            // Extended past each shared side so `.clipped()` below removes those edges and
            // leaves only the group's outline. See the header.
            segmentShape
                .strokeBorder(AppColors.cardEdge, lineWidth: 1)
                .padding(.top, position.hasRowAbove ? -1 : 0)
                .padding(.bottom, position.hasRowBelow ? -1 : 0)
        )
        .overlay(alignment: .top) {
            if position.hasRowAbove {
                AppColors.divider
                    .frame(height: 1)
                    .padding(.leading, Self.dividerInset)
            }
        }
        .clipped()
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
    }

    /// "Deep research report · AAPL" — the detail in secondary ink so the title still leads.
    private var headline: Text {
        let title = Text(item.title)
            .font(AppTypography.body)
            .foregroundColor(AppColors.textPrimary)
        guard let detail = item.detail else { return title }
        return title + Text(" · \(detail)")
            .font(AppTypography.body)
            .foregroundColor(AppColors.textSecondary)
    }

    private var segmentShape: UnevenRoundedRectangle {
        let r = AppCornerRadius.large
        let top: CGFloat = position.hasRowAbove ? 0 : r
        let bottom: CGFloat = position.hasRowBelow ? 0 : r
        return UnevenRoundedRectangle(
            cornerRadii: RectangleCornerRadii(
                topLeading: top, bottomLeading: bottom, bottomTrailing: bottom, topTrailing: top
            ),
            style: .continuous
        )
    }

    /// Spelled out: the display amount uses U+2212, and the meta line's " · " separators read
    /// as pauses rather than "dot".
    private var accessibilityText: String {
        [item.title, item.detail, item.metaLine, item.accessibilityAmount]
            .compactMap { $0 }
            .joined(separator: ", ")
    }
}

#Preview {
    let rows: [CreditTransactionDTO] = [
        CreditTransactionDTO(id: "1", createdAt: "2026-09-24T18:48:00Z", delta: -1, kind: "spend",
                             title: "Ask Cay AI", poolNote: "1 purchased", reason: "chat_charge"),
        CreditTransactionDTO(id: "2", createdAt: "2026-09-24T16:12:00Z", delta: -20, kind: "spend",
                             title: "Deep research report", subtitle: "AAPL",
                             poolNote: "20 purchased", reason: "report_charge"),
        CreditTransactionDTO(id: "3", createdAt: "2026-09-24T15:00:00Z", delta: -1, kind: "spend",
                             title: "Ask Cay AI", isReversed: true, reason: "chat_charge"),
        CreditTransactionDTO(id: "4", createdAt: "2026-09-24T14:59:00Z", delta: 1, kind: "refund",
                             title: "Refund · answer was already cached", reason: "chat_cache_hit"),
    ]
    return ScrollView {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                CreditHistoryRow(item: row, position: RowGroupPosition(index: index, count: rows.count))
            }
            Spacer().frame(height: AppSpacing.lg)
            CreditHistoryRow(
                item: CreditTransactionDTO(id: "5", delta: 540, kind: "purchase", title: "Credit pack",
                                           subtitle: "Power", poolNote: "Never expires",
                                           reason: "pack_purchase"),
                position: .only
            )
        }
        .padding(AppSpacing.lg)
    }
    .background(AppColors.background)
}
