//
//  PersonaBadge.swift
//  ios
//
//  Atom: Badge showing investor persona name
//

import SwiftUI

struct PersonaBadge: View {
    let persona: InvestorPersona

    var body: some View {
        Text(persona.displayName)
            .font(AppTypography.caption)
            .foregroundColor(.white)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            .background(Color(hex: persona.badgeColor))
            .clipShape(Capsule())
    }
}

#Preview {
    VStack(spacing: 10) {
        ForEach(InvestorPersona.allCases, id: \.self) { persona in
            PersonaBadge(persona: persona)
        }
    }
    .padding()
    .background(AppColors.background)
}
