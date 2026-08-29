//
//  PersonaCard.swift
//  ios
//
//  Molecule: Persona selection card with icon, name, and tagline
//

import SwiftUI

struct PersonaCard: View {
    let persona: AnalysisPersona
    var isSelected: Bool = false
    var onTap: (() -> Void)?

    // Card dimensions. `cardHeight` is a FLOOR, not a fixed height — see the frame below.
    private let cardWidth: CGFloat = 100
    private let cardHeight: CGFloat = 120

    /// Two-line name for the card. Computed on the model so the "drop the article,
    /// last word on its own line" rule lives in one place.
    private var nameLines: (top: String, bottom: String) { persona.cardNameLines }

    var body: some View {
        Button(action: {
            onTap?()
        }) {
            VStack(spacing: AppSpacing.sm) {
                // Persona Icon
                PersonaIcon(
                    persona: persona,
                    size: 48,
                    isSelected: isSelected
                )

                // Name (split into two lines)
                VStack(spacing: 0) {
                    // Omitted for a one-word name, so the card doesn't reserve a line
                    // for an empty string.
                    if !nameLines.top.isEmpty {
                        Text(nameLines.top)
                            .font(AppTypography.labelSmall)
                            .fontWeight(.semibold)
                            .foregroundColor(AppColors.textPrimary)
                            .lineLimit(1)
                            // 0.75, not 0.8, and derived rather than felt. "Everyday Growth"
                            // measured 100.1pt of 12pt semibold in a 100pt box — it overflowed
                            // at the DEFAULT text size, with no Dynamic Type involved, by a
                            // tenth of a point. A margin that thin is decided by how a given
                            // SwiftUI release rounds text measurement, which is why one tester
                            // saw "Everyday Grow…" while another saw the full name on the same
                            // build. The longest line that remains ("Concentrator") needs 0.95
                            // at the 1.4x cap, so 0.75 is headroom, not a shrink.
                            .minimumScaleFactor(0.75)
                            .allowsTightening(true)
                    }

                    Text(nameLines.bottom)
                        .font(AppTypography.labelSmall)
                        .fontWeight(.semibold)
                        .foregroundColor(AppColors.textPrimary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.75)
                        .allowsTightening(true)
                }

                // Tagline — reserves two lines so short taglines still align across cards
                Text(persona.tagline)
                    .font(AppTypography.caption)
                    .foregroundColor(persona.accentColor)
                    .multilineTextAlignment(.center)
                    .lineLimit(2)
                    // This was the card's ONLY Text with no scale floor, so truncation was
                    // the single remedy its modifier chain allowed. "Growth at a Reasonable
                    // Price" needs 205.9pt of run at the 1.4x cap against the 200pt that two
                    // 100pt lines provide — it overflows by 6pt and had nothing to give.
                    // 0.70 is what a RENDER proved, not what arithmetic suggested:
                    // 0.85 still truncated to "Reasonable Pri…" at the cap, and
                    // `fixedSize` alone did not save it either. The floor has to be
                    // low enough that the two lines can break at a word boundary —
                    // "Reasonable" is 10 characters that cannot be split.
                    .minimumScaleFactor(0.70)
                    .allowsTightening(true)
                    // Take the full ideal height for the proposed width, so a bad
                    // height proposal cannot force truncation. Same idiom as
                    // AnalysisDescriptionCard and PersonasSheet use for persona prose.
                    .fixedSize(horizontal: false, vertical: true)
                    // Two lines of `caption` occupy 28pt at the default content size but
                    // ~31pt at the 1.4x cap, so a hard `height: 28` clipped the second line
                    // outright at any raised size. A floor keeps the reserved space (short
                    // taglines still align across cards) without capping growth.
                    .frame(minHeight: 28, alignment: .top)

                Spacer(minLength: 0)
            }
            // Height is a FLOOR, not a fixed size. See the header of RelatedTickerCard.swift
            // for the full rationale: a `.frame(height:)` centres an oversized child, so text
            // that outgrows the box bleeds off the top AND bottom edges. `maxHeight: .infinity`
            // lets the card take the height the parent HStack resolves, which keeps interior
            // Spacers working (so nothing moves at the default content size) and keeps every
            // card in the row the same height. Parent uses `HStack(alignment: .top)` to match.
            //
            // ⚠️ `.frame` deliberately comes BEFORE `.padding` on this card: the padding sits
            // OUTSIDE the box, making the real card 116x144. Reordering would shrink it.
            .frame(minWidth: cardWidth, maxWidth: cardWidth,
                   minHeight: cardHeight, maxHeight: .infinity, alignment: .top)
            .padding(.vertical, AppSpacing.md)
            .padding(.horizontal, AppSpacing.sm)
            .background(
                RoundedRectangle(cornerRadius: AppCornerRadius.large)
                    .cardFill(isSelected ? persona.accentColor.opacity(0.15) : AppColors.cardBackground)
                    .overlay(
                        RoundedRectangle(cornerRadius: AppCornerRadius.large)
                            .stroke(
                                isSelected ? persona.accentColor : Color.clear,
                                lineWidth: 2
                            )
                    )
            )
        }
        .buttonStyle(PlainButtonStyle())
    }
}

#Preview {
    ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: AppSpacing.md) {
            ForEach(AnalysisPersona.allCases) { persona in
                PersonaCard(
                    persona: persona,
                    isSelected: persona == .warrenBuffett
                )
            }
        }
        .padding()
    }
    .background(AppColors.background)
}
