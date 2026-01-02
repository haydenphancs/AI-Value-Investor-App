//
//  LearnCreditsSection.swift
//  ios
//
//  Organism: Section showing credit balance in Learn tab
//

import SwiftUI

struct LearnCreditsSection: View {
    let balance: CreditBalance
    var onAddCredits: (() -> Void)?

    var body: some View {
        LearnCreditsCard(balance: balance, onAddCredits: onAddCredits)
            .padding(.horizontal, AppSpacing.lg)
    }
}

#Preview {
    VStack {
        LearnCreditsSection(balance: CreditBalance.mock)
        Spacer()
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
