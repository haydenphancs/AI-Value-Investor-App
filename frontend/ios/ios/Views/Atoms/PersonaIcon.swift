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
                    isSelected
                        ? persona.accentColor
                        : AppColors.cardBackgroundLight
                )
                .frame(width: size, height: size)

            Image(systemName: persona.systemIconName)
                .font(.system(size: size * 0.4, weight: .semibold))
                .foregroundColor(
                    isSelected
                        ? AppColors.textPrimary
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
    .preferredColorScheme(.dark)
}
