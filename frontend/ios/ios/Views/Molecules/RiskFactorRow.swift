//
//  RiskFactorRow.swift
//  ios
//
//  Molecule: Single risk factor row with icon, description, and impact badge
//

import SwiftUI

struct RiskFactorRow: View {
    let factor: RiskFactor

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Header with icon and title
            HStack(alignment: .top, spacing: AppSpacing.md) {
                // Icon
                ZStack {
                    Circle()
                        .fill(factor.iconColor.opacity(0.15))
                        .frame(width: 36, height: 36)

                    Image(systemName: factor.iconName)
                        .font(.system(size: 16, weight: .semibold))
                        .foregroundColor(factor.iconColor)
                }

                VStack(alignment: .leading, spacing: AppSpacing.xs) {
                    Text(factor.title)
                        .font(AppTypography.bodyBold)
                        .foregroundColor(AppColors.textPrimary)

                    Text(factor.description)
                        .font(AppTypography.caption)
                        .foregroundColor(AppColors.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            // Impact badge
            ImpactBadge(level: factor.impactLevel)
                .padding(.leading, 36 + AppSpacing.md)
        }
        .padding(AppSpacing.md)
        .background(AppColors.cardBackground)
        .cornerRadius(AppCornerRadius.medium)
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        RiskFactorRow(factor: RiskFactor(
            iconName: "exclamationmark.triangle.fill",
            iconColor: AppColors.bearish,
            title: "Market Competition",
            description: "Traditional automakers and new EV startups intensifying competition globally",
            impactLevel: .high
        ))

        RiskFactorRow(factor: RiskFactor(
            iconName: "doc.text.fill",
            iconColor: AppColors.neutral,
            title: "Regulatory Changes",
            description: "Potential changes in EV subsidies and environmental regulations",
            impactLevel: .medium
        ))

        RiskFactorRow(factor: RiskFactor(
            iconName: "dollarsign.circle.fill",
            iconColor: AppColors.primaryBlue,
            title: "Valuation Concerns",
            description: "High P/E ratio compared to traditional automakers",
            impactLevel: .variable
        ))
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
