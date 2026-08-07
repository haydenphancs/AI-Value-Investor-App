//
//  PersonaIcon.swift
//  ios
//
//  Atom: Persona avatar icon with customizable style
//

import SwiftUI

struct PersonaIcon: View {
    let persona: AnalysisPersona
    var size: CGFloat = 48
    var isSelected: Bool = false

    var body: some View {
        ZStack {
            Circle()
                .fill(
                    // Selected = a saturated fill carrying light ink, so `accentFill` +
                    // `textOnAccent` below. It was `accentColor` + `textPrimary`, i.e.
                    // exactly the "text token on a fill" defect theme-lint rule 3 exists
                    // to catch — invisible to it because the rule greps literal `*Fill`
                    // token names and this arrives from the server at runtime.
                    isSelected
                        ? persona.accentFill
                        : AppColors.cardBackgroundLight
                )
                .frame(width: size, height: size)

            Image(systemName: persona.systemIconName)
                .font(.system(size: size * 0.4, weight: .semibold))
                .foregroundColor(
                    isSelected
                        ? AppColors.textOnAccent
                        : persona.accentColor
                )
        }
    }
}

#Preview {
    HStack(spacing: AppSpacing.lg) {
        ForEach(AnalysisPersona.allCases) { persona in
            PersonaIcon(persona: persona, isSelected: persona == .warrenBuffett)
        }
    }
    .padding()
    .background(AppColors.background)
}
