//
//  PersonaSelectionSection.swift
//  ios
//
//  Organism: Horizontal scrollable persona selection with description
//

import SwiftUI

struct PersonaSelectionSection: View {
    let personas: [AnalysisPersona]
    @Binding var selectedPersona: AnalysisPersona
    var onViewAllTapped: (() -> Void)?

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.lg) {
            // Section header
            HStack {
                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text("Select Analysis Persona:")
                        .font(AppTypography.title3)
                        .foregroundColor(AppColors.textPrimary)

                    Text("Choose your investment style")
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                }

                Spacer()

                Button(action: {
                    onViewAllTapped?()
                }) {
                    Text("View All")
                        .font(AppTypography.callout)
                        .foregroundColor(AppColors.primaryBlue)
                }
            }
            .padding(.horizontal, AppSpacing.lg)

            // Horizontal scroll of persona cards
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: AppSpacing.md) {
                    ForEach(personas) { persona in
                        PersonaCard(
                            persona: persona,
                            isSelected: selectedPersona == persona
                        ) {
                            withAnimation(.easeInOut(duration: 0.2)) {
                                selectedPersona = persona
                            }
                        }
                    }
                }
                .padding(.horizontal, AppSpacing.lg)
            }

            // Analysis description card
            AnalysisDescriptionCard(persona: selectedPersona)
                .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        PersonaSelectionSection(
            personas: AnalysisPersona.allCases,
            selectedPersona: .constant(.warrenBuffett)
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
