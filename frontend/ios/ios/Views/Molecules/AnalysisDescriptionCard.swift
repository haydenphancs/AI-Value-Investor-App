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
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text(styleTitle)
                    .font(AppTypography.bodySmallEmphasis)
                    .foregroundColor(AppColors.textPrimary)

                Text(persona.description)
                    .font(AppTypography.body)
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
