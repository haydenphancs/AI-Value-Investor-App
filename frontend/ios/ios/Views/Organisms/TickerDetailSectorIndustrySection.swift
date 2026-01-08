//
//  TickerDetailSectorIndustrySection.swift
//  ios
//
//  Organism: Sector & Industry section for Ticker Detail
//

import SwiftUI

struct TickerDetailSectorIndustrySection: View {
    let info: SectorIndustryInfo

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            // Section header
            Text("Sector & Industry")
                .font(AppTypography.headline)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // Info card
            VStack(spacing: AppSpacing.md) {
                SectorIndustryRow(label: "Sector", value: info.sector)
                SectorIndustryRow(label: "Industry", value: info.industry)
                SectorIndustryRow(
                    label: "Sector Performance",
                    value: info.formattedPerformance,
                    valueColor: info.performanceColor
                )
                SectorIndustryRow(label: "Industry Rank", value: info.industryRank)
            }
            .padding(AppSpacing.lg)
            .background(AppColors.cardBackground)
            .cornerRadius(AppCornerRadius.large)
            .padding(.horizontal, AppSpacing.lg)
        }
    }
}

#Preview {
    ScrollView {
        TickerDetailSectorIndustrySection(
            info: SectorIndustryInfo(
                sector: "Technology",
                industry: "Consumer Electronics",
                sectorPerformance: 2.87,
                industryRank: "#1 of 42"
            )
        )
    }
    .background(AppColors.background)
    .preferredColorScheme(.dark)
}
