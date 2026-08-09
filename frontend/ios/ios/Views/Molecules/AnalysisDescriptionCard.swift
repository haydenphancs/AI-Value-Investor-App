//
//  AnalysisDescriptionCard.swift
//  ios
//
//  Molecule: Analysis style description card
//

import SwiftUI

struct AnalysisDescriptionCard: View {
    let persona: AnalysisPersona

    /// "Quality Style Analysis". Keyed on the persona's STYLE word, not the last word
    /// of the display name — that derivation dated from the real-surname names and
    /// had been reading "Compounder Style Analysis" / "Seeker Style Analysis" since
    /// the rename.
    private var styleTitle: String {
        "\(persona.shortName) Style Analysis"
    }

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Accent line. Deliberately `accentColor` and NOT `accentFill`: this bar
            // carries no ink, so it has no white-on-it floor to clear, and `accentColor`'s
            // 4.5 text floor is already stricter than the 3:1 a decorative bar needs.
            // `accentFill` would only make it muddier on a dark card.
            RoundedRectangle(cornerRadius: 2)
                .fill(persona.accentColor)
                .frame(width: 4)

            // Content
            //
            // TYPE SCALE — deliberately one step BELOW the app-wide reading ladder, and
            // that is a considered exception rather than drift. Do not "restore" it to
            // body/bodySmallEmphasis without re-reading this.
            //
            // This card sits directly under the three persona chips, whose name is 12
            // (`labelSmall`) and tagline 11 (`caption`) because they are compact chrome
            // (`lineLimit(1)` + `minimumScaleFactor`). At the documented prose size of 15
            // this paragraph became the largest text on the screen — the DESCRIPTION of the
            // thing you picked out-weighing the thing itself — and it also landed a point
            // ABOVE its own title, which inverts the rule that a header is never smaller
            // than its prose.
            //
            // Fixed by lowering both together (title 14→13, prose 15→14) so the block
            // recedes toward the chips it belongs to while keeping the header above the
            // body. Repointed PER VIEW, never by editing the shared tokens: `body` alone
            // backs prose across the app, and `label`/`caption` back hundreds of chart and
            // table usages.
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text(styleTitle)
                    .font(AppTypography.labelEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text(persona.description)
                    .font(AppTypography.bodySmall)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        AnalysisDescriptionCard(persona: .warrenBuffett)
        AnalysisDescriptionCard(persona: .cathieWood)
    }
    .padding()
    .background(AppColors.background)
}
