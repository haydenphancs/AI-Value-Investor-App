//
//  FeatureRow.swift
//  ios
//
//  Molecule: Feature row with icon, title, and subtitle
//

import SwiftUI

struct FeatureRow: View {
    let feature: AnalysisFeature

    var body: some View {
        HStack(spacing: AppSpacing.md) {
            // Feature Icon
            FeatureIcon(
                systemIconName: feature.systemIconName,
                color: feature.iconColor,
                size: 40
            )

            // Text Content
            VStack(alignment: .leading, spacing: AppSpacing.xxs) {
                Text(feature.title)
                    .font(AppTypography.calloutBold)
                    .foregroundColor(AppColors.textPrimary)

                Text(feature.subtitle)
                    .font(AppTypography.caption)
                    .foregroundColor(AppColors.textSecondary)
                    .lineLimit(2)
            }

            Spacer()
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackground)
        )
    }
}

#Preview {
    VStack(spacing: AppSpacing.sm) {
        ForEach(AnalysisFeature.allFeatures) { feature in
            FeatureRow(feature: feature)
        }
    }
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
