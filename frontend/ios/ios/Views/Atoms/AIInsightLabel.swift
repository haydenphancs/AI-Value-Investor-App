//
//  AIInsightLabel.swift
//  ios
//
//  Atom: the "✨ Insight" header that marks AI-written prose.
//
//  This exact HStack — `sparkles.2` in indigo, the word in an indigo→cyan
//  gradient — is inlined identically in 12 places across the report sections
//  (ReportPriceMovementSection, ReportFundamentalsSection, ReportMoatCompetition
//  Section, ReportMacroGeopoliticalSection, ReportRevenueEngineSection,
//  ReportHiddenMarketSignalsSection, ReportFutureForecastSection,
//  ReportConsensusBar, ReportKeyManagementTable). Those copies are left in place
//  for now; new call sites should use this atom so the mark stays consistent.
//
//  It is a PROVENANCE mark: it means "a model wrote the text below". Do not put
//  it on deterministic or templated copy — that is the same class of claim as
//  the AI badge the Updates card gates on `isAIGenerated`.
//

import SwiftUI

struct AIInsightLabel: View {
    /// "Insight" on report sections, "Insights" on the Updates card.
    var text: String = "Insight"
    /// Defaults match the report sections, where the label sits inline above a
    /// narrative paragraph. The Updates card uses it as a card title and passes
    /// a larger font, so the atom must not hardcode the scale.
    var font: Font = AppTypography.bodySmallEmphasis
    var iconFont: Font = AppTypography.iconDefault

    var body: some View {
        HStack(spacing: AppSpacing.xs) {
            Image(systemName: "sparkles.2")
                // Flat indigo, NOT `AppGradients.ai` — deliberate, and it is
                // what the report sections render. A two-stop ramp across a
                // glyph this small reads as muddy rather than as a gradient.
                // (The report writes this as a one-colour LinearGradient, which
                // rasterises identically.)
                .foregroundStyle(Color.indigo)
                .font(iconFont)
                .fontWeight(.semibold)

            Text(text)
                .font(font)
                .foregroundStyle(AppGradients.ai)
        }
        // One element to VoiceOver, and it reads as a label rather than
        // "sparkles two, Insight".
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(text), AI generated")
    }
}

#Preview {
    VStack(alignment: .leading, spacing: 24) {
        // Report sections: inline above a narrative paragraph.
        VStack(alignment: .leading, spacing: 8) {
            AIInsightLabel()
            Text("SanDisk's stock fell 24 percent in the last seven days, driven by broader semiconductor weakness.")
                .font(AppTypography.body)
                .foregroundColor(AppColors.textSecondary)
        }

        // Updates card: used as the card title, at header scale.
        AIInsightLabel(
            text: "Insights",
            font: AppTypography.bodyEmphasis,
            iconFont: AppTypography.iconSmall
        )
    }
    .padding()
    .frame(maxWidth: .infinity, alignment: .leading)
    .background(AppColors.cardBackground)
}
