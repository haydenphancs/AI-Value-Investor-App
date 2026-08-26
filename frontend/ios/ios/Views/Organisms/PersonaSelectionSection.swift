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
            Text("Analysis Persona:")
                .font(AppTypography.headingSmall)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Horizontal scroll of persona cards
            ScrollView(.horizontal, showsIndicators: false) {
                // `alignment: .top` pairs with the card's `minHeight`/`maxHeight: .infinity` frame:
                // cards can now grow with the text, and a taller one must not vertically offset
                // its neighbours. Without it the HStack centres them and the row looks ragged.
                HStack(alignment: .top, spacing: AppSpacing.md) {
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
}
