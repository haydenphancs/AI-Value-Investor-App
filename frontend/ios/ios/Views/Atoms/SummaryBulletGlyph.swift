//
//  SummaryBulletGlyph.swift
//  ios
//
//  Atom: the leading marker of an AI summary bullet — a dot, or a turn-down
//  arrow for the conclusion.
//

import SwiftUI

/// The marker at the head of a summary bullet.
///
/// The final bullet of every AI summary is the conclusion. It used to announce
/// itself in words — *"The takeaway for everyday investors, …"* — which is eleven
/// words of scaffolding in front of the point on a card built to be skimmed. The
/// arrow says the same thing in one glyph: it points back at the bullets above and
/// reads as "and so".
///
/// Why this is an atom rather than three inline `Circle()`s:
///
/// 1. **An SF Symbol is roughly twice the dot's width.** Swapping one in place
///    would push the last bullet's text to the right and leave the list with a
///    ragged left edge, so both markers have to share one fixed column.
/// 2. **The vertical centring is the easy thing to get wrong.** The old inline
///    version hardcoded `.padding(.top, 6)` to centre a 5pt dot on a 14pt line.
///    That constant is wrong for the news card (12pt text) and drifts at large
///    Dynamic Type, because icons are capped at 1.25x while reading text is capped
///    at 1.4x. Here the column takes its height from an invisible line of the
///    NEIGHBOURING text instead, so it re-centres itself at every type size and on
///    every surface with no constant to maintain.
struct SummaryBulletGlyph: View {
    /// The last bullet — rendered as the turn-down arrow.
    var isConclusion: Bool = false
    var color: Color = AppColors.textSecondary
    /// The font of the text this glyph sits beside. Drives the centring; pass the
    /// same font the adjacent `Text` uses.
    var textFont: Font = AppTypography.bodySmall

    /// One column for both markers, so every bullet's text starts at the same x.
    static let columnWidth: CGFloat = 12

    var body: some View {
        // A hidden line of the neighbouring text sets this column's height, which
        // is what makes the centring track Dynamic Type. `.hidden()` removes the
        // space from the drawing but keeps its layout, and the overlay draws over
        // it — so the marker lands on the text line's optical centre.
        Text(" ")
            .font(textFont)
            .hidden()
            .overlay(marker)
            .frame(width: Self.columnWidth)
            .accessibilityHidden(true)
    }

    @ViewBuilder
    private var marker: some View {
        if isConclusion {
            // `arrow.turn.down.right` is iOS 13 — far below the 18.0 deployment
            // target, so it needs no `#available` gate. It is also unused anywhere
            // else in the app: the sparkle is the AI provenance mark, the bolt is
            // "Why it moved", and the lightbulb is the Wiser tab.
            Image(systemName: "arrow.turn.down.right")
                .font(AppTypography.iconTiny)
                .foregroundColor(color)
        } else {
            Circle()
                .fill(color)
                .frame(width: 5, height: 5)
        }
    }
}

#Preview("Bullets with a conclusion") {
    VStack(alignment: .leading, spacing: AppSpacing.sm) {
        ForEach(Array([
            "AI continues to influence sectors beyond tech, with some analysts seeing the boom as a concentrated risk.",
            "Investors await the Jackson Hole speech for clues on interest rates.",
            "While AI drives innovation, watch for broader economic signals from the Fed.",
        ].enumerated()), id: \.offset) { index, text in
            HStack(alignment: .top, spacing: AppSpacing.sm) {
                SummaryBulletGlyph(isConclusion: index == 2)
                Text(text)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }
    .padding()
    .background(AppColors.cardBackground)
}
