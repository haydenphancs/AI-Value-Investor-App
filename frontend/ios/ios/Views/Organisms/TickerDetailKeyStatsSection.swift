//
//  TickerDetailKeyStatsSection.swift
//  ios
//
//  Organism: Key Statistics section with horizontally scrollable cards
//

import SwiftUI

struct TickerDetailKeyStatsSection: View {
    let statisticsGroups: [KeyStatisticsGroup]

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.sm) {
            // Section title inside card styling
            Text("Key Statistics")
                .font(AppTypography.heading)
                .foregroundColor(AppColors.textPrimary)
                .padding(.horizontal, AppSpacing.lg)

            // The card row. Shared by every detail screen — equal-height, top-aligned cards
            // live in KeyStatisticsCarousel, not here.
            KeyStatisticsCarousel(statisticsGroups: statisticsGroups)
        }
        .padding(.top, AppSpacing.md)
        .padding(.bottom, AppSpacing.sm)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.large)
                .cardFill()
        )
    }
}

#Preview {
    ScrollView {
        TickerDetailKeyStatsSection(statisticsGroups: KeyStatisticsGroup.sampleData)
    }
    .background(AppColors.background)
}
