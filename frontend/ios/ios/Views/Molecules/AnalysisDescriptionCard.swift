//
//  AnalysisDescriptionCard.swift
//  ios
//
//  Molecule: Analysis style description card
//

import SwiftUI

struct AnalysisDescriptionCard: View {
    let persona: AnalysisPersona

    private var styleTitle: String {
        let lastName = persona.rawValue.components(separatedBy: " ").last ?? ""
        return "\(lastName) Style Analysis"
    }

    var body: some View {
        HStack(alignment: .top, spacing: AppSpacing.md) {
            // Accent line
            RoundedRectangle(cornerRadius: 2)
                .fill(persona.accentColor)
                .frame(width: 4)

            // Content
            VStack(alignment: .leading, spacing: AppSpacing.sm) {
                Text(styleTitle)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(persona.description)
                    .font(AppTypography.footnote)
                    .foregroundColor(AppColors.textSecondary)
                    .lineSpacing(4)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(AppSpacing.lg)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .fill(AppColors.cardBackground)
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
    .preferredColorScheme(.dark)
}
