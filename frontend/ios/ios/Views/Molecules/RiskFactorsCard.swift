//
//  RiskFactorsCard.swift
//  ios
//
//  Molecule: Card containing multiple risk factors
//

import SwiftUI

struct RiskFactorsCard: View {
    let data: RiskFactorsData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ForEach(data.factors) { factor in
                RiskFactorRow(factor: factor)
            }
        }
    }
}

#Preview {
    RiskFactorsCard(
        data: RiskFactorsData(
            introText: "",
            factors: [
                RiskFactor(
                    iconName: "exclamationmark.triangle.fill",
                    iconColor: AppColors.bearish,
                    title: "Market Competition",
                    description: "Traditional automakers and new EV startups intensifying competition globally",
                    impactLevel: .high
                ),
                RiskFactor(
                    iconName: "doc.text.fill",
                    iconColor: AppColors.neutral,
                    title: "Regulatory Changes",
                    description: "Potential changes in EV subsidies and environmental regulations",
                    impactLevel: .medium
                )
            ]
        )
    )
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
