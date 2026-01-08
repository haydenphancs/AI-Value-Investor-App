//
//  SectorIndustryRow.swift
//  ios
//
//  Molecule: Row displaying sector/industry information
//

import SwiftUI

struct SectorIndustryRow: View {
    let label: String
    let value: String
    var valueColor: Color? = nil
    var isLink: Bool = false

    var body: some View {
        HStack {
            Text(label)
                .font(AppTypography.footnote)
                .foregroundColor(AppColors.textSecondary)

            Spacer()

            Text(value)
                .font(AppTypography.footnoteBold)
                .foregroundColor(valueColor ?? AppColors.textPrimary)
        }
    }
}

#Preview {
    VStack(spacing: AppSpacing.md) {
        SectorIndustryRow(label: "Sector", value: "Technology")
        SectorIndustryRow(label: "Industry", value: "Consumer Electronics")
        SectorIndustryRow(label: "Sector Performance", value: "+2.87%", valueColor: AppColors.bullish)
        SectorIndustryRow(label: "Industry Rank", value: "#1 of 42")
    }
    .padding()
    .background(AppColors.cardBackground)
    .cornerRadius(AppCornerRadius.large)
    .padding()
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
