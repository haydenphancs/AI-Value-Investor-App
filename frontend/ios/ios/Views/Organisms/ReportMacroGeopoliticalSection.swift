//
//  ReportMacroGeopoliticalSection.swift
//  ios
//
//  Organism: Macro-Economic & Geopolitical deep dive content.
//  Intelligence briefing design: DEFCON-style threat level bar,
//  2-column risk gauge grid, and classified-style AI intelligence brief.
//

import SwiftUI

struct ReportMacroGeopoliticalSection: View {
    let data: ReportMacroData

    var body: some View {
        VStack(alignment: .leading, spacing: AppSpacing.xxl) {
            // Threat Level Bar
            threatLevelSection

            // Intelligence Brief
            intelligenceBriefSection
        }
    }

    // MARK: - Threat Level

    private var threatLevelSection: some View {
        VStack(alignment: .leading, spacing: AppSpacing.md) {
            ReportThreatLevelBar(level: data.overallThreatLevel)

            // Headline
            Text(data.headline)
                .font(AppTypography.calloutBold)
                .foregroundColor(AppColors.textPrimary)
                .lineSpacing(2)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.cardBackgroundLight)
                .overlay(
                    RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                        .stroke(data.overallThreatLevel.color.opacity(0.2), lineWidth: 1)
                )
        )
    }

    // MARK: - Intelligence Brief

    private var intelligenceBriefSection: some View {
        HStack(alignment: .top, spacing: AppSpacing.sm) {
            Image(systemName: "lightbulb.fill")
                .font(.system(size: 12))
                .foregroundColor(AppColors.neutral)

            Text(data.intelligenceBrief)
                .font(AppTypography.subheadline)
                .foregroundColor(AppColors.textSecondary)
                .lineSpacing(3)
        }
        .padding(AppSpacing.md)
        .background(
            RoundedRectangle(cornerRadius: AppCornerRadius.medium)
                .fill(AppColors.neutral.opacity(0.06))
        )
    }
}

#Preview {
    ScrollView {
        ReportMacroGeopoliticalSection(
            data: TickerReportData.sampleOracle.macroData
        )
        .padding()
    }
    .background(AppColors.cardBackground)
    .preferredColorScheme(.dark)
}
