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
            .foregroundColor(AppColors.textOnAccent)
            .padding(.horizontal, AppSpacing.sm)
            .padding(.vertical, AppSpacing.xs)
            // `.fill` + `textOnAccent`, and both halves must stay together: this capsule
            // is a saturated background carrying light ink, so the contrast contract is
            // white-ON-it. Under `.graphic` the badge hexes measured as low as 1.92:1.
            .background(Color(themedHex: persona.badgeColor, role: .fill, fallback: AppColors.primaryFill))
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
